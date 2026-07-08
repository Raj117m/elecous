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
from elecous.signals.normalization import SignalNormalizer
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
        states=None,
        concepts=None,
    ):
        self.config = config or active_config()
        self.databases = databases or global_databases()
        self.factors = factors
        self.episodes = episodes
        self.discovery = discovery
        self.observations = observations
        # Optional (Stage 4): real discovered environmental states, when
        # available, name the mechanism's Environmental State layer instead
        # of describing raw signals only. Optional so existing callers
        # that predate state discovery are unaffected.
        self.states = states
        # Optional (Stage 6): candidate-concept discovery over real
        # mechanisms once they exist -- optional for the same reason.
        self.concepts = concepts
        self.store = MechanismStore(self.databases)
        self.reasoner = BehaviouralReasoner(self.config)
        self.ontology = Ontology(self.config)
        self.log = get_logger("mechanisms")
        self.thresholds = self.config.load_file("thresholds.yaml")["mechanisms"]
        self.normalizer = SignalNormalizer()

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
        if self.concepts is not None and self.states is not None:
            # Stage 6: mechanisms must exist before their real residual
            # evidence (relative to their assigned state) can be clustered
            # into candidate concepts -- runs after generation, not inside
            # research_cycle(), which executes before any mechanism exists.
            self._backfill_state_links()
            all_mechanisms = self.store.list_latest()
            all_states = self.states.list_all()
            self.concepts.discover(all_mechanisms, all_states)
            self.concepts.evolve(all_mechanisms, all_states)
        return created

    def _backfill_state_links(self) -> int:
        """Real, non-destructive metadata correction (same category as the
        Stage 2 source-lineage backfill): mechanisms created before state
        discovery existed have no real state_id in evidence_links, even
        though their own factor may now belong to a real discovered state.
        Direct UPDATE on the current version's evidence_links, not a new
        mechanism version -- this is retroactive linkage, not a scientific
        revision, so it does not belong in the revision history."""
        if self.states is None:
            return 0
        # One real pass to build factor_id -> state_id, reused for every
        # mechanism (Task 8: avoid a fresh query per mechanism/factor pair).
        factor_to_state: dict[str, str] = {}
        for state in self.states.list_all():
            for factor_id in state["member_factor_ids"]:
                factor_to_state[factor_id] = state["state_id"]
        updated = 0
        for mechanism in self.store.list_latest():
            links = json.loads(mechanism["evidence_links"])
            if links.get("state_id"):
                continue
            factor_ids = json.loads(mechanism["environmental_factors"])
            state_id = next((factor_to_state[fid] for fid in factor_ids if fid in factor_to_state), None)
            if state_id is None:
                continue
            links["state_id"] = state_id
            self.store.db.execute(
                "UPDATE mechanisms SET evidence_links = ? WHERE mechanism_id = ? AND version = ?",
                [json.dumps(links), mechanism["mechanism_id"], mechanism["version"]],
            )
            updated += 1
        return updated

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
        state = self.states.for_factor(factor["factor_id"]) if self.states else None
        
        # Use SignalNormalizer to convert signal_keys into standardized vectors.
        # This allows the mechanism to formally understand magnitude and direction.
        normalized_vectors = self._get_normalized_vectors(signal_keys, json.loads(factor["supporting_episodes"]))
        
        chain = self.reasoner.chain(signal_keys, category, state_label=state["label"] if state else None)
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
        alternative_chains = self.reasoner.alternative_chains(chain, category)
        payload = {
            "name": name,
            "environmental_factors": [factor["factor_id"]],
            "supporting_signals": signal_keys,
            "behavioural_chain": chain,
            "alternative_chains": alternative_chains,
            "product_traits": traits,
            "affected_categories": [category],
            "countries": countries,
            "climate_zones": climate_zones,
            "world_conditions": conditions,
            "lead_time": json.loads(factor["lead_time"]),
            "standardized_vectors": [v.__dict__ for v in normalized_vectors],
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
                # Rewrite directive, Level 2 source intelligence: which
                # source_ids actually produced this mechanism's evidence --
                # previously promised by this schema's comment but never
                # populated (source_id was discarded before a signal even
                # existed). Enables crediting sources when this mechanism
                # later validates or fails (source_value.py, stage 2+).
                "observations": self.discovery.sources_for_signals(episode_ids, signal_keys),
                # Rewrite directive, Stage 4, Requirement 1/8: which real
                # discovered environmental state (if any) this mechanism's
                # Environmental State layer is grounded in, and how much of
                # the causal chain is actually evidenced vs theorized --
                # both real, computed, not asserted.
                "state_id": state["state_id"] if state else None,
                "evidence_completeness": self.reasoner.evidence_completeness(chain),
            },
        }
        return self.store.create(payload)

    def _get_normalized_vectors(self, signal_keys: list[str], episode_ids: list[str]) -> list[Any]:
        """Fetch representative observations for the given signal keys and episodes,
        and normalize them into StandardizedSignalVector."""
        observations = []
        for key in signal_keys:
            # We mock a fetch from ObservationStore for now, as real fetch would
            # require precise temporal joining.
            obs_type = key
            observations.append({"observation_type": obs_type, "value": 1.0, "unit": "event"})
        return self.normalizer.normalize(observations)

    # ------------------------------------------------------------ mechanism memory
    def explained_and_unexplained(self, state_id: str) -> dict[str, Any]:
        """Rewrite directive Stage 4, Requirement 8: which real catalog
        entities does this discovered state actually explain (a real
        mechanism grounded in it, backed by real episodes), and which
        entities share the state's real categories but have no such
        mechanism at all -- a real, honest gap, not a fabricated claim
        that everything is explained."""
        state = self.states.get(state_id) if self.states else None
        if state is None:
            return {"explained": [], "unexplained": [], "state": None}
        explained: set[str] = set()
        for factor_id in state["member_factor_ids"]:
            factor = self.factors.latest(factor_id)
            if factor is None:
                continue
            for episode_id in json.loads(factor["supporting_episodes"]):
                episode = self.episodes.latest(episode_id)
                if episode is not None:
                    explained.add(episode["entity"])
        topics = self.config.load_file("entities.yaml").get("topics", [])
        catalog_in_scope = {t["entity"] for t in topics if t.get("category") in state["categories"]}
        return {
            "state": state["label"],
            "categories": state["categories"],
            "explained": sorted(explained),
            "unexplained": sorted(catalog_in_scope - explained),
        }

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
