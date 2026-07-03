"""Observation Health (spec 2.14, 6.6).

Every source continuously receives reliability, freshness, availability,
latency, completeness, prediction contribution, schema stability and error
rate — computed from its recorded connector runs, never asserted. Low
performers lose priority through the budget and scheduler.
"""

from __future__ import annotations

from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.logging import get_logger
from elecous.observation.registry.registry import SourceRegistry

#: update_frequency -> expected freshness horizon in hours
FRESHNESS_HORIZON_H = {"hourly": 6, "daily": 48, "weekly": 24 * 8, "monthly": 24 * 35}


class ObservationHealth:
    def __init__(self, registry: SourceRegistry):
        self.registry = registry
        self.log = get_logger("observation")

    def score(self, source_id: str) -> dict[str, Any]:
        source = self.registry.get(source_id)
        if source is None:
            raise KeyError(source_id)
        runs = self.registry.runs(source_id, limit=50)
        live_runs = [r for r in runs if r["run_type"] in ("live", "backfill")]
        if not live_runs:
            return {
                "source_id": source_id,
                "reliability": source["reliability"],
                "freshness": 0.0,
                "availability": 0.0,
                "latency_ms": None,
                "completeness": 0.0,
                "contribution": source["historical_contribution"],
                "schema_stability": 1.0,
                "error_rate": 0.0,
                "health": source["health"],
            }

        successes = [r for r in live_runs if r["success"]]
        availability = len(successes) / len(live_runs)
        error_rate = 1.0 - availability

        latencies = [r["latency_ms"] for r in successes if r["latency_ms"] is not None]
        latency = sum(latencies) / len(latencies) if latencies else None
        latency_score = 1.0 if latency is None else max(0.0, min(1.0, 1.0 - (latency / 30000.0)))

        # Freshness: how recently a successful extraction happened relative
        # to the source's declared cadence.
        freshness = 0.0
        if source["last_successful_extraction"] is not None:
            age_h = (timeutil.utcnow() - timeutil.ensure_utc(source["last_successful_extraction"])).total_seconds() / 3600
            horizon = FRESHNESS_HORIZON_H.get(source["update_frequency"], 48)
            freshness = max(0.0, min(1.0, 1.0 - age_h / (horizon * 2)))

        # Completeness: share of successful runs that actually produced
        # accepted or merged observations.
        productive = [r for r in successes if (r["observations_accepted"] or 0) + (r["observations_merged"] or 0) > 0]
        completeness = len(productive) / len(successes) if successes else 0.0

        # Schema stability: distinct payload shapes across recent runs.
        hashes = {r["schema_hash"] for r in successes if r["schema_hash"]}
        schema_stability = 1.0 if len(hashes) <= 1 else max(0.0, 1.0 - 0.25 * (len(hashes) - 1))

        # Reliability blends availability with rejection behaviour: a source
        # whose observations get rejected by validation is unreliable even
        # when its HTTP endpoint is healthy.
        totals = [
            ((r["observations_accepted"] or 0) + (r["observations_merged"] or 0) + (r["observations_duplicate"] or 0),
             (r["observations_rejected"] or 0))
            for r in successes
        ]
        produced = sum(t[0] for t in totals)
        rejected = sum(t[1] for t in totals)
        rejection_rate = rejected / (produced + rejected) if produced + rejected else 0.0
        reliability = availability * (1.0 - rejection_rate)

        contribution = float(source["historical_contribution"] or 0.0)
        health = (
            0.30 * reliability
            + 0.20 * freshness
            + 0.15 * completeness
            + 0.15 * schema_stability
            + 0.10 * latency_score
            + 0.10 * contribution
        )
        return {
            "source_id": source_id,
            "reliability": round(reliability, 4),
            "freshness": round(freshness, 4),
            "availability": round(availability, 4),
            "latency_ms": round(latency, 1) if latency is not None else None,
            "completeness": round(completeness, 4),
            "contribution": round(contribution, 4),
            "schema_stability": round(schema_stability, 4),
            "error_rate": round(error_rate, 4),
            "health": round(health, 4),
        }

    def refresh_all(self) -> list[dict[str, Any]]:
        """Recompute and persist health for every non-retired source."""
        reports = []
        for source in self.registry.list():
            if source["status"] == "retired":
                continue
            report = self.score(source["source_id"])
            self.registry.update_health(
                source["source_id"], report["health"], report["reliability"], report["latency_ms"]
            )
            reports.append(report)
        return reports

    def capability_health(self) -> dict[str, float]:
        """Mean health per observation capability (budget input, spec 2.8)."""
        by_capability: dict[str, list[float]] = {}
        for source in self.registry.list():
            if source["status"] in ("retired", "suspended"):
                continue
            by_capability.setdefault(source["observation_capability"], []).append(float(source["health"]))
        return {cap: sum(vals) / len(vals) for cap, vals in by_capability.items() if vals}
