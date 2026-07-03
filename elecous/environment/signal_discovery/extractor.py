"""Environmental signal extraction (spec 4.5, 4.13).

For a moment in time, answers: which parts of the observable world changed,
in which direction, where, how strongly, and when did the change begin?

A signal change is expressed in the shared phenomenon vocabulary
(``thermal_stress:up:US``) so different providers and units collapse into
one scientific language (Semantic Engine, 4.13). Values are aggregated to
daily means per (phenomenon, scope) and compared to the preceding baseline
window with robust statistics — the same instrument family the Demand Engine
uses, applied to the world instead of the product.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from elecous.common import timeutil
from elecous.infrastructure.config import Config, active_config
from elecous.observation.normalizers.semantic import SemanticNormalizer
from elecous.observation.store import ObservationStore

#: Observation categories describing the world (never the product) — 1.5.
ENVIRONMENTAL_CATEGORIES = (
    "weather",
    "air_quality",
    "weather_hazard",
    "geophysical",
    "calendar",
    "economics",
    "energy",
    "media",
)


@dataclass(frozen=True)
class SignalChange:
    signal_key: str  # phenomenon:direction:scope
    phenomenon: str
    direction: str  # up | down
    scope: str  # ISO country, media entity, or "global"
    z: float
    first_crossed_at: datetime | None
    observation_types: tuple[str, ...]


class EnvironmentalSignalExtractor:
    def __init__(self, store: ObservationStore, config: Config | None = None):
        self.store = store
        self.config = config or active_config()
        self.normalizer = SemanticNormalizer(self.config)
        self.thresholds = self.config.load_file("thresholds.yaml")["environment"]

    def changes(
        self,
        window_end: datetime,
        window_days: int | None = None,
        baseline_days: int = 28,
    ) -> list[SignalChange]:
        """Signal changes in [window_end - window_days, window_end] measured
        against the preceding baseline window."""
        window_days = window_days or int(self.thresholds["lookback_days"])
        window_end = timeutil.ensure_utc(window_end)
        window_start = window_end - timedelta(days=window_days)
        baseline_start = window_start - timedelta(days=baseline_days)
        change_z = float(self.thresholds["change_z"])

        rows = self.store.db.query_dicts(
            """
            SELECT observation_type, category,
                   coalesce(country, CASE WHEN category IN ('media','attention') THEN entity ELSE 'global' END) AS scope,
                   CAST(timestamp AS DATE) AS day,
                   avg(value) AS value
            FROM observations
            WHERE timestamp >= ? AND timestamp <= ? AND category IN ({})
            GROUP BY 1, 2, 3, 4
            ORDER BY 1, 3, 4
            """.format(",".join("?" * len(ENVIRONMENTAL_CATEGORIES))),
            [baseline_start, window_end, *ENVIRONMENTAL_CATEGORIES],
        )

        series: dict[tuple[str, str], list[tuple[datetime, float]]] = {}
        for row in rows:
            key = (row["observation_type"], str(row["scope"]))
            day = row["day"]
            ts = datetime(day.year, day.month, day.day, tzinfo=timeutil.UTC)
            series.setdefault(key, []).append((ts, float(row["value"])))

        changes: list[SignalChange] = []
        for (obs_type, scope), points in series.items():
            phenomenon = self.normalizer.phenomenon_for(obs_type)
            if phenomenon is None:
                continue
            baseline_values = np.array(
                [v for ts, v in points if ts < window_start], dtype=float
            )
            window_points = [(ts, v) for ts, v in points if ts >= window_start]
            if baseline_values.size < 7 or len(window_points) < 3:
                continue
            median = float(np.median(baseline_values))
            mad = float(np.median(np.abs(baseline_values - median)))
            scale = 1.4826 * mad
            if scale <= 1e-12:
                std = float(np.std(baseline_values))
                scale = std if std > 1e-12 else max(abs(median), 1.0) * 0.05
            zs = [(ts, (v - median) / scale) for ts, v in window_points]
            peak_ts, peak_z = max(zs, key=lambda t: abs(t[1]))
            if abs(peak_z) < change_z:
                continue
            direction = "up" if peak_z > 0 else "down"
            crossings = [ts for ts, z in zs if (z >= change_z if direction == "up" else z <= -change_z)]
            changes.append(
                SignalChange(
                    signal_key=f"{phenomenon}:{direction}:{scope}",
                    phenomenon=phenomenon,
                    direction=direction,
                    scope=scope,
                    z=round(float(peak_z), 3),
                    first_crossed_at=min(crossings) if crossings else peak_ts,
                    observation_types=(obs_type,),
                )
            )
        # Merge changes of the same signal key arising from different
        # observation types (e.g. two providers measuring the same
        # phenomenon): strongest z wins, evidence types union.
        merged: dict[str, SignalChange] = {}
        for change in changes:
            existing = merged.get(change.signal_key)
            if existing is None:
                merged[change.signal_key] = change
            else:
                merged[change.signal_key] = SignalChange(
                    signal_key=change.signal_key,
                    phenomenon=change.phenomenon,
                    direction=change.direction,
                    scope=change.scope,
                    z=max(existing.z, change.z, key=abs),
                    first_crossed_at=min(
                        t for t in (existing.first_crossed_at, change.first_crossed_at) if t is not None
                    )
                    if (existing.first_crossed_at or change.first_crossed_at)
                    else None,
                    observation_types=tuple(sorted(set(existing.observation_types) | set(change.observation_types))),
                )
        return sorted(merged.values(), key=lambda c: -abs(c.z))
