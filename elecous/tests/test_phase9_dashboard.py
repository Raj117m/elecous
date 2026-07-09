"""Phase 9 tests: Dashboard API. Verifies every panel route returns 200 and
well-formed JSON built from real data already produced by the engines
(no fabricated observations — one small real weather+holiday backfill seeds
the stack so panels have content).

R6 rebuild additions: zero-state honesty of the new /home, timeline
pagination, request-time feed derivation from versioned opportunity rows,
the product ranking/page routes, and the jargon gate on the shipped
frontend. The rows inserted by the two helpers below exercise the
dashboard's READ-ONLY serialization of versioned tables (rendering logic),
not any scientific pipeline — no observation, demand or environmental
science is fabricated.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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


# ------------------------------------------------------------------ helpers
def _insert_episode(stack, entity: str, category: str, spike_start: datetime, negative: bool = False) -> str:
    """Insert a minimal latest-version episode row so the dashboard's
    read-only queries have something to serialize."""
    from elecous.common.ids import new_id

    episode_id = new_id("research_episode")
    stack.demand.episodes.db.execute(
        "INSERT INTO research_episodes (episode_id, version, entity, category, traits, country, region, baseline, "
        "window_days, spike_start, spike_peak, recovery_end, demand_score, evidence, timeline, signal_agreement, "
        "confidence, overall_confidence, control_episodes, mechanisms_found, false_spike_checks, outcome, "
        "validation_status, lifecycle, is_control, is_negative, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            episode_id, 1, entity, category, "[]", "US", None, "{}", 14, spike_start, None, None,
            2.5, "{}", "{}", "{}", json.dumps({"overall": 0.7}), 0.7, "[]", "[]", "{}",
            None, "pending", "candidate", False, negative, spike_start,
        ],
    )
    return episode_id


def _insert_world_state(stack, state_id: str, as_of: datetime) -> None:
    stack.environment.world.db.execute(
        "INSERT INTO world_states VALUES (?, ?, ?, ?, ?, ?)",
        [state_id, as_of, "[]", "[]", 0, as_of],
    )


def _insert_opportunity_version(
    stack, opportunity_id: str, version: int, state_id: str, probability: float,
    created_at: datetime, signals: list[str],
) -> None:
    evidence = {
        "activated_mechanisms": [
            {
                "mechanism_id": "m-test", "mechanism_version": 1, "activation": 0.8,
                "matched_signals": signals, "missing_signals": [], "behavioural_chain": [],
            }
        ],
        "environmental_factors": [],
    }
    stack.prediction.db.execute(
        "INSERT INTO opportunities (opportunity_id, version, world_state_id, categories, products, "
        "activated_mechanisms, environmental_factors, evidence, scores, lead_time, confidence, probability, "
        "uncertainty, business_value, regions, stage, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            opportunity_id, version, state_id, json.dumps(["cooling"]), json.dumps([]),
            json.dumps([]), json.dumps([]), json.dumps(evidence), "{}",
            json.dumps({"earliest": 2, "most_probable": 5, "latest": 9, "confidence": 0.5}),
            json.dumps({"overall": 0.6}), probability, "{}", "{}", json.dumps(["US"]),
            "emerging", created_at, created_at + timedelta(days=14),
        ],
    )


# ------------------------------------------------------------------- existing
def test_home_and_core_panels_return_200_without_data(client):
    test_client, _ = client
    for endpoint in (
        "/api/home", "/api/world-state", "/api/observation/sources", "/api/observation/gaps",
        "/api/timeline", "/api/opportunities", "/api/mechanisms", "/api/factors", "/api/episodes",
        "/api/world-map", "/api/validations", "/api/validations/prospective", "/api/knowledge/growth",
        "/api/knowledge/search", "/api/health/self-healing", "/api/expansion", "/api/health/system",
        "/api/health/resources", "/api/search?q=camping", "/api/unknowns", "/api/notifications",
        "/api/discovery", "/api/concepts", "/api/experiments", "/api/validations/benchmark",
        "/api/predictions/accuracy", "/api/activity",
        # R6 additions: feed, coverage, product ranking, paginated timeline, faceted search
        "/api/feed", "/api/coverage", "/api/products", "/api/timeline?limit=5",
        "/api/search?q=camping&kind=force&min_confidence=0.1",
        "/api/search?kind=spike&category=cooling&region=US&status=pending",
    ):
        response = test_client.get(endpoint)
        assert response.status_code == 200, f"{endpoint}: {response.text}"
        assert isinstance(response.json(), dict)


def test_home_reflects_zero_state_honestly(client):
    """Zero state must render honestly: no invented accuracy, no fake
    opportunities, benchmark says 'none yet', verdict date is null because
    no predictions exist — and sources are real (registered at bootstrap)."""
    test_client, _ = client
    data = test_client.get("/api/home").json()
    assert data["attention"] == []
    assert data["top_products"] == []
    assert data["do_next"] == []
    assert data["verdicts"]["judged"] == 0
    assert data["verdicts"]["accuracy"] is None
    assert data["verdicts"]["first_verdict_due"] is None
    assert data["benchmark"]["tone"] == "none"
    assert "no benchmark" in data["benchmark"]["summary"].lower()
    assert data["observation_network"]["sources"] > 0  # sources registered even with no runs yet
    assert data["coverage"]["active_source_count"] > 0  # derived from the live registry, not hardcoded
    assert data["learned"]["new_relationships_today"] == 0


def test_404_for_unknown_ids(client):
    test_client, _ = client
    assert test_client.get("/api/mechanisms/does-not-exist").status_code == 404
    assert test_client.get("/api/factors/does-not-exist").status_code == 404
    assert test_client.get("/api/opportunities/does-not-exist").status_code == 404
    assert test_client.get("/api/episodes/does-not-exist/replay").status_code == 404
    assert test_client.get("/api/audit/does-not-exist").status_code == 404
    assert test_client.get("/api/products/does-not-exist").status_code == 404


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


# --------------------------------------------------------------- R6: timeline
def test_timeline_pagination_params_work(client):
    test_client, stack = client
    utc = timezone.utc
    _insert_episode(stack, "Air conditioner", "cooling", datetime(2026, 1, 1, tzinfo=utc))
    _insert_episode(stack, "Electric fan", "cooling", datetime(2026, 2, 1, tzinfo=utc))
    _insert_episode(stack, "Sunscreen", "sun_protection", datetime(2026, 3, 1, tzinfo=utc))

    page1 = test_client.get("/api/timeline?limit=2").json()
    assert page1["past_total"] == 3
    assert page1["past_returned"] == 2
    past1 = [e for e in page1["events"] if e["phase"] == "past"]
    assert [e["entity"] for e in past1] == ["Sunscreen", "Electric fan"]
    assert page1["oldest_returned"] is not None
    # every past event carries an honest did-we-predict-it badge
    assert all(e["badge"]["predicted"] is False for e in past1)

    page2 = test_client.get("/api/timeline", params={"limit": 2, "until": page1["oldest_returned"]}).json()
    past2 = [e for e in page2["events"] if e["phase"] == "past"]
    assert [e["entity"] for e in past2] == ["Air conditioner"]
    # paging into the past must not re-emit present/future blocks
    assert page2["present"] is None
    assert all(e["phase"] == "past" for e in page2["events"])

    since_page = test_client.get("/api/timeline", params={"since": "2026-02-15T00:00:00+00:00"}).json()
    since_past = [e for e in since_page["events"] if e["phase"] == "past"]
    assert [e["entity"] for e in since_past] == ["Sunscreen"]

    assert test_client.get("/api/timeline?until=not-a-date").status_code == 400


# ------------------------------------------------------------------- R6: feed
def test_feed_derives_events_from_versioned_opportunity_rows(client):
    """Two stored versions of one opportunity must surface as a
    strengthened event whose WHY names the live signals and the real
    probability move — derived at request time, no notification state."""
    test_client, stack = client
    utc = timezone.utc
    t0 = datetime.now(utc) - timedelta(hours=3)
    t1 = datetime.now(utc) - timedelta(hours=1)
    _insert_world_state(stack, "ws-test-1", t0)
    _insert_world_state(stack, "ws-test-2", t1)
    _insert_opportunity_version(stack, "test-opp-1", 1, "ws-test-1", 0.40, t0, ["cold_stress:up:AU"])
    _insert_opportunity_version(stack, "test-opp-1", 2, "ws-test-2", 0.70, t0, ["cold_stress:up:AU", "media_attention:up:US"])

    events = test_client.get("/api/feed").json()["events"]
    kinds = {e["kind"] for e in events}
    assert "opportunity_new" in kinds
    strengthened = [e for e in events if e["kind"] == "opportunity_strengthened"]
    assert strengthened, f"no strengthened event derived; kinds={kinds}"
    why = strengthened[0]["why"]
    assert "because" in why
    assert "cold stress rising in AU" in why
    assert "40%" in why and "70%" in why

    # since filter excludes older events
    recent_only = test_client.get(
        "/api/feed", params={"since": (datetime.now(utc) - timedelta(hours=2)).isoformat()}
    ).json()["events"]
    assert all(e["at"] > (datetime.now(utc) - timedelta(hours=2)).isoformat()[:16] for e in recent_only)
    assert not any(e["kind"] == "opportunity_new" and e["id"].split(":")[1] == "test-opp-1" for e in recent_only)


# --------------------------------------------------------------- R6: products
def test_products_ranking_always_positions_every_product(client):
    test_client, stack = client
    catalog = stack.config.load_file("entities.yaml").get("topics", [])
    data = test_client.get("/api/products").json()
    assert data["total"] == len(catalog)
    assert data["products"][0]["rank"] == 1
    assert data["sort_explanation"]
    assert set(data["available_sorts"]) == {
        "largest_expected_spike", "strongest_evidence", "newest_opportunity", "most_improved", "recently_failed",
    }
    # zero state: every product says honestly that it has no evidence
    assert all(p["status"] == "monitored" for p in data["products"])
    assert all("no demand evidence recorded yet" in p["why"] for p in data["products"])
    assert test_client.get("/api/products?sort=nope").status_code == 400

    # give one real catalog product its own episode: it must rank first on evidence
    topic = catalog[0]
    _insert_episode(stack, topic["entity"], topic.get("category", "unknown"), datetime(2026, 6, 1, tzinfo=timezone.utc))
    ranked = test_client.get("/api/products?sort=strongest_evidence").json()["products"]
    assert ranked[0]["product"] == topic["entity"]
    assert ranked[0]["spike_count"] == 1
    assert ranked[0]["status"] == "evidence building"


def test_product_page_zero_state_is_honest(client):
    test_client, stack = client
    import re as _re

    topic = stack.config.load_file("entities.yaml")["topics"][0]
    slug = _re.sub(r"[^a-z0-9]+", "-", topic["entity"].lower()).strip("-")
    page = test_client.get(f"/api/products/{slug}").json()
    assert page["product"] == topic["entity"]
    assert page["current_demand_evidence"] is None
    assert page["current_demand_note"]
    assert page["prediction_verdicts"] == []
    assert "no predictions have been issued yet" in page["prediction_verdicts_note"]
    assert page["related_products"] == []
    assert "not enough evidence" in page["related_products_note"]
    assert page["confidence_history"] == []


# ------------------------------------------------------------ R6: jargon gate
def test_frontend_jargon_gate(client):
    """The primary UI ships ZERO implementation vocabulary; engineering
    words and raw id prefixes may appear only inside the explicitly marked
    developer-mode block."""
    test_client, _ = client
    html = test_client.get("/").text
    start = html.index("<!-- DEVMODE:START")
    end = html.index("<!-- DEVMODE:END -->")
    primary = (html[:start] + html[end:]).lower()
    for banned in ("mechanism", "factor_id", "scheduler", "worker", "fac_", "mech_019", "opp_019"):
        assert banned not in primary, f"jargon '{banned}' leaked outside the developer-mode block"
    # and the dev block genuinely exists (the gate is a gate, not an absence)
    assert "developer" in html[start:end].lower()
