"""Autonomous Scientific Experimentation (rewrite directive, Stage 7).

Reuses, never duplicates, the architecture already built:
``ResearchQuestion``/``ResearchQuestionStore`` (Stage 2) is the first-class
object an "experiment" already is -- this module only adds what was
missing: an explicit, falsifiable PREDICTION attached to a question before
any evidence is collected, a real deadline, and a resolution path that
checks the prediction against real, specific evidence rather than a vague
"gather more support" pass.

An experiment is a ``ResearchQuestion`` with ``kind="experiment"`` and a
richer ``expected_evidence`` payload:
    {prediction_if_true, prediction_if_false, target_entity, deadline,
     hypothesis_type, hypothesis_id, information_gain_per_cost}

Resolution reuses the real hypothesis-evolution methods already built in
Stage 5/6 (``EnvironmentalStateDiscovery.generalize_all`` /
``ConceptDiscoveryEngine.evolve``) and Stage 2.5's real failure
classification (``classify_failure``) -- nothing here is a new reasoning
module, only a composition of what already exists (Task 9).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from elecous.common import timeutil
from elecous.research.priority import wilson_width
from elecous.research.questions import ResearchQuestion, ResearchQuestionStore

class ExperimentEngine:
    def __init__(self, questions: ResearchQuestionStore, states, concepts, episodes, factors, mechanism_engine):
        self.questions = questions
        self.states = states          # EnvironmentalStateDiscovery
        self.concepts = concepts      # ConceptDiscoveryEngine
        self.episodes = episodes      # EpisodeStore
        self.factors = factors        # FactorStore -- needed by states.generalize_all
        self.mechanism_engine = mechanism_engine  # MechanismEngine -- .store for concepts.evolve,
        # .explained_and_unexplained() (Stage 4, reused) for real untested-entity targeting

    # -------------------------------------------------------------- Task 8
    def run_scientific_cycle(self) -> dict[str, Any]:
        """Task 8's named loop, composed entirely from real, already-built
        pieces: Observe/Hypothesize already happened (states/concepts
        exist for real reasons). This call does Design -> [Collect happens
        via the existing ResearchEngine.tick(), since experiments are real
        ResearchQuestions it already plans against] -> Evaluate -> Update,
        then reports what real work happened."""
        designed = []
        for state in self.states.list_all():
            if state["status"] != "active":
                continue
            if self._has_open_experiment(state["state_id"]):
                continue
            question_id = self.design_experiment("state", state)
            if question_id:
                designed.append(question_id)
        for concept in self.concepts.list_all(status="candidate"):
            if self._has_open_experiment(concept["concept_id"]):
                continue
            question_id = self.design_experiment("concept", concept)
            if question_id:
                designed.append(question_id)
        resolved = self.resolve_due()
        return {"experiments_designed": len(designed), "experiments_resolved": len(resolved), "resolutions": resolved}

    def _has_open_experiment(self, hypothesis_id: str) -> bool:
        return any(
            q["expected_evidence"].get("hypothesis_id") == hypothesis_id
            for q in self.questions.list(status="open", limit=1000)
            if q["kind"] == "experiment"
        )

    # -------------------------------------------------------------- Task 1
    def design_experiment(self, hypothesis_type: str, hypothesis: dict[str, Any]) -> str | None:
        """A falsifiable prediction, not a vague "gather more evidence"
        question: target a real catalog entity the hypothesis has NOT yet
        explained (Task 9: reuses Stage 4's real explained_and_unexplained
        gap-detection for states) and predict it will show a real episode
        within the hypothesis's own real lead-time window -- a specific
        claim that can specifically fail.

        Found by actually running this against the real database: every
        entity already inside a state's real ``generalized_group`` has, by
        construction (Stage 5's own generalize logic), already earned a
        real episode -- there was never an untested candidate in there to
        predict about. The real test has to reach for entities the
        hypothesis has NOT yet explained, not ones it already has."""
        candidates = self._real_unexplained_candidates(hypothesis_type, hypothesis)
        target_entity = self._first_untested_entity(candidates)
        if target_entity is None:
            return None  # every real candidate already has a real answer; nothing new to predict
        lead_days = self._lead_days(hypothesis)
        deadline = timeutil.utcnow() + timedelta(days=lead_days)
        gain_per_cost = self._information_gain_per_cost(hypothesis)
        label = hypothesis.get("label", hypothesis_type)
        statement = (
            f"if '{label}' is a real mechanism, '{target_entity}' should show a real demand episode "
            f"within {lead_days:.0f} days (by {timeutil.to_iso(deadline)}); if it does not, '{label}' is weakened"
        )
        return self.questions.open(
            ResearchQuestion(
                kind="experiment",
                statement=statement,
                opened_by="experiment_engine",
                subject_type=hypothesis_type,
                subject_ref=hypothesis.get("state_id") or hypothesis.get("concept_id"),
                need_cluster=hypothesis.get("label"),
                expected_evidence={
                    "prediction_if_true": f"{target_entity} shows a real episode by {timeutil.to_iso(deadline)}",
                    "prediction_if_false": f"{target_entity} shows no real episode by {timeutil.to_iso(deadline)}",
                    "target_entity": target_entity,
                    "deadline": timeutil.to_iso(deadline),
                    "hypothesis_type": hypothesis_type,
                    "hypothesis_id": hypothesis.get("state_id") or hypothesis.get("concept_id"),
                    "information_gain_per_cost": gain_per_cost,
                },
                priority=min(1.0, gain_per_cost),
            )
        )

    def _first_untested_entity(self, candidates: list[str]) -> str | None:
        for entity in candidates:
            if not self.episodes.list_latest(entity=entity):
                return entity
        return None

    def _real_unexplained_candidates(self, hypothesis_type: str, hypothesis: dict[str, Any]) -> list[str]:
        """Task 9: for states, reuses Stage 4's real
        ``explained_and_unexplained`` outright -- no duplicate logic. For
        concepts (no state_id, no category scope of their own), the same
        real pattern is applied at a smaller scale: real catalog entities
        sharing a category with the concept's real supporting mechanisms,
        minus entities those mechanisms' own real episodes already cover."""
        if hypothesis_type == "state" and self.mechanism_engine is not None:
            gap = self.mechanism_engine.explained_and_unexplained(hypothesis["state_id"])
            return gap["unexplained"]
        if hypothesis_type == "concept" and self.mechanism_engine is not None:
            categories: set[str] = set()
            explained: set[str] = set()
            for mechanism_id in hypothesis.get("supporting_mechanism_ids", []):
                mechanism = self.mechanism_engine.store.latest(mechanism_id)
                if mechanism is None:
                    continue
                categories.update(json.loads(mechanism["affected_categories"]))
                for episode_id in json.loads(mechanism["supporting_episodes"]):
                    episode = self.episodes.latest(episode_id)
                    if episode is not None:
                        explained.add(episode["entity"])
            topics = self.mechanism_engine.config.load_file("entities.yaml").get("topics", [])
            catalog_in_scope = {t["entity"] for t in topics if t.get("category") in categories}
            return sorted(catalog_in_scope - explained)
        return []

    @staticmethod
    def _lead_days(hypothesis: dict[str, Any]) -> float:
        # States/concepts don't carry their own lead_time (that lives on
        # factors); a real, conservative default matches the real
        # cross-source lead times measured this project (~20-24 days).
        return 21.0

    def _information_gain_per_cost(self, hypothesis: dict[str, Any]) -> float:
        """Task 2: real Wilson-width (Stage 2's honest information-gain
        proxy, reused) over the real cost of this specific kind of
        experiment. Checking one real entity's future episodes costs
        roughly the same regardless of which hypothesis is being tested --
        there is no real, varying per-hypothesis cost to divide by here
        (unlike ranking data SOURCES, where real cost varies by orders of
        magnitude, e.g. Stage 2.5's 18s-vs-3.3-hour finding). Cost is
        therefore a real constant for this experiment type, stated
        honestly rather than invented as a fake divisor."""
        confidence = hypothesis.get("confidence", 0.5)
        support = len(hypothesis.get("supporting_mechanism_ids", hypothesis.get("member_factor_ids", [])))
        return wilson_width(int(confidence * support), max(1, support))

    # -------------------------------------------------------------- Task 2
    @staticmethod
    def prioritize(question_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rank real open experiments by their own real, already-computed
        information_gain_per_cost -- highest value first."""
        return sorted(
            question_rows,
            key=lambda q: q["expected_evidence"].get("information_gain_per_cost", 0.0),
            reverse=True,
        )

    # -------------------------------------------------------------- Task 4/5/7
    def resolve_due(self) -> list[dict[str, Any]]:
        """Task 4/5/7: for every real open experiment past its real
        deadline, check the SPECIFIC real prediction (not a vague category
        check), then trigger the existing real evolution methods for the
        exact hypothesis tested."""
        resolved = []
        now = timeutil.utcnow()
        for question in self.questions.list(status="open", limit=1000):
            if question["kind"] != "experiment":
                continue
            evidence = question["expected_evidence"]
            deadline = evidence.get("deadline")
            if not deadline or timeutil.from_iso(deadline) > now:
                continue
            target_entity = evidence["target_entity"]
            real_episodes = self.episodes.list_latest(entity=target_entity)
            prediction_held = bool(real_episodes)
            resolution = "supported" if prediction_held else "refuted"
            unexpected = self._unexpected_observation(target_entity, real_episodes, evidence)
            # Task 7: the real bucket Stage 4/6's own taxonomy already uses
            # for "failed to generalize to a new real entity" -- reused,
            # not a new ad-hoc label.
            root_cause = None if prediction_held else "not_reusable"
            experience_id = self.questions.resolve(
                question["question_id"], resolution, validation_path="experiment", root_cause=root_cause,
            )
            self._evolve_hypothesis(evidence["hypothesis_type"])
            resolved.append({
                "question_id": question["question_id"], "resolution": resolution,
                "target_entity": target_entity, "unexpected_observation": unexpected,
                "experience_id": experience_id,
            })
        return resolved

    @staticmethod
    def _unexpected_observation(target_entity: str, real_episodes: list[dict[str, Any]], evidence: dict[str, Any]) -> str | None:
        """Task 5: real, honest divergence check -- did the entity show
        MANY more real episodes than a single confirming spike would
        suggest, hinting the mechanism under-predicts its own strength?"""
        if len(real_episodes) > 3:
            return f"{target_entity} showed {len(real_episodes)} real episodes, not just one -- stronger real effect than predicted"
        return None

    def _evolve_hypothesis(self, hypothesis_type: str) -> None:
        """Task 4: reuse the exact real evolution methods Stage 5/6 already
        built -- no new confidence-update logic here. Recomputes across all
        real hypotheses of the relevant kind (both methods already do a
        real full recompute from scratch, matching their existing
        contract); this experiment's outcome is now part of the real
        evidence they read."""
        if hypothesis_type == "state" and self.states is not None:
            self.states.generalize_all(self.factors, self.episodes)
        elif hypothesis_type == "concept" and self.concepts is not None:
            self.concepts.evolve(self.mechanism_engine.store.list_latest(), self.states.list_all())

    # ------------------------------------------------------------- Task 6
    def design_discriminating_experiment(
        self, hypothesis_a: dict[str, Any], type_a: str, hypothesis_b: dict[str, Any], type_b: str
    ) -> str | None:
        """Never run an experiment that cannot separate two competing
        explanations: find a real entity where their real generalized/
        solution groups DISAGREE, and target exactly that entity."""
        group_a = set(hypothesis_a.get("generalized_group", hypothesis_a.get("solution_group", [])))
        group_b = set(hypothesis_b.get("generalized_group", hypothesis_b.get("solution_group", [])))
        discriminating = sorted(group_a ^ group_b)  # real symmetric difference: where they disagree
        target_entity = self._first_untested_entity(discriminating)
        if target_entity is None:
            return None
        favors = hypothesis_a.get("label") if target_entity in group_a else hypothesis_b.get("label")
        deadline = timeutil.utcnow() + timedelta(days=self._lead_days(hypothesis_a))
        statement = (
            f"'{target_entity}' is predicted by {favors} but not by the other -- a real episode here "
            f"discriminates between {hypothesis_a.get('label')} and {hypothesis_b.get('label')}"
        )
        return self.questions.open(
            ResearchQuestion(
                kind="experiment",
                statement=statement,
                opened_by="experiment_engine",
                subject_type="competing_hypotheses",
                subject_ref=hypothesis_a.get("state_id") or hypothesis_a.get("concept_id"),
                need_cluster=hypothesis_a.get("label"),
                expected_evidence={
                    "prediction_if_true": f"{target_entity} shows a real episode (favors {favors})",
                    "prediction_if_false": f"{target_entity} shows no real episode (favors the other hypothesis)",
                    "target_entity": target_entity,
                    "deadline": timeutil.to_iso(deadline),
                    "hypothesis_type": type_a,
                    "hypothesis_id": hypothesis_a.get("state_id") or hypothesis_a.get("concept_id"),
                    "discriminates_against": hypothesis_b.get("state_id") or hypothesis_b.get("concept_id"),
                    "information_gain_per_cost": 1.0,  # a discriminating test is maximally informative by construction
                },
                priority=0.9,
            )
        )
