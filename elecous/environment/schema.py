"""Environmental Intelligence database schema (spec 4.26).

Nothing is overwritten; factors are versioned. Episode signal contexts are
cached so pattern mining is incremental (spec 10.19)."""

from __future__ import annotations

from elecous.storage.database import Migration

ENVIRONMENT_MIGRATION = Migration(
    "environment.0001.create_environment_tables",
    "environment",
    (
        """
        CREATE TABLE IF NOT EXISTS episode_signals (
            episode_id VARCHAR NOT NULL,
            signal_key VARCHAR NOT NULL,       -- phenomenon:direction:scope
            phenomenon VARCHAR NOT NULL,
            direction VARCHAR NOT NULL,        -- up|down
            scope VARCHAR NOT NULL,            -- country code, entity or global
            z DOUBLE NOT NULL,
            first_crossed_at TIMESTAMPTZ,
            days_before_spike DOUBLE,
            observation_types VARCHAR NOT NULL, -- JSON list (evidence trace)
            computed_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (episode_id, signal_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ordinary_windows (
            window_id VARCHAR PRIMARY KEY,
            category VARCHAR NOT NULL,
            window_end TIMESTAMPTZ NOT NULL,
            signal_keys VARCHAR NOT NULL,      -- JSON list of active keys
            computed_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS environmental_factors (
            factor_id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            name VARCHAR NOT NULL,
            signals VARCHAR NOT NULL,          -- JSON sorted list of signal keys
            categories VARCHAR NOT NULL,       -- JSON list
            regions VARCHAR NOT NULL,          -- JSON list of scopes
            lead_time VARCHAR NOT NULL,        -- JSON {min,avg,max,stddev} days
            confidence VARCHAR NOT NULL,       -- JSON {historical,recent,regional,category,scientific,current}
            current_confidence DOUBLE NOT NULL,
            support INTEGER NOT NULL,
            lift DOUBLE NOT NULL,
            contribution VARCHAR NOT NULL,     -- JSON per-signal contribution
            supporting_episodes VARCHAR NOT NULL,
            contradictory_episodes VARCHAR NOT NULL,
            counterfactual VARCHAR,            -- JSON counterfactual results
            discovery_quality VARCHAR,         -- JSON (4.24 scores)
            transferability DOUBLE NOT NULL,
            lifecycle VARCHAR NOT NULL,
            validation_status VARCHAR NOT NULL,
            merged_into VARCHAR,
            last_validation TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (factor_id, version)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_factor_lifecycle ON environmental_factors (lifecycle)",
        """
        CREATE TABLE IF NOT EXISTS signal_interactions (
            interaction_id VARCHAR PRIMARY KEY,
            signal_a VARCHAR NOT NULL,
            signal_b VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            joint_lift DOUBLE NOT NULL,
            individual_lift_a DOUBLE NOT NULL,
            individual_lift_b DOUBLE NOT NULL,
            synergy DOUBLE NOT NULL,           -- joint vs best individual
            support INTEGER NOT NULL,
            computed_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS world_conditions (
            condition_id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            name VARCHAR NOT NULL,             -- e.g. thermal_stress:up+energy_demand:up
            scope VARCHAR NOT NULL,
            signals VARCHAR NOT NULL,          -- JSON list of signal keys
            started_at TIMESTAMPTZ NOT NULL,
            ended_at TIMESTAMPTZ,
            active BOOLEAN NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (condition_id, version)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS world_states (
            state_id VARCHAR PRIMARY KEY,
            as_of TIMESTAMPTZ NOT NULL,
            signals VARCHAR NOT NULL,          -- JSON list of active signal changes
            conditions VARCHAR NOT NULL,       -- JSON list of active condition names
            observation_count INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_world_states_asof ON world_states (as_of)",
    ),
)

#: Additive lineage fix (rewrite directive, Level 2 source intelligence).
#: ``source_id`` was discarded at the very first aggregation step in
#: ``EnvironmentalSignalExtractor._compute_changes`` -- there was no code
#: path from a validated factor/mechanism back to which source produced it.
#: This column captures which sources' observations fell inside the window
#: that produced each cached signal change, without touching the z-score
#: computation itself (still computed at phenomenon:scope granularity).
ENVIRONMENT_PROVENANCE_MIGRATION = Migration(
    "environment.0002.add_signal_provenance",
    "environment",
    ("ALTER TABLE episode_signals ADD COLUMN IF NOT EXISTS source_ids VARCHAR",),
)

#: Discovered environmental states (rewrite directive Stage 4, Requirement
#: 2) -- real clusters of already-mined factors grouped by real phenomenon
#: overlap, not a fixed enum of state names.
STATE_MIGRATION = Migration(
    "environment.0003.create_environmental_states",
    "environment",
    (
        """
        CREATE TABLE IF NOT EXISTS environmental_states (
            state_id VARCHAR PRIMARY KEY,
            label VARCHAR NOT NULL,                 -- derived from real dominant phenomena
            dominant_phenomena VARCHAR NOT NULL,    -- JSON list of phenomenon:direction
            member_factor_ids VARCHAR NOT NULL,     -- JSON list of real factor_ids in this cluster
            categories VARCHAR NOT NULL,            -- JSON list of real affected categories
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_states_label ON environmental_states (label)",
    ),
)

#: Mechanism-hypothesis fields (rewrite directive Stage 5). A discovered
#: state becomes a real mechanism hypothesis once it has an inferred human
#: problem, a solution group, a generalization score and contradiction
#: counts -- all recomputed from real evidence, never asserted.
STATE_HYPOTHESIS_MIGRATION = Migration(
    "environment.0004.add_mechanism_hypothesis_fields",
    "environment",
    (
        # JSON-typed columns (solution_group, generalized_group,
        # contradictions, unknowns) store serialized lists/dicts; status is
        # active|retired.
        "ALTER TABLE environmental_states ADD COLUMN IF NOT EXISTS human_problem_statement VARCHAR",
        "ALTER TABLE environmental_states ADD COLUMN IF NOT EXISTS human_problem_confidence DOUBLE",
        "ALTER TABLE environmental_states ADD COLUMN IF NOT EXISTS solution_group VARCHAR",
        "ALTER TABLE environmental_states ADD COLUMN IF NOT EXISTS generalized_group VARCHAR",
        "ALTER TABLE environmental_states ADD COLUMN IF NOT EXISTS generality_score DOUBLE",
        "ALTER TABLE environmental_states ADD COLUMN IF NOT EXISTS contradictions VARCHAR",
        "ALTER TABLE environmental_states ADD COLUMN IF NOT EXISTS confidence DOUBLE",
        "ALTER TABLE environmental_states ADD COLUMN IF NOT EXISTS unknowns VARCHAR",
        "ALTER TABLE environmental_states ADD COLUMN IF NOT EXISTS status VARCHAR",
    ),
)

#: Candidate concepts (rewrite directive Stage 6): recurring, real
#: UNEXPLAINED residual evidence across mechanisms -- evidence current
#: states/vocabulary cannot describe. Provisional labels (CONCEPT-NNN),
#: never a permanent name, until/unless promoted.
CONCEPT_MIGRATION = Migration(
    "environment.0005.create_candidate_concepts",
    "environment",
    (
        """
        CREATE TABLE IF NOT EXISTS candidate_concepts (
            concept_id VARCHAR PRIMARY KEY,
            label VARCHAR NOT NULL,                      -- provisional "CONCEPT-NNN"
            sequence_number INTEGER NOT NULL,
            residual_phenomena VARCHAR NOT NULL,          -- JSON list of phenomenon:direction
            supporting_mechanism_ids VARCHAR NOT NULL,    -- JSON list
            contradicting_mechanism_ids VARCHAR NOT NULL, -- JSON list
            origin VARCHAR NOT NULL,                      -- JSON: how/why this concept was created
            confidence DOUBLE NOT NULL,
            confidence_history VARCHAR NOT NULL,          -- JSON list of {at, value}
            transferability_families VARCHAR NOT NULL,   -- JSON list of real Ontology families spanned
            evidence_explained_count INTEGER NOT NULL,
            competing_state_id VARCHAR,
            competition_result VARCHAR,
            unknowns VARCHAR NOT NULL,                    -- JSON list
            status VARCHAR NOT NULL,                       -- candidate|promoted|retired|merged|split
            retirement_reason VARCHAR,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_concepts_label ON candidate_concepts (label)",
        "CREATE INDEX IF NOT EXISTS idx_concepts_status ON candidate_concepts (status)",
    ),
)

#: Real bottleneck found in the Final Implementation Phase: environment.duckdb
#: grew to 2.5GB across 75,695 real factor-version rows for only 3,078
#: distinct factor_ids (~25 versions each, from many real research cycles
#: this session). find_by_signals() -- called on every single mined pattern
#: -- filters WHERE signals = ? with no index on that column, forcing a
#: real full-table scan every call (measured at 51ms/call). A missing
#: index, not a redundant-query bug; fixed the same way the schema's own
#: existing idx_factor_lifecycle index already does for a different column.
FACTOR_SIGNALS_INDEX_MIGRATION = Migration(
    "environment.0006.add_factor_signals_index",
    "environment",
    ("CREATE INDEX IF NOT EXISTS idx_factor_signals ON environmental_factors (signals)",),
)
