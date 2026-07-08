"""The Source Registry (spec 2.5) — one of the most important databases.

ELECOUS never hardcodes providers; it manages them. Sources are seeded from
``configs/sources.yaml`` and thereafter live in the observations database,
where health, reliability and status evolve from evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, Migration, global_databases

REGISTRY_MIGRATION = Migration(
    "observations.0002.create_source_registry",
    "observations",
    (
        """
        CREATE TABLE IF NOT EXISTS source_registry (
            source_id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            provider VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            domain VARCHAR NOT NULL,
            observation_capability VARCHAR NOT NULL,
            license VARCHAR NOT NULL,
            access_method VARCHAR NOT NULL,
            update_frequency VARCHAR NOT NULL,
            countries VARCHAR NOT NULL,          -- JSON list; [] = global
            coverage VARCHAR NOT NULL,
            reliability DOUBLE NOT NULL,
            latency_ms DOUBLE,
            historical_contribution DOUBLE NOT NULL,
            schema_version INTEGER NOT NULL,
            health DOUBLE NOT NULL,
            status VARCHAR NOT NULL,              -- active|sandbox|suspended|retired
            requires_credential VARCHAR,          -- config key for API key, if any
            connector VARCHAR NOT NULL,           -- python path of the connector class
            connector_config VARCHAR NOT NULL,    -- JSON connector parameters
            last_validation TIMESTAMPTZ,
            last_successful_extraction TIMESTAMPTZ,
            failure_count INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS connector_runs (
            run_id VARCHAR PRIMARY KEY,
            source_id VARCHAR NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            success BOOLEAN,
            observations_accepted INTEGER,
            observations_merged INTEGER,
            observations_rejected INTEGER,
            observations_duplicate INTEGER,
            latency_ms DOUBLE,
            schema_hash VARCHAR,
            error VARCHAR,
            run_type VARCHAR NOT NULL             -- live|backfill|sandbox
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_runs_source ON connector_runs (source_id, started_at)",
    ),
)

REGISTRY_MIGRATION_0002 = Migration(
    "observations.0002.add_domain_column",
    "observations",
    (
        "ALTER TABLE source_registry ADD COLUMN IF NOT EXISTS domain VARCHAR DEFAULT 'atmospheric'",
    ),
)

@dataclass
class Source:
    source_id: str
    name: str
    provider: str
    category: str
    domain: str
    observation_capability: str
    license: str
    access_method: str
    update_frequency: str
    countries: list[str]
    coverage: str
    connector: str
    connector_config: dict[str, Any] = field(default_factory=dict)
    reliability: float = 0.5
    latency_ms: float | None = None
    historical_contribution: float = 0.0
    schema_version: int = 1
    health: float = 0.5
    status: str = "sandbox"
    requires_credential: str | None = None
    last_validation: str | None = None
    last_successful_extraction: str | None = None
    failure_count: int = 0


class SourceRegistry:
    def __init__(self, databases: DatabaseManager | None = None, config: Config | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(REGISTRY_MIGRATION)
        self.databases.register_schema(REGISTRY_MIGRATION_0002)
        self.db = self.databases.get("observations")
        self.config = config or active_config()
        self.log = get_logger("observation")

    # ---------------------------------------------------------------- seeding
    def seed_from_config(self) -> int:
        """Insert sources declared in configs/sources.yaml that are not yet
        registered. Existing rows are never overwritten — health, reliability
        and status are earned state, not configuration."""
        declared = self.config.load_file("sources.yaml").get("sources", [])
        added = 0
        for entry in declared:
            if self.get(entry["source_id"]) is not None:
                continue
            credential_key = entry.get("requires_credential")
            has_credential = bool(self.config.get(f"credentials.{credential_key}")) if credential_key else True
            source = Source(
                source_id=entry["source_id"],
                name=entry["name"],
                provider=entry["provider"],
                category=entry["category"],
                domain=entry.get("domain", "atmospheric"),
                observation_capability=entry["observation_capability"],
                license=entry["license"],
                access_method=entry["access_method"],
                update_frequency=entry.get("update_frequency", "daily"),
                countries=entry.get("countries", []),
                coverage=entry.get("coverage", "global"),
                connector=entry["connector"],
                connector_config=entry.get("connector_config", {}),
                requires_credential=credential_key,
                # Key-gated sources without a key wait, suspended, for real
                # data (Real Data Rule) instead of being dropped.
                status=entry.get("status", "sandbox") if has_credential else "suspended",
            )
            self._insert(source)
            added += 1
        if added:
            self.log.info("source registry seeded", added=added)
        return added

    def _insert(self, s: Source) -> None:
        now = timeutil.utcnow()
        self.db.execute(
            "INSERT INTO source_registry VALUES (" + ", ".join("?" * 25) + ")",
            [
                s.source_id,
                s.name,
                s.provider,
                s.category,
                s.domain,
                s.observation_capability,
                s.license,
                s.access_method,
                s.update_frequency,
                json.dumps(s.countries),
                s.coverage,
                s.reliability,
                s.latency_ms,
                s.historical_contribution,
                s.schema_version,
                s.health,
                s.status,
                s.requires_credential,
                s.connector,
                json.dumps(s.connector_config),
                None,
                None,
                s.failure_count,
                now,
                now,
            ],
        )

    # ---------------------------------------------------------------- access
    def get(self, source_id: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts("SELECT * FROM source_registry WHERE source_id = ?", [source_id])
        return rows[0] if rows else None

    def list(self, status: str | None = None, capability: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM source_registry", []
        clauses = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if capability:
            clauses.append("observation_capability = ?")
            params.append(capability)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.db.query_dicts(sql + " ORDER BY source_id", params)

    def list_by_domain(self, domain: str, status: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM source_registry WHERE domain = ?", [domain]
        if status:
            sql += " AND status = ?"
            params.append(status)
        return self.db.query_dicts(sql + " ORDER BY source_id", params)

    def capabilities(self) -> list[str]:
        return [r[0] for r in self.db.query("SELECT DISTINCT observation_capability FROM source_registry ORDER BY 1")]

    def set_status(self, source_id: str, status: str, reason: str = "") -> None:
        self.db.execute(
            "UPDATE source_registry SET status = ?, updated_at = ? WHERE source_id = ?",
            [status, timeutil.utcnow(), source_id],
        )
        self.log.info("source status changed", source_id=source_id, status=status, reason=reason)

    def update_health(self, source_id: str, health: float, reliability: float, latency_ms: float | None) -> None:
        self.db.execute(
            "UPDATE source_registry SET health = ?, reliability = ?, latency_ms = ?, updated_at = ? WHERE source_id = ?",
            [health, reliability, latency_ms, timeutil.utcnow(), source_id],
        )

    def update_contribution(self, source_id: str, contribution: float) -> None:
        self.db.execute(
            "UPDATE source_registry SET historical_contribution = ?, updated_at = ? WHERE source_id = ?",
            [contribution, timeutil.utcnow(), source_id],
        )

    def record_run(
        self,
        source_id: str,
        started_at,
        finished_at,
        success: bool,
        stats: dict[str, int],
        latency_ms: float | None,
        schema_hash: str | None,
        error: str | None,
        run_type: str,
    ) -> None:
        self.db.execute(
            "INSERT INTO connector_runs VALUES (" + ", ".join("?" * 13) + ")",
            [
                new_id("task"),
                source_id,
                started_at,
                finished_at,
                success,
                stats.get("accepted", 0),
                stats.get("merged", 0),
                stats.get("rejected", 0),
                stats.get("duplicates", 0),
                latency_ms,
                schema_hash,
                error,
                run_type,
            ],
        )
        if success:
            self.db.execute(
                "UPDATE source_registry SET last_successful_extraction = ?, failure_count = 0, updated_at = ? WHERE source_id = ?",
                [finished_at, timeutil.utcnow(), source_id],
            )
        else:
            self.db.execute(
                "UPDATE source_registry SET failure_count = failure_count + 1, updated_at = ? WHERE source_id = ?",
                [timeutil.utcnow(), source_id],
            )

    def runs(self, source_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.query_dicts(
            "SELECT * FROM connector_runs WHERE source_id = ? ORDER BY started_at DESC LIMIT ?",
            [source_id, limit],
        )
