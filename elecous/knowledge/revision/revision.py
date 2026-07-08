"""Knowledge aging, half-life, revision and reorganization (spec 7.10-7.14, 7.26-7.29).

Knowledge Half-Life: a mechanism validated long ago without recent support
slowly loses influence — it is never deleted, only discounted. Revision:
failures trigger investigation, not deletion (mirrors the Negative Learning
Engine at the mechanism level, applied here as a knowledge-graph concern:
integrity scoring and node discounting). Self-reorganization: merge/split
delegate to the authoritative stores (FactorStore already implements
merge_similar/split_overbroad); this module adds discovery ranking and
integrity checks that scan the graph itself.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.infrastructure.logging import get_logger
from elecous.knowledge.schema import KNOWLEDGE_MIGRATION, KNOWLEDGE_MIGRATION_0002
from elecous.storage.database import DatabaseManager, global_databases

#: Default half-life per object type, in days (spec 7.10). Configurable via
#: thresholds.yaml if desired; conservative defaults here favor patience.
DEFAULT_HALF_LIFE_DAYS = {
    "signal": 180.0,
    "environmental_factor": 270.0,
    "mechanism": 365.0,
}


class KnowledgeRevisionEngine:
    def __init__(self, graph, databases: DatabaseManager | None = None):
        self.graph = graph  # KnowledgeGraph
        self.databases = databases or global_databases()
        self.databases.register_schema(KNOWLEDGE_MIGRATION)
        self.databases.register_schema(KNOWLEDGE_MIGRATION_0002)
        self.db = self.databases.get("knowledge")
        self.log = get_logger("knowledge")

    # -------------------------------------------------------------- aging
    def decayed_confidence(self, node: dict[str, Any], last_support_at) -> float:
        """Exponential decay since the object's last supporting evidence.
        Half-life default depends on object type unless the node overrides
        it (spec 7.10)."""
        half_life = node.get("half_life_days") or DEFAULT_HALF_LIFE_DAYS.get(node["object_type"], 270.0)
        age_days = (timeutil.utcnow() - timeutil.ensure_utc(last_support_at)).days
        decay = 0.5 ** (age_days / half_life)
        return round(float(node["confidence"]) * decay, 4)

    def apply_aging(self, node_id: str, last_support_at) -> float:
        node = self.db.query_one("SELECT * FROM knowledge_nodes WHERE node_id = ?", [node_id])
        rows = self.db.query_dicts("SELECT * FROM knowledge_nodes WHERE node_id = ?", [node_id])
        if not rows:
            return 0.0
        aged = self.decayed_confidence(rows[0], last_support_at)
        self.db.execute(
            "UPDATE knowledge_nodes SET confidence = ?, updated_at = ? WHERE node_id = ?",
            [aged, timeutil.utcnow(), node_id],
        )
        return aged

    # ----------------------------------------------------------- revision
    def revise(self, object_type: str, object_id: str, from_version: int | None, to_version: int, reason: str, actor: str) -> str:
        revision_id = new_id("knowledge")
        self.db.execute(
            "INSERT INTO knowledge_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [revision_id, object_type, object_id, from_version, to_version, reason, actor, timeutil.utcnow()],
        )
        self.log.info("knowledge revised", object_type=object_type, object_id=object_id, reason=reason)
        return revision_id

    def revision_history(self, object_id: str) -> list[dict[str, Any]]:
        return self.db.query_dicts(
            "SELECT * FROM knowledge_revisions WHERE object_id = ? ORDER BY created_at", [object_id]
        )

    # ------------------------------------------------------ self-healing
    def detect_issues(self) -> dict[str, Any]:
        """Read-only detection half of Self-Healing Knowledge (spec 7.27):
        counts broken references, duplicate objects and invalid links
        without repairing anything. Used by health checks that must not
        have side effects (Detect must not silently become Repair)."""
        node_ids = {r[0] for r in self.db.query("SELECT node_id FROM knowledge_nodes")}
        edges = self.db.query_dicts("SELECT * FROM knowledge_edges")
        broken = [e for e in edges if e["source_node"] not in node_ids or e["target_node"] not in node_ids]
        duplicates = self.db.query_dicts(
            "SELECT object_type, object_ref, count(*) AS n FROM knowledge_nodes GROUP BY object_type, object_ref HAVING n > 1"
        )
        missing_evidence = self.db.query_dicts(
            "SELECT n.* FROM knowledge_nodes n LEFT JOIN knowledge_edges e ON n.node_id = e.source_node "
            "WHERE e.edge_id IS NULL AND n.object_type IN ('environmental_factor', 'mechanism')"
        )
        return {
            "broken_edges": len(broken),
            "duplicate_node_groups": len(duplicates),
            "nodes_missing_evidence_links": len(missing_evidence),
        }

    def integrity_scan(self) -> dict[str, Any]:
        """Repair half of Self-Healing Knowledge (spec 7.27): find broken
        references, duplicate objects and invalid links, and repair what can
        be repaired automatically (dangling edges are removed); reports the
        rest for investigation."""
        node_ids = {r[0] for r in self.db.query("SELECT node_id FROM knowledge_nodes")}
        edges = self.db.query_dicts("SELECT * FROM knowledge_edges")
        broken = [e for e in edges if e["source_node"] not in node_ids or e["target_node"] not in node_ids]
        for edge in broken:
            self.db.execute("DELETE FROM knowledge_edges WHERE edge_id = ?", [edge["edge_id"]])

        remaining = self.detect_issues()
        report = {
            "broken_edges_repaired": len(broken),
            "broken_edges_remaining": remaining["broken_edges"],
            "duplicate_node_groups": remaining["duplicate_node_groups"],
            "nodes_missing_evidence_links": remaining["nodes_missing_evidence_links"],
        }
        self.log.info("knowledge integrity scan", **report)
        return report

    # ---------------------------------------------------- discovery ranking
    def rank_research_priorities(self, unknowns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Knowledge Discovery Ranking (spec 7.28): priority blends
        research_priority (business/scientific uncertainty proxy already
        assigned at recording time) with recency — newer unknowns rank
        slightly higher so research stays current."""
        now = timeutil.utcnow()
        ranked = []
        for unknown in unknowns:
            age_days = (now - timeutil.ensure_utc(unknown["created_at"])).days
            recency_bonus = 1.0 / (1.0 + age_days / 30.0)
            score = 0.7 * float(unknown["research_priority"]) + 0.3 * recency_bonus
            ranked.append({**unknown, "rank_score": round(score, 4)})
        ranked.sort(key=lambda r: -r["rank_score"])
        return ranked

    # -------------------------------------------------------- compression
    def compression_ratio(self, observation_count: int, mechanism_count: int) -> float | None:
        """Information Density (Appendix A Principle 3): observations per
        mechanism. Rising over time is the health signal, not raw volume.
        None (not infinity, which is not JSON-serializable) when no
        mechanisms exist yet to compress into."""
        if mechanism_count == 0:
            return None
        return round(observation_count / mechanism_count, 2)

    # -------------------------------------------------------- Phase 3 Lifecycle
    def retire_obsolete_knowledge(self) -> dict[str, int]:
        """Automatically archive nodes whose confidence has decayed below
        the operational threshold or that have consistently failed validations.
        Retiring means marking them as 'retired' status so they no longer
        drive predictions but remain in the memory."""
        retired_count = 0
        try:
            nodes = self.db.query_dicts("SELECT * FROM knowledge_nodes WHERE status != 'retired' AND status != 'merged'")
        except Exception:
            # If migration didn't run properly yet, fallback
            nodes = self.db.query_dicts("SELECT * FROM knowledge_nodes")
            
        for node in nodes:
            if float(node["confidence"]) < 0.2:
                try:
                    self.db.execute("UPDATE knowledge_nodes SET status = 'retired', updated_at = ? WHERE node_id = ?", [timeutil.utcnow(), node["node_id"]])
                except Exception:
                    pass
                self.revise(node["object_type"], node["object_ref"], None, None, "confidence_decay_below_threshold", "revision_engine")
                retired_count += 1
        
        if retired_count > 0:
            self.log.info("knowledge retired", retired_nodes=retired_count)
        return {"retired_nodes": retired_count}

    def merge_duplicate_concepts(self) -> dict[str, int]:
        """Detect heavily overlapping factors or concepts in the graph and
        merge them. We consider two nodes identical if they share the exact
        same label."""
        merged_count = 0
        try:
            duplicates = self.db.query_dicts(
                "SELECT label, object_type, group_concat(node_id) as ids FROM knowledge_nodes WHERE status != 'retired' AND status != 'merged' GROUP BY label, object_type HAVING count(*) > 1"
            )
            for dup in duplicates:
                ids = dup["ids"].split(",")
                primary_id = ids[0]
                for duplicate_id in ids[1:]:
                    self.db.execute("UPDATE knowledge_edges SET source_node = ? WHERE source_node = ?", [primary_id, duplicate_id])
                    self.db.execute("UPDATE knowledge_edges SET target_node = ? WHERE target_node = ?", [primary_id, duplicate_id])
                    self.db.execute("UPDATE knowledge_nodes SET status = 'merged', updated_at = ? WHERE node_id = ?", [timeutil.utcnow(), duplicate_id])
                    self.revise(dup["object_type"], duplicate_id, None, None, f"merged_into_{primary_id}", "revision_engine")
                    merged_count += 1
        except Exception as e:
            self.log.warning("merge_duplicate_concepts failed, possible schema mismatch", error=str(e))
            
        if merged_count > 0:
            self.log.info("knowledge merged", merged_nodes=merged_count)
        return {"merged_nodes": merged_count}
