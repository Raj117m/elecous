"""Historical validation by walk-forward replay (spec 6.11).

For weekly instants across observed history, the mechanism's activation is
computed from observations up to that instant ONLY (the extractor's window
ends there; the constitution guard double-checks), then compared against
whether a genuine episode followed within the mechanism's lead-time horizon.
Precision, recall and lead-time error emerge from real history — never from
simulation.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from elecous.common import timeutil
from elecous.governance import constitution
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger

#: Resource Intelligence (spec 8.25, 10.24): historical replay must never
#: become unbounded CPU work. If real history is longer than this many
#: weekly-scale instants, the replay samples evenly across it instead of
#: walking every step — precision/recall remain statistically meaningful
#: while wall-clock time stays bounded on a MacBook.
DEFAULT_MAX_INSTANTS = 60


class HistoricalReplay:
    def __init__(self, extractor, episodes, config: Config | None = None):
        self.extractor = extractor  # EnvironmentalSignalExtractor
        self.episodes = episodes  # EpisodeStore
        self.config = config or active_config()
        self.log = get_logger("validation")

    def replay_mechanism(
        self,
        mechanism: dict[str, Any],
        step_days: int = 7,
        activation_threshold: float = 0.99,
        max_instants: int | None = None,
    ) -> dict[str, Any]:
        signals = set(json.loads(mechanism["supporting_signals"]))
        categories = json.loads(mechanism["affected_categories"])
        lead = json.loads(mechanism["lead_time"])
        horizon = max(float(lead.get("max") or 14.0), 7.0)

        bounds = self.extractor.store.db.query_one("SELECT min(timestamp), max(timestamp) FROM observations")
        if not bounds or bounds[0] is None:
            return {"instants": 0}
        start = timeutil.ensure_utc(bounds[0]) + timedelta(days=60)
        end = timeutil.ensure_utc(bounds[1]) - timedelta(days=int(horizon))
        if start >= end:
            return {"instants": 0}

        # Resource guard: cap total instants regardless of how long real
        # history has grown, by widening the effective step (never by
        # skipping the guard itself).
        cap = max_instants or DEFAULT_MAX_INSTANTS
        naive_instants = max(1, int((end - start).days / step_days) + 1)
        if naive_instants > cap:
            step_days = max(step_days, int((end - start).days / cap) + 1)

        episode_starts = [
            timeutil.ensure_utc(e["spike_start"])
            for category in categories
            for e in self.episodes.list_latest(category=category, include_controls=False)
            if not e["is_negative"]
        ]

        instants = 0
        true_pos, false_pos, false_neg, true_neg = 0, 0, 0, 0
        lead_errors: list[float] = []
        instant = start
        while instant <= end and instants < cap:
            instants += 1
            changes = self.extractor.changes(window_end=instant)
            # Leakage guard: no signal evidence may postdate the instant.
            constitution.require_no_future_leakage(
                instant, [c.first_crossed_at for c in changes if c.first_crossed_at]
            )
            active = {c.signal_key for c in changes}
            activation = len(signals & active) / len(signals) if signals else 0.0
            predicted = activation >= activation_threshold
            actual_starts = [s for s in episode_starts if instant <= s <= instant + timedelta(days=horizon)]
            if predicted and actual_starts:
                true_pos += 1
                lead_errors.append((min(actual_starts) - instant).days - float(lead.get("avg") or 0))
            elif predicted:
                false_pos += 1
            elif actual_starts:
                false_neg += 1
            else:
                true_neg += 1
            instant += timedelta(days=step_days)

        precision = true_pos / (true_pos + false_pos) if (true_pos + false_pos) else None
        recall = true_pos / (true_pos + false_neg) if (true_pos + false_neg) else None
        report = {
            "instants": instants,
            "true_positives": true_pos,
            "false_positives": false_pos,
            "false_negatives": false_neg,
            "true_negatives": true_neg,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "mean_lead_error_days": round(sum(lead_errors) / len(lead_errors), 2) if lead_errors else None,
            "activation_threshold": activation_threshold,
            "horizon_days": horizon,
        }
        self.log.info("historical replay completed", mechanism_id=mechanism["mechanism_id"], **report)
        return report
