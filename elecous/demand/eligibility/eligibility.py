"""Product Eligibility Engine (spec 3.5).

Research only begins when stable historical behaviour exists. Rejected:
entities without historical baselines, with insufficient public
observations, or with unreliable tracking (large gaps).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from elecous.infrastructure.config import Config, active_config
from elecous.observation.store import ObservationStore


@dataclass
class EligibilityDecision:
    entity: str
    signal_type: str
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    history_days: int = 0
    observation_count: int = 0
    max_gap_days: int = 0


class EligibilityEngine:
    def __init__(self, store: ObservationStore, config: Config | None = None):
        self.store = store
        self.config = config or active_config()
        self.thresholds = self.config.load_file("thresholds.yaml")["demand"]

    def check(self, entity: str, signal_type: str, as_of: datetime) -> EligibilityDecision:
        # Constitution Phase 2: Enforce 5-year historical backfill requirement
        min_history = 1825  # 5 years
        min_observations = int(self.thresholds.get("min_observations", 1800))
        max_gap = int(self.thresholds.get("max_gap_days", 3))

        series = self.store.series(entity, signal_type, as_of - timedelta(days=min_history + 30), as_of)
        decision = EligibilityDecision(entity=entity, signal_type=signal_type, eligible=True)
        decision.observation_count = len(series)
        if not series:
            decision.eligible = False
            decision.reasons.append("no_observations")
            return decision

        first, last = series[0]["timestamp"], series[-1]["timestamp"]
        decision.history_days = (last - first).days
        if decision.history_days < min_history:
            decision.eligible = False
            decision.reasons.append(f"history_{decision.history_days}d_lt_{min_history}d")
        if decision.observation_count < min_observations:
            decision.eligible = False
            decision.reasons.append(f"observations_{decision.observation_count}_lt_{min_observations}")

        gaps = [
            (series[i]["timestamp"] - series[i - 1]["timestamp"]).days
            for i in range(1, len(series))
        ]
        decision.max_gap_days = max(gaps) if gaps else 0
        if decision.max_gap_days > max_gap:
            decision.eligible = False
            decision.reasons.append(f"gap_{decision.max_gap_days}d_gt_{max_gap}d")
        return decision
