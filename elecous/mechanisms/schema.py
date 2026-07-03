"""Mechanism database schema (spec 5.4, 5.23)."""

from __future__ import annotations

from elecous.storage.database import Migration

MECHANISMS_MIGRATION = Migration(
    "mechanisms.0001.create_mechanism_tables",
    "mechanisms",
    (
        """
        CREATE TABLE IF NOT EXISTS mechanisms (
            mechanism_id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            name VARCHAR NOT NULL,
            environmental_factors VARCHAR NOT NULL,  -- JSON factor ids
            supporting_signals VARCHAR NOT NULL,     -- JSON signal keys
            behavioural_chain VARCHAR NOT NULL,      -- JSON list of chain steps (5.7)
            product_traits VARCHAR NOT NULL,         -- JSON traits activated (5.12)
            affected_categories VARCHAR NOT NULL,    -- JSON list
            countries VARCHAR NOT NULL,              -- JSON list
            climate_zones VARCHAR NOT NULL,          -- JSON list
            world_conditions VARCHAR NOT NULL,       -- JSON list (Appendix D)
            lead_time VARCHAR NOT NULL,              -- JSON {min,avg,max,stddev}
            confidence VARCHAR NOT NULL,             -- JSON {historical,recent,regional,scientific,current}
            current_confidence DOUBLE NOT NULL,
            historical_accuracy DOUBLE,
            business_value VARCHAR NOT NULL,         -- JSON (5.25)
            stability VARCHAR NOT NULL,              -- JSON {age_days,consistency,volatility,revisions}
            reputation DOUBLE NOT NULL,              -- scientific reputation (6.29)
            parent_mechanism VARCHAR,                -- hierarchy (5.8)
            supporting_episodes VARCHAR NOT NULL,
            control_episodes VARCHAR NOT NULL,
            contradicting_episodes VARCHAR NOT NULL,
            validation_history VARCHAR NOT NULL,     -- JSON list
            evidence_links VARCHAR NOT NULL,         -- JSON {factors,signals,episodes,observations}
            lifecycle VARCHAR NOT NULL,
            validation_status VARCHAR NOT NULL,
            next_validation_at TIMESTAMPTZ,
            last_validation TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (mechanism_id, version)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_mech_lifecycle ON mechanisms (lifecycle)",
        """
        CREATE TABLE IF NOT EXISTS mechanism_interactions (
            mechanism_a VARCHAR NOT NULL,
            mechanism_b VARCHAR NOT NULL,
            shared_episodes INTEGER NOT NULL,
            strength DOUBLE NOT NULL,           -- learned interaction strength (5.9)
            computed_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (mechanism_a, mechanism_b)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mechanism_competitions (
            competition_id VARCHAR PRIMARY KEY,
            mechanism_a VARCHAR NOT NULL,
            mechanism_b VARCHAR NOT NULL,
            subject VARCHAR NOT NULL,            -- what they disagree about
            opened_at TIMESTAMPTZ NOT NULL,
            resolved_at TIMESTAMPTZ,
            winner VARCHAR,
            evidence VARCHAR                     -- JSON outcome comparison
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mechanism_failures (
            failure_id VARCHAR PRIMARY KEY,
            mechanism_id VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL,
            kind VARCHAR NOT NULL,
            questions VARCHAR NOT NULL,          -- JSON failure investigation (5.21)
            resolution VARCHAR
        )
        """,
    ),
)
