"""Dynamic per-entity baselines (spec 3.6).

There is NO universal baseline. Every entity learns its own from its real
observation history using robust statistics (median / MAD), which tolerate
the heavy-tailed behaviour of attention data far better than mean/std.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np

from elecous.common import timeutil
from elecous.infrastructure.config import Config, active_config
from elecous.observation.store import ObservationStore
from elecous.storage.database import DatabaseManager, global_databases

from elecous.demand.schema import DEMAND_MIGRATION


@dataclass
class Baseline:
    entity: str
    signal_type: str
    as_of: date
    window_days: int
    n_points: int
    median: float
    mad: float
    mean: float
    std: float
    reliable: bool

    def robust_z(self, value: float) -> float:
        """Robust z-score; falls back to std when MAD degenerates."""
        scale = 1.4826 * self.mad
        if scale <= 1e-12:
            scale = self.std if self.std > 1e-12 else max(abs(self.median), 1.0) * 0.05
        return (value - self.median) / scale


class DynamicBaseline:
    def __init__(
        self,
        store: ObservationStore,
        config: Config | None = None,
        databases: DatabaseManager | None = None,
    ):
        self.store = store
        self.config = config or active_config()
        self.databases = databases or global_databases()
        self.databases.register_schema(DEMAND_MIGRATION)
        self.db = self.databases.get("demand")
        self.thresholds = self.config.load_file("thresholds.yaml")["demand"]

    def compute(
        self, entity: str, signal_type: str, as_of: datetime, persist: bool = True
    ) -> Baseline | None:
        """Baseline from the trailing window ending strictly *before* the
        evaluation window, so a spike never contaminates its own baseline.
        ``as_of`` marks the start of the evaluation window."""
        window_days = int(self.thresholds["baseline_window_days"])
        start = as_of - timedelta(days=window_days)
        series = self.store.series(entity, signal_type, start, as_of)
        values = np.array([p["value"] for p in series], dtype=float)
        if values.size == 0:
            return None
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        baseline = Baseline(
            entity=entity,
            signal_type=signal_type,
            as_of=timeutil.day_of(as_of),
            window_days=window_days,
            n_points=int(values.size),
            median=median,
            mad=mad,
            mean=float(np.mean(values)),
            std=float(np.std(values)),
            reliable=self._is_reliable(values, window_days),
        )
        if persist:
            self.db.execute(
                "INSERT INTO baselines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    entity,
                    signal_type,
                    timeutil.utcnow(),
                    baseline.as_of,
                    window_days,
                    baseline.n_points,
                    baseline.median,
                    baseline.mad,
                    baseline.mean,
                    baseline.std,
                    baseline.reliable,
                ],
            )
        return baseline

    def _is_reliable(self, values: np.ndarray, window_days: int) -> bool:
        coverage = values.size / window_days
        if coverage < 0.6:
            return False
        # A baseline is unreliable while the series is itself exploding:
        # compare the two window halves.
        half = values.size // 2
        if half >= 5:
            first, second = np.median(values[:half]), np.median(values[half:])
            if first > 0 and (second / first > 2.0 or second / first < 0.5):
                return False
        return not math.isclose(float(np.max(values)), 0.0)
