"""Semantic normalization (spec 2.13).

Different providers describe the same phenomenon differently ("Heavy Rain",
"Severe Rain", "Extreme Rainfall"). ELECOUS converts raw measurements into
shared level scales (e.g. Rainfall Intensity Level 4) so every subsystem
speaks one language. Level boundaries live in configs/semantics.yaml —
configuration, never code (spec 10.20).
"""

from __future__ import annotations

import bisect
import threading
from typing import Any

from elecous.infrastructure.config import Config, active_config


class SemanticNormalizer:
    _cache_lock = threading.Lock()
    _cache: dict[int, dict[str, Any]] = {}

    def __init__(self, config: Config | None = None):
        self.config = config or active_config()
        key = id(self.config)
        with self._cache_lock:
            if key not in self._cache:
                self._cache[key] = self.config.load_file("semantics.yaml")
            self._semantics = self._cache[key]

    def level_for(self, observation_type: str, value: float) -> int | None:
        """Map a numeric value to its semantic level, or None when the
        observation type has no level scale."""
        scale = self._semantics.get("levels", {}).get(observation_type)
        if not scale:
            return None
        boundaries = scale["boundaries"]  # ascending upper bounds
        return bisect.bisect_right(boundaries, value)

    def phenomenon_for(self, observation_type: str) -> str | None:
        """The shared phenomenon name for an observation type
        (e.g. temperature_max_c -> thermal_stress), used by the
        Environmental Engine's Semantic Engine (spec 4.13)."""
        return self._semantics.get("phenomena", {}).get(observation_type)

    def alias_to_type(self, provider_term: str) -> str | None:
        """Provider vocabulary -> ELECOUS observation type."""
        return self._semantics.get("aliases", {}).get(provider_term.strip().lower())
