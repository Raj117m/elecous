"""Knowledge Engine facade (spec ch. 7).

Owns the permanent scientific memory: syncs the graph from the authoritative
stores, snapshots World State, records failures/unknowns, ages confidence,
scores quality/information gain, and runs self-healing integrity scans. It
never predicts (7.31) — the Prediction Engine reads this library.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.vocabulary import Relationship
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.logging import get_logger
from elecous.knowledge.governance import governance
from elecous.knowledge.graph.graph import KnowledgeGraph
from elecous.knowledge.memory.memory import KnowledgeMemory
from elecous.knowledge.revision.revision import KnowledgeRevisionEngine
from elecous.storage.database import DatabaseManager, global_databases


class KnowledgeEngine:
    def __init__(
        self,
        signals,
        factors,
        mechanisms,
        episodes,
        world,
        observations,
        config: Config | None = None,
        databases: DatabaseManager | None = None,
        bus: EventBus | None = None,
    ):
        self.config = config or active_config()
        self.databases = databases or global_databases()
        self.bus = bus or global_bus()
        self.log = get_logger("knowledge")

        self.signals = signals
        self.factors = factors
        self.mechanisms = mechanisms
        self.episodes = episodes
        self.world = world
        self.observations = observations

        self.graph = KnowledgeGraph(self.databases)
        self.memory = KnowledgeMemory(self.databases)
        self.revision = KnowledgeRevisionEngine(self.graph, self.databases)

    # -------------------------------------------------------------- sync
    def sync(self) -> dict[str, int]:
        """Rebuild the graph and emit KNOWLEDGE_UPDATED (spec 7.5, 10.18)."""
        for factor in self.factors.list_latest():
            governance.require_traceable_node(
                "environmental_factor", factor["factor_id"], json.loads(factor["supporting_episodes"])
            )
        for mechanism in self.mechanisms.list_latest():
            governance.require_traceable_node(
                "mechanism", mechanism["mechanism_id"], json.loads(mechanism["supporting_episodes"])
            )
        counts = self.graph.rebuild_from_stores(self.signals, self.factors, self.mechanisms)
        self.bus.emit(EventType.KNOWLEDGE_UPDATED, source="knowledge", **counts)
        return counts

    # ----------------------------------------------------- world snapshot
    def snapshot_current_world(self) -> str:
        state = self.world.latest_state() or self.world.build()
        active_conditions = set(state["conditions"])
        active_mechanisms = []
        for mechanism in self.mechanisms.list_latest():
            conditions = set(json.loads(mechanism.get("world_conditions", "[]")) if isinstance(mechanism.get("world_conditions"), str) else mechanism.get("world_conditions", []))
            if conditions & active_conditions:
                active_mechanisms.append(mechanism["mechanism_id"])
        active_factors = [
            f["factor_id"]
            for f in self.factors.list_latest()
            if set(json.loads(f["signals"])) & {s["signal_key"] for s in state["signals"]}
        ]
        return self.memory.snapshot_world_state(state, active_mechanisms, active_factors)

    # -------------------------------------------------------------- aging
    def apply_half_life(self) -> int:
        """Knowledge Half-Life (spec 7.10): discount confidence for objects
        without recent supporting evidence."""
        aged = 0
        for factor in self.factors.list_latest():
            episodes = json.loads(factor["supporting_episodes"])
            if not episodes:
                continue
            latest_episode = max(
                (self.episodes.latest(e) for e in episodes if self.episodes.latest(e)),
                key=lambda e: timeutil.ensure_utc(e["spike_start"]),
                default=None,
            )
            if latest_episode is None:
                continue
            node = self.graph.node_for("environmental_factor", factor["factor_id"])
            if node:
                self.revision.apply_aging(node["node_id"], latest_episode["spike_start"])
                aged += 1
        return aged

    # ------------------------------------------------------------ quality
    def score_quality(self, object_type: str, object_id: str, novelty: float, replication: int, business_value: float) -> dict[str, float]:
        """Knowledge Quality (spec 7.20)."""
        quality = {
            "evidence_quality": round(min(1.0, replication / 5.0), 3),
            "replication": replication,
            "generalization": round(novelty, 3),
            "novelty": round(novelty, 3),
            "business_value": round(business_value, 3),
            "prediction_value": round(0.5 * replication / 5.0 + 0.5 * business_value, 3),
        }
        node = self.graph.node_for(object_type, object_id)
        if node:
            self.graph.upsert_node(
                object_type, object_id, node["label"], node["confidence"], node["integrity"], quality=quality
            )
        return quality

    def information_gain(self, new_facts: set[str], known_facts: set[str]) -> float:
        """Information Gain (spec 7.21): does this discovery teach anything
        not already known? Simple set-difference ratio — deliberately
        minimal per Appendix A Principle 4 (scientific minimalism)."""
        if not new_facts:
            return 0.0
        unknown = new_facts - known_facts
        return round(len(unknown) / len(new_facts), 4)

    # -------------------------------------------------------- self-healing
    def self_heal(self) -> dict[str, Any]:
        return self.revision.integrity_scan()

    # ------------------------------------------------------- failure/unknown
    def record_failure(self, object_type: str, object_id: str, summary: str, detail: dict[str, Any]) -> str:
        return self.memory.record_failure(object_type, object_id, summary, detail)

    def record_unknown(self, kind: str, description: str, context: dict[str, Any], research_priority: float = 0.5) -> str:
        unknown_id = self.memory.record_unknown(kind, description, context, research_priority)
        self.bus.emit(EventType.UNKNOWN_RECORDED, source="knowledge", kind=kind, description=description)
        return unknown_id

    def research_agenda(self) -> list[dict[str, Any]]:
        return self.revision.rank_research_priorities(self.memory.open_unknowns())

    # ------------------------------------------------------------ compression
    def compression_report(self) -> dict[str, Any]:
        observation_count = self.observations.count()
        mechanism_count = len(self.mechanisms.list_latest())
        factor_count = len(self.factors.list_latest())
        return {
            "observations": observation_count,
            "mechanisms": mechanism_count,
            "factors": factor_count,
            "observations_per_mechanism": self.revision.compression_ratio(observation_count, mechanism_count),
            "observations_per_factor": self.revision.compression_ratio(observation_count, factor_count),
        }
