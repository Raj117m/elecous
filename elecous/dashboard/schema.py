"""Dashboard database schema (spec ch. 9). Stores only presentation-layer
state: notification thresholds already fired and watchlists. Everything
else the dashboard shows is read live from the other domain databases —
the Dashboard Engine performs zero reasoning (9.29).
"""

from __future__ import annotations

from elecous.storage.database import Migration

DASHBOARD_MIGRATION = Migration(
    "dashboard.0001.create_dashboard_tables",
    "dashboard",
    (
        """
        CREATE TABLE IF NOT EXISTS notifications (
            notification_id VARCHAR PRIMARY KEY,
            kind VARCHAR NOT NULL,
            message VARCHAR NOT NULL,
            severity VARCHAR NOT NULL,
            context VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            acknowledged BOOLEAN NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS watchlists (
            watchlist_id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            kind VARCHAR NOT NULL,          -- category|mechanism|region|product
            items VARCHAR NOT NULL,         -- JSON list
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
    ),
)
