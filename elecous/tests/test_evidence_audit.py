"""Correctness tests for the evidence audit script's logic, using the
isolated empty test database (no fabricated observations — this verifies
the analysis code doesn't crash and reports honest zeros, not that any
particular real number is produced)."""

from __future__ import annotations

from elecous.scripts.evidence_audit import EvidenceAuditor, _industry_of


def test_industry_mapping_uses_real_ontology():
    assert _industry_of("camping_equipment") == "outdoor_activity"
    assert _industry_of("totally_unknown_category") == "unclassified"


def test_audit_on_empty_database_reports_honest_zeros(isolated_config):
    auditor = EvidenceAuditor()
    coverage = auditor.audit_coverage()
    assert coverage["totals"]["total_observations"] == 0
    assert coverage["totals"]["unique_phenomena_with_episodes"] == 0
    assert coverage["phenomena"] == []
    assert coverage["gaps"]["missing_categories_no_episode_ever"], "every category should show as missing with zero data"

    bottlenecks = auditor.rank_bottlenecks(coverage)
    zero_episode_bottleneck = next(b for b in bottlenecks if b["bottleneck"] == "zero_episodes_whatsoever")
    assert zero_episode_bottleneck["categories_affected"] == len(coverage["gaps"]["missing_categories_no_episode_ever"])

    priorities = auditor.prioritize_collection(coverage)
    assert priorities == []  # no phenomena exist yet to prioritize among

    gate = auditor.validation_gate_all(coverage)
    assert gate["open"] == []
    assert gate["blocked"] == []  # nothing to block either; categories never appear without episodes

    single_gate = auditor.validation_gate(coverage, "cooling")
    assert single_gate["gate"] == "blocked"
    assert "zero real episodes" in single_gate["reason"]
