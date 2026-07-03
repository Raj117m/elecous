"""Phase 2 tests: observation model, validators, registry, store dedup and
compression logic — plus live connector tests against real public APIs
(marked ``network``). No observation is ever fabricated: logic tests build
Observation objects only to verify that the validator REJECTS malformed or
non-provenanced input; accepted-path tests use real fetched data."""

from __future__ import annotations

from datetime import timedelta

import pytest

from elecous.common import timeutil
from elecous.observation.model import Observation, SourceRecord
from elecous.observation.network import ObservationNetwork
from elecous.observation.registry.registry import SourceRegistry
from elecous.observation.store import ObservationStore
from elecous.observation.validators.checks import ObservationValidator


def _record(source_id: str = "open_meteo_weather", access: str = "official_api") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        url="https://api.open-meteo.com/v1/forecast",
        access_method=access,
        extracted_at=timeutil.to_iso(timeutil.utcnow()),
        extraction="test_parser@1",
        license="CC BY 4.0",
    )


def test_validator_rejects_missing_provenance():
    obs = Observation(
        timestamp=timeutil.utcnow() - timedelta(days=1),
        entity="Berlin",
        category="weather",
        observation_type="temperature_max_c",
        value=25.0,
        unit="celsius",
        source_record=SourceRecord("", "", "official_api", "", ""),
    )
    outcome = ObservationValidator().validate(obs)
    assert not outcome.passed and outcome.failed_check == "source"


def test_validator_rejects_future_and_synthetic_and_nan():
    validator = ObservationValidator()
    future = Observation(
        timestamp=timeutil.utcnow() + timedelta(days=3),
        entity="Berlin",
        category="weather",
        observation_type="temperature_max_c",
        value=25.0,
        unit="celsius",
        source_record=_record(),
    )
    assert validator.validate(future).failed_check == "timestamp"

    synthetic = Observation(
        timestamp=timeutil.utcnow() - timedelta(days=1),
        entity="Berlin",
        category="weather",
        observation_type="temperature_max_c",
        value=25.0,
        unit="celsius",
        source_record=_record(access="synthetic-generator"),
    )
    assert synthetic and validator.validate(synthetic).failed_check == "public_origin"

    nan = Observation(
        timestamp=timeutil.utcnow() - timedelta(days=1),
        entity="Berlin",
        category="weather",
        observation_type="temperature_max_c",
        value=float("nan"),
        unit="celsius",
        source_record=_record(),
    )
    assert validator.validate(nan).failed_check == "corruption"


def test_registry_seeding_and_credential_gating(isolated_config):
    registry = SourceRegistry()
    added = registry.seed_from_config()
    assert added >= 8
    # keyless sources are active; key-gated sources without keys suspend
    assert registry.get("open_meteo_weather")["status"] == "active"
    assert registry.get("eia_electricity")["status"] == "suspended"
    assert registry.get("fred_series")["status"] == "suspended"
    # reseeding never duplicates or overwrites earned state
    assert registry.seed_from_config() == 0
    capabilities = registry.capabilities()
    assert "weather" in capabilities and "public_attention" in capabilities


@pytest.mark.network
def test_open_meteo_live_collection_end_to_end(isolated_config):
    """Full pipeline with real weather data: connector -> validator -> store,
    dedup on second ingest, series query."""
    network = ObservationNetwork()
    network.bootstrap()
    result = network.run_source("open_meteo_weather")
    assert result["success"], result
    assert result["stats"]["accepted"] > 0
    count_after_first = network.store.count()
    assert count_after_first > 0

    # Second run of the same window: everything must dedup/merge, not double.
    result2 = network.run_source("open_meteo_weather")
    assert result2["success"]
    assert network.store.count() == count_after_first  # no growth from re-observation
    assert result2["stats"]["duplicates"] + result2["stats"]["merged"] > 0

    series = network.store.series(
        "Berlin", "temperature_max_c", timeutil.days_ago(10), timeutil.utcnow()
    )
    assert len(series) >= 3
    for point in series:
        assert point["value"] is not None and point["confidence"] > 0
    # provenance is recorded on every run
    runs = network.registry.runs("open_meteo_weather")
    assert runs and runs[0]["success"]


@pytest.mark.network
def test_wikimedia_pageviews_live_collection(isolated_config):
    network = ObservationNetwork()
    network.bootstrap()
    result = network.run_source("wikimedia_pageviews")
    assert result["success"], result
    assert result["stats"]["accepted"] > 0
    series = network.store.series(
        "Air conditioner", "pageviews_daily", timeutil.days_ago(10), timeutil.utcnow()
    )
    assert len(series) >= 3
    assert all(p["value"] >= 0 for p in series)


@pytest.mark.network
def test_holidays_and_worldbank_live_collection(isolated_config):
    network = ObservationNetwork()
    network.bootstrap()
    holidays = network.run_source("nager_holidays")
    assert holidays["success"], holidays
    assert holidays["stats"]["accepted"] > 0
    worldbank = network.run_source("worldbank_macro")
    assert worldbank["success"], worldbank
    assert worldbank["stats"]["accepted"] > 0
    categories = network.store.counts_by_category()
    assert categories.get("calendar", 0) > 0
    assert categories.get("economics", 0) > 0


@pytest.mark.network
def test_health_scoring_from_real_runs(isolated_config):
    network = ObservationNetwork()
    network.bootstrap()
    network.run_source("open_meteo_weather")
    report = network.health.score("open_meteo_weather")
    assert report["availability"] == 1.0
    assert report["health"] > 0.3
    network.refresh_health()
    assert float(network.registry.get("open_meteo_weather")["health"]) > 0.3


def test_gap_engine_detects_suspended_capability(isolated_config):
    from elecous.observation.gap import ObservationGapEngine

    registry = SourceRegistry()
    registry.seed_from_config()
    store = ObservationStore()
    gap_engine = ObservationGapEngine(store, registry)
    # energy capability only has the suspended EIA source -> coverage gap
    gaps = gap_engine.detect_coverage_gaps()
    capabilities = {g["capability"] for g in gaps}
    assert "energy_demand" in capabilities
    assert gap_engine.record_gaps() >= 1
    assert gap_engine.record_gaps() == 0  # idempotent while still open
    assert any(r["capability"] == "energy_demand" for r in gap_engine.open_requests())
