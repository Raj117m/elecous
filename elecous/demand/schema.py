"""Demand Intelligence database schema (spec 3.19, ADR 0001).

Nothing is overwritten; episodes are versioned and frozen."""

from __future__ import annotations

from elecous.storage.database import Migration

DEMAND_MIGRATION = Migration(
    "demand.0001.create_demand_tables",
    "demand",
    (
        """
        CREATE TABLE IF NOT EXISTS demand_signal_registry (
            signal_type VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            country VARCHAR NOT NULL,          -- '' = all
            reliability DOUBLE NOT NULL,
            freshness DOUBLE NOT NULL,
            historical_accuracy DOUBLE NOT NULL,
            contribution DOUBLE NOT NULL,
            validation_count INTEGER NOT NULL,
            confidence DOUBLE NOT NULL,
            lifecycle VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (signal_type, category, country, version)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS baselines (
            entity VARCHAR NOT NULL,
            signal_type VARCHAR NOT NULL,
            computed_at TIMESTAMPTZ NOT NULL,
            as_of DATE NOT NULL,
            window_days INTEGER NOT NULL,
            n_points INTEGER NOT NULL,
            median DOUBLE NOT NULL,
            mad DOUBLE NOT NULL,
            mean DOUBLE NOT NULL,
            std DOUBLE NOT NULL,
            reliable BOOLEAN NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_baseline ON baselines (entity, signal_type, as_of)",
        """
        CREATE TABLE IF NOT EXISTS research_episodes (
            episode_id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            entity VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            traits VARCHAR NOT NULL,           -- JSON list
            country VARCHAR,
            region VARCHAR,
            baseline VARCHAR NOT NULL,         -- JSON per-signal baselines
            window_days INTEGER NOT NULL,
            spike_start TIMESTAMPTZ NOT NULL,
            spike_peak TIMESTAMPTZ,
            recovery_end TIMESTAMPTZ,
            demand_score DOUBLE NOT NULL,
            evidence VARCHAR NOT NULL,         -- JSON frozen evidence bundle
            timeline VARCHAR NOT NULL,         -- JSON phase boundaries (3.14)
            signal_agreement VARCHAR NOT NULL, -- JSON per-signal contributions
            confidence VARCHAR NOT NULL,       -- JSON research confidence (3.16)
            overall_confidence DOUBLE NOT NULL,
            control_episodes VARCHAR NOT NULL, -- JSON list of control descriptors
            mechanisms_found VARCHAR NOT NULL, -- JSON list of mechanism ids
            false_spike_checks VARCHAR NOT NULL,
            outcome VARCHAR,                   -- JSON post-hoc outcome
            validation_status VARCHAR NOT NULL,
            lifecycle VARCHAR NOT NULL,
            is_control BOOLEAN NOT NULL,
            is_negative BOOLEAN NOT NULL,      -- negative episode (4.18)
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (episode_id, version)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_episode_entity ON research_episodes (entity, spike_start)",
        "CREATE INDEX IF NOT EXISTS idx_episode_category ON research_episodes (category)",
        """
        CREATE TABLE IF NOT EXISTS signal_weights (
            category VARCHAR NOT NULL,
            signal_type VARCHAR NOT NULL,
            weight DOUBLE NOT NULL,
            learned_from INTEGER NOT NULL,     -- episodes contributing
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (category, signal_type)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS learned_windows (
            category VARCHAR PRIMARY KEY,
            window_days INTEGER NOT NULL,
            learned_from INTEGER NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS demand_failures (
            failure_id VARCHAR PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL,
            entity VARCHAR,
            category VARCHAR,
            kind VARCHAR NOT NULL,             -- missed_spike|false_positive|...
            questions VARCHAR NOT NULL,        -- JSON failure-research answers
            resolution VARCHAR
        )
        """,
    ),
)
