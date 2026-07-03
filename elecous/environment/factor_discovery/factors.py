"""Environmental Factor formation and life (spec 4.10-4.26).

A factor is a validated combination of environmental signals repeatedly
preceding verified demand opportunities. Factors are versioned scientific
objects with timing intelligence, per-signal contribution, geographic
scopes, counterfactual results, discovery quality and a lifecycle. Nothing
is overwritten.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.common.vocabulary import Lifecycle, ValidationStatus
from elecous.environment.pattern_engine.mining import MinedPattern
from elecous.environment.schema import ENVIRONMENT_MIGRATION
from elecous.governance import constitution
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, global_databases

FACTOR_COLUMNS = 22


class FactorStore:
    def __init__(self, databases: DatabaseManager | None = None, bus: EventBus | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(ENVIRONMENT_MIGRATION)
        self.db = self.databases.get("environment")
        self.bus = bus or global_bus()
        self.log = get_logger("environment")

    # -------------------------------------------------------------- formation
    def form_from_pattern(
        self,
        pattern: MinedPattern,
        lead_times: dict[str, dict[str, float]],
        contribution: dict[str, float],
        regions: list[str],
    ) -> str:
        """Create a Candidate Environmental Factor from a mined pattern
        (spec 4.10). Idempotent per (category, signal set)."""
        existing = self.find_by_signals(pattern.category, list(pattern.signals))
        if existing is not None:
            return self._update_support(existing, pattern, lead_times, contribution)
        constitution.require_evidence("environmental_factor", list(pattern.supporting_episodes))
        factor_id = new_id("environmental_factor")
        timing = self._timing(pattern, lead_times)
        confidence = self._initial_confidence(pattern)
        name = " + ".join(k.rsplit(":", 1)[0] for k in pattern.signals) + f" → {pattern.category}"
        self.db.execute(
            f"INSERT INTO environmental_factors VALUES ({', '.join('?' * FACTOR_COLUMNS)})",
            [
                factor_id,
                1,
                name,
                json.dumps(sorted(pattern.signals)),
                json.dumps([pattern.category]),
                json.dumps(sorted(regions)),
                json.dumps(timing),
                json.dumps(confidence),
                confidence["current"],
                pattern.support,
                pattern.lift,
                json.dumps(contribution),
                json.dumps(list(pattern.supporting_episodes)),
                json.dumps([]),
                None,
                None,
                self._transferability(regions),
                Lifecycle.CANDIDATE.value,
                ValidationStatus.PENDING.value,
                None,
                None,
                timeutil.utcnow(),
            ],
        )
        self.bus.emit(
            EventType.FACTOR_DISCOVERED,
            source="environment",
            factor_id=factor_id,
            name=name,
            category=pattern.category,
            support=pattern.support,
            lift=pattern.lift,
        )
        self.log.info("environmental factor formed", factor_id=factor_id, name=name)
        return factor_id

    def _update_support(
        self,
        existing: dict[str, Any],
        pattern: MinedPattern,
        lead_times: dict[str, dict[str, float]],
        contribution: dict[str, float],
    ) -> str:
        """New supporting evidence for a known factor -> new version."""
        old_support = set(json.loads(existing["supporting_episodes"]))
        new_support = old_support | set(pattern.supporting_episodes)
        if new_support == old_support and abs(existing["lift"] - pattern.lift) < 0.05:
            return existing["factor_id"]
        updates = {
            "supporting_episodes": json.dumps(sorted(new_support)),
            "support": len(new_support),
            "lift": pattern.lift,
            "lead_time": json.dumps(self._timing(pattern, lead_times)),
            "contribution": json.dumps(contribution),
        }
        self.new_version(existing["factor_id"], updates, actor="pattern_miner")
        return existing["factor_id"]

    @staticmethod
    def _timing(pattern: MinedPattern, lead_times: dict[str, dict[str, float]]) -> dict[str, Any]:
        """Timing intelligence (spec 4.11): per-episode factor lead time is
        when the *last* signal of the combination locked in (min days-before
        across the factor's signals)."""
        per_episode: list[float] = []
        for episode_id in pattern.supporting_episodes:
            days = [
                lead_times.get(episode_id, {}).get(key)
                for key in pattern.signals
                if lead_times.get(episode_id, {}).get(key) is not None
            ]
            if days:
                per_episode.append(min(days))
        if not per_episode:
            return {"min": None, "avg": None, "max": None, "stddev": None, "episodes": 0}
        return {
            "min": round(min(per_episode), 1),
            "avg": round(sum(per_episode) / len(per_episode), 1),
            "max": round(max(per_episode), 1),
            "stddev": round(statistics.pstdev(per_episode), 2) if len(per_episode) > 1 else 0.0,
            "episodes": len(per_episode),
        }

    @staticmethod
    def _initial_confidence(pattern: MinedPattern) -> dict[str, float]:
        """Confidence components (spec 4.19). Initial values derive from
        support and lift; validation evolves them."""
        support_component = min(1.0, pattern.support / 10.0)
        lift_component = min(1.0, (pattern.lift - 1.0) / 4.0)
        base = round(0.5 * support_component + 0.5 * max(0.0, lift_component), 3)
        return {
            "historical": base,
            "recent": base,
            "regional": base,
            "category": base,
            "scientific": 0.0,  # earned only through the Validation Engine
            "current": base * 0.5,  # discounted until validated
        }

    @staticmethod
    def _transferability(regions: list[str]) -> float:
        """Geographic intelligence (4.20): more independent scopes -> more
        transferable, capped until proven elsewhere."""
        countries = [r for r in regions if len(r) == 2]
        return round(min(0.9, 0.2 + 0.15 * len(set(countries))), 3)

    # -------------------------------------------------------------- versions
    def latest(self, factor_id: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts(
            "SELECT * FROM environmental_factors WHERE factor_id = ? ORDER BY version DESC LIMIT 1", [factor_id]
        )
        return rows[0] if rows else None

    def find_by_signals(self, category: str, signals: list[str]) -> dict[str, Any] | None:
        target = json.dumps(sorted(signals))
        rows = self.db.query_dicts(
            "SELECT * FROM environmental_factors f WHERE signals = ? AND merged_into IS NULL AND version = "
            "(SELECT max(version) FROM environmental_factors WHERE factor_id = f.factor_id)",
            [target],
        )
        for row in rows:
            if category in json.loads(row["categories"]):
                return row
        return None

    def new_version(self, factor_id: str, updates: dict[str, Any], actor: str) -> int:
        current = self.latest(factor_id)
        if current is None:
            raise KeyError(factor_id)
        version = int(current["version"]) + 1
        constitution.require_append_only(int(current["version"]), version)
        row = dict(current)
        row.update(updates)
        self.db.execute(
            f"INSERT INTO environmental_factors VALUES ({', '.join('?' * FACTOR_COLUMNS)})",
            [
                row["factor_id"],
                version,
                row["name"],
                row["signals"],
                row["categories"],
                row["regions"],
                row["lead_time"],
                row["confidence"] if isinstance(row["confidence"], str) else json.dumps(row["confidence"]),
                row["current_confidence"],
                row["support"],
                row["lift"],
                row["contribution"] if isinstance(row["contribution"], str) else json.dumps(row["contribution"]),
                row["supporting_episodes"],
                row["contradictory_episodes"] if isinstance(row["contradictory_episodes"], str) else json.dumps(row["contradictory_episodes"]),
                row.get("counterfactual") if isinstance(row.get("counterfactual"), (str, type(None))) else json.dumps(row["counterfactual"]),
                row.get("discovery_quality") if isinstance(row.get("discovery_quality"), (str, type(None))) else json.dumps(row["discovery_quality"]),
                row["transferability"],
                row["lifecycle"],
                row["validation_status"],
                row.get("merged_into"),
                row.get("last_validation"),
                timeutil.utcnow(),
            ],
        )
        self.log.info("factor version appended", factor_id=factor_id, version=version, actor=actor)
        return version

    def promote(self, factor_id: str, target: Lifecycle, status: ValidationStatus, actor: str) -> None:
        current = self.latest(factor_id)
        if current is None:
            raise KeyError(factor_id)
        constitution.require_validated_promotion(Lifecycle(current["lifecycle"]), target, status)
        self.new_version(
            factor_id,
            {"lifecycle": target.value, "validation_status": status.value, "last_validation": timeutil.utcnow()},
            actor=actor,
        )
        if target in (Lifecycle.VALIDATED, Lifecycle.TRUSTED):
            self.bus.emit(EventType.FACTOR_PROMOTED, source="environment", factor_id=factor_id, lifecycle=target.value)

    # ---------------------------------------------------------------- queries
    def list_latest(self, lifecycle: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM environmental_factors f WHERE merged_into IS NULL AND version = "
            "(SELECT max(version) FROM environmental_factors WHERE factor_id = f.factor_id)"
        )
        params: list[Any] = []
        if lifecycle:
            sql += " AND lifecycle = ?"
            params.append(lifecycle)
        rows = self.db.query_dicts(sql + " ORDER BY current_confidence DESC", params)
        if category:
            rows = [r for r in rows if category in json.loads(r["categories"])]
        return rows

    # ------------------------------------------------------- merge and split
    def merge_similar(self, jaccard_threshold: float = 0.8) -> int:
        """Similarity Engine (spec 4.14): highly similar factors of the same
        category merge; the union factor keeps the combined evidence."""
        merged = 0
        factors = self.list_latest()
        for i, a in enumerate(factors):
            if a.get("merged_into"):
                continue
            for b in factors[i + 1 :]:
                if b.get("merged_into"):
                    continue
                cats_a, cats_b = set(json.loads(a["categories"])), set(json.loads(b["categories"]))
                if not cats_a & cats_b:
                    continue
                sig_a, sig_b = set(json.loads(a["signals"])), set(json.loads(b["signals"]))
                jaccard = len(sig_a & sig_b) / len(sig_a | sig_b)
                if jaccard < jaccard_threshold:
                    continue
                keeper, absorbed = (a, b) if a["support"] >= b["support"] else (b, a)
                support = sorted(
                    set(json.loads(keeper["supporting_episodes"])) | set(json.loads(absorbed["supporting_episodes"]))
                )
                self.new_version(
                    keeper["factor_id"],
                    {"supporting_episodes": json.dumps(support), "support": len(support)},
                    actor="similarity_engine",
                )
                self.new_version(
                    absorbed["factor_id"],
                    {"merged_into": keeper["factor_id"], "lifecycle": Lifecycle.ARCHIVED.value},
                    actor="similarity_engine",
                )
                merged += 1
        return merged

    def split_overbroad(self) -> int:
        """Split factors spanning categories with divergent timing (4.14):
        per-category factors inherit the shared signal set."""
        split = 0
        for factor in self.list_latest():
            categories = json.loads(factor["categories"])
            if len(categories) <= 1:
                continue
            timing = json.loads(factor["lead_time"])
            if not timing.get("stddev") or not timing.get("avg"):
                continue
            if timing["stddev"] > max(2.0, timing["avg"]):  # timing incoherent across uses
                for category in categories:
                    self.db.execute(
                        "UPDATE environmental_factors SET categories = ? WHERE factor_id = ? AND version = ?",
                        [json.dumps([categories[0]]), factor["factor_id"], factor["version"]],
                    )
                split += 1
        return split

    def record_contradiction(self, factor_id: str, episode_id: str) -> None:
        """A negative episode (4.18) where the factor was present without a
        spike becomes contradictory evidence."""
        current = self.latest(factor_id)
        if current is None:
            return
        contradictory = set(json.loads(current["contradictory_episodes"]))
        if episode_id in contradictory:
            return
        contradictory.add(episode_id)
        confidence = json.loads(current["confidence"])
        support = int(current["support"])
        ratio = support / (support + len(contradictory))
        confidence["recent"] = round(confidence["recent"] * ratio, 3)
        confidence["current"] = round(confidence["current"] * ratio, 3)
        self.new_version(
            factor_id,
            {
                "contradictory_episodes": json.dumps(sorted(contradictory)),
                "confidence": json.dumps(confidence),
                "current_confidence": confidence["current"],
            },
            actor="negative_learning",
        )
