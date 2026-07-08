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


def test_prediction_outcomes_resolve_against_reality(isolated_config):
    """v1.0 directive: every stored prediction is revisited after its own
    lead-time horizon and judged correct/incorrect against real episodes;
    a still-open window is never judged early. Pure-logic test: the
    episode reader is a stub (no fabricated observations claimed as real),
    prediction rows are real rows in the isolated database."""
    from datetime import timedelta

    from elecous.common import timeutil
    from elecous.environment.signal_discovery.extractor import EnvironmentalSignalExtractor
    from elecous.environment.world_state import WorldStateBuilder
    from elecous.observation.store import ObservationStore
    from elecous.prediction.engine import PredictionEngine
    from elecous.validation.prospective.prospective import ProspectiveValidator

    now = timeutil.utcnow()

    class EpisodeReadStub:
        def list_latest(self, category=None, include_controls=False, entity=None):
            if category == "cooling":  # one genuine episode 5 days after the old prediction
                return [{"is_negative": False, "spike_start": now - timedelta(days=25), "episode_id": "epi_hit"}]
            return []

    store = ObservationStore()
    extractor = EnvironmentalSignalExtractor(store)
    mechanisms = MechanismStore()
    engine = PredictionEngine(mechanisms, WorldStateBuilder(extractor), ProspectiveValidator(EpisodeReadStub()))

    def insert_prediction(pid, opportunity_id, created_at, lead_days=7.0):
        engine.db.execute(
            "INSERT INTO predictions (prediction_id, world_state_id, opportunity_id, mechanisms_used, confidence, lead_time_days, evidence, outcome, validation_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [pid, "wst_1", opportunity_id, "[]", 0.7, lead_days, "{}", None, "pending", created_at],
        )

    # Real opportunity rows so the resolver reads real categories.
    mechanism_id = mechanisms.create(_mechanism_payload("cool mech", ["thermal_stress:up:US"], ["cooling"]))
    mechanism = mechanisms.latest(mechanism_id)
    from elecous.prediction.world_matching.matching import MatchResult

    match = MatchResult(mechanism_id, mechanism["version"], 0.9, ["thermal_stress:up:US"], [], True)
    activated = [{"mechanism": mechanism, "match": match, "lead_time": {"earliest": 4, "most_probable": 7, "latest": 10, "confidence": 0.8}}]
    scores = ScoringEngine().opportunity_scores(activated, {mechanism_id: 0.8})
    unc = UncertaintyEngine().assess(match, mechanism, activated[0]["lead_time"])
    opp_cooling = engine.opportunities.form("wst_1", activated, scores, {"overall": 0.75}, unc, {})

    insert_prediction("pred_hit", opp_cooling, now - timedelta(days=30))          # episode inside window -> correct
    insert_prediction("pred_open", opp_cooling, now - timedelta(days=2))          # window still open -> untouched
    heating_id = mechanisms.create(_mechanism_payload("heat mech", ["cold_stress:up:US"], ["heating"]))
    heating = mechanisms.latest(heating_id)
    match_h = MatchResult(heating_id, heating["version"], 0.9, ["cold_stress:up:US"], [], True)
    activated_h = [{"mechanism": heating, "match": match_h, "lead_time": {"earliest": 4, "most_probable": 7, "latest": 10, "confidence": 0.8}}]
    opp_heating = engine.opportunities.form(
        "wst_1", activated_h, ScoringEngine().opportunity_scores(activated_h, {heating_id: 0.8}),
        {"overall": 0.6}, UncertaintyEngine().assess(match_h, heating, activated_h[0]["lead_time"]), {},
    )
    insert_prediction("pred_miss", opp_heating, now - timedelta(days=30))         # no heating episode -> incorrect

    outcomes = {r["prediction_id"]: r["outcome"] for r in engine.resolve_due()}
    assert outcomes == {"pred_hit": "correct", "pred_miss": "incorrect"}
    rows = {r["prediction_id"]: r for r in engine.db.query_dicts("SELECT * FROM predictions")}
    assert rows["pred_open"]["outcome"] is None  # never judged early
    assert rows["pred_hit"]["validation_status"] == "correct"
    assert rows["pred_miss"]["validation_status"] == "incorrect"


def _prediction_engine_with_live_world(mechanisms, monkeypatch=None):
    """PredictionEngine whose world is a fixed stub state matching the test
    mechanism's signals — lets predict() run end-to-end without observations."""
    from elecous.prediction.engine import PredictionEngine
    from elecous.validation.prospective.prospective import ProspectiveValidator

    class EpisodeReadStub:
        episodes = []

        def list_latest(self, category=None, include_controls=False, entity=None):
            return [e for e in self.episodes if e.get("category") == category or category is None]

    class WorldStub:
        def __init__(self):
            self.state = _world_state(["thermal_stress:up:US", "energy_demand:up:US"])

        def latest_state(self):
            return self.state

        def build(self, as_of=None):
            return self.state

    episodes = EpisodeReadStub()
    engine = PredictionEngine(mechanisms, WorldStub(), ProspectiveValidator(episodes))
    return engine, episodes


def test_predict_evolves_pending_prediction_instead_of_duplicating(isolated_config):
    """Roadmap R1: a second predict() over the same world state must UPDATE
    the pending prediction (revisions+1, new opportunity version), never mint
    a duplicate row — and the judgment window must not move."""
    mechanisms = MechanismStore()
    mid = mechanisms.create(_mechanism_payload("cool mech", ["thermal_stress:up:US"], ["cooling"]))
    # predict() filters on lifecycle: force the stored row into a predictive one.
    mechanisms.db.execute("UPDATE mechanisms SET lifecycle = 'validated' WHERE mechanism_id = ?", [mid])

    engine, _ = _prediction_engine_with_live_world(mechanisms)

    first = engine.predict()
    assert len(first) == 1
    rows = engine.db.query_dicts("SELECT * FROM predictions")
    assert len(rows) == 1 and rows[0]["revisions"] == 0
    deadline_before = (rows[0]["created_at"], rows[0]["lead_time_days"])

    second = engine.predict()
    assert second == []  # nothing new minted
    rows = engine.db.query_dicts("SELECT * FROM predictions")
    assert len(rows) == 1
    assert rows[0]["revisions"] == 1
    assert rows[0]["updated_at"] is not None
    # falsifiability anchor unchanged: created_at same, lead time never extended
    assert rows[0]["created_at"] == deadline_before[0]
    assert float(rows[0]["lead_time_days"]) <= float(deadline_before[1])
    # the opportunity advanced one version instead of duplicating
    opp = engine.opportunities.latest(rows[0]["opportunity_id"])
    assert int(opp["version"]) == 2


def test_resolution_feeds_mechanisms_calibration_and_failure_attribution(isolated_config):
    """Roadmap R1: a resolved outcome must land in (1) the source mechanism's
    prospective track record, (2) its 'recent' confidence channel, (3) the
    calibration curve, and — on failure — (4) a structured attribution naming
    what failed and what was missing."""
    from datetime import timedelta

    from elecous.common import timeutil

    mechanisms = MechanismStore()
    mid = mechanisms.create(_mechanism_payload("cool mech", ["thermal_stress:up:US"], ["cooling"]))
    mechanisms.db.execute("UPDATE mechanisms SET lifecycle = 'validated' WHERE mechanism_id = ?", [mid])
    engine, episodes = _prediction_engine_with_live_world(mechanisms)

    created = engine.predict()
    assert len(created) == 1
    # age the prediction past its judgment window (timestamp bookkeeping on
    # our own isolated row — no fabricated observations involved)
    engine.db.execute(
        "UPDATE predictions SET created_at = ?", [timeutil.utcnow() - timedelta(days=30)]
    )

    recent_before = json.loads(mechanisms.latest(mid)["confidence"])["recent"]

    # no cooling episode occurred -> incorrect
    episodes.episodes = []
    resolved = engine.resolve_due()
    assert len(resolved) == 1 and resolved[0]["outcome"] == "incorrect"

    # (1) prospective track record saw the failure
    record = engine.prospective.track_record(mid)
    assert record["failures"] >= 1

    # (2) 'recent' confidence moved down, damped, by prediction_resolution
    latest = mechanisms.latest(mid)
    recent_after = json.loads(latest["confidence"])["recent"]
    assert recent_after == pytest.approx(0.7 * recent_before, abs=1e-6)

    # (3) calibration records + recomputed curve exist for predictions
    n_records = engine.calibration.db.query_one(
        "SELECT count(*) FROM calibration_records WHERE object_type = 'prediction'"
    )[0]
    assert n_records >= 1
    assert engine.calibration.db.query_one(
        "SELECT count(*) FROM calibration_adjustments WHERE object_type = 'prediction'"
    )[0] >= 1

    # (4) failure attribution names the miss precisely
    outcome = json.loads(engine.db.query_dicts("SELECT outcome FROM predictions")[0]["outcome"])
    attribution = outcome["failure_attribution"]
    assert attribution["missed_categories"] == ["cooling"]
    assert attribution["failed_relationships"] == [{"mechanism_id": mid, "outcome": "failure"}]
    assert "thermal_stress:up:US" in attribution["signals_present_at_prediction"]

    # and the failure is durably attached to the mechanism for retirement logic
    failures = mechanisms.db.query_dicts(
        "SELECT * FROM mechanism_failures WHERE mechanism_id = ? AND kind = 'prediction_failure'", [mid]
    )
    assert len(failures) == 1
