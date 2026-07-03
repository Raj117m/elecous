"""World State builder (spec 1.5, 8.4) and World Conditions (Appendix D).

The World State is the synchronized representation of the current observable
world: the set of active environmental signal changes right now, plus the
World Conditions (temporary global states like "thermal_stress:up +
energy_demand:up") derived from them. World Conditions gate mechanism
applicability so a mechanism is never applied outside the context where it
was valid (Appendix D).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.environment.ontology.ontology import Ontology
from elecous.environment.schema import ENVIRONMENT_MIGRATION
from elecous.environment.signal_discovery.extractor import EnvironmentalSignalExtractor
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, global_databases


class WorldStateBuilder:
    def __init__(
        self,
        extractor: EnvironmentalSignalExtractor,
        databases: DatabaseManager | None = None,
        bus: EventBus | None = None,
    ):
        self.extractor = extractor
        self.databases = databases or global_databases()
        self.databases.register_schema(ENVIRONMENT_MIGRATION)
        self.db = self.databases.get("environment")
        self.bus = bus or global_bus()
        self.ontology = Ontology()
        self.log = get_logger("environment")

    # ------------------------------------------------------------- building
    def build(self, as_of: datetime | None = None, window_days: int = 7, persist: bool = True) -> dict[str, Any]:
        """Current (or historical) World State from real observations only."""
        as_of = as_of or timeutil.utcnow()
        changes = self.extractor.changes(window_end=as_of, window_days=window_days)
        signals = [
            {
                "signal_key": c.signal_key,
                "phenomenon": c.phenomenon,
                "direction": c.direction,
                "scope": c.scope,
                "z": c.z,
                "domain": self.ontology.domain_of(c.phenomenon),
                "first_crossed_at": timeutil.to_iso(c.first_crossed_at) if c.first_crossed_at else None,
                "evidence_observation_types": list(c.observation_types),
            }
            for c in changes
        ]
        conditions = self.derive_conditions(signals)
        observation_count = self.extractor.store.count()
        state = {
            "state_id": new_id("world_state"),
            "as_of": timeutil.to_iso(as_of),
            "signals": signals,
            "conditions": conditions,
            "observation_count": observation_count,
        }
        if persist:
            self.db.execute(
                "INSERT INTO world_states VALUES (?, ?, ?, ?, ?, ?)",
                [
                    state["state_id"],
                    as_of,
                    json.dumps(signals),
                    json.dumps(conditions),
                    observation_count,
                    timeutil.utcnow(),
                ],
            )
            self._update_condition_records(conditions, as_of)
            self.bus.emit(
                EventType.WORLD_STATE_UPDATED,
                source="environment",
                state_id=state["state_id"],
                signals=len(signals),
                conditions=conditions,
            )
        return state

    # ----------------------------------------------------- world conditions
    def derive_conditions(self, signals: list[dict[str, Any]]) -> list[str]:
        """World Conditions (Appendix D): per country scope, the sorted
        combination of strongly active phenomena (|z| >= 2) forms a named
        temporary state, e.g. ``US:thermal_stress:up+energy_demand:up``.
        Global-scope behavioural/media signals form a global condition."""
        by_scope: dict[str, list[str]] = {}
        for signal in signals:
            if abs(signal["z"]) < 2.0:
                continue
            scope = signal["scope"] if len(signal["scope"]) == 2 else "global"
            by_scope.setdefault(scope, []).append(f"{signal['phenomenon']}:{signal['direction']}")
        conditions = []
        for scope, parts in by_scope.items():
            unique = sorted(set(parts))
            if len(unique) >= 2:
                conditions.append(f"{scope}:" + "+".join(unique))
        return sorted(conditions)

    def _update_condition_records(self, active_names: list[str], as_of: datetime) -> None:
        """Track condition start/end so mechanisms can reference the
        conditions under which they were discovered."""
        rows = self.db.query_dicts(
            "SELECT * FROM world_conditions w WHERE active AND version = "
            "(SELECT max(version) FROM world_conditions WHERE condition_id = w.condition_id)"
        )
        currently_active = {r["name"]: r for r in rows}
        for name in active_names:
            if name not in currently_active:
                self.db.execute(
                    "INSERT INTO world_conditions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        new_id("world_condition"),
                        1,
                        name,
                        name.split(":", 1)[0],
                        json.dumps(name.split(":", 1)[1].split("+")),
                        as_of,
                        None,
                        True,
                        timeutil.utcnow(),
                    ],
                )
                self.bus.emit(EventType.WORLD_CONDITION_CHANGED, source="environment", name=name, active=True)
        for name, row in currently_active.items():
            if name not in active_names:
                self.db.execute(
                    "INSERT INTO world_conditions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        row["condition_id"],
                        int(row["version"]) + 1,
                        row["name"],
                        row["scope"],
                        row["signals"],
                        row["started_at"],
                        as_of,
                        False,
                        timeutil.utcnow(),
                    ],
                )
                self.bus.emit(EventType.WORLD_CONDITION_CHANGED, source="environment", name=name, active=False)

    # --------------------------------------------------------------- queries
    def latest_state(self) -> dict[str, Any] | None:
        rows = self.db.query_dicts("SELECT * FROM world_states ORDER BY as_of DESC LIMIT 1")
        if not rows:
            return None
        row = rows[0]
        return {
            "state_id": row["state_id"],
            "as_of": timeutil.to_iso(timeutil.ensure_utc(row["as_of"])),
            "signals": json.loads(row["signals"]),
            "conditions": json.loads(row["conditions"]),
            "observation_count": row["observation_count"],
        }

    def state_at(self, as_of: datetime) -> dict[str, Any] | None:
        """Historical snapshot for replay (spec 8.23): nearest stored state
        at or before ``as_of``; rebuilt from observations if none stored."""
        rows = self.db.query_dicts(
            "SELECT * FROM world_states WHERE as_of <= ? ORDER BY as_of DESC LIMIT 1",
            [timeutil.ensure_utc(as_of)],
        )
        if rows:
            row = rows[0]
            return {
                "state_id": row["state_id"],
                "as_of": timeutil.to_iso(timeutil.ensure_utc(row["as_of"])),
                "signals": json.loads(row["signals"]),
                "conditions": json.loads(row["conditions"]),
                "observation_count": row["observation_count"],
            }
        return self.build(as_of=as_of, persist=False)

    def active_conditions(self) -> list[str]:
        rows = self.db.query(
            "SELECT DISTINCT name FROM world_conditions w WHERE active AND version = "
            "(SELECT max(version) FROM world_conditions WHERE condition_id = w.condition_id)"
        )
        return [r[0] for r in rows]
