"""Observation Gap Engine (spec 2.15, 4.23, 12.7).

Asks: "What important part of reality are we unable to observe?" Gaps come
from two directions:

* coverage gaps — capabilities with no healthy active source, or entities
  with stale/absent series,
* research gaps — Observation Requests emitted by downstream engines when
  predictions repeatedly fail (the Environmental Gap Engine publishes
  OBSERVATION_REQUEST events; they land here).

Gaps become Observation Requests for the Expansion Engine. The gap engine
never invents data to fill a gap; it only names what is missing.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.event_bus import Event, EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.observation.registry.registry import SourceRegistry
from elecous.observation.store import ObservationStore
from elecous.storage.database import Migration

GAP_MIGRATION = Migration(
    "observations.0003.create_observation_requests",
    "observations",
    (
        """
        CREATE TABLE IF NOT EXISTS observation_requests (
            request_id VARCHAR PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL,
            capability VARCHAR NOT NULL,
            reason VARCHAR NOT NULL,
            requested_by VARCHAR NOT NULL,
            context VARCHAR NOT NULL,
            status VARCHAR NOT NULL           -- open|investigating|satisfied|abandoned
        )
        """,
    ),
)


class ObservationGapEngine:
    def __init__(self, store: ObservationStore, registry: SourceRegistry, bus: EventBus | None = None):
        self.store = store
        self.registry = registry
        self.registry.databases.register_schema(GAP_MIGRATION)
        self.db = self.registry.db
        self.bus = bus or global_bus()
        self.log = get_logger("observation")
        self.bus.subscribe(EventType.OBSERVATION_REQUEST, self._on_request)

    # ------------------------------------------------------------- detection
    def detect_coverage_gaps(self) -> list[dict[str, Any]]:
        """Capabilities whose every source is suspended/retired/unhealthy."""
        gaps = []
        by_capability: dict[str, list[dict[str, Any]]] = {}
        for source in self.registry.list():
            by_capability.setdefault(source["observation_capability"], []).append(source)
        for capability, sources in by_capability.items():
            healthy = [s for s in sources if s["status"] == "active" and float(s["health"]) >= 0.3]
            if not healthy:
                gaps.append(
                    {
                        "capability": capability,
                        "reason": "no healthy active source",
                        "sources": [s["source_id"] for s in sources],
                    }
                )
        return gaps

    def record_gaps(self) -> int:
        from elecous.common.ids import new_id

        recorded = 0
        for gap in self.detect_coverage_gaps():
            existing = self.db.query_one(
                "SELECT request_id FROM observation_requests WHERE capability = ? AND status = 'open' AND reason = ?",
                [gap["capability"], gap["reason"]],
            )
            if existing:
                continue
            self.db.execute(
                "INSERT INTO observation_requests VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    new_id("task"),
                    timeutil.utcnow(),
                    gap["capability"],
                    gap["reason"],
                    "observation_gap_engine",
                    json.dumps(gap, default=str),
                    "open",
                ],
            )
            recorded += 1
            self.bus.emit(
                EventType.EXPANSION_CANDIDATE,
                source="observation",
                capability=gap["capability"],
                reason=gap["reason"],
            )
        return recorded

    # -------------------------------------------------- downstream requests
    def _on_request(self, event: Event) -> None:
        from elecous.common.ids import new_id

        payload = event.payload
        self.db.execute(
            "INSERT INTO observation_requests VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                new_id("task"),
                timeutil.utcnow(),
                payload.get("capability", "unknown"),
                payload.get("reason", "downstream request"),
                event.source,
                json.dumps(payload, default=str),
                "open",
            ],
        )
        self.log.info("observation request recorded", capability=payload.get("capability"), source=event.source)

    def open_requests(self) -> list[dict[str, Any]]:
        return self.db.query_dicts(
            "SELECT * FROM observation_requests WHERE status = 'open' ORDER BY created_at DESC"
        )

    def set_request_status(self, request_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE observation_requests SET status = ? WHERE request_id = ?", [status, request_id]
        )
