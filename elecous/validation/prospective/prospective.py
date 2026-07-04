"""Live prospective validation (spec 6.12).

A mechanism discovered today is frozen — its exact version recorded — and
the future decides. When the mechanism's factor signals activate, a
prospective prediction is registered with a resolution deadline; reality
resolves it. Only prospective success creates Trusted Knowledge.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, global_databases
from elecous.validation.schema import VALIDATION_MIGRATION

from datetime import timedelta


class ProspectiveValidator:
    def __init__(self, episodes, databases: DatabaseManager | None = None):
        self.episodes = episodes  # EpisodeStore
        self.databases = databases or global_databases()
        self.databases.register_schema(VALIDATION_MIGRATION)
        self.db = self.databases.get("validation")
        self.log = get_logger("validation")

    def register(self, mechanism: dict[str, Any], activation: float) -> str:
        """Freeze the mechanism version and let the future judge."""
        lead = json.loads(mechanism["lead_time"])
        horizon = max(float(lead.get("max") or 14.0), 7.0)
        prospective_id = new_id("validation")
        category = json.loads(mechanism["affected_categories"])[0]
        now = timeutil.utcnow()
        self.db.execute(
            "INSERT INTO prospective_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                prospective_id,
                mechanism["mechanism_id"],
                int(mechanism["version"]),  # frozen: no edits count (6.12)
                category,
                now,
                round(activation, 4),
                now + timedelta(days=horizon),
                None,
                None,
                None,
            ],
        )
        self.log.info(
            "prospective prediction registered",
            mechanism_id=mechanism["mechanism_id"],
            version=mechanism["version"],
            category=category,
        )
        return prospective_id

    def has_open(self, mechanism_id: str) -> bool:
        return (
            self.db.query_one(
                "SELECT 1 FROM prospective_predictions WHERE mechanism_id = ? AND outcome IS NULL",
                [mechanism_id],
            )
            is not None
        )

    def resolve_due(self) -> list[dict[str, Any]]:
        """Resolve prospective predictions whose window has closed: success
        if a genuine (non-control, non-negative) episode for the category
        began between issue and deadline."""
        now = timeutil.utcnow()
        open_rows = self.db.query_dicts(
            "SELECT * FROM prospective_predictions WHERE outcome IS NULL"
        )
        resolved = []
        for row in open_rows:
            issued = timeutil.ensure_utc(row["issued_at"])
            deadline = timeutil.ensure_utc(row["expected_by"])
            episodes = [
                e
                for e in self.episodes.list_latest(category=row["category"], include_controls=False)
                if not e["is_negative"] and issued <= timeutil.ensure_utc(e["spike_start"]) <= deadline
            ]
            if episodes:
                outcome, episode_id = "success", episodes[0]["episode_id"]
            elif now > deadline:
                outcome, episode_id = "failure", None
            else:
                continue  # window still open
            self.db.execute(
                "UPDATE prospective_predictions SET resolved_at = ?, outcome = ?, outcome_episode = ? WHERE prospective_id = ?",
                [now, outcome, episode_id, row["prospective_id"]],
            )
            row.update({"outcome": outcome, "outcome_episode": episode_id})
            resolved.append(row)
        return resolved

    def track_record(self, mechanism_id: str) -> dict[str, Any]:
        rows = self.db.query_dicts(
            "SELECT outcome, count(*) AS n FROM prospective_predictions "
            "WHERE mechanism_id = ? AND outcome IS NOT NULL GROUP BY outcome",
            [mechanism_id],
        )
        counts = {r["outcome"]: r["n"] for r in rows}
        total = sum(counts.values())
        return {
            "successes": counts.get("success", 0),
            "failures": counts.get("failure", 0),
            "total": total,
            "success_rate": round(counts.get("success", 0) / total, 4) if total else None,
        }
