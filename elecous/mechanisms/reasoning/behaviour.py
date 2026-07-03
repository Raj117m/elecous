"""Behavioural reasoning (spec 5.7).

Mechanisms are not just statistics: each carries an explicit behavioural
chain (world change → human consequence → need → category demand) composed
from the config-declared consequence vocabulary. The chain is explanation,
never evidence — evidence remains the episodes, factors and observations the
mechanism links to.
"""

from __future__ import annotations

from typing import Any

from elecous.infrastructure.config import Config, active_config


class BehaviouralReasoner:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()
        vocab = self.config.load_file("behaviour.yaml")
        self.consequences: dict[str, str] = vocab.get("phenomenon_consequences", {})
        self.needs: dict[str, str] = vocab.get("category_needs", {})

    def chain(self, signal_keys: list[str], category: str) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for key in sorted(signal_keys):
            parts = key.split(":")
            if len(parts) != 3:
                continue
            phenomenon, direction, scope = parts
            consequence = self.consequences.get(f"{phenomenon}:{direction}")
            steps.append(
                {
                    "step": "world_change",
                    "signal": key,
                    "statement": f"{phenomenon.replace('_', ' ')} {'rises' if direction == 'up' else 'falls'}"
                    + ("" if scope == "global" else f" in {scope}"),
                }
            )
            if consequence:
                steps.append({"step": "human_consequence", "signal": key, "statement": consequence})
        need = self.needs.get(category, f"{category.replace('_', ' ')} products")
        steps.append({"step": "need", "statement": f"consumers act on {need}"})
        steps.append({"step": "demand", "statement": f"{category.replace('_', ' ')} demand accelerates"})
        return steps

    def describe(self, chain: list[dict[str, Any]]) -> str:
        return " → ".join(step["statement"] for step in chain)
