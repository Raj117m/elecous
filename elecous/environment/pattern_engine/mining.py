"""Pattern Mining Engine (spec 4.8-4.9).

Asks one question: which combinations of environmental signals occur
significantly more often before verified demand opportunities than during
ordinary periods? Patterns are never manually written — they emerge from
episode evidence versus ordinary-window base rates (lift).

Search: bottom-up frequent itemset growth (Apriori) over episode signal
sets, bounded by ``max_signals_per_factor``. The candidate space stays small
because signal keys are phenomenon-level, not raw observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.logging import get_logger


@dataclass
class MinedPattern:
    signals: tuple[str, ...]  # sorted signal keys
    category: str
    support: int  # episodes containing the pattern
    support_rate: float  # share of episodes
    base_rate: float  # share of ordinary windows
    lift: float
    supporting_episodes: tuple[str, ...]


#: Beam width: only the top-N most-supported single signals seed Apriori
#: growth. Without this bound, a category with M frequent singles produces
#: up to C(M, max_signals_per_factor) candidates — with real multi-location
#: weather data, M easily reaches 20-60, producing tens of thousands of
#: statistically meaningless combinations for a handful of real episodes.
#: This is standard beam-search pruning for frequent-itemset mining, not an
#: arbitrary shortcut: the signals most likely to form a genuine factor are,
#: by construction, the ones that already recur most often.
DEFAULT_BEAM_WIDTH = 12

#: Ordinary-window samples below this count cannot support a meaningful
#: base rate — with e.g. 8 samples, a single zero-occurrence itemset
#: produces a manufactured base rate that turns tiny, coincidental support
#: into a fake double-digit "lift". Below this floor, pattern mining for the
#: category is skipped entirely rather than reporting unreliable numbers.
MIN_ORDINARY_SAMPLES = 20


class PatternMiner:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()
        self.thresholds = self.config.load_file("thresholds.yaml")["environment"]
        self.beam_width = int(self.thresholds.get("pattern_beam_width", DEFAULT_BEAM_WIDTH))
        self.log = get_logger("environment")

    def mine(
        self,
        category: str,
        episode_signals: dict[str, set[str]],
        ordinary_sets: list[set[str]],
    ) -> list[MinedPattern]:
        min_support = int(self.thresholds["min_support_episodes"])
        min_lift = float(self.thresholds["min_lift"])
        max_size = int(self.thresholds["max_signals_per_factor"])
        episodes = {e: s for e, s in episode_signals.items() if s}
        n_episodes = len(episodes)
        if n_episodes < min_support:
            return []
        if len(ordinary_sets) < MIN_ORDINARY_SAMPLES:
            self.log.info(
                "pattern mining skipped: insufficient ordinary-window samples for a reliable base rate",
                category=category,
                ordinary_samples=len(ordinary_sets),
                required=MIN_ORDINARY_SAMPLES,
            )
            return []

        # Frequent single signals first, bounded to the beam width.
        item_counts: dict[str, list[str]] = {}
        for episode_id, signals in episodes.items():
            for key in signals:
                item_counts.setdefault(key, []).append(episode_id)
        frequent_all = {key: eps for key, eps in item_counts.items() if len(eps) >= min_support}
        beam_keys = sorted(frequent_all, key=lambda k: -len(frequent_all[k]))[: self.beam_width]
        frequent: dict[tuple[str, ...], list[str]] = {(key,): frequent_all[key] for key in beam_keys}
        if len(frequent_all) > self.beam_width:
            self.log.info(
                "pattern mining beam-pruned",
                category=category,
                frequent_singles=len(frequent_all),
                kept=len(frequent),
            )

        all_frequent: dict[tuple[str, ...], list[str]] = dict(frequent)
        current = frequent
        size = 1
        while current and size < max_size:
            size += 1
            singles = sorted({s for itemset in current for s in itemset})
            candidates: set[tuple[str, ...]] = set()
            for itemset in current:
                for single in singles:
                    if single > itemset[-1]:
                        candidates.add(tuple(sorted((*itemset, single))))
            next_level: dict[tuple[str, ...], list[str]] = {}
            for candidate in candidates:
                # Apriori pruning: all sub-itemsets must be frequent.
                if any(tuple(sorted(sub)) not in all_frequent for sub in combinations(candidate, size - 1)):
                    continue
                eps = [e for e, s in episodes.items() if set(candidate) <= s]
                if len(eps) >= min_support:
                    next_level[candidate] = eps
            all_frequent.update(next_level)
            current = next_level

        # Lift against ordinary periods. With no ordinary samples yet the
        # base rate is unknown — assume a conservative 0.5 so only extremely
        # frequent patterns pass, rather than everything.
        n_ordinary = len(ordinary_sets)
        patterns: list[MinedPattern] = []
        for itemset, eps in all_frequent.items():
            if len(itemset) < 2:
                continue  # a factor is a *combination* (spec 4.3)
            support_rate = len(eps) / n_episodes
            if n_ordinary:
                base = sum(1 for s in ordinary_sets if set(itemset) <= s) / n_ordinary
                base_rate = max(base, 1.0 / (2 * n_ordinary))  # avoid zero-division lift explosions
            else:
                base_rate = 0.5
            lift = support_rate / base_rate
            if lift >= min_lift:
                patterns.append(
                    MinedPattern(
                        signals=itemset,
                        category=category,
                        support=len(eps),
                        support_rate=round(support_rate, 4),
                        base_rate=round(base_rate, 4),
                        lift=round(lift, 3),
                        supporting_episodes=tuple(sorted(eps)),
                    )
                )
        # Prefer larger, higher-lift patterns; drop patterns subsumed by a
        # superset with at least the same support (they carry no new info —
        # information gain, 7.21).
        patterns.sort(key=lambda p: (len(p.signals), p.lift), reverse=True)
        kept: list[MinedPattern] = []
        for pattern in patterns:
            if any(set(pattern.signals) < set(k.signals) and pattern.support <= k.support for k in kept):
                continue
            kept.append(pattern)
        self.log.info("patterns mined", category=category, episodes=n_episodes, patterns=len(kept))
        return kept
