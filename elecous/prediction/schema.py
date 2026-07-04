"""Prediction database schema (spec 8.22)."""

from __future__ import annotations

from elecous.storage.database import Migration

PREDICTION_MIGRATION = Migration(
    "predictions.0001.create_prediction_tables",
    "predictions",
    (
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            opportunity_id VARCHAR NOT NULL,
            version INTEGER NOT NULL,
            world_state_id VARCHAR NOT NULL,
            categories VARCHAR NOT NULL,        -- JSON list
            products VARCHAR NOT NULL,          -- JSON list of entity names
            activated_mechanisms VARCHAR NOT NULL,  -- JSON [{mechanism_id, activation}]
            environmental_factors VARCHAR NOT NULL, -- JSON list
            evidence VARCHAR NOT NULL,          -- JSON explainability bundle (8.26)
            scores VARCHAR NOT NULL,            -- JSON independent scores (8.8)
            lead_time VARCHAR NOT NULL,         -- JSON {earliest,most_probable,latest,confidence}
            confidence VARCHAR NOT NULL,        -- JSON components (8.16)
            probability DOUBLE NOT NULL,
            uncertainty VARCHAR NOT NULL,       -- JSON (8.13)
            business_value VARCHAR NOT NULL,
            regions VARCHAR NOT NULL,           -- JSON
            stage VARCHAR NOT NULL,             -- OpportunityStage
            created_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (opportunity_id, version)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_opp_stage ON opportunities (stage)",
        """
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id VARCHAR PRIMARY KEY,
            world_state_id VARCHAR NOT NULL,
            opportunity_id VARCHAR NOT NULL,
            mechanisms_used VARCHAR NOT NULL,   -- JSON list of {id, version}
            confidence DOUBLE NOT NULL,
            lead_time_days DOUBLE,
            evidence VARCHAR NOT NULL,
            outcome VARCHAR,                    -- JSON post-hoc, filled by resolve
            validation_status VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_pred_created ON predictions (created_at)",
        """
        CREATE TABLE IF NOT EXISTS opportunity_conflicts (
            conflict_id VARCHAR PRIMARY KEY,
            opportunity_a VARCHAR NOT NULL,
            opportunity_b VARCHAR NOT NULL,
            reason VARCHAR NOT NULL,
            resolution VARCHAR,                 -- JSON: which side has better evidence
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
    ),
)
