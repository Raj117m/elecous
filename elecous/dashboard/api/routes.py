"""Dashboard API routes — one per spec panel (ch. 9). Read-only; every value
returned already exists inside ELECOUS (9.27: traceable, reproducible,
validated, timestamped, evidence-backed, versioned — otherwise it cannot be
displayed).
"""

from __future__ import annotations

import json
from datetime import datetime
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
    return {
        "past": [_parse(e, ("traits", "evidence", "timeline", "confidence")) for e in past],
        "present": present,
        "future": [_parse(o, ("categories", "products", "scores", "confidence")) for o in future],
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
        "databases": stack.databases.integrity_report(),
    }


# -------------------------------------------------------------- 9.20 resources
@router.get("/health/resources")
def resources(request: Request):
    stack = _stack(request)
    return {"sample": stack.resources.last_sample(), "tiers": stack.tiers.report()}


# ----------------------------------------------------------------- 9.21 search
@router.get("/search")
def search(request: Request, q: str, kind: str | None = None):
    stack = _stack(request)
    results: dict[str, Any] = {}
    if kind in (None, "mechanism"):
        results["mechanisms"] = [
            m for m in stack.mechanisms.store.list_latest() if q.lower() in m["name"].lower()
        ]
    if kind in (None, "factor"):
        results["factors"] = [
            f for f in stack.environment.factors.list_latest() if q.lower() in f["name"].lower()
        ]
    if kind in (None, "episode"):
        results["episodes"] = [
            e for e in stack.demand.episodes.list_latest(include_controls=False) if q.lower() in e["entity"].lower()
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
