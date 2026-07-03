"""Phase 4 tests: pattern mining and factor logic (pure evidence-set
mathematics — episode identifiers and signal-key sets are abstract inputs to
the miner, not fabricated observations), plus live environmental signal
extraction and world-state construction from real weather/media data."""

from __future__ import annotations

import json

import pytest

from elecous.common import timeutil
from elecous.environment.ontology.ontology import Ontology
from elecous.environment.pattern_engine.mining import PatternMiner
from elecous.environment.semantic.engine import SemanticEngine


def test_pattern_miner_finds_lifted_combinations(isolated_config):
    """The miner is pure set arithmetic over evidence identifiers; verify
    lift, apriori pruning and subsumption behaviour."""
    miner = PatternMiner()
    heat_combo = {"thermal_stress:up:US", "energy_demand:up:US", "media_attention:up:Heatwave"}
    episode_signals = {
        f"epi_{i}": set(heat_combo) | ({"rainfall_intensity:down:US"} if i % 2 else set())
        for i in range(6)
    }
    # ordinary periods rarely contain the full combination
    ordinary = [
        {"thermal_stress:up:US"},
        {"energy_demand:up:US"},
        {"rainfall_intensity:down:US", "thermal_stress:up:US"},
        set(),
        {"media_attention:up:Heatwave"},
        {"labor_stress:up:US"},
        set(),
        {"thermal_stress:up:US", "energy_demand:up:US", "media_attention:up:Heatwave"},
    ]
    patterns = miner.mine("cooling", episode_signals, ordinary)
    assert patterns, "expected at least one mined pattern"
    combo = next(p for p in patterns if set(p.signals) == heat_combo)
    assert combo.support == 6
    assert combo.lift >= 1.8
    # The rarer 4-signal superset may also qualify, but only with its own
    # sufficient support.
    for pattern in patterns:
        assert pattern.support >= 3
    # No single-signal factors: a factor is a combination (spec 4.3).
    assert all(len(p.signals) >= 2 for p in patterns)


def test_pattern_miner_requires_support(isolated_config):
    miner = PatternMiner()
    episode_signals = {"epi_1": {"a:up:US", "b:up:US"}, "epi_2": {"a:up:US"}}
    assert miner.mine("cooling", episode_signals, []) == []


def test_ontology_and_semantics(isolated_config):
    ontology = Ontology()
    assert ontology.domain_of("thermal_stress") == "atmospheric"
    assert ontology.family_of("camping_equipment") == "outdoor_activity"
    assert "outdoor_recreation" in ontology.sibling_categories("camping_equipment")
    assert ontology.generalize_signals(["thermal_stress:up:US", "energy_demand:up:US"]) == [
        "atmospheric",
        "infrastructure",
    ]
    semantic = SemanticEngine()
    unified = semantic.unify(["Heavy Rain", "Extreme Rainfall", "heatwave"])
    assert unified["Heavy Rain"] == "rainfall_intensity"
    assert unified["heatwave"] == "thermal_stress"
    assert semantic.describe_signal("thermal_stress:up:US") == "thermal stress rising in US"


@pytest.mark.network
def test_signal_extraction_and_world_state_from_real_observations(isolated_config):
    """Backfill real weather history, then extract signal changes and build
    a persisted World State with conditions."""
    from elecous.environment.signal_discovery.extractor import EnvironmentalSignalExtractor
    from elecous.environment.world_state import WorldStateBuilder
    from elecous.observation.network import ObservationNetwork

    network = ObservationNetwork()
    network.bootstrap()
    source = network.registry.get("open_meteo_weather")
    cfg = json.loads(source["connector_config"])
    cfg["backfill_days"] = 90
    network.registry.db.execute(
        "UPDATE source_registry SET connector_config = ? WHERE source_id = ?",
        [json.dumps(cfg), "open_meteo_weather"],
    )
    result = network.run_source("open_meteo_weather", mode="backfill")
    assert result["success"], result

    extractor = EnvironmentalSignalExtractor(network.store)
    changes = extractor.changes(window_end=timeutil.utcnow(), window_days=14)
    # Real weather always moves somewhere across 14 cities; every change must
    # be expressed in the shared phenomenon vocabulary with evidence types.
    for change in changes:
        phenomenon, direction, scope = change.signal_key.split(":")
        assert direction in ("up", "down")
        assert change.observation_types
        assert abs(change.z) >= 1.5

    builder = WorldStateBuilder(extractor)
    state = builder.build(window_days=14)
    assert state["observation_count"] > 0
    assert isinstance(state["conditions"], list)
    stored = builder.latest_state()
    assert stored is not None and stored["state_id"] == state["state_id"]
    # Historical state reconstruction never sees the future.
    past = builder.state_at(timeutil.days_ago(30))
    assert past is not None
    for signal in past["signals"]:
        if signal["first_crossed_at"]:
            assert timeutil.from_iso(signal["first_crossed_at"]) <= timeutil.days_ago(29)
