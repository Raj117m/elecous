"""Evidence-based entity expansion driver.

Runs EntityDiscoveryEngine across every phenomenon category, using real
Wikipedia category membership (or full-text search as fallback) plus real
pageview verification, and appends newly-discovered real entities into
configs/entities.yaml. Never invents an entity; every one added here is a
real Wikipedia article with measured, non-trivial real pageview history.

Existing manually-curated entries are preserved untouched; only new,
real, verified entities are appended, tagged with their discovery
provenance (query used, category source) for full traceability.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from elecous.expansion.entity_discovery import EntityDiscoveryEngine
from elecous.infrastructure.logging import get_logger

ENTITIES_PATH = Path(__file__).resolve().parent.parent / "configs" / "entities.yaml"

#: category -> (search keyword, default traits for newly discovered
#: siblings in that category). Keywords are search queries, not entities —
#: every actual entity comes back from real Wikipedia evidence.
CATEGORY_QUERIES: dict[str, tuple[str, list[str]]] = {
    "cooling": ("cooling technology", ["cooling", "home", "summer", "electrical"]),
    "climate_control": ("HVAC", ["humidity", "home", "electrical"]),
    "heating": ("space heating", ["heating", "home", "winter", "electrical"]),
    "rain_protection": ("rainwear", ["rain", "portable", "outdoor"]),
    "sun_protection": ("sun protection", ["summer", "outdoor", "personal_care"]),
    "outdoor_protection": ("insect repellents", ["summer", "outdoor", "personal_care", "consumable"]),
    "outdoor_recreation": ("outdoor recreation equipment", ["outdoor", "recreation", "travel"]),
    "camping_equipment": ("camping equipment", ["outdoor", "camping", "portable", "shelter"]),
    "outdoor_cooking": ("outdoor cooking", ["outdoor", "cooking", "summer", "social"]),
    "winter_equipment": ("winter sports equipment", ["winter", "snow", "outdoor"]),
    "emergency_power": ("emergency power systems", ["emergency", "electrical", "resilience"]),
    "indoor_recreation": ("indoor games", ["indoor", "recreation", "social", "family"]),
    "seasonal_food": ("frozen desserts", ["summer", "cooling", "consumable", "food"]),
    # New phenomena beyond the original 13, per the evidence-expansion
    # directive — kept to a small, defensible set discoverable from clean
    # real Wikipedia categories rather than attempting the entire example
    # list speculatively.
    "air_quality_protection": ("air filtration", ["air_quality", "health", "indoor"]),
    "allergy_season": ("allergy medications", ["allergy", "health", "seasonal", "consumable"]),
    "travel_season": ("luggage", ["travel", "portable"]),
}


def main() -> None:
    log = get_logger("expansion")
    doc = yaml.safe_load(ENTITIES_PATH.read_text())
    existing_articles = {t["article"] for t in doc.get("topics", [])}

    engine = EntityDiscoveryEngine()
    added = []
    report_lines = []
    for category, (query, default_traits) in CATEGORY_QUERIES.items():
        try:
            discovered = engine.discover(query, min_median_pageviews=20.0, max_candidates=20, search_limit=25)
        except Exception as exc:
            log.error("discovery failed for category", category=category, query=query, error=str(exc))
            report_lines.append(f"{category:25s} query={query!r:35s} FAILED: {exc}")
            continue
        new_for_category = 0
        for entity in discovered:
            if entity.article in existing_articles:
                continue
            doc["topics"].append(
                {
                    "entity": entity.entity,
                    "article": entity.article,
                    "category": category,
                    "traits": default_traits,
                    "discovered": True,
                    "discovery_query": entity.query,
                    "discovery_median_pageviews": round(entity.median_daily_pageviews, 1),
                }
            )
            existing_articles.add(entity.article)
            added.append(entity.article)
            new_for_category += 1
        report_lines.append(
            f"{category:25s} query={query!r:35s} real_candidates={len(discovered):3d} newly_added={new_for_category:3d}"
        )
        log.info("category expansion complete", category=category, query=query, new_entities=new_for_category)

    ENTITIES_PATH.write_text(yaml.dump(doc, sort_keys=False, allow_unicode=True, width=100))

    print(f"Total new real, pageview-verified entities added: {len(added)}")
    print()
    for line in report_lines:
        print(line)
    print()
    print("Categories and their entity counts after expansion:")
    from collections import Counter

    counts = Counter(t["category"] for t in doc["topics"])
    for cat, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:25s} {n}")


if __name__ == "__main__":
    sys.exit(main())
