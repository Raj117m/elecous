"""Interaction Discovery (spec 4.16).

Some signals matter only together (heat alone → nothing; heat • school
holiday → cooling products). ELECOUS specifically measures synergy: the lift
of a pair versus the best lift of its members. Interactions with high
synergy are prioritized as factor candidates.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.environment.schema import ENVIRONMENT_MIGRATION
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, global_databases


class InteractionDiscovery:
    def __init__(self, databases: DatabaseManager | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(ENVIRONMENT_MIGRATION)
        self.db = self.databases.get("environment")
        self.log = get_logger("environment")

    def discover(
        self,
        category: str,
        episode_signals: dict[str, set[str]],
        ordinary_sets: list[set[str]],
        min_support: int = 3,
    ) -> list[dict[str, Any]]:
        episodes = list(episode_signals.values())
        n_episodes = len(episodes)
        n_ordinary = len(ordinary_sets)
        if n_episodes < min_support:
            return []

        def lift(keys: tuple[str, ...]) -> tuple[float, int]:
            support = sum(1 for s in episodes if set(keys) <= s)
            rate = support / n_episodes
            if n_ordinary:
                base = sum(1 for s in ordinary_sets if set(keys) <= s) / n_ordinary
                base = max(base, 1.0 / (2 * n_ordinary))
            else:
                base = 0.5
            return rate / base, support

        singles = sorted({k for s in episodes for k in s})
        results = []
        for a, b in combinations(singles, 2):
            joint_lift, support = lift((a, b))
            if support < min_support:
                continue
            lift_a, _ = lift((a,))
            lift_b, _ = lift((b,))
            synergy = joint_lift / max(lift_a, lift_b, 1e-9)
            if synergy <= 1.2:  # pair adds nothing beyond its parts
                continue
            row = {
                "interaction_id": new_id("task"),
                "signal_a": a,
                "signal_b": b,
                "category": category,
                "joint_lift": round(joint_lift, 3),
                "individual_lift_a": round(lift_a, 3),
                "individual_lift_b": round(lift_b, 3),
                "synergy": round(synergy, 3),
                "support": support,
            }
            self.db.execute(
                "INSERT INTO signal_interactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [*row.values(), timeutil.utcnow()],
            )
            results.append(row)
        results.sort(key=lambda r: -r["synergy"])
        if results:
            self.log.info("interactions discovered", category=category, count=len(results))
        return results
