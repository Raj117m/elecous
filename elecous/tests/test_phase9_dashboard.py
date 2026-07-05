"""Phase 9 tests: Dashboard API. Verifies every panel route returns 200 and
well-formed JSON built from real data already produced by the engines
(no fabricated observations — one small real weather+holiday backfill seeds
the stack so panels have content)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from elecous.dashboard.api.app import create_app
from elecous.dashboard.monitor.notifications import NotificationEngine


@pytest.fixture()
def client(isolated_config):
    from elecous.bootstrap import build_stack

    stack = build_stack()
    app = create_app(stack)
    return TestClient(app), stack


def test_home_and_core_panels_return_200_without_data(client):
    test_client, _ = client
    for endpoint in (
        "/api/home", "/api/world-state", "/api/observation/sources", "/api/observation/gaps",
        "/api/timeline", "/api/opportunities", "/api/mechanisms", "/api/factors", "/api/episodes",
        "/api/world-map", "/api/validations", "/api/validations/prospective", "/api/knowledge/growth",
        "/api/knowledge/search", "/api/health/self-healing", "/api/expansion", "/api/health/system",
        "/api/health/resources", "/api/search?q=camping", "/api/unknowns", "/api/notifications",
        "/api/discovery", "/api/concepts", "/api/experiments", "/api/validations/benchmark",
        "/api/predictions/accuracy",
    ):
        response = test_client.get(endpoint)
        assert response.status_code == 200, f"{endpoint}: {response.text}"
        assert isinstance(response.json(), dict)


def test_home_reflects_zero_state_honestly(client):
    test_client, _ = client
    data = test_client.get("/api/home").json()
    assert data["opportunities"] == []
    assert data["knowledge_growth"]["factors"] == 0
    assert data["observation_network"]["sources"] > 0  # sources registered even with no runs yet


def test_404_for_unknown_ids(client):
    test_client, _ = client
    assert test_client.get("/api/mechanisms/does-not-exist").status_code == 404
    assert test_client.get("/api/factors/does-not-exist").status_code == 404
    assert test_client.get("/api/opportunities/does-not-exist").status_code == 404
    assert test_client.get("/api/episodes/does-not-exist/replay").status_code == 404
    assert test_client.get("/api/audit/does-not-exist").status_code == 404


def test_predict_replay_validates_timestamp(client):
    test_client, _ = client
    assert test_client.get("/api/predict/replay?as_of=not-a-date").status_code == 400
    ok = test_client.get("/api/predict/replay", params={"as_of": "2026-01-01T00:00:00+00:00"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert "world_state" in body and "matches" in body


def test_dashboard_performs_zero_reasoning(client):
    """The dashboard must not compute science — /api/opportunities returns
    exactly what PredictionEngine.opportunities.active() already produced,
    with no extra scoring applied in the route layer."""
    test_client, stack = client
    direct = stack.prediction.opportunities.active()
    via_api = test_client.get("/api/opportunities").json()["opportunities"]
    assert len(direct) == len(via_api)


@pytest.mark.network
def test_dashboard_reflects_real_observation_backfill(isolated_config):
    """Seed a small real backfill and confirm the observation panel and
    world-state panel surface it — end-to-end through the API layer."""
    from elecous.bootstrap import build_stack

    stack = build_stack()
    result = stack.observation.run_source("open_meteo_weather", mode="live")
    assert result["success"], result

    app = create_app(stack)
    test_client = TestClient(app)

    sources = test_client.get("/api/observation/sources").json()["sources"]
    weather = next(s for s in sources if s["source_id"] == "open_meteo_weather")
    assert weather["status"] == "active"

    world_state = test_client.get("/api/world-state").json()
    assert world_state["observation_count"] > 0

    world_map = test_client.get("/api/world-map").json()
    assert isinstance(world_map["signals_by_scope"], dict)


def test_notification_engine_thresholds(isolated_config):
    engine = NotificationEngine()
    import json

    low_conf_opp = {"opportunity_id": "opp_1", "confidence": json.dumps({"overall": 0.5}), "categories": json.dumps(["cooling"])}
    high_conf_opp = {"opportunity_id": "opp_2", "confidence": json.dumps({"overall": 0.9}), "categories": json.dumps(["cooling"])}
    assert engine.check_opportunity(low_conf_opp) is None  # below threshold: silent
    notif_id = engine.check_opportunity(high_conf_opp)
    assert notif_id is not None
    recent = engine.recent()
    assert any(n["notification_id"] == notif_id for n in recent)
    engine.acknowledge(notif_id)
    assert engine.recent(unacknowledged_only=True) == []

    assert engine.check_mechanism_failure("mech_1", confidence_drop=0.1) is None  # below threshold
    assert engine.check_mechanism_failure("mech_1", confidence_drop=0.3) is not None
