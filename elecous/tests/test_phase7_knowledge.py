"""Phase 7 tests: Knowledge Graph, memory, revision/aging, governance.
Pure logic over abstract object identifiers — no fabricated observations."""

from __future__ import annotations

from datetime import timedelta

import pytest

from elecous.common import timeutil
from elecous.common.errors import ConstitutionViolation
from elecous.common.vocabulary import Relationship
from elecous.knowledge.governance import governance
from elecous.knowledge.graph.graph import KnowledgeGraph
from elecous.knowledge.memory.memory import KnowledgeMemory
from elecous.knowledge.revision.revision import KnowledgeRevisionEngine


def test_graph_upsert_and_relationships(isolated_config):
    graph = KnowledgeGraph()
    signal_node = graph.upsert_node("signal", "sig_1", "thermal_stress:up:US", confidence=0.7)
    factor_node = graph.upsert_node("environmental_factor", "fac_1", "Heat Factor", confidence=0.6)
    graph.link(factor_node, signal_node, Relationship.DEPENDS_ON)
    neighbors = graph.neighbors(factor_node, Relationship.DEPENDS_ON)
    assert len(neighbors) == 1 and neighbors[0]["target_node"] == signal_node

    # upsert is idempotent by (object_type, object_ref)
    again = graph.upsert_node("signal", "sig_1", "thermal_stress:up:US", confidence=0.9)
    assert again == signal_node
    assert graph.node_for("signal", "sig_1")["confidence"] == 0.9


def test_governance_blocks_manual_edits_and_missing_evidence():
    with pytest.raises(ConstitutionViolation):
        governance.forbid_manual_edit("human")
    governance.forbid_manual_edit("validation_engine")  # ok

    with pytest.raises(ConstitutionViolation):
        governance.require_traceable_node("mechanism", "mech_1", evidence_ids=[])
    governance.require_traceable_node("mechanism", "mech_1", evidence_ids=["epi_1"])

    with pytest.raises(ConstitutionViolation):
        governance.require_relationship_declared("secret_link", known={"supports", "contradicts"})


def test_knowledge_memory_world_state_and_failures(isolated_config):
    memory = KnowledgeMemory()
    state = {
        "state_id": "wst_1",
        "as_of": timeutil.to_iso(timeutil.utcnow()),
        "conditions": ["US:thermal_stress:up+energy_demand:up"],
    }
    memory.snapshot_world_state(state, active_mechanisms=["mech_1"], active_factors=["fac_1"])
    found = memory.world_state_near(timeutil.utcnow())
    assert found is not None and found["world_state_id"] == "wst_1"

    memory.record_failure("mechanism", "mech_1", "prospective failure", {"reason": "wrong_timing"})
    failures = memory.failures_for("mech_1")
    assert len(failures) == 1 and failures[0]["summary"] == "prospective failure"

    unknown_id = memory.record_unknown("unknown_pattern", "unexplained spike", {"entity": "X"}, research_priority=0.8)
    open_unknowns = memory.open_unknowns()
    assert any(u["unknown_id"] == unknown_id for u in open_unknowns)
    memory.resolve_unknown(unknown_id)
    assert memory.open_unknowns() == []


def test_half_life_decays_without_deleting(isolated_config):
    graph = KnowledgeGraph()
    revision = KnowledgeRevisionEngine(graph)
    node_id = graph.upsert_node("mechanism", "mech_old", "Old Mechanism", confidence=0.9, half_life_days=30)
    old_support = timeutil.utcnow() - timedelta(days=60)  # two half-lives ago
    aged = revision.apply_aging(node_id, old_support)
    assert aged == pytest.approx(0.9 * 0.25, abs=0.01)  # 2 half-lives -> 0.25x
    node = graph.node_for("mechanism", "mech_old")
    assert node is not None  # never deleted, only discounted
    assert node["confidence"] == aged


def test_integrity_scan_repairs_broken_edges(isolated_config):
    graph = KnowledgeGraph()
    revision = KnowledgeRevisionEngine(graph)
    a = graph.upsert_node("signal", "sig_a", "a", confidence=0.5)
    graph.db.execute(
        "INSERT INTO knowledge_edges VALUES (?, ?, ?, ?, ?, ?)",
        ["edge_dangling", a, "node_does_not_exist", "supports", 1.0, timeutil.utcnow()],
    )
    report = revision.integrity_scan()
    assert report["broken_edges_repaired"] == 1
    remaining = graph.db.query_one("SELECT count(*) FROM knowledge_edges WHERE edge_id = 'edge_dangling'")[0]
    assert remaining == 0


def test_discovery_ranking_prefers_priority_and_recency(isolated_config):
    graph = KnowledgeGraph()
    revision = KnowledgeRevisionEngine(graph)
    old_unknown = {
        "unknown_id": "u1",
        "research_priority": 0.5,
        "created_at": timeutil.utcnow() - timedelta(days=90),
    }
    new_unknown = {
        "unknown_id": "u2",
        "research_priority": 0.5,
        "created_at": timeutil.utcnow(),
    }
    ranked = revision.rank_research_priorities([old_unknown, new_unknown])
    assert ranked[0]["unknown_id"] == "u2"  # recency tiebreaker favors newer

    high_priority_old = {
        "unknown_id": "u3",
        "research_priority": 0.95,
        "created_at": timeutil.utcnow() - timedelta(days=200),
    }
    ranked2 = revision.rank_research_priorities([new_unknown, high_priority_old])
    assert ranked2[0]["unknown_id"] == "u3"  # priority dominates recency


def test_compression_ratio_grows_with_understanding():
    revision = KnowledgeRevisionEngine.__new__(KnowledgeRevisionEngine)
    assert KnowledgeRevisionEngine.compression_ratio(revision, 1_000_000, 500) == 2000.0
    # Undefined (no mechanisms yet) must be JSON-serializable, so None not inf.
    assert KnowledgeRevisionEngine.compression_ratio(revision, 0, 0) is None
    assert KnowledgeRevisionEngine.compression_ratio(revision, 500, 0) is None
