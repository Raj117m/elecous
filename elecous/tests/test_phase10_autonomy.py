"""Phase 10 tests: Autonomous Supervisor, self-healing, Expansion Engine
(real candidate discovery/sandbox/promotion against a real public API), and
the Curiosity Engine / Unknown Repository. No fabricated data — the
Expansion Engine test hits the real Open-Meteo Flood API end-to-end."""

from __future__ import annotations

import pytest

from elecous.bootstrap import build_stack
from elecous.infrastructure.supervisor import AutonomousSupervisor


def test_supervisor_status_reports_all_subsystems(isolated_config):
    stack = build_stack()
    status = stack.supervisor.status()
    assert set(status["subsystems"]) == {"observation", "knowledge", "validation", "scheduler", "resources"}
    assert isinstance(status["overall"], bool)


def test_supervisor_repairs_knowledge_integrity(isolated_config):
    from elecous.common import timeutil

    stack = build_stack()
    # introduce a real broken edge the way integrity_scan would find one
    graph = stack.knowledge.graph
    node = graph.upsert_node("signal", "sig_x", "x", confidence=0.5)
    graph.db.execute(
        "INSERT INTO knowledge_edges VALUES (?, ?, ?, ?, ?, ?)",
        ["edge_dangling", node, "does_not_exist", "supports", 1.0, timeutil.utcnow()],
    )
    report = stack.supervisor.check_knowledge()
    assert report.healthy is False
    attempts = stack.supervisor.self_heal()
    knowledge_attempt = next(a for a in attempts if a.subsystem == "knowledge")
    assert knowledge_attempt.succeeded is True
    assert stack.supervisor.check_knowledge().healthy is True


def test_supervisor_escalates_after_repeated_failures(isolated_config):
    stack = build_stack()

    class AlwaysBroken:
        def status(self):
            pass

    calls = {"n": 0}

    def fake_repair(report):
        calls["n"] += 1
        return False

    stack.supervisor._repairers["observation"] = fake_repair
    # Force check_observation to report unhealthy every time.
    stack.supervisor.check_observation = lambda: stack.supervisor.check_knowledge().__class__(
        "observation", False, {"failing": ["fake_source"]}
    )
    for _ in range(3):
        stack.supervisor.self_heal()
    assert calls["n"] == 3
    from elecous.dashboard.monitor.notifications import NotificationEngine

    notifications = NotificationEngine(stack.databases, stack.config).recent()
    assert any(n["kind"] == "self_healing_exhausted" for n in notifications)


def test_curiosity_surfaces_declining_mechanisms(isolated_config):
    stack = build_stack()
    from elecous.common.vocabulary import Lifecycle, ValidationStatus
    from elecous.mechanisms.versioning.store import MechanismStore

    store = MechanismStore(stack.databases)
    payload = {
        "name": "declining test mechanism",
        "environmental_factors": ["fac_ref"],
        "supporting_signals": ["thermal_stress:up:US"],
        "behavioural_chain": [],
        "product_traits": [],
        "affected_categories": ["cooling"],
        "countries": ["US"],
        "lead_time": {"min": 3, "avg": 7, "max": 12, "stddev": 2},
        "confidence": {"historical": 0.3, "recent": 0.3, "regional": 0.3, "scientific": 0.3, "current": 0.3},
        "supporting_episodes": ["epi_1", "epi_2", "epi_3"],
        "evidence_links": {},
    }
    mechanism_id = store.create(payload)
    store.promote(mechanism_id, Lifecycle.EXPERIMENTAL, ValidationStatus.PASSED, actor="t")
    store.promote(mechanism_id, Lifecycle.VALIDATED, ValidationStatus.PASSED, actor="t")
    store.promote(mechanism_id, Lifecycle.TRUSTED, ValidationStatus.PASSED, actor="t")
    store.promote(mechanism_id, Lifecycle.DECLINING, ValidationStatus.FAILED, actor="t")

    curiosity = stack.curiosity
    curiosity.mechanisms = store
    surfaced = curiosity._surface_declining_mechanisms()
    assert surfaced == 1
    agenda = stack.knowledge.research_agenda()
    assert any(mechanism_id in str(u.get("context", "")) for u in agenda)
    # idempotent: a second pass doesn't duplicate the same open unknown
    assert curiosity._surface_declining_mechanisms() == 0


@pytest.mark.network
def test_expansion_engine_discovers_sandboxes_and_promotes_real_source(isolated_config):
    """Full pipeline on a genuinely new real capability: candidate pool ->
    sandbox (schema stability, repeatability, quality, freshness,
    performance against the real Open-Meteo Flood API) -> promotion."""
    stack = build_stack()
    assert stack.observation.registry.get("open_meteo_flood") is None

    result = stack.expansion.run_expansion_cycle()
    assert result["discovered"] == 1
    assert "open_meteo_flood" in result["registered"]

    source = stack.observation.registry.get("open_meteo_flood")
    assert source is not None
    if result["promoted"]:
        assert source["status"] == "active"
        # promoted source can now actually collect real river discharge data
        run = stack.observation.run_source("open_meteo_flood")
        assert run["success"]
    else:
        assert source["status"] == "sandbox"
        assert "open_meteo_flood" in result["rejected"]

    # re-running does not re-register an already-known source
    result2 = stack.expansion.run_expansion_cycle()
    assert result2["discovered"] == 0
