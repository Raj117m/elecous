"""Lead-Time Prediction (spec 8.9).

Every mechanism stores historical timing; ELECOUS reports earliest / most
probable / latest activation with a confidence distribution rather than a
single number.
"""

from __future__ import annotations

from typing import Any


class LeadTimeEstimator:
    def estimate(self, mechanism: dict[str, Any], activation: float) -> dict[str, Any]:
        import json

        timing = json.loads(mechanism["lead_time"]) if isinstance(mechanism["lead_time"], str) else mechanism["lead_time"]
        avg = timing.get("avg")
        if avg is None:
            return {"earliest": None, "most_probable": None, "latest": None, "confidence": 0.0}
        stddev = timing.get("stddev") or max(1.0, avg * 0.2)
        # Partial activation widens the timing window: less evidence, less
        # certainty about exactly when the mechanism will complete.
        spread = stddev * (1.0 + (1.0 - activation))
        confidence = max(0.1, min(0.95, activation * (1.0 - min(0.5, stddev / max(avg, 1.0)))))
        return {
            "earliest": round(max(0.0, avg - spread), 1),
            "most_probable": round(avg, 1),
            "latest": round(avg + spread, 1),
            "confidence": round(confidence, 3),
            "samples": timing.get("episodes", 0),
        }
