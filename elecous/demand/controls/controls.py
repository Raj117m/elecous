"""Control Episodes (spec 3.13, 6.14).

Every successful episode automatically creates matched controls: entities in
the same category (or sharing traits) that did NOT spike in the same window,
evaluated with the same instruments. Mechanisms must later explain both
success and non-success.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from elecous.demand.spikes.detector import SpikeDetector
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger


class ControlSelector:
    def __init__(self, detector: SpikeDetector, config: Config | None = None):
        self.detector = detector
        self.config = config or active_config()
        self.log = get_logger("demand")

    def entity_catalog(self) -> list[dict[str, Any]]:
        return self.config.load_file("entities.yaml").get("topics", [])

    def select_controls(
        self,
        spiking_entity: str,
        category: str,
        traits: list[str],
        as_of: datetime,
        window_days: int,
        max_controls: int = 3,
    ) -> list[dict[str, Any]]:
        """Find non-spiking comparable entities in the same window.

        Comparability = same category first, then trait overlap. The control
        evaluation reuses the exact detector, so control evidence is
        measured, not assumed.
        """
        candidates = []
        for topic in self.entity_catalog():
            if topic["entity"] == spiking_entity:
                continue
            overlap = len(set(topic.get("traits", [])) & set(traits))
            same_category = topic.get("category") == category
            if not same_category and overlap == 0:
                continue
            candidates.append((topic, 2 if same_category else 0, overlap))
        candidates.sort(key=lambda c: (c[1], c[2]), reverse=True)

        controls = []
        for topic, _, overlap in candidates:
            if len(controls) >= max_controls:
                break
            evidence = self.detector.evaluate(topic["entity"], as_of, window_days)
            if not evidence.signals:
                continue
            if evidence.stage in ("abnormal_acceleration",):
                continue  # spiked itself; useless as a control
            controls.append(
                {
                    "entity": topic["entity"],
                    "category": topic.get("category"),
                    "traits": topic.get("traits", []),
                    "trait_overlap": overlap,
                    "stage": evidence.stage,
                    "score": evidence.score,
                    "evidence": {
                        s.signal_type: {"peak_z": s.peak_z, "evidence": s.evidence} for s in evidence.signals
                    },
                }
            )
        self.log.info(
            "controls selected",
            entity=spiking_entity,
            controls=[c["entity"] for c in controls],
        )
        return controls
