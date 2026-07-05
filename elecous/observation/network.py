"""The Observation Network facade (spec ch. 2).

Wires registry → connectors → validator → store into the single legal path
of spec 2.3, runs sources on their dynamic schedules, records runs for
health, and reacts to failures (retry ladder of spec 10.22; full autonomous
repair arrives with the Supervisor in Phase 10 via the same interfaces).
"""

from __future__ import annotations

import traceback
from datetime import datetime, timedelta
from typing import Any

from elecous.common import timeutil
from elecous.common.errors import ConnectorSuspended
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.observation.connectors.base import load_connector
from elecous.observation.health.health import ObservationHealth
from elecous.observation.registry.registry import SourceRegistry
from elecous.observation.store import ObservationStore
from elecous.scheduler.budget import ObservationBudget
from elecous.scheduler.scheduler import Scheduler

#: update_frequency -> default collection interval
DEFAULT_INTERVALS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(hours=6),
    "weekly": timedelta(days=2),
    "monthly": timedelta(days=7),
}


class ObservationNetwork:
    def __init__(
        self,
        config: Config | None = None,
        store: ObservationStore | None = None,
        registry: SourceRegistry | None = None,
        bus: EventBus | None = None,
    ):
        self.config = config or active_config()
        self.bus = bus or global_bus()
        self.store = store or ObservationStore(bus=self.bus)
        self.registry = registry or SourceRegistry(config=self.config)
        self.health = ObservationHealth(self.registry)
        self.budget = ObservationBudget(self.config)
        self.log = get_logger("observation")

    # -------------------------------------------------------------- lifecycle
    def bootstrap(self) -> None:
        self.registry.seed_from_config()

    # -------------------------------------------------------------- execution
    def run_source(self, source_id: str, mode: str = "live") -> dict[str, Any]:
        """Run one connector through the full pipeline and record the run."""
        source = self.registry.get(source_id)
        if source is None:
            raise KeyError(source_id)
        if source["status"] not in ("active", "sandbox") and mode != "sandbox":
            return {"source_id": source_id, "skipped": source["status"]}
        started = timeutil.utcnow()
        stats: dict[str, int] = {}
        error: str | None = None
        schema_hash = None
        latency_ms = None
        success = False
        try:
            connector = load_connector(source, config=self.config)
            since = self.store.last_timestamp(source_id)
            result, latency_ms = connector.timed_collect(mode=mode, since=since)
            schema_hash = result.schema_hash
            stats = self.store.ingest(result.observations)
            stats["raw_count"] = result.raw_count
            success = True
        except ConnectorSuspended as exc:
            error = str(exc)
            self.registry.set_status(source_id, "suspended", reason=error)
            self.log.warning("connector suspended", source_id=source_id, reason=error)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.log.error("connector failed", source_id=source_id, error=error, trace=traceback.format_exc(limit=5))
            self.bus.emit(EventType.CONNECTOR_FAILED, source="observation", source_id=source_id, error=error)
        finished = timeutil.utcnow()
        self.registry.record_run(
            source_id,
            started,
            finished,
            success,
            stats,
            latency_ms,
            schema_hash,
            error,
            run_type=mode,
        )
        return {"source_id": source_id, "success": success, "stats": stats, "error": error}

    def run_all(self, mode: str = "live", capability: str | None = None) -> list[dict[str, Any]]:
        results = []
        for source in self.registry.list(status="active", capability=capability):
            results.append(self.run_source(source["source_id"], mode=mode))
        return results

    def backfill(self) -> list[dict[str, Any]]:
        """Historical collection of real public data for every active source."""
        return self.run_all(mode="backfill")

    # ------------------------------------------------------------- scheduling
    def register_schedules(self, scheduler: Scheduler) -> None:
        """One dynamic task per active source. Cadence adapts each hour via
        ``retune_schedules`` (observation intelligence, spec 2.19)."""
        for source in self.registry.list(status="active"):
            interval = DEFAULT_INTERVALS.get(source["update_frequency"], timedelta(hours=6))
            source_id = source["source_id"]
            scheduler.register(
                name=f"observe:{source_id}",
                subsystem="observation",
                action=lambda sid=source_id: self.run_source(sid),
                interval=interval,
                priority=3,
                run_immediately=True,
            )
        scheduler.register(
            name="observation:health",
            subsystem="observation",
            action=self.refresh_health,
            interval=timedelta(minutes=30),
            priority=4,
        )
        scheduler.register(
            name="observation:retune",
            subsystem="observation",
            action=lambda: self.retune_schedules(scheduler),
            interval=timedelta(hours=1),
            priority=4,
        )

    def refresh_health(self) -> None:
        reports = self.health.refresh_all()
        for report in reports:
            self.bus.emit(EventType.SOURCE_HEALTH_CHANGED, source="observation", **report)

    def retune_schedules(self, scheduler: Scheduler) -> dict[str, float]:
        """Observation follows value, not habit (spec 2.8): re-allocate the
        hourly unit budget across capabilities and adjust per-source cadence
        proportionally."""
        capabilities = self.registry.capabilities()
        # Mean across every source sharing a capability, not last-source-wins
        # (real bug found once Stage 2 makes historical_contribution
        # non-zero: a dict comprehension previously overwrote all but one
        # source's contribution per capability -- invisible while the field
        # was always 0.0, silently wrong the moment it became real evidence).
        contribution_sums: dict[str, float] = {}
        contribution_counts: dict[str, int] = {}
        for s in self.registry.list():
            capability = s["observation_capability"]
            contribution_sums[capability] = contribution_sums.get(capability, 0.0) + max(
                float(s["historical_contribution"] or 0.0), 0.0
            )
            contribution_counts[capability] = contribution_counts.get(capability, 0) + 1
        contribution = {cap: contribution_sums[cap] / contribution_counts[cap] for cap in contribution_sums}
        health = self.health.capability_health()
        allocations = {a.capability: a for a in self.budget.allocate(capabilities, contribution, health)}
        cadence: dict[str, float] = {}
        base_cycle_minutes = 60.0
        for source in self.registry.list(status="active"):
            allocation = allocations.get(source["observation_capability"])
            if allocation is None or allocation.units <= 0:
                continue
            # More units -> shorter interval; bounded by the cadence tiers.
            minutes = max(
                float(self.config.get("scheduler.cadence.high_minutes", 5)),
                min(
                    float(self.config.get("scheduler.cadence.low_minutes", 1440)),
                    base_cycle_minutes * (10.0 / allocation.units),
                ),
            )
            name = f"observe:{source['source_id']}"
            try:
                scheduler.set_interval(name, timedelta(minutes=minutes))
                cadence[name] = minutes
            except KeyError:
                continue
        return cadence
