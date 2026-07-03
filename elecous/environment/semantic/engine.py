"""Semantic Engine (spec 4.13).

Removes linguistic variation: different observations describing the same
underlying phenomenon collapse into one term. The mapping itself lives in
configs/semantics.yaml (via SemanticNormalizer); this engine exposes the
factor-facing operations built on it.
"""

from __future__ import annotations

from elecous.infrastructure.config import Config, active_config
from elecous.observation.normalizers.semantic import SemanticNormalizer


class SemanticEngine:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()
        self.normalizer = SemanticNormalizer(self.config)

    def phenomenon(self, observation_type: str) -> str | None:
        return self.normalizer.phenomenon_for(observation_type)

    def unify(self, provider_terms: list[str]) -> dict[str, str | None]:
        """Provider vocabulary -> shared phenomena, e.g. 'Heavy Rain',
        'Extreme Rainfall' -> rainfall_intensity."""
        out: dict[str, str | None] = {}
        for term in provider_terms:
            obs_type = self.normalizer.alias_to_type(term)
            out[term] = self.phenomenon(obs_type) if obs_type else None
        return out

    def describe_signal(self, signal_key: str) -> str:
        """Human-readable factor component ('thermal_stress rising in US')."""
        parts = signal_key.split(":")
        if len(parts) != 3:
            return signal_key
        phenomenon, direction, scope = parts
        arrow = "rising" if direction == "up" else "falling"
        where = "globally" if scope == "global" else f"in {scope}"
        return f"{phenomenon.replace('_', ' ')} {arrow} {where}"
