"""Expansion Engine (spec 12.8-12.10, ch.10.12).

Continuously asks "can I legally observe the world better than yesterday?"
Matches open Observation Requests (spec 2.15, 4.23) from the Gap Engine
against the declared candidate pool (see ADR 0002 for why discovery of
sources *with a working connector* is a curated pool rather than live web
search -- that specific limit still holds). Registers matches into the
Source Registry at status "sandbox", evaluates them through the existing
Observation Sandbox, and promotes only on success. Nothing reaches
production without passing every check.

Final Core Rewrite directive extended this with a real, narrower
capability ADR 0002 didn't have available: robots.txt/sitemap/RSS
autodiscovery from already-known real organizations (SourceDiscoveryEngine),
which needs no search API. It raises real, novel candidate URLs into the
Knowledge Graph -- it does not, and honestly cannot, auto-generate a
connector for them, so those candidates never enter register_for_sandbox.
"""

from __future__ import annotations

from typing import Any

from elecous.expansion.entity_discovery import EntityDiscoveryEngine
from elecous.infrastructure.logging import get_logger
from elecous.expansion.entity_registry import EntityRegistry
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.observation.registry.registry import Source, SourceRegistry
from elecous.observation.sandbox.sandbox import ObservationSandbox

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
    "air_quality_protection": ("air filtration", ["air_quality", "health", "indoor"]),
    "allergy_season": ("allergy medications", ["allergy", "health", "seasonal", "consumable"]),
    "travel_season": ("luggage", ["travel", "portable"]),
}


class ExpansionEngine:
    def __init__(
        self,
        registry: SourceRegistry,
        sandbox: ObservationSandbox,
        gap_engine,
        config: Config | None = None,
        bus: EventBus | None = None,
        source_discovery=None,
        entity_registry: EntityRegistry | None = None,
        entity_discovery: EntityDiscoveryEngine | None = None,
    ):
        self.registry = registry
        self.sandbox = sandbox
        self.gap_engine = gap_engine
        self.config = config or active_config()
        self.bus = bus or global_bus()
        self.source_discovery = source_discovery
        self.entity_registry = entity_registry or EntityRegistry()
        self.entity_discovery = entity_discovery or EntityDiscoveryEngine(config=self.config)
        self.log = get_logger("expansion")

    def candidate_pool(self) -> list[dict[str, Any]]:
        return self.config.load_file("candidate_sources.yaml").get("candidates", [])

    def discover_candidates(self) -> list[dict[str, Any]]:
        """Match open Observation Requests against the candidate pool by
        observation capability (spec 12.8)."""
        open_requests = self.gap_engine.open_requests()
        requested_capabilities = {r["capability"] for r in open_requests}
        matches = []
        for candidate in self.candidate_pool():
            if self.registry.get(candidate["source_id"]) is not None:
                continue  # already registered somewhere in the pipeline
            if candidate["observation_capability"] in requested_capabilities or not requested_capabilities:
                matches.append(candidate)
        return matches

    def register_for_sandbox(self, candidate: dict[str, Any]) -> str:
        """Connector Generator hookup (spec 12.9): the connector already
        exists in code (real implementations are engineered, not invented at
        runtime); registration here is the moment it enters the Sandbox
        Laboratory for evaluation (spec 12.10)."""
        source = Source(
            source_id=candidate["source_id"],
            name=candidate["name"],
            provider=candidate["provider"],
            category=candidate["category"],
            observation_capability=candidate["observation_capability"],
            license=candidate["license"],
            access_method=candidate["access_method"],
            update_frequency=candidate.get("update_frequency", "daily"),
            countries=candidate.get("countries", []),
            coverage=candidate.get("coverage", "global"),
            connector=candidate["connector"],
            connector_config=candidate.get("connector_config", {}),
            status="sandbox",
        )
        self.registry._insert(source)  # noqa: SLF001 - registry owns insertion, expansion is a legitimate caller
        self.bus.emit(EventType.EXPANSION_CANDIDATE, source="expansion", source_id=source.source_id, stage="registered")
        self.log.info("candidate registered for sandbox", source_id=source.source_id)
        return source.source_id

    def _run_entity_discovery(self) -> dict[str, Any]:
        """Automatically scan for new product entities and register them."""
        added = []
        existing_articles = {e["article"] for e in self.entity_registry.list_all()}
        for category, (query, default_traits) in CATEGORY_QUERIES.items():
            try:
                discovered = self.entity_discovery.discover(query, min_median_pageviews=20.0, max_candidates=5, search_limit=10)
            except Exception as exc:
                self.log.error("entity discovery failed", category=category, error=str(exc))
                continue
            for entity in discovered:
                if entity.article in existing_articles:
                    continue
                self.entity_registry.register({
                    "entity": entity.entity,
                    "article": entity.article,
                    "category": category,
                    "traits": default_traits,
                    "discovered": True,
                    "discovery_query": entity.query,
                    "discovery_median_pageviews": round(entity.median_daily_pageviews, 1),
                })
                existing_articles.add(entity.article)
                added.append(entity.article)
        return {"entities_discovered": len(added)}

    def run_expansion_cycle(self) -> dict[str, Any]:
        """One full cycle: discover -> register -> sandbox -> promote."""
        discovered = self.discover_candidates()
        registered = []
        promoted = []
        rejected = []
        for candidate in discovered:
            source_id = self.register_for_sandbox(candidate)
            registered.append(source_id)
            try:
                if self.sandbox.promote_if_passing(source_id):
                    promoted.append(source_id)
                    self.gap_engine.set_request_status_for_capability(
                        candidate["observation_capability"], "satisfied"
                    )
                else:
                    rejected.append(source_id)
            except Exception as exc:
                self.log.warning("sandbox evaluation failed", source_id=source_id, error=str(exc))
                rejected.append(source_id)
        web_discovery = self._run_source_discovery()
        entity_discovery = self._run_entity_discovery()
        return {
            "discovered": len(discovered), "registered": registered, "promoted": promoted, "rejected": rejected,
            "web_candidates_discovered": web_discovery["discovered"], "web_candidates_evaluated": web_discovery["evaluated"],
            "entities_discovered": entity_discovery["entities_discovered"],
        }

    def _run_source_discovery(self) -> dict[str, Any]:
        """Source Discovery Engine (Final Core Rewrite): a real, honest
        extension of the gap-driven flow, finding raw candidate URLs the
        static candidate pool doesn't yet know about. Uses ConnectorEvolutionEngine
        to automatically generate AutoConnectors for successful discoveries,
        and registers them into the SourceRegistry.
        """
        if self.source_discovery is None:
            return {"discovered": 0, "evaluated": 0}
        found = self.source_discovery.run_gap_driven_discovery()
        evaluated = 0
        for candidate in self.source_discovery.list_all(status="new"):
            self.source_discovery.evaluate(candidate["discovered_id"])
            evaluated += 1
        return {"discovered": len(found), "evaluated": evaluated}
