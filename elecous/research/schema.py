"""Research Intelligence database schema (rewrite directive, Level 2).

Three tables, one database domain (``research``), following the same
per-responsibility split as every other engine (spec 10.14,
docs/decisions/0001-demand-database.md):

- ``research_questions``: a specific, answerable claim under test — the
  first-class object the reasoning workflow revolves around (replaces ad-hoc
  "collect on interval" with "collect because a question needs this").
- ``research_experience``: one row per resolved question — what strategy was
  tried and what happened. This is raw experience, not yet generalized.
- ``research_lessons``: patterns generalized from >=3 experiences that agree
  (e.g. "this validation path repeatedly fails for this reason") — the
  reusable output of Level-2 learning that future questions consult before
  repeating a known-wasteful strategy.
"""

from __future__ import annotations

from elecous.storage.database import Migration

RESEARCH_MIGRATION = Migration(
    "research.0001.create_research_tables",
    "research",
    (
        """
        CREATE TABLE IF NOT EXISTS research_questions (
            question_id VARCHAR PRIMARY KEY,
            kind VARCHAR NOT NULL,              -- mechanism_candidate | factor_candidate |
                                                 -- validation_path_retry | source_evaluation |
                                                 -- entity_causal_check | custom
            statement VARCHAR NOT NULL,         -- human-readable question text
            subject_type VARCHAR,               -- environmental_factor|mechanism|source|entity
            subject_ref VARCHAR,                -- id in its home store, nullable
            unknown_id VARCHAR,                 -- FK to knowledge.unknown_memory, nullable
            need_cluster VARCHAR,               -- today: demand category (see ADR)
            expected_evidence VARCHAR NOT NULL, -- JSON {capabilities, sources, min_observations, rationale}
            evidence_plan_source VARCHAR NOT NULL, -- 'experience_store' | 'default_heuristic'
            priority DOUBLE NOT NULL,
            status VARCHAR NOT NULL,            -- open|investigating|resolved|abandoned
            resolution VARCHAR,                 -- supported|refuted|inconclusive, null until resolved
            confidence_before DOUBLE,
            confidence_after DOUBLE,
            budget_spent_units DOUBLE NOT NULL DEFAULT 0.0,
            opened_by VARCHAR NOT NULL,         -- subsystem name (curiosity|validation|manual|...)
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            resolved_at TIMESTAMPTZ
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_research_questions_status ON research_questions (status, priority)",
        "CREATE INDEX IF NOT EXISTS idx_research_questions_unknown ON research_questions (unknown_id)",
        """
        CREATE TABLE IF NOT EXISTS research_experience (
            experience_id VARCHAR PRIMARY KEY,
            question_id VARCHAR NOT NULL,
            need_cluster VARCHAR,
            strategy VARCHAR NOT NULL,          -- JSON {sources_used, validation_path, representation}
            outcome VARCHAR NOT NULL,           -- supported|refuted|inconclusive|aborted
            cost VARCHAR NOT NULL,              -- JSON {observation_units_spent, wall_clock_days, connector_runs}
            confidence_before DOUBLE,
            confidence_after DOUBLE,
            lesson VARCHAR,                     -- free text, machine-generated summary
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_experience_question ON research_experience (question_id)",
        "CREATE INDEX IF NOT EXISTS idx_experience_cluster ON research_experience (need_cluster)",
        """
        CREATE TABLE IF NOT EXISTS research_lessons (
            lesson_id VARCHAR PRIMARY KEY,
            applies_to VARCHAR NOT NULL,        -- e.g. 'skeptic.overfitting_check'
            condition VARCHAR NOT NULL,         -- JSON structured predicate
            lesson_text VARCHAR NOT NULL,
            confirmations INTEGER NOT NULL DEFAULT 1,
            first_observed_at TIMESTAMPTZ NOT NULL,
            last_confirmed_at TIMESTAMPTZ NOT NULL,
            source_experience_ids VARCHAR NOT NULL  -- JSON list
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_lessons_applies_to ON research_lessons (applies_to)",
    ),
)
