"""One-off backfill: apply Stage 2's Source Value Ledger retroactively to
real, already-collected validation history (rewrite directive Requirement
11 -- measurement, not new architecture).

The event bus's persistence is a stub (nothing subscribed to
VALIDATION_COMPLETED before this session, so no historical events survive
to replay). The real, permanent record of what was validated does survive,
though: the ``validations`` table in ``validation.duckdb`` -- 400 real rows
from prior sessions' real pipeline runs. This script reconstructs one real
``Event`` per historical row and feeds it through the exact same
``SourceValueLedger.on_validation_completed`` / ``ResearchEngine`` logic
that now runs live, so the ledger's first population is genuine retroactive
history, not invented numbers.
"""

from __future__ import annotations

import json

from elecous.bootstrap import build_stack
from elecous.common import timeutil
from elecous.infrastructure.event_bus import Event, EventType
from elecous.infrastructure.logging import get_logger
from elecous.research.experience import classify_failure
from elecous.research.questions import ResearchQuestion

log = get_logger("research")

_KIND_BY_OBJECT_TYPE = {"environmental_factor": "factor_candidate", "mechanism": "mechanism_candidate"}


def backfill_episode_signal_lineage(stack) -> int:
    """episode_signals rows cached before the Stage 1 lineage fix have
    source_ids = NULL (the discovery cache returns the stored row without
    recomputing). Recompute lineage only -- via the same, now-fixed
    extractor -- for real existing episodes, using the real observations
    already on disk; the z-scores/signal keys are untouched, only the new
    column is filled in."""
    db = stack.environment.discovery.db
    missing = db.query(
        "SELECT DISTINCT episode_id FROM episode_signals WHERE source_ids IS NULL"
    )
    updated = 0
    for (episode_id,) in missing:
        episode = stack.demand.episodes.latest(episode_id)
        if episode is None:
            continue
        changes = stack.environment.extractor.changes(window_end=timeutil.ensure_utc(episode["spike_start"]))
        for change in changes:
            if not change.contributing_sources:
                continue
            db.execute(
                "UPDATE episode_signals SET source_ids = ? WHERE episode_id = ? AND signal_key = ? AND source_ids IS NULL",
                [json.dumps(list(change.contributing_sources)), episode_id, change.signal_key],
            )
            updated += 1
    return updated


def backfill_investigation_history(stack, rows: list[dict]) -> list[str]:
    """Rewrite directive Stage 2.5, Requirement 10 / success criterion: one
    real ResearchQuestion opened AND resolved per real historical
    validation row, in chronological order. Every field traces to an
    already-real row (name/categories/method/outcome) -- nothing is
    invented, and because each resolution writes a real experience row
    before the next question opens (with ``estimate_priority=True``),
    later questions in this same loop genuinely benefit from real
    accumulated history earlier ones did not have. Returns the ordered list
    of question_ids."""
    question_ids: list[str] = []
    for row in rows:
        object_type, object_id = row["object_type"], row["object_id"]
        obj_store = stack.environment.factors if object_type == "environmental_factor" else stack.mechanisms.store
        category_field = "categories" if object_type == "environmental_factor" else "affected_categories"
        obj = obj_store.latest(object_id)
        if obj is None:
            continue
        categories = json.loads(obj[category_field])
        need_cluster = categories[0] if categories else "unknown"
        qid = stack.research.questions.open(
            ResearchQuestion(
                kind=_KIND_BY_OBJECT_TYPE[object_type],
                statement=f"is '{obj['name']}' a real mechanism for {need_cluster}?",
                opened_by="backfill",
                subject_type=object_type,
                subject_ref=object_id,
                need_cluster=need_cluster,
                expected_evidence={},
            ),
            estimate_priority=True,
        )
        resolution = "supported" if row["outcome"] == "passed" else "refuted"
        root_cause = classify_failure(row) if resolution == "refuted" else None
        stack.research.questions.resolve(
            qid, resolution, validation_path=row["method"], root_cause=root_cause,
        )
        question_ids.append(qid)
    return question_ids


def backfill() -> dict[str, object]:
    stack = build_stack()
    lineage_updated = backfill_episode_signal_lineage(stack)
    rows = stack.databases.get("validation").query_dicts(
        "SELECT object_type, object_id, method, outcome, controls, evidence, created_at FROM validations "
        "WHERE object_type IN ('environmental_factor', 'mechanism') ORDER BY created_at"
    )
    applied = 0
    for row in rows:
        event = Event(
            event_type=EventType.VALIDATION_COMPLETED,
            source="validation",
            payload={"object_type": row["object_type"], "object_id": row["object_id"],
                      "method": row["method"], "outcome": row["outcome"], "created_at": row["created_at"]},
        )
        stack.research.source_value.on_validation_completed(event)
        applied += 1
    question_ids = backfill_investigation_history(stack, rows)
    return {
        "episode_signal_lineage_backfilled": lineage_updated,
        "validations_replayed": applied,
        "total_validations": len(rows),
        "investigations_recorded": len(question_ids),
    }


if __name__ == "__main__":
    result = backfill()
    print(json.dumps(result, indent=2))
