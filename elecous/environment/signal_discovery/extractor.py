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
from elecous.infrastructure.cache import TTLCache
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
    # Provenance (rewrite directive, Level 2 source intelligence): which
    # source_ids' observations fell inside the window that produced this
    # change. Defaulted so existing callers constructing SignalChange by
    # keyword are unaffected. Does not participate in the z-score math.
    contributing_sources: tuple[str, ...] = ()
    # Multi-mode extraction (Phase 2): which detection mode(s) produced this
    # change. A phenomenon can register via more than one mode; all that
    # agreed on direction are recorded. Defaulted to ("spike",) so existing
    # callers/tests constructing SignalChange directly are unaffected.
    modes: tuple[str, ...] = ("spike",)


class EnvironmentalSignalExtractor:
    def __init__(self, store: ObservationStore, config: Config | None = None):
        self.store = store
        self.config = config or active_config()
        self.normalizer = SemanticNormalizer(self.config)
        self.thresholds = self.config.load_file("thresholds.yaml")["environment"]
        # Cache (spec 10.25): the same instant is queried repeatedly across
        # mechanisms during a single replay/research pass. Short TTL keeps
        # live World State queries fresh relative to the observation
        # cadence (minutes-to-hours) while eliminating redundant aggregation.
        self._cache = TTLCache(ttl_seconds=120.0, max_entries=256)

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
        cache_key = (window_end.isoformat(), window_days, baseline_days)
        return self._cache.get_or_compute(cache_key, lambda: self._compute_changes(window_end, window_days, baseline_days))

    def _compute_changes(self, window_end: datetime, window_days: int, baseline_days: int) -> list[SignalChange]:
        window_start = window_end - timedelta(days=window_days)
        baseline_start = window_start - timedelta(days=baseline_days)
        change_z = float(self.thresholds["change_z"])

        rows = self.store.db.query_dicts(
            """
            SELECT observation_type, category,
                   coalesce(country, CASE WHEN category IN ('media','attention') THEN entity ELSE 'global' END) AS scope,
                   -- Real leakage bug caught by the constitution guard on
                   -- the 2015-deep backfill: a bare CAST(ts AS DATE) uses
                   -- the SESSION timezone (+05:30 here), so a 23:59-UTC
                   -- observation lands on the NEXT day, which line ~104
                   -- then reconstructs as midnight UTC -- one minute past
                   -- the observation's real time and past window_end.
                   -- AT TIME ZONE 'UTC' makes day boundaries UTC, matching
                   -- how every timestamp in ELECOUS is stored and compared.
                   CAST(timestamp AT TIME ZONE 'UTC' AS DATE) AS day,
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

        # Provenance side-query (Level 2 source intelligence): which sources
        # actually produced the observations inside the anomaly window
        # itself (not the baseline). Kept separate from the z-score
        # aggregation above so the statistical computation is untouched.
        source_rows = self.store.db.query_dicts(
            """
            SELECT observation_type,
                   coalesce(country, CASE WHEN category IN ('media','attention') THEN entity ELSE 'global' END) AS scope,
                   list(DISTINCT source_id) AS source_ids
            FROM observations
            WHERE timestamp >= ? AND timestamp <= ? AND category IN ({})
            GROUP BY 1, 2
            """.format(",".join("?" * len(ENVIRONMENTAL_CATEGORIES))),
            [window_start, window_end, *ENVIRONMENTAL_CATEGORIES],
        )
        sources_by_key: dict[tuple[str, str], tuple[str, ...]] = {
            (row["observation_type"], str(row["scope"])): tuple(sorted(row["source_ids"])) for row in source_rows
        }

        trend_min_slope_z = float(self.thresholds.get("trend_min_slope_z", 0.8))
        trend_min_corr = float(self.thresholds.get("trend_min_correlation", 0.6))
        level_percentile = float(self.thresholds.get("level_percentile", 0.85))

        changes: list[SignalChange] = []
        processed_series: dict[tuple[str, str], dict[datetime.date, float]] = {}
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

            candidates: dict[str, tuple[float, datetime | None, str]] = {}

            def register(direction, magnitude, at, mode):
                if direction in candidates:
                    old_mag, old_at, old_modes = candidates[direction]
                    candidates[direction] = (max(magnitude, old_mag), min(at, old_at), old_modes + "+" + mode)
                else:
                    candidates[direction] = (magnitude, at, mode)

            # Mode 1 -- Spike (original, unchanged logic): a single point
            # deviates sharply from the recent baseline. Fires for volatile
            # phenomena (weather, media); rarely fires for slow-moving ones.
            zs = [(ts, (v - median) / scale) for ts, v in window_points]
            peak_ts, peak_z = max(zs, key=lambda t: abs(t[1]))
            if abs(peak_z) >= change_z:
                direction = "up" if peak_z > 0 else "down"
                crossings = [ts for ts, z in zs if (z >= change_z if direction == "up" else z <= -change_z)]
                register(direction, abs(peak_z), min(crossings) if crossings else peak_ts, "spike")

            # Mode 2 -- Trend: root cause of the measured weather/media bias
            # (docs/manual/AUDIT_environmental_bias.md) was that phenomena
            # like inflation or energy demand move persistently without any
            # single day spiking. A significant, consistent slope over the
            # whole fetched series (baseline+window) is a real, distinct
            # kind of environmental change from a spike.
            all_points = sorted(points, key=lambda p: p[0])
            if len(all_points) >= 10:
                key = (phenomenon, scope)
                if key not in processed_series:
                    processed_series[key] = {p[0].date(): p[1] for p in all_points}

                idx = np.arange(len(all_points), dtype=float)
                vals = np.array([v for _, v in all_points], dtype=float)
                slope = float(np.polyfit(idx, vals, 1)[0])
                corr = float(np.corrcoef(idx, vals)[0, 1]) if np.std(vals) > 1e-12 else 0.0
                slope_z = slope * len(all_points) / scale  # total real movement over the series, in baseline-scale units
                if abs(slope_z) >= trend_min_slope_z and abs(corr) >= trend_min_corr:
                    direction = "up" if slope > 0 else "down"
                    register(direction, abs(slope_z), all_points[-1][0], "trend")

            # Mode 3 -- Level
            window_mean = float(np.mean([v for _, v in window_points]))
            if baseline_values.size >= 10:
                rank = float((baseline_values < window_mean).mean())
                effect_size = abs(window_mean - median) / scale
                if effect_size >= change_z:
                    if rank >= level_percentile:
                        register("up", rank * effect_size, window_points[-1][0], "level")
                    elif rank <= (1.0 - level_percentile):
                        register("down", (1.0 - rank) * effect_size, window_points[-1][0], "level")

            # Mode 4 -- Regime: a sudden step change in the mean between the first
            # and second half of the baseline, which persists into the window.
            if baseline_values.size >= 14:
                half = baseline_values.size // 2
                mean1 = float(np.mean(baseline_values[:half]))
                mean2 = float(np.mean(baseline_values[half:]))
                regime_shift = (mean2 - mean1) / scale
                if abs(regime_shift) >= change_z:
                    window_diff = (window_mean - mean1) / scale
                    # Only register if the window maintains the new regime
                    if abs(window_diff) >= change_z and np.sign(regime_shift) == np.sign(window_diff):
                        direction = "up" if regime_shift > 0 else "down"
                        register(direction, abs(regime_shift), all_points[half][0], "regime")

            # Mode 5 -- Cyclic: detects strong weekly seasonality (7-day lag autocorrelation)
            # that is broken or strengthened in the window period.
            if len(all_points) >= 21:
                vals = np.array([v for _, v in all_points], dtype=float)
                if np.std(vals) > 1e-12:
                    autocorr_7 = float(np.corrcoef(vals[:-7], vals[7:])[0, 1])
                    if autocorr_7 > 0.6:  # Strong weekly seasonality
                        # Is the cycle amplitude increasing?
                        window_std = float(np.std([v for _, v in window_points]))
                        baseline_std = float(np.std(baseline_values))
                        if baseline_std > 1e-12:
                            amp_ratio = window_std / baseline_std
                            if amp_ratio > 1.5:
                                register("up", amp_ratio, window_points[0][0], "cyclic")
                            elif amp_ratio < 0.5:
                                register("down", 1.0 / amp_ratio, window_points[0][0], "cyclic")

            # Mode 6 -- Momentum: second derivative (acceleration).
            # Checks if the trend slope in the second half is significantly steeper than the first.
            if len(all_points) >= 14:
                half = len(all_points) // 2
                vals1 = np.array([v for _, v in all_points[:half]], dtype=float)
                vals2 = np.array([v for _, v in all_points[half:]], dtype=float)
                idx1 = np.arange(len(vals1), dtype=float)
                idx2 = np.arange(len(vals2), dtype=float)
                slope1 = float(np.polyfit(idx1, vals1, 1)[0]) if np.std(vals1) > 1e-12 else 0.0
                slope2 = float(np.polyfit(idx2, vals2, 1)[0]) if np.std(vals2) > 1e-12 else 0.0
                momentum = slope2 - slope1
                momentum_z = momentum * half / scale
                if abs(momentum_z) >= trend_min_slope_z and np.sign(slope1) == np.sign(slope2) and abs(slope2) > abs(slope1) * 1.5:
                    direction = "up" if momentum > 0 else "down"
                    register(direction, abs(momentum_z), all_points[half][0], "momentum")

            # Mode 7 -- Persistence: an anomaly that lasts for a significant duration.
            # If >80% of window points are anomalous in the same direction.
            if len(window_points) >= 3:
                ups = sum(1 for ts, v in zs if v >= change_z)
                downs = sum(1 for ts, v in zs if v <= -change_z)
                if ups / len(window_points) >= 0.8:
                    register("up", ups / len(window_points) * change_z, window_points[0][0], "persistence")
                elif downs / len(window_points) >= 0.8:
                    register("down", downs / len(window_points) * change_z, window_points[0][0], "persistence")

            for direction, (magnitude, at, modes) in candidates.items():
                modes_tuple = tuple(modes.split("+"))
                if len(modes_tuple) > 1:
                    modes_tuple = modes_tuple + ("composite",)
                changes.append(
                    SignalChange(
                        signal_key=f"{phenomenon}:{direction}:{scope}",
                        phenomenon=phenomenon,
                        direction=direction,
                        scope=scope,
                        z=round(magnitude, 3),
                        first_crossed_at=at,
                        observation_types=(obs_type,),
                        contributing_sources=sources_by_key.get((obs_type, scope), ()),
                        modes=modes_tuple,
                    )
                )

        # Modes 8 & 9: Correlation and Divergence (Joint Analysis)
        # O(N^2) comparison across different phenomena in the same scope
        phenom_scope_keys = list(processed_series.keys())
        for i in range(len(phenom_scope_keys)):
            phenomA, scopeA = phenom_scope_keys[i]
            dictA = processed_series[(phenomA, scopeA)]
            for j in range(i + 1, len(phenom_scope_keys)):
                phenomB, scopeB = phenom_scope_keys[j]
                if scopeA != scopeB:
                    continue
                dictB = processed_series[(phenomB, scopeB)]
                
                common_dates = sorted(set(dictA.keys()).intersection(dictB.keys()))
                if len(common_dates) < 14:
                    continue
                
                baseline_dates = [d for d in common_dates if d < window_start.date()]
                window_dates = [d for d in common_dates if d >= window_start.date()]
                
                if len(baseline_dates) < 7 or len(window_dates) < 3:
                    continue
                    
                valsA_base = np.array([dictA[d] for d in baseline_dates], dtype=float)
                valsB_base = np.array([dictB[d] for d in baseline_dates], dtype=float)
                
                stdA = np.std(valsA_base)
                stdB = np.std(valsB_base)
                if stdA < 1e-12 or stdB < 1e-12:
                    continue
                
                base_corr = float(np.corrcoef(valsA_base, valsB_base)[0, 1])
                
                valsA_win = np.array([dictA[d] for d in window_dates], dtype=float)
                valsB_win = np.array([dictB[d] for d in window_dates], dtype=float)
                
                win_stdA = np.std(valsA_win)
                win_stdB = np.std(valsB_win)
                if win_stdA < 1e-12 or win_stdB < 1e-12:
                    continue
                    
                win_corr = float(np.corrcoef(valsA_win, valsB_win)[0, 1])
                
                # Mode 8: Correlation (they become highly correlated in the window)
                if abs(base_corr) < 0.4 and abs(win_corr) > 0.8:
                    direction = "up" if win_corr > 0 else "down"
                    at = datetime.combine(window_dates[0], datetime.min.time(), tzinfo=timeutil.UTC)
                    z_val = round(abs(win_corr) * change_z, 3)
                    
                    changes.append(SignalChange(
                        signal_key=f"{phenomA}:{direction}:{scopeA}",
                        phenomenon=phenomA, direction=direction, scope=scopeA, z=z_val, first_crossed_at=at,
                        observation_types=("joint_analysis",), contributing_sources=(), modes=("correlation",)
                    ))
                    changes.append(SignalChange(
                        signal_key=f"{phenomB}:{direction}:{scopeB}",
                        phenomenon=phenomB, direction=direction, scope=scopeB, z=z_val, first_crossed_at=at,
                        observation_types=("joint_analysis",), contributing_sources=(), modes=("correlation",)
                    ))

                # Mode 9: Divergence (historically correlated, but breaks in window)
                elif abs(base_corr) > 0.7 and abs(win_corr) < 0.2:
                    direction = "down"
                    at = datetime.combine(window_dates[0], datetime.min.time(), tzinfo=timeutil.UTC)
                    z_val = round(abs(base_corr - win_corr) * change_z, 3)
                    
                    changes.append(SignalChange(
                        signal_key=f"{phenomA}:{direction}:{scopeA}",
                        phenomenon=phenomA, direction=direction, scope=scopeA, z=z_val, first_crossed_at=at,
                        observation_types=("joint_analysis",), contributing_sources=(), modes=("divergence",)
                    ))
                    changes.append(SignalChange(
                        signal_key=f"{phenomB}:{direction}:{scopeB}",
                        phenomenon=phenomB, direction=direction, scope=scopeB, z=z_val, first_crossed_at=at,
                        observation_types=("joint_analysis",), contributing_sources=(), modes=("divergence",)
                    ))

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
                    contributing_sources=tuple(
                        sorted(set(existing.contributing_sources) | set(change.contributing_sources))
                    ),
                    modes=tuple(sorted(set(existing.modes) | set(change.modes))),
                )
        return sorted(merged.values(), key=lambda c: -abs(c.z))

    def invalidate_cache(self) -> None:
        """Force fresh computation (used by tests and after bulk ingestion
        when 120s staleness is not acceptable)."""
        self._cache.invalidate()

    def cache_stats(self) -> dict:
        return self._cache.stats()
