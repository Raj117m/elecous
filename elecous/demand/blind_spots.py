"""Blind Spot Engine (Constitution Phase 2).

Automatically identifies entities, categories, and environments that lack
sufficient coverage, explicitly tagging these as Blind Spots and generating
programmatic requests for the Data Ingestion pipeline.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from elecous.common import timeutil
from elecous.common.ids import new_id
from elecous.storage.database import DatabaseManager, global_databases
from elecous.observation.store import ObservationStore
from elecous.infrastructure.logging import get_logger

class BlindSpotEngine:
    def __init__(self, store: ObservationStore, databases: DatabaseManager | None = None):
        self.store = store
        self.databases = databases or global_databases()
        self.db = self.databases.get("demand")
        self.log = get_logger("demand")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS blind_spots (
                blind_spot_id VARCHAR PRIMARY KEY,
                entity VARCHAR,
                category VARCHAR,
                signal_type VARCHAR,
                coverage_metrics VARCHAR NOT NULL, -- JSON
                generated_at TIMESTAMPTZ NOT NULL,
                status VARCHAR NOT NULL            -- pending | investigating | resolved
            )
            """
        )

    def evaluate_coverage(self, entity: str, signal_type: str, category: str, as_of: datetime | None = None) -> None:
        """Evaluates the 10-part Coverage metric and generates a Blind Spot if deficient.

        Coverage (Constitution): Temporal Depth (min 5 years), Granularity, Continuity,
        Geographic reach, Diversity, Independence, Consistency, Representation, Timeliness, Confidence.
        """
        as_of = timeutil.ensure_utc(as_of) if as_of else timeutil.utcnow()
        min_history = 1825  # 5 years
        max_gap = 3

        series = self.store.series(entity, signal_type, as_of - timedelta(days=min_history + 30), as_of)
        if not series:
            self._register_blind_spot(entity, category, signal_type, {"temporal_depth": 0, "reason": "No data available"})
            return

        first, last = series[0]["timestamp"], series[-1]["timestamp"]
        history_days = (last - first).days

        gaps = [(series[i]["timestamp"] - series[i - 1]["timestamp"]).days for i in range(1, len(series))]
        max_gap_found = max(gaps) if gaps else 0

        # Timeliness: difference between last observation and as_of
        timeliness = (as_of - last).days

        # Independence & Diversity (stubs, to be populated by Source Engine)
        independence = 1.0  # Percentage of independent sources
        diversity = 1.0     # Variety of source types
        
        # Granularity & Consistency
        granularity = 1.0   # Frequency of data points vs expected
        consistency = 1.0   # Data formatting/schema consistency
        
        # Geographic reach & Representation
        geo_reach = 1.0
        representation = 1.0

        deficiencies = {}
        if history_days < min_history:
            deficiencies["temporal_depth"] = {"actual": history_days, "required": min_history}
        if max_gap_found > max_gap:
            deficiencies["continuity"] = {"actual_max_gap": max_gap_found, "required_max_gap": max_gap}
        if timeliness > max_gap:
            deficiencies["timeliness"] = {"actual": timeliness, "required": max_gap}
        if independence < 0.5:
            deficiencies["independence"] = {"actual": independence, "required": 0.5}
        if diversity < 0.5:
            deficiencies["diversity"] = {"actual": diversity, "required": 0.5}
        if granularity < 0.8:
            deficiencies["granularity"] = {"actual": granularity, "required": 0.8}
        if consistency < 0.9:
            deficiencies["consistency"] = {"actual": consistency, "required": 0.9}
        if geo_reach < 0.5:
            deficiencies["geographic_reach"] = {"actual": geo_reach, "required": 0.5}
        if representation < 0.5:
            deficiencies["representation"] = {"actual": representation, "required": 0.5}

        # Average confidence
        avg_confidence = sum(s["confidence"] for s in series) / len(series)
        if avg_confidence < 0.6:
            deficiencies["confidence"] = {"actual": avg_confidence, "required": 0.6}

        if deficiencies:
            self._register_blind_spot(entity, category, signal_type, deficiencies)

    def _register_blind_spot(self, entity: str, category: str, signal_type: str, metrics: dict[str, Any]) -> None:
        """Registers a Blind Spot if one for this entity/signal is not already pending."""
        existing = self.db.query_one(
            "SELECT blind_spot_id FROM blind_spots WHERE entity = ? AND signal_type = ? AND status = 'pending'",
            [entity, signal_type]
        )
        if existing:
            return

        blind_spot_id = new_id("blind_spot")
        self.db.execute(
            "INSERT INTO blind_spots VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                blind_spot_id,
                entity,
                category,
                signal_type,
                json.dumps(metrics),
                timeutil.utcnow(),
                "pending"
            ]
        )
        self.log.warning("Blind Spot generated", blind_spot_id=blind_spot_id, entity=entity, signal_type=signal_type, metrics=metrics)
