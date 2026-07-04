"""Knowledge Memory (spec 7.16-7.19).

World State Memory: periodic snapshots of which mechanisms/factors were
active, so "show the world on 2028-07-14" is a lookup, not a recomputation.
Failure Memory: every failed validation/prediction becomes permanent
research, never silently dropped. Unknown Memory: first-class citizenship
for what ELECOUS doesn't yet understand (Appendix A Principle 9).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.infrastructure.logging import get_logger
from elecous.knowledge.schema import KNOWLEDGE_MIGRATION
from elecous.storage.database import DatabaseManager, global_databases


class KnowledgeMemory:
    def __init__(self, databases: DatabaseManager | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(KNOWLEDGE_MIGRATION)
        self.db = self.databases.get("knowledge")
        self.log = get_logger("knowledge")

    # ------------------------------------------------------- world state
    def snapshot_world_state(
        self, world_state: dict[str, Any], active_mechanisms: list[str], active_factors: list[str]
    ) -> str:
        snapshot_id = new_id("knowledge")
        self.db.execute(
            "INSERT INTO world_state_memory VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                snapshot_id,
                timeutil.from_iso(world_state["as_of"]),
                world_state["state_id"],
                json.dumps(active_mechanisms),
                json.dumps(active_factors),
                json.dumps(world_state["conditions"]),
                timeutil.utcnow(),
            ],
        )
        return snapshot_id

    def world_state_near(self, as_of: datetime) -> dict[str, Any] | None:
        rows = self.db.query_dicts(
            "SELECT * FROM world_state_memory WHERE as_of <= ? ORDER BY as_of DESC LIMIT 1",
            [timeutil.ensure_utc(as_of)],
        )
        return rows[0] if rows else None

    def episodes_matching(self, category_hint: str | None = None) -> list[dict[str, Any]]:
        """Research Memory (spec 7.17): questions like 'show every episode
        during heatwaves' resolve through world_state_memory's conditions."""
        rows = self.db.query_dicts("SELECT * FROM world_state_memory ORDER BY as_of")
        if category_hint is None:
            return rows
        return [r for r in rows if category_hint.lower() in json.loads(r["conditions"]).__str__().lower()]

    # ---------------------------------------------------------- failures
    def record_failure(self, object_type: str, object_id: str, summary: str, detail: dict[str, Any]) -> str:
        failure_id = new_id("knowledge")
        self.db.execute(
            "INSERT INTO failure_memory VALUES (?, ?, ?, ?, ?, ?)",
            [failure_id, object_type, object_id, timeutil.utcnow(), summary, json.dumps(detail, default=str)],
        )
        self.log.info("failure recorded in knowledge memory", object_type=object_type, object_id=object_id)
        return failure_id

    def failures_for(self, object_id: str) -> list[dict[str, Any]]:
        return self.db.query_dicts(
            "SELECT * FROM failure_memory WHERE object_id = ? ORDER BY recorded_at DESC", [object_id]
        )

    # ---------------------------------------------------------- unknowns
    def record_unknown(
        self, kind: str, description: str, context: dict[str, Any], research_priority: float = 0.5
    ) -> str:
        unknown_id = new_id("knowledge")
        now = timeutil.utcnow()
        self.db.execute(
            "INSERT INTO unknown_memory VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [unknown_id, kind, description, json.dumps(context, default=str), research_priority, "open", now, now],
        )
        return unknown_id

    def open_unknowns(self, min_priority: float = 0.0) -> list[dict[str, Any]]:
        return self.db.query_dicts(
            "SELECT * FROM unknown_memory WHERE status = 'open' AND research_priority >= ? ORDER BY research_priority DESC",
            [min_priority],
        )

    def resolve_unknown(self, unknown_id: str, status: str = "resolved") -> None:
        self.db.execute(
            "UPDATE unknown_memory SET status = ?, updated_at = ? WHERE unknown_id = ?",
            [status, timeutil.utcnow(), unknown_id],
        )
