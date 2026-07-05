"""The Autonomous Supervisor (spec 12.2-12.5).

A permanent subsystem above every other engine. It performs zero scientific
reasoning — it only asks "is every subsystem behaving normally?" and runs
the universal self-healing pipeline: Detect -> Isolate -> Diagnose -> Repair
-> Validate -> Resume. If repair succeeds, no notification is required
(spec 12.4); only repeated failure escalates to the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from elecous.common import timeutil
from elecous.infrastructure.logging import get_logger


@dataclass
class HealthReport:
    subsystem: str
    healthy: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RepairAttempt:
    subsystem: str
    issue: str
    action: str
    succeeded: bool
    detail: dict[str, Any] = field(default_factory=dict)


class AutonomousSupervisor:
    def __init__(self, stack):
        self.stack = stack
        self.log = get_logger("supervisor")
        # subsystem -> repair strategy. Each strategy receives the health
        # report's detail dict and returns True if repair succeeded.
        self._repairers: dict[str, Callable[[HealthReport], bool]] = {
            "observation": self._repair_observation,
            "knowledge": self._repair_knowledge,
        }
        self._failure_streak: dict[str, int] = {}

    # ------------------------------------------------------------ monitoring
    def check_observation(self) -> HealthReport:
        sources = self.stack.observation.registry.list()
        active = [s for s in sources if s["status"] == "active"]
        unhealthy = [s for s in active if float(s["health"]) < 0.3]
        failing = [s for s in active if int(s["failure_count"]) >= 3]
        healthy = not failing
        return HealthReport(
            "observation",
            healthy,
            {"active": len(active), "unhealthy": [s["source_id"] for s in unhealthy], "failing": [s["source_id"] for s in failing]},
        )

    def check_knowledge(self) -> HealthReport:
        """Read-only: detection must never itself repair, or the repair
        stage of the pipeline would find nothing left to do (Detect and
        Repair are distinct stages, spec 12.4)."""
        report = self.stack.knowledge.detect_issues()
        healthy = report["broken_edges"] == 0 and report["duplicate_node_groups"] == 0
        return HealthReport("knowledge", healthy, report)

    def check_validation(self) -> HealthReport:
        audit = self.stack.validation.self_audit()
        return HealthReport("validation", audit["verdict"] == "healthy", audit)

    def check_scheduler(self) -> HealthReport:
        tasks = self.stack.scheduler.report()
        stuck = [t for t in tasks if t["consecutive_failures"] >= 3]
        return HealthReport("scheduler", not stuck, {"stuck_tasks": [t["name"] for t in stuck]})

    def check_resources(self) -> HealthReport:
        sample = self.stack.resources.last_sample()
        pause = self.stack.resources.should_pause()
        return HealthReport("resources", not pause, sample)

    def full_health_check(self) -> list[HealthReport]:
        return [
            self.check_observation(),
            self.check_knowledge(),
            self.check_validation(),
            self.check_scheduler(),
            self.check_resources(),
        ]

    # ------------------------------------------------------------ self-heal
    def self_heal(self) -> list[RepairAttempt]:
        """Detect -> Isolate -> Diagnose -> Repair -> Validate -> Resume
        (spec 12.4). Repeated failure past three attempts escalates via a
        dashboard notification instead of retrying forever."""
        attempts: list[RepairAttempt] = []
        for report in self.full_health_check():
            if report.healthy:
                self._failure_streak[report.subsystem] = 0
                continue
            repairer = self._repairers.get(report.subsystem)
            if repairer is None:
                continue
            self._failure_streak[report.subsystem] = self._failure_streak.get(report.subsystem, 0) + 1
            succeeded = repairer(report)
            attempts.append(RepairAttempt(report.subsystem, str(report.detail), repairer.__name__, succeeded, report.detail))
            if succeeded:
                self._failure_streak[report.subsystem] = 0
                self.log.info("self-healing succeeded silently", subsystem=report.subsystem)
            elif self._failure_streak[report.subsystem] >= 3:
                self._escalate(report)
        return attempts

    def _repair_observation(self, report: HealthReport) -> bool:
        """Connector self-healing (spec 12.5): retry once immediately; if
        still failing, suspend for the Expansion Engine's source-replacement
        search rather than continuing to burn budget on a dead source.

        Final Implementation Directive, Stage 6/7: for an auto-generated
        connector specifically (Final Missing Capability directive), a
        real, previously-working extraction can start failing because the
        source's real structure changed -- not just because the source is
        gone. Before suspending one of those, re-run Stages 1-4
        (ConnectorEvolutionEngine.attempt) against the same real URL: if a
        new extraction spec is found, swap the connector_config in place
        (repair) instead of suspending it as if the source had vanished."""
        repaired = True
        for source_id in report.detail.get("failing", []):
            result = self.stack.observation.run_source(source_id)
            if result["success"]:
                self.stack.observation.registry.set_status(source_id, "active", reason="self-healed")
                continue
            if self._try_regenerate_connector(source_id):
                continue
            self.stack.observation.registry.set_status(
                source_id, "suspended", reason="repeated failure; awaiting alternative source"
            )
            self.stack.gap.record_gaps()
            repaired = False
        return repaired

    def _try_regenerate_connector(self, source_id: str) -> bool:
        source = self.stack.observation.registry.get(source_id)
        if source is None or source["connector"] != "elecous.observation.connectors.auto.AutoConnector":
            return False
        import json as json_module

        params = json_module.loads(source["connector_config"])
        try:
            response = self.stack.source_discovery.http.get(params["url"])
        except Exception as exc:
            self.log.info("connector regeneration fetch failed", source_id=source_id, error=str(exc))
            return False
        attempt = self.stack.connector_evolution.attempt(
            params["url"], response.headers.get("Content-Type", ""), response.content,
        )
        if attempt["outcome"] != "generated":
            return False
        new_candidate = json_module.loads(attempt["best_candidate"])
        new_config = {**params, "path": new_candidate["path"], "field_roles": new_candidate["field_roles"]}
        if new_config == params:
            return False  # same spec still fails -- not a structural change, don't pretend to have fixed it
        self.stack.observation.registry.db.execute(
            "UPDATE source_registry SET connector_config = ?, failure_count = 0, updated_at = ? WHERE source_id = ?",
            [json_module.dumps(new_config), timeutil.utcnow(), source_id],
        )
        retry = self.stack.observation.run_source(source_id)
        if retry["success"]:
            self.stack.observation.registry.set_status(source_id, "active", reason="connector regenerated")
            self.log.info("auto-connector regenerated after real structure change", source_id=source_id)
            return True
        return False

    def _repair_knowledge(self, report: HealthReport) -> bool:
        after = self.stack.knowledge.self_heal()
        return after["broken_edges_remaining"] == 0 and after["duplicate_node_groups"] == 0

    def _escalate(self, report: HealthReport) -> None:
        from elecous.dashboard.monitor.notifications import NotificationEngine

        engine = NotificationEngine(self.stack.databases, self.stack.config)
        engine.emit_system_alert(
            "self_healing_exhausted",
            f"{report.subsystem} failed self-healing after repeated attempts",
            "critical",
            report.detail,
        )
        self.log.error("self-healing exhausted, escalating", subsystem=report.subsystem, detail=report.detail)

    # ------------------------------------------------------------- summary
    def status(self) -> dict[str, Any]:
        reports = self.full_health_check()
        return {
            "timestamp": timeutil.to_iso(timeutil.utcnow()),
            "subsystems": {r.subsystem: {"healthy": r.healthy, "detail": r.detail} for r in reports},
            "overall": all(r.healthy for r in reports),
        }
