"""Source Value Records schema (rewrite directive, Level 2 source
intelligence, stage 1).

Extends the Source Registry's existing, previously-dead
``historical_contribution`` field (``registry.py``) with a real, per-need
scored history: for each (source, need_cluster) pair, how often has this
source's evidence fed a validation that passed vs. failed, how early does
it typically indicate change, and how large is its sample.

Lives in the ``observations`` domain (not a new database) because it reads
and writes the same blast radius as ``ObservationHealth``/``SourceRegistry``,
which already live there -- this is an extension of existing source scoring,
not a new scientific responsibility (docs/decisions/0001, spec 10.14).

Stage 1 scope: schema only. The consumer that populates this table by
listening to ``VALIDATION_COMPLETED`` is ``elecous/research/source_value.py``
and is not yet subscribed to the event bus -- see the staged rollout.
"""

from __future__ import annotations

from elecous.storage.database import Migration

SOURCE_VALUE_MIGRATION = Migration(
    "observations.0003.create_source_value_records",
    "observations",
    (
        """
        CREATE TABLE IF NOT EXISTS source_value_records (
            source_id VARCHAR NOT NULL,
            need_cluster VARCHAR NOT NULL,      -- today: demand category (placeholder, see ADR)
            supports INTEGER NOT NULL DEFAULT 0,        -- times fed a PASSED validation
            contradictions INTEGER NOT NULL DEFAULT 0,  -- times fed a FAILED validation
            net_value_ema DOUBLE NOT NULL DEFAULT 0.0,  -- ema of (supports - contradictions) / n
            lead_time_days_avg DOUBLE,          -- avg days between this source's observation
                                                 -- and the episode spike it contributed to
            sample_size INTEGER NOT NULL DEFAULT 0,
            last_event_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (source_id, need_cluster)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_svr_source ON source_value_records (source_id)",
    ),
)

#: Additive richer metrics (rewrite directive, Stage 2.5, Requirement 2).
#: Every one is a real aggregate of data already collected elsewhere
#: (connector_runs, evidence lineage, validations ordering) -- nothing here
#: is a new kind of measurement, only new columns to persist real numbers
#: that were previously recomputed on the fly.
SOURCE_VALUE_METRICS_MIGRATION = Migration(
    "observations.0004.add_source_value_richer_metrics",
    "observations",
    (
        "ALTER TABLE source_value_records ADD COLUMN IF NOT EXISTS redundancy_count INTEGER",
        "ALTER TABLE source_value_records ADD COLUMN IF NOT EXISTS novelty_count INTEGER",
        "ALTER TABLE source_value_records ADD COLUMN IF NOT EXISTS compute_cost_ms_avg DOUBLE",
        "ALTER TABLE source_value_records ADD COLUMN IF NOT EXISTS storage_bytes_est DOUBLE",
    ),
)
