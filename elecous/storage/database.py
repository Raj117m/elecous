"""DuckDB database manager (spec 10.14, 10.16).

One database file per scientific responsibility: observations, demand,
signals, environment, mechanisms, knowledge, predictions, validation,
dashboard. Isolation improves reliability — corruption or contention in one
domain never touches another.

Note: the spec's example list in 10.14 omits ``demand.duckdb`` while chapter
3.19 requires a Demand Intelligence Database. Following 10.14's own principle
(separate databases by responsibility), demand intelligence gets its own
database file. Recorded in docs/decisions/0001-demand-database.md.

Schemas are declared by each subsystem via ``register_schema`` and applied
idempotently. Migrations are tracked in ``_schema_migrations`` inside each
database so schema history is auditable (spec 11.16).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import duckdb

from elecous.common import timeutil
from elecous.common.errors import DatabaseError
from elecous.infrastructure.config import Config, active_config

MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS _schema_migrations (
    migration_id VARCHAR PRIMARY KEY,
    domain VARCHAR NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL,
    statement_hash VARCHAR NOT NULL
)
"""


@dataclass(frozen=True)
class Migration:
    migration_id: str  # e.g. "observations.0001.create_observations"
    domain: str
    statements: tuple[str, ...]


class Database:
    """A single-domain DuckDB handle with serialized writes."""

    def __init__(self, domain: str, path: Path):
        self.domain = domain
        self.path = path
        self._conn = duckdb.connect(str(path))
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute(MIGRATIONS_TABLE)

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> duckdb.DuckDBPyConnection:
        with self._lock:
            try:
                return self._conn.execute(sql, list(params) if params is not None else None)
            except duckdb.Error as exc:
                raise DatabaseError(f"{self.domain}: {exc}", context={"sql": sql[:200]}) from exc

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            try:
                self._conn.executemany(sql, rows)
            except duckdb.Error as exc:
                raise DatabaseError(f"{self.domain}: {exc}", context={"sql": sql[:200]}) from exc

    def query(self, sql: str, params: Iterable[Any] | None = None) -> list[tuple]:
        return self.execute(sql, params).fetchall()

    def query_dicts(self, sql: str, params: Iterable[Any] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self.execute(sql, params)
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] | None = None) -> tuple | None:
        return self.execute(sql, params).fetchone()

    def apply_migration(self, migration: Migration) -> bool:
        from elecous.common.ids import content_hash

        with self._lock:
            existing = self.query_one(
                "SELECT migration_id FROM _schema_migrations WHERE migration_id = ?", [migration.migration_id]
            )
            if existing:
                return False
            for statement in migration.statements:
                self._conn.execute(statement)
            self._conn.execute(
                "INSERT INTO _schema_migrations VALUES (?, ?, ?, ?)",
                [
                    migration.migration_id,
                    migration.domain,
                    timeutil.utcnow(),
                    content_hash(list(migration.statements)),
                ],
            )
            return True

    def table_exists(self, name: str) -> bool:
        row = self.query_one(
            "SELECT table_name FROM information_schema.tables WHERE table_name = ?", [name]
        )
        return row is not None

    def integrity_check(self) -> dict[str, Any]:
        """Cheap consistency probe used by self-healing (spec 12.23)."""
        tables = [r[0] for r in self.query("SELECT table_name FROM information_schema.tables")]
        counts = {}
        for table in tables:
            counts[table] = self.query_one(f'SELECT count(*) FROM "{table}"')[0]  # noqa: S608 - table names come from information_schema
        return {"domain": self.domain, "path": str(self.path), "tables": counts}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class DatabaseManager:
    """Owns one Database per domain and all registered migrations."""

    def __init__(self, config: Config | None = None):
        self.config = config or active_config()
        self._databases: dict[str, Database] = {}
        self._migrations: list[Migration] = []
        self._lock = threading.RLock()

    def register_schema(self, migration: Migration) -> None:
        with self._lock:
            if all(m.migration_id != migration.migration_id for m in self._migrations):
                self._migrations.append(migration)
            db = self._databases.get(migration.domain)
        if db is not None:
            db.apply_migration(migration)

    def get(self, domain: str) -> Database:
        domains = self.config.get("databases.domains", [])
        if domain not in domains:
            raise DatabaseError(f"unknown database domain: {domain}")
        with self._lock:
            if domain not in self._databases:
                db = Database(domain, self.config.db_path(domain))
                for migration in self._migrations:
                    if migration.domain == domain:
                        db.apply_migration(migration)
                self._databases[domain] = db
            return self._databases[domain]

    def integrity_report(self) -> list[dict[str, Any]]:
        return [db.integrity_check() for db in list(self._databases.values())]

    def close_all(self) -> None:
        with self._lock:
            for db in self._databases.values():
                db.close()
            self._databases.clear()


_manager_lock = threading.Lock()
_manager: DatabaseManager | None = None


def global_databases() -> DatabaseManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = DatabaseManager()
        return _manager


def reset_databases() -> None:
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.close_all()
        _manager = None
