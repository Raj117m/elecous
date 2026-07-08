"""Dynamic Entity Registry (spec 12.8, 12.9, ADR 0002).

Replaces the static YAML catalog with a database table that continuously
accepts newly discovered entities from the ExpansionEngine.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.expansion.schema import EXPANSION_MIGRATION
from elecous.storage.database import DatabaseManager, global_databases


class EntityRegistry:
    def __init__(self, databases: DatabaseManager | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(EXPANSION_MIGRATION)
        self.db = self.databases.get("expansion")

    def register(self, entity: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO entity_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                entity["entity"],
                entity["article"],
                entity["category"],
                json.dumps(entity.get("traits", [])),
                entity.get("discovered", False),
                entity.get("discovery_query"),
                entity.get("discovery_median_pageviews"),
                timeutil.utcnow(),
            ],
        )

    def list_all(self) -> list[dict[str, Any]]:
        rows = self.db.query_dicts("SELECT * FROM entity_registry")
        for r in rows:
            r["traits"] = json.loads(r["traits"])
        return rows
