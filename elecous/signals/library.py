"""The Signal Library (spec 4.4) and signal lifecycle (spec 4.7).

Every discovered signal — environmental or demand — lives here with its
reliability, contribution, coverage and lifecycle. Signals are reusable;
Environmental Factors are built from signals. Promotion follows the
universal lifecycle ladder and requires validation evidence.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.common.vocabulary import Lifecycle, ValidationStatus
from elecous.governance import constitution
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, Migration, global_databases

SIGNALS_MIGRATION = Migration(
    "signals.0001.create_signal_library",
    "signals",
    (
        """
        CREATE TABLE IF NOT EXISTS signal_library (
            signal_id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            signal_key VARCHAR NOT NULL,       -- phenomenon:direction:scope or demand type
            signal_kind VARCHAR NOT NULL,      -- environmental|demand
            observation_capability VARCHAR NOT NULL,
            categories VARCHAR NOT NULL,       -- JSON list of demand categories linked
            countries VARCHAR NOT NULL,        -- JSON list
            reliability DOUBLE NOT NULL,
            contribution DOUBLE NOT NULL,
            freshness DOUBLE NOT NULL,
            coverage DOUBLE NOT NULL,
            historical_performance VARCHAR NOT NULL,  -- JSON history of validations
            seen_in_episodes INTEGER NOT NULL,
            lifecycle VARCHAR NOT NULL,
            validation_status VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (signal_id, version)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_signal_key ON signal_library (signal_key)",
    ),
)


class SignalLibrary:
    def __init__(self, databases: DatabaseManager | None = None, bus: EventBus | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(SIGNALS_MIGRATION)
        self.db = self.databases.get("signals")
        self.bus = bus or global_bus()
        self.log = get_logger("signals")

    def latest(self, signal_key: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts(
            "SELECT * FROM signal_library WHERE signal_key = ? ORDER BY version DESC LIMIT 1", [signal_key]
        )
        return rows[0] if rows else None

    def observe_candidate(
        self,
        signal_key: str,
        signal_kind: str,
        observation_capability: str,
        category: str | None,
        country: str | None,
    ) -> str:
        """Record a signal sighting. New keys enter as candidates; existing
        keys accumulate episode counts (spec 4.5: thousands generated, most
        rejected later)."""
        existing = self.latest(signal_key)
        now = timeutil.utcnow()
        if existing is None:
            signal_id = new_id("signal")
            self.db.execute(
                "INSERT INTO signal_library VALUES (" + ", ".join("?" * 17) + ")",
                [
                    signal_id,
                    1,
                    signal_key,
                    signal_kind,
                    observation_capability,
                    json.dumps([category] if category else []),
                    json.dumps([country] if country else []),
                    0.0,
                    0.0,
                    1.0,
                    0.0,
                    json.dumps([]),
                    1,
                    Lifecycle.CANDIDATE.value,
                    ValidationStatus.PENDING.value,
                    now,
                    now,
                ],
            )
            self.bus.emit(EventType.SIGNAL_DISCOVERED, source="signals", signal_key=signal_key)
            return signal_id
        # accumulate sighting on the current version (counter, not science)
        categories = set(json.loads(existing["categories"]))
        countries = set(json.loads(existing["countries"]))
        if category:
            categories.add(category)
        if country:
            countries.add(country)
        self.db.execute(
            "UPDATE signal_library SET seen_in_episodes = seen_in_episodes + 1, categories = ?, countries = ?, updated_at = ? "
            "WHERE signal_id = ? AND version = ?",
            [json.dumps(sorted(categories)), json.dumps(sorted(countries)), now, existing["signal_id"], existing["version"]],
        )
        return existing["signal_id"]

    def apply_validation(
        self,
        signal_key: str,
        reliability: float,
        contribution: float,
        coverage: float,
        passed: bool,
    ) -> None:
        """Append a validated new version with updated scores and lifecycle
        movement (4.6-4.7). Called by the Validation Engine only."""
        existing = self.latest(signal_key)
        if existing is None:
            raise KeyError(signal_key)
        history = json.loads(existing["historical_performance"])
        history.append(
            {
                "at": timeutil.to_iso(timeutil.utcnow()),
                "reliability": reliability,
                "contribution": contribution,
                "passed": passed,
            }
        )
        current = Lifecycle(existing["lifecycle"])
        if passed:
            ladder = {
                Lifecycle.CANDIDATE: Lifecycle.EXPERIMENTAL,
                Lifecycle.EXPERIMENTAL: Lifecycle.VALIDATED,
                Lifecycle.VALIDATED: Lifecycle.TRUSTED,
                Lifecycle.TRUSTED: Lifecycle.TRUSTED,
                Lifecycle.MONITORING: Lifecycle.TRUSTED,
            }
            target = ladder.get(current, current)
            status = ValidationStatus.PASSED
        else:
            target = Lifecycle.DECLINING if current in (Lifecycle.VALIDATED, Lifecycle.TRUSTED) else Lifecycle.RETIRED
            status = ValidationStatus.FAILED
        if target != current:
            constitution.require_validated_promotion(current, target, ValidationStatus.PASSED if passed else status)
        version = int(existing["version"]) + 1
        self.db.execute(
            "INSERT INTO signal_library VALUES (" + ", ".join("?" * 17) + ")",
            [
                existing["signal_id"],
                version,
                signal_key,
                existing["signal_kind"],
                existing["observation_capability"],
                existing["categories"],
                existing["countries"],
                reliability,
                contribution,
                existing["freshness"],
                coverage,
                json.dumps(history),
                existing["seen_in_episodes"],
                target.value,
                status.value,
                existing["created_at"],
                timeutil.utcnow(),
            ],
        )
        if passed and target in (Lifecycle.VALIDATED, Lifecycle.TRUSTED):
            self.bus.emit(EventType.SIGNAL_PROMOTED, source="signals", signal_key=signal_key, lifecycle=target.value)

    def list_latest(self, lifecycle: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM signal_library s WHERE version = "
            "(SELECT max(version) FROM signal_library WHERE signal_id = s.signal_id)"
        )
        params: list[Any] = []
        if lifecycle:
            sql += " AND lifecycle = ?"
            params.append(lifecycle)
        if kind:
            sql += " AND signal_kind = ?"
            params.append(kind)
        return self.db.query_dicts(sql + " ORDER BY signal_key", params)

    def counts(self) -> dict[str, int]:
        return {
            r[0]: r[1]
            for r in self.db.query(
                "SELECT lifecycle, count(DISTINCT signal_id) FROM signal_library s WHERE version = "
                "(SELECT max(version) FROM signal_library WHERE signal_id = s.signal_id) GROUP BY lifecycle"
            )
        }
