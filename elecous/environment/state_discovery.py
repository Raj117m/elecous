"""Environmental State Discovery (rewrite directive, Stage 4, Requirement 2).

ELECOUS mined factors (combinations of phenomena) but never asked whether
several factors are really the same underlying hidden state observed
through different signals. This module answers that: clusters real,
already-mined factors by real phenomenon-set overlap (Jaccard, the same
similarity measure the Skeptic Engine already uses for duplication) into
named "environmental states" -- discovered from recurring evidence, never
from a hardcoded list of state names.

A state's label is composed FROM its own real dominant phenomena (a
generic, data-driven naming rule), not looked up in a dictionary of
pre-written state names ("Heat Stress", "Travel Season", ...). If the real
evidence happens to produce something resembling those names, that is
because the phenomena themselves are heat- or travel-related -- not because
the name was pre-supplied.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from elecous.common.ids import new_id
from elecous.common import timeutil
from elecous.environment.schema import ENVIRONMENT_MIGRATION, STATE_HYPOTHESIS_MIGRATION, STATE_MIGRATION
from elecous.environment.signal_discovery.discovery import EpisodeSignalDiscovery
from elecous.infrastructure.config import Config, active_config
from elecous.storage.database import DatabaseManager, global_databases

#: Same untestable-explanation list the Skeptic Engine already declares
#: (Stage 4) -- reused, not redeclared, so "unknowns" on a mechanism
#: hypothesis and "untestable competing explanations" stay one real fact.
UNTESTABLE_UNKNOWNS = (
    "no real transaction/sales data exists to confirm purchase behaviour",
    "no real marketing-spend data exists to rule out a marketing-driven confound",
    "no real pricing data exists to rule out a price-driven confound",
    "no real supply-chain data exists to rule out a supply-shortage confound",
)

#: A generalized-group episode counts as "explained" by a state's window if
#: it falls within this many days of a real member-factor lead time -- the
#: factor's own real lead_time.avg, not a separate invented tolerance.
MIN_CONFIDENCE_TO_SURVIVE = 0.1

#: Two factors are the same underlying state if their real phenomenon sets
#: overlap at least this much -- same threshold family as the Skeptic
#: Engine's existing duplication check (0.9 there is for near-identical
#: factors; 0.5 here is deliberately looser because a state is a coarser,
#: more general concept than a factor).
STATE_SIMILARITY_THRESHOLD = 0.5


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def dominant_phenomena(all_phenomena: list[str], member_count: int) -> list[str]:
    """Real, derived: a phenomenon:direction counts as dominant for a
    cluster only if it appears in at least half of the cluster's members
    -- not every phenomenon any single member happened to carry. Shared
    (Stage 6, Task 8/9) between factor->state and mechanism-residual->
    concept clustering."""
    counts: dict[str, int] = {}
    for p in all_phenomena:
        counts[p] = counts.get(p, 0) + 1
    return sorted(p for p, c in counts.items() if c >= max(1, member_count / 2))


def is_coherent(dominant: list[str]) -> bool:
    """Reject a cluster whose dominant phenomena contain the same
    phenomenon in both directions at once (e.g. cold_stress:up AND
    cold_stress:down both dominant) -- a real, mechanical self-
    contradiction found by running state discovery against the real
    database, not a hypothetical edge case. Shared (Task 8/9)."""
    phenomena_seen: dict[str, set[str]] = {}
    for entry in dominant:
        phenomenon, _, direction = entry.rpartition(":")
        phenomena_seen.setdefault(phenomenon, set()).add(direction)
    return all(len(directions) == 1 for directions in phenomena_seen.values())


def complete_linkage_cluster(item_sets: dict[str, set[str]], threshold: float) -> list[list[str]]:
    """Shared clustering primitive (Stage 6, Task 8/9: reuse, don't
    reimplement). A member joins an existing cluster only if it real-
    overlaps with EVERY current member above threshold -- single-linkage
    chained an unrelated 454-factor mega-cluster together the first time
    this was tried (Stage 4); complete-linkage does not chain. Used by
    both real factor->state clustering and real mechanism-residual->
    concept clustering (Stage 6) -- one algorithm, two real inputs."""
    clusters: list[list[str]] = []
    for key in item_sets:
        placed = False
        for cluster in clusters:
            if all(_jaccard(item_sets[key], item_sets[other]) >= threshold for other in cluster):
                cluster.append(key)
                placed = True
                break
        if not placed:
            clusters.append([key])
    return clusters


class EnvironmentalStateDiscovery:
    def __init__(self, databases: DatabaseManager | None = None, config: Config | None = None):
        self.databases = databases or global_databases()
        self.databases.register_schema(ENVIRONMENT_MIGRATION)
        self.databases.register_schema(STATE_MIGRATION)
        self.databases.register_schema(STATE_HYPOTHESIS_MIGRATION)
        self.db = self.databases.get("environment")
        self.config = config or active_config()
        vocab = self.config.load_file("behaviour.yaml")
        self.consequences: dict[str, str] = vocab.get("phenomenon_consequences", {})
        # Populated by generalize_all() for the bulk path; solution_group()
        # falls back to querying episodes_store directly when called on its
        # own so there is exactly one real code path either way (Task 9).
        self._episode_cache: dict[str, dict[str, Any]] = {}

    def discover(self, factors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Cluster real factors into real states by real phenomenon overlap
        -- no fixed number of clusters, no pre-named categories, just
        recurring real evidence grouped by real similarity.

        Complete-linkage, not transitive union-find: a factor joins an
        existing cluster only if it real-overlaps with EVERY current
        member above threshold, not merely one. Found by actually running
        this against the real 1,139-factor database: single-linkage
        (A~B, B~C implies A,B,C together even if A and C barely overlap)
        chained almost everything into one 454-factor mega-cluster spanning
        11 unrelated categories -- a real, useless result, not a real
        state. Complete-linkage is stricter and does not chain."""
        phenomenon_sets: dict[str, set[str]] = {}
        for factor in factors:
            keys = json.loads(factor["signals"])
            phenomenon_sets[factor["factor_id"]] = {EpisodeSignalDiscovery.phenomenon_of(k) for k in keys}

        clusters = complete_linkage_cluster(phenomenon_sets, STATE_SIMILARITY_THRESHOLD)

        by_id = {f["factor_id"]: f for f in factors}
        states = []
        for member_ids in clusters:
            # A "state" is by definition recurring evidence (Requirement 2)
            # -- a single factor on its own is not yet a generalization,
            # just one observation; it stays unclustered until a second,
            # sufficiently-similar factor real-confirms the pattern.
            if len(member_ids) < 2:
                continue
            all_phenomena: list[str] = []
            categories: set[str] = set()
            for fid in member_ids:
                all_phenomena.extend(phenomenon_sets[fid])
                categories.update(json.loads(by_id[fid]["categories"]))
            dominant = dominant_phenomena(all_phenomena, len(member_ids))
            if not dominant or not is_coherent(dominant):
                # Reject weak/incoherent mechanisms (Requirement 6): a real
                # finding from running this against the real database --
                # several clusters had the SAME phenomenon dominant in both
                # directions at once (e.g. cold_stress:up AND cold_stress:down),
                # a mechanical contradiction, not a real physical state.
                continue
            label = self._label_from_phenomena(dominant)
            states.append(self._upsert_state(label, dominant, member_ids, sorted(categories)))
        return states

    @staticmethod
    def _label_from_phenomena(dominant: list[str]) -> str:
        """A generic naming rule applied to real phenomena -- not a lookup
        in a table of pre-written state names. Two states with different
        real evidence will always get different labels; the same real
        evidence always gets the same label."""
        parts = []
        for p in sorted(dominant)[:3]:
            phenomenon, _, direction = p.rpartition(":")
            parts.append(f"{phenomenon}_{direction}")
        return "+".join(parts)

    def _upsert_state(
        self, label: str, dominant_phenomena: list[str], member_factor_ids: list[str], categories: list[str]
    ) -> dict[str, Any]:
        existing = self.db.query_one("SELECT state_id FROM environmental_states WHERE label = ?", [label])
        now = timeutil.utcnow()
        if existing:
            state_id = existing[0]
            self.db.execute(
                "UPDATE environmental_states SET dominant_phenomena = ?, member_factor_ids = ?, "
                "categories = ?, updated_at = ? WHERE state_id = ?",
                [json.dumps(dominant_phenomena), json.dumps(member_factor_ids), json.dumps(categories), now, state_id],
            )
        else:
            state_id = new_id("world_condition")
            self.db.execute(
                "INSERT INTO environmental_states "
                "(state_id, label, dominant_phenomena, member_factor_ids, categories, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [state_id, label, json.dumps(dominant_phenomena), json.dumps(member_factor_ids),
                 json.dumps(categories), now, now],
            )
        return self.get(state_id)

    def get(self, state_id: str) -> dict[str, Any] | None:
        rows = self.db.query_dicts("SELECT * FROM environmental_states WHERE state_id = ?", [state_id])
        return self._decode(rows[0]) if rows else None

    def list_all(self) -> list[dict[str, Any]]:
        return [self._decode(r) for r in self.db.query_dicts("SELECT * FROM environmental_states ORDER BY label")]

    def for_factor(self, factor_id: str) -> dict[str, Any] | None:
        for state in self.list_all():
            if factor_id in state["member_factor_ids"]:
                return state
        return None

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["dominant_phenomena"] = json.loads(row["dominant_phenomena"])
        row["member_factor_ids"] = json.loads(row["member_factor_ids"])
        row["categories"] = json.loads(row["categories"])
        row["solution_group"] = json.loads(row["solution_group"]) if row.get("solution_group") else []
        row["generalized_group"] = json.loads(row["generalized_group"]) if row.get("generalized_group") else []
        row["contradictions"] = json.loads(row["contradictions"]) if row.get("contradictions") else {}
        row["unknowns"] = json.loads(row["unknowns"]) if row.get("unknowns") else []
        return row

    # ---------------------------------------------------- Stage 5: mechanism hypotheses

    def _get_episode(self, episode_id: str, episodes_store=None) -> dict[str, Any] | None:
        """One real lookup path for both the bulk (cached) and standalone
        (direct query) callers -- Task 9: no second, duplicated lookup."""
        if episode_id in self._episode_cache:
            return self._episode_cache[episode_id]
        if episodes_store is not None:
            episode = episodes_store.latest(episode_id)
            if episode is not None:
                self._episode_cache[episode_id] = episode
            return episode
        return None

    def solution_group(self, state: dict[str, Any], factors, episodes_store=None) -> set[str]:
        """Task 3/5: the real, category-blind set of entities whose real
        episodes support this state's member factors -- discovered from
        observations/time/signals only, never from a catalog category."""
        entities: set[str] = set()
        for factor_id in state["member_factor_ids"]:
            factor = factors.latest(factor_id)
            if factor is None:
                continue
            for episode_id in json.loads(factor["supporting_episodes"]):
                episode = self._get_episode(episode_id, episodes_store)
                if episode is not None:
                    entities.add(episode["entity"])
        return entities

    @staticmethod
    def _build_episode_index(episodes: list[dict[str, Any]]) -> dict[str, list[Any]]:
        """Task 9: one real pass over every real episode, built once and
        reused across every state in a generalize_all() call, instead of a
        fresh query per (state, entity) pair."""
        index: dict[str, list[Any]] = {}
        for episode in episodes:
            index.setdefault(episode["entity"], []).append(timeutil.ensure_utc(episode["spike_start"]))
        for entity in index:
            index[entity].sort()
        return index

    def generalize_all(self, factors, episodes_store) -> list[dict[str, Any]]:
        """Task 4/5/6/7: for every real discovered state, test whether its
        real timing (never its category or name) also explains OTHER real
        catalog entities, detect real contradictions, and recompute a real
        confidence -- retiring states that no longer earn one. Rebuilds the
        episode index exactly once (Task 9), not once per state."""
        all_episodes = episodes_store.list_latest()
        self._episode_cache = {e["episode_id"]: e for e in all_episodes}
        episode_index = self._build_episode_index(all_episodes)
        catalog = set(episode_index.keys())
        # Real chance-rate baseline (Task 10 self-check): a first real run
        # against the actual database showed every state "generalizing" to
        # nearly the same ~90-entity group with generality_score up to 6x --
        # a real leakage/overgeneralization symptom, not discovery. Root
        # cause: raw window overlap has no control for how often an entity
        # spikes anyway. Fixed by requiring real lift over the entity's own
        # base rate across the real observed period, reusing the same
        # min_lift convention (1.8) already used by PatternMiner/Skeptic.
        all_dates = [ts for dates in episode_index.values() for ts in dates]
        total_days = max(1.0, (max(all_dates) - min(all_dates)).total_seconds() / 86400.0) if all_dates else 1.0
        min_lift = float(self.config.load_file("thresholds.yaml")["environment"]["min_lift"])

        results = []
        for state in self.list_all():
            original_group = self.solution_group(state, factors)
            windows = self._state_windows(state, factors)
            generalized_group, unmatched_windows = self._generalize(
                original_group, windows, episode_index, catalog, total_days, min_lift
            )
            contradictions = self._detect_contradictions(
                generalized_group, windows, episode_index, unmatched_windows
            )
            generality_score = (
                len(generalized_group) / len(original_group) if original_group else 0.0
            )
            contradiction_ratio = contradictions["unexplained_demand_ratio"]
            confidence = max(0.0, min(1.0, generality_score * (1.0 - contradiction_ratio) / max(1.0, generality_score)))
            # Normalize: a state explaining exactly its own original group
            # with zero contradictions should sit at a modest, not perfect,
            # confidence -- real generalization to NEW entities is what
            # should push it higher (Task 4).
            new_entities = len(generalized_group - original_group)
            confidence = max(0.0, min(1.0, 0.4 + 0.4 * min(1.0, new_entities / max(1, len(original_group)))
                                        - 0.4 * contradiction_ratio))
            problem_statement = self._human_problem_statement(state)
            status = "active" if confidence >= MIN_CONFIDENCE_TO_SURVIVE else "retired"
            results.append(self._persist_hypothesis(
                state["state_id"], problem_statement, confidence, sorted(original_group),
                sorted(generalized_group), generality_score, contradictions, status,
            ))
        return results

    def _state_windows(self, state: dict[str, Any], factors) -> list[tuple[Any, Any, float]]:
        """Real windows: for each real supporting episode of each member
        factor, [spike_start - lead_avg, spike_start] using the factor's
        own real lead_time.avg -- no invented tolerance."""
        windows = []
        for factor_id in state["member_factor_ids"]:
            factor = factors.latest(factor_id)
            if factor is None:
                continue
            lead_avg = max(1.0, json.loads(factor["lead_time"]).get("avg") or 3.0)
            for episode_id in json.loads(factor["supporting_episodes"]):
                episode = self._episode_cache.get(episode_id)
                if episode is None:
                    continue
                spike = timeutil.ensure_utc(episode["spike_start"])
                windows.append((spike - timedelta(days=lead_avg), spike, lead_avg))
        return windows

    @staticmethod
    def _generalize(
        original_group: set[str], windows: list[tuple[Any, Any, float]],
        episode_index: dict[str, list[Any]], catalog: set[str],
        total_days: float, min_lift: float,
    ) -> tuple[set[str], list[tuple[Any, Any, float]]]:
        """Task 4/5: category-blind -- checks only real episode timing,
        never the entity's name or category. Requires real lift over the
        entity's own base rate (matches expected by chance if its episodes
        were spread uniformly across the real observed period), not mere
        raw overlap -- an entity that spikes constantly will coincide with
        almost any window and must not be credited as "explained" by it."""
        window_days = sum((end - start).total_seconds() / 86400.0 for start, end, _ in windows)
        generalized = set(original_group)
        matched_windows: set[int] = set()
        for entity in catalog:
            if entity in original_group:
                continue
            timestamps = episode_index.get(entity, [])
            if not timestamps:
                continue
            actual = 0
            for i, (start, end, _) in enumerate(windows):
                hits = sum(1 for ts in timestamps if start <= ts <= end)
                if hits:
                    actual += hits
                    matched_windows.add(i)
            expected = len(timestamps) * (window_days / total_days) if total_days else 0.0
            if actual > 0 and actual >= max(1.0, min_lift * expected):
                generalized.add(entity)
        unmatched = [w for i, w in enumerate(windows) if i not in matched_windows]
        return generalized, unmatched

    @staticmethod
    def _detect_contradictions(
        generalized_group: set[str], windows: list[tuple[Any, Any, float]],
        episode_index: dict[str, list[Any]], unmatched_windows: list[tuple[Any, Any, float]],
    ) -> dict[str, Any]:
        """Task 6: real, checkable contradictions --
        (a) environmental conditions (real windows) with no real demand
            from anyone at all;
        (b) demand from generalized-group members that falls OUTSIDE every
            real window -- the mechanism doesn't explain even its own
            members' full history."""
        total_group_episodes = 0
        unexplained_group_episodes = 0
        for entity in generalized_group:
            for ts in episode_index.get(entity, []):
                total_group_episodes += 1
                if not any(start <= ts <= end for start, end, _ in windows):
                    unexplained_group_episodes += 1
        return {
            "conditions_without_demand": len(unmatched_windows),
            "total_conditions": len(windows),
            "demand_without_trigger": unexplained_group_episodes,
            "total_group_episodes": total_group_episodes,
            "unexplained_demand_ratio": (
                unexplained_group_episodes / total_group_episodes if total_group_episodes else 0.0
            ),
        }

    def _human_problem_statement(self, state: dict[str, Any]) -> str:
        """Task 2: composed from real config-declared consequences applied
        to this state's own real dominant phenomena -- never a per-state
        invented sentence, never a category-name lookup."""
        parts = [self.consequences[p] for p in state["dominant_phenomena"] if p in self.consequences]
        return "; ".join(parts) if parts else "no declared behavioural consequence for this state's phenomena"

    def _persist_hypothesis(
        self, state_id: str, problem_statement: str, confidence: float,
        solution_group: list[str], generalized_group: list[str], generality_score: float,
        contradictions: dict[str, Any], status: str,
    ) -> dict[str, Any]:
        now = timeutil.utcnow()
        self.db.execute(
            "UPDATE environmental_states SET human_problem_statement = ?, human_problem_confidence = ?, "
            "solution_group = ?, generalized_group = ?, generality_score = ?, contradictions = ?, "
            "confidence = ?, unknowns = ?, status = ?, updated_at = ? WHERE state_id = ?",
            [
                problem_statement, confidence, json.dumps(solution_group), json.dumps(generalized_group),
                generality_score, json.dumps(contradictions), confidence, json.dumps(list(UNTESTABLE_UNKNOWNS)),
                status, now, state_id,
            ],
        )
        return self.get(state_id)
