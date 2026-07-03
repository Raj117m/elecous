"""Phase 3 tests: Demand Intelligence with real backfilled public data
(Wikipedia pageviews + GDELT media volume). Nothing is fabricated: baselines,
evidence and episodes are computed from genuine history. Because reality is
not scripted, tests assert machinery invariants (eligibility, baselines,
false-spike governance, freezing, versioning, replay) rather than demanding
that a spike exists on a given day."""

from __future__ import annotations

import json

import pytest

from elecous.common import timeutil
from elecous.common.vocabulary import Lifecycle, ValidationStatus
from elecous.demand.engine import DemandEngine
from elecous.observation.network import ObservationNetwork

BACKFILL_DAYS = 150


@pytest.fixture(scope="module")
def real_history(tmp_path_factory):
    """Module-scoped real dataset: backfill attention + media history once."""
    import os

    from elecous.infrastructure.config import Config, set_active_config
    from elecous.infrastructure.event_bus import reset_bus
    from elecous.infrastructure.logging import reset_logging
    from elecous.storage.database import reset_databases

    os.environ["ELECOUS_DATA_DIR"] = str(tmp_path_factory.mktemp("demand-data"))
    set_active_config(Config.load())
    reset_databases()
    reset_bus()
    reset_logging()

    network = ObservationNetwork()
    network.bootstrap()
    # Reduce the backfill horizon (configuration, not code — spec 10.20).
    for source_id in ("wikimedia_pageviews", "gdelt_media"):
        source = network.registry.get(source_id)
        cfg = json.loads(source["connector_config"])
        cfg["backfill_days"] = BACKFILL_DAYS
        network.registry.db.execute(
            "UPDATE source_registry SET connector_config = ? WHERE source_id = ?",
            [json.dumps(cfg), source_id],
        )
    wiki = network.run_source("wikimedia_pageviews", mode="backfill")
    gdelt = network.run_source("gdelt_media", mode="backfill")
    assert wiki["success"], wiki
    assert gdelt["success"], gdelt
    yield network
    reset_databases()
    reset_bus()
    reset_logging()
    os.environ.pop("ELECOUS_DATA_DIR", None)
    set_active_config(Config.load())


@pytest.mark.network
@pytest.mark.slow
def test_baseline_and_eligibility_from_real_history(real_history):
    engine = DemandEngine(real_history.store)
    now = timeutil.utcnow()
    baseline = engine.baselines.compute("Air conditioner", "pageviews_daily", now)
    assert baseline is not None
    assert baseline.n_points >= 15
    assert baseline.median > 0
    # A typical day near the median scores |z| < 2 against its own baseline.
    assert abs(baseline.robust_z(baseline.median)) < 0.01

    decision = engine.eligibility.check("Air conditioner", "pageviews_daily", now)
    assert decision.eligible, decision.reasons
    missing = engine.eligibility.check("Nonexistent product XYZ", "pageviews_daily", now)
    assert not missing.eligible and "no_observations" in missing.reasons


@pytest.mark.network
@pytest.mark.slow
def test_evidence_evaluation_and_scan_governance(real_history):
    engine = DemandEngine(real_history.store)
    now = timeutil.utcnow()
    evidence = engine.detector.evaluate("Camping", now, 14)
    assert evidence.signals, "expected real demand signals for Camping"
    for signal in evidence.signals:
        assert 0 <= signal.evidence <= 100
        assert signal.coverage > 0
    assert abs(sum(evidence.weights.values()) - 1.0) < 1e-6

    # Weekly scans across the real backfilled period: every created episode
    # must satisfy governance (3.20) — multi-signal agreement, controls
    # attempted, confidence recorded, evidence frozen.
    created: list[str] = []
    from datetime import timedelta

    as_of = now - timedelta(days=BACKFILL_DAYS - 45)
    while as_of <= now:
        created += engine.scan(as_of=as_of)
        as_of += timedelta(days=7)
    for episode_id in created:
        episode = engine.episodes.latest(episode_id)
        checks = json.loads(episode["false_spike_checks"])
        assert checks["passed"] is True
        agreement = json.loads(episode["signal_agreement"])
        assert len(agreement) >= 2
        confidence = json.loads(episode["confidence"])
        assert 0 <= confidence["overall"] <= 1
        frozen = json.loads(episode["evidence"])
        assert frozen["signals"] and all(s["series"] for s in frozen["signals"])


@pytest.mark.network
@pytest.mark.slow
def test_episode_freezing_versioning_promotion_replay(real_history):
    """Episode lifecycle invariants exercised on a real negative episode
    (spec 4.18 makes non-events first-class research objects)."""
    engine = DemandEngine(real_history.store)
    now = timeutil.utcnow()
    evidence = engine.detector.evaluate("Board game", now, 14)
    assert evidence.signals
    timeline = engine.detector.research_timeline(evidence)
    confidence = engine.scientist.assess(evidence, [], timeline)
    episode_id = engine.episodes.create_episode(
        evidence,
        entity_meta={"category": "indoor_recreation", "traits": ["indoor"]},
        timeline=timeline,
        controls=[],
        confidence=confidence,
        is_negative=True,
    )
    episode = engine.episodes.latest(episode_id)
    assert episode["version"] == 1 and episode["lifecycle"] == "candidate"

    # Frozen fields cannot be rewritten in new versions.
    with pytest.raises(ValueError):
        engine.episodes.new_version(episode_id, {"evidence": "{}"}, actor="test")

    # Promotion without passed validation is unconstitutional.
    from elecous.common.errors import ConstitutionViolation

    with pytest.raises(ConstitutionViolation):
        engine.episodes.promote(episode_id, Lifecycle.EXPERIMENTAL, ValidationStatus.PENDING, actor="test")

    engine.episodes.promote(episode_id, Lifecycle.EXPERIMENTAL, ValidationStatus.PASSED, actor="test")
    assert engine.episodes.latest(episode_id)["version"] == 2
    # Stage skipping is impossible.
    with pytest.raises(ConstitutionViolation):
        engine.episodes.promote(episode_id, Lifecycle.TRUSTED, ValidationStatus.PASSED, actor="test")

    replay = engine.episodes.replay(episode_id)
    assert replay is not None and len(replay["frames"]) >= 10
    days = [f["day"] for f in replay["frames"]]
    assert days == sorted(days)


@pytest.mark.network
@pytest.mark.slow
def test_controls_and_improvement_learning(real_history):
    engine = DemandEngine(real_history.store)
    now = timeutil.utcnow()
    controls = engine.controls.select_controls(
        "Tent", "camping_equipment", ["outdoor", "camping", "portable"], now, 14
    )
    assert controls, "expected comparable control entities from real data"
    for control in controls:
        assert control["entity"] != "Tent"
        assert control["stage"] != "abnormal_acceleration"
        assert control["evidence"]

    # Weight/window learning runs (updates apply once validated episodes exist).
    updates = engine.improve_from_episodes()
    assert set(updates) == {"weights", "windows"}
    engine.signal_registry_refresh()
