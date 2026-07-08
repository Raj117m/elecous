"""Demand Pressure Index (DPI) calculation (Constitution 1.4).

DPI is the continuous scalar representation of total unresolved demand tension.
It integrates active Demand Spikes, weighted by their Volume Multiplier (M) and
Duration, and applies the Environmental Weight (W_env).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.config import active_config
from elecous.storage.database import DatabaseManager, global_databases

class DemandPressureIndex:
    def __init__(self, databases: DatabaseManager | None = None):
        self.databases = databases or global_databases()
        self.db = self.databases.get("demand")
        self.thresholds = active_config().load_file("thresholds.yaml")["demand"]

    def compute(self, entity: str, as_of: datetime | None = None) -> float:
        """Compute the DPI for an entity based on active Demand Spikes and associated forces."""
        as_of = timeutil.ensure_utc(as_of) if as_of else timeutil.utcnow()
        d_max = int(self.thresholds.get("false_spike", {}).get("d_max", 30))

        # Fetch associated forces for this product (Constitution Product-Centric Redesign)
        prod_rows = self.db.query_dicts(
            "SELECT associated_forces FROM products WHERE name = ? OR product_id = ?", 
            [entity, entity.lower().replace(" ", "_")]
        )
        associated_forces = json.loads(prod_rows[0]["associated_forces"]) if prod_rows else ["consumer_behaviour"]

        # Fetch active episodes for this entity
        rows = self.db.query_dicts(
            "SELECT timeline, signal_agreement FROM research_episodes WHERE entity = ? AND is_control = FALSE ORDER BY version DESC",
            [entity]
        )

        dpi = 0.0
        for row in rows:
            timeline = json.loads(row["timeline"])
            if not timeline.get("acceleration") or not timeline.get("peak"):
                continue

            accel_time = timeutil.from_iso(timeline["acceleration"])
            peak_time = timeutil.from_iso(timeline["peak"])
            
            if (as_of - peak_time).days > d_max:
                continue
                
            duration_days = (peak_time - accel_time).days + 1
            duration_factor = min(1.0, duration_days / d_max)

            agreement = json.loads(row["signal_agreement"])
            m_approx = max(agreement.values()) / 50.0 if agreement else 1.5

            # Compute W_env based on how many of the product's associated forces are active
            # For this simple prototype, we scale weight by the number of associated forces
            w_env = 0.5 + (0.5 * len(associated_forces))

            dpi += m_approx * duration_factor * w_env

        return round(dpi, 3)
