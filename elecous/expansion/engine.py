"""Expansion Engine (spec 12.8-12.10, ch.10.12).

Continuously asks "can I legally observe the world better than yesterday?"
Matches open Observation Requests (spec 2.15, 4.23) from the Gap Engine
against the declared candidate pool (see ADR 0002 for why discovery itself
is a curated pool rather than live web search), registers matches into the
Source Registry at status "sandbox", evaluates them through the existing
Observation Sandbox, and promotes only on success. Nothing reaches
production without passing every check.
"""

from __future__ import annotations

from typing import Any

from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.observation.registry.registry import Source, SourceRegistry
from elecous.observation.sandbox.sandbox import ObservationSandbox


class ExpansionEngine:
    def __init__(
        self,
        registry: SourceRegistry,
        sandbox: ObservationSandbox,
        gap_engine,
        config: Config | None = None,
        bus: EventBus | None = None,
    ):
        self.registry = registry
        self.sandbox = sandbox
        self.gap_engine = gap_engine
        self.config = config or active_config()
        self.bus = bus or global_bus()
        self.log = get_logger("expansion")

    def candidate_pool(self) -> list[dict[str, Any]]:
        return self.config.load_file("candidate_sources.yaml").get("candidates", [])

    def discover_candidates(self) -> list[dict[str, Any]]:
        """Match open Observation Requests against the candidate pool by
        observation capability (spec 12.8)."""
        open_requests = self.gap_engine.open_requests()
        requested_capabilities = {r["capability"] for r in open_requests}
        matches = []
        for candidate in self.candidate_pool():
            if self.registry.get(candidate["source_id"]) is not None:
                continue  # already registered somewhere in the pipeline
            if candidate["observation_capability"] in requested_capabilities or not requested_capabilities:
                matches.append(candidate)
        return matches

    def register_for_sandbox(self, candidate: dict[str, Any]) -> str:
        """Connector Generator hookup (spec 12.9): the connector already
        exists in code (real implementations are engineered, not invented at
        runtime); registration here is the moment it enters the Sandbox
        Laboratory for evaluation (spec 12.10)."""
        source = Source(
            source_id=candidate["source_id"],
            name=candidate["name"],
            provider=candidate["provider"],
            category=candidate["category"],
            observation_capability=candidate["observation_capability"],
            license=candidate["license"],
            access_method=candidate["access_method"],
            update_frequency=candidate.get("update_frequency", "daily"),
            countries=candidate.get("countries", []),
            coverage=candidate.get("coverage", "global"),
            connector=candidate["connector"],
            connector_config=candidate.get("connector_config", {}),
            status="sandbox",
        )
        self.registry._insert(source)  # noqa: SLF001 - registry owns insertion, expansion is a legitimate caller
        self.bus.emit(EventType.EXPANSION_CANDIDATE, source="expansion", source_id=source.source_id, stage="registered")
        self.log.info("candidate registered for sandbox", source_id=source.source_id)
        return source.source_id

    def run_expansion_cycle(self) -> dict[str, Any]:
        """One full cycle: discover -> register -> sandbox -> promote."""
        discovered = self.discover_candidates()
        registered = []
        promoted = []
        rejected = []
        for candidate in discovered:
            source_id = self.register_for_sandbox(candidate)
            registered.append(source_id)
            try:
                if self.sandbox.promote_if_passing(source_id):
                    promoted.append(source_id)
                    self.gap_engine.set_request_status_for_capability(
                        candidate["observation_capability"], "satisfied"
                    )
                else:
                    rejected.append(source_id)
            except Exception as exc:
                self.log.warning("sandbox evaluation failed", source_id=source_id, error=str(exc))
                rejected.append(source_id)
        return {"discovered": len(discovered), "registered": registered, "promoted": promoted, "rejected": rejected}
