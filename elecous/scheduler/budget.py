"""Dynamic Observation Budget (spec 2.8, 12.18).

Every hour the Resource Engine allocates Observation Units across observation
capabilities. Allocation follows measured value — a capability's recent
prediction contribution and observation health — not habit. Weights are
learned from evidence, never manually assigned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from elecous.infrastructure.config import Config, active_config


@dataclass(frozen=True)
class BudgetAllocation:
    capability: str
    units: int
    weight: float


class ObservationBudget:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()

    def allocate(
        self,
        capabilities: list[str],
        contribution: Mapping[str, float],
        health: Mapping[str, float],
    ) -> list[BudgetAllocation]:
        """Distribute the hourly unit budget.

        contribution: recent prediction contribution per capability in [0, 1]
                      (from the Validation Engine's contribution analysis).
        health:       observation health per capability in [0, 1]
                      (from Observation Health, spec 2.14).

        Every capability keeps a minimum floor so ELECOUS never goes blind on
        a capability solely because it was recently quiet — dormancy is a
        scheduling decision for the gap engine, not a budget accident.
        """
        if not capabilities:
            return []
        total_units = int(self.config.get("scheduler.observation_units_per_hour", 100))
        floor = int(self.config.get("scheduler.min_units_per_capability", 2))

        weights: dict[str, float] = {}
        for cap in capabilities:
            c = max(0.0, min(1.0, contribution.get(cap, 0.0)))
            h = max(0.0, min(1.0, health.get(cap, 0.5)))
            # Contribution dominates; health scales it. A brand-new capability
            # with no contribution history still receives exploration weight.
            exploration = 0.15
            weights[cap] = (c + exploration) * (0.5 + 0.5 * h)

        weight_sum = sum(weights.values()) or 1.0
        remaining = total_units - floor * len(capabilities)
        if remaining < 0:  # more capabilities than the budget can floor
            floor = max(1, total_units // len(capabilities))
            remaining = total_units - floor * len(capabilities)

        allocations = []
        for cap in capabilities:
            share = weights[cap] / weight_sum
            allocations.append(
                BudgetAllocation(capability=cap, units=floor + round(share * remaining), weight=round(share, 4))
            )
        return allocations
