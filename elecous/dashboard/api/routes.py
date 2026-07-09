"""Dashboard API routes — one per spec panel (ch. 9). Read-only; every value
returned already exists inside ELECOUS (9.27: traceable, reproducible,
validated, timestamped, evidence-backed, versioned — otherwise it cannot be
displayed).

R6 rebuild (roadmap §4): the dashboard is the intelligence surface, not an
admin panel. Routes here perform ZERO scientific reasoning — they serialize
rows the engines already computed into decision language. Where a value
cannot be honestly populated from stored data, the payload says so in place
("no contradicting evidence found in N relationships checked") instead of
omitting or inventing it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from elecous.bootstrap import ElecousStack
from elecous.common import timeutil
from elecous.dashboard.monitor.notifications import NotificationEngine

router = APIRouter()


def _stack(request: Request) -> ElecousStack:
    return request.app.state.stack


# --------------------------------------------------------------------- helpers
def _parse(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    out = dict(row)
    for field in fields:
        if isinstance(out.get(field), str):
            try:
                out[field] = json.loads(out[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return out


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return timeutil.ensure_utc(value)
    if isinstance(value, str):
        try:
            return timeutil.from_iso(value)
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    dt = _coerce_datetime(value)
    if dt is not None:
        return timeutil.to_iso(dt)
    return str(value) if value is not None else None


def _product_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "product"


def _confidence_value(row: dict[str, Any]) -> float:
    confidence = _as_dict(row.get("confidence"))
    if confidence:
        return _coerce_float(
            confidence.get("overall", confidence.get("current", confidence.get("scientific"))), 0.0
        )
    return _coerce_float(row.get("current_confidence", row.get("confidence")), 0.0)


def _human_signal(signal: str) -> str:
    """'cold_stress:up:AU' -> 'cold stress rising in AU'. Signal keys are the
    platform's real stored vocabulary; this only rewords, never invents."""
    parts = str(signal).split(":")
    phenomenon = parts[0].replace("_", " ") if parts else str(signal)
    direction = parts[1] if len(parts) > 1 else ""
    scope = parts[2] if len(parts) > 2 else ""
    verb = "rising" if direction == "up" else "falling" if direction == "down" else direction
    text = f"{phenomenon} {verb}".strip()
    if scope and scope not in ("", "global"):
        text += f" in {scope}"
    return text


def _human_signals(signals: list[Any], cap: int = 3) -> str:
    unique: list[str] = []
    for s in signals:
        h = _human_signal(s)
        if h not in unique:
            unique.append(h)
    shown = unique[:cap]
    text = " and ".join(shown) if len(shown) <= 2 else ", ".join(shown[:-1]) + " and " + shown[-1]
    extra = len(unique) - len(shown)
    if extra > 0:
        text += f" (+{extra} more)"
    return text


def _human_categories(categories: list[Any]) -> str:
    return ", ".join(str(c).replace("_", " ") for c in categories) or "demand"


def _opportunity_signals(opportunity: dict[str, Any]) -> list[str]:
    """Matched signal keys stored on the opportunity's frozen evidence."""
    evidence = _as_dict(opportunity.get("evidence"))
    signals: list[str] = []
    for am in evidence.get("activated_mechanisms", []):
        for s in am.get("matched_signals", []):
            if s not in signals:
                signals.append(s)
    return signals


def _window_dates(created_at: Any, lead_time: dict[str, Any]) -> dict[str, Any]:
    """Exact expected dates from a stored lead-time estimate — no invention:
    if the estimate has no bounds, the dates are honestly null."""
    created = _coerce_datetime(created_at)
    out: dict[str, Any] = {"earliest": None, "most_probable": None, "latest": None}
    if created is None:
        return out
    for key in out:
        days = lead_time.get(key)
        if days is not None:
            out[key] = timeutil.to_iso(created + timedelta(days=float(days)))
    return out


def _honest_benchmark(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Reword the latest stored benchmark metrics into the truth (roadmap R6,
    honesty rule): the green treatment requires winning PRECISION, not just
    F1 — recall-only wins are over-prediction and are said to be."""
    if not metrics or not isinstance(metrics.get("elecous"), dict) or not isinstance(metrics.get("baseline"), dict):
        return {"summary": "No benchmark has been recorded yet — first comparison runs after enough validated relationships exist.", "tone": "none"}
    e, b = metrics["elecous"], metrics["baseline"]
    pe, pb = e.get("precision"), b.get("precision")
    re_, rb = e.get("recall"), b.get("recall")
    if pe is None or pb is None or re_ is None or rb is None:
        return {"summary": "Benchmark recorded but incomplete — not enough resolved windows to compare honestly.", "tone": "none"}
    pieces = {
        "precision": f"precision {pe:.3f} vs baseline {pb:.3f}",
        "recall": f"recall {re_:.2f} vs baseline {rb:.2f}",
    }
    if pe >= pb and re_ >= rb:
        return {"summary": f"Outperforms the naive baseline on both fronts: {pieces['precision']}; {pieces['recall']}.", "tone": "good"}
    if pe < pb and re_ >= rb:
        return {
            "summary": (
                f"Higher recall, LOWER precision than the naive baseline — {pieces['recall']}, but {pieces['precision']}. "
                "ELECOUS currently over-predicts; fewer, more reliable predictions are the active fix."
            ),
            "tone": "mixed",
        }
    if pe >= pb and re_ < rb:
        return {"summary": f"Higher precision, lower recall than the naive baseline — {pieces['precision']}; {pieces['recall']}.", "tone": "mixed"}
    return {"summary": f"Currently BELOW the naive baseline on both precision and recall — {pieces['precision']}; {pieces['recall']}.", "tone": "bad"}


def _extract_domains(signals: list[str], stack: ElecousStack) -> list[str]:
    domains = set()
    for sig in signals:
        parts = str(sig).split(":")
        if not parts:
            continue
        source = stack.observation.registry.get(parts[0])
        if source:
            domains.add(source["category"])
    return sorted(domains)


# --------------------------------------------------------------- 9.5 world state
@router.get("/world-state")
def world_state(request: Request):
    stack = _stack(request)
    return stack.environment.world.latest_state() or stack.environment.world.build()


# ----------------------------------------------------------- 9.6 observation
@router.get("/observation/sources")
def observation_sources(request: Request):
    stack = _stack(request)
    sources = stack.observation.registry.list()
    for source in sources:
        source["countries"] = json.loads(source["countries"])
    return {"sources": sources, "capabilities": stack.observation.health.capability_health()}


@router.get("/observation/gaps")
def observation_gaps(request: Request):
    return {"requests": _stack(request).gap.open_requests()}


# ------------------------------------------------------------------- coverage
@router.get("/coverage")
def coverage(request: Request):
    """Honest coverage, DERIVED from the live source registry — never
    hardcoded text (mission audit finding: the old panel was string
    literals that silently drifted from reality)."""
    stack = _stack(request)
    return _coverage(stack)


def _coverage(stack: ElecousStack) -> dict[str, Any]:
    sources = stack.observation.registry.list()
    health = stack.observation.health.capability_health()
    domains: dict[str, dict[str, Any]] = {}
    suspended: list[dict[str, Any]] = []
    for s in sources:
        if s["status"] in ("retired",):
            continue
        if s["status"] == "suspended":
            suspended.append({
                "name": s["name"],
                "watches": s["observation_capability"].replace("_", " "),
                "needs": ("an access key" if s.get("requires_credential") else "attention"),
            })
            continue
        cap = s["observation_capability"]
        entry = domains.setdefault(cap, {"capability": cap.replace("_", " "), "sources": [], "health": health.get(cap)})
        entry["sources"].append(s["name"])
    gaps = [
        {
            "capability": str(r.get("capability", "")).replace("_", " "),
            "reason": r.get("reason") or r.get("justification"),
            "requested_at": _iso(r.get("created_at")),
        }
        for r in stack.gap.open_requests()
    ]
    return {
        "watching": sorted(domains.values(), key=lambda d: -(d["health"] or 0)),
        "blind_spots": gaps,
        "suspended_sources": suspended,
        "active_source_count": sum(len(d["sources"]) for d in domains.values()),
    }


# ------------------------------------------------------------- 9.7 timeline
@router.get("/timeline")
def timeline(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    limit: int = 60,
):
    """One continuous chronological surface: past = real demand episodes with
    did-ELECOUS-predict-it badges, present = live world signals, future =
    pending predictions at their expected windows. Paginates the past with
    since/until/limit (the old route silently capped history at 50 of
    thousands of episodes)."""
    stack = _stack(request)
    limit = max(1, min(int(limit), 500))
    since_dt = _parse_instant(since, "since")
    until_dt = _parse_instant(until, "until")
    now = timeutil.utcnow()

    # ------------------------------------------------------------ past page
    sql = (
        "SELECT * FROM research_episodes e WHERE version = "
        "(SELECT max(version) FROM research_episodes WHERE episode_id = e.episode_id) "
        "AND NOT is_control"
    )
    params: list[Any] = []
    if since_dt is not None:
        sql += " AND spike_start >= ?"
        params.append(since_dt)
    if until_dt is not None:
        sql += " AND spike_start < ?"
        params.append(until_dt)
    sql += " ORDER BY spike_start DESC LIMIT ?"
    params.append(limit)
    episodes = stack.demand.episodes.db.query_dicts(sql, params)
    past_total = stack.demand.episodes.db.query_one(
        "SELECT count(DISTINCT episode_id) FROM research_episodes WHERE NOT is_control"
    )[0]

    claims = _prediction_claims(stack)
    events: list[dict[str, Any]] = []
    for ep in episodes:
        spike_start = _coerce_datetime(ep["spike_start"])
        badge = _episode_badge(ep, spike_start, claims)
        events.append({
            "at": _iso(ep["spike_start"]),
            "phase": "past",
            "kind": "demand_spike",
            "title": f"{ep['entity']} demand spike",
            "detail": (
                f"{str(ep['category']).replace('_', ' ')} — evidence score {_coerce_float(ep['demand_score']):.2f}"
                + (f", peaked {_iso(ep['spike_peak'])[:10]}" if ep.get("spike_peak") else "")
            ),
            "entity": ep["entity"],
            "category": ep["category"],
            "confidence": _coerce_float(ep.get("overall_confidence")),
            "unexpected_absence": bool(ep.get("is_negative")),
            "badge": badge,
            "episode_id": ep["episode_id"],
        })

    include_now = until_dt is None or until_dt >= now
    present_block: dict[str, Any] | None = None
    if include_now:
        state = stack.environment.world.latest_state()
        if state:
            strong = sorted(state["signals"], key=lambda s: -abs(_coerce_float(s.get("z"))))[:12]
            present_block = {
                "as_of": state["as_of"],
                "signals": [
                    {"text": _human_signal(s["signal_key"]), "strength": round(abs(_coerce_float(s.get("z"))), 2)}
                    for s in strong
                ],
                "observation_count": state.get("observation_count", 0),
            }
        # ------------------------------------------------------------ future
        for pred in claims:
            if pred["outcome"] is not None:
                continue
            opp = pred["opportunity"]
            lead = _as_dict(opp.get("lead_time")) if opp else {}
            window = _window_dates(pred["created_at"], lead) if lead else {"earliest": None, "most_probable": None, "latest": None}
            if window["most_probable"] is None and pred["lead_time_days"] is not None:
                window["most_probable"] = timeutil.to_iso(pred["created_at"] + timedelta(days=float(pred["lead_time_days"])))
            at = window["most_probable"] or window["latest"] or timeutil.to_iso(pred["created_at"] + timedelta(days=float(pred["window_days"])))
            products = pred["claim"].get("products", [])
            events.append({
                "at": at,
                "phase": "future",
                "kind": "prediction",
                "title": f"Expected demand rise: {_human_categories(pred['claim'].get('categories', []))}",
                "detail": (
                    (f"products with evidence: {', '.join(products[:4])}" + (f" (+{len(products) - 4} more)" if len(products) > 4 else ""))
                    if products else "no product has earned evidence for this claim yet"
                ),
                "confidence": _coerce_float(pred["confidence"]),
                "window": window,
                "judgment_due": timeutil.to_iso(pred["created_at"] + timedelta(days=float(pred["window_days"]))),
                "categories": pred["claim"].get("categories", []),
            })

    events.sort(key=lambda e: str(e["at"]), reverse=True)
    oldest = None
    past_events = [e for e in events if e["phase"] == "past"]
    if past_events:
        oldest = past_events[-1]["at"]
    return {
        "events": events,
        "present": present_block,
        "past_total": past_total,
        "past_returned": len(past_events),
        "oldest_returned": oldest,
        "window": {"since": since, "until": until, "limit": limit},
    }


def _parse_instant(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        return timeutil.from_iso(value)
    except ValueError as exc:
        raise HTTPException(400, f"invalid {name}: {exc}") from exc


def _prediction_claims(stack: ElecousStack) -> list[dict[str, Any]]:
    """Every prediction with its parsed falsification claim (R2 stores
    categories, products, window and criterion under evidence.claim)."""
    rows = stack.prediction.db.query_dicts(
        "SELECT prediction_id, opportunity_id, confidence, lead_time_days, evidence, outcome, "
        "validation_status, created_at, revisions, updated_at FROM predictions ORDER BY created_at DESC"
    )
    out = []
    for row in rows:
        evidence = _as_dict(row.get("evidence"))
        claim = _as_dict(evidence.get("claim"))
        created = _coerce_datetime(row["created_at"])
        if created is None:
            continue
        window_days = _coerce_float(claim.get("window_days"), _coerce_float(row.get("lead_time_days"), 14.0) + 3.0)
        out.append({
            "prediction_id": row["prediction_id"],
            "opportunity_id": row["opportunity_id"],
            "opportunity": stack.prediction.opportunities.latest(row["opportunity_id"]),
            "claim": claim,
            "created_at": created,
            "window_days": window_days,
            "confidence": row["confidence"],
            "lead_time_days": row.get("lead_time_days"),
            "outcome": _as_dict(row.get("outcome")) if row.get("outcome") else None,
            "validation_status": row.get("validation_status"),
            "revisions": row.get("revisions") or 0,
            "updated_at": _coerce_datetime(row.get("updated_at")),
        })
    return out


def _episode_badge(ep: dict[str, Any], spike_start: datetime | None, claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Did ELECOUS predict this episode, and was it right? Joined from real
    prediction claims (categories + window in evidence.claim) — never
    estimated. Predictions must PRECEDE the spike to count."""
    if spike_start is None:
        return {"predicted": False, "verdict": None, "note": "spike date unavailable"}
    covering = [
        p for p in claims
        if ep["category"] in p["claim"].get("categories", [])
        and p["created_at"] <= spike_start <= p["created_at"] + timedelta(days=p["window_days"])
    ]
    if not covering:
        return {"predicted": False, "verdict": None, "note": "not predicted"}
    resolved = [p for p in covering if p["outcome"] is not None]
    if not resolved:
        due = min(p["created_at"] + timedelta(days=p["window_days"]) for p in covering)
        return {"predicted": True, "verdict": "pending", "note": f"verdict due {timeutil.to_iso(due)[:10]}"}
    best = resolved[0]
    product_hit = None
    hits = best["outcome"].get("product_hits") or {}
    if ep["entity"] in hits:
        product_hit = bool(hits[ep["entity"]])
    return {
        "predicted": True,
        "verdict": best["validation_status"],
        "product_verdict": product_hit,
        "note": f"prediction judged {best['validation_status'].replace('_', ' ')}",
    }


# ------------------------------------------------------ 9.8-9.9 opportunity radar
@router.get("/opportunities")
def opportunities(request: Request):
    stack = _stack(request)
    rows = stack.prediction.opportunities.active()
    fields = ("categories", "products", "activated_mechanisms", "environmental_factors", "scores", "lead_time", "confidence", "uncertainty", "business_value", "regions")
    res = []
    for r in rows:
        parsed = _parse(r, fields)
        parsed["domains"] = _extract_domains(_opportunity_signals(parsed), stack)
        res.append(parsed)
    return {"opportunities": res}


@router.get("/opportunities/{opportunity_id}")
def opportunity_detail(opportunity_id: str, request: Request):
    stack = _stack(request)
    row = stack.prediction.opportunities.latest(opportunity_id)
    if row is None:
        raise HTTPException(404, "opportunity not found")
    fields = ("categories", "products", "activated_mechanisms", "environmental_factors", "evidence", "scores", "lead_time", "confidence", "uncertainty", "business_value", "regions")
    parsed = _parse(row, fields)
    parsed["domains"] = _extract_domains(_opportunity_signals(parsed), stack)
    parsed["contract"] = _recommendation_contract(stack, row)
    return parsed


@router.get("/opportunities/{opportunity_id}/forces")
def opportunity_forces(opportunity_id: str, request: Request):
    """The force timeline (roadmap P3): watch the reasoning evolve. For every
    force the story's relationships care about — supporting AND wanted-but-
    absent — the real z trajectory across stored world states, when it
    appeared, whether it is strengthening/weakening/gone, plus how the
    story's probability moved version by version. Pure serialization of
    world_states + opportunity versions; nothing computed that isn't stored."""
    stack = _stack(request)
    versions = stack.prediction.db.query_dicts(
        "SELECT version, probability, world_state_id, evidence, created_at "
        "FROM opportunities WHERE opportunity_id = ? ORDER BY version",
        [opportunity_id],
    )
    if not versions:
        raise HTTPException(404, "opportunity not found")
    latest = versions[-1]
    evidence = _as_dict(_parse(latest, ("evidence",)).get("evidence"))
    matched: set[str] = set()
    missing: set[str] = set()
    for am in evidence.get("activated_mechanisms", []):
        matched.update(am.get("matched_signals") or [])
        missing.update(am.get("missing_signals") or [])
    created = _coerce_datetime(latest["created_at"]) or timeutil.utcnow()
    window_start = created - timedelta(days=7)

    world_db = stack.environment.world.db
    states = world_db.query_dicts(
        "SELECT state_id, as_of, signals FROM world_states WHERE as_of >= ? ORDER BY as_of",
        [window_start],
    )
    series: dict[str, list[dict[str, Any]]] = {k: [] for k in matched | missing}
    last_state_asof = None
    for st in states:
        at = timeutil.to_iso(_coerce_datetime(st["as_of"]))
        last_state_asof = at
        z_by_key = {s.get("signal_key"): s.get("z") for s in (_as_list(_parse(st, ("signals",)).get("signals")))}
        for key, points in series.items():
            if key in z_by_key:
                points.append({"at": at, "z": z_by_key[key]})

    forces = []
    for key, points in series.items():
        role = "supporting" if key in matched else "wanted_but_absent"
        if not points:
            status = "not seen in this window"
        elif points[-1]["at"] != last_state_asof:
            status = "disappeared"
        elif len(points) >= 2 and abs(_coerce_float(points[-1]["z"])) > abs(_coerce_float(points[0]["z"])) * 1.1:
            status = "strengthening"
        elif len(points) >= 2 and abs(_coerce_float(points[-1]["z"])) < abs(_coerce_float(points[0]["z"])) * 0.9:
            status = "weakening"
        else:
            status = "holding"
        forces.append({
            "signal_key": key,
            "force": _human_signals([key]),
            "role": role,
            "status": status,
            "first_seen": points[0]["at"] if points else None,
            "latest_z": points[-1]["z"] if points else None,
            "series": points,
        })
    # contributed most first: supporting forces by current strength
    forces.sort(key=lambda f: (f["role"] != "supporting", -abs(_coerce_float(f["latest_z"], 0.0))))

    state_times = {
        r["state_id"]: timeutil.to_iso(_coerce_datetime(r["as_of"]))
        for r in world_db.query_dicts(
            f"SELECT state_id, as_of FROM world_states WHERE state_id IN ({','.join('?' * len(versions))})",
            [v["world_state_id"] for v in versions],
        )
    } if versions else {}
    confidence_path = [
        {
            "version": v["version"],
            "probability": _coerce_float(v["probability"]),
            "at": state_times.get(v["world_state_id"]),
        }
        for v in versions
    ]
    return {"opportunity_id": opportunity_id, "forces": forces, "confidence_path": confidence_path}


def _recommendation_contract(stack: ElecousStack, opportunity: dict[str, Any]) -> dict[str, Any]:
    """The 9-field recommendation contract (roadmap correction #13). Every
    field comes from stored rows; a field that cannot be honestly populated
    says so in place — absence of counter-evidence is itself information."""
    parsed = _parse(opportunity, ("categories", "products", "evidence", "lead_time", "confidence", "uncertainty", "business_value", "regions"))
    categories = _as_list(parsed.get("categories"))
    products = _as_list(parsed.get("products"))
    signals = _opportunity_signals(parsed)
    evidence = _as_dict(parsed.get("evidence"))
    business = _as_dict(parsed.get("business_value"))
    uncertainty = _as_dict(parsed.get("uncertainty"))
    confidence = _as_dict(parsed.get("confidence"))
    lead = _as_dict(parsed.get("lead_time"))

    chains = []
    for am in evidence.get("activated_mechanisms", []):
        chain = am.get("behavioural_chain") or []
        if chain:
            chains.append(" → ".join(str(step) for step in chain[:3]))

    # why ELECOUS believes it
    why = ""
    if signals:
        why = f"Live external forces match learned relationships: {_human_signals(signals)}."
    if chains:
        why += f" Expected behaviour: {chains[0]}."
    if not why:
        why = "No stored explanation found for this opportunity — this is a gap, not a secret."

    # supporting evidence
    supporting: list[str] = [_human_signal(s) + " (live now)" for s in signals[:6]]
    episode_support = 0
    for category in categories:
        try:
            episode_support += stack.demand.episodes.db.query_one(
                "SELECT count(DISTINCT episode_id) FROM research_episodes WHERE category = ? AND NOT is_control AND NOT is_negative",
                [category],
            )[0]
        except Exception:
            pass
    if episode_support:
        supporting.append(f"{episode_support} historical demand spikes recorded in {_human_categories(categories)}")
    if not supporting:
        supporting = ["no supporting evidence stored — this opportunity should not be trusted"]

    # contradicting evidence — read the factor rows this opportunity used
    factor_ids = _as_list(parsed.get("environmental_factors")) or _as_list(evidence.get("environmental_factors"))
    contradicting: list[str] = []
    checked = 0
    for fid in factor_ids:
        if not isinstance(fid, str):
            continue
        frow = stack.environment.factors.latest(fid)
        if frow is None:
            continue
        checked += 1
        contra = _as_list(frow.get("contradictory_episodes"))
        if contra:
            contradicting.append(f"{frow['name']}: {len(contra)} contradicting demand spikes on record")
    contradicting_note = None
    if not contradicting:
        contradicting_note = (
            f"No contradicting evidence found in the {checked} stored relationships checked."
            if checked else "No relationships were available to check for contradicting evidence."
        )

    # timing
    window = _window_dates(parsed.get("created_at"), lead)
    timing = window if any(window.values()) else {"note": "no lead-time evidence stored for this opportunity"}

    # sales increase — honest: the platform's ground truth is attention, not sales (roadmap R3 pending)
    attention_median = business.get("attention_volume_median")
    expected_sales = {
        "value": None,
        "note": (
            "Cannot be honestly quantified yet: ELECOUS currently measures demand attention, not sales. "
            + (f"Attention-demand proxy: median historical demand volume {attention_median:.0f}." if attention_median else "")
        ).strip(),
    }

    # profit opportunity — from stored business value, with its provenance stated
    profit = {
        "expected_roi": business.get("expected_roi"),
        "commercial_importance": business.get("commercial_importance"),
        "note": "derived from historical attention volumes of the underlying relationships, not from sales data",
    } if business else {"note": "no business value stored for this opportunity"}

    # risk
    risk = {
        "risk_score": business.get("risk"),
        "conflicting_stories": uncertainty.get("conflicts"),
        "note": None if business else "no risk assessment stored",
    }

    # remaining uncertainty
    remaining = {
        "overall": uncertainty.get("overall"),
        "spread_between_relationships": confidence.get("sigma_p"),
        "components": {k: v for k, v in uncertainty.items() if k != "overall"} or None,
    }

    # similar historical situations — real episodes in the same categories
    similar: list[dict[str, Any]] = []
    for category in categories:
        for ep in stack.demand.episodes.list_latest(category=category, include_controls=False)[:2]:
            if ep.get("is_negative"):
                continue
            similar.append({
                "entity": ep["entity"],
                "category": str(ep["category"]).replace("_", " "),
                "began": _iso(ep["spike_start"]),
                "evidence_score": _coerce_float(ep["demand_score"]),
            })
    similar = similar[:4]

    return {
        "why": why,
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting or None,
        "contradicting_evidence_note": contradicting_note,
        "expected_sales_increase": expected_sales,
        "expected_timing": timing,
        "expected_profit_opportunity": profit,
        "risk": risk,
        "remaining_uncertainty": remaining,
        "similar_historical_situations": similar or None,
        "similar_historical_situations_note": None if similar else "no similar historical situations recorded yet",
    }


# ----------------------------------------------------------------- live feed
@router.get("/feed")
def feed(request: Request, since: str | None = None, limit: int = 50):
    """Live intelligence feed, derived AT REQUEST TIME from real versioned
    rows (roadmap R6). The NotificationEngine's four checks were never wired
    in production (0 events ever) — instead of adding new notification state,
    every event class it was meant to catch is derived here from the tables
    that already record it: opportunity version deltas, prediction evolutions
    and resolutions, relationship failures/validations, new relationships,
    new sources, and confirmed demand spikes. Each event says WHY."""
    stack = _stack(request)
    since_dt = _parse_instant(since, "since")
    limit = max(1, min(int(limit), 200))
    events = _derive_feed_events(stack)
    if since_dt is not None:
        since_iso = timeutil.to_iso(since_dt)
        events = [e for e in events if e["at"] > since_iso]
    return {"events": events[:limit], "generated_at": timeutil.to_iso(timeutil.utcnow())}


def _derive_feed_events(stack: ElecousStack) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def add(at: Any, kind: str, title: str, why: str, ref: str) -> None:
        iso = _iso(at)
        if iso is None:
            return
        events.append({"id": f"{kind}:{ref}:{iso}", "at": iso, "kind": kind, "title": title, "why": why})

    # --- opportunity versions: new / strengthened / weakened, timed by the
    # world state each version was built from (a real stored timestamp).
    try:
        state_times = {
            r["state_id"]: r["as_of"]
            for r in stack.environment.world.db.query_dicts("SELECT state_id, as_of FROM world_states")
        }
    except Exception:
        state_times = {}
    pred_updates = {
        r["opportunity_id"]: r["updated_at"]
        for r in stack.prediction.db.query_dicts(
            "SELECT opportunity_id, updated_at FROM predictions WHERE updated_at IS NOT NULL"
        )
    }
    opp_rows = stack.prediction.db.query_dicts(
        "SELECT * FROM opportunities ORDER BY opportunity_id, version"
    )
    by_opp: dict[str, list[dict[str, Any]]] = {}
    for row in opp_rows:
        by_opp.setdefault(row["opportunity_id"], []).append(row)
    for opp_id, versions in by_opp.items():
        for i, row in enumerate(versions):
            categories = _as_list(row.get("categories"))
            signals = _opportunity_signals(_parse(row, ("evidence",)))
            because = f"because {_human_signals(signals)}" if signals else "no live signals recorded on this version"
            if i == 0:
                add(
                    row["created_at"], "opportunity_new",
                    f"New opportunity: {_human_categories(categories)}",
                    f"Probability {_coerce_float(row['probability']):.0%} {because}.",
                    opp_id,
                )
                continue
            prev = versions[i - 1]
            delta = round(_coerce_float(row["probability"]) - _coerce_float(prev["probability"]), 4)
            at = state_times.get(row["world_state_id"]) or pred_updates.get(opp_id)
            if at is None:
                continue  # cannot honestly date this version change
            if abs(delta) >= 0.01:
                kind = "opportunity_strengthened" if delta > 0 else "opportunity_weakened"
                verb = "strengthened" if delta > 0 else "weakened"
                add(
                    at, kind,
                    f"Opportunity {verb}: {_human_categories(categories)}",
                    f"Probability {_coerce_float(prev['probability']):.0%} → {_coerce_float(row['probability']):.0%} {because}.",
                    f"{opp_id}:v{row['version']}",
                )
            elif row.get("stage") != prev.get("stage") or row.get("decision") != prev.get("decision"):
                add(
                    at, "opportunity_stage",
                    f"Opportunity moved to '{row.get('stage')}': {_human_categories(categories)}",
                    f"Recommended action now: {row.get('decision')} {because}.",
                    f"{opp_id}:v{row['version']}",
                )

    # --- prediction evolutions and resolutions
    for pred in stack.prediction.db.query_dicts(
        "SELECT prediction_id, opportunity_id, confidence, evidence, outcome, validation_status, "
        "created_at, revisions, updated_at FROM predictions"
    ):
        evidence = _as_dict(pred.get("evidence"))
        claim = _as_dict(evidence.get("claim"))
        categories = claim.get("categories") or []
        label = _human_categories(categories)
        if (pred.get("revisions") or 0) > 0 and pred.get("updated_at") is not None:
            add(
                pred["updated_at"], "prediction_updated",
                f"Prediction refreshed: {label}",
                f"New evidence arrived ({pred['revisions']} revisions so far); confidence now {_coerce_float(pred['confidence']):.0%}. "
                "The judgment deadline set at creation is unchanged.",
                pred["prediction_id"],
            )
        outcome = _as_dict(pred.get("outcome")) if pred.get("outcome") else None
        if outcome:
            resolved_at = outcome.get("resolved_at")
            if pred["validation_status"] == "correct":
                add(
                    resolved_at, "prediction_confirmed",
                    f"Prediction confirmed: {label}",
                    f"Real demand spikes were recorded in {_human_categories(outcome.get('hit_categories', []))} inside the predicted window.",
                    pred["prediction_id"],
                )
            elif pred["validation_status"] in ("incorrect", "partially_correct"):
                attribution = _as_dict(outcome.get("failure_attribution"))
                missed = attribution.get("missed_categories") or outcome.get("categories", [])
                present = attribution.get("signals_present_at_prediction", [])
                why = f"No demand spike appeared in {_human_categories(missed)} within the window."
                if present:
                    why += f" Evidence at prediction time: {_human_signals(present)}."
                add(resolved_at, "prediction_failed", f"Prediction {pred['validation_status'].replace('_', ' ')}: {label}", why, pred["prediction_id"])
            elif pred["validation_status"] == "insufficient_evidence":
                add(
                    resolved_at, "prediction_unjudgeable",
                    f"Prediction could not be judged: {label}",
                    "Not enough stored evidence existed to grade this claim either way — recorded, not hidden.",
                    pred["prediction_id"],
                )

    # --- relationship failures (mechanism_failures table, business words only)
    try:
        failures = stack.mechanisms.store.db.query_dicts(
            "SELECT * FROM mechanism_failures ORDER BY recorded_at DESC LIMIT 100"
        )
    except Exception:
        failures = []
    for f in failures:
        mech = stack.mechanisms.store.latest(f["mechanism_id"])
        name = mech["name"] if mech else "a learned relationship"
        questions = _as_dict(f.get("questions"))
        missed = questions.get("missed_categories") or []
        why = f"Failure type: {str(f.get('kind', '')).replace('_', ' ')}."
        if missed:
            why += f" It expected demand in {_human_categories(missed)} that never came."
        add(f["recorded_at"], "relationship_failed", f"Relationship failed a live test: {name}", why, f["failure_id"])

    # --- validations of relationships (passed/failed re-checks)
    method_words = {
        "historical": "a historical replay",
        "mechanism_validation": "a full evidence re-check",
        "prospective": "a real-future test",
        "counterfactual": "a counterfactual check",
        "control": "a control comparison",
        "skeptic": "an adversarial review",
    }
    for v in stack.validation.db.query_dicts(
        "SELECT object_type, object_id, method, outcome, created_at, validation_id FROM validations "
        "ORDER BY created_at DESC LIMIT 200"
    ):
        if v["object_type"] not in ("mechanism", "factor"):
            continue
        if v["object_type"] == "mechanism":
            obj = stack.mechanisms.store.latest(v["object_id"])
        else:
            obj = stack.environment.factors.latest(v["object_id"])
        name = obj["name"] if obj else "a learned relationship"
        method = method_words.get(v["method"], "a validation check")
        kind = "relationship_validated" if v["outcome"] == "passed" else "relationship_rechecked"
        verdict = "passed" if v["outcome"] == "passed" else v["outcome"]
        add(v["created_at"], kind, f"Relationship {verdict} {method}", name, v["validation_id"])

    # --- newly discovered relationships (factor version 1 rows; names are
    # human sentences since roadmap R7 part 1)
    try:
        new_factors = stack.environment.factors.db.query_dicts(
            "SELECT factor_id, name, created_at FROM environmental_factors WHERE version = 1 "
            "ORDER BY created_at DESC LIMIT 100"
        )
    except Exception:
        new_factors = []
    for frow in new_factors:
        add(frow["created_at"], "relationship_discovered", "New relationship discovered", frow["name"], frow["factor_id"])

    # --- new evidence sources
    for s in stack.observation.registry.list():
        add(
            s["created_at"], "source_connected",
            f"Evidence source connected: {s['name']}",
            f"Watching {str(s['observation_capability']).replace('_', ' ')} ({s['category']}).",
            s["source_id"],
        )

    # --- confirmed demand spikes (recent)
    for ep in stack.demand.episodes.db.query_dicts(
        "SELECT episode_id, entity, category, spike_start, demand_score, created_at, is_negative "
        "FROM research_episodes e WHERE version = (SELECT max(version) FROM research_episodes WHERE episode_id = e.episode_id) "
        "AND NOT is_control ORDER BY created_at DESC LIMIT 100"
    ):
        if ep.get("is_negative"):
            continue
        add(
            ep["created_at"], "demand_spike",
            f"Demand spike confirmed: {ep['entity']}",
            f"{str(ep['category']).replace('_', ' ')} — began {_iso(ep['spike_start'])[:10]}, evidence score {_coerce_float(ep['demand_score']):.2f}.",
            ep["episode_id"],
        )

    events.sort(key=lambda e: e["at"], reverse=True)
    return events


# --------------------------------------------------------- 9.10 mechanism explorer
@router.get("/mechanisms")
def mechanisms(request: Request, lifecycle: str | None = None, category: str | None = None):
    stack = _stack(request)
    rows = stack.mechanisms.store.list_latest(lifecycle=lifecycle, category=category)
    fields = ("environmental_factors", "supporting_signals", "behavioural_chain", "product_traits", "affected_categories", "countries", "confidence", "business_value", "stability", "supporting_episodes")
    return {"mechanisms": [_parse(r, fields) for r in rows]}


@router.get("/mechanisms/{mechanism_id}")
def mechanism_detail(mechanism_id: str, request: Request):
    stack = _stack(request)
    row = stack.mechanisms.store.latest(mechanism_id)
    if row is None:
        raise HTTPException(404, "mechanism not found")
    fields = ("environmental_factors", "supporting_signals", "behavioural_chain", "product_traits", "affected_categories", "countries", "climate_zones", "world_conditions", "lead_time", "confidence", "business_value", "stability", "supporting_episodes", "control_episodes", "contradicting_episodes", "validation_history", "evidence_links")
    return _parse(row, fields)


# --------------------------------------------------- 9.11 environmental factor explorer
@router.get("/factors")
def factors(request: Request, lifecycle: str | None = None, category: str | None = None):
    stack = _stack(request)
    rows = stack.environment.factors.list_latest(lifecycle=lifecycle, category=category)
    fields = ("signals", "categories", "regions", "lead_time", "confidence", "contribution", "supporting_episodes", "contradictory_episodes", "counterfactual", "discovery_quality")
    res = []
    for r in rows:
        parsed = _parse(r, fields)
        parsed["domains"] = _extract_domains([str(s) for s in _as_list(parsed.get("signals"))], stack)
        res.append(parsed)
    return {"factors": res}


@router.get("/factors/{factor_id}")
def factor_detail(factor_id: str, request: Request):
    stack = _stack(request)
    row = stack.environment.factors.latest(factor_id)
    if row is None:
        raise HTTPException(404, "factor not found")
    fields = ("signals", "categories", "regions", "lead_time", "confidence", "contribution", "supporting_episodes", "contradictory_episodes", "counterfactual", "discovery_quality")
    parsed = _parse(row, fields)
    parsed["domains"] = _extract_domains([str(s) for s in _as_list(parsed.get("signals"))], stack)
    return parsed


# ------------------------------------------------------- 9.12 research episode explorer
@router.get("/episodes")
def episodes(request: Request, category: str | None = None, entity: str | None = None):
    stack = _stack(request)
    rows = stack.demand.episodes.list_latest(category=category, entity=entity, include_controls=False)
    fields = ("traits", "evidence", "timeline", "signal_agreement", "confidence", "control_episodes", "mechanisms_found", "false_spike_checks")
    return {"episodes": [_parse(r, fields) for r in rows]}


@router.get("/episodes/{episode_id}/replay")
def episode_replay(episode_id: str, request: Request):
    replay = _stack(request).demand.episodes.replay(episode_id)
    if replay is None:
        raise HTTPException(404, "episode not found")
    return replay


# ------------------------------------------------------------------ 9.13 world map
@router.get("/world-map")
def world_map(request: Request):
    stack = _stack(request)
    state = stack.environment.world.latest_state() or stack.environment.world.build()
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for signal in state["signals"]:
        by_scope.setdefault(signal["scope"], []).append(signal)
    opportunities_by_region: dict[str, int] = {}
    for opp in stack.prediction.opportunities.active():
        for region in json.loads(opp["regions"]):
            opportunities_by_region[region] = opportunities_by_region.get(region, 0) + 1
    return {"signals_by_scope": by_scope, "opportunities_by_region": opportunities_by_region}


# -------------------------------------------------------- 9.15 validation center
@router.get("/validations")
def validations(request: Request, object_type: str | None = None, limit: int = 100):
    stack = _stack(request)
    sql = "SELECT * FROM validations"
    params: list[Any] = []
    if object_type:
        sql += " WHERE object_type = ?"
        params.append(object_type)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = stack.validation.db.query_dicts(sql, params)
    return {"validations": [_parse(r, ("evidence", "controls", "failures")) for r in rows]}


@router.get("/validations/prospective")
def prospective_predictions(request: Request):
    stack = _stack(request)
    rows = stack.validation.prospective.db.query_dicts(
        "SELECT * FROM prospective_predictions ORDER BY issued_at DESC LIMIT 100"
    )
    return {"prospective_predictions": rows}


@router.get("/validations/benchmark")
def rolling_benchmark(request: Request):
    stack = _stack(request)
    latest = stack.validation.benchmarks.latest("rolling_vs_baseline")
    metrics = _as_dict(latest.get("metrics")) if latest else None
    return {"latest": latest, "honest": _honest_benchmark(metrics)}


@router.get("/activity")
def activity(request: Request):
    """Live-operations directive: what changed in the last minute / hour /
    day / week, from real timestamped rows -- rates before totals. Also
    explains quiet periods: the next scheduled work and when it fires."""
    stack = _stack(request)

    def windows(db, table, col):
        row = db.query_one(
            f"SELECT count(*) FILTER ({col} >= now() - INTERVAL 1 MINUTE),"
            f" count(*) FILTER ({col} >= now() - INTERVAL 1 HOUR),"
            f" count(*) FILTER ({col} >= now() - INTERVAL 1 DAY),"
            f" count(*) FILTER ({col} >= now() - INTERVAL 7 DAY), count(*) FROM {table}"
        )
        return {"minute": row[0], "hour": row[1], "day": row[2], "week": row[3], "total": row[4]}

    tele = stack.scheduler.telemetry()
    report = stack.scheduler.report()
    upcoming = sorted((t for t in report if t["enabled"]), key=lambda t: t["next_run"])[:3]
    return {
        "observations": windows(stack.observation.store.db, "observations", "collection_time"),
        "episodes": windows(stack.demand.episodes.db, "research_episodes", "created_at"),
        "validations": windows(stack.validation.db, "validations", "created_at"),
        "predictions": windows(stack.prediction.db, "predictions", "created_at"),
        "running_now": tele.get("running_tasks", []),
        "next_work": [{"task": t["name"], "at": t["next_run"]} for t in upcoming],
    }


@router.get("/predictions/accuracy")
def prediction_accuracy(request: Request):
    """Live prediction-outcome ledger. Every number here is a real row —
    resolved outcomes come from resolve_due's judgments against real
    episodes, never estimated. Zero judged -> accuracy null plus the real
    date the first verdict is due (honesty rule, R6)."""
    stack = _stack(request)
    rows = stack.prediction.db.query_dicts(
        "SELECT validation_status, count(*) AS n FROM predictions GROUP BY validation_status"
    )
    counts = {r["validation_status"]: r["n"] for r in rows}
    judged = sum(v for k, v in counts.items() if k in ("correct", "incorrect", "partially_correct"))
    recent = stack.prediction.db.query_dicts(
        "SELECT prediction_id, opportunity_id, mechanisms_used, evidence, confidence, lead_time_days, validation_status, outcome, created_at "
        "FROM predictions ORDER BY created_at DESC LIMIT 30"
    )
    return {
        "counts": counts,
        "accuracy": round(counts.get("correct", 0) / judged, 4) if judged else None,
        "judged": judged,
        "first_verdict_due": _first_verdict_due(stack),
        "recent": [_parse(r, ("outcome", "evidence", "mechanisms_used")) for r in recent],
    }


def _typical_past_spike(stack: ElecousStack, categories: list[str]) -> dict[str, Any] | None:
    """HOW MUCH, answered honestly from history: the median size of genuine
    past demand spikes in these categories (episodes.volume_multiplier) and
    how many episodes that median rests on. This is stored history
    serialized — labeled as 'typical past spike', never as a forecast."""
    if not categories:
        return None
    try:
        placeholders = ",".join("?" for _ in categories)
        rows = stack.demand.episodes.db.query_dicts(
            f"""
            SELECT volume_multiplier FROM research_episodes e
            WHERE version = (SELECT max(version) FROM research_episodes WHERE episode_id = e.episode_id)
              AND category IN ({placeholders})
              AND is_control = FALSE AND is_negative = FALSE
              AND volume_multiplier IS NOT NULL
            """,
            list(categories),
        )
    except Exception:
        return None
    values = sorted(_coerce_float(r["volume_multiplier"], 0.0) for r in rows)
    # 1.0 is the migration backfill DEFAULT for episodes recorded before the
    # multiplier existed — indistinguishable from 'unknown'. Treating those
    # as measurements made the median read '×1', which communicates nothing
    # (caught in the live first-time-user audit). Only genuinely measured
    # sizes count; if none exist we say so instead.
    values = [v for v in values if v > 0 and abs(v - 1.0) > 1e-9]
    if not values:
        return None
    median = values[len(values) // 2]
    return {"multiplier": round(median, 2), "episodes": len(values)}


def _first_verdict_due(stack: ElecousStack) -> str | None:
    """The real date the earliest unresolved prediction becomes judgeable
    (created_at + lead_time + 3d tolerance) — computed, never asserted."""
    rows = stack.prediction.db.query_dicts(
        "SELECT created_at, lead_time_days FROM predictions WHERE outcome IS NULL"
    )
    deadlines = [
        _coerce_datetime(r["created_at"]) + timedelta(days=_coerce_float(r["lead_time_days"], 14.0) + 3.0)
        for r in rows
        if _coerce_datetime(r["created_at"]) is not None
    ]
    return timeutil.to_iso(min(deadlines)) if deadlines else None


# -------------------------------------------------------- 9.16 knowledge evolution
@router.get("/knowledge/growth")
def knowledge_growth(request: Request):
    stack = _stack(request)

    db_env = stack.environment.factors.db
    new_factors = db_env.query_one("SELECT count(*) FROM environmental_factors WHERE created_at >= now() - INTERVAL 7 DAY")[0]

    db_val = stack.validation.db
    validations = db_val.query_dicts("SELECT object_type, outcome FROM validations WHERE created_at >= now() - INTERVAL 7 DAY")

    relationships_validated = sum(1 for v in validations if v["object_type"] == "mechanism" and v["outcome"] == "passed")
    hypotheses_rejected = sum(1 for v in validations if v["outcome"] == "failed")
    predictions_strengthened = stack.prediction.db.query_one("SELECT count(*) FROM predictions WHERE validation_status = 'correct' AND created_at >= now() - INTERVAL 7 DAY")[0]

    return {
        "metrics": [
            {"label": "new environmental factors discovered", "value": f"+{new_factors}"},
            {"label": "hypotheses rejected", "value": f"-{hypotheses_rejected}"},
            {"label": "predictions validated correctly", "value": f"+{predictions_strengthened}"},
            {"label": "historical relationships validated", "value": f"+{relationships_validated}"},
        ],
        "totals": {
            "mechanisms": stack.mechanisms.store.counts(),
            "signals": stack.environment.library.counts(),
            "factors": len(stack.environment.factors.list_latest()),
            "research_episodes": len(stack.demand.episodes.list_latest(include_controls=False)),
            "compression": stack.knowledge.compression_report(),
        },
    }


@router.get("/knowledge/search")
def knowledge_search(request: Request, object_type: str | None = None, min_confidence: float = 0.0):
    return {"results": _stack(request).knowledge.graph.search(object_type, min_confidence)}


@router.get("/knowledge/graph")
def knowledge_graph(request: Request, limit: int = 500):
    db = _stack(request).knowledge.graph.db
    nodes = db.query_dicts("SELECT node_id as id, object_type as group, label, confidence FROM knowledge_nodes ORDER BY confidence DESC LIMIT ?", [limit])
    node_ids = [n["id"] for n in nodes]
    if not node_ids:
        return {"nodes": [], "links": []}

    placeholders = ",".join("?" * len(node_ids))
    edges = db.query_dicts(
        f"SELECT source_node as source, target_node as target, relationship as type, weight as value FROM knowledge_edges "
        f"WHERE source_node IN ({placeholders}) AND target_node IN ({placeholders})",
        node_ids * 2,
    )
    return {"nodes": nodes, "links": edges}


# -------------------------------------------------------------- 9.17 self-healing
@router.get("/health/self-healing")
def self_healing(request: Request):
    stack = _stack(request)
    return {
        "status": stack.supervisor.status(),
        "knowledge_integrity": stack.knowledge.self_heal(),
    }


@router.post("/health/self-healing/run")
def run_self_healing(request: Request):
    stack = _stack(request)
    attempts = stack.supervisor.self_heal()
    return {"attempts": [a.__dict__ for a in attempts]}


# --------------------------------------------------------------- 9.18 expansion
@router.get("/expansion")
def expansion(request: Request):
    stack = _stack(request)
    return {
        "observation_requests": stack.gap.open_requests(),
        "candidate_pool": stack.expansion.candidate_pool(),
        "candidate_signals": stack.environment.library.list_latest(lifecycle="candidate"),
        "research_agenda": stack.knowledge.research_agenda()[:20],
    }


@router.post("/expansion/run")
def run_expansion(request: Request):
    return _stack(request).expansion.run_expansion_cycle()


# ------------------------------------------------- Source Discovery / Connector Evolution
@router.get("/discovery")
def discovery(request: Request):
    stack = _stack(request)
    discovered = [_parse(r, ()) for r in stack.source_discovery.list_all()]
    attempts = [_parse(r, ("structure", "best_candidate", "test_result")) for r in stack.connector_evolution.list_all()]
    auto_sources = [
        s for s in stack.observation.registry.list()
        if s["connector"] == "elecous.observation.connectors.auto.AutoConnector"
    ]
    return {
        "discovered_sources": discovered,
        "connector_generation_attempts": attempts,
        "auto_generated_connectors": [
            {"source_id": s["source_id"], "name": s["name"], "status": s["status"], "health": s["health"],
             "failure_count": s["failure_count"]} for s in auto_sources
        ],
        "outcome_counts": {
            outcome: len(stack.connector_evolution.list_all(outcome=outcome))
            for outcome in ("generated", "rejected_no_value_field", "rejected_low_quality", "unsupported_kind")
        },
    }


# ------------------------------------------------------------------- concepts
@router.get("/concepts")
def concepts(request: Request, status: str | None = None):
    # list_all() already fully decodes its JSON columns (ConceptDiscoveryEngine._decode)
    return {"concepts": _stack(request).environment.concepts.list_all(status=status)}


# ----------------------------------------------------------------- experiments
@router.get("/experiments")
def experiments(request: Request):
    stack = _stack(request)
    rows = stack.research.questions.list(status=None, limit=500)
    experiment_rows = [_parse(r, ("expected_evidence", "strategy")) for r in rows if r["kind"] == "experiment"]
    by_status: dict[str, list[dict[str, Any]]] = {}
    for row in experiment_rows:
        by_status.setdefault(row["status"], []).append(row)
    return {
        "running": by_status.get("open", []),
        "completed": [r for r in by_status.get("resolved", []) if r.get("strategy", {}).get("outcome") != "failed"],
        "failed": [r for r in by_status.get("resolved", []) if r.get("strategy", {}).get("outcome") == "failed"],
        "counts": {status: len(rows) for status, rows in by_status.items()},
    }


# ----------------------------------------------------------------- 9.19 system health
@router.get("/health/system")
def system_health(request: Request):
    stack = _stack(request)
    return {
        "observation": {"sources": len(stack.observation.registry.list(status="active"))},
        "demand": {"episodes": len(stack.demand.episodes.list_latest(include_controls=False))},
        "environment": {"factors": len(stack.environment.factors.list_latest())},
        "mechanisms": stack.mechanisms.store.counts(),
        "validation": stack.validation.self_audit(),
        "scheduler": stack.scheduler.report(),
        "scheduler_telemetry": stack.scheduler.telemetry(),
        "databases": stack.databases.integrity_report(),
    }


# -------------------------------------------------------------- 9.20 resources
@router.get("/health/resources")
def resources(request: Request):
    stack = _stack(request)
    return {"sample": stack.resources.last_sample(), "tiers": stack.tiers.report()}


# ----------------------------------------------------------------- 9.21 search
@router.get("/search")
def search(
    request: Request,
    q: str = "",
    kind: str | None = None,
    category: str | None = None,
    region: str | None = None,
    status: str | None = None,
    min_confidence: float = 0.0,
    since: str | None = None,
    until: str | None = None,
):
    """Faceted search over everything ELECOUS knows: free text plus the real
    facets the stores support (category, region, status, confidence) and a
    time period (since/until). Group names are business vocabulary."""
    stack = _stack(request)
    needle = q.lower().strip()
    since_dt = _parse_instant(since, "since")
    until_dt = _parse_instant(until, "until")

    def text_match(*parts: Any) -> bool:
        if not needle:
            return True
        return needle in " ".join(str(p) for p in parts if p is not None).lower()

    def category_match(categories: Any) -> bool:
        if not category:
            return True
        if isinstance(categories, str):
            return categories == category
        return category in [str(c) for c in (categories or [])]

    def region_match(regions: Any) -> bool:
        if not region:
            return True
        if isinstance(regions, str):
            return regions == region
        return region in [str(r) for r in (regions or []) if r]

    def period_match(value: Any) -> bool:
        dt = _coerce_datetime(value)
        if dt is None:
            return since_dt is None and until_dt is None
        if since_dt is not None and dt < since_dt:
            return False
        if until_dt is not None and dt >= until_dt:
            return False
        return True

    results: dict[str, Any] = {}

    if kind in (None, "product"):
        catalog = stack.config.load_file("entities.yaml").get("topics", [])
        episode_counts: dict[str, int] = {}
        try:
            for row in stack.demand.episodes.db.query_dicts(
                "SELECT entity, count(DISTINCT episode_id) AS n, max(spike_start) AS latest FROM research_episodes "
                "WHERE NOT is_control GROUP BY entity"
            ):
                episode_counts[row["entity"]] = row["n"]
        except Exception:
            pass
        items = []
        for topic in catalog:
            name = topic.get("entity", "")
            cat = topic.get("category", "unknown")
            if not text_match(name, cat, topic.get("traits")):
                continue
            if not category_match(cat):
                continue
            items.append({
                "kind": "product",
                "product_id": _product_id(name),
                "title": name,
                "category": cat,
                "recorded_spikes": episode_counts.get(name, 0),
            })
        results["products"] = sorted(items, key=lambda i: -i["recorded_spikes"])[:50]

    if kind in (None, "opportunity"):
        fields = ("categories", "products", "evidence", "lead_time", "confidence", "regions")
        items = []
        for opp in (_parse(o, fields) for o in stack.prediction.opportunities.active()):
            cats = _as_list(opp.get("categories"))
            regions = _as_list(opp.get("regions"))
            if not text_match(cats, opp.get("products"), regions):
                continue
            if not category_match(cats) or not region_match(regions):
                continue
            if status and opp.get("stage") != status:
                continue
            if _confidence_value(opp) < min_confidence:
                continue
            if not period_match(opp.get("created_at")):
                continue
            items.append({
                "kind": "opportunity",
                "opportunity_id": opp["opportunity_id"],
                "title": f"Expected demand rise: {_human_categories(cats)}",
                "probability": _coerce_float(opp.get("probability")),
                "decision": opp.get("decision"),
                "stage": opp.get("stage"),
                "categories": cats,
                "regions": regions,
                "products": _as_list(opp.get("products"))[:6],
                "window": _window_dates(opp.get("created_at"), _as_dict(opp.get("lead_time"))),
                "created_at": _iso(opp.get("created_at")),
            })
        results["opportunities"] = items[:50]

    if kind in (None, "force"):
        items = []
        for f in (_parse(row, ("signals", "categories", "regions", "confidence")) for row in stack.environment.factors.list_latest()):
            cats = _as_list(f.get("categories"))
            regions = _as_list(f.get("regions"))
            if not text_match(f["name"], cats, regions):
                continue
            if not category_match(cats) or not region_match(regions):
                continue
            if status and f.get("lifecycle") != status:
                continue
            if _confidence_value(f) < min_confidence:
                continue
            if not period_match(f.get("created_at")):
                continue
            items.append({
                "kind": "external_force",
                "title": f["name"],
                "categories": cats,
                "regions": regions[:8],
                "confidence": _confidence_value(f),
                "status": f.get("lifecycle"),
                "first_seen": _iso(f.get("created_at")),
            })
        results["external_forces"] = items[:50]

    if kind in (None, "relationship"):
        items = []
        for m in (_parse(row, ("affected_categories", "countries", "supporting_signals", "confidence")) for row in stack.mechanisms.store.list_latest()):
            cats = _as_list(m.get("affected_categories"))
            countries = _as_list(m.get("countries"))
            if not text_match(m["name"], cats, countries):
                continue
            if not category_match(cats) or not region_match(countries):
                continue
            if status and m.get("lifecycle") != status:
                continue
            if _confidence_value(m) < min_confidence:
                continue
            if not period_match(m.get("created_at")):
                continue
            items.append({
                "kind": "relationship",
                "title": m["name"],
                "categories": cats,
                "regions": countries[:8],
                "confidence": _coerce_float(m.get("current_confidence")),
                "status": m.get("lifecycle"),
                "first_seen": _iso(m.get("created_at")),
            })
        results["relationships"] = items[:50]

    if kind in (None, "spike"):
        items = []
        for e in stack.demand.episodes.list_latest(include_controls=False):
            if not text_match(e["entity"], e.get("category"), e.get("country"), e.get("region")):
                continue
            if not category_match(e.get("category")):
                continue
            if not region_match([e.get("country"), e.get("region")]):
                continue
            if status and e.get("validation_status") != status and e.get("lifecycle") != status:
                continue
            if _coerce_float(e.get("overall_confidence")) < min_confidence:
                continue
            if not period_match(e.get("spike_start")):
                continue
            items.append({
                "kind": "demand_spike",
                "title": f"{e['entity']} demand spike",
                "category": e.get("category"),
                "began": _iso(e.get("spike_start")),
                "evidence_score": _coerce_float(e.get("demand_score")),
                "confidence": _coerce_float(e.get("overall_confidence")),
            })
        results["demand_spikes"] = items[:50]

    if kind in (None, "prediction"):
        items = []
        for p in _prediction_claims(stack):
            cats = p["claim"].get("categories", [])
            if not text_match(cats, p["claim"].get("products")):
                continue
            if not category_match(cats):
                continue
            if status and p["validation_status"] != status:
                continue
            if _coerce_float(p["confidence"]) < min_confidence:
                continue
            if not period_match(p["created_at"]):
                continue
            items.append({
                "kind": "prediction",
                "title": f"Prediction: {_human_categories(cats)}",
                "status": p["validation_status"],
                "confidence": _coerce_float(p["confidence"]),
                "made_at": timeutil.to_iso(p["created_at"]),
                "judgment_due": timeutil.to_iso(p["created_at"] + timedelta(days=p["window_days"])),
            })
        results["predictions"] = items[:50]

    results["facets"] = _search_facets(stack)
    return results


def _search_facets(stack: ElecousStack) -> dict[str, Any]:
    categories = sorted({
        str(t.get("category", "unknown"))
        for t in stack.config.load_file("entities.yaml").get("topics", [])
    })
    regions: set[str] = set()
    try:
        for row in stack.demand.episodes.db.query_dicts(
            "SELECT DISTINCT country FROM research_episodes WHERE country IS NOT NULL"
        ):
            if row["country"]:
                regions.add(str(row["country"]))
    except Exception:
        pass
    for opp in stack.prediction.opportunities.active():
        regions.update(str(r) for r in _as_list(opp.get("regions")))
    return {
        "kinds": ["product", "opportunity", "force", "relationship", "spike", "prediction"],
        "categories": categories,
        "regions": sorted(regions),
        "statuses": ["candidate", "experimental", "validated", "trusted", "monitoring", "pending", "correct", "incorrect", "partially_correct"],
    }


# ------------------------------------------------------------- 9.7 timeline stream
from fastapi.responses import StreamingResponse  # noqa: E402
import asyncio  # noqa: E402


@router.get("/timeline/stream")
async def timeline_stream(request: Request):
    stack = _stack(request)
    queue = asyncio.Queue()

    def on_event(event):
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:
            pass

    stack.bus.subscribe_all(on_event)

    async def event_generator():
        past = stack.demand.episodes.list_latest(include_controls=False)[:50]
        present = stack.environment.world.latest_state() or stack.environment.world.build()
        future = stack.prediction.opportunities.active()

        initial_state = {
            "past": [_parse(e, ("traits", "evidence", "timeline", "confidence")) for e in past],
            "present": present,
            "future": [_parse(o, ("categories", "products", "scores", "confidence")) for o in future],
        }
        yield f"data: {json.dumps({'type': 'initial', 'data': initial_state}, default=str)}\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                payload = {
                    "event_type": event.event_type,
                    "event_id": event.event_id,
                    "timestamp": event.timestamp,
                    "source": event.source,
                    "payload": event.payload,
                }
                yield f"data: {json.dumps({'type': 'update', 'data': payload}, default=str)}\n\n"
        finally:
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ------------------------------------------------------------- 9.22 scientific audit
@router.get("/audit/{prediction_id}")
def audit(prediction_id: str, request: Request):
    stack = _stack(request)
    row = stack.prediction.db.query_dicts("SELECT * FROM predictions WHERE prediction_id = ?", [prediction_id])
    if not row:
        raise HTTPException(404, "prediction not found")
    prediction = _parse(row[0], ("mechanisms_used", "evidence", "outcome"))
    opportunity = stack.prediction.opportunities.latest(prediction["opportunity_id"])

    signals: list[str] = []
    for group in _as_dict(prediction.get("evidence")).get("matched_signals", []):
        for s in _as_list(group):
            if s not in signals:
                signals.append(s)
    prediction["domains"] = _extract_domains(signals, stack)

    mechanisms_detail = [
        stack.mechanisms.store.latest(m["id"]) for m in prediction["mechanisms_used"]
    ]
    return {
        "prediction": prediction,
        "opportunity": _parse(opportunity, ("categories", "products", "evidence", "scores", "confidence")) if opportunity else None,
        "mechanisms": [
            _parse(m, ("environmental_factors", "supporting_signals", "behavioural_chain", "confidence")) for m in mechanisms_detail if m
        ],
    }


# ------------------------------------------------------- 9.23 unknown intelligence
@router.get("/unknowns")
def unknowns(request: Request):
    stack = _stack(request)
    return {"unknowns": stack.knowledge.research_agenda()}


# --------------------------------------------------------------- 9.24 prediction replay
@router.get("/predict/replay")
def predict_replay(request: Request, as_of: str):
    stack = _stack(request)
    try:
        instant = timeutil.from_iso(as_of)
    except ValueError as exc:
        raise HTTPException(400, f"invalid as_of: {exc}") from exc
    return stack.prediction.replay(instant)


# -------------------------------------------------------------- 9.25 notifications
@router.get("/notifications")
def notifications(request: Request, unacknowledged_only: bool = False):
    engine = NotificationEngine(_stack(request).databases, _stack(request).config)
    return {"notifications": engine.recent(unacknowledged_only=unacknowledged_only)}


@router.post("/notifications/{notification_id}/acknowledge")
def acknowledge_notification(notification_id: str, request: Request):
    engine = NotificationEngine(_stack(request).databases, _stack(request).config)
    engine.acknowledge(notification_id)
    return {"acknowledged": notification_id}


# ---------------------------------------------------------------------- 9.4 home
@router.get("/home")
def home(request: Request):
    """The 10-second answers (roadmap R6): what deserves attention now and
    why, what changed since yesterday, which predictions failed, what was
    learned today, what to do next — nothing else. Every value is a stored
    row; zero-state renders honestly."""
    stack = _stack(request)
    now = timeutil.utcnow()
    day_ago = now - timedelta(hours=24)

    active = stack.prediction.opportunities.active()
    delta_by_opp = _latest_probability_deltas(stack, [o["opportunity_id"] for o in active])

    decision_rank = {"Act": 0, "Prepare": 1, "Review": 2, "Monitor": 3, "Ignore": 4}
    ranked = sorted(
        active,
        key=lambda o: (
            decision_rank.get(str(o.get("decision")), 5),
            -_coerce_float(o.get("probability")),
            # among duplicates, prefer the newest version of the newest row
            str(o.get("created_at") or ""),
        ),
    )
    attention = []
    seen_category_sets: set[frozenset] = set()
    for opp in ranked:
        if len(attention) >= 6:
            break
        # Attention means attention: rows the decision engine itself marked
        # Ignore do not belong on a list titled "what deserves attention".
        if str(opp.get("decision")) == "Ignore":
            continue
        parsed = _parse(opp, ("categories", "products", "lead_time", "evidence"))
        categories = _as_list(parsed.get("categories"))
        # One card per demand story: the pre-R1 era minted duplicate
        # opportunities for the same category set every 15 minutes and those
        # rows stay 'active' until they expire — never show the same story twice.
        cat_key = frozenset(categories)
        if cat_key in seen_category_sets:
            continue
        seen_category_sets.add(cat_key)
        signals = _opportunity_signals(parsed)
        product_scores = _as_dict(parsed.get("evidence")).get("product_scores") or []
        attention.append({
            "opportunity_id": opp["opportunity_id"],
            "headline": _human_categories(categories),
            "products": _as_list(parsed.get("products"))[:5],
            "product_scores": product_scores[:10],
            "probability": _coerce_float(opp.get("probability")),
            "probability_delta": delta_by_opp.get(opp["opportunity_id"]),
            "decision": opp.get("decision"),
            "stage": opp.get("stage"),
            "why": (f"because {_human_signals(signals)}" if signals else "no live signals stored on this version"),
            "expected_window": _window_dates(opp.get("created_at"), _as_dict(parsed.get("lead_time"))),
            "typical_past_spike": _typical_past_spike(stack, categories),
            "contract": _recommendation_contract(stack, opp),
        })

    # Products lead (the mission question is WHICH PRODUCTS): every product
    # claimed by a non-Ignore attention story, ranked by its story's
    # probability, recency-first within a story (the measured 0.68 ranking
    # signal). Pure aggregation of rows already selected above.
    top_products: list[dict[str, Any]] = []
    seen_products: set[str] = set()
    for item in attention:
        scored = {s["product"]: s for s in item.get("product_scores", [])}
        for product in item["products"]:
            if product in seen_products:
                continue
            seen_products.add(product)
            score = scored.get(product) or {}
            top_products.append({
                "product": product,
                # per-product probability = story x measured participation
                # (roadmap P2); falls back to the story probability only
                # when no participation history exists.
                "probability": score.get("probability", item["probability"]),
                "participation": score.get("participation"),
                "story_probability": item["probability"],
                "story": item["headline"],
                "why": item["why"],
                "expected_window": item["expected_window"],
                "typical_past_spike": item["typical_past_spike"],
                "opportunity_id": item["opportunity_id"],
            })
    top_products.sort(key=lambda p: -(p["probability"] or 0))
    top_products = top_products[:10]

    # what changed since yesterday — from the same real event derivation as /feed
    all_events = _derive_feed_events(stack)
    day_ago_iso = timeutil.to_iso(day_ago)
    recent_events = [e for e in all_events if e["at"] >= day_ago_iso]
    changed = {
        "strengthened": [e for e in recent_events if e["kind"] == "opportunity_strengthened"][:5],
        "weakened": [e for e in recent_events if e["kind"] == "opportunity_weakened"][:5],
        "new": [e for e in recent_events if e["kind"] == "opportunity_new"][:5],
        "window_hours": 24,
    }

    # prediction verdicts — real counts; zero judged says when the first verdict is due
    counts_rows = stack.prediction.db.query_dicts(
        "SELECT validation_status, count(*) AS n FROM predictions GROUP BY validation_status"
    )
    counts = {r["validation_status"]: r["n"] for r in counts_rows}
    judged = sum(v for k, v in counts.items() if k in ("correct", "incorrect", "partially_correct"))
    failed_recent = [e for e in recent_events if e["kind"] == "prediction_failed"][:5]
    verdicts = {
        "counts": counts,
        "judged": judged,
        "accuracy": round(counts.get("correct", 0) / judged, 4) if judged else None,
        "failed_recent": failed_recent,
        "first_verdict_due": _first_verdict_due(stack),
    }

    # what ELECOUS learned today — real 24h counts
    val_rows = stack.validation.db.query_dicts(
        "SELECT outcome, count(*) AS n FROM validations WHERE created_at >= now() - INTERVAL 1 DAY GROUP BY outcome"
    )
    val_counts = {r["outcome"]: r["n"] for r in val_rows}
    new_relationships = stack.environment.factors.db.query_one(
        "SELECT count(*) FROM environmental_factors WHERE version = 1 AND created_at >= now() - INTERVAL 1 DAY"
    )[0]
    new_sources = sum(
        1 for s in stack.observation.registry.list()
        if (_coerce_datetime(s.get("created_at")) or now) >= day_ago
    )
    ecosystem_updates = 0
    try:
        ecosystem_updates = stack.demand.episodes.db.query_one(
            "SELECT count(*) FROM product_force_links WHERE updated_at >= now() - INTERVAL 1 DAY"
        )[0]
    except Exception:
        pass
    learned = {
        "relationships_validated_today": val_counts.get("passed", 0),
        "relationships_rejected_today": val_counts.get("failed", 0),
        "new_relationships_today": new_relationships,
        "new_sources_today": new_sources,
        "product_force_updates_today": ecosystem_updates,
    }

    # what to do next — the stored decisions, strongest first
    do_next = [
        {
            "action": item["decision"],
            "headline": item["headline"],
            "probability": item["probability"],
            "window": item["expected_window"],
            "products": item["products"],
        }
        for item in attention if item["decision"] in ("Act", "Prepare", "Review")
    ][:5]

    latest_bench = stack.validation.benchmarks.latest("rolling_vs_baseline")
    bench_metrics = _as_dict(latest_bench.get("metrics")) if latest_bench else None

    return {
        "as_of": timeutil.to_iso(now),
        "attention": attention,
        "top_products": top_products,
        "changed": changed,
        "verdicts": verdicts,
        "learned": learned,
        "do_next": do_next,
        "benchmark": _honest_benchmark(bench_metrics),
        "coverage": _coverage(stack),
        "feed_preview": all_events[:8],
        "observation_network": {"sources": len(stack.observation.registry.list(status="active"))},
        # Stories, not rows: the pre-R1 era left duplicate active rows per
        # category set (expire_superseded cleans them on the next predict
        # cycle); the user-facing count must never inflate on duplicates.
        "opportunities_active": len({
            frozenset(_as_list(_parse(o, ("categories",)).get("categories"))) for o in active
        }),
        "opportunity_rows_active": len(active),
    }


def _latest_probability_deltas(stack: ElecousStack, opportunity_ids: list[str]) -> dict[str, float]:
    """probability change between the last two stored versions of each
    opportunity — real versioned rows, the quantity R1's evolve() emits."""
    if not opportunity_ids:
        return {}
    placeholders = ",".join("?" * len(opportunity_ids))
    rows = stack.prediction.db.query_dicts(
        f"SELECT opportunity_id, version, probability FROM opportunities "
        f"WHERE opportunity_id IN ({placeholders}) ORDER BY opportunity_id, version",
        list(opportunity_ids),
    )
    by_opp: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_opp.setdefault(r["opportunity_id"], []).append(r)
    return {
        opp_id: round(_coerce_float(vs[-1]["probability"]) - _coerce_float(vs[-2]["probability"]), 4)
        for opp_id, vs in by_opp.items() if len(vs) >= 2
    }


# ---------------------------------------------------- products: ranking + page
RANK_SORTS: dict[str, str] = {
    "largest_expected_spike": "ordered by the probability of the live opportunity claiming each product — biggest expected demand rise first",
    "strongest_evidence": "ordered by confirmed demand spikes of the product's own — most and most recent evidence first",
    "newest_opportunity": "ordered by when each product's newest live opportunity was identified — most recent first",
    "most_improved": "ordered by how much each product's live opportunity strengthened since its previous assessment (probability change)",
    "recently_failed": "ordered by the most recent prediction about the product that real demand data judged wrong",
}


def _product_rows(stack: ElecousStack) -> list[dict[str, Any]]:
    """Every monitored product with its real evidence aggregates. Positions
    always exist; products without evidence say so instead of faking a
    score (the old decision_score blend is gone)."""
    catalog = stack.config.load_file("entities.yaml").get("topics", [])
    products: dict[str, dict[str, Any]] = {}
    for topic in catalog:
        name = topic.get("entity", "unknown")
        products[name] = {
            "product_id": _product_id(name),
            "product": name,
            "category": topic.get("category", "unknown"),
            "status": "monitored",
            "why": None,
            "spike_count": 0,
            "latest_spike_at": None,
            "strongest_spike_score": 0.0,
            "expected_spike_probability": None,
            "probability_delta": None,
            "opportunity_newest_at": None,
            "expected_window": None,
            "decision": None,
            "last_verdict": None,
        }

    # real episode aggregates, one query
    try:
        for row in stack.demand.episodes.db.query_dicts(
            "SELECT entity, count(DISTINCT episode_id) AS n, max(spike_start) AS latest, max(demand_score) AS best "
            "FROM research_episodes e WHERE version = (SELECT max(version) FROM research_episodes WHERE episode_id = e.episode_id) "
            "AND NOT is_control AND NOT is_negative GROUP BY entity"
        ):
            item = products.get(row["entity"])
            if item is None:
                continue
            item["spike_count"] = row["n"]
            item["latest_spike_at"] = _iso(row["latest"])
            item["strongest_spike_score"] = _coerce_float(row["best"])
            item["status"] = "evidence building"
            item["why"] = f"{row['n']} confirmed demand spikes, most recent {_iso(row['latest'])[:10]}"
    except Exception:
        pass

    # live opportunities claiming each product
    active = stack.prediction.opportunities.active()
    deltas = _latest_probability_deltas(stack, [o["opportunity_id"] for o in active])
    for opp in active:
        parsed = _parse(opp, ("categories", "products", "lead_time", "evidence"))
        probability = _coerce_float(opp.get("probability"))
        window = _window_dates(opp.get("created_at"), _as_dict(parsed.get("lead_time")))
        signals = _opportunity_signals(parsed)
        why = f"live opportunity at {probability:.0%}" + (f" because {_human_signals(signals)}" if signals else "")
        for name in _as_list(parsed.get("products")):
            item = products.get(str(name))
            if item is None:
                continue
            if item["expected_spike_probability"] is None or probability > item["expected_spike_probability"]:
                item["expected_spike_probability"] = probability
                item["expected_window"] = window
                item["decision"] = opp.get("decision")
                item["why"] = why
            delta = deltas.get(opp["opportunity_id"])
            if delta is not None and (item["probability_delta"] is None or abs(delta) > abs(item["probability_delta"])):
                item["probability_delta"] = delta
            created = _iso(opp.get("created_at"))
            if item["opportunity_newest_at"] is None or (created and created > item["opportunity_newest_at"]):
                item["opportunity_newest_at"] = created
            item["status"] = "live opportunity"

    # per-product verdicts from resolved predictions (outcome.product_hits, R2)
    for pred in stack.prediction.db.query_dicts(
        "SELECT outcome, validation_status FROM predictions WHERE outcome IS NOT NULL ORDER BY created_at DESC LIMIT 500"
    ):
        outcome = _as_dict(pred.get("outcome"))
        hits = outcome.get("product_hits") or {}
        resolved_at = outcome.get("resolved_at")
        for name, hit in hits.items():
            item = products.get(str(name))
            if item is None:
                continue
            if item["last_verdict"] is None or (resolved_at and resolved_at > item["last_verdict"]["at"]):
                item["last_verdict"] = {
                    "verdict": "correct" if hit else "wrong",
                    "at": resolved_at,
                    "prediction_status": pred["validation_status"],
                }

    for item in products.values():
        if item["why"] is None:
            item["why"] = "no demand evidence recorded yet — monitored, not scored"
    return list(products.values())


@router.get("/products")
def products(request: Request, sort: str = "largest_expected_spike", limit: int = 500):
    stack = _stack(request)
    if sort not in RANK_SORTS:
        raise HTTPException(400, f"unknown sort '{sort}'; one of {sorted(RANK_SORTS)}")
    items = _product_rows(stack)

    def has(value: Any) -> bool:
        return value is not None

    if sort == "largest_expected_spike":
        keyed = [((0 if has(i["expected_spike_probability"]) else 1, -( i["expected_spike_probability"] or 0), -i["spike_count"]), i) for i in items]
    elif sort == "strongest_evidence":
        keyed = [((0 if i["spike_count"] else 1, -i["spike_count"], -(1 if i["latest_spike_at"] else 0)), i) for i in items]
    elif sort == "newest_opportunity":
        keyed = [
            ((0, -_ts_order(i["opportunity_newest_at"])) if has(i["opportunity_newest_at"]) else (1, 0.0), i)
            for i in items
        ]
    elif sort == "most_improved":
        keyed = [((0 if has(i["probability_delta"]) else 1, -(i["probability_delta"] or 0)), i) for i in items]
    else:  # recently_failed
        keyed = [
            (
                (0, -_ts_order(i["last_verdict"]["at"])) if (i["last_verdict"] and i["last_verdict"]["verdict"] == "wrong") else (1, -i["spike_count"]),
                i,
            )
            for i in items
        ]
    keyed.sort(key=lambda pair: pair[0])
    ranked = []
    for rank, (_, item) in enumerate(keyed, start=1):
        entry = dict(item)
        entry["rank"] = rank
        ranked.append(entry)
    return {
        "sort": sort,
        "sort_explanation": RANK_SORTS[sort],
        "available_sorts": {k: v for k, v in RANK_SORTS.items()},
        "total": len(ranked),
        "products": ranked[: max(1, min(int(limit), 1000))],
    }


def _ts_order(value: Any) -> float:
    dt = _coerce_datetime(value)
    return dt.timestamp() if dt else 0.0


@router.get("/products/{product_id}")
def product_detail(product_id: str, request: Request):
    """The product page: its own demand evidence with dates, the external
    forces that historically preceded its spikes, claimed-in opportunities,
    per-product prediction verdicts, confidence history from versioned
    opportunity rows, and related products — every section from stored data
    or an honest 'not enough evidence yet'."""
    stack = _stack(request)
    catalog = stack.config.load_file("entities.yaml").get("topics", [])
    topic = next((t for t in catalog if _product_id(t.get("entity", "")) == product_id), None)
    if topic is None:
        raise HTTPException(404, "product not found")
    name = topic["entity"]
    category = topic.get("category", "unknown")

    # ---- its own episode history, exact dates
    episodes = stack.demand.episodes.list_latest(entity=name, include_controls=False)
    history = [
        {
            "began": _iso(e["spike_start"]),
            "peaked": _iso(e.get("spike_peak")),
            "recovered": _iso(e.get("recovery_end")),
            "evidence_score": _coerce_float(e["demand_score"]),
            "confidence": _coerce_float(e.get("overall_confidence")),
            "unexpected_absence": bool(e.get("is_negative")),
        }
        for e in episodes
    ]
    current_evidence = history[0] if history else None

    # ---- external forces that historically preceded this category's spikes
    forces = []
    for f in stack.environment.factors.list_latest(category=category):
        lead = _as_dict(f.get("lead_time"))
        forces.append({
            "force": f["name"],
            "typical_lead_days": lead.get("avg"),
            "confidence": _coerce_float(f.get("current_confidence")),
            "supporting_spikes": len(_as_list(f.get("supporting_episodes"))),
            "contradicting_spikes": len(_as_list(f.get("contradictory_episodes"))),
            "status": f.get("lifecycle"),
        })
    forces.sort(key=lambda f: -f["confidence"])

    # ---- learned per-product force links (ecosystem, correction #4)
    learned_forces = []
    try:
        for link in stack.demand.episodes.db.query_dicts(
            "SELECT force, supports, contradictions, updated_at FROM product_force_links WHERE product = ? "
            "ORDER BY supports DESC, contradictions ASC",
            [name],
        ):
            learned_forces.append({
                "force": str(link["force"]).replace("_", " "),
                "confirmed_by_outcomes": link["supports"],
                "contradicted_by_outcomes": link["contradictions"],
                "updated_at": _iso(link["updated_at"]),
            })
    except Exception:
        pass

    # ---- opportunities claiming this product + confidence history from versions
    claiming = []
    confidence_history = []
    try:
        state_times = {
            r["state_id"]: r["as_of"]
            for r in stack.environment.world.db.query_dicts("SELECT state_id, as_of FROM world_states")
        }
    except Exception:
        state_times = {}
    all_versions = stack.prediction.db.query_dicts("SELECT * FROM opportunities ORDER BY opportunity_id, version")
    latest_by_opp: dict[str, dict[str, Any]] = {}
    for row in all_versions:
        latest_by_opp[row["opportunity_id"]] = row
        if name in _as_list(row.get("products")):
            at = state_times.get(row["world_state_id"]) or row.get("created_at")
            confidence_history.append({
                "at": _iso(at),
                "probability": _coerce_float(row["probability"]),
                "stage": row.get("stage"),
            })
    active_ids = {o["opportunity_id"] for o in stack.prediction.opportunities.active()}
    for opp_id, row in latest_by_opp.items():
        if opp_id not in active_ids or name not in _as_list(row.get("products")):
            continue
        parsed = _parse(row, ("categories", "lead_time", "evidence"))
        signals = _opportunity_signals(parsed)
        claiming.append({
            "opportunity_id": opp_id,
            "headline": _human_categories(_as_list(parsed.get("categories"))),
            "probability": _coerce_float(row["probability"]),
            "decision": row.get("decision"),
            "stage": row.get("stage"),
            "why": (f"because {_human_signals(signals)}" if signals else None),
            "expected_window": _window_dates(row.get("created_at"), _as_dict(parsed.get("lead_time"))),
        })
    claiming.sort(key=lambda o: -o["probability"])
    confidence_history.sort(key=lambda c: str(c["at"]))

    # ---- per-product prediction verdicts (outcome.product_hits, R2)
    verdicts = []
    for pred in stack.prediction.db.query_dicts(
        "SELECT evidence, outcome, validation_status, created_at FROM predictions WHERE outcome IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 500"
    ):
        outcome = _as_dict(pred.get("outcome"))
        hits = outcome.get("product_hits") or {}
        if name not in hits:
            continue
        claim = _as_dict(_as_dict(pred.get("evidence")).get("claim"))
        verdicts.append({
            "made_at": _iso(pred["created_at"]),
            "judged_at": outcome.get("resolved_at"),
            "verdict_for_this_product": "demand spike happened" if hits[name] else "no demand spike — claim was wrong for this product",
            "prediction_status": pred["validation_status"],
            "claimed_categories": claim.get("categories", []),
        })
    verdicts_note = None
    if not verdicts:
        due = _first_verdict_due(stack)
        pending_claims = any(
            name in p["claim"].get("products", []) for p in _prediction_claims(stack) if p["outcome"] is None
        )
        if pending_claims and due:
            verdicts_note = f"no verdicts yet — first verdicts due {due[:10]}"
        elif due:
            verdicts_note = f"no prediction has claimed this product yet; first platform verdicts due {due[:10]}"
        else:
            verdicts_note = "no predictions have been issued yet"

    # ---- related products: same category + shared force evidence, never faked
    related = []
    own_forces = {lf["force"] for lf in learned_forces}
    episode_entities: dict[str, int] = {}
    try:
        for row in stack.demand.episodes.db.query_dicts(
            "SELECT entity, count(DISTINCT episode_id) AS n FROM research_episodes WHERE category = ? AND NOT is_control GROUP BY entity",
            [category],
        ):
            episode_entities[row["entity"]] = row["n"]
    except Exception:
        pass
    for other in catalog:
        other_name = other.get("entity", "")
        if other_name == name or other.get("category") != category:
            continue
        shared: list[str] = []
        if own_forces:
            try:
                for link in stack.demand.episodes.db.query_dicts(
                    "SELECT force FROM product_force_links WHERE product = ?", [other_name]
                ):
                    force = str(link["force"]).replace("_", " ")
                    if force in own_forces:
                        shared.append(force)
            except Exception:
                pass
        if shared:
            related.append({
                "product": other_name,
                "product_id": _product_id(other_name),
                "reason": f"shares learned external forces: {', '.join(sorted(set(shared))[:3])}",
            })
        elif episode_entities.get(other_name) and history:
            related.append({
                "product": other_name,
                "product_id": _product_id(other_name),
                "reason": f"same category with {episode_entities[other_name]} recorded demand spikes of its own",
            })
    related = related[:8]

    return {
        "product_id": product_id,
        "product": name,
        "category": category,
        "traits": topic.get("traits", []),
        "current_demand_evidence": current_evidence,
        "current_demand_note": None if current_evidence else "no demand spikes recorded for this product yet",
        "episode_history": history,
        "preceding_forces": forces,
        "preceding_forces_note": None if forces else "not enough evidence yet — no external force has been linked to this product's category",
        "learned_force_links": learned_forces,
        "learned_force_links_note": None if learned_forces else "no judged prediction outcomes have linked forces to this product yet",
        "opportunities": claiming,
        "opportunities_note": None if claiming else "no live opportunity currently claims this product",
        "prediction_verdicts": verdicts,
        "prediction_verdicts_note": verdicts_note,
        "confidence_history": confidence_history,
        "confidence_history_note": None if confidence_history else "no opportunity has claimed this product, so there is no confidence history yet",
        "related_products": related,
        "related_products_note": None if related else "not enough evidence yet to identify related products",
    }
