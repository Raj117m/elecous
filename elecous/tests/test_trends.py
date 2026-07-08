from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from elecous.common import timeutil
from elecous.infrastructure.config import Config
from elecous.observation.connectors.trends import GoogleTrendsConnector

def test_google_trends_no_network(isolated_config):
    # Tests initialization and graceful exit without network call
    source = {
        "source_id": "google_trends",
        "access_method": "unofficial_api",
        "connector_config": '{"past_days": 1, "backfill_days": 1}'
    }
    connector = GoogleTrendsConnector(source, config=isolated_config)
    assert connector.source_id == "google_trends"

@pytest.mark.network
def test_google_trends_network(isolated_config):
    source = {
        "source_id": "google_trends",
        "access_method": "unofficial_api",
        "connector_config": '{"past_days": 1, "backfill_days": 1}'
    }
    connector = GoogleTrendsConnector(source, config=isolated_config)
    result = connector.collect(mode="live")
    
    # Can't assert much because pytrends might rate limit us and return empty list
    assert isinstance(result.observations, list)
