"""Per-episode environmental signal discovery (spec 4.5).

Whenever a Research Episode exists, ELECOUS examines every observation
preceding the demand opportunity: what changed, when, where, how rapidly,
was it unusual? Discovered changes are cached per episode (incremental
processing) and sighted into the Signal Library as candidates.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.environment.schema import ENVIRONMENT_MIGRATION
from elecous.environment.signal_discovery.extractor import EnvironmentalSignalExtractor, SignalChange
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.signals.library import SignalLibrary
from elecous.storage.database import DatabaseManager, global_databases


class EpisodeSignalDiscovery:
    def __init__(
        self,
        extractor: EnvironmentalSignalExtractor,
        library: SignalLibrary,
        databases: DatabaseManager | None = None,
        config: Config | None = None,
    ):
        self.extractor = extractor
        self.library = library
        self.databases = databases or global_databases()
        self.databases.register_schema(ENVIRONMENT_MIGRATION)
        self.db = self.databases.get("environment")
        self.config = config or active_config()
        self.log = get_logger("environment")

    def discover_for_episode(self, episode: dict[str, Any]) -> list[SignalChange]:
        """Extract and cache the environmental context of one episode."""
        episode_id = episode["episode_id"]
        cached = self.db.query_dicts("SELECT * FROM episode_signals WHERE episode_id = ?", [episode_id])
        if cached:
            return [self._from_row(r) for r in cached]
        spike_start = timeutil.ensure_utc(episode["spike_start"])
        changes = self.extractor.changes(window_end=spike_start)
        for change in changes:
            days_before = (
                (spike_start - change.first_crossed_at).total_seconds() / 86400.0
                if change.first_crossed_at
                else None
            )
            self.db.execute(
                "INSERT INTO episode_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    episode_id,
                    change.signal_key,
                    change.phenomenon,
                    change.direction,
                    change.scope,
                    change.z,
                    change.first_crossed_at,
                    days_before,
                    json.dumps(list(change.observation_types)),
                    timeutil.utcnow(),
                ],
            )
            self.library.observe_candidate(
                change.signal_key,
                signal_kind="environmental",
                observation_capability=change.observation_types[0],
                category=episode.get("category"),
                country=change.scope if len(change.scope) == 2 else None,
            )
        self.log.info("episode signals discovered", episode_id=episode_id, signals=len(changes))
        return changes

    def episode_signal_sets(self, episode_ids: list[str]) -> dict[str, set[str]]:
        if not episode_ids:
            return {}
        placeholders = ",".join("?" * len(episode_ids))
        rows = self.db.query(
            f"SELECT episode_id, signal_key FROM episode_signals WHERE episode_id IN ({placeholders})",
            episode_ids,
        )
        sets: dict[str, set[str]] = {e: set() for e in episode_ids}
        for episode_id, key in rows:
            sets[episode_id].add(key)
        return sets

    @staticmethod
    def phenomenon_of(signal_key: str) -> str:
        return ":".join(signal_key.split(":")[:2])

    def episode_phenomenon_sets(self, episode_ids: list[str]) -> dict[str, set[str]]:
        """Collapse scoped signal keys (phenomenon:direction:scope) to
        phenomenon:direction for pattern mining. A factor is a combination
        of PHENOMENA (spec 4.3); geography is factor metadata (spec 4.20),
        not a mining dimension. Without this collapse, one real global
        weather event appearing in N tracked countries is treated as N
        independent combinatorial items, producing a combinatorial
        explosion of near-duplicate per-country patterns that carry no
        additional scientific information."""
        scoped = self.episode_signal_sets(episode_ids)
        return {eid: {self.phenomenon_of(k) for k in keys} for eid, keys in scoped.items()}

    def scoped_keys_for_phenomena(self, episode_ids: list[str], phenomena: tuple[str, ...]) -> dict[str, list[str]]:
        """phenomenon:direction -> concrete scoped signal keys that actually
        satisfied it across the given episodes, for factor metadata
        (regions, contribution, lead time) once mining has already decided
        the phenomenon-level combination is worth keeping."""
        scoped = self.episode_signal_sets(episode_ids)
        wanted = set(phenomena)
        result: dict[str, set[str]] = {p: set() for p in phenomena}
        for keys in scoped.values():
            for key in keys:
                phenomenon = self.phenomenon_of(key)
                if phenomenon in wanted:
                    result[phenomenon].add(key)
        return {p: sorted(keys) for p, keys in result.items()}

    def episode_lead_times(self, episode_ids: list[str]) -> dict[str, dict[str, float]]:
        """episode -> signal_key -> days before spike."""
        if not episode_ids:
            return {}
        placeholders = ",".join("?" * len(episode_ids))
        rows = self.db.query(
            f"SELECT episode_id, signal_key, days_before_spike FROM episode_signals WHERE episode_id IN ({placeholders})",
            episode_ids,
        )
        out: dict[str, dict[str, float]] = {}
        for episode_id, key, days in rows:
            if days is not None:
                out.setdefault(episode_id, {})[key] = float(days)
        return out

    # ------------------------------------------------------ ordinary windows
    def record_ordinary_window(self, category: str, window_end: datetime) -> str:
        """Sample the world during an ordinary (non-spike) period — the base
        rate against which pattern lift is computed (spec 4.9)."""
        changes = self.extractor.changes(window_end=window_end)
        window_id = new_id("task")
        self.db.execute(
            "INSERT INTO ordinary_windows VALUES (?, ?, ?, ?, ?)",
            [
                window_id,
                category,
                timeutil.ensure_utc(window_end),
                json.dumps(sorted(c.signal_key for c in changes)),
                timeutil.utcnow(),
            ],
        )
        return window_id

    def ordinary_signal_sets(self, category: str | None = None) -> list[set[str]]:
        sql, params = "SELECT signal_keys FROM ordinary_windows", []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        return [set(json.loads(r[0])) for r in self.db.query(sql, params)]

    def ordinary_phenomenon_sets(self, category: str | None = None) -> list[set[str]]:
        """Same phenomenon-level collapse as episode_phenomenon_sets,
        applied to ordinary-window samples so the base rate is computed on
        the same dimension the miner searches (spec 4.9)."""
        return [{self.phenomenon_of(k) for k in s} for s in self.ordinary_signal_sets(category)]

    @staticmethod
    def _from_row(row: dict[str, Any]) -> SignalChange:
        return SignalChange(
            signal_key=row["signal_key"],
            phenomenon=row["phenomenon"],
            direction=row["direction"],
            scope=row["scope"],
            z=row["z"],
            first_crossed_at=timeutil.ensure_utc(row["first_crossed_at"]) if row["first_crossed_at"] else None,
            observation_types=tuple(json.loads(row["observation_types"])),
        )
