"""Mechanism Intelligence Engine facade (spec ch. 5).

Transforms surviving Environmental Factors into candidate mechanisms via
scientific comparison (episodes vs controls), attaches behavioural chains,
traits, geography, world conditions and business value, maintains hierarchy
and the interaction graph. It never predicts (5.27) and never promotes
itself — promotion belongs to the Validation Engine.
"""

from __future__ import annotations

import json
from typing import Any

from elecous.common import timeutil
from elecous.demand.episodes.episodes import EpisodeStore
from elecous.environment.factor_discovery.factors import FactorStore
from elecous.environment.ontology.ontology import Ontology
from elecous.environment.signal_discovery.discovery import EpisodeSignalDiscovery
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger
from elecous.mechanisms.reasoning.behaviour import BehaviouralReasoner
from elecous.mechanisms.versioning.store import MechanismStore
from elecous.observation.store import ObservationStore
from elecous.storage.database import DatabaseManager, global_databases


class MechanismEngine:
    def __init__(
        self,
        factors: FactorStore,
        episodes: EpisodeStore,
        discovery: EpisodeSignalDiscovery,
        observations: ObservationStore,
        config: Config | None = None,
        databases: DatabaseManager | None = None,
    ):
        self.config = config or active_config()
        self.databases = databases or global_databases()
        self.factors = factors
        self.episodes = episodes
        self.discovery = discovery
        self.observations = observations
        self.store = MechanismStore(self.databases)
        self.reasoner = BehaviouralReasoner(self.config)
        self.ontology = Ontology(self.config)
        self.log = get_logger("mechanisms")
        self.thresholds = self.config.load_file("thresholds.yaml")["mechanisms"]

    # ------------------------------------------------------------ generation
    def generate_from_factors(self) -> list[str]:
        """Mechanism discovery (5.5-5.6): each factor whose counterfactual
        survives and whose supporting episodes have controls that did NOT
        spike becomes a candidate mechanism. Only differences between
        spiking and non-spiking worlds survive into mechanisms."""
        created: list[str] = []
        min_support = int(self.thresholds["min_supporting_episodes"])
        for factor in self.factors.list_latest():
            if factor["lifecycle"] in ("retired", "archived") or factor.get("merged_into"):
                continue
            if int(factor["support"]) < min_support:
                continue
            counterfactual = json.loads(factor["counterfactual"]) if factor["counterfactual"] else None
            if not counterfactual or not counterfactual.get("survives"):
                continue
            for category in json.loads(factor["categories"]):
                if self.store.find_by_factors([factor["factor_id"]], category) is not None:
                    continue
                control_check = self._control_comparison(factor, category)
                if not control_check["passes"]:
                    self.log.info(
                        "mechanism candidate rejected by control comparison",
                        factor_id=factor["factor_id"],
                        category=category,
                        detail=control_check,
                    )
                    continue
                created.append(self._create_mechanism(factor, category, control_check))
        self._assign_hierarchy()
        self._update_interactions()
        return created

    def _control_comparison(self, factor: dict[str, Any], category: str) -> dict[str, Any]:
        """Control comparison (5.17): in the factor's supporting episodes,
        matched control entities must have stayed calm. If controls spiked
        as often as subjects, the factor explains nothing entity-specific."""
        episode_ids = json.loads(factor["supporting_episodes"])
        calm, spiked, control_total = 0, 0, 0
        control_refs: list[str] = []
        for episode_id in episode_ids:
            episode = self.episodes.latest(episode_id)
            if episode is None:
                continue
            for control in json.loads(episode["control_episodes"]):
                control_total += 1
                control_refs.append(control["entity"])
                if control.get("stage") in ("normal", "small_growth", "expected_growth"):
                    calm += 1
                else:
                    spiked += 1
        calm_share = calm / control_total if control_total else 0.0
        return {
            "passes": control_total > 0 and calm_share >= 0.5,
            "control_total": control_total,
            "calm_share": round(calm_share, 3),
            "controls": sorted(set(control_refs)),
        }

    def _create_mechanism(self, factor: dict[str, Any], category: str, control_check: dict[str, Any]) -> str:
        signal_keys = json.loads(factor["signals"])
        chain = self.reasoner.chain(signal_keys, category)
        traits = self._traits_for_category(category)
        countries = sorted({s for s in json.loads(factor["regions"]) if len(s) == 2})
        climate_zones = self._climate_zones(countries)
        conditions = self._episode_conditions(json.loads(factor["supporting_episodes"]))
        factor_confidence = json.loads(factor["confidence"])
        base = float(factor["current_confidence"])
        confidence = {
            "historical": round(factor_confidence.get("historical", base), 3),
            "recent": round(factor_confidence.get("recent", base), 3),
            "regional": round(factor_confidence.get("regional", base), 3),
            "scientific": 0.0,
            "current": round(base * 0.6, 3),  # discounted until validated
        }
        episode_ids = json.loads(factor["supporting_episodes"])
        name = self.reasoner.describe(chain[:2]) + f" → {category.replace('_', ' ')}"
        payload = {
            "name": name,
            "environmental_factors": [factor["factor_id"]],
            "supporting_signals": signal_keys,
            "behavioural_chain": chain,
            "product_traits": traits,
            "affected_categories": [category],
            "countries": countries,
            "climate_zones": climate_zones,
            "world_conditions": conditions,
            "lead_time": json.loads(factor["lead_time"]),
            "confidence": confidence,
            "business_value": self.business_value(category),
            "supporting_episodes": episode_ids,
            "control_episodes": control_check["controls"],
            "contradicting_episodes": json.loads(factor["contradictory_episodes"]),
            "evidence_links": {
                "factors": [factor["factor_id"]],
                "signals": signal_keys,
                "episodes": episode_ids,
                "counterfactual": json.loads(factor["counterfactual"]) if factor["counterfactual"] else None,
            },
        }
        return self.store.create(payload)

    # ----------------------------------------------------------------- traits
    def _traits_for_category(self, category: str) -> list[str]:
        """Trait intelligence (5.12): mechanisms activate traits; products
        inherit activation. Traits come from the observed entity catalog."""
        topics = self.config.load_file("entities.yaml").get("topics", [])
        traits: dict[str, int] = {}
        members = [t for t in topics if t.get("category") == category]
        for topic in members:
            for trait in topic.get("traits", []):
                traits[trait] = traits.get(trait, 0) + 1
        # traits shared by the majority of the category's members
        return sorted(t for t, n in traits.items() if n >= max(1, len(members) / 2))

    def _climate_zones(self, countries: list[str]) -> list[str]:
        locations = self.config.load_file("entities.yaml").get("locations", [])
        return sorted({l["climate"] for l in locations if l["country"] in countries})

    def _episode_conditions(self, episode_ids: list[str]) -> list[str]:
        """World Conditions under which the mechanism was discovered
        (Appendix D): reconstructed from each episode's cached signals."""
        sets = self.discovery.episode_signal_sets(episode_ids)
        condition_counts: dict[str, int] = {}
        for signals in sets.values():
            by_scope: dict[str, list[str]] = {}
            for key in signals:
                phenomenon, direction, scope = key.split(":")
                scope = scope if len(scope) == 2 else "global"
                by_scope.setdefault(scope, []).append(f"{phenomenon}:{direction}")
            for scope, parts in by_scope.items():
                unique = sorted(set(parts))
                if len(unique) >= 2:
                    name = f"{scope}:" + "+".join(unique)
                    condition_counts[name] = condition_counts.get(name, 0) + 1
        threshold = max(1, len(episode_ids) // 2)
        return sorted(c for c, n in condition_counts.items() if n >= threshold)

    # --------------------------------------------------------- business value
    def business_value(self, category: str) -> dict[str, Any]:
        """Business Value Engine (5.25). Market size has no free legal
        public dataset, so commercial importance is proxied by *measured*
        public attention volume (median daily pageviews across the
        category's entities) — real, public, and comparable across
        categories. The proxy choice is recorded in the value object."""
        topics = [
            t
            for t in self.config.load_file("entities.yaml").get("topics", [])
            if t.get("category") == category
        ]
        attention = []
        for topic in topics:
            row = self.observations.db.query_one(
                "SELECT median(value) FROM observations WHERE entity = ? AND observation_type = 'pageviews_daily'",
                [topic["entity"]],
            )
            if row and row[0] is not None:
                attention.append(float(row[0]))
        episodes = self.episodes.list_latest(category=category, include_controls=False)
        return {
            "attention_volume_median": round(sum(attention) / len(attention), 1) if attention else None,
            "attention_proxy": "median_daily_wikipedia_pageviews",
            "historical_opportunities": len([e for e in episodes if not e["is_negative"]]),
            "entities": len(topics),
            "market_size": None,  # no legal public dataset wired; never invented
            "computed_at": timeutil.to_iso(timeutil.utcnow()),
        }

    # -------------------------------------------------------------- hierarchy
    def _assign_hierarchy(self) -> None:
        """Mechanism hierarchy (5.8): mechanisms of sibling categories under
        one activity family link to a family umbrella mechanism, letting
        knowledge flow downward."""
        mechanisms = self.store.list_latest()
        by_family: dict[str, list[dict[str, Any]]] = {}
        for mechanism in mechanisms:
            categories = json.loads(mechanism["affected_categories"])
            if not categories:
                continue
            family = self.ontology.family_of(categories[0])
            if family != "unclassified":
                by_family.setdefault(family, []).append(mechanism)
        for family, children in by_family.items():
            real_children = [c for c in children if not c["name"].startswith("family:")]
            if len(real_children) < 2:
                continue
            umbrella = next((m for m in children if m["name"] == f"family:{family}"), None)
            if umbrella is None:
                shared_signals = set.intersection(
                    *(set(json.loads(c["supporting_signals"])) for c in real_children)
                ) if real_children else set()
                episodes = sorted(
                    {e for c in real_children for e in json.loads(c["supporting_episodes"])}
                )
                umbrella_id = self.store.create(
                    {
                        "name": f"family:{family}",
                        "environmental_factors": sorted(
                            {f for c in real_children for f in json.loads(c["environmental_factors"])}
                        ),
                        "supporting_signals": sorted(shared_signals),
                        "behavioural_chain": [
                            {"step": "family", "statement": f"{family.replace('_', ' ')} activity rises"}
                        ],
                        "product_traits": [],
                        "affected_categories": sorted(
                            {cat for c in real_children for cat in json.loads(c["affected_categories"])}
                        ),
                        "lead_time": json.loads(real_children[0]["lead_time"]),
                        "confidence": {"historical": 0, "recent": 0, "regional": 0, "scientific": 0, "current": 0},
                        "supporting_episodes": episodes,
                        "evidence_links": {"children": [c["mechanism_id"] for c in real_children]},
                    }
                )
            else:
                umbrella_id = umbrella["mechanism_id"]
            for child in real_children:
                if child["parent_mechanism"] != umbrella_id:
                    self.store.new_version(
                        child["mechanism_id"],
                        {"parent_mechanism": umbrella_id},
                        actor="hierarchy_engine",
                        reason=f"linked under family:{family}",
                    )

    # ------------------------------------------------------------ interactions
    def _update_interactions(self) -> None:
        """Interaction graph (5.9): shared supporting episodes measure how
        often mechanisms co-activate; strength is the Jaccard overlap."""
        mechanisms = self.store.list_latest()
        for i, a in enumerate(mechanisms):
            eps_a = set(json.loads(a["supporting_episodes"]))
            for b in mechanisms[i + 1 :]:
                eps_b = set(json.loads(b["supporting_episodes"]))
                shared = eps_a & eps_b
                if not shared:
                    continue
                strength = len(shared) / len(eps_a | eps_b)
                self.store.record_interaction(a["mechanism_id"], b["mechanism_id"], len(shared), strength)

    # ------------------------------------------------------------ competition
    def detect_competitions(self) -> list[str]:
        """Mechanism competition (5.22): two mechanisms over the same
        category whose signal sets point in opposite directions compete;
        future outcomes decide."""
        opened: list[str] = []
        mechanisms = self.store.list_latest()
        for i, a in enumerate(mechanisms):
            for b in mechanisms[i + 1 :]:
                cats_a = set(json.loads(a["affected_categories"]))
                cats_b = set(json.loads(b["affected_categories"]))
                shared_cats = cats_a & cats_b
                if not shared_cats:
                    continue
                # opposite direction on the same phenomenon+scope
                keys_a = {(k.split(":")[0], k.split(":")[2], k.split(":")[1]) for k in json.loads(a["supporting_signals"])}
                keys_b = {(k.split(":")[0], k.split(":")[2], k.split(":")[1]) for k in json.loads(b["supporting_signals"])}
                conflicts = {
                    (p, s)
                    for p, s, d in keys_a
                    if (p, s, "down" if d == "up" else "up") in keys_b
                }
                if conflicts:
                    subject = f"categories={sorted(shared_cats)} phenomena={sorted(conflicts)}"
                    opened.append(self.store.open_competition(a["mechanism_id"], b["mechanism_id"], subject))
        return opened
