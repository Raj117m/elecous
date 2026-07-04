"""Validation database schema (spec 6.20). Every validation is auditable."""

from __future__ import annotations

from elecous.storage.database import Migration

VALIDATION_MIGRATION = Migration(
    "validation.0001.create_validation_tables",
    "validation",
    (
        """
        CREATE TABLE IF NOT EXISTS validations (
            validation_id VARCHAR PRIMARY KEY,
            object_type VARCHAR NOT NULL,
            object_id VARCHAR NOT NULL,
            object_version INTEGER,
            method VARCHAR NOT NULL,           -- historical|prospective|counterfactual|control|skeptic|calibration
            evidence VARCHAR NOT NULL,         -- JSON metrics and samples
            controls VARCHAR,                  -- JSON control comparison
            failures VARCHAR,                  -- JSON failing checks
            confidence DOUBLE,
            reviewer_engine VARCHAR NOT NULL,
            outcome VARCHAR NOT NULL,          -- passed|failed|inconclusive
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_validations_object ON validations (object_type, object_id, created_at)",
        """
        CREATE TABLE IF NOT EXISTS prospective_predictions (
            prospective_id VARCHAR PRIMARY KEY,
            mechanism_id VARCHAR NOT NULL,
            mechanism_version INTEGER NOT NULL,  -- frozen version (6.12)
            category VARCHAR NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL,
            activation DOUBLE NOT NULL,
            expected_by TIMESTAMPTZ NOT NULL,    -- issued_at + max lead time
            resolved_at TIMESTAMPTZ,
            outcome VARCHAR,                     -- success|failure|expired
            outcome_episode VARCHAR
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS calibration_records (
            recorded_at TIMESTAMPTZ NOT NULL,
            object_type VARCHAR NOT NULL,
            predicted_confidence DOUBLE NOT NULL,
            succeeded BOOLEAN NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS calibration_adjustments (
            object_type VARCHAR NOT NULL,
            bin_low DOUBLE NOT NULL,
            bin_high DOUBLE NOT NULL,
            predicted_mean DOUBLE NOT NULL,
            observed_rate DOUBLE NOT NULL,
            samples INTEGER NOT NULL,
            computed_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (object_type, bin_low)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS benchmarks (
            benchmark_id VARCHAR PRIMARY KEY,
            platform_version VARCHAR NOT NULL,
            suite VARCHAR NOT NULL,
            metrics VARCHAR NOT NULL,          -- JSON {precision,recall,lead_time,calibration_error,...}
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS drift_reports (
            drift_id VARCHAR PRIMARY KEY,
            mechanism_id VARCHAR NOT NULL,
            historical_accuracy DOUBLE,
            recent_accuracy DOUBLE,
            drop DOUBLE,
            action VARCHAR NOT NULL,           -- none|review|demoted
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
    ),
)
