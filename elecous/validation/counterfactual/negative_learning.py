"""Negative Learning Engine (spec 6.15, 3.22, 5.21).

A failed prediction is never wasted. Every failure is investigated along a
fixed question ladder and the answer becomes research, not blame.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.infrastructure.logging import get_logger


class NegativeLearningEngine:
    def __init__(self, mechanisms, factors, discovery, world):
        self.mechanisms = mechanisms  # MechanismStore
        self.factors = factors  # FactorStore
        self.discovery = discovery  # EpisodeSignalDiscovery
        self.world = world  # WorldStateBuilder
        self.log = get_logger("validation")

    def investigate_failed_prospective(self, mechanism_id: str, prospective_row: dict[str, Any]) -> dict[str, Any]:
        mechanism = self.mechanisms.latest(mechanism_id)
        if mechanism is None:
            return {}
        signals = set(json.loads(mechanism["supporting_signals"]))
        issued = timeutil.ensure_utc(prospective_row["issued_at"])
        deadline = timeutil.ensure_utc(prospective_row["expected_by"])

        # Wrong timing: did the mechanism's signals reactivate shortly after
        # the deadline (evidence timing was off, not the mechanism itself)?
        state_after = self.world.state_at(deadline + (deadline - issued))
        active_after = {s["signal_key"] for s in state_after["signals"]} if state_after else set()
        wrong_timing = bool(signals & active_after) and len(signals & active_after) / len(signals) >= 0.5

        # Missing factor: activation never reached full strength even though
        # some signals fired — a factor in the mechanism may be incomplete.
        state_during = self.world.state_at(deadline)
        active_during = {s["signal_key"] for s in state_during["signals"]} if state_during else set()
        partial_only = bool(signals & active_during) and not signals <= active_during

        # Wrong geography: signals fired in scopes outside the mechanism's
        # registered countries.
        countries = set(json.loads(mechanism["countries"]))
        fired_scopes = {k.split(":")[2] for k in (signals & active_during)}
        wrong_geography = bool(countries) and bool(fired_scopes - countries)

        # World regime changed: the underlying factor's confidence has
        # degraded since the mechanism was built.
        factor_ids = json.loads(mechanism["environmental_factors"])
        regime_changed = False
        for factor_id in factor_ids:
            factor = self.factors.latest(factor_id)
            if factor and float(factor["current_confidence"]) < 0.3:
                regime_changed = True

        questions = {
            "wrong_timing": wrong_timing,
            "missing_factor": partial_only,
            "wrong_geography": wrong_geography,
            "world_regime_changed": regime_changed,
            "concept_drift_suspected": regime_changed and not wrong_timing,
        }
        self.log.info("negative learning investigation", mechanism_id=mechanism_id, **questions)
        return questions
