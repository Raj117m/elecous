"""Resumable GDELT deep backfill (2017 -> present), then the full pipeline.

Sweeps the catalog in chunks, ingesting after every chunk, so an
interruption loses at most one chunk (~4 real minutes) instead of the
whole ~60-minute sweep -- the exact failure that occurred when the first
deep-backfill attempt was killed with zero rows landed. Resume state is
derived from real per-entity coverage in the database, never from a
separate state file that could drift from reality.
"""

from __future__ import annotations

import json
import sys
import time

from elecous.bootstrap import build_stack

CHUNK = 40
DEEP_CUTOFF = "2018-01-01"  # an entity with coverage before this is already deep


def main() -> None:
    stack = build_stack()
    registry = stack.observation.registry
    store = stack.observation.store

    entities_config = stack.config.load_file("entities.yaml")
    all_entities = [t["entity"] for t in entities_config.get("themes", [])] + [
        t["entity"] for t in entities_config.get("topics", [])
    ]

    covered = {
        r["entity"]
        for r in store.db.query_dicts(
            "SELECT entity, min(timestamp) AS first FROM observations "
            "WHERE source_id = 'gdelt_media' GROUP BY entity HAVING first < ?",
            [DEEP_CUTOFF],
        )
    }
    pending = [e for e in all_entities if e not in covered]
    print(f"catalog={len(all_entities)} already-deep={len(covered)} pending={len(pending)}", flush=True)

    source = registry.get("gdelt_media")
    base_config = json.loads(source["connector_config"])
    base_config.pop("entity_filter", None)

    try:
        for i in range(0, len(pending), CHUNK):
            chunk = pending[i : i + CHUNK]
            registry.db.execute(
                "UPDATE source_registry SET connector_config = ? WHERE source_id = 'gdelt_media'",
                [json.dumps({**base_config, "entity_filter": chunk})],
            )
            t0 = time.time()
            result = stack.observation.run_source("gdelt_media", mode="backfill")
            print(
                f"chunk {i // CHUNK + 1}/{(len(pending) + CHUNK - 1) // CHUNK}: "
                f"{result['stats']} ({time.time() - t0:.0f}s)",
                flush=True,
            )
            if not result["success"]:
                print(f"chunk failed: {result['error']} -- rerun to resume from real coverage", flush=True)
                sys.exit(1)
    finally:
        # Always restore the unfiltered config -- scheduled live runs must
        # keep sweeping the whole catalog.
        registry.db.execute(
            "UPDATE source_registry SET connector_config = ? WHERE source_id = 'gdelt_media'",
            [json.dumps(base_config)],
        )

    r = store.db.query_one(
        "SELECT min(timestamp), count(*) FROM observations WHERE source_id = 'gdelt_media'"
    )
    print(f"GDELT_BACKFILL_DONE coverage_start={r[0]} rows={r[1]}", flush=True)


if __name__ == "__main__":
    main()
