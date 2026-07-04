"""Uncertainty Engine (spec 8.13).

Every prediction must expose uncertainty rather than hide it: unknown
observations, weak mechanisms, missing signals, conflicting evidence,
regional and temporal uncertainty all become visible fields.
"""

from __future__ import annotations

from typing import Any


class UncertaintyEngine:
    def assess(
        self,
        match,  # MatchResult
        mechanism: dict[str, Any],
        lead_time: dict[str, Any],
        conflicts: int = 0,
    ) -> dict[str, Any]:
        missing_share = len(match.missing_signals) / max(
            len(match.matched_signals) + len(match.missing_signals), 1
        )
        weak_mechanism = float(mechanism["current_confidence"]) < 0.5
        regional_uncertainty = not match.world_condition_match
        temporal_uncertainty = (lead_time.get("latest") or 0) - (lead_time.get("earliest") or 0)
        return {
            "missing_signals": match.missing_signals,
            "missing_signal_share": round(missing_share, 3),
            "weak_mechanism": weak_mechanism,
            "regional_uncertainty": regional_uncertainty,
            "temporal_uncertainty_days": round(temporal_uncertainty, 1),
            "conflicting_mechanisms": conflicts,
            "overall": round(
                min(
                    1.0,
                    0.4 * missing_share
                    + 0.3 * (1.0 if weak_mechanism else 0.0)
                    + 0.15 * (1.0 if regional_uncertainty else 0.0)
                    + 0.15 * min(1.0, temporal_uncertainty / 14.0),
                ),
                3,
            ),
        }
