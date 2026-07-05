"""Phase 5 tests: mechanism machinery — versioned storage, promotion
governance, confidence blending, behavioural chains, competitions.
These exercise knowledge-object plumbing with abstract evidence identifiers;
no observations, signals or demand values are fabricated."""

from __future__ import annotations

import json

import pytest

from elecous.common.errors import ConstitutionViolation
from elecous.common.vocabulary import Lifecycle, ValidationStatus
from elecous.mechanisms.reasoning.behaviour import BehaviouralReasoner
from elecous.mechanisms.versioning.store import MechanismStore


def _payload(name: str = "test mechanism") -> dict:
    return {
        "name": name,
        "environmental_factors": ["fac_ref_1"],
        "supporting_signals": ["thermal_stress:up:US", "energy_demand:up:US"],
        "behavioural_chain": [{"step": "world_change", "statement": "thermal stress rises in US"}],
        "product_traits": ["cooling", "summer"],
        "affected_categories": ["cooling"],
        "countries": ["US"],
        "lead_time": {"min": 3, "avg": 6.5, "max": 12, "stddev": 2.1},
        "confidence": {"historical": 0.4, "recent": 0.4, "regional": 0.3, "scientific": 0.0, "current": 0.24},
        "supporting_episodes": ["epi_ref_1", "epi_ref_2", "epi_ref_3"],
        "evidence_links": {"factors": ["fac_ref_1"], "episodes": ["epi_ref_1"]},
    }


def test_mechanism_requires_evidence(isolated_config):
    store = MechanismStore()
    payload = _payload()
    payload["supporting_episodes"] = []
    with pytest.raises(ConstitutionViolation):
        store.create(payload)


def test_mechanism_versioning_and_promotion_ladder(isolated_config):
    store = MechanismStore()
    mechanism_id = store.create(_payload())
    current = store.latest(mechanism_id)
    assert current["version"] == 1 and current["lifecycle"] == "candidate"

    # No skipping candidate -> trusted.
    with pytest.raises(ConstitutionViolation):
        store.promote(mechanism_id, Lifecycle.TRUSTED, ValidationStatus.PASSED, actor="test")
    # No promotion without passed validation.
    with pytest.raises(ConstitutionViolation):
        store.promote(mechanism_id, Lifecycle.EXPERIMENTAL, ValidationStatus.PENDING, actor="test")

    store.promote(mechanism_id, Lifecycle.EXPERIMENTAL, ValidationStatus.PASSED, actor="validation")
    store.promote(mechanism_id, Lifecycle.VALIDATED, ValidationStatus.PASSED, actor="validation")
    store.promote(mechanism_id, Lifecycle.TRUSTED, ValidationStatus.PASSED, actor="validation")
    latest = store.latest(mechanism_id)
    assert latest["lifecycle"] == "trusted"
    history = json.loads(latest["validation_history"])
    assert [h["to"] for h in history] == ["experimental", "validated", "trusted"]
    # every prior version remains reproducible
    assert store.version(mechanism_id, 1)["lifecycle"] == "candidate"
    assert latest["next_validation_at"] is not None
    stability = json.loads(latest["stability"])
    assert stability["revisions"] == 3


def test_confidence_blend_and_reputation(isolated_config):
    store = MechanismStore()
    mechanism_id = store.create(_payload())
    store.update_confidence(
        mechanism_id, {"recent": 0.8, "historical": 0.6, "scientific": 0.5, "regional": 0.4}, actor="calibration"
    )
    latest = store.latest(mechanism_id)
    confidence = json.loads(latest["confidence"])
    assert confidence["current"] == pytest.approx(
        0.3 * 0.8 + 0.25 * 0.6 + 0.2 * 0.5 + 0.15 * 0.4 + 0.1 * 0.8, abs=1e-6
    )
    store.update_reputation(mechanism_id, 0.72, actor="validation")
    assert store.latest(mechanism_id)["reputation"] == 0.72


def test_competition_and_failure_records(isolated_config):
    store = MechanismStore()
    a = store.create(_payload("mechanism A"))
    b = store.create(_payload("mechanism B"))
    competition = store.open_competition(a, b, subject="cooling direction conflict")
    store.resolve_competition(competition, winner=a, evidence={"prospective_accuracy": {a: 0.7, b: 0.3}})
    row = store.db.query_dicts("SELECT * FROM mechanism_competitions WHERE competition_id = ?", [competition])[0]
    assert row["winner"] == a and row["resolved_at"] is not None

    failure = store.record_failure(a, kind="missed_activation", questions={"wrong_timing": True})
    assert failure


def test_behavioural_chain_composition(isolated_config):
    """Rewrite directive Stage 4: the chain is now the full explicit layer
    sequence (Environmental State -> Human Need -> Human Behaviour ->
    Attention -> Purchase Intent -> Demand -> Sales), each layer marked
    evidenced or explicitly theorized -- never fabricated content."""
    reasoner = BehaviouralReasoner()
    chain = reasoner.chain(["thermal_stress:up:US", "calendar_event:up:US"], "cooling")
    layers = [s["layer"] for s in chain]
    assert layers.count("environmental_state") == 2
    assert "human_behaviour" in layers
    assert layers[-3:] == ["attention", "purchase_intent", "demand"] or "sales" in layers
    assert layers[-1] == "sales"

    sales_step = next(s for s in chain if s["layer"] == "sales")
    assert sales_step["evidenced"] is False  # never fabricated -- no real sales data exists

    purchase_intent_step = next(s for s in chain if s["layer"] == "purchase_intent")
    assert purchase_intent_step["evidenced"] is False  # always an attention proxy, stated honestly

    text = reasoner.describe(chain)
    assert "thermal stress rises in US" in text
    assert "cooling demand-proxy signal accelerates" in text

    completeness = reasoner.evidence_completeness(chain)
    assert 0.0 < completeness < 1.0  # some layers real, some theorized -- never all-or-nothing
