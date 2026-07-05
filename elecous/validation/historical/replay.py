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
        activation_threshold: float | None = None,
        max_instants: int | None = None,
    ) -> dict[str, Any]:
        """Real bug found via the Final Patch's rolling benchmark (Task 6):
        this defaulted to a hardcoded 0.99, while the live system's actual
        activation decision (MechanismMatcher, prediction/world_matching/
        matching.py) uses thresholds.yaml's prediction.match_threshold
        (0.6). The mismatch meant replay almost never predicted anything
        (0 true/false positives across a real 15-mechanism sample), so
        validate_mechanism's historical_precision_ok check was vacuously
        passing (precision=None) rather than actually gating on real
        precision -- fixed by measuring the same real threshold the live
        system uses, not a different, disconnected one."""
        if activation_threshold is None:
            activation_threshold = float(self.config.load_file("thresholds.yaml")["prediction"]["match_threshold"])
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
            # Same real phenomenon-level fix as MechanismMatcher.match()
            # (prediction/world_matching/matching.py) -- both must measure
            # activation the same way, or the replay tests a different
            # decision rule than the live system actually uses.
            required_phenomena = {":".join(s.split(":")[:2]) for s in signals}
            active_phenomena = {":".join(s.split(":")[:2]) for s in (signals & active)}
            activation = len(active_phenomena) / len(required_phenomena) if required_phenomena else 0.0
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
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall and (precision + recall) else None
        report = {
            "instants": instants,
            "true_positives": true_pos,
            "false_positives": false_pos,
            "false_negatives": false_neg,
            "true_negatives": true_neg,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
            "false_discovery_rate": round(1 - precision, 4) if precision is not None else None,
            "mean_lead_error_days": round(sum(lead_errors) / len(lead_errors), 2) if lead_errors else None,
            "activation_threshold": activation_threshold,
            "horizon_days": horizon,
        }
        self.log.info("historical replay completed", mechanism_id=mechanism["mechanism_id"], **report)
        return report

    def replay_baseline(
        self,
        mechanism: dict[str, Any],
        step_days: int = 7,
        max_instants: int | None = None,
    ) -> dict[str, Any]:
        """Task 6 (Final Patch): a real, simple baseline for the *same*
        real walk-forward windows replay_mechanism just used -- "predict a
        spike whenever the single most-frequently-active supporting signal
        fires," instead of requiring the mechanism's full multi-signal
        combination. This tests whether the mechanism's compound reasoning
        earns its complexity, using only real, already-computed history --
        no synthetic scenario."""
        signals = list(json.loads(mechanism["supporting_signals"]))
        categories = json.loads(mechanism["affected_categories"])
        lead = json.loads(mechanism["lead_time"])
        horizon = max(float(lead.get("max") or 14.0), 7.0)
        if not signals:
            return {"instants": 0}

        bounds = self.extractor.store.db.query_one("SELECT min(timestamp), max(timestamp) FROM observations")
        if not bounds or bounds[0] is None:
            return {"instants": 0}
        start = timeutil.ensure_utc(bounds[0]) + timedelta(days=60)
        end = timeutil.ensure_utc(bounds[1]) - timedelta(days=int(horizon))
        if start >= end:
            return {"instants": 0}
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

        # One real pass: cache each instant's active-signal set and actual
        # outcome once (extractor.changes() is the expensive real query --
        # calling it a second time to pick the baseline signal would double
        # real cost for no new information, since the same real windows
        # were already walked by replay_mechanism moments earlier).
        activity_counts = {s: 0 for s in signals}
        per_instant: list[tuple[set[str], bool]] = []
        instant = start
        while instant <= end and len(per_instant) < cap:
            changes = self.extractor.changes(window_end=instant)
            constitution.require_no_future_leakage(instant, [c.first_crossed_at for c in changes if c.first_crossed_at])
            active = {c.signal_key for c in changes}
            for s in signals:
                if s in active:
                    activity_counts[s] += 1
            actual = any(instant <= s <= instant + timedelta(days=horizon) for s in episode_starts)
            per_instant.append((active, actual))
            instant += timedelta(days=step_days)
        baseline_signal = max(activity_counts, key=activity_counts.get)

        instants = len(per_instant)
        true_pos, false_pos, false_neg, true_neg = 0, 0, 0, 0
        for active, actual in per_instant:
            predicted = baseline_signal in active
            if predicted and actual:
                true_pos += 1
            elif predicted:
                false_pos += 1
            elif actual:
                false_neg += 1
            else:
                true_neg += 1

        precision = true_pos / (true_pos + false_pos) if (true_pos + false_pos) else None
        recall = true_pos / (true_pos + false_neg) if (true_pos + false_neg) else None
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall and (precision + recall) else None
        return {
            "instants": instants, "baseline_signal": baseline_signal,
            "true_positives": true_pos, "false_positives": false_pos,
            "false_negatives": false_neg, "true_negatives": true_neg,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
        }
