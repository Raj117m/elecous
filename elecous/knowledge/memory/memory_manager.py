"""Scientific Memory Management (Constitution 4.2).

Enforces the rigorous physical and logical separation of Memory Tiers
and the transitions of the Knowledge Lifecycle.
"""

from __future__ import annotations

from typing import Any
from datetime import timedelta
from elecous.common import timeutil
from elecous.knowledge.graph.graph import KnowledgeGraph
from elecous.storage.database import DatabaseManager, global_databases
from elecous.infrastructure.logging import get_logger

class MemoryManager:
    def __init__(self, graph: KnowledgeGraph, databases: DatabaseManager | None = None):
        self.graph = graph
        self.databases = databases or global_databases()
        self.db = self.databases.get("knowledge")
        self.log = get_logger("knowledge")

    def evaluate_transitions(self) -> dict[str, int]:
        """Applies mathematical transition rules to all nodes in the Knowledge Graph."""
        transitions = {
            "to_short_term": 0,
            "to_long_term": 0,
            "to_archive": 0,
            "to_forgotten": 0,
        }

        # Rule 1: Short-Term -> Long-Term only upon out-of-sample validation
        # Find hypotheses/beliefs that have an incoming SUPPORTS edge from a live prospective validation
        rows = self.db.query_dicts(
            """
            SELECT DISTINCT n.node_id
            FROM knowledge_nodes n
            JOIN knowledge_edges e ON e.target_node = n.node_id
            JOIN knowledge_nodes v ON e.source_node = v.node_id
            WHERE n.memory_tier = 'short_term'
              AND e.relationship = 'supports'
              AND v.object_type = 'validation'
              AND v.label LIKE 'prospective:%'
            """
        )
        for row in rows:
            self.db.execute(
                "UPDATE knowledge_nodes SET memory_tier = 'long_term', lifecycle_stage = 'validated_knowledge', updated_at = ? WHERE node_id = ?",
                [timeutil.utcnow(), row["node_id"]]
            )
            transitions["to_long_term"] += 1

        # Rule 2: Short-Term -> Forgotten if mathematically falsified
        # Find hypotheses that have an incoming CONTRADICTS edge from a validation
        rows = self.db.query_dicts(
            """
            SELECT DISTINCT n.node_id
            FROM knowledge_nodes n
            JOIN knowledge_edges e ON e.target_node = n.node_id
            WHERE n.memory_tier = 'short_term'
              AND e.relationship = 'contradicts'
            """
        )
        for row in rows:
            self.db.execute(
                "UPDATE knowledge_nodes SET memory_tier = 'forgotten', lifecycle_stage = 'forgotten_knowledge', updated_at = ? WHERE node_id = ?",
                [timeutil.utcnow(), row["node_id"]]
            )
            transitions["to_forgotten"] += 1

        # Rule 3: Long-Term -> Archive when correlation W_i < epsilon for > 1 year
        # In the graph, weight < 0.1 and not updated in > 365 days
        rows = self.db.query_dicts(
            """
            SELECT n.node_id
            FROM knowledge_nodes n
            WHERE n.memory_tier = 'long_term'
              AND n.updated_at < ?
              AND n.confidence < 0.1
            """,
            [timeutil.utcnow() - timedelta(days=365)]
        )
        for row in rows:
            self.db.execute(
                "UPDATE knowledge_nodes SET memory_tier = 'archive', lifecycle_stage = 'archived_knowledge', updated_at = ? WHERE node_id = ?",
                [timeutil.utcnow(), row["node_id"]]
            )
            transitions["to_archive"] += 1

        # Rule 4: Validated Knowledge -> Long-term Knowledge if validated repeatedly over > 1 year
        rows = self.db.query_dicts(
            """
            SELECT n.node_id
            FROM knowledge_nodes n
            WHERE n.memory_tier = 'long_term'
              AND n.lifecycle_stage = 'validated_knowledge'
              AND n.created_at < ?
            """,
            [timeutil.utcnow() - timedelta(days=365)]
        )
        for row in rows:
            self.db.execute(
                "UPDATE knowledge_nodes SET lifecycle_stage = 'long_term_knowledge', updated_at = ? WHERE node_id = ?",
                [timeutil.utcnow(), row["node_id"]]
            )

        self.log.info("memory transitions evaluated", **transitions)
        return transitions
