"""Mechanism Matching Engine (spec 8.5-8.6, 8.25).

Asks "which previously validated mechanisms resemble today's world?" instead
of "what will sell?". Activation is the share of a mechanism's required
signals currently active in the World State — partial activation is normal;
the missing evidence becomes uncertainty, not a hard failure.

Resource Intelligence (8.25): mechanisms with zero overlap with the current
World State are skipped entirely — dormant mechanisms cost nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from elecous.infrastructure.config import Config, active_config


@dataclass
class MatchResult:
    mechanism_id: str
    mechanism_version: int
    activation: float  # [0,1] share of required signals currently active
    matched_signals: list[str]
    missing_signals: list[str]
    world_condition_match: bool


class MechanismMatcher:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()
        self.threshold = float(self.config.load_file("thresholds.yaml")["prediction"]["match_threshold"])

    def match(self, mechanisms: list[dict[str, Any]], world_state: dict[str, Any]) -> list[MatchResult]:
        import json

        active_signals = {s["signal_key"] for s in world_state["signals"]}
        active_conditions = set(world_state["conditions"])
        results: list[MatchResult] = []
        for mechanism in mechanisms:
            required = set(json.loads(mechanism["supporting_signals"]))
            if not required:
                continue
            overlap = required & active_signals
            if not overlap:
                continue  # dormant: skip entirely (8.25 resource intelligence)
            # Real bug found via the Final Patch's rolling benchmark (Task
            # 6): activation measured at the concrete signal:direction:scope
            # level requires nearly every entity/country variant to fire
            # simultaneously (mechanisms carry up to 172 concrete signals
            # for as few as 5 real phenomena -- the same representation
            # mismatch already fixed in SkepticEngine.challenge_mechanism's
            # overfitting check this session). A mechanism's real claim is
            # about the phenomenon, evidenced across whichever entities
            # currently show it -- not that every entity fires at once.
            required_phenomena = {":".join(s.split(":")[:2]) for s in required}
            active_phenomena = {":".join(s.split(":")[:2]) for s in overlap}
            activation = len(active_phenomena) / len(required_phenomena)
            conditions = mechanism.get("world_conditions", [])
            if isinstance(conditions, str):
                conditions = json.loads(conditions)
            condition_match = not conditions or bool(set(conditions) & active_conditions)
            if activation < self.threshold and not condition_match:
                continue
            results.append(
                MatchResult(
                    mechanism_id=mechanism["mechanism_id"],
                    mechanism_version=int(mechanism["version"]),
                    activation=round(activation, 4),
                    matched_signals=sorted(overlap),
                    missing_signals=sorted(required - overlap),
                    world_condition_match=condition_match,
                )
            )
        return sorted(results, key=lambda r: -r.activation)
