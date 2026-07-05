"""Phase 1 tests: configuration, ids, time, lifecycle, event bus, databases,
scheduler, budget, constitution guards. Pure-logic tests only — no data is
fabricated; nothing here claims to be an observation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from elecous.common import timeutil
from elecous.common.errors import ConstitutionViolation
from elecous.common.ids import (
    content_hash,
    dedup_key,
    entity_hash,
    geographic_hash,
    new_id,
    semantic_hash,
    temporal_hash,
)
from elecous.common.vocabulary import Lifecycle, ValidationStatus, is_legal_promotion
from elecous.governance import constitution
from elecous.infrastructure.config import active_config
from elecous.infrastructure.event_bus import EventBus, EventType
from elecous.scheduler.budget import ObservationBudget
from elecous.scheduler.scheduler import Scheduler
from elecous.storage.database import Migration, global_databases


def test_ids_are_prefixed_and_unique():
    a, b = new_id("mechanism"), new_id("mechanism")
    assert a.startswith("mech_") and b.startswith("mech_") and a != b


def test_content_hash_is_deterministic():
    assert content_hash({"b": 1, "a": 2}) == content_hash({"a": 2, "b": 1})


def test_dedup_key_merges_equivalent_observations():
    ts = datetime(2026, 7, 1, 14, 5, tzinfo=UTC)
    ts2 = datetime(2026, 7, 1, 14, 55, tzinfo=UTC)  # same hour bucket
    key1 = dedup_key(
        semantic_hash("Berlin", "temperature_max", "celsius"),
        temporal_hash(ts),
        geographic_hash("DE", "berlin", 52.52, 13.405),
        entity_hash("Berlin", "weather"),
    )
    key2 = dedup_key(
        semantic_hash("berlin ", "temperature_max", "celsius"),
        temporal_hash(ts2),
        geographic_hash("de", "Berlin", 52.521, 13.4051),
        entity_hash("BERLIN", "Weather"),
    )
    assert key1 == key2


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        timeutil.ensure_utc(datetime(2026, 1, 1))


def test_lifecycle_never_skips_stages():
    assert is_legal_promotion(Lifecycle.CANDIDATE, Lifecycle.EXPERIMENTAL)
    assert not is_legal_promotion(Lifecycle.CANDIDATE, Lifecycle.TRUSTED)
    assert not is_legal_promotion(Lifecycle.CANDIDATE, Lifecycle.VALIDATED)


def test_constitution_rejects_synthetic_provenance():
    with pytest.raises(ConstitutionViolation):
        constitution.require_real_provenance("src_x", "synthetic-generator", None)
    constitution.require_real_provenance("src_x", "official_api", "https://api.open-meteo.com")


def test_constitution_rejects_future_observations():
    with pytest.raises(ConstitutionViolation):
        constitution.require_no_future_timestamp(timeutil.utcnow() + timedelta(days=2))


def test_constitution_blocks_unvalidated_promotion():
    with pytest.raises(ConstitutionViolation):
        constitution.require_validated_promotion(
            Lifecycle.CANDIDATE, Lifecycle.EXPERIMENTAL, ValidationStatus.PENDING
        )
    constitution.require_validated_promotion(
        Lifecycle.CANDIDATE, Lifecycle.EXPERIMENTAL, ValidationStatus.PASSED
    )


def test_constitution_blocks_future_leakage():
    as_of = datetime(2024, 6, 1, tzinfo=UTC)
    with pytest.raises(ConstitutionViolation):
        constitution.require_no_future_leakage(as_of, [datetime(2024, 6, 2, tzinfo=UTC)])


def test_constitution_append_only():
    with pytest.raises(ConstitutionViolation):
        constitution.require_append_only(existing_version=3, incoming_version=3)
    constitution.require_append_only(existing_version=3, incoming_version=4)


def test_event_bus_dispatch_and_isolation(isolated_config):
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(EventType.OBSERVATION_ADDED, lambda e: seen.append(e.payload["obs"]))
    bus.subscribe(EventType.OBSERVATION_ADDED, lambda e: 1 / 0)  # failing subscriber
    bus.emit(EventType.OBSERVATION_ADDED, source="test", obs="o1")
    assert seen == ["o1"]
    failures = bus.drain_failures()
    assert len(failures) == 1 and failures[0]["stage"] == "dispatch"


def test_databases_are_isolated_per_domain(isolated_config):
    manager = global_databases()
    manager.register_schema(
        Migration("observations.test.t1", "observations", ('CREATE TABLE IF NOT EXISTS t1 (x INT)',))
    )
    obs = manager.get("observations")
    sig = manager.get("signals")
    assert obs.table_exists("t1")
    assert not sig.table_exists("t1")
    assert obs.path != sig.path
    # migrations are idempotent
    assert obs.apply_migration(Migration("observations.test.t1", "observations", ())) is False


def test_scheduler_runs_due_tasks_and_backs_off(isolated_config):
    scheduler = Scheduler()
    runs: list[int] = []
    scheduler.register("ok", "test", lambda: runs.append(1), timedelta(seconds=60), run_immediately=True)
    scheduler.register("bad", "test", lambda: 1 / 0, timedelta(seconds=60), run_immediately=True)
    executed = scheduler.run_pending()
    assert executed == 2 and runs == [1]
    report = {t["name"]: t for t in scheduler.report()}
    assert report["bad"]["consecutive_failures"] == 1
    # failed task backed off beyond one interval
    assert report["bad"]["next_run"] > report["ok"]["next_run"]


def test_scheduler_background_thread_runs_unattended(isolated_config):
    """Final Implementation Phase, Task 3/7: the real continuous-operation
    mechanism (elecous run's background thread) must actually tick and
    execute due tasks without any manual call to run_pending() -- the one
    real gap found in this session's scheduler test coverage (run_pending()
    itself was already tested; start()/stop()'s threading wrapper was not).
    Uses only safe, fast, fake tasks -- no real network/observation source
    is touched."""
    import time

    config = active_config()
    config._root = dict(config._root)
    config._root["scheduler"] = {**config._root.get("scheduler", {}), "tick_seconds": 0.05}
    scheduler = Scheduler(config=config)
    runs: list[int] = []
    scheduler.register("fast", "test", lambda: runs.append(1), timedelta(seconds=60), run_immediately=True)
    scheduler.start()
    try:
        deadline = time.monotonic() + 2.0
        while not runs and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        scheduler.stop()
    assert runs == [1]  # the background thread executed the due task on its own
    assert not scheduler._thread.is_alive()  # stop() actually joins/stops the real thread


def test_scheduler_runs_independent_subsystems_concurrently(isolated_config):
    """Performance directive: one slow, network-bound task must not starve
    other subsystems. Measured, not asserted: two 0.4s tasks in DIFFERENT
    subsystems complete in ~max (concurrent), two in the SAME subsystem in
    ~sum (serialized -- heavy same-domain jobs never overlap themselves),
    and a still-running long task never blocks the next tick's dispatch."""
    import time

    scheduler = Scheduler()
    scheduler.register("obs", "observation", lambda: time.sleep(0.4), timedelta(seconds=60), run_immediately=True)
    scheduler.register("val", "validation", lambda: time.sleep(0.4), timedelta(seconds=60), run_immediately=True)
    t0 = time.monotonic()
    assert scheduler.run_pending() == 2
    concurrent_wall = time.monotonic() - t0
    assert concurrent_wall < 0.7, f"different subsystems should overlap, took {concurrent_wall:.2f}s"

    scheduler.register("env_a", "environment", lambda: time.sleep(0.4), timedelta(seconds=60), run_immediately=True)
    scheduler.register("env_b", "environment", lambda: time.sleep(0.4), timedelta(seconds=60), run_immediately=True)
    t0 = time.monotonic()
    assert scheduler.run_pending() == 2
    serialized_wall = time.monotonic() - t0
    assert serialized_wall >= 0.75, f"same subsystem must serialize, took {serialized_wall:.2f}s"

    # A long-running task must not block the next tick from dispatching
    # newly-due work (wait=False), and must not be double-dispatched.
    ran: list[str] = []
    scheduler.register("slow", "observe", lambda: (time.sleep(0.8), ran.append("slow")), timedelta(seconds=60), run_immediately=True)
    assert scheduler.run_pending(wait=False) == 1
    scheduler.register("quick", "prediction", lambda: ran.append("quick"), timedelta(seconds=60), run_immediately=True)
    t0 = time.monotonic()
    assert scheduler.run_pending(wait=True) == 1  # dispatches quick only; slow already running, not re-dispatched
    assert time.monotonic() - t0 < 0.5
    assert "quick" in ran and "slow" not in ran  # quick finished while slow still sleeps
    deadline = time.monotonic() + 3
    while "slow" not in ran and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "slow" in ran


def test_budget_follows_value_not_habit(isolated_config):
    budget = ObservationBudget()
    allocations = budget.allocate(
        ["weather", "shipping", "search"],
        contribution={"weather": 0.9, "shipping": 0.0, "search": 0.4},
        health={"weather": 1.0, "shipping": 1.0, "search": 1.0},
    )
    by_cap = {a.capability: a.units for a in allocations}
    assert by_cap["weather"] > by_cap["search"] > by_cap["shipping"]
    assert by_cap["shipping"] >= 2  # exploration floor: never fully blind
    total = int(isolated_config.get("scheduler.observation_units_per_hour", 100))
    assert abs(sum(by_cap.values()) - total) <= 3  # rounding tolerance
