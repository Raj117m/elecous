"""Phase 8 tests: prediction machinery (matching, lead-time, uncertainty,
scoring, opportunity formation/clustering/conflicts). Inputs are abstract
mechanism/world-state dicts describing evidence *identifiers*, not
fabricated observations or demand data — the same pattern used in Phase 5/6
tests for versioned-object plumbing."""

from __future__ import annotations

import json

import pytest

from elecous.common.vocabulary import OpportunityStage
from elecous.mechanisms.versioning.store import MechanismStore
from elecous.prediction.lead_time.lead_time import LeadTimeEstimator
from elecous.prediction.opportunities.opportunities import OpportunityStore
from elecous.prediction.ranking.ranking import ScoringEngine
from elecous.prediction.uncertainty.uncertainty import UncertaintyEngine
from elecous.prediction.world_matching.matching import MechanismMatcher


def _mechanism_payload(name, signals, categories, conditions=None):
    return {
        "name": name,
        "environmental_factors": ["fac_1"],
        "supporting_signals": signals,
        "behavioural_chain": [{"step": "demand", "statement": f"{categories[0]} demand accelerates"}],
        "product_traits": ["cooling"],
        "affected_categories": categories,
        "countries": ["US"],
        "world_conditions": conditions or [],
        "lead_time": {"min": 3, "avg": 7, "max": 12, "stddev": 2, "episodes": 5},
        "confidence": {"historical": 0.6, "recent": 0.7, "regional": 0.5, "scientific": 0.6, "current": 0.65},
        "supporting_episodes": ["epi_1", "epi_2", "epi_3"],
        "evidence_links": {},
    }


def _world_state(signals, conditions=None):
    return {
        "state_id": "wst_test",
        "as_of": "2026-07-01T00:00:00+00:00",
        "signals": [{"signal_key": s, "first_crossed_at": None} for s in signals],
        "conditions": conditions or [],
        "observation_count": 1000,
    }


def test_mechanism_matcher_partial_activation_and_dormancy(isolated_config):
    store = MechanismStore()
    active_id = store.create(_mechanism_payload("cooling mech", ["thermal_stress:up:US", "energy_demand:up:US"], ["cooling"]))
    dormant_id = store.create(_mechanism_payload("winter mech", ["cold_stress:up:US"], ["heating"]))
    mechanisms = [store.latest(active_id), store.latest(dormant_id)]
    state = _world_state(["thermal_stress:up:US"])  # only one of two cooling signals active

    matcher = MechanismMatcher()
    results = matcher.match(mechanisms, state)
    ids = {r.mechanism_id for r in results}
    assert active_id in ids  # partial activation still surfaces
    assert dormant_id not in ids  # zero overlap -> skipped (resource intelligence)
    match = next(r for r in results if r.mechanism_id == active_id)
    assert match.activation == pytest.approx(0.5)
    assert match.missing_signals == ["energy_demand:up:US"]


def test_lead_time_widens_with_partial_activation():
    estimator = LeadTimeEstimator()
    mechanism = {"lead_time": json.dumps({"avg": 10, "stddev": 2, "episodes": 5})}
    full = estimator.estimate(mechanism, activation=1.0)
    partial = estimator.estimate(mechanism, activation=0.4)
    assert full["most_probable"] == partial["most_probable"] == 10
    assert (partial["latest"] - partial["earliest"]) > (full["latest"] - full["earliest"])
    assert partial["confidence"] < full["confidence"]


def test_uncertainty_reflects_missing_evidence():
    from elecous.prediction.world_matching.matching import MatchResult

    engine = UncertaintyEngine()
    weak_mechanism = {"current_confidence": 0.3}
    match = MatchResult(
        mechanism_id="m1", mechanism_version=1, activation=0.5,
        matched_signals=["a:up:US"], missing_signals=["b:up:US"], world_condition_match=False,
    )
    lead_time = {"earliest": 2, "latest": 16}
    report = engine.assess(match, weak_mechanism, lead_time, conflicts=1)
    assert report["weak_mechanism"] is True
    assert report["regional_uncertainty"] is True
    assert report["missing_signal_share"] == 0.5
    assert 0 < report["overall"] <= 1


def test_scoring_and_ranking(isolated_config):
    from elecous.prediction.world_matching.matching import MatchResult

    scoring = ScoringEngine()
    mechanism = {
        "mechanism_id": "m1",
        "current_confidence": 0.8,
        "stability": json.dumps({"age_days": 200}),
    }
    match = MatchResult("m1", 1, activation=0.9, matched_signals=["a"], missing_signals=[], world_condition_match=True)
    activated = [{"mechanism": mechanism, "match": match, "lead_time": {}}]
    scores = scoring.opportunity_scores(activated, historical_success={"m1": 0.7})
    assert 0 < scores["overall"] <= 1
    assert set(scores) >= {"scientific_confidence", "current_activation", "historical_success", "overall"}

    ranked = scoring.rank_products(["Air conditioner", "Tent"], ["cooling"], scores)
    assert ranked[0]["product"] == "Air conditioner"  # matches category
    assert ranked[0]["score"] > ranked[1]["score"]


def test_opportunity_formation_stage_and_clustering(isolated_config):
    store = MechanismStore()
    mechanism_id = store.create(_mechanism_payload("cooling mech", ["thermal_stress:up:US"], ["cooling"]))
    mechanism = store.latest(mechanism_id)

    from elecous.prediction.world_matching.matching import MatchResult

    match = MatchResult(mechanism_id, mechanism["version"], activation=0.95, matched_signals=["thermal_stress:up:US"], missing_signals=[], world_condition_match=True)
    activated = [{"mechanism": mechanism, "match": match, "lead_time": {"earliest": 4, "most_probable": 7, "latest": 10, "confidence": 0.8}}]

    opp_store = OpportunityStore()
    scoring = ScoringEngine()
    uncertainty = UncertaintyEngine()
    scores = scoring.opportunity_scores(activated, {mechanism_id: 0.8})
    unc = uncertainty.assess(match, mechanism, activated[0]["lead_time"])
    opportunity_id = opp_store.form(
        "wst_1", activated, scores, {"overall": 0.75}, unc, {"attention_volume_median": 500}
    )
    stored = opp_store.latest(opportunity_id)
    assert stored["stage"] in (s.value for s in OpportunityStage)
    assert json.loads(stored["products"])  # cooling products populated
    assert stored["probability"] > 0

    opp_store.update_stage(opportunity_id, OpportunityStage.PEAK)
    assert opp_store.latest(opportunity_id)["version"] == 2
    assert opp_store.latest(opportunity_id)["stage"] == "peak_opportunity"

    clusters = opp_store.cluster([opp_store.latest(opportunity_id)])
    assert clusters and opportunity_id in clusters[0]
