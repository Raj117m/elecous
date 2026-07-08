"""Signal Normalization Pipeline.

Converts diverse environmental observations (discrete events like "Port Strike"
and continuous timeseries like "Inflation %") into standardized vectors so the
MechanismEngine can process them uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from elecous.common.vocabulary import EnvironmentalDomain

@dataclass
class StandardizedSignalVector:
    """A mathematically standardized vector representing an environmental event or trend.
    This abstract format replaces raw string signal keys in the MechanismEngine."""
    signal_key: str  # Original key, e.g. phenomenon:direction:scope
    domain: str
    phenomenon: str
    direction: str
    scope: str
    is_discrete: bool
    magnitude: float  # Normalized 0.0 to 1.0 (or -1.0 to 1.0)
    momentum: float   # Rate of change
    duration_days: int
    raw_value: float | None = None
    unit: str | None = None

class SignalNormalizer:
    def __init__(self):
        pass

    def normalize(self, observations: list[dict[str, Any]]) -> list[StandardizedSignalVector]:
        """Convert raw observations into standardized vectors.
        This handles both discrete and continuous sources."""
        vectors = []
        for obs in observations:
            obs_type = obs.get("observation_type", "")
            val = obs.get("value", 1.0)
            
            is_discrete = obs.get("unit") == "event" or "strike" in obs_type.lower() or "event" in obs_type.lower()
            
            # Simulated Z-score/normalization
            magnitude = min(1.0, val / 100.0) if not is_discrete else 1.0
            momentum = 0.0
            
            parts = obs_type.split(":")
            if len(parts) >= 3:
                phenomenon, direction, scope = parts[0], parts[1], parts[2]
            else:
                phenomenon = obs_type
                direction = "up" if val > 0 else "down"
                scope = obs.get("scope", "global")
                
            domain = obs.get("domain", EnvironmentalDomain.ATMOSPHERIC)
            
            vectors.append(StandardizedSignalVector(
                signal_key=f"{phenomenon}:{direction}:{scope}",
                domain=domain,
                phenomenon=phenomenon,
                direction=direction,
                scope=scope,
                is_discrete=is_discrete,
                magnitude=magnitude,
                momentum=momentum,
                duration_days=1,
                raw_value=val,
                unit=obs.get("unit")
            ))
            
        return vectors
