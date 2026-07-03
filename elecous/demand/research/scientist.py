"""The Demand Scientist (spec 3.15-3.16).

Never trusts first impressions. For every candidate episode it asks: why do
we believe demand increased, can another explanation fit, is one signal
dominating, did we miss observations, are controls behaving similarly, can
this be replicated — and quantifies Research Confidence from the answers.
"""

from __future__ import annotations

from typing import Any

from elecous.demand.spikes.detector import DemandEvidence
from elecous.infrastructure.logging import get_logger


class DemandScientist:
    def __init__(self):
        self.log = get_logger("demand")

    def assess(
        self,
        evidence: DemandEvidence,
        controls: list[dict[str, Any]],
        timeline: dict[str, Any],
    ) -> dict[str, float]:
        """Research Confidence components (spec 3.16), each in [0, 1]."""
        # Evidence quality: strength of the demand-evidence score.
        evidence_quality = min(1.0, evidence.score / 100.0 + 0.2)

        # Signal agreement: share of observed signals independently abnormal.
        agreeing = evidence.agreeing_signals()
        signal_agreement = len(agreeing) / len(evidence.signals) if evidence.signals else 0.0

        # Observation coverage inside the window.
        coverage = min((s.coverage for s in evidence.signals), default=0.0)
        observation_coverage = min(1.0, coverage)

        # Baseline reliability.
        reliable = [s for s in evidence.signals if s.baseline is not None and s.baseline.reliable]
        baseline_reliability = len(reliable) / len(evidence.signals) if evidence.signals else 0.0

        # Time confidence: a coherent timeline (formation before peak,
        # phases resolvable) earns trust.
        phases_present = sum(1 for k in ("signal_formation", "acceleration", "peak") if timeline.get(k))
        time_confidence = phases_present / 3.0

        # Category confidence: do controls diverge? If comparable entities
        # remained normal while this one spiked, the spike is entity-specific
        # (real), not a category-wide measurement artifact.
        if controls:
            calm = [c for c in controls if c["stage"] in ("normal", "small_growth", "expected_growth")]
            category_confidence = 0.3 + 0.7 * (len(calm) / len(controls))
        else:
            category_confidence = 0.2  # no controls found -> weak

        components = {
            "evidence_quality": round(evidence_quality, 3),
            "signal_agreement": round(signal_agreement, 3),
            "observation_coverage": round(observation_coverage, 3),
            "baseline_reliability": round(baseline_reliability, 3),
            "time_confidence": round(time_confidence, 3),
            "category_confidence": round(category_confidence, 3),
        }
        # Overall confidence: weakest-link geometric blend — a single bad
        # component should drag the whole assessment down.
        product = 1.0
        for value in components.values():
            product *= max(value, 1e-6)
        overall = product ** (1.0 / len(components))
        components["overall"] = round(overall, 3)
        self.log.info("research confidence assessed", entity=evidence.entity, **components)
        return components
