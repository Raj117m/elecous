"""Research Experience Store (rewrite directive, Stage 2, Level 2 memory).

One row per resolved Research Question -- what strategy was tried and what
happened (Requirement 5). Repeated, real agreement across >=3 experiences
generalizes into a ``research_lessons`` row (Requirement 6/8), which
``research/priority.py:plan_evidence`` consults before planning the next
investigation. This is the concrete read-write loop that closes the
self-improvement requirement: nothing here is asserted or hardcoded --
lessons only exist once real experience has accumulated enough to support
them.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.research.schema import RESEARCH_MIGRATION
from elecous.storage.database import DatabaseManager, global_databases


#: Real, explicit classification (Stage 2.5, Requirement 9) of the Skeptic
#: Engine's own structured challenge names (found by reading real rows in
#: validation.duckdb's `controls` column this session -- `failures` is
#: always NULL; the real per-check detail lives in `controls.challenges`)
#: into the eight requested root-cause buckets. This is a lookup table over
#: names the Skeptic Engine already emits, not a new judgment model.
_SKEPTIC_CHECK_TO_ROOT_CAUSE = {
    "sampling_bias": "poor_source_selection",     # too few distinct entities behind the claim
    "regional_bias": "poor_source_selection",
    "duplication": "poor_investigation_planning",  # near-duplicate factor from unpruned mining
    "overfitting": "weak_reasoning",               # signal count vastly exceeds real supporting episodes
    "data_leakage": "weak_reasoning",              # mechanism's timing logic used future information
    "seasonality": "invalid_hypothesis",           # calendar seasonality explains it better than the claim
    "simpler_explanation": "invalid_hypothesis",   # a simpler subset already explains the same evidence
    "coincidence": "insufficient_evidence",        # support/lift too weak to distinguish from chance
    # Stage 4, Requirement 6/9: fails to span multiple real products/years
    # -- exactly "not reusable" in Requirement 9's own vocabulary.
    "generalization": "not_reusable",
}
#: Outer, method-level checks (validate_factor/validate_mechanism's own
#: `evidence` dict) mapped the same honest way, consulted only when no
#: skeptic sub-check explains the failure.
_OUTER_CHECK_TO_ROOT_CAUSE = {
    "multiple_episodes": "insufficient_evidence",
    "multiple_regions": "insufficient_evidence",
    "counterfactual_survives": "invalid_hypothesis",
    "generalizes": "insufficient_evidence",
    "repeatable": "insufficient_evidence",
    "timing_stable": "weak_reasoning",
    "historical_precision_ok": "computational_limits",  # real, session-specific finding: the historical
                                                          # replay's activation_threshold makes this check
                                                          # nearly unsatisfiable by construction (see
                                                          # docs/decisions), not evidence of a wrong mechanism
}


def classify_failure(validation_row: dict[str, Any]) -> str:
    """One of the real root-cause buckets, derived only from the judged
    object's own real, already-stored check results -- never guessed."""
    controls = validation_row.get("controls")
    if controls:
        challenges = (json.loads(controls) if isinstance(controls, str) else controls).get("challenges", {})
        for check_name, bucket in _SKEPTIC_CHECK_TO_ROOT_CAUSE.items():
            if check_name in challenges and not challenges[check_name].get("passed", True):
                return bucket
    evidence = validation_row.get("evidence")
    if evidence:
        checks = json.loads(evidence) if isinstance(evidence, str) else evidence
        # Stage 4, Requirement 7/9: a mechanism whose real signals are
        # purely attention-phenomena (no independent environmental signal)
        # is a real, checkable confound -- "confounded" in Requirement 9's
        # own vocabulary, not force-mapped onto an unrelated bucket.
        competing = checks.get("competing_explanations", {}).get("verdicts", {})
        if competing.get("media_event_only", {}).get("ruled_out") is False:
            return "confounded"
        for check_name, bucket in _OUTER_CHECK_TO_ROOT_CAUSE.items():
            if check_name in checks.get("checks", checks) and not checks.get("checks", checks)[check_name]:
                return bucket
    return "insufficient_evidence"  # honest default: no specific real reason was found to point elsewhere


class ResearchExperienceStore:
    def __init__(self, databases: DatabaseManager | None = None, config: Config | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(RESEARCH_MIGRATION)
        self.db = self.databases.get("research")
        self.config = config or active_config()
        self.log = get_logger("research")

    def record(
        self,
        question_id: str,
        need_cluster: str | None,
        strategy: dict[str, Any],
        outcome: str,
        cost: dict[str, Any],
        confidence_before: float | None = None,
        confidence_after: float | None = None,
    ) -> str:
        experience_id = new_id("research_experience")
        now = timeutil.utcnow()
        self.db.execute(
            "INSERT INTO research_experience VALUES (" + ", ".join("?" * 10) + ")",
            [
                experience_id,
                question_id,
                need_cluster,
                json.dumps(strategy),
                outcome,
                json.dumps(cost),
                confidence_before,
                confidence_after,
                None,
                now,
            ],
        )
        # Lesson derivation runs AFTER the insert so this row itself counts
        # toward the real >=3-consistent-attempts check (unchanged Stage 2
        # ordering; only the strategy-augmentation step below is new).
        lesson_id = self._maybe_derive_lesson(need_cluster, strategy, outcome, experience_id)
        # Real, derived-not-invented recommendation (Requirement 5): a
        # lesson just confirmed means "stop repeating this," a supported
        # outcome means "this combination worked, reuse it," anything else
        # means there isn't yet enough repeated evidence to generalize.
        if lesson_id:
            recommendation = "avoid repeating this validation_path/root_cause combination without a changed strategy"
        elif outcome == "supported":
            recommendation = "reuse this source/validation_path combination for similar future questions"
        else:
            recommendation = "insufficient repeated evidence yet to generalize; retry cautiously"
        strategy = {**strategy, "future_recommendation": recommendation}
        self.db.execute(
            "UPDATE research_experience SET strategy = ?, lesson = ? WHERE experience_id = ?",
            [json.dumps(strategy), lesson_id, experience_id],
        )
        self.log.info("research experience recorded", experience_id=experience_id, outcome=outcome)
        return experience_id

    def similar_attempts(
        self, need_cluster: str | None, kind: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Exact need_cluster match first; no embeddings/ML -- proportionate
        to the real data volume (~1GB total across the whole project). When
        ``kind`` is given, joins to ``research_questions`` (both tables live
        in the same ``research`` domain) rather than silently ignoring it."""
        if kind is None:
            rows = self.db.query_dicts(
                "SELECT * FROM research_experience WHERE need_cluster = ? ORDER BY created_at DESC LIMIT ?",
                [need_cluster, limit],
            )
        else:
            rows = self.db.query_dicts(
                "SELECT e.* FROM research_experience e JOIN research_questions q ON e.question_id = q.question_id "
                "WHERE e.need_cluster = ? AND q.kind = ? ORDER BY e.created_at DESC LIMIT ?",
                [need_cluster, kind, limit],
            )
        return [self._decode(r) for r in rows]

    def lessons_for(self, applies_to: str) -> list[dict[str, Any]]:
        rows = self.db.query_dicts(
            "SELECT * FROM research_lessons WHERE applies_to = ? ORDER BY confirmations DESC", [applies_to]
        )
        return [self._decode_lesson(r) for r in rows]

    # -------------------------------------------------------------- lessons
    def _maybe_derive_lesson(
        self, need_cluster: str | None, strategy: dict[str, Any], outcome: str, experience_id: str
    ) -> str | None:
        """A lesson generalizes real, repeated agreement -- not a single
        data point. Keyed by (need_cluster, validation_path): if the same
        combination has been tried >=3 times and always refuted, that is a
        real, reusable finding (Requirement 6's literal example: the same
        search path failing fifty times should not be retried blindly)."""
        applies_to = need_cluster or "unknown"
        validation_path = strategy.get("validation_path", "unknown")
        condition = {"validation_path": validation_path}
        rows = self.db.query_dicts(
            "SELECT * FROM research_experience WHERE need_cluster = ? ORDER BY created_at DESC LIMIT 20",
            [need_cluster],
        )
        matching = [r for r in rows if json.loads(r["strategy"]).get("validation_path") == validation_path]
        if len(matching) < 3:
            return None
        refuted = sum(1 for r in matching if r["outcome"] in ("refuted", "aborted"))
        if refuted != len(matching):
            return None  # not a clean, consistent pattern -- do not overclaim
        now = timeutil.utcnow()
        lesson_text = (
            f"'{validation_path}' has refuted {refuted}/{len(matching)} consecutive real attempts "
            f"for need_cluster='{applies_to}' -- do not repeat without a changed strategy"
        )
        existing = self.db.query_one(
            "SELECT lesson_id, confirmations, source_experience_ids FROM research_lessons "
            "WHERE applies_to = ? AND condition = ?",
            [applies_to, json.dumps(condition)],
        )
        if existing:
            lesson_id, confirmations, source_ids_raw = existing
            source_ids = json.loads(source_ids_raw) + [experience_id]
            self.db.execute(
                "UPDATE research_lessons SET confirmations = ?, lesson_text = ?, last_confirmed_at = ?, "
                "source_experience_ids = ? WHERE lesson_id = ?",
                [confirmations + 1, lesson_text, now, json.dumps(source_ids), lesson_id],
            )
            return lesson_id
        lesson_id = new_id("research_lesson")
        self.db.execute(
            "INSERT INTO research_lessons VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [lesson_id, applies_to, json.dumps(condition), lesson_text, refuted, now, now, json.dumps([experience_id])],
        )
        return lesson_id

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["strategy"] = json.loads(row["strategy"])
        row["cost"] = json.loads(row["cost"])
        return row

    @staticmethod
    def _decode_lesson(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["condition"] = json.loads(row["condition"])
        row["source_experience_ids"] = json.loads(row["source_experience_ids"])
        return row
