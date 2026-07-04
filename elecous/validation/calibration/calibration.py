"""Confidence Calibration (spec 6.17, 12.13).

If ELECOUS says 90% and reality delivers 70%, confidence is wrong. Every
prediction outcome lands here as (predicted confidence, succeeded); binned
observed rates become adjustment curves applied to future confidence values.
Confidence must reflect reality.
"""

from __future__ import annotations

from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, global_databases
from elecous.validation.schema import VALIDATION_MIGRATION


class CalibrationEngine:
    def __init__(self, databases: DatabaseManager | None = None, config: Config | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(VALIDATION_MIGRATION)
        self.db = self.databases.get("validation")
        self.config = config or active_config()
        self.bins = int(self.config.load_file("thresholds.yaml")["validation"]["calibration_bins"])
        self.log = get_logger("validation")

    def record(self, object_type: str, predicted_confidence: float, succeeded: bool) -> None:
        self.db.execute(
            "INSERT INTO calibration_records VALUES (?, ?, ?, ?)",
            [timeutil.utcnow(), object_type, float(predicted_confidence), bool(succeeded)],
        )

    def recompute(self, object_type: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT predicted_confidence, succeeded FROM calibration_records WHERE object_type = ?",
            [object_type],
        )
        if not rows:
            return []
        width = 1.0 / self.bins
        adjustments = []
        self.db.execute("DELETE FROM calibration_adjustments WHERE object_type = ?", [object_type])
        for i in range(self.bins):
            low, high = i * width, (i + 1) * width
            in_bin = [(p, s) for p, s in rows if low <= p < high or (i == self.bins - 1 and p == 1.0)]
            if not in_bin:
                continue
            predicted_mean = sum(p for p, _ in in_bin) / len(in_bin)
            observed = sum(1 for _, s in in_bin if s) / len(in_bin)
            self.db.execute(
                "INSERT INTO calibration_adjustments VALUES (?, ?, ?, ?, ?, ?, ?)",
                [object_type, low, high, predicted_mean, observed, len(in_bin), timeutil.utcnow()],
            )
            adjustments.append(
                {"bin": [low, high], "predicted_mean": predicted_mean, "observed_rate": observed, "samples": len(in_bin)}
            )
        self.log.info("calibration recomputed", object_type=object_type, bins=len(adjustments))
        return adjustments

    def calibrate(self, object_type: str, raw_confidence: float) -> float:
        """Map raw confidence through the observed-rate curve. Bins with
        fewer than 5 samples are ignored (not enough reality yet)."""
        row = self.db.query_one(
            "SELECT observed_rate, samples FROM calibration_adjustments "
            "WHERE object_type = ? AND bin_low <= ? AND bin_high > ? ORDER BY computed_at DESC LIMIT 1",
            [object_type, raw_confidence, raw_confidence],
        )
        if row is None or row[1] < 5:
            return raw_confidence
        observed = float(row[0])
        # Blend toward observed reality; never fully discard the raw signal.
        return round(0.3 * raw_confidence + 0.7 * observed, 4)

    def expected_calibration_error(self, object_type: str) -> float | None:
        rows = self.db.query(
            "SELECT predicted_mean, observed_rate, samples FROM calibration_adjustments WHERE object_type = ?",
            [object_type],
        )
        if not rows:
            return None
        total = sum(r[2] for r in rows)
        return round(sum(abs(r[0] - r[1]) * r[2] for r in rows) / total, 4) if total else None
