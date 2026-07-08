"""R7: one-time hygiene pass rewriting stored factor names.

Old names joined every raw per-region signal key — up to 6,687 characters of
the same token repeated. Rewrites ALL versions of every factor using
FactorStore.pattern_name() (same function new factors use), in place: names
are display labels, not scientific evidence, so no new version is minted.

Run only while nothing else holds the environment DB (single-writer DuckDB).
Idempotent — re-running produces identical names. Must be re-run after the
production data flip (the flip overwrites canonical data with the copy's).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from elecous.environment.factor_discovery.factors import FactorStore  # noqa: E402
from elecous.storage.database import global_databases  # noqa: E402


def main() -> None:
    db = global_databases().get("environment")
    rows = db.query_dicts("SELECT factor_id, version, name, signals, categories FROM environmental_factors")
    before_max = max((len(r["name"]) for r in rows), default=0)
    renamed = 0
    for r in rows:
        category = (json.loads(r["categories"]) or ["unknown"])[0]
        new_name = FactorStore.pattern_name(json.loads(r["signals"]), category)
        if new_name != r["name"]:
            db.execute(
                "UPDATE environmental_factors SET name = ? WHERE factor_id = ? AND version = ?",
                [new_name, r["factor_id"], r["version"]],
            )
            renamed += 1
    after = db.query_dicts("SELECT name FROM environmental_factors ORDER BY length(name) DESC LIMIT 3")
    print(f"rows: {len(rows)}, renamed: {renamed}")
    print(f"longest name before: {before_max} chars; after: {max((len(r['name']) for r in after), default=0)} chars")
    for r in after:
        print("  e.g.:", r["name"][:160])


if __name__ == "__main__":
    main()
