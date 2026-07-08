"""Scientific Health Metrics (Constitution Phase 4).

Replaces engineering health metrics with 12 mathematical Scientific Health Metrics
that evaluate the intelligence capability of the platform.
"""

from __future__ import annotations

from typing import Any
from datetime import timedelta

from elecous.common import timeutil
from elecous.storage.database import DatabaseManager, global_databases
from elecous.infrastructure.logging import get_logger

class ScientificHealthEngine:
    def __init__(self, databases: DatabaseManager | None = None):
        self.databases = databases or global_databases()
        self.db_val = self.databases.get("validation")
        self.db_pred = self.databases.get("predictions")
        self.db_know = self.databases.get("knowledge")
        self.db_dem = self.databases.get("demand")
        self.log = get_logger("validation")

    def calculate_metrics(self) -> dict[str, float]:
        """Calculates the 12 defined Scientific Health Metrics."""
        now = timeutil.utcnow()
        week_ago = now - timedelta(days=7)
        day_ago = now - timedelta(days=1)
        year_ago = now - timedelta(days=365)

        # 1. Knowledge Velocity: New Validated Mechanisms per week
        rows = self.db_val.query_dicts(
            "SELECT count(DISTINCT object_id) as n FROM validations WHERE object_type = 'mechanism' AND outcome = 'passed' AND created_at >= ?",
            [week_ago]
        )
        knowledge_velocity = float(rows[0]["n"]) if rows else 0.0

        # 2. Knowledge Quality: Average Brier Score of Validated Mechanisms
        rows = self.db_pred.query_dicts(
            "SELECT outcome FROM predictions WHERE outcome IS NOT NULL"
        )
        brier_scores = []
        for r in rows:
            try:
                detail = r["outcome"] if isinstance(r["outcome"], dict) else __import__('json').loads(r["outcome"])
                if "metrics" in detail and "brier_score" in detail["metrics"]:
                    brier_scores.append(detail["metrics"]["brier_score"])
            except Exception:
                pass
        knowledge_quality = sum(brier_scores) / len(brier_scores) if brier_scores else 0.25 # Default 0.25 if none

        # 3. Knowledge Retention: % of Validated Mechanisms that survive >1 year
        # Stub query for surviving mechanisms
        knowledge_retention = 1.0 # 100% until we have a year of history

        # 4. Blind Spot Reduction
        rows = self.db_dem.query_dicts(
            "SELECT count(*) as c FROM blind_spots WHERE status = 'resolved' AND generated_at >= ?",
            [week_ago]
        )
        blind_spot_reduction = float(rows[0]["c"]) if rows else 0.0

        # 5. Relationship Discovery Rate: New nodes in Short-Term Memory per day
        rows = self.db_know.query_dicts(
            "SELECT count(*) as c FROM knowledge_nodes WHERE memory_tier = 'short_term' AND created_at >= ?",
            [day_ago]
        )
        discovery_rate = float(rows[0]["c"]) if rows else 0.0

        # 6. Relationship Survival Rate: Long-Term / Short-Term
        rows_lt = self.db_know.query_dicts("SELECT count(*) as c FROM knowledge_nodes WHERE memory_tier = 'long_term'")
        rows_st = self.db_know.query_dicts("SELECT count(*) as c FROM knowledge_nodes WHERE memory_tier = 'short_term'")
        lt = float(rows_lt[0]["c"]) if rows_lt else 0.0
        st = float(rows_st[0]["c"]) if rows_st else 0.0
        survival_rate = (lt / st) if st > 0 else 0.0

        # 7. Hypothesis Rejection Rate
        rows_forgotten = self.db_know.query_dicts("SELECT count(*) as c FROM knowledge_nodes WHERE memory_tier = 'forgotten'")
        forgotten = float(rows_forgotten[0]["c"]) if rows_forgotten else 0.0
        rejection_rate = (forgotten / st) if st > 0 else 0.0

        # 8. Prediction Calibration
        prediction_calibration = knowledge_quality # Proxy for now

        # 9. Prediction Quality: F1 Score
        rows = self.db_val.query_dicts("SELECT metrics FROM benchmarks WHERE suite = 'rolling_vs_baseline' ORDER BY created_at DESC LIMIT 1")
        f1_score = 0.5
        if rows:
            try:
                metrics = __import__('json').loads(rows[0]["metrics"])
                f1_score = float(metrics.get("elecous", {}).get("f1", 0.5))
            except Exception:
                pass
        prediction_quality = f1_score

        # 10. Data Freshness: Median latency (stub)
        data_freshness = 2.0 # hours

        # 11. Coverage Growth
        coverage_growth = 0.05 # 5% per week stub

        # 12. Scientific Progress Index: (F1 * Knowledge Coverage) / Brier Score
        knowledge_coverage = 0.8 # Stub
        spi = (f1_score * knowledge_coverage) / max(0.01, knowledge_quality)

        metrics = {
            "knowledge_velocity": round(knowledge_velocity, 2),
            "knowledge_quality": round(knowledge_quality, 4),
            "knowledge_retention": round(knowledge_retention, 4),
            "blind_spot_reduction": round(blind_spot_reduction, 2),
            "relationship_discovery_rate": round(discovery_rate, 2),
            "relationship_survival_rate": round(survival_rate, 4),
            "hypothesis_rejection_rate": round(rejection_rate, 4),
            "prediction_calibration": round(prediction_calibration, 4),
            "prediction_quality": round(prediction_quality, 4),
            "data_freshness_hours": round(data_freshness, 2),
            "coverage_growth": round(coverage_growth, 4),
            "scientific_progress_index": round(spi, 4),
        }

        self.log.info("scientific health metrics computed", spi=metrics["scientific_progress_index"])
        return metrics
