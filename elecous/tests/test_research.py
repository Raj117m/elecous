"""Stage 1+2 tests for the Research Intelligence package (rewrite directive,
Level 2 scientific memory). Pure schema/store logic — no fabricated
observations; identifiers are abstract inputs to the stores under test."""

from __future__ import annotations

from datetime import timedelta

from elecous.common import timeutil
from elecous.research.engine import ResearchEngine
from elecous.research.experience import ResearchExperienceStore, classify_failure
from elecous.research.priority import (
    expected_value,
    lesson_discount_for,
    plan_evidence,
    score_source,
    wilson_interval,
    wilson_width,
)
from elecous.research.progress import ProgressReporter
from elecous.research.questions import ResearchQuestion, ResearchQuestionStore
from elecous.research.source_value import SourceValueLedger


def test_research_question_open_get_list(isolated_config):
    store = ResearchQuestionStore()
    qid = store.open(
        ResearchQuestion(
            kind="mechanism_candidate",
            statement="does heat deviation drive cooling-product attention in AU?",
            opened_by="curiosity",
            subject_type="mechanism",
            subject_ref="mech_1",
            need_cluster="cooling",
            expected_evidence={"capabilities": ["weather"], "sources": ["open_meteo_weather"]},
            priority=0.7,
        )
    )
    row = store.get(qid)
    assert row is not None
    assert row["status"] == "open"
    assert row["expected_evidence"]["capabilities"] == ["weather"]

    open_questions = store.list(status="open")
    assert any(r["question_id"] == qid for r in open_questions)


def test_research_question_open_is_idempotent_per_unknown(isolated_config):
    """Bridging the same surfaced anomaly twice must not spawn duplicate
    open questions (Curiosity re-surfaces the same unknown every cycle until
    it's resolved)."""
    store = ResearchQuestionStore()
    first = store.open(
        ResearchQuestion(kind="factor_candidate", statement="q1", opened_by="curiosity", unknown_id="unk_1")
    )
    second = store.open(
        ResearchQuestion(kind="factor_candidate", statement="q1 again", opened_by="curiosity", unknown_id="unk_1")
    )
    assert first == second
    assert len(store.list(status="open")) == 1


def test_research_question_resolve_transitions_status(isolated_config):
    store = ResearchQuestionStore()
    qid = store.open(ResearchQuestion(kind="custom", statement="q", opened_by="manual"))
    store.spend_budget(qid, 3.0)
    store.resolve(qid, "refuted", confidence_after=0.1)
    row = store.get(qid)
    assert row["status"] == "resolved"
    assert row["resolution"] == "refuted"
    assert row["confidence_after"] == 0.1
    assert row["budget_spent_units"] == 3.0
    assert row not in store.list(status="open")


def test_source_value_ledger_accumulates_per_need_cluster(isolated_config):
    ledger = SourceValueLedger()
    ledger.record("open_meteo_weather", "cooling", passed=True, lead_time_days=5.0)
    ledger.record("open_meteo_weather", "cooling", passed=True, lead_time_days=7.0)
    ledger.record("open_meteo_weather", "heating", passed=False)

    cooling = ledger.get("open_meteo_weather", "cooling")
    assert cooling["supports"] == 2
    assert cooling["contradictions"] == 0
    assert cooling["net_value_ema"] > 0
    assert cooling["sample_size"] == 2

    heating = ledger.get("open_meteo_weather", "heating")
    assert heating["contradictions"] == 1
    assert heating["net_value_ema"] < 0

    # Keyed per (source, need_cluster): a source can be known-good for one
    # domain and known-bad for another, not collapsed into one global score.
    assert cooling["net_value_ema"] != heating["net_value_ema"]


def test_source_value_ledger_global_contribution_weights_by_sample_size(isolated_config):
    ledger = SourceValueLedger()
    for _ in range(10):
        ledger.record("wikimedia_pageviews", "cooling", passed=True)
    ledger.record("wikimedia_pageviews", "heating", passed=False)
    contribution = ledger.global_contribution("wikimedia_pageviews")
    # 10 strong passes vs 1 fail: weighted average should stay clearly
    # positive, not be dragged to zero by the single low-sample cluster.
    assert contribution > 0.5


def test_source_value_ledger_unknown_source_has_no_contribution(isolated_config):
    ledger = SourceValueLedger()
    assert ledger.global_contribution("never_queried_source") == 0.0
    assert ledger.get("never_queried_source", "cooling") is None


# ------------------------------------------------------------ Stage 2: priority.py


def test_wilson_interval_no_evidence_is_fully_uncertain_not_bad():
    """Zero samples must not look "bad" (a naive point estimate of 0/0 or
    treating no-data as failure would incorrectly exclude untried sources
    forever)."""
    lower, upper = wilson_interval(0, 0)
    assert lower == 0.0 and upper == 1.0


def test_wilson_interval_narrows_with_more_consistent_samples():
    """The real, honest stand-in for "information gain": interval width
    must shrink as consistent evidence accumulates."""
    narrow_few = wilson_width(8, 10)
    narrow_many = wilson_width(80, 100)
    assert narrow_many < narrow_few  # same 80% rate, more samples => tighter


def test_score_source_uses_only_real_supplied_numbers():
    ledger_record = {"supports": 9, "contradictions": 1, "last_event_at": timeutil.utcnow()}
    health_record = {"health": 0.9}
    result = score_source("open_meteo_weather", "cooling", ledger_record, health_record, timeutil.utcnow())
    assert result.sample_size == 10
    assert 0.0 < result.wilson_lower < result.wilson_upper < 1.0
    assert "wilson=" in result.rationale and "health=0.90" in result.rationale


def test_plan_evidence_excludes_confirmed_poor_source(isolated_config):
    """A source with >=5 judged outcomes and a Wilson upper bound below the
    fair-coin baseline (0.5) is a real, statistically-confirmed poor
    performer for this need_cluster and must be excluded (Requirement 2)."""
    ledger = SourceValueLedger()
    for _ in range(9):
        ledger.record("bad_source", "cooling", passed=False)
    ledger.record("bad_source", "cooling", passed=True)  # 1/10 -> clearly poor
    for _ in range(8):
        ledger.record("good_source", "cooling", passed=True)
    ledger.record("good_source", "cooling", passed=False)  # 8/9 -> clearly good

    class _NoHealth:
        def score(self, source_id):
            return None

    plan = plan_evidence(
        need_cluster="cooling",
        candidate_sources=["bad_source", "good_source"],
        ledger=ledger,
        health=_NoHealth(),
        lessons=[],
        unit_budget=10.0,
        now=timeutil.utcnow(),
    )
    assert "good_source" in plan.sources
    assert "bad_source" in plan.excluded
    assert "bad_source" not in plan.sources


def test_plan_evidence_keeps_exploration_floor_when_all_candidates_unproven(isolated_config):
    """No evidence yet is not the same as confirmed-poor -- an unproven
    source must still be selectable (otherwise a brand-new source could
    never be tried)."""
    ledger = SourceValueLedger()

    class _NoHealth:
        def score(self, source_id):
            return None

    plan = plan_evidence(
        need_cluster="cooling",
        candidate_sources=["never_used_source"],
        ledger=ledger,
        health=_NoHealth(),
        lessons=[],
        unit_budget=10.0,
        now=timeutil.utcnow(),
    )
    assert plan.sources == ["never_used_source"]


def test_plan_evidence_respects_unit_budget(isolated_config):
    ledger = SourceValueLedger()

    class _NoHealth:
        def score(self, source_id):
            return None

    plan = plan_evidence(
        need_cluster="cooling",
        candidate_sources=["source_a", "source_b", "source_c"],
        ledger=ledger,
        health=_NoHealth(),
        lessons=[],
        unit_budget=2.0,
        now=timeutil.utcnow(),
    )
    assert len(plan.sources) == 2


def test_lesson_discount_requires_repeated_confirmation():
    assert lesson_discount_for([])[0] == 0.0
    assert lesson_discount_for([{"confirmations": 2, "lesson_text": "x"}])[0] == 0.0
    discount, note = lesson_discount_for([{"confirmations": 4, "lesson_text": "known-failing path"}])
    assert discount == 0.4  # 0.1 * 4, capped
    assert "known-failing path" in note


# ----------------------------------------------------------- Stage 2: experience.py


def test_resolve_writes_exactly_one_experience_row(isolated_config):
    store = ResearchQuestionStore()
    qid = store.open(
        ResearchQuestion(
            kind="factor_candidate", statement="q", opened_by="manual", need_cluster="cooling",
            expected_evidence={"sources": ["open_meteo_weather"]},
        )
    )
    store.resolve(qid, "supported", confidence_after=0.7, validation_path="factor_validation")
    attempts = store.experience.similar_attempts("cooling")
    assert len(attempts) == 1
    assert attempts[0]["question_id"] == qid
    assert attempts[0]["outcome"] == "supported"
    assert attempts[0]["strategy"]["validation_path"] == "factor_validation"


def test_repeated_real_failures_generalize_into_a_lesson(isolated_config):
    """Requirement 6/8: the same validation_path refuted 3 real times for
    the same need_cluster must become a queryable, reusable lesson -- not
    just three isolated experience rows."""
    experience = ResearchExperienceStore()
    for _ in range(3):
        experience.record(
            question_id="q_irrelevant",
            need_cluster="cooling",
            strategy={"validation_path": "historical_replay"},
            outcome="refuted",
            cost={"observation_units_spent": 1.0},
        )
    lessons = experience.lessons_for("cooling")
    assert len(lessons) == 1
    assert lessons[0]["confirmations"] == 3
    assert "historical_replay" in lessons[0]["lesson_text"]


def test_mixed_outcomes_do_not_overclaim_a_lesson(isolated_config):
    """A pattern that sometimes succeeds must not be declared a
    known-failing path -- lessons only generalize from a clean, consistent
    real pattern."""
    experience = ResearchExperienceStore()
    outcomes = ["refuted", "refuted", "supported"]
    for outcome in outcomes:
        experience.record(
            question_id="q_irrelevant",
            need_cluster="heating",
            strategy={"validation_path": "historical_replay"},
            outcome=outcome,
            cost={"observation_units_spent": 1.0},
        )
    assert experience.lessons_for("heating") == []


# --------------------------------------------------------------- Stage 2: engine.py


class _FakeRegistry:
    def __init__(self, sources):
        self._sources = sources

    def list(self, status=None, capability=None):
        return [
            s for s in self._sources
            if (status is None or s["status"] == status) and (capability is None or s["observation_capability"] == capability)
        ]

    def update_contribution(self, source_id, value):
        pass


class _FakeHealth:
    def score(self, source_id):
        return {"health": 0.6}


class _FakeNetwork:
    def __init__(self, accepted=1):
        self.calls = []
        self._accepted = accepted

    def run_source(self, source_id, mode="live"):
        self.calls.append(source_id)
        return {"source_id": source_id, "success": True, "stats": {"accepted": self._accepted}}


class _FakeResources:
    def __init__(self, pause=False):
        self._pause = pause
        self._n = 0

    def should_pause(self):
        return self._pause

    def sample(self):
        self._n += 1
        return {"process_cpu_percent": float(self._n), "process_memory_mb": 100.0 + self._n}


def test_research_engine_tick_plans_and_spends_real_budget(isolated_config):
    registry = _FakeRegistry([
        {"source_id": "open_meteo_weather", "observation_capability": "weather", "status": "active"},
        {"source_id": "nws_alerts", "observation_capability": "weather", "status": "active"},
    ])
    questions = ResearchQuestionStore()
    qid = questions.open(
        ResearchQuestion(
            kind="factor_candidate", statement="q", opened_by="manual", need_cluster="cooling",
            expected_evidence={"capabilities": ["weather"]},
        )
    )
    ledger = SourceValueLedger()
    network = _FakeNetwork()
    engine = ResearchEngine(questions, ledger, questions.experience, network, _FakeHealth(), registry, _FakeResources())

    summary = engine.tick(unit_budget=1.0)
    assert summary["questions_considered"] == 1
    assert summary["budget_spent"] == 1.0
    assert len(network.calls) == 1  # budget of 1 unit -> exactly one source queried

    row = questions.get(qid)
    assert row["budget_spent_units"] == 1.0
    assert "wilson=" in row["expected_evidence"]["rationale"]  # Requirement 9: real, stored rationale


def test_research_engine_tick_respects_pause_signal(isolated_config):
    registry = _FakeRegistry([{"source_id": "open_meteo_weather", "observation_capability": "weather", "status": "active"}])
    questions = ResearchQuestionStore()
    questions.open(
        ResearchQuestion(
            kind="factor_candidate", statement="q", opened_by="manual", need_cluster="cooling",
            expected_evidence={"capabilities": ["weather"]},
        )
    )
    ledger = SourceValueLedger()
    network = _FakeNetwork()
    engine = ResearchEngine(questions, ledger, questions.experience, network, _FakeHealth(), registry, _FakeResources(pause=True))

    summary = engine.tick(unit_budget=5.0)
    assert summary["paused"] is True
    assert network.calls == []  # never even attempted a source while paused


def test_research_engine_resolves_matching_open_question_on_validation(isolated_config):
    """on_validation_completed must resolve exactly the question about the
    object the Validation Engine judged, and only that one."""
    from elecous.infrastructure.event_bus import Event, EventType

    questions = ResearchQuestionStore()
    matching = questions.open(
        ResearchQuestion(
            kind="factor_candidate", statement="q1", opened_by="manual",
            subject_type="environmental_factor", subject_ref="fac_1",
        )
    )
    other = questions.open(
        ResearchQuestion(
            kind="factor_candidate", statement="q2", opened_by="manual",
            subject_type="environmental_factor", subject_ref="fac_2",
        )
    )
    ledger = SourceValueLedger()
    engine = ResearchEngine(questions, ledger, questions.experience, None, None, None, None)
    engine.on_validation_completed(
        Event(event_type=EventType.VALIDATION_COMPLETED, source="validation",
              payload={"object_type": "environmental_factor", "object_id": "fac_1", "method": "factor_validation", "outcome": "passed"})
    )
    assert questions.get(matching)["status"] == "resolved"
    assert questions.get(matching)["resolution"] == "supported"
    assert questions.get(other)["status"] == "open"


# --------------------------------------------------------- Stage 2.5: progress.py


def test_progress_reporter_tracks_real_done_and_stall():
    progress = ProgressReporter(total=10, label="test")
    assert progress.stalled_for() < 1.0
    progress.tick(3)
    assert progress.done == 3
    assert progress.stalled_for() < 1.0
    assert progress.check_stall() is False  # just ticked, not stalled


# ------------------------------------------------------ Stage 2.5: classify_failure


def test_classify_failure_reads_real_skeptic_challenge_shape():
    """Shape taken directly from a real row inspected this session in
    validation.duckdb: controls.challenges.<check>.passed=False."""
    row = {
        "controls": '{"survived": false, "challenges": {"coincidence": {"passed": true}, '
                     '"sampling_bias": {"passed": false, "entities": ["Umbrella"]}}}',
        "evidence": '{"multiple_episodes": true, "multiple_regions": true, "counterfactual_survives": true, "skeptic_survives": false}',
    }
    assert classify_failure(row) == "poor_source_selection"  # sampling_bias fired


def test_classify_failure_falls_back_to_outer_evidence_check():
    row = {
        "controls": '{"survived": true, "challenges": {}}',
        "evidence": '{"multiple_episodes": false, "multiple_regions": true, "counterfactual_survives": true}',
    }
    assert classify_failure(row) == "insufficient_evidence"  # multiple_episodes failed


def test_classify_failure_honest_default_when_nothing_recognized():
    assert classify_failure({"controls": None, "evidence": None}) == "insufficient_evidence"


# ------------------------------------------------------- Stage 2.5: expected_value


def test_expected_value_neutral_with_no_prior_experience(isolated_config):
    experience = ResearchExperienceStore()
    p, rationale = expected_value("brand_new_cluster", "factor_candidate", experience)
    assert p == 0.5
    assert "no prior real experience" in rationale


def test_expected_value_reflects_real_prior_success_rate(isolated_config):
    experience = ResearchExperienceStore()
    store = ResearchQuestionStore(experience=experience)
    for outcome in ["supported", "supported", "refuted"]:
        qid = store.open(ResearchQuestion(kind="factor_candidate", statement="q", opened_by="manual", need_cluster="cooling"))
        store.resolve(qid, outcome)
    p, rationale = expected_value("cooling", "factor_candidate", experience)
    assert 0.0 < p < 1.0
    assert "2/3 real prior attempts supported" in rationale


def test_open_with_estimate_priority_uses_real_history(isolated_config):
    experience = ResearchExperienceStore()
    store = ResearchQuestionStore(experience=experience)
    for _ in range(3):
        qid = store.open(ResearchQuestion(kind="factor_candidate", statement="q", opened_by="manual", need_cluster="cooling"))
        store.resolve(qid, "refuted")
    new_qid = store.open(
        ResearchQuestion(kind="factor_candidate", statement="new", opened_by="manual", need_cluster="cooling", priority=0.9),
        estimate_priority=True,
    )
    row = store.get(new_qid)
    assert row["priority"] < 0.9  # real repeated-failure history overrides the caller's optimistic default


# ------------------------------------------------- Stage 2.5: richer ledger metrics


def test_source_value_ledger_tracks_redundancy_and_novelty(isolated_config):
    ledger = SourceValueLedger()
    ledger.record("source_a", "cooling", passed=True, redundant=True, novel=True, compute_cost_ms=120.0)
    ledger.record("source_a", "cooling", passed=True, redundant=False, novel=False, compute_cost_ms=80.0)
    row = ledger.get("source_a", "cooling")
    assert row["redundancy_count"] == 1
    assert row["novelty_count"] == 1
    assert row["compute_cost_ms_avg"] is not None


def test_learned_facts_are_real_sentences_from_stored_numbers(isolated_config):
    ledger = SourceValueLedger()
    for _ in range(9):
        ledger.record("bad_source", "rain_protection", passed=False)
    ledger.record("bad_source", "rain_protection", passed=True)
    facts = ledger.learned_facts("bad_source")
    assert len(facts) == 1
    assert "rain_protection" in facts[0]
    assert "confirmed poor" in facts[0]
    assert "1/10" in facts[0]


# --------------------------------------------- active question generation


def test_on_unknown_recorded_opens_a_real_question(isolated_config):
    from elecous.infrastructure.event_bus import Event, EventType

    questions = ResearchQuestionStore()
    ledger = SourceValueLedger()
    engine = ResearchEngine(questions, ledger, questions.experience, None, None, None, None)
    engine.on_unknown_recorded(
        Event(
            event_type=EventType.UNKNOWN_RECORDED, source="knowledge",
            payload={
                "kind": "unknown_mechanism", "description": "why is mechanism X declining?",
                "unknown_id": "unk_1", "context": {"mechanism_id": "mech_1", "category": "cooling"},
                "research_priority": 0.7,
            },
        )
    )
    open_qs = questions.list(status="open")
    assert len(open_qs) == 1
    assert open_qs[0]["kind"] == "mechanism_candidate"
    assert open_qs[0]["subject_ref"] == "mech_1"
    assert open_qs[0]["unknown_id"] == "unk_1"


def test_on_unknown_recorded_is_idempotent(isolated_config):
    from elecous.infrastructure.event_bus import Event, EventType

    questions = ResearchQuestionStore()
    ledger = SourceValueLedger()
    engine = ResearchEngine(questions, ledger, questions.experience, None, None, None, None)
    event = Event(
        event_type=EventType.UNKNOWN_RECORDED, source="knowledge",
        payload={"kind": "missing_observations", "description": "d", "unknown_id": "unk_2", "context": {"capability": "weather"}},
    )
    engine.on_unknown_recorded(event)
    engine.on_unknown_recorded(event)
    assert len(questions.list(status="open")) == 1


# ------------------------------------------------- capability fallback (Req 1 planner)


def test_capabilities_for_falls_back_to_all_active_when_no_history(isolated_config):
    from elecous.research.priority import capabilities_for

    registry = _FakeRegistry([
        {"source_id": "s1", "observation_capability": "weather", "status": "active"},
        {"source_id": "s2", "observation_capability": "media", "status": "active"},
    ])
    ledger = SourceValueLedger()
    assert capabilities_for("brand_new_cluster", ledger, registry.list(status="active")) == ["media", "weather"]


def test_capabilities_for_uses_real_history_when_present(isolated_config):
    from elecous.research.priority import capabilities_for

    registry = _FakeRegistry([
        {"source_id": "s1", "observation_capability": "weather", "status": "active"},
        {"source_id": "s2", "observation_capability": "media", "status": "active"},
    ])
    ledger = SourceValueLedger()
    ledger.record("s1", "cooling", passed=True)
    assert capabilities_for("cooling", ledger, registry.list(status="active")) == ["weather"]


# ------------------------------------------------- dynamic stop rule (Req 3/8)


def test_tick_stops_remaining_plan_after_a_zero_yield_source(isolated_config):
    registry = _FakeRegistry([
        {"source_id": "source_a", "observation_capability": "weather", "status": "active"},
        {"source_id": "source_b", "observation_capability": "weather", "status": "active"},
    ])
    questions = ResearchQuestionStore()
    questions.open(
        ResearchQuestion(
            kind="factor_candidate", statement="q", opened_by="manual", need_cluster="cooling",
            expected_evidence={"capabilities": ["weather"]},
        )
    )
    ledger = SourceValueLedger()
    network = _FakeNetwork(accepted=0)  # every real call yields nothing
    engine = ResearchEngine(questions, ledger, questions.experience, network, _FakeHealth(), registry, _FakeResources())

    summary = engine.tick(unit_budget=10.0)
    assert len(network.calls) == 1  # stopped after the first zero-yield call, not both planned sources
    assert len(summary["sources_skipped_zero_yield"]) >= 1


# ============================================================ Stage 7: experiments.py

from elecous.research.experiments import ExperimentEngine  # noqa: E402


class _FakeEpisodeStore:
    def __init__(self, episodes_by_entity=None):
        self._by_entity = episodes_by_entity or {}
        self.calls = []

    def list_latest(self, entity=None, **kwargs):
        self.calls.append(entity)
        return self._by_entity.get(entity, [])


class _FakeStates:
    def __init__(self, states):
        self._states = states
        self.generalize_all_calls = 0

    def list_all(self):
        return self._states

    def generalize_all(self, factors, episodes):
        self.generalize_all_calls += 1
        return self._states


class _FakeConcepts:
    def __init__(self, concepts):
        self._concepts = concepts
        self.evolve_calls = 0

    def list_all(self, status=None):
        return [c for c in self._concepts if status is None or c["status"] == status]

    def evolve(self, mechanisms, states):
        self.evolve_calls += 1
        return self._concepts


class _FakeMechanismEngine:
    """Stands in for Stage 4's real explained_and_unexplained gap-detection
    (Requirement 8) -- returns whatever unexplained/explained sets the test
    wires in, so ExperimentEngine's candidate-selection logic is tested
    independent of the real query it composes with (Task 9: reuse, not
    reimplement, so nothing new to test here beyond the composition)."""

    def __init__(self, unexplained_by_state=None):
        self._unexplained_by_state = unexplained_by_state or {}

    def explained_and_unexplained(self, state_id):
        return {"unexplained": self._unexplained_by_state.get(state_id, []), "explained": []}


def test_design_experiment_targets_a_real_untested_entity(isolated_config):
    questions = ResearchQuestionStore()
    episodes = _FakeEpisodeStore({"already_tested": [{"episode_id": "e1"}]})
    mechanism_engine = _FakeMechanismEngine({"wcn_1": ["already_tested", "untested_entity"]})
    engine = ExperimentEngine(questions, _FakeStates([]), _FakeConcepts([]), episodes, factors=None, mechanism_engine=mechanism_engine)
    state = {
        "state_id": "wcn_1", "label": "STATE-A", "confidence": 0.6,
        "member_factor_ids": ["fac_1", "fac_2"],
    }
    qid = engine.design_experiment("state", state)
    assert qid is not None
    row = questions.get(qid)
    assert row["expected_evidence"]["target_entity"] == "untested_entity"
    assert "if it does not" in row["statement"]
    assert row["kind"] == "experiment"


def test_design_experiment_returns_none_when_everything_already_tested(isolated_config):
    questions = ResearchQuestionStore()
    episodes = _FakeEpisodeStore({"a": [{"episode_id": "e1"}], "b": [{"episode_id": "e2"}]})
    mechanism_engine = _FakeMechanismEngine({"wcn_1": ["a", "b"]})  # real unexplained candidates, both already tested
    engine = ExperimentEngine(questions, _FakeStates([]), _FakeConcepts([]), episodes, factors=None, mechanism_engine=mechanism_engine)
    state = {"state_id": "wcn_1", "label": "STATE-A", "confidence": 0.6, "member_factor_ids": []}
    assert engine.design_experiment("state", state) is None


def test_resolve_due_supported_when_real_episode_appeared(isolated_config):
    questions = ResearchQuestionStore()
    episodes = _FakeEpisodeStore({"target_x": [{"episode_id": "e1"}]})
    states = _FakeStates([])
    engine = ExperimentEngine(questions, states, _FakeConcepts([]), episodes, factors=None, mechanism_engine=None)
    past_deadline = timeutil.to_iso(timeutil.utcnow() - timedelta(days=1))
    qid = questions.open(ResearchQuestion(
        kind="experiment", statement="s", opened_by="experiment_engine",
        expected_evidence={"target_entity": "target_x", "deadline": past_deadline, "hypothesis_type": "state", "hypothesis_id": "wcn_1"},
    ))
    resolved = engine.resolve_due()
    assert len(resolved) == 1
    assert resolved[0]["resolution"] == "supported"
    assert questions.get(qid)["status"] == "resolved"
    assert states.generalize_all_calls == 1  # Task 4: real hypothesis re-evolution triggered


def test_resolve_due_refuted_and_not_reusable_when_no_real_episode(isolated_config):
    questions = ResearchQuestionStore()
    episodes = _FakeEpisodeStore({})  # target entity never got a real episode
    engine = ExperimentEngine(questions, _FakeStates([]), _FakeConcepts([]), episodes, factors=None, mechanism_engine=None)
    past_deadline = timeutil.to_iso(timeutil.utcnow() - timedelta(days=1))
    qid = questions.open(ResearchQuestion(
        kind="experiment", statement="s", opened_by="experiment_engine", need_cluster="STATE-A",
        expected_evidence={"target_entity": "never_spiked", "deadline": past_deadline, "hypothesis_type": "state", "hypothesis_id": "wcn_1"},
    ))
    resolved = engine.resolve_due()
    assert resolved[0]["resolution"] == "refuted"
    experience = questions.experience.similar_attempts(need_cluster="STATE-A")
    matching = [e for e in experience if e["question_id"] == qid]
    assert matching and matching[0]["strategy"]["root_cause"] == "not_reusable"


def test_resolve_due_skips_experiments_not_yet_at_deadline(isolated_config):
    questions = ResearchQuestionStore()
    engine = ExperimentEngine(questions, _FakeStates([]), _FakeConcepts([]), _FakeEpisodeStore({}), factors=None, mechanism_engine=None)
    future_deadline = timeutil.to_iso(timeutil.utcnow() + timedelta(days=30))
    questions.open(ResearchQuestion(
        kind="experiment", statement="s", opened_by="experiment_engine",
        expected_evidence={"target_entity": "x", "deadline": future_deadline, "hypothesis_type": "state", "hypothesis_id": "wcn_1"},
    ))
    assert engine.resolve_due() == []


def test_design_discriminating_experiment_targets_the_disagreement(isolated_config):
    questions = ResearchQuestionStore()
    episodes = _FakeEpisodeStore({})
    engine = ExperimentEngine(questions, _FakeStates([]), _FakeConcepts([]), episodes, factors=None, mechanism_engine=None)
    hyp_a = {"state_id": "wcn_a", "label": "STATE-A", "generalized_group": ["shared", "only_a"]}
    hyp_b = {"state_id": "wcn_b", "label": "STATE-B", "generalized_group": ["shared", "only_b"]}
    qid = engine.design_discriminating_experiment(hyp_a, "state", hyp_b, "state")
    row = questions.get(qid)
    assert row["expected_evidence"]["target_entity"] in ("only_a", "only_b")  # never the shared, non-discriminating entity


def test_prioritize_orders_by_real_information_gain_per_cost():
    rows = [
        {"expected_evidence": {"information_gain_per_cost": 0.2}},
        {"expected_evidence": {"information_gain_per_cost": 0.9}},
        {"expected_evidence": {"information_gain_per_cost": 0.5}},
    ]
    ranked = ExperimentEngine.prioritize(rows)
    assert [r["expected_evidence"]["information_gain_per_cost"] for r in ranked] == [0.9, 0.5, 0.2]


def test_run_scientific_cycle_designs_and_resolves_in_one_real_pass(isolated_config):
    questions = ResearchQuestionStore()
    episodes = _FakeEpisodeStore({"tested": [{"episode_id": "e1"}]})
    states = _FakeStates([{
        "state_id": "wcn_1", "label": "STATE-A", "status": "active", "confidence": 0.6,
        "member_factor_ids": ["fac_1"],
    }])
    mechanism_engine = _FakeMechanismEngine({"wcn_1": ["tested", "fresh_entity"]})
    engine = ExperimentEngine(questions, states, _FakeConcepts([]), episodes, factors=None, mechanism_engine=mechanism_engine)
    result = engine.run_scientific_cycle()
    assert result["experiments_designed"] == 1
    open_experiments = [q for q in questions.list(status="open") if q["kind"] == "experiment"]
    assert len(open_experiments) == 1
    assert open_experiments[0]["expected_evidence"]["target_entity"] == "fresh_entity"
    # A second real pass must not open a duplicate for the same hypothesis.
    result2 = engine.run_scientific_cycle()
    assert result2["experiments_designed"] == 0
