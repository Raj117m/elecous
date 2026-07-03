"""Environmental Ontology (spec 4.12) and semantic hierarchy.

Knowledge becomes hierarchical: phenomena group into domains, product
categories group into activity families. The seed hierarchy lives in
configuration; data-driven links (factor→category, discovered by mining)
extend it at runtime through the Knowledge Graph (7.5). The ontology gives
every subsystem one shared vocabulary for generalization/specialization.
"""

from __future__ import annotations

from elecous.infrastructure.config import Config, active_config

#: Seed phenomenon hierarchy: phenomenon -> parent domain.
PHENOMENON_DOMAINS = {
    "thermal_stress": "atmospheric",
    "cold_stress": "atmospheric",
    "rainfall_intensity": "atmospheric",
    "wind_intensity": "atmospheric",
    "air_pollution": "atmospheric",
    "weather_hazard": "atmospheric",
    "seismic_activity": "geophysical",
    "public_attention": "behavioural",
    "media_attention": "behavioural",
    "calendar_event": "societal",
    "calendar_proximity": "societal",
    "energy_demand": "infrastructure",
    "economic_growth": "economic",
    "price_pressure": "economic",
    "labor_stress": "economic",
}

#: Seed category hierarchy: category -> parent activity family.
CATEGORY_FAMILIES = {
    "cooling": "climate_response",
    "heating": "climate_response",
    "climate_control": "climate_response",
    "rain_protection": "weather_response",
    "sun_protection": "weather_response",
    "winter_equipment": "weather_response",
    "outdoor_recreation": "outdoor_activity",
    "camping_equipment": "outdoor_activity",
    "outdoor_cooking": "outdoor_activity",
    "outdoor_protection": "outdoor_activity",
    "indoor_recreation": "indoor_activity",
    "emergency_power": "resilience",
    "seasonal_food": "seasonal_consumption",
}


class Ontology:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()

    def domain_of(self, phenomenon: str) -> str:
        return PHENOMENON_DOMAINS.get(phenomenon, "unclassified")

    def family_of(self, category: str) -> str:
        return CATEGORY_FAMILIES.get(category, "unclassified")

    def sibling_categories(self, category: str) -> list[str]:
        family = self.family_of(category)
        return [c for c, f in CATEGORY_FAMILIES.items() if f == family and c != category]

    def signal_domain(self, signal_key: str) -> str:
        return self.domain_of(signal_key.split(":", 1)[0])

    def generalize_signals(self, signal_keys: list[str]) -> list[str]:
        """Signal keys -> parent-domain terms, used by hierarchy inheritance
        (5.8) to recognize when two factors express the same broader force."""
        return sorted({self.signal_domain(k) for k in signal_keys})
