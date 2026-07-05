"""Research Question store (rewrite directive, Level 2, stage 1).

A Research Question generalizes the Curiosity Engine's three hardcoded
surfacing rules (declining mechanisms, contradicted factors, persistent
gaps) into one answerable object: "is [subject] real, and what evidence
would move confidence?" It does not replace ``unknown_memory`` (spec 7.19)
-- it sits one layer above it and, once wired (stage 2+), is what turns a
noticed anomaly into a prioritized, budgeted evidence-collection action.

Stage 1 scope: pure CRUD over the schema in ``research/schema.py``, exactly
mirroring the store pattern already used by ``SourceRegistry``
(``elecous/observation/registry/registry.py``) and ``KnowledgeMemory``. Not
yet wired into the scheduler, event bus, or bootstrap -- see the staged
rollout in docs/decisions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.research.experience import ResearchExperienceStore
from elecous.research.priority import expected_value
from elecous.research.schema import RESEARCH_MIGRATION
from elecous.storage.database import DatabaseManager, global_databases

OPEN_STATUSES = ("open", "investigating")


@dataclass
class ResearchQuestion:
    kind: str  # mechanism_candidate|factor_candidate|validation_path_retry|source_evaluation|entity_causal_check|custom
    statement: str
    opened_by: str
    subject_type: str | None = None
    subject_ref: str | None = None
    unknown_id: str | None = None
    need_cluster: str | None = None
    expected_evidence: dict[str, Any] = field(default_factory=dict)
    evidence_plan_source: str = "default_heuristic"
    priority: float = 0.5


class ResearchQuestionStore:
    def __init__(
        self,
        databases: DatabaseManager | None = None,
        config: Config | None = None,
        experience: ResearchExperienceStore | None = None,
    ):
        self.databases = databases or global_databases()
        self.databases.register_schema(RESEARCH_MIGRATION)
        self.db = self.databases.get("research")
        self.config = config or active_config()
        self.log = get_logger("research")
        # Lazily constructed default keeps existing callers (Stage 1 tests,
        # any code that only needs question CRUD) unaffected -- resolve()
        # gains experience recording without requiring every caller to wire
        # a ResearchExperienceStore explicitly.
        self.experience = experience or ResearchExperienceStore(self.databases, self.config)

    def open(self, question: ResearchQuestion, estimate_priority: bool = False) -> str:
        """Open a new question, or return the id of an existing open question
        for the same ``unknown_id`` (idempotent bridge from Curiosity — a
        surfaced anomaly should not spawn duplicate questions every cycle).

        ``estimate_priority`` (Stage 2.5, Requirement 3): when true, real
        historical experience for this (need_cluster, kind) -- via
        ``priority.expected_value`` -- sets the opening priority, so a
        question with a real track record of costly, repeated failure
        starts deprioritized from the moment it exists, not only after
        plan_evidence re-scores it later."""
        if question.unknown_id:
            existing = self.db.query_one(
                "SELECT question_id FROM research_questions WHERE unknown_id = ? AND status IN ('open','investigating')",
                [question.unknown_id],
            )
            if existing:
                return existing[0]
        priority = question.priority
        if estimate_priority:
            p_success, _ = expected_value(question.need_cluster, question.kind, self.experience)
            priority = p_success
        question_id = new_id("research_question")
        now = timeutil.utcnow()
        self.db.execute(
            "INSERT INTO research_questions VALUES (" + ", ".join("?" * 19) + ")",
            [
                question_id,
                question.kind,
                question.statement,
                question.subject_type,
                question.subject_ref,
                question.unknown_id,
                question.need_cluster,
                json.dumps(question.expected_evidence),
                question.evidence_plan_source,
                priority,
                "open",
                None,
                None,
                None,
                0.0,
                question.opened_by,
                now,
                now,
                None,
            ],
        )
        self.log.info("research question opened", question_id=question_id, kind=question.kind)
        return question_id

    def get(self, question_id: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts("SELECT * FROM research_questions WHERE question_id = ?", [question_id])
        return self._decode(rows[0]) if rows else None

    def list(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM research_questions", []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY priority DESC, created_at ASC LIMIT ?"
        params.append(limit)
        return [self._decode(r) for r in self.db.query_dicts(sql, params)]

    def set_status(self, question_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE research_questions SET status = ?, updated_at = ? WHERE question_id = ?",
            [status, timeutil.utcnow(), question_id],
        )

    def spend_budget(self, question_id: str, units: float) -> None:
        self.db.execute(
            "UPDATE research_questions SET budget_spent_units = budget_spent_units + ?, updated_at = ? WHERE question_id = ?",
            [units, timeutil.utcnow(), question_id],
        )

    def resolve(
        self,
        question_id: str,
        resolution: str,
        confidence_after: float | None = None,
        validation_path: str = "unknown",
        root_cause: str | None = None,
    ) -> str | None:
        """Mark a question resolved and write exactly one
        ``research_experience`` row (Requirement 5) -- every resolution
        automatically produces experience, no separate manual bookkeeping.
        ``root_cause`` (Requirement 9) is the caller's own real
        classification (``experience.classify_failure``), not derived here.
        Returns the new experience_id."""
        before = self.get(question_id)
        if before is None:
            return None
        now = timeutil.utcnow()
        self.db.execute(
            "UPDATE research_questions SET status = 'resolved', resolution = ?, confidence_after = ?, "
            "updated_at = ?, resolved_at = ? WHERE question_id = ?",
            [resolution, confidence_after, now, now, question_id],
        )
        strategy = {
            "sources_used": before["expected_evidence"].get("sources", []),
            "validation_path": validation_path,
            "hypothesis": before["statement"],
            "root_cause": root_cause,
        }
        measured_costs = before["expected_evidence"].get("measured_costs", [])
        cost = {
            "observation_units_spent": before["budget_spent_units"],
            # Real per-call CPU/memory/latency samples accumulated by
            # ResearchEngine.tick() (Requirement 6) -- empty list if this
            # question was never planned through tick() (e.g. resolved
            # directly by a historical backfill).
            "measured_costs": measured_costs,
        }
        return self.experience.record(
            question_id=question_id,
            need_cluster=before["need_cluster"],
            strategy=strategy,
            outcome=resolution,
            cost=cost,
            confidence_before=before["confidence_before"],
            confidence_after=confidence_after,
        )

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["expected_evidence"] = json.loads(row["expected_evidence"])
        return row
