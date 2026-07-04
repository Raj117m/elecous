"""Phase 11 tests: caching and storage tiering. Real weather data seeds the
observation store so the cache is exercised against genuine aggregation
queries, not synthetic values."""

from __future__ import annotations

import pytest

from elecous.common import timeutil
from elecous.infrastructure.cache import TTLCache


def test_ttl_cache_avoids_recomputation():
    cache = TTLCache(ttl_seconds=60.0)
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return 42

    assert cache.get_or_compute("k", compute) == 42
    assert cache.get_or_compute("k", compute) == 42
    assert calls["n"] == 1  # second call served from cache
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_ttl_cache_expires_and_evicts():
    cache = TTLCache(ttl_seconds=0.01, max_entries=2)
    cache.get_or_compute("a", lambda: 1)
    cache.get_or_compute("b", lambda: 2)
    cache.get_or_compute("c", lambda: 3)  # evicts oldest since max_entries=2
    assert len(cache._store) <= 2
    import time

    time.sleep(0.02)
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return 99

    cache.get_or_compute("a", compute)
    assert calls["n"] == 1  # expired entry recomputed


@pytest.mark.network
def test_extractor_caches_real_signal_extraction(isolated_config):
    from elecous.environment.signal_discovery.extractor import EnvironmentalSignalExtractor
    from elecous.observation.network import ObservationNetwork

    network = ObservationNetwork()
    network.bootstrap()
    result = network.run_source("open_meteo_weather", mode="backfill")
    assert result["success"], result

    extractor = EnvironmentalSignalExtractor(network.store)
    now = timeutil.utcnow()
    first = extractor.changes(window_end=now, window_days=14)
    stats_after_first = extractor.cache_stats()
    second = extractor.changes(window_end=now, window_days=14)
    stats_after_second = extractor.cache_stats()

    assert first == second
    assert stats_after_second["hits"] > stats_after_first["hits"]

    extractor.invalidate_cache()
    third = extractor.changes(window_end=now, window_days=14)
    assert third == first  # same real data, recomputed from scratch
