"""Abnormal acceleration and Demand Evidence (spec 3.7-3.11).

ELECOUS doesn't detect growth — it detects abnormal acceleration relative to
each entity's own dynamically learned baseline, then scores Demand Evidence
across independent demand signals with learned weights, and finally applies
False Spike Rules before anything becomes a candidate Research Episode.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from elecous.common import timeutil
from elecous.demand.baseline.baseline import Baseline, DynamicBaseline
from elecous.infrastructure.config import Config, active_config
from elecous.observation.store import ObservationStore

STAGES = ("normal", "small_growth", "expected_growth", "acceleration", "abnormal_acceleration")


@dataclass
class SignalEvidence:
    signal_type: str
    stage: str
    peak_z: float
    peak_at: datetime | None
    evidence: float  # [0, 100]
    baseline: Baseline | None
    coverage: float
    values: list[tuple[str, float]] = field(default_factory=list)  # frozen series (day, value)


@dataclass
class DemandEvidence:
    entity: str
    as_of: datetime
    window_days: int
    score: float  # [0, 100]
    stage: str
    signals: list[SignalEvidence]
    weights: dict[str, float]
    is_candidate_spike: bool
    false_spike: dict[str, Any] | None = None  # populated when rejected

    def agreeing_signals(self, z_threshold: float = 2.0) -> list[str]:
        return [s.signal_type for s in self.signals if s.peak_z >= z_threshold]


class SpikeDetector:
    #: Demand signal types available from the observation network. New types
    #: register here as connectors for them are added (spec 3.3 lists the
    #: publicly observable candidates; these are the ones with legal public
    #: sources today).
    DEMAND_SIGNAL_TYPES = ("pageviews_daily", "media_volume_pct")

    def __init__(
        self,
        store: ObservationStore,
        baselines: DynamicBaseline,
        config: Config | None = None,
    ):
        self.store = store
        self.baselines = baselines
        self.config = config or active_config()
        self.thresholds = self.config.load_file("thresholds.yaml")["demand"]

    # ------------------------------------------------------------ evaluation
    def evaluate(
        self,
        entity: str,
        as_of: datetime,
        window_days: int,
        weights: dict[str, float] | None = None,
    ) -> DemandEvidence:
        """Assess the evaluation window [as_of - window, as_of] for abnormal
        demand acceleration."""
        window_start = as_of - timedelta(days=window_days)
        stage_z = self.thresholds["stage_z"]
        signal_reports: list[SignalEvidence] = []
        for signal_type in self.DEMAND_SIGNAL_TYPES:
            baseline = self.baselines.compute(entity, signal_type, window_start, persist=False)
            series = self.store.series(entity, signal_type, window_start, as_of)
            if baseline is None or not series:
                continue
            zs = [(p["timestamp"], baseline.robust_z(p["value"])) for p in series]
            peak_at, peak_z = max(zs, key=lambda t: t[1]) if zs else (None, 0.0)
            stage = self._stage(peak_z, stage_z)
            evidence = self._evidence_from_z(peak_z)
            coverage = len(series) / window_days
            signal_reports.append(
                SignalEvidence(
                    signal_type=signal_type,
                    stage=stage,
                    peak_z=round(float(peak_z), 3),
                    peak_at=peak_at,
                    evidence=round(evidence, 2),
                    baseline=baseline,
                    coverage=round(coverage, 3),
                    values=[(timeutil.to_iso(p["timestamp"]), p["value"]) for p in series],
                )
            )

        weights = self._normalized_weights(signal_reports, weights)
        score = sum(weights.get(s.signal_type, 0.0) * s.evidence for s in signal_reports)
        overall_stage = self._combine_stages(signal_reports)
        evidence = DemandEvidence(
            entity=entity,
            as_of=as_of,
            window_days=window_days,
            score=round(score, 2),
            stage=overall_stage,
            signals=signal_reports,
            weights=weights,
            is_candidate_spike=False,
        )
        threshold = float(self.thresholds["evidence_threshold"])
        if overall_stage == "abnormal_acceleration" and score >= threshold:
            rejection = self.false_spike_checks(evidence)
            if rejection is None:
                evidence.is_candidate_spike = True
            else:
                evidence.false_spike = rejection
        return evidence

    # ----------------------------------------------------------- false spikes
    def false_spike_checks(self, evidence: DemandEvidence) -> dict[str, Any] | None:
        """False Spike Rules (spec 3.11). Returns the failing rule or None.

        Launches and unstable histories are already excluded by the
        Eligibility Engine and by requiring a reliable baseline; the rules
        here catch data glitches, coverage holes and single-signal artifacts.
        """
        rules = self.thresholds["false_spike"]

        # Rule: platform bug / impossible jump.
        for signal in evidence.signals:
            values = [v for _, v in signal.values]
            for previous, current in zip(values, values[1:]):
                if previous > 0 and current / previous > float(rules["max_single_day_multiple"]):
                    return {
                        "rule": "impossible_jump",
                        "signal": signal.signal_type,
                        "multiple": round(current / previous, 1),
                    }

        # Rule: missing data inside the window.
        for signal in evidence.signals:
            if signal.coverage < 1.0 - float(rules["max_missing_share"]):
                return {"rule": "missing_data", "signal": signal.signal_type, "coverage": signal.coverage}

        # Rule: unreliable baseline (temporary listing changes, launches).
        for signal in evidence.signals:
            if signal.baseline is not None and not signal.baseline.reliable:
                return {"rule": "unreliable_baseline", "signal": signal.signal_type}

        # Rule: multiple independent signals must agree (governance 3.20).
        agreeing = evidence.agreeing_signals()
        if len(agreeing) < int(rules["min_signals_agreeing"]):
            return {"rule": "insufficient_signal_agreement", "agreeing": agreeing}

        # Rule: one signal dominating the evidence (3.15).
        total = sum(evidence.weights.get(s.signal_type, 0) * s.evidence for s in evidence.signals)
        if total > 0:
            for signal in evidence.signals:
                share = evidence.weights.get(signal.signal_type, 0) * signal.evidence / total
                if share > float(rules["dominance_share"]):
                    return {"rule": "signal_dominance", "signal": signal.signal_type, "share": round(share, 3)}
        return None

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _stage(peak_z: float, stage_z: dict[str, float]) -> str:
        if peak_z >= float(stage_z["abnormal_acceleration"]):
            return "abnormal_acceleration"
        if peak_z >= float(stage_z["acceleration"]):
            return "acceleration"
        if peak_z >= float(stage_z["expected_growth"]):
            return "expected_growth"
        if peak_z >= float(stage_z["small_growth"]):
            return "small_growth"
        return "normal"

    @staticmethod
    def _combine_stages(signals: list[SignalEvidence]) -> str:
        """Overall stage: the highest stage supported by at least one signal,
        but abnormal acceleration requires a second signal at least at
         'acceleration' (independence requirement, 3.9)."""
        if not signals:
            return "normal"
        ordered = sorted(signals, key=lambda s: STAGES.index(s.stage), reverse=True)
        top = ordered[0].stage
        if top == "abnormal_acceleration" and len(ordered) > 1:
            second = ordered[1].stage
            if STAGES.index(second) >= STAGES.index("acceleration"):
                return "abnormal_acceleration"
            return "acceleration"
        if top == "abnormal_acceleration":
            return "acceleration"  # single signal alone cannot claim a spike
        return top

    @staticmethod
    def _evidence_from_z(z: float) -> float:
        """Map a robust z-score to [0,100] via a logistic centred at z=4
        (the abnormal-acceleration boundary)."""
        return 100.0 / (1.0 + math.exp(-(z - 4.0)))

    def _normalized_weights(
        self, signals: list[SignalEvidence], learned: dict[str, float] | None
    ) -> dict[str, float]:
        """Learned weights (spec 3.8) with a uniform uninformative prior
        before any episodes exist. Weights are never manually assigned."""
        present = [s.signal_type for s in signals]
        if not present:
            return {}
        raw = {t: float((learned or {}).get(t, 1.0)) for t in present}
        total = sum(raw.values()) or 1.0
        return {t: v / total for t, v in raw.items()}

    # ------------------------------------------------------------- timeline
    def research_timeline(self, evidence: DemandEvidence) -> dict[str, Any]:
        """Phase boundaries of the research timeline (spec 3.14), derived
        from the strongest signal's frozen series."""
        strongest = max(evidence.signals, key=lambda s: s.evidence, default=None)
        if strongest is None or not strongest.values or strongest.baseline is None:
            return {}
        baseline = strongest.baseline
        days = [(timeutil.from_iso(d), v) for d, v in strongest.values]
        zs = [(d, baseline.robust_z(v)) for d, v in days]
        acceleration_start = next((d for d, z in zs if z >= 2.0), None)
        spike_start = next((d for d, z in zs if z >= 4.0), None)
        peak_at, _ = max(zs, key=lambda t: t[1])
        after_peak = [t for t in zs if t[0] > peak_at]
        recovery = next((d for d, z in after_peak if z < 2.0), None)
        stabilization = next((d for d, z in after_peak if abs(z) < 1.0), None)
        return {
            "before": timeutil.to_iso(days[0][0]),
            "signal_formation": timeutil.to_iso(acceleration_start) if acceleration_start else None,
            "acceleration": timeutil.to_iso(spike_start) if spike_start else None,
            "peak": timeutil.to_iso(peak_at),
            "decline": timeutil.to_iso(recovery) if recovery else None,
            "stabilization": timeutil.to_iso(stabilization) if stabilization else None,
        }
