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
