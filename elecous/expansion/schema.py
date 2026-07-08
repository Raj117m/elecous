"""Expansion database schema."""

from __future__ import annotations

from elecous.storage.database import Migration

EXPANSION_MIGRATION = Migration(
    "expansion.0001.create_entity_registry",
    "expansion",
    (
        """
        CREATE TABLE IF NOT EXISTS entity_registry (
            entity VARCHAR PRIMARY KEY,
            article VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            traits VARCHAR NOT NULL,
            discovered BOOLEAN NOT NULL,
            discovery_query VARCHAR,
            discovery_median_pageviews DOUBLE,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
    ),
)
