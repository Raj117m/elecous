"""Phase 6 tests: validation machinery. Skeptic/calibration/drift/benchmark
logic is tested with abstract evidence identifiers (no fabricated
observations, demand or environmental data). One integration test wires the
full chain — observation -> demand -> environment -> mechanism -> validation
-> promotion -> prospective registration — on real backfilled public data.
"""

from __future__ import annotations

import json

import pytest

from elecous.common.vocabulary import Lifecycle, ValidationStatus
from elecous.mechanisms.versioning.store import MechanismStore
from elecous.validation.benchmarks.benchmark import BenchmarkEngine
from elecous.validation.calibration.calibration import CalibrationEngine
from elecous.validation.drift.drift import ConceptDriftEngine
from elecous.validation.prospective.prospective import ProspectiveValidator


def test_calibration_recomputes_and_blends(isolated_config):
    engine = CalibrationEngine()
    for _ in range(8):
        engine.record("mechanism", 0.9, succeeded=True)
    for _ in range(2):
        engine.record("mechanism", 0.9, succeeded=False)
    for _ in range(10):
        engine.record("mechanism", 0.3, succeeded=False)
    engine.recompute("mechanism")
    calibrated_high = engine.calibrate("mechanism", 0.9)
    calibrated_low = engine.calibrate("mechanism", 0.3)
    assert calibrated_high < 0.9  # observed rate (0.8) pulls it down
    assert calibrated_low < 0.3
    assert engine.expected_calibration_error("mechanism") is not None
    # sparse bin (fewer than 5 samples) returns raw confidence unchanged
    assert engine.calibrate("mechanism", 0.55) == 0.55


def test_benchmark_promotion_requires_improvement(isolated_config):
    from elecous.infrastructure.config import active_config

    config = active_config()
    engine = BenchmarkEngine(config=config)
    config._root["platform"]["version"] = "1.0.0"
    engine.record("historical_replay", {"precision": 0.6, "recall": 0.5, "calibration_error": 0.1})
    config._root["platform"]["version"] = "1.1.0"
    engine.record("historical_replay", {"precision": 0.5, "recall": 0.5, "calibration_error": 0.1})
    verdict = engine.compare_to_previous("historical_replay")
    assert verdict["verdict"] == "investigate"  # precision regressed

    config._root["platform"]["version"] = "1.2.0"
    engine.record("historical_replay", {"precision": 0.65, "recall": 0.55, "calibration_error": 0.08})
    verdict2 = engine.compare_to_previous("historical_replay")
    assert verdict2["verdict"] == "promote"


def _mechanism_payload(name="drift mechanism"):
    return {
        "name": name,
        "environmental_factors": ["fac_x"],
        "supporting_signals": ["thermal_stress:up:US"],
        "behavioural_chain": [],
        "product_traits": [],
        "affected_categories": ["cooling"],
        "countries": ["US"],
        "lead_time": {"min": 3, "avg": 7, "max": 14, "stddev": 2},
        "confidence": {"historical": 0.6, "recent": 0.6, "regional": 0.5, "scientific": 0.4, "current": 0.6},
        "supporting_episodes": ["epi_1", "epi_2", "epi_3"],
        "evidence_links": {},
    }


def test_concept_drift_demotes_trusted_mechanism(isolated_config):
    mechanisms = MechanismStore()
    mechanism_id = mechanisms.create(_mechanism_payload())
    mechanisms.promote(mechanism_id, Lifecycle.EXPERIMENTAL, ValidationStatus.PASSED, actor="t")
    mechanisms.promote(mechanism_id, Lifecycle.VALIDATED, ValidationStatus.PASSED, actor="t")
    mechanisms.promote(mechanism_id, Lifecycle.TRUSTED, ValidationStatus.PASSED, actor="t")

    from elecous.demand.episodes.episodes import EpisodeStore

    prospective = ProspectiveValidator(EpisodeStore())
    mechanism = mechanisms.latest(mechanism_id)
    # 6 historical successes, then 5 recent failures -> steep drop
    for _ in range(6):
        pid = prospective.register(mechanism, activation=1.0)
        prospective.db.execute(
            "UPDATE prospective_predictions SET resolved_at = now(), outcome = 'success' WHERE prospective_id = ?",
            [pid],
        )
    for _ in range(5):
        pid = prospective.register(mechanism, activation=1.0)
        prospective.db.execute(
            "UPDATE prospective_predictions SET resolved_at = now(), outcome = 'failure' WHERE prospective_id = ?",
            [pid],
        )
    drift = ConceptDriftEngine(mechanisms, prospective)
    report = drift.check(mechanism_id)
    assert report["drift"] is True
    assert report["action"] == "demoted"
    assert mechanisms.latest(mechanism_id)["lifecycle"] == "declining"


@pytest.mark.network
@pytest.mark.slow
def test_full_validation_chain_on_real_data(tmp_path_factory):
    """End-to-end: real backfill -> demand episodes -> environmental
    factors -> mechanisms -> validation -> promotion -> prospective
    registration. Assertions target machinery, not a guaranteed spike."""
    import os

    from elecous.demand.engine import DemandEngine
    from elecous.environment.engine import EnvironmentalEngine
    from elecous.infrastructure.config import Config, set_active_config
    from elecous.infrastructure.event_bus import reset_bus
    from elecous.infrastructure.logging import reset_logging
    from elecous.mechanisms.engine import MechanismEngine
    from elecous.observation.network import ObservationNetwork
    from elecous.storage.database import reset_databases
    from elecous.validation.engine import ValidationEngine

    os.environ["ELECOUS_DATA_DIR"] = str(tmp_path_factory.mktemp("validation-data"))
    set_active_config(Config.load())
    reset_databases()
    reset_bus()
    reset_logging()
    try:
        network = ObservationNetwork()
        network.bootstrap()
        for source_id in ("wikimedia_pageviews", "gdelt_media", "open_meteo_weather"):
            source = network.registry.get(source_id)
            cfg = json.loads(source["connector_config"])
            cfg["backfill_days"] = 150
            network.registry.db.execute(
                "UPDATE source_registry SET connector_config = ? WHERE source_id = ?",
                [json.dumps(cfg), source_id],
            )
        for source_id in ("wikimedia_pageviews", "gdelt_media", "open_meteo_weather"):
            result = network.run_source(source_id, mode="backfill")
            assert result["success"], result

        demand_engine = DemandEngine(network.store)
        from datetime import timedelta

        from elecous.common import timeutil

        as_of = timeutil.days_ago(100)
        while as_of <= timeutil.utcnow():
            demand_engine.scan(as_of=as_of)
            as_of += timedelta(days=7)

        env_engine = EnvironmentalEngine(network.store, demand_engine.episodes)
        env_engine.research_cycle()

        mech_engine = MechanismEngine(
            env_engine.factors, demand_engine.episodes, env_engine.discovery, network.store
        )
        mech_engine.generate_from_factors()

        validator = ValidationEngine(
            env_engine.library,
            env_engine.factors,
            mech_engine.store,
            demand_engine.episodes,
            env_engine.discovery,
            env_engine.extractor,
            env_engine.world,
        )

        # Whatever factors/mechanisms real data produced, validation
        # machinery must run cleanly and only promote through legal steps.
        for factor in env_engine.factors.list_latest(lifecycle=Lifecycle.CANDIDATE.value):
            report = validator.validate_factor(factor["factor_id"])
            assert report["outcome"] in ("passed", "failed")
        for mechanism in mech_engine.store.list_latest(lifecycle=Lifecycle.CANDIDATE.value):
            report = validator.validate_mechanism(mechanism["mechanism_id"])
            assert report["outcome"] in ("passed", "failed")
            updated = mech_engine.store.latest(mechanism["mechanism_id"])
            if report["outcome"] == "passed":
                assert updated["lifecycle"] == "experimental"
            else:
                assert updated["lifecycle"] == "candidate"

        audit = validator.self_audit()
        assert audit["verdict"] in ("healthy", "too_permissive", "too_conservative")
    finally:
        reset_databases()
        reset_bus()
        reset_logging()
        os.environ.pop("ELECOUS_DATA_DIR", None)
        set_active_config(Config.load())
