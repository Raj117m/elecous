"""Phase 4 tests: pattern mining and factor logic (pure evidence-set
mathematics — episode identifiers and signal-key sets are abstract inputs to
the miner, not fabricated observations), plus live environmental signal
extraction and world-state construction from real weather/media data."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from elecous.common import timeutil
from elecous.environment.engine import EnvironmentalEngine
from elecous.environment.ontology.ontology import Ontology
from elecous.environment.pattern_engine.mining import PatternMiner
from elecous.environment.semantic.engine import SemanticEngine


def test_validation_gate_blocks_thin_evidence_categories(isolated_config):
    """The evidence-sufficiency gate (Phase 7 of the evidence-bottleneck
    directive) must block mechanism discovery for a category before any
    computation runs, per real thresholds in configs/thresholds.yaml —
    verified against the exact incident this gate was built to prevent
    (a category with 1 entity / 3 episodes producing a combinatorial
    explosion of unvalidatable factors)."""
    from elecous.demand.episodes.episodes import EpisodeStore
    from elecous.observation.store import ObservationStore

    engine = EnvironmentalEngine(ObservationStore(), EpisodeStore())
    now = timeutil.utcnow()

    thin_group = [
        {"entity": "Evaporative cooler", "spike_start": now, "category": "cooling"},
        {"entity": "Evaporative cooler", "spike_start": now, "category": "cooling"},
        {"entity": "Evaporative cooler", "spike_start": now, "category": "cooling"},
    ]
    gate = engine._validation_gate("cooling", thin_group)
    assert gate["open"] is False
    assert any("entities" in r for r in gate["unmet_requirements"])
    assert any("episodes" in r for r in gate["unmet_requirements"])
    assert any("seasons" in r for r in gate["unmet_requirements"])

    from datetime import timedelta

    rich_group = [
        {"entity": f"Product {i}", "spike_start": now - timedelta(days=30 * i), "category": "cooling"}
        for i in range(9)
    ]
    gate2 = engine._validation_gate("cooling", rich_group)
    assert gate2["open"] is True
    assert gate2["unmet_requirements"] == []


def test_pattern_miner_finds_lifted_combinations(isolated_config):
    """The miner is pure set arithmetic over evidence identifiers; verify
    lift, apriori pruning and subsumption behaviour."""
    miner = PatternMiner()
    heat_combo = {"thermal_stress:up:US", "energy_demand:up:US", "media_attention:up:Heatwave"}
    episode_signals = {
        f"epi_{i}": set(heat_combo) | ({"rainfall_intensity:down:US"} if i % 2 else set())
        for i in range(6)
    }
    # Ordinary periods rarely contain the full combination. A real base-rate
    # estimate needs a real sample size (MIN_ORDINARY_SAMPLES) — fewer than
    # that and a single zero-occurrence itemset manufactures an arbitrarily
    # large, statistically meaningless "lift" (found via real-data testing).
    base_ordinary = [
        {"thermal_stress:up:US"},
        {"energy_demand:up:US"},
        {"rainfall_intensity:down:US", "thermal_stress:up:US"},
        set(),
        {"media_attention:up:Heatwave"},
        {"labor_stress:up:US"},
        set(),
        {"thermal_stress:up:US", "energy_demand:up:US", "media_attention:up:Heatwave"},
    ]
    ordinary = (base_ordinary * 3)[:20]
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


def test_signal_change_carries_contributing_source_lineage(isolated_config):
    """Rewrite directive, Level 2 source intelligence: source_id must
    survive into a SignalChange so a Source Value Ledger can later credit
    the right source. Rows are inserted directly against the real
    observations schema (same technique test_phase7_knowledge.py uses for
    edge rows) to isolate the extractor's aggregation logic from network
    collection -- no fabricated scientific claim is being tested, only that
    the SQL correctly threads source_id through."""
    from elecous.environment.signal_discovery.extractor import EnvironmentalSignalExtractor
    from elecous.observation.store import ObservationStore

    store = ObservationStore()
    now = timeutil.utcnow()

    def insert(day_offset: int, value: float, source_id: str) -> None:
        ts = now - timedelta(days=day_offset)
        store.db.execute(
            "INSERT INTO observations VALUES (" + ", ".join("?" * 21) + ")",
            [
                f"obs_{day_offset}_{source_id}",
                f"dedup_{day_offset}_{source_id}",
                ts,
                "US",
                "weather",
                "temperature_max_c",
                value,
                "celsius",
                None,
                "US",
                None,
                None,
                None,
                True,
                1.0,
                source_id,
                "{}",
                1,
                "chk",
                1,
                ts,
            ],
        )

    # Baseline: 10 calm days from source_a. Window: 3 hot days from source_b.
    for day in range(10, 20):
        insert(day, 20.0, "source_a")
    for day in range(0, 3):
        insert(day, 40.0, "source_b")

    extractor = EnvironmentalSignalExtractor(store)
    changes = extractor.changes(window_end=now, window_days=5, baseline_days=15)
    thermal = next(c for c in changes if c.phenomenon == "thermal_stress")
    assert thermal.direction == "up"
    # Only the source active during the anomalous window is attributed --
    # the baseline-only source must not be credited for the change.
    assert thermal.contributing_sources == ("source_b",)


def test_sources_for_signals_derives_lineage_for_mechanisms(isolated_config):
    """Rewrite directive, Level 2: mechanisms.evidence_links['observations']
    is populated via this derivation rather than a stored column (the
    factor/mechanism rows already carry signals + supporting_episodes;
    deriving avoids a migration on the highest-traffic environment table)."""
    from elecous.environment.schema import ENVIRONMENT_MIGRATION, ENVIRONMENT_PROVENANCE_MIGRATION
    from elecous.storage.database import global_databases

    databases = global_databases()
    databases.register_schema(ENVIRONMENT_MIGRATION)
    databases.register_schema(ENVIRONMENT_PROVENANCE_MIGRATION)
    db = databases.get("environment")
    now = timeutil.utcnow()
    db.execute(
        "INSERT INTO episode_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["epi_1", "thermal_stress:up:US", "thermal_stress", "up", "US", 2.0, now, 3.0,
         json.dumps(["temperature_max_c"]), now, json.dumps(["open_meteo_weather"])],
    )
    db.execute(
        "INSERT INTO episode_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["epi_2", "thermal_stress:up:US", "thermal_stress", "up", "US", 1.8, now, 2.0,
         json.dumps(["temperature_max_c"]), now, json.dumps(["open_meteo_weather", "nws_alerts"])],
    )
    from elecous.environment.signal_discovery.discovery import EpisodeSignalDiscovery
    from elecous.environment.signal_discovery.extractor import EnvironmentalSignalExtractor
    from elecous.observation.store import ObservationStore
    from elecous.signals.library import SignalLibrary

    discovery = EpisodeSignalDiscovery(
        EnvironmentalSignalExtractor(ObservationStore()), SignalLibrary(), databases=databases
    )
    sources = discovery.sources_for_signals(["epi_1", "epi_2"], ["thermal_stress:up:US"])
    assert sources == ["nws_alerts", "open_meteo_weather"]
    # A signal key never observed contributes nothing.
    assert discovery.sources_for_signals(["epi_1"], ["rainfall_intensity:up:US"]) == []


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
        assert abs(change.z) >= 1.0

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
