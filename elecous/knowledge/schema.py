"""Knowledge database schema (spec ch. 7).

The Knowledge Engine does not store raw data — it stores validated
understanding: the graph of relationships between signals, factors,
mechanisms and episodes, plus memory (world-state history, failures,
unknowns) and integrity/quality scores. Nothing here is overwritten.
"""

from __future__ import annotations

from elecous.storage.database import Migration

KNOWLEDGE_MIGRATION = Migration(
    "knowledge.0001.create_knowledge_tables",
    "knowledge",
    (
        """
        CREATE TABLE IF NOT EXISTS knowledge_nodes (
            node_id VARCHAR PRIMARY KEY,
            object_type VARCHAR NOT NULL,     -- signal|environmental_factor|mechanism|research_episode
            object_ref VARCHAR NOT NULL,      -- id in its home database
            label VARCHAR NOT NULL,
            confidence DOUBLE NOT NULL,
            integrity DOUBLE NOT NULL,
            quality VARCHAR,                  -- JSON (7.20)
            half_life_days DOUBLE,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_knowledge_ref ON knowledge_nodes (object_type, object_ref)",
        """
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            edge_id VARCHAR PRIMARY KEY,
            source_node VARCHAR NOT NULL,
            target_node VARCHAR NOT NULL,
            relationship VARCHAR NOT NULL,    -- supports|contradicts|depends_on|extends|...
            weight DOUBLE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_edges_source ON knowledge_edges (source_node)",
        "CREATE INDEX IF NOT EXISTS idx_edges_target ON knowledge_edges (target_node)",
        """
        CREATE TABLE IF NOT EXISTS world_state_memory (
            snapshot_id VARCHAR PRIMARY KEY,
            as_of TIMESTAMPTZ NOT NULL,
            world_state_id VARCHAR NOT NULL,
            active_mechanisms VARCHAR NOT NULL, -- JSON list of mechanism ids
            active_factors VARCHAR NOT NULL,
            conditions VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS failure_memory (
            failure_id VARCHAR PRIMARY KEY,
            object_type VARCHAR NOT NULL,
            object_id VARCHAR NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL,
            summary VARCHAR NOT NULL,
            detail VARCHAR NOT NULL           -- JSON
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS unknown_memory (
            unknown_id VARCHAR PRIMARY KEY,
            kind VARCHAR NOT NULL,            -- unknown_signal|unknown_pattern|unknown_mechanism|unexplained_opportunity
            description VARCHAR NOT NULL,
            context VARCHAR NOT NULL,         -- JSON
            research_priority DOUBLE NOT NULL,
            status VARCHAR NOT NULL,          -- open|researching|resolved|abandoned
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS knowledge_revisions (
            revision_id VARCHAR PRIMARY KEY,
            object_type VARCHAR NOT NULL,
            object_id VARCHAR NOT NULL,
            from_version INTEGER,
            to_version INTEGER NOT NULL,
            reason VARCHAR NOT NULL,
            actor VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
    ),
)
