"""Research Engine facade (rewrite directive, Stage 2).

Composes the Stage 1 stores with Stage 2's planning (``priority.py``) into
the actual behavior loop: pull open questions, plan evidence within a real
compute budget, collect only what was planned, persist the real rationale
(Requirement 9), and resolve questions reactively when the Validation Engine
-- which retains sole authority to promote/demote -- judges the object they
were about.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import Event
from elecous.infrastructure.logging import get_logger
from elecous.research.experience import ResearchExperienceStore, classify_failure
from elecous.research.priority import capabilities_for, plan_evidence
from elecous.research.progress import ProgressReporter
from elecous.research.questions import ResearchQuestion, ResearchQuestionStore
from elecous.research.source_value import SourceValueLedger


class _MemoizedHealth:
    """Avoids recomputing the same source's health (a real scan of up to
    50 connector_runs rows) once per question in a single tick when
    several open questions share candidate sources -- pure redundant-work
    removal, same real numbers, fewer real scans."""

    def __init__(self, health):
        self._health = health
        self._cache: dict[str, Any] = {}

    def score(self, source_id: str):
        if source_id not in self._cache:
            self._cache[source_id] = self._health.score(source_id)
        return self._cache[source_id]


class ResearchEngine:
    def __init__(
        self,
        questions: ResearchQuestionStore,
        source_value: SourceValueLedger,
        experience: ResearchExperienceStore,
        network,
        health,
        registry,
        resources=None,
        config: Config | None = None,
        databases=None,
    ):
        self.questions = questions
        self.source_value = source_value
        self.experience = experience
        self.network = network
        self.health = health
        self.registry = registry
        self.resources = resources
        self.config = config or active_config()
        self.databases = databases
        self.log = get_logger("research")

    def tick(self, unit_budget: float | None = None) -> dict[str, Any]:
        """One planning cycle (Requirement 3/4/7/9). Returns a real summary
        -- every number in it is what actually happened this call, not an
        estimate."""
        if unit_budget is None:
            unit_budget = float(self.config.get("research.units_per_cycle", 20))
        remaining = unit_budget
        summary: dict[str, Any] = {
            "questions_considered": 0, "sources_queried": [], "sources_skipped_zero_yield": [],
            "budget_spent": 0.0, "paused": False,
        }
        open_questions = self.questions.list(status="open")
        progress = ProgressReporter(len(open_questions), "research:tick")
        health = _MemoizedHealth(self.health)
        # One real registry scan for the whole tick, not one per question
        # (redundant-work removal -- registry.list() is a real SQL query).
        active_sources = self.registry.list(status="active")

        for question in open_questions:
            progress.tick()
            if remaining <= 0:
                break
            if self.resources is not None and self.resources.should_pause():
                summary["paused"] = True
                break
            capabilities = question["expected_evidence"].get("capabilities") or capabilities_for(
                question["need_cluster"], self.source_value, active_sources
            )
            candidate_sources = sorted({
                s["source_id"] for s in active_sources if s["observation_capability"] in capabilities
            })
            if not candidate_sources:
                continue

            # Lessons are WRITTEN under need_cluster (experience.py: applies_to =
            # need_cluster or "unknown") -- reading by kind retrieved nothing, ever
            # (mission-audit D4). Read by the same key the writer uses.
            lessons = self.experience.lessons_for(question["need_cluster"] or "unknown")
            plan = plan_evidence(
                need_cluster=question["need_cluster"] or "unknown",
                candidate_sources=candidate_sources,
                ledger=self.source_value,
                health=health,
                lessons=lessons,
                unit_budget=remaining,
                now=timeutil.utcnow(),
            )
            real_costs = []
            for source_id in plan.sources:
                if self.resources is not None and self.resources.should_pause():
                    summary["paused"] = True
                    break
                # Real multi-dimensional cost (Requirement 6): actual CPU/
                # memory delta around the actual call, via the existing,
                # real ResourceManager.sample() -- not a modeled estimate.
                before_sample = self.resources.sample() if self.resources is not None else None
                started = timeutil.utcnow()
                result = self.network.run_source(source_id, mode="live")
                after_sample = self.resources.sample() if self.resources is not None else None
                real_costs.append({
                    "source_id": source_id,
                    "wall_clock_seconds": (timeutil.utcnow() - started).total_seconds(),
                    "cpu_percent_delta": (
                        (after_sample["process_cpu_percent"] - before_sample["process_cpu_percent"])
                        if before_sample and after_sample else None
                    ),
                    "memory_mb_delta": (
                        (after_sample["process_memory_mb"] - before_sample["process_memory_mb"])
                        if before_sample and after_sample else None
                    ),
                })
                self.questions.spend_budget(question["question_id"], 1.0)
                remaining -= 1.0
                summary["sources_queried"].append(source_id)
                summary["budget_spent"] += 1.0
                # Dynamic stop rule (Requirement 3/8): this specific real
                # call just produced zero accepted observations -- expected
                # information gain from it was realized as zero. Continuing
                # to the rest of this question's planned sources this same
                # tick is spending budget on evidence already shown, this
                # run, not to exist yet; skip them and move to the next
                # question instead of blindly exhausting the plan.
                accepted = (result or {}).get("stats", {}).get("accepted", 0)
                if not accepted:
                    skipped = [s for s in plan.sources if s not in summary["sources_queried"]]
                    summary["sources_skipped_zero_yield"].extend(skipped)
                    break

            self._persist_rationale(question["question_id"], question["expected_evidence"], plan, real_costs)
            summary["questions_considered"] += 1
            if summary["paused"]:
                break

        self.log.info("research tick complete", **{k: v for k, v in summary.items() if k != "sources_queried"})
        return summary

    _UNKNOWN_KIND_TO_QUESTION_KIND = {
        "unknown_mechanism": "mechanism_candidate",
        "unexplained_opportunity": "factor_candidate",
        "missing_observations": "source_evaluation",
        "failed_validation": "validation_path_retry",
    }

    def on_unknown_recorded(self, event: Event) -> None:
        """Active question generation: every surfaced unknown (declining
        mechanism, contradicted factor, persistent gap, failed validation)
        becomes a real, budgeted Research Question instead of sitting
        inert in the Unknown Repository -- the bridge Stage 1 built the
        payload for but never wired."""
        unknown_id = event.payload.get("unknown_id")
        kind = event.payload.get("kind")
        description = event.payload.get("description")
        if not unknown_id or not kind:
            return
        context = event.payload.get("context") or {}
        need_cluster = context.get("category") or context.get("capability")
        self.questions.open(
            ResearchQuestion(
                kind=self._UNKNOWN_KIND_TO_QUESTION_KIND.get(kind, "custom"),
                statement=description or f"investigate {kind}",
                opened_by="curiosity",
                subject_type=(
                    "mechanism" if "mechanism_id" in context
                    else "environmental_factor" if "factor_id" in context
                    else None
                ),
                subject_ref=context.get("mechanism_id") or context.get("factor_id"),
                unknown_id=unknown_id,
                need_cluster=need_cluster,
                expected_evidence={"capabilities": [context["capability"]]} if "capability" in context else {},
                priority=event.payload.get("research_priority", 0.5),
            ),
            estimate_priority=need_cluster is not None,
        )

    def on_validation_completed(self, event: Event) -> None:
        """Reactively resolve any open question about the object the
        Validation Engine just judged -- resolution authority stays with
        Validation (spec principle); this only records that the question is
        now answered, producing one real experience row (Requirement 5)."""
        object_type = event.payload.get("object_type")
        object_id = event.payload.get("object_id")
        outcome = event.payload.get("outcome")
        if not object_type or not object_id:
            return
        matches = [
            q for q in self.questions.list(status="open")
            if q["subject_type"] == object_type and q["subject_ref"] == object_id
        ] + [
            q for q in self.questions.list(status="investigating")
            if q["subject_type"] == object_type and q["subject_ref"] == object_id
        ]
        resolution = "supported" if outcome == "passed" else "refuted"
        root_cause = None
        if resolution == "refuted" and self.databases is not None:
            root_cause = self._real_root_cause(object_type, object_id, event.payload.get("method"))
        for question in matches:
            # confidence_after is not carried on VALIDATION_COMPLETED's
            # payload today (only object_type/object_id/method/outcome) --
            # left None rather than invented.
            self.questions.resolve(
                question["question_id"], resolution, confidence_after=None,
                validation_path=event.payload.get("method", "unknown"), root_cause=root_cause,
            )

    def _real_root_cause(self, object_type: str, object_id: str, method: str | None) -> str | None:
        """Requirement 9: classify the real, already-stored check detail
        for the most recent matching validation row -- returns None (not a
        guess) if no such row can be found."""
        validation_db = self.databases.get("validation")
        rows = validation_db.query_dicts(
            "SELECT * FROM validations WHERE object_type = ? AND object_id = ? AND method = ? "
            "ORDER BY created_at DESC LIMIT 1",
            [object_type, object_id, method],
        )
        return classify_failure(rows[0]) if rows else None

    def _persist_rationale(
        self, question_id: str, expected_evidence: dict[str, Any], plan, real_costs: list[dict[str, Any]]
    ) -> None:
        updated = dict(expected_evidence)
        updated["rationale"] = plan.rationale
        updated["sources"] = plan.sources
        # Accumulate real per-call CPU/memory/latency measurements
        # (Requirement 6) so resolve() can fold them into the experience
        # row's cost -- appended across ticks, not overwritten.
        updated["measured_costs"] = expected_evidence.get("measured_costs", []) + real_costs
        self.questions.db.execute(
            "UPDATE research_questions SET expected_evidence = ?, updated_at = ? WHERE question_id = ?",
            [json.dumps(updated), timeutil.utcnow(), question_id],
        )
