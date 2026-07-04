"""The Skeptic Engine (spec 4.25, 5.18, 6.16).

Every factor and mechanism receives an opponent whose only job is to destroy
it. Each challenge is a measurable check against real evidence; anything
surviving all challenges grows stronger (its survival is recorded in the
validation history).
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.logging import get_logger


class SkepticEngine:
    def __init__(self, discovery, episodes, factors):
        self.discovery = discovery  # EpisodeSignalDiscovery
        self.episodes = episodes  # EpisodeStore
        self.factors = factors  # FactorStore
        self.log = get_logger("validation")

    def challenge_factor(self, factor: dict[str, Any]) -> dict[str, Any]:
        signals = set(json.loads(factor["signals"]))
        supporting = json.loads(factor["supporting_episodes"])
        category = json.loads(factor["categories"])[0] if json.loads(factor["categories"]) else None
        challenges: dict[str, dict[str, Any]] = {}

        # Coincidence: with few supporting episodes and many mined patterns,
        # random co-occurrence is likely. Requires lift AND support.
        challenges["coincidence"] = {
            "passed": int(factor["support"]) >= 3 and float(factor["lift"]) >= 1.8,
            "support": factor["support"],
            "lift": factor["lift"],
        }

        # Seasonality: if supporting episodes all fall in the same 8-week
        # calendar band of different years, the factor may just be a season.
        weeks = []
        for episode_id in supporting:
            episode = self.episodes.latest(episode_id)
            if episode:
                weeks.append(timeutil.ensure_utc(episode["spike_start"]).isocalendar().week)
        seasonal = len(weeks) >= 3 and (max(weeks) - min(weeks)) <= 8
        challenges["seasonality"] = {
            "passed": not seasonal or len(set(weeks)) <= 1,  # single observation year cannot prove seasonality either way
            "weeks": weeks,
            "note": "flagged seasonal band" if seasonal else "spread across calendar",
        }

        # Duplication: near-identical sibling factor already exists.
        duplicates = []
        for other in self.factors.list_latest():
            if other["factor_id"] == factor["factor_id"] or other.get("merged_into"):
                continue
            other_signals = set(json.loads(other["signals"]))
            union = signals | other_signals
            if union and len(signals & other_signals) / len(union) >= 0.9:
                duplicates.append(other["factor_id"])
        challenges["duplication"] = {"passed": not duplicates, "duplicates": duplicates}

        # Sampling bias: all evidence from a single entity.
        entities = set()
        for episode_id in supporting:
            episode = self.episodes.latest(episode_id)
            if episode:
                entities.add(episode["entity"])
        challenges["sampling_bias"] = {"passed": len(entities) >= 2, "entities": sorted(entities)}

        # Data leakage: a signal that first crossed AFTER the spike began
        # cannot have preceded it.
        lead_times = self.discovery.episode_lead_times(supporting)
        leaks = [
            (episode_id, key)
            for episode_id, keys in lead_times.items()
            for key, days in keys.items()
            if key in signals and days is not None and days < 0
        ]
        challenges["data_leakage"] = {"passed": not leaks, "leaks": leaks}

        # Simpler explanation: a strict subset with at least the same
        # support explains as much with less (Appendix A Principle 4).
        simpler = []
        for other in self.factors.list_latest(category=category):
            if other["factor_id"] == factor["factor_id"]:
                continue
            other_signals = set(json.loads(other["signals"]))
            if other_signals < signals and int(other["support"]) >= int(factor["support"]) and float(other["lift"]) >= float(factor["lift"]):
                simpler.append(other["factor_id"])
        challenges["simpler_explanation"] = {"passed": not simpler, "simpler": simpler}

        survived = all(c["passed"] for c in challenges.values())
        report = {"survived": survived, "challenges": challenges}
        self.log.info("skeptic challenged factor", factor_id=factor["factor_id"], survived=survived)
        return report

    def challenge_mechanism(self, mechanism: dict[str, Any], factor_reports: list[dict[str, Any]]) -> dict[str, Any]:
        """A mechanism inherits its factors' challenges plus mechanism-level
        checks: overfitting and regional bias."""
        challenges: dict[str, dict[str, Any]] = {}
        challenges["factor_survival"] = {
            "passed": bool(factor_reports) and all(r["survived"] for r in factor_reports),
            "factors": len(factor_reports),
        }
        signals = json.loads(mechanism["supporting_signals"])
        episodes = json.loads(mechanism["supporting_episodes"])
        # Overfitting: more explanatory signals than independent evidence.
        challenges["overfitting"] = {
            "passed": len(episodes) >= max(3, len(signals)),
            "signals": len(signals),
            "episodes": len(episodes),
        }
        countries = json.loads(mechanism["countries"])
        scopes = {k.split(":")[2] for k in signals}
        challenges["regional_bias"] = {
            "passed": len(scopes) >= 2 or not countries or len(countries) >= 2 or "global" in scopes,
            "scopes": sorted(scopes),
        }
        survived = all(c["passed"] for c in challenges.values())
        return {"survived": survived, "challenges": challenges}
