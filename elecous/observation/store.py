"""Observation Database (spec 2.10-2.12).

Stores only normalized observations — never HTML, never webpages. Handles:

* validation gate — nothing enters unvalidated (2.3),
* automatic deduplication by semantic/temporal/geographic/entity hashes (2.12),
* observation compression — N reports of the same world change merge into one
  observation whose ``source_count`` grows and whose source records remain
  available for auditing (2.11),
* append-only versioning (11.16).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from elecous.common import timeutil
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.observation.model import Observation
from elecous.observation.validators.checks import ObservationValidator, ValidationOutcome
from elecous.storage.database import DatabaseManager, Migration, global_databases

OBSERVATIONS_MIGRATION = Migration(
    "observations.0001.create_observations",
    "observations",
    (
        """
        CREATE TABLE IF NOT EXISTS observations (
            observation_id VARCHAR PRIMARY KEY,
            dedup_key VARCHAR NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            entity VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            observation_type VARCHAR NOT NULL,
            value DOUBLE NOT NULL,
            unit VARCHAR NOT NULL,
            level INTEGER,
            country VARCHAR,
            region VARCHAR,
            lat DOUBLE,
            lon DOUBLE,
            signal_candidate BOOLEAN NOT NULL,
            confidence DOUBLE NOT NULL,
            source_id VARCHAR NOT NULL,
            source_record VARCHAR NOT NULL,
            source_count INTEGER NOT NULL,
            checksum VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            collection_time TIMESTAMPTZ NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_obs_dedup ON observations (dedup_key)",
        "CREATE INDEX IF NOT EXISTS idx_obs_time ON observations (timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_obs_entity ON observations (entity, observation_type)",
        "CREATE INDEX IF NOT EXISTS idx_obs_category ON observations (category)",
        """
        CREATE TABLE IF NOT EXISTS rejected_observations (
            rejected_at TIMESTAMPTZ NOT NULL,
            reason VARCHAR NOT NULL,
            source_id VARCHAR,
            entity VARCHAR,
            observation_type VARCHAR,
            detail VARCHAR
        )
        """,
    ),
)


class ObservationStore:
    def __init__(self, databases: DatabaseManager | None = None, bus: EventBus | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(OBSERVATIONS_MIGRATION)
        self.db = self.databases.get("observations")
        self.bus = bus or global_bus()
        self.log = get_logger("observation")
        self.validator = ObservationValidator()

    # ------------------------------------------------------------- ingestion
    def ingest(self, observations: Iterable[Observation]) -> dict[str, int]:
        """Validate and store a batch. Returns counters for health metrics."""
        stats = {"accepted": 0, "merged": 0, "rejected": 0, "duplicates": 0}
        for obs in observations:
            outcome = self.validator.validate(obs, self)
            if not outcome.passed:
                self._reject(obs, outcome)
                stats["rejected"] += 1
                continue
            merged = self._upsert(obs)
            if merged == "merged":
                stats["merged"] += 1
            elif merged == "duplicate":
                stats["duplicates"] += 1
            else:
                stats["accepted"] += 1
                self.bus.emit(
                    EventType.OBSERVATION_ADDED,
                    source="observation",
                    observation_id=obs.observation_id,
                    entity=obs.entity,
                    category=obs.category,
                    observation_type=obs.observation_type,
                )
        return stats

    def _upsert(self, obs: Observation) -> str:
        """Insert, or merge into the existing observation with the same
        dedup key (spec 2.11-2.12)."""
        existing = self.db.query_one(
            "SELECT observation_id, source_id, source_count, value, checksum FROM observations WHERE dedup_key = ?",
            [obs.dedup_key],
        )
        if existing is None:
            self.db.execute(
                f"INSERT INTO observations ({', '.join(Observation.columns())}) VALUES ({', '.join('?' * len(Observation.columns()))})",
                obs.to_row(),
            )
            return "inserted"
        existing_id, existing_source, source_count, existing_value, existing_checksum = existing
        if existing_checksum == obs.checksum:
            return "duplicate"  # byte-identical report; nothing new learned
        # Independent report of the same world change: compress into one
        # event observation. Confidence grows with corroboration; the raw
        # source references remain auditable in the merge log.
        new_count = source_count + 1
        corroborated_confidence = min(1.0, 1.0 - (1.0 - 0.6) ** new_count)
        self.db.execute(
            "UPDATE observations SET source_count = ?, confidence = greatest(confidence, ?) WHERE observation_id = ?",
            [new_count, corroborated_confidence, existing_id],
        )
        self.db.execute(
            "INSERT INTO rejected_observations VALUES (?, ?, ?, ?, ?, ?)",
            [
                timeutil.utcnow(),
                "merged_into_existing",
                obs.source_record.source_id,
                obs.entity,
                obs.observation_type,
                json.dumps({"merged_into": existing_id, "source_record": obs.source_record.__dict__}),
            ],
        )
        return "merged"

    def _reject(self, obs: Observation, outcome: ValidationOutcome) -> None:
        self.db.execute(
            "INSERT INTO rejected_observations VALUES (?, ?, ?, ?, ?, ?)",
            [
                timeutil.utcnow(),
                outcome.failed_check or "unknown",
                obs.source_record.source_id,
                obs.entity,
                obs.observation_type,
                json.dumps(outcome.detail, default=str),
            ],
        )
        self.bus.emit(
            EventType.OBSERVATION_REJECTED,
            source="observation",
            reason=outcome.failed_check,
            source_id=obs.source_record.source_id,
        )

    # --------------------------------------------------------------- queries
    def exists_dedup(self, key: str) -> bool:
        return self.db.query_one("SELECT 1 FROM observations WHERE dedup_key = ?", [key]) is not None

    def series(
        self,
        entity: str,
        observation_type: str,
        start: datetime,
        end: datetime,
        country: str | None = None,
    ) -> list[dict[str, Any]]:
        """Daily-ordered time series for an entity/type, hot tier."""
        sql = (
            "SELECT timestamp, value, unit, confidence, source_count FROM observations "
            "WHERE entity = ? AND observation_type = ? AND timestamp >= ? AND timestamp <= ?"
        )
        params: list[Any] = [entity, observation_type, timeutil.ensure_utc(start), timeutil.ensure_utc(end)]
        if country:
            sql += " AND country = ?"
            params.append(country)
        sql += " ORDER BY timestamp"
        return self.db.query_dicts(sql, params)

    def latest(self, category: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM observations"
        params: list[Any] = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return self.db.query_dicts(sql, params)

    def count(self) -> int:
        return self.db.query_one("SELECT count(*) FROM observations")[0]

    def counts_by_category(self) -> dict[str, int]:
        return {
            r[0]: r[1]
            for r in self.db.query("SELECT category, count(*) FROM observations GROUP BY category")
        }

    def last_timestamp(self, source_id: str) -> datetime | None:
        row = self.db.query_one("SELECT max(timestamp) FROM observations WHERE source_id = ?", [source_id])
        return timeutil.ensure_utc(row[0]) if row and row[0] else None
