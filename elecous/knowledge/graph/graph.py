"""The Knowledge Graph (spec 7.5, 7.15).

ELECOUS does not store isolated facts — it stores relationships. Every
validated signal, factor, mechanism and episode becomes a node; typed edges
(supports, contradicts, depends_on, extends, specializes, generalizes,
replaces, derived_from, observed_by, member_of) connect them. The graph is
rebuilt incrementally from the authoritative stores (signals, factors,
mechanisms, episodes) rather than duplicating their content.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.common.vocabulary import Relationship
from elecous.infrastructure.logging import get_logger
from elecous.knowledge.schema import KNOWLEDGE_MIGRATION
from elecous.storage.database import DatabaseManager, global_databases


class KnowledgeGraph:
    def __init__(self, databases: DatabaseManager | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(KNOWLEDGE_MIGRATION)
        self.db = self.databases.get("knowledge")
        self.log = get_logger("knowledge")

    # --------------------------------------------------------------- nodes
    def upsert_node(
        self,
        object_type: str,
        object_ref: str,
        label: str,
        confidence: float,
        integrity: float = 1.0,
        quality: dict[str, Any] | None = None,
        half_life_days: float | None = None,
    ) -> str:
        existing = self.db.query_one(
            "SELECT node_id FROM knowledge_nodes WHERE object_type = ? AND object_ref = ?",
            [object_type, object_ref],
        )
        now = timeutil.utcnow()
        if existing:
            node_id = existing[0]
            self.db.execute(
                "UPDATE knowledge_nodes SET label = ?, confidence = ?, integrity = ?, quality = ?, "
                "half_life_days = ?, updated_at = ? WHERE node_id = ?",
                [label, confidence, integrity, json.dumps(quality) if quality else None, half_life_days, now, node_id],
            )
            return node_id
        node_id = new_id("knowledge")
        self.db.execute(
            "INSERT INTO knowledge_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                node_id,
                object_type,
                object_ref,
                label,
                confidence,
                integrity,
                json.dumps(quality) if quality else None,
                half_life_days,
                now,
                now,
            ],
        )
        return node_id

    def node_for(self, object_type: str, object_ref: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts(
            "SELECT * FROM knowledge_nodes WHERE object_type = ? AND object_ref = ?", [object_type, object_ref]
        )
        return rows[0] if rows else None

    # --------------------------------------------------------------- edges
    def link(self, source_node: str, target_node: str, relationship: Relationship, weight: float = 1.0) -> str:
        existing = self.db.query_one(
            "SELECT edge_id FROM knowledge_edges WHERE source_node = ? AND target_node = ? AND relationship = ?",
            [source_node, target_node, relationship.value],
        )
        if existing:
            self.db.execute("UPDATE knowledge_edges SET weight = ? WHERE edge_id = ?", [weight, existing[0]])
            return existing[0]
        edge_id = new_id("knowledge")
        self.db.execute(
            "INSERT INTO knowledge_edges VALUES (?, ?, ?, ?, ?, ?)",
            [edge_id, source_node, target_node, relationship.value, weight, timeutil.utcnow()],
        )
        return edge_id

    def neighbors(self, node_id: str, relationship: Relationship | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM knowledge_edges WHERE source_node = ?"
        params: list[Any] = [node_id]
        if relationship:
            sql += " AND relationship = ?"
            params.append(relationship.value)
        return self.db.query_dicts(sql, params)

    def incoming(self, node_id: str, relationship: Relationship | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM knowledge_edges WHERE target_node = ?"
        params: list[Any] = [node_id]
        if relationship:
            sql += " AND relationship = ?"
            params.append(relationship.value)
        return self.db.query_dicts(sql, params)

    # ---------------------------------------------------------------- sync
    def sync_signal(self, signal_row: dict[str, Any]) -> str:
        return self.upsert_node(
            "signal",
            signal_row["signal_id"],
            signal_row["signal_key"],
            confidence=float(signal_row["reliability"]),
            integrity=1.0,
        )

    def sync_factor(self, factor_row: dict[str, Any], signal_nodes: dict[str, str]) -> str:
        node_id = self.upsert_node(
            "environmental_factor",
            factor_row["factor_id"],
            factor_row["name"],
            confidence=float(factor_row["current_confidence"]),
            quality=json.loads(factor_row["discovery_quality"]) if factor_row.get("discovery_quality") else None,
        )
        for signal_key in json.loads(factor_row["signals"]):
            signal_node = signal_nodes.get(signal_key)
            if signal_node:
                self.link(node_id, signal_node, Relationship.DEPENDS_ON)
        for episode_id in json.loads(factor_row["supporting_episodes"]):
            episode_node = self.upsert_node("research_episode", episode_id, episode_id, confidence=1.0)
            self.link(node_id, episode_node, Relationship.SUPPORTS)
        for episode_id in json.loads(factor_row["contradictory_episodes"]):
            episode_node = self.upsert_node("research_episode", episode_id, episode_id, confidence=1.0)
            self.link(node_id, episode_node, Relationship.CONTRADICTS)
        return node_id

    def sync_mechanism(self, mechanism_row: dict[str, Any], factor_nodes: dict[str, str]) -> str:
        node_id = self.upsert_node(
            "mechanism",
            mechanism_row["mechanism_id"],
            mechanism_row["name"],
            confidence=float(mechanism_row["current_confidence"]),
        )
        for factor_id in json.loads(mechanism_row["environmental_factors"]):
            factor_node = factor_nodes.get(factor_id)
            if factor_node:
                self.link(node_id, factor_node, Relationship.DEPENDS_ON)
        parent = mechanism_row.get("parent_mechanism")
        if parent:
            parent_node = self.node_for("mechanism", parent)
            if parent_node:
                self.link(node_id, parent_node["node_id"], Relationship.SPECIALIZES)
                self.link(parent_node["node_id"], node_id, Relationship.GENERALIZES)
        return node_id

    def sync_product(self, entity: str, category: str, episode_ids: list[str]) -> str:
        """Product node (Phase 4 evidence chain: ...Demand Signals -> Products
        -> Historical Episodes...). A product is the real catalog entity an
        episode is about; linking it to every episode it appears in lets the
        graph answer "what evidence exists about this product" directly."""
        node_id = self.upsert_node("product", entity, entity, confidence=1.0)
        for episode_id in episode_ids:
            episode_node = self.upsert_node("research_episode", episode_id, episode_id, confidence=1.0)
            self.link(node_id, episode_node, Relationship.MEMBER_OF)
        return node_id

    def sync_validation(self, validation_row: dict[str, Any]) -> str:
        """Validation Evidence node (Phase 4 chain: ...Historical Episodes ->
        Validation Evidence). Links the validation record to the exact
        factor/mechanism/signal it judged, with the real outcome as the
        relationship — supports on pass, contradicts on fail — so the graph
        carries the validation history, not just the current confidence."""
        label = f"{validation_row['method']}:{validation_row['outcome']}"
        node_id = self.upsert_node(
            "validation", validation_row["validation_id"], label,
            confidence=float(validation_row["confidence"]) if validation_row.get("confidence") is not None else 0.0,
        )
        target = self.node_for(validation_row["object_type"], validation_row["object_id"])
        if target:
            relationship = Relationship.SUPPORTS if validation_row["outcome"] == "passed" else Relationship.CONTRADICTS
            self.link(node_id, target["node_id"], relationship)
        return node_id

    def rebuild_from_stores(
        self,
        signal_library,
        factor_store,
        mechanism_store,
        episode_store=None,
        validation_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Incremental sync (spec 10.19): re-derives nodes/edges from the
        authoritative versioned stores. Cheap because each store already
        holds only the latest version per object. ``episode_store`` and
        ``validation_rows`` are optional so existing callers that only need
        signal/factor/mechanism sync are unaffected."""
        signal_nodes = {s["signal_key"]: self.sync_signal(s) for s in signal_library.list_latest()}
        factor_nodes = {}
        for factor in factor_store.list_latest():
            factor_nodes[factor["factor_id"]] = self.sync_factor(factor, signal_nodes)
        mechanism_count = 0
        for mechanism in mechanism_store.list_latest():
            self.sync_mechanism(mechanism, factor_nodes)
            mechanism_count += 1

        product_count = 0
        if episode_store is not None:
            episodes = episode_store.list_latest(include_controls=False)
            by_entity: dict[tuple[str, str], list[str]] = {}
            for episode in episodes:
                by_entity.setdefault((episode["entity"], episode["category"]), []).append(episode["episode_id"])
            for (entity, category), episode_ids in by_entity.items():
                self.sync_product(entity, category, episode_ids)
                product_count += 1

        validation_count = 0
        if validation_rows:
            for row in validation_rows:
                self.sync_validation(row)
                validation_count += 1

        return {
            "signals": len(signal_nodes),
            "factors": len(factor_nodes),
            "mechanisms": mechanism_count,
            "products": product_count,
            "validations": validation_count,
        }

    # -------------------------------------------------------------- search
    def search(self, object_type: str | None = None, min_confidence: float = 0.0) -> list[dict[str, Any]]:
        """Knowledge Search (spec 7.23): filterable node listing."""
        sql = "SELECT * FROM knowledge_nodes WHERE confidence >= ?"
        params: list[Any] = [min_confidence]
        if object_type:
            sql += " AND object_type = ?"
            params.append(object_type)
        return self.db.query_dicts(sql + " ORDER BY confidence DESC", params)
