"""Dashboard API routes — one per spec panel (ch. 9). Read-only; every value
returned already exists inside ELECOUS (9.27: traceable, reproducible,
validated, timestamped, evidence-backed, versioned — otherwise it cannot be
displayed).
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
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
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
            confidence.get("overall", confidence.get("current", confidence.get("scientific"))),
            0.0,
        )
    return _coerce_float(row.get("current_confidence", row.get("confidence")), 0.0)


def _product_intelligence(stack: ElecousStack) -> dict[str, dict[str, Any]]:
    """Compose product-facing intelligence from already-computed scientific
    rows. This route layer does not create new mechanisms or predictions; it
    only organizes monitored entities, opportunities, predictions, episodes
    and validated relationships for a business reader.
    """

    def ensure(name: str, category: str = "unknown", traits: list[str] | None = None, article: str | None = None) -> dict[str, Any]:
        if name not in products:
            products[name] = {
                "product_id": _product_id(name),
                "product": name,
                "category": category,
                "article": article,
                "traits": traits or [],
                "rank": None,
                "decision_score": 0.0,
                "current_demand_score": 0.0,
                "expected_demand_pressure": 0.0,
                "confidence": 0.0,
                "status": "monitored",
                "strongest_force": None,
                "lead_time_days": None,
                "historical_spike_count": 0,
                "latest_spike_at": None,
                "future_opportunity_count": 0,
                "prediction_counts": {},
                "validated_relationships": 0,
                "environmental_forces": set(),
                "regions": set(),
                "opportunities": [],
                "recent_predictions": [],
                "historical_spikes": [],
                "relationships": [],
            }
        return products[name]

    products: dict[str, dict[str, Any]] = {}
    for topic in stack.config.load_file("entities.yaml").get("topics", []):
        ensure(
            topic.get("entity", "unknown"),
            topic.get("category", "unknown"),
            topic.get("traits", []),
            topic.get("article"),
        )

    opportunity_fields = (
        "categories",
        "products",
        "activated_mechanisms",
        "environmental_factors",
        "evidence",
        "scores",
        "lead_time",
        "confidence",
        "uncertainty",
        "business_value",
        "regions",
    )
    opportunities = [_parse(o, opportunity_fields) for o in stack.prediction.opportunities.active()]
    opportunity_products: dict[str, list[str]] = {}
    for opportunity in opportunities:
        names = [str(p) for p in _as_list(opportunity.get("products"))]
        opportunity_products[opportunity["opportunity_id"]] = names
        categories = [str(c) for c in _as_list(opportunity.get("categories"))]
        forces = [str(f) for f in _as_list(opportunity.get("environmental_factors"))]
        regions = [str(r) for r in _as_list(opportunity.get("regions"))]
        lead_time = _as_dict(opportunity.get("lead_time"))
        lead_days = lead_time.get("most_probable")
        probability = _coerce_float(opportunity.get("probability"))
        confidence = _confidence_value(opportunity)
        for name in names:
            item = ensure(name, categories[0] if categories else "unknown")
            item["future_opportunity_count"] += 1
            item["expected_demand_pressure"] = max(item["expected_demand_pressure"], probability)
            item["confidence"] = max(item["confidence"], confidence)
            if item["lead_time_days"] is None or (
                lead_days is not None and _coerce_float(lead_days, 9999) < _coerce_float(item["lead_time_days"], 9999)
            ):
                item["lead_time_days"] = lead_days
            item["status"] = "live_opportunity"
            item["environmental_forces"].update(forces)
            item["regions"].update(regions)
            if not item["strongest_force"] and forces:
                item["strongest_force"] = forces[0]
            item["opportunities"].append(
                {
                    "opportunity_id": opportunity["opportunity_id"],
                    "stage": opportunity.get("stage"),
                    "probability": probability,
                    "confidence": confidence,
                    "categories": categories,
                    "lead_time": lead_time,
                    "environmental_factors": forces,
                    "regions": regions,
                    "created_at": _iso(opportunity.get("created_at")),
                    "expires_at": _iso(opportunity.get("expires_at")),
                }
            )

    episode_fields = (
        "traits",
        "evidence",
        "timeline",
        "signal_agreement",
        "confidence",
        "control_episodes",
        "mechanisms_found",
        "false_spike_checks",
    )
    for episode in (_parse(e, episode_fields) for e in stack.demand.episodes.list_latest(include_controls=False)):
        item = ensure(episode["entity"], episode.get("category", "unknown"), _as_list(episode.get("traits")))
        score = _coerce_float(episode.get("demand_score"))
        item["historical_spike_count"] += 1
        item["current_demand_score"] = max(item["current_demand_score"], score)
        if item["latest_spike_at"] is None or str(episode.get("spike_start")) > item["latest_spike_at"]:
            item["latest_spike_at"] = _iso(episode.get("spike_start"))
        item["historical_spikes"].append(
            {
                "episode_id": episode["episode_id"],
                "spike_start": _iso(episode.get("spike_start")),
                "spike_peak": _iso(episode.get("spike_peak")),
                "demand_score": score,
                "confidence": _coerce_float(episode.get("overall_confidence")),
                "validation_status": episode.get("validation_status"),
            }
        )

    mechanism_fields = (
        "environmental_factors",
        "supporting_signals",
        "behavioural_chain",
        "product_traits",
        "affected_categories",
        "countries",
        "confidence",
        "business_value",
        "stability",
        "supporting_episodes",
    )
    mechanisms_by_category: dict[str, list[dict[str, Any]]] = {}
    for mechanism in (_parse(m, mechanism_fields) for m in stack.mechanisms.store.list_latest()):
        summary = {
            "mechanism_id": mechanism["mechanism_id"],
            "name": mechanism["name"],
            "lifecycle": mechanism["lifecycle"],
            "confidence": _coerce_float(mechanism.get("current_confidence")),
            "supporting_signals": _as_list(mechanism.get("supporting_signals")),
            "environmental_factors": _as_list(mechanism.get("environmental_factors")),
            "behavioural_chain": _as_list(mechanism.get("behavioural_chain")),
            "lead_time": _as_dict(mechanism.get("lead_time")),
            "countries": _as_list(mechanism.get("countries")),
            "created_at": _iso(mechanism.get("created_at")),
        }
        for category in _as_list(mechanism.get("affected_categories")):
            mechanisms_by_category.setdefault(str(category), []).append(summary)

    for item in products.values():
        relationships = mechanisms_by_category.get(item["category"], [])
        item["relationships"] = relationships[:12]
        item["validated_relationships"] = sum(
            1 for rel in relationships if rel["lifecycle"] in ("validated", "trusted")
        )
        for rel in relationships[:6]:
            item["environmental_forces"].update(str(s) for s in rel["supporting_signals"])
            item["regions"].update(str(c) for c in rel["countries"])
            if not item["strongest_force"] and rel["supporting_signals"]:
                item["strongest_force"] = rel["supporting_signals"][0]
            item["confidence"] = max(item["confidence"], rel["confidence"])

    prediction_rows = stack.prediction.db.query_dicts(
        "SELECT prediction_id, opportunity_id, confidence, lead_time_days, validation_status, outcome, created_at "
        "FROM predictions ORDER BY created_at DESC LIMIT 1000"
    )
    for prediction in prediction_rows:
        for name in opportunity_products.get(prediction["opportunity_id"], []):
            item = ensure(name)
            status = prediction["validation_status"]
            item["prediction_counts"][status] = item["prediction_counts"].get(status, 0) + 1
            item["recent_predictions"].append(
                {
                    "prediction_id": prediction["prediction_id"],
                    "confidence": _coerce_float(prediction["confidence"]),
                    "lead_time_days": prediction.get("lead_time_days"),
                    "validation_status": status,
                    "created_at": _iso(prediction.get("created_at")),
                    "outcome": _as_dict(prediction.get("outcome")),
                }
            )

    for item in products.values():
        pending = 1.0 if item["prediction_counts"].get("pending", 0) else 0.0
        item["decision_score"] = round(
            100
            * (
                0.45 * item["expected_demand_pressure"]
                + 0.2 * item["confidence"]
                + 0.15 * min(item["validated_relationships"] / 6, 1.0)
                + 0.1 * min(item["historical_spike_count"] / 5, 1.0)
                + 0.1 * pending
            ),
            1,
        )
        if item["status"] == "monitored" and (item["historical_spike_count"] or item["validated_relationships"]):
            item["status"] = "evidence_building"
        item["environmental_forces"] = sorted(item["environmental_forces"])
        item["regions"] = sorted(item["regions"])
        item["opportunities"] = sorted(item["opportunities"], key=lambda o: o["probability"], reverse=True)
        item["recent_predictions"] = item["recent_predictions"][:10]
        item["historical_spikes"] = item["historical_spikes"][:10]
    return products


def _sorted_products(products: list[dict[str, Any]], sort: str) -> tuple[list[dict[str, Any]], str]:
    explanations = {
        "future_demand": "ranked by live opportunity probability, confidence, validated relationships and pending predictions",
        "environmental_pressure": "ranked by strongest active demand pressure from current opportunities",
        "largest_spike": "ranked by the largest historical demand score already observed",
        "most_validated": "ranked by validated or trusted relationships in the product category",
        "highest_risk": "ranked by live pressure with weaker confidence",
        "lowest_confidence": "ranked by low confidence first so uncertainty is visible",
        "newest_opportunity": "ranked by most recently created live opportunity",
        "most_evidence": "ranked by historical spikes plus validated relationships",
    }
    if sort == "environmental_pressure":
        key = lambda p: (p["expected_demand_pressure"], p["confidence"], p["product"])
        reverse = True
    elif sort == "largest_spike":
        key = lambda p: (p["current_demand_score"], p["historical_spike_count"], p["product"])
        reverse = True
    elif sort == "most_validated":
        key = lambda p: (p["validated_relationships"], p["confidence"], p["product"])
        reverse = True
    elif sort == "highest_risk":
        key = lambda p: (p["expected_demand_pressure"] * (1 - p["confidence"]), p["product"])
        reverse = True
    elif sort == "lowest_confidence":
        key = lambda p: (p["confidence"], -p["expected_demand_pressure"], p["product"])
        reverse = False
    elif sort == "newest_opportunity":
        key = lambda p: (max((o["created_at"] or "" for o in p["opportunities"]), default=""), p["product"])
        reverse = True
    elif sort == "most_evidence":
        key = lambda p: (p["historical_spike_count"] + p["validated_relationships"], p["confidence"], p["product"])
        reverse = True
    else:
        key = lambda p: (p["decision_score"], p["expected_demand_pressure"], p["confidence"], p["product"])
        reverse = True
        sort = "future_demand"
    ranked = sorted(products, key=key, reverse=reverse)
    for idx, product in enumerate(ranked, start=1):
        product["rank"] = idx
    return ranked, explanations[sort]


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


# ------------------------------------------------------------- 9.7 timeline
@router.get("/timeline")
def timeline(request: Request):
    stack = _stack(request)
    past = stack.demand.episodes.list_latest(include_controls=False)[:50]
    present = stack.environment.world.latest_state() or stack.environment.world.build()
    future = stack.prediction.opportunities.active()
    parsed_past = [_parse(e, ("traits", "evidence", "timeline", "confidence")) for e in past]
    parsed_future = [_parse(o, ("categories", "products", "scores", "confidence", "lead_time", "environmental_factors", "regions")) for o in future]
    events: list[dict[str, Any]] = []
    for episode in parsed_past:
        events.append(
            {
                "event_id": episode["episode_id"],
                "kind": "historical_spike",
                "phase": "past",
                "at": _iso(episode.get("spike_start")),
                "title": f"{episode['entity']} demand spike",
                "product": episode["entity"],
                "category": episode.get("category"),
                "confidence": _coerce_float(episode.get("overall_confidence")),
                "validation_status": episode.get("validation_status"),
                "demand_score": _coerce_float(episode.get("demand_score")),
            }
        )
    state_at = _iso(present.get("as_of") or present.get("created_at"))
    for signal in present.get("signals", []):
        signal_key = signal.get("signal_key") or ":".join(
            str(signal.get(part, "")) for part in ("phenomenon", "direction", "scope")
        ).strip(":")
        events.append(
            {
                "event_id": f"signal:{signal_key}:{state_at}",
                "kind": "world_change",
                "phase": "present",
                "at": state_at,
                "title": signal_key,
                "signal_key": signal_key,
                "scope": signal.get("scope"),
                "magnitude": signal.get("z"),
                "evidence": signal,
            }
        )
    for opportunity in parsed_future:
        created = _coerce_datetime(opportunity.get("created_at"))
        lead = _as_dict(opportunity.get("lead_time"))
        expected_at = None
        if created is not None and lead.get("most_probable") is not None:
            expected_at = timeutil.to_iso(created + timedelta(days=_coerce_float(lead.get("most_probable"))))
        events.append(
            {
                "event_id": opportunity["opportunity_id"],
                "kind": "future_opportunity",
                "phase": "future",
                "at": expected_at or _iso(opportunity.get("created_at")),
                "issued_at": _iso(opportunity.get("created_at")),
                "title": ", ".join(_as_list(opportunity.get("categories"))) or "Demand opportunity",
                "products": _as_list(opportunity.get("products")),
                "categories": _as_list(opportunity.get("categories")),
                "confidence": _confidence_value(opportunity),
                "probability": _coerce_float(opportunity.get("probability")),
                "stage": opportunity.get("stage"),
                "lead_time": lead,
                "environmental_factors": _as_list(opportunity.get("environmental_factors")),
                "regions": _as_list(opportunity.get("regions")),
            }
        )
    predictions = stack.prediction.db.query_dicts(
        "SELECT prediction_id, opportunity_id, confidence, lead_time_days, validation_status, outcome, created_at "
        "FROM predictions ORDER BY created_at DESC LIMIT 100"
    )
    for prediction in predictions:
        events.append(
            {
                "event_id": prediction["prediction_id"],
                "kind": "prediction",
                "phase": "future" if prediction["validation_status"] == "pending" else "past",
                "at": _iso(prediction.get("created_at")),
                "title": f"Prediction {prediction['validation_status']}",
                "confidence": _coerce_float(prediction.get("confidence")),
                "lead_time_days": prediction.get("lead_time_days"),
                "validation_status": prediction.get("validation_status"),
                "opportunity_id": prediction.get("opportunity_id"),
            }
        )
    validations = stack.validation.db.query_dicts(
        "SELECT validation_id, object_type, object_id, method, outcome, created_at FROM validations ORDER BY created_at DESC LIMIT 100"
    )
    for validation in validations:
        events.append(
            {
                "event_id": validation["validation_id"],
                "kind": "validation",
                "phase": "past",
                "at": _iso(validation.get("created_at")),
                "title": f"{validation['object_type']} validation {validation['outcome']}",
                "object_type": validation.get("object_type"),
                "object_id": validation.get("object_id"),
                "method": validation.get("method"),
                "outcome": validation.get("outcome"),
            }
        )
    events.sort(key=lambda e: e.get("at") or "", reverse=True)
    return {
        "events": events,
        "past": parsed_past,
        "present": present,
        "future": parsed_future,
    }


# ------------------------------------------------------ 9.8-9.9 opportunity radar
@router.get("/opportunities")
def opportunities(request: Request):
    stack = _stack(request)
    rows = stack.prediction.opportunities.active()
    fields = ("categories", "products", "activated_mechanisms", "environmental_factors", "scores", "lead_time", "confidence", "uncertainty", "business_value", "regions")
    return {"opportunities": [_parse(r, fields) for r in rows]}


@router.get("/opportunities/{opportunity_id}")
def opportunity_detail(opportunity_id: str, request: Request):
    stack = _stack(request)
    row = stack.prediction.opportunities.latest(opportunity_id)
    if row is None:
        raise HTTPException(404, "opportunity not found")
    fields = ("categories", "products", "activated_mechanisms", "environmental_factors", "evidence", "scores", "lead_time", "confidence", "uncertainty", "business_value", "regions")
    return _parse(row, fields)


# ------------------------------------------------------------ product intelligence
@router.get("/products")
def products(
    request: Request,
    q: str | None = None,
    category: str | None = None,
    status: str | None = None,
    sort: str = "future_demand",
):
    items = list(_product_intelligence(_stack(request)).values())
    needle = (q or "").strip().lower()
    if needle:
        items = [
            item
            for item in items
            if needle
            in " ".join(
                [
                    item["product"],
                    item["category"],
                    " ".join(item["traits"]),
                    " ".join(item["environmental_forces"]),
                    " ".join(item["regions"]),
                ]
            ).lower()
        ]
    if category:
        items = [item for item in items if item["category"] == category]
    if status:
        items = [item for item in items if item["status"] == status]
    ranked, explanation = _sorted_products(items, sort)
    return {
        "as_of": timeutil.to_iso(timeutil.utcnow()),
        "count": len(ranked),
        "sort": sort,
        "sort_explanation": explanation,
        "products": ranked,
    }


@router.get("/products/{product_id}")
def product_detail(product_id: str, request: Request):
    products_by_name = _product_intelligence(_stack(request))
    for item in products_by_name.values():
        if product_id.lower() in (item["product"].lower(), item["product_id"]):
            ranked, _ = _sorted_products(list(products_by_name.values()), "future_demand")
            rank_by_id = {p["product_id"]: p["rank"] for p in ranked}
            item["rank"] = rank_by_id.get(item["product_id"])
            return item
    raise HTTPException(404, "product not found")


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
    return {"factors": [_parse(r, fields) for r in rows]}


@router.get("/factors/{factor_id}")
def factor_detail(factor_id: str, request: Request):
    stack = _stack(request)
    row = stack.environment.factors.latest(factor_id)
    if row is None:
        raise HTTPException(404, "factor not found")
    fields = ("signals", "categories", "regions", "lead_time", "confidence", "contribution", "supporting_episodes", "contradictory_episodes", "counterfactual", "discovery_quality")
    return _parse(row, fields)


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
    return {"latest": latest}


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
    """v1.0 directive Task 8: live prediction-outcome ledger. Every number
    here is a real row -- resolved outcomes come from PredictionEngine.
    resolve_due's judgments against real episodes, never estimated."""
    stack = _stack(request)
    rows = stack.prediction.db.query_dicts(
        "SELECT validation_status, count(*) AS n FROM predictions GROUP BY validation_status"
    )
    counts = {r["validation_status"]: r["n"] for r in rows}
    judged = sum(v for k, v in counts.items() if k in ("correct", "incorrect", "partially_correct"))
    recent = stack.prediction.db.query_dicts(
        "SELECT prediction_id, opportunity_id, confidence, lead_time_days, validation_status, outcome, created_at "
        "FROM predictions ORDER BY created_at DESC LIMIT 30"
    )
    return {
        "counts": counts,
        "accuracy": round(counts.get("correct", 0) / judged, 4) if judged else None,
        "judged": judged,
        "recent": [_parse(r, ("outcome",)) for r in recent],
    }


# -------------------------------------------------------- 9.16 knowledge evolution
@router.get("/knowledge/growth")
def knowledge_growth(request: Request):
    stack = _stack(request)
    return {
        "mechanisms": stack.mechanisms.store.counts(),
        "signals": stack.environment.library.counts(),
        "factors": len(stack.environment.factors.list_latest()),
        "research_episodes": len(stack.demand.episodes.list_latest(include_controls=False)),
        "compression": stack.knowledge.compression_report(),
    }


@router.get("/knowledge/search")
def knowledge_search(request: Request, object_type: str | None = None, min_confidence: float = 0.0):
    return {"results": _stack(request).knowledge.graph.search(object_type, min_confidence)}


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
):
    stack = _stack(request)
    needle = q.lower().strip()

    def text_match(*parts: Any) -> bool:
        if not needle:
            return True
        haystack = " ".join(str(p) for p in parts if p is not None).lower()
        return needle in haystack

    def category_match(categories: list[Any] | str | None) -> bool:
        if not category:
            return True
        if isinstance(categories, str):
            return categories == category
        return category in [str(c) for c in (categories or [])]

    def region_match(regions: list[Any] | str | None) -> bool:
        if not region:
            return True
        if isinstance(regions, str):
            return regions == region
        return region in [str(r) for r in (regions or [])]

    results: dict[str, Any] = {}
    if kind in (None, "product"):
        products = []
        for product in _product_intelligence(stack).values():
            if not text_match(product["product"], product["category"], product["traits"], product["environmental_forces"], product["regions"]):
                continue
            if not category_match(product["category"]) or not region_match(product["regions"]):
                continue
            if status and product["status"] != status:
                continue
            if product["confidence"] < min_confidence:
                continue
            products.append(product)
        products, _ = _sorted_products(products, "future_demand")
        results["products"] = products[:50]
    if kind in (None, "opportunity"):
        fields = ("categories", "products", "environmental_factors", "scores", "lead_time", "confidence", "regions")
        opportunities = []
        for opportunity in (_parse(o, fields) for o in stack.prediction.opportunities.active()):
            categories = _as_list(opportunity.get("categories"))
            regions = _as_list(opportunity.get("regions"))
            if not text_match(categories, opportunity.get("products"), opportunity.get("environmental_factors")):
                continue
            if not category_match(categories) or not region_match(regions):
                continue
            if status and opportunity.get("stage") != status:
                continue
            if _confidence_value(opportunity) < min_confidence:
                continue
            opportunities.append(opportunity)
        results["opportunities"] = opportunities[:50]
    if kind in (None, "mechanism"):
        results["mechanisms"] = [
            m
            for m in (_parse(row, ("affected_categories", "countries", "supporting_signals", "environmental_factors", "confidence")) for row in stack.mechanisms.store.list_latest())
            if text_match(m["name"], m.get("supporting_signals"), m.get("environmental_factors"), m.get("affected_categories"), m.get("countries"))
            and category_match(_as_list(m.get("affected_categories")))
            and region_match(_as_list(m.get("countries")))
            and (not status or m.get("lifecycle") == status)
            and _confidence_value(m) >= min_confidence
        ]
    if kind in (None, "factor"):
        results["factors"] = [
            f
            for f in (_parse(row, ("signals", "categories", "regions", "confidence")) for row in stack.environment.factors.list_latest())
            if text_match(f["name"], f.get("signals"), f.get("categories"), f.get("regions"))
            and category_match(_as_list(f.get("categories")))
            and region_match(_as_list(f.get("regions")))
            and (not status or f.get("lifecycle") == status)
            and _confidence_value(f) >= min_confidence
        ]
    if kind in (None, "episode"):
        results["episodes"] = [
            e
            for e in (_parse(row, ("traits", "evidence", "timeline", "confidence")) for row in stack.demand.episodes.list_latest(include_controls=False))
            if text_match(e["entity"], e.get("category"), e.get("traits"), e.get("country"), e.get("region"))
            and category_match(e.get("category"))
            and region_match([e.get("country"), e.get("region")])
            and (not status or e.get("validation_status") == status or e.get("lifecycle") == status)
            and _coerce_float(e.get("overall_confidence")) >= min_confidence
        ]
    return results


# ------------------------------------------------------------- 9.22 scientific audit
@router.get("/audit/{prediction_id}")
def audit(prediction_id: str, request: Request):
    stack = _stack(request)
    row = stack.prediction.db.query_dicts("SELECT * FROM predictions WHERE prediction_id = ?", [prediction_id])
    if not row:
        raise HTTPException(404, "prediction not found")
    prediction = _parse(row[0], ("mechanisms_used", "evidence", "outcome"))
    opportunity = stack.prediction.opportunities.latest(prediction["opportunity_id"])
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
    stack = _stack(request)
    return {
        "world_state": stack.environment.world.latest_state() or stack.environment.world.build(),
        "opportunities": [
            _parse(o, ("categories", "products", "scores", "confidence")) for o in stack.prediction.opportunities.active()[:10]
        ],
        "system_health": system_health(request),
        "knowledge_growth": knowledge_growth(request),
        "validation_status": stack.validation.self_audit(),
        "observation_network": {"sources": len(stack.observation.registry.list(status="active"))},
    }
