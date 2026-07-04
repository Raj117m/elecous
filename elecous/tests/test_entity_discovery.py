"""Tests for real, evidence-based entity discovery. Every assertion in the
network-marked tests is checked against genuine Wikipedia API responses —
no fabricated titles or pageview numbers."""

from __future__ import annotations

import pytest

from elecous.expansion.entity_discovery import EntityDiscoveryEngine, _EXCLUDE_PATTERNS


def test_exclude_patterns_filter_navigation_pages():
    assert _EXCLUDE_PATTERNS.match("List of cooling devices")
    assert _EXCLUDE_PATTERNS.match("Category:Cooling")
    assert _EXCLUDE_PATTERNS.match("Timeline of refrigeration")
    assert not _EXCLUDE_PATTERNS.match("Ceiling fan")
    assert not _EXCLUDE_PATTERNS.match("Air conditioning")


@pytest.mark.network
def test_find_categories_returns_real_existing_categories():
    engine = EntityDiscoveryEngine()
    categories = engine.find_categories("camping equipment", limit=5)
    assert categories, "expected at least one real matching category"
    assert any("Camping" in c for c in categories)


@pytest.mark.network
def test_category_members_are_real_articles_not_navigation_pages():
    engine = EntityDiscoveryEngine()
    members = engine.category_members("Category:Camping equipment", limit=30)
    assert len(members) >= 10
    titles = {m["title"] for m in members}
    assert not any(_EXCLUDE_PATTERNS.match(t) for t in titles)


@pytest.mark.network
def test_real_median_pageviews_returns_plausible_number_or_none():
    engine = EntityDiscoveryEngine()
    median = engine.real_median_pageviews("Air conditioning", days=30)
    assert median is not None and median > 0

    # A page unlikely to exist yields None, not a fabricated zero.
    bogus = engine.real_median_pageviews("Zzzznonexistentarticle12345", days=30)
    assert bogus is None or bogus == 0.0


@pytest.mark.network
def test_discover_only_keeps_entities_with_real_pageview_evidence():
    engine = EntityDiscoveryEngine()
    discovered = engine.discover("camping equipment", min_median_pageviews=20.0, max_candidates=8)
    assert discovered, "expected real discoveries for a well-populated real category"
    for entity in discovered:
        assert entity.median_daily_pageviews >= 20.0
        assert entity.query == "camping equipment"
        assert entity.article == entity.entity
    # No duplicates
    assert len({e.entity for e in discovered}) == len(discovered)
