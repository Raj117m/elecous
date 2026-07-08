"""Per-product force ecosystems (roadmap correction #4).

Every product's set of associated external forces is a LEARNED object, not
config: each (product, force) link carries real outcome counters that
prediction resolutions strengthen or weaken. Links that accumulate enough
reality and keep failing are removed from the product's ecosystem — the
knowledge base is pruned by evidence, not grown forever.

Force vocabulary = signal phenomena (cold_stress, media_attention, ...) —
the platform's real, measurable force names.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.demand.schema import DEMAND_MIGRATION, DEMAND_MIGRATION_0003, DEMAND_MIGRATION_0004
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.storage.database import DatabaseManager, global_databases


class ProductEcosystem:
    def __init__(self, databases: DatabaseManager | None = None, config: Config | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(DEMAND_MIGRATION)
        self.databases.register_schema(DEMAND_MIGRATION_0003)
        self.databases.register_schema(DEMAND_MIGRATION_0004)
        self.db = self.databases.get("demand")
        self.config = config or active_config()
        self.log = get_logger("demand")

    def record_outcome(self, product: str, forces: list[str], hit: bool) -> None:
        """One judged prediction outcome for this product: every force that
        was part of the claim's evidence gets strengthened (product spiked)
        or weakened (it did not)."""
        now = timeutil.utcnow()
        for force in sorted(set(forces)):
            self.db.execute(
                "INSERT INTO product_force_links (product, force, supports, contradictions, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (product, force) DO UPDATE SET "
                "supports = supports + excluded.supports, "
                "contradictions = contradictions + excluded.contradictions, "
                "updated_at = excluded.updated_at",
                [product, force, 1 if hit else 0, 0 if hit else 1, now],
            )

    def links(self, product: str) -> list[dict[str, Any]]:
        return self.db.query_dicts(
            "SELECT * FROM product_force_links WHERE product = ? ORDER BY supports DESC, contradictions ASC",
            [product],
        )

    def refresh_associated_forces(self, product: str) -> list[str]:
        """Recompute the product's ecosystem from its real track record.
        A force stays associated while it lacks enough evidence to judge
        (exploration) or while it supports at least as often as it
        contradicts; judged-and-failing forces are REMOVED. Thresholds from
        demand.ecosystem in thresholds.yaml (initial policy, to be
        recalibrated once outcomes accumulate — documented there)."""
        cfg = self.config.load_file("thresholds.yaml").get("demand", {}).get("ecosystem", {})
        min_samples = int(cfg.get("min_samples", 5))
        min_ratio = float(cfg.get("min_ratio", 0.5))

        kept = []
        for link in self.links(product):
            n = link["supports"] + link["contradictions"]
            if n < min_samples:
                kept.append(link["force"])  # not enough reality to judge yet
            elif link["supports"] / n >= min_ratio:
                kept.append(link["force"])
        kept = sorted(kept)

        row = self.db.query_dicts(
            "SELECT product_id, associated_forces FROM products WHERE name = ? OR product_id = ?",
            [product, product.lower().replace(" ", "_")],
        )
        if row and json.loads(row[0]["associated_forces"] or "[]") != kept:
            self.db.execute(
                "UPDATE products SET associated_forces = ? WHERE product_id = ?",
                [json.dumps(kept), row[0]["product_id"]],
            )
            self.log.info("product ecosystem updated", product=product, forces=kept)
        return kept
