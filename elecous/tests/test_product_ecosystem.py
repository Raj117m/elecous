"""Per-product force ecosystem lifecycle (roadmap correction #4):
(product, force) links strengthen/weaken with real judged outcomes, and a
force that accumulates enough reality while failing is REMOVED from the
product's ecosystem — never a shared static force set."""

from __future__ import annotations

import json

from elecous.common import timeutil
from elecous.demand.ecosystem import ProductEcosystem
from elecous.storage.database import global_databases


def test_links_strengthen_weaken_and_prune(isolated_config):
    eco = ProductEcosystem()

    # a product row so refresh has somewhere to write the learned set
    eco.db.execute(
        "INSERT INTO products (product_id, name, category, associated_forces, baseline_demand, created_at) "
        "VALUES ('space_heater', 'Space heater', 'heating', '[]', 1.0, ?)",
        [timeutil.utcnow()],
    )

    # 5 judged outcomes where both forces were in the claim's evidence:
    # each outcome applies to every force in the claim.
    for hit in (True, True, True, True, False):
        eco.record_outcome("Space heater", ["cold_stress", "media_attention"], hit)
    links = {r["force"]: r for r in eco.links("Space heater")}
    assert links["cold_stress"]["supports"] == 4 and links["cold_stress"]["contradictions"] == 1

    # give media_attention a separately-judged failing record
    for _ in range(5):
        eco.record_outcome("Space heater", ["media_attention"], hit=False)
    links = {r["force"]: r for r in eco.links("Space heater")}
    assert links["media_attention"]["contradictions"] == 6  # 1 shared + 5 direct

    kept = eco.refresh_associated_forces("Space heater")
    assert "cold_stress" in kept          # 4/5 support ratio -> stays
    assert "media_attention" not in kept  # 4/10 < 0.5 with n >= 5 -> pruned

    stored = eco.db.query_dicts("SELECT associated_forces FROM products WHERE product_id = 'space_heater'")
    assert json.loads(stored[0]["associated_forces"]) == kept


def test_underexplored_forces_are_kept(isolated_config):
    """Below min_samples a link cannot be judged — exploration floor."""
    eco = ProductEcosystem()
    eco.record_outcome("Tent", ["rainfall_intensity"], hit=False)
    eco.record_outcome("Tent", ["rainfall_intensity"], hit=False)
    kept = eco.refresh_associated_forces("Tent")  # no products row: returns computed set
    assert kept == ["rainfall_intensity"]  # 2 samples < 5 -> not judged yet
