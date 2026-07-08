import pytest
from datetime import datetime, timedelta
import numpy as np

from elecous.environment.signal_discovery.extractor import EnvironmentalSignalExtractor, SignalChange
from elecous.infrastructure.config import Config

class MockDB:
    def __init__(self, rows):
        self.rows = rows
    
    def query_dicts(self, sql, params):
        if "list(DISTINCT source_id)" in sql:
            # Source query
            return [{"observation_type": r["observation_type"], "scope": r["scope"], "source_ids": ["mock_source"]} for r in self.rows if r.get("source_id_marker")]
        return self.rows

class MockStore:
    def __init__(self, rows):
        self.db = MockDB(rows)

def _make_rows(values, obs_type="mock_obs"):
    rows = []
    base_date = datetime(2026, 1, 1)
    for i, v in enumerate(values):
        rows.append({
            "observation_type": obs_type,
            "category": "weather",
            "scope": "global",
            "day": (base_date + timedelta(days=i)).date(),
            "value": v,
            "source_id_marker": i == 0
        })
    return rows

from elecous.common import timeutil

def test_regime_shift_extraction():
    # 14 days of baseline (7 days at 10, 7 days at 20), 7 days of window at 20.
    values = [10.0]*7 + [20.0]*7 + [20.0]*7
    store = MockStore(_make_rows(values))
    
    extractor = EnvironmentalSignalExtractor(store)
    extractor.normalizer = type('obj', (object,), {'phenomenon_for': lambda self, x: 'mock_phenomenon'})()
    extractor.thresholds = {"lookback_days": 7, "change_z": 1.0, "level_percentile": 0.85, "trend_min_slope_z": 0.8, "trend_min_correlation": 0.6}
    
    changes = extractor._compute_changes(datetime(2026, 1, 22, tzinfo=timeutil.UTC), 7, 14)
    assert len(changes) > 0
    assert "regime" in changes[0].modes

def test_cyclic_extraction():
    # 28 days of baseline, with strong 7-day seasonality, then window with double amplitude
    base_cycle = [10, 15, 20, 15, 10, 5, 0]
    values = base_cycle * 4 + [x * 2 for x in base_cycle] # window has higher variance
    store = MockStore(_make_rows(values))
    
    extractor = EnvironmentalSignalExtractor(store)
    extractor.normalizer = type('obj', (object,), {'phenomenon_for': lambda self, x: 'mock_phenomenon'})()
    extractor.thresholds = {"lookback_days": 7, "change_z": 2.0, "level_percentile": 0.85}
    
    changes = extractor._compute_changes(datetime(2026, 2, 5, tzinfo=timeutil.UTC), 7, 28)
    assert len(changes) > 0
    assert "cyclic" in changes[0].modes

def test_momentum_extraction():
    # Slowly increasing slope, then sharply increasing slope
    values = [i * 0.1 for i in range(14)] + [1.4 + i * 1.0 for i in range(7)]
    store = MockStore(_make_rows(values))
    
    extractor = EnvironmentalSignalExtractor(store)
    extractor.normalizer = type('obj', (object,), {'phenomenon_for': lambda self, x: 'mock_phenomenon'})()
    extractor.thresholds = {"lookback_days": 7, "change_z": 2.0, "level_percentile": 0.85, "trend_min_slope_z": 0.1, "trend_min_correlation": 0.1}
    
    changes = extractor._compute_changes(datetime(2026, 1, 22, tzinfo=timeutil.UTC), 7, 14)
    assert len(changes) > 0
    assert "momentum" in changes[0].modes

def test_persistence_extraction():
    # 14 days baseline flat at 0. Window is 7 days all spiked at 100
    values = [0.0]*14 + [100.0]*7
    store = MockStore(_make_rows(values))
    
    extractor = EnvironmentalSignalExtractor(store)
    extractor.normalizer = type('obj', (object,), {'phenomenon_for': lambda self, x: 'mock_phenomenon'})()
    extractor.thresholds = {"lookback_days": 7, "change_z": 2.0, "level_percentile": 0.85}
    
    changes = extractor._compute_changes(datetime(2026, 1, 22, tzinfo=timeutil.UTC), 7, 14)
    assert len(changes) > 0
    assert "persistence" in changes[0].modes
    assert "spike" in changes[0].modes
    assert "composite" in changes[0].modes
