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
from elecous.knowledge.schema import KNOWLEDGE_MIGRATION, KNOWLEDGE_MIGRATION_0002, KNOWLEDGE_MIGRATION_0003, KNOWLEDGE_MIGRATION_0004
from elecous.storage.database import DatabaseManager, global_databases


class KnowledgeGraph:
    def __init__(self, databases: DatabaseManager | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(KNOWLEDGE_MIGRATION)
        self.databases.register_schema(KNOWLEDGE_MIGRATION_0002)
        self.databases.register_schema(KNOWLEDGE_MIGRATION_0003)
        self.databases.register_schema(KNOWLEDGE_MIGRATION_0004)
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
        memory_tier: str = "short_term",
        lifecycle_stage: str = "hypothesis",
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
                "half_life_days = ?, updated_at = ?, memory_tier = ?, lifecycle_stage = ? WHERE node_id = ?",
                [label, confidence, integrity, json.dumps(quality) if quality else None, half_life_days, now, memory_tier, lifecycle_stage, node_id],
            )
            return node_id
        node_id = new_id("knowledge")
        self.db.execute(
            "INSERT INTO knowledge_nodes (node_id, object_type, object_ref, label, confidence, integrity, quality, half_life_days, created_at, updated_at, memory_tier, lifecycle_stage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                memory_tier,
                lifecycle_stage,
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
        now = timeutil.utcnow()
        if existing:
            self.db.execute("UPDATE knowledge_edges SET weight = ?, last_seen_at = ? WHERE edge_id = ?", [weight, now, existing[0]])
            return existing[0]
        edge_id = new_id("knowledge")
        self.db.execute(
            "INSERT INTO knowledge_edges (edge_id, source_node, target_node, relationship, weight, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [edge_id, source_node, target_node, relationship.value, weight, now, now],
        )
        return edge_id

    def compress(self) -> dict[str, int]:
        """Continuously compresses state, removing inactive relationships that
        span more than 5 years (spec 7.21)."""
        edges_removed = self.db.execute(
            "DELETE FROM knowledge_edges WHERE last_seen_at < now() - INTERVAL 5 YEAR"
        )
        nodes_removed = self.db.execute(
            "DELETE FROM knowledge_nodes WHERE node_id NOT IN (SELECT source_node FROM knowledge_edges) "
            "AND node_id NOT IN (SELECT target_node FROM knowledge_edges)"
        )
        if edges_removed > 0 or nodes_removed > 0:
            self.log.info("knowledge graph compressed", edges_removed=edges_removed, nodes_removed=nodes_removed)
        return {"edges_removed": edges_removed, "nodes_removed": nodes_removed}

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

    def sync_source(self, source_row: dict[str, Any]) -> str:
        """Source node (rewrite directive, Level 2 source intelligence).
        ``ObjectType.SOURCE`` has existed in the vocabulary since spec 2.5,
        but no code path ever created a graph node for it -- sources were
        tracked only in the registry, invisible to the graph that otherwise
        records every other object's relationships. confidence=health keeps
        this consistent with how every other node's confidence is derived
        from its own store's evidence, not asserted."""
        return self.upsert_node(
            "source", source_row["source_id"], source_row["name"], confidence=float(source_row["health"])
        )

    def sync_discovered_source(self, discovered_row: dict[str, Any], seed_node_id: str | None = None) -> str:
        """Source Discovery Engine node (Final Core Rewrite, Principle 5):
        a real candidate source becomes a real graph node the moment it has
        been evaluated with real metadata -- confidence is the same
        quality_score computed from real HTTP headers, never asserted.
        DERIVED_FROM links it back to the known organization it was found
        on, so the graph records *how* it was discovered, not just that it
        was. Distinct from sync_source: this node has no registry entry
        (no connector exists for it yet) -- object_ref is the URL itself."""
        node_id = self.upsert_node(
            "source", discovered_row["url"], discovered_row["url"],
            confidence=float(discovered_row.get("quality_score") or 0.0),
            quality={"kind": discovered_row["kind"], "trigger_reason": discovered_row["trigger_reason"], "status": discovered_row["status"]},
        )
        if seed_node_id:
            self.link(node_id, seed_node_id, Relationship.DERIVED_FROM)
        return node_id

    def sync_research_question(self, question_row: dict[str, Any]) -> str:
        """Research Question node (rewrite directive, Level 2 scientific
        memory). Links OBSERVED_BY to the source nodes named in its evidence
        plan (already-defined-but-previously-unused relationship, spec
        7.15) and DEPENDS_ON to its subject (the factor/mechanism it targets,
        if any) -- reusing existing vocabulary rather than inventing new
        edge types for what the graph already has a name for."""
        node_id = self.upsert_node(
            "research_question",
            question_row["question_id"],
            question_row["statement"],
            confidence=float(question_row.get("priority") or 0.0),
        )
        if question_row.get("subject_type") and question_row.get("subject_ref"):
            subject = self.node_for(question_row["subject_type"], question_row["subject_ref"])
            if subject:
                self.link(node_id, subject["node_id"], Relationship.DEPENDS_ON)
        for source_id in question_row.get("expected_evidence", {}).get("sources", []):
            source_node = self.node_for("source", source_id)
            if source_node:
                self.link(node_id, source_node["node_id"], Relationship.OBSERVED_BY)
        return node_id

    def rebuild_from_stores(
        self,
        signal_library,
        factor_store,
        mechanism_store,
        episode_store=None,
        validation_rows: list[dict[str, Any]] | None = None,
        source_rows: list[dict[str, Any]] | None = None,
        research_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Incremental sync (spec 10.19): re-derives nodes/edges from the
        authoritative versioned stores. Cheap because each store already
        holds only the latest version per object. ``episode_store``,
        ``validation_rows``, ``source_rows`` and ``research_rows`` are all
        optional so existing callers that only need signal/factor/mechanism
        sync are unaffected (same additive pattern used to add
        ``episode_store``/``validation_rows`` previously)."""
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

        source_count = 0
        if source_rows:
            for row in source_rows:
                self.sync_source(row)
                source_count += 1

        research_count = 0
        if research_rows:
            # Sources must be synced first (above) so OBSERVED_BY edges have
            # a target node to link to.
            for row in research_rows:
                self.sync_research_question(row)
                research_count += 1

        return {
            "signals": len(signal_nodes),
            "factors": len(factor_nodes),
            "mechanisms": mechanism_count,
            "products": product_count,
            "validations": validation_count,
            "sources": source_count,
            "research_questions": research_count,
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
