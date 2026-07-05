"""Investigation planning (rewrite directive, Stage 2).

Pure functions over real, already-stored data -- no new engine class
hierarchy, no invented numbers. ``wilson_interval`` is a standard,
small-sample-correct confidence interval (Wilson 1927); everything else is a
direct aggregate of ``SourceValueLedger``/``ObservationHealth`` records.

"Information gain" (rewrite directive Requirement 1) is deliberately not a
separate stored field: it is the real, computable reduction in
``wilson_interval`` width as ``sample_size`` grows, i.e. a function of data
already present, not a new invented metric. It is exposed as
``wilson_width`` so a caller can diff it across two points in time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

#: 95% confidence, the conventional default -- not tuned per project.
WILSON_Z = 1.96

#: A source needs at least this many judged outcomes before its track record
#: is trusted enough to exclude it outright (Requirement 2: "almost never
#: helps"). Below this, a source is unproven, not bad -- it stays eligible.
MIN_SAMPLES_TO_EXCLUDE = 5

#: A source is excluded once even its optimistic (upper) bound sits below a
#: fair-coin baseline of usefulness. Framed against 0.5 because the
#: underlying statistic is a proportion of supports over (supports +
#: contradictions), which lives in [0, 1] -- there is no such thing as a
#: "negative" proportion, so the exclusion baseline is the neutral midpoint.
EXCLUDE_BELOW_UPPER_BOUND = 0.5

#: How many days of no use before the recency term decays to zero.
RECENCY_HALF_LIFE_DAYS = 60.0

#: Reference scale for the cost-efficiency term: a real call at this many
#: milliseconds scores 0.5; much faster scores near 1.0, much slower (real
#: example this session: GDELT's ~3.3-hour average) scores near 0.
COST_SCALE_MS = 60_000.0

#: Repeated-failure threshold before a lesson meaningfully discounts a plan.
LESSON_CONFIRMATIONS_FOR_DISCOUNT = 3
MAX_LESSON_DISCOUNT = 0.5


def wilson_interval(successes: int, total: int, z: float = WILSON_Z) -> tuple[float, float]:
    """Wilson score interval for a proportion -- correct at small sample
    sizes, unlike the naive normal approximation (which can produce bounds
    outside [0,1] exactly when data is thinnest, i.e. exactly when it
    matters most here)."""
    if total <= 0:
        return (0.0, 1.0)  # no evidence yet: fully uncertain, not "bad"
    p = successes / total
    denom = 1 + z * z / total
    center = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (max(0.0, (center - margin) / denom), min(1.0, (center + margin) / denom))


def wilson_width(successes: int, total: int, z: float = WILSON_Z) -> float:
    """Interval width -- shrinks monotonically as sample_size grows. The
    real, honest stand-in for "information gain": the amount this source's
    next judged outcome is expected to narrow what we know about it."""
    lower, upper = wilson_interval(successes, total, z)
    return upper - lower


@dataclass(frozen=True)
class SourceScore:
    source_id: str
    score: float
    wilson_lower: float
    wilson_upper: float
    sample_size: int
    rationale: str


def score_source(
    source_id: str,
    need_cluster: str,
    ledger_record: dict[str, Any] | None,
    health_record: dict[str, Any] | None,
    now,
) -> SourceScore:
    """One source's real, explainable score for one need_cluster.
    ``ledger_record``/``health_record`` are the exact dicts returned by
    ``SourceValueLedger.get`` / ``ObservationHealth.score`` -- callers pass
    real rows, never synthesize them."""
    supports = ledger_record["supports"] if ledger_record else 0
    contradictions = ledger_record["contradictions"] if ledger_record else 0
    sample_size = supports + contradictions
    lower, upper = wilson_interval(supports, sample_size)

    health_score = float(health_record["health"]) if health_record else 0.5

    last_event = ledger_record.get("last_event_at") if ledger_record else None
    if last_event is None:
        recency_bonus = 0.5  # never used: neutral, encourages exploration
        recency_note = "never used"
    else:
        days_since = max(0.0, (now - last_event).total_seconds() / 86400.0)
        recency_bonus = max(0.0, 1.0 - days_since / RECENCY_HALF_LIFE_DAYS)
        recency_note = f"last used {days_since:.0f}d ago"

    # Real cost term: "which source is cheapest?" was asked for explicitly
    # but never scored -- compute_cost_ms_avg is real (found this session:
    # weather ~18s vs GDELT ~3.3 hours per real call) and was sitting
    # unused in the ledger. COST_SCALE_MS=60_000 (1 minute) means a
    # sub-minute source scores near 1.0 and GDELT's real multi-hour cost
    # scores near 0, without needing a source-specific hardcoded case.
    cost_ms = ledger_record.get("compute_cost_ms_avg") if ledger_record else None
    if cost_ms is None:
        cost_efficiency = 0.5  # unknown cost yet: neutral, not free
        cost_note = "cost unknown"
    else:
        cost_efficiency = 1.0 / (1.0 + cost_ms / COST_SCALE_MS)
        cost_note = f"cost={cost_ms / 1000:.1f}s/call"

    score = 0.35 * health_score + 0.35 * lower + 0.15 * recency_bonus + 0.15 * cost_efficiency
    rationale = (
        f"health={health_score:.2f}, wilson=[{lower:.2f},{upper:.2f}] over {sample_size} outcomes, "
        f"{recency_note}, {cost_note}"
    )
    return SourceScore(source_id, score, lower, upper, sample_size, rationale)


@dataclass
class EvidencePlan:
    sources: list[str] = field(default_factory=list)
    rationale: str = ""
    lesson_discount: float = 0.0
    excluded: list[str] = field(default_factory=list)


def lesson_discount_for(lessons: list[dict[str, Any]]) -> tuple[float, str]:
    """Repeated, real failure (>=3 confirmations) discounts a plan's
    priority -- Requirement 6/8: a lesson generalized from experience, not a
    hardcoded rule. Discount is capped and monotonic in confirmations."""
    if not lessons:
        return 0.0, ""
    strongest = max(lessons, key=lambda l: l["confirmations"])
    if strongest["confirmations"] < LESSON_CONFIRMATIONS_FOR_DISCOUNT:
        return 0.0, ""
    discount = min(MAX_LESSON_DISCOUNT, 0.1 * strongest["confirmations"])
    return discount, f"lesson '{strongest['lesson_text']}' (confirmed {strongest['confirmations']}x)"


def plan_evidence(
    need_cluster: str,
    candidate_sources: list[str],
    ledger,
    health,
    lessons: list[dict[str, Any]],
    unit_budget: float,
    now,
) -> EvidencePlan:
    """Score every real candidate source, exclude statistically-confirmed
    poor performers (Requirement 2), keep at least one candidate even if all
    are unproven (exploration floor), and take the best within budget
    (Requirement 4), most valuable first (Requirement 7)."""
    scored = []
    for source_id in candidate_sources:
        ledger_record = ledger.get(source_id, need_cluster)
        health_record = health.score(source_id) if health else None
        scored.append(score_source(source_id, need_cluster, ledger_record, health_record, now))
    scored.sort(key=lambda s: -s.score)

    eligible = [
        s for s in scored
        if not (s.sample_size >= MIN_SAMPLES_TO_EXCLUDE and s.wilson_upper < EXCLUDE_BELOW_UPPER_BOUND)
    ]
    excluded = [s for s in scored if s not in eligible]
    if not eligible and scored:
        # Exploration floor: every candidate has a confirmed-poor record for
        # this need_cluster -- take the single least-bad one rather than
        # collecting nothing, since "poor for this cluster" can still change
        # with new evidence and starving it forever would prevent that.
        eligible = [scored[0]]
        excluded = scored[1:]

    lesson_discount, lesson_note = lesson_discount_for(lessons)
    budget = unit_budget * (1.0 - lesson_discount)

    selected: list[str] = []
    spent = 0.0
    for s in eligible:
        if spent + 1.0 > budget:
            break
        selected.append(s.source_id)
        spent += 1.0

    parts = [s.rationale for s in eligible if s.source_id in selected]
    rationale = "; ".join(f"{sid}: {r}" for sid, r in zip(selected, parts))
    if lesson_note:
        rationale = f"{lesson_note} -> budget discounted to {budget:.1f} units; {rationale}"
    if excluded:
        rationale += f"; excluded (confirmed poor, n>={MIN_SAMPLES_TO_EXCLUDE}): {[s.source_id for s in excluded]}"

    return EvidencePlan(sources=selected, rationale=rationale, lesson_discount=lesson_discount,
                         excluded=[s.source_id for s in excluded])


def expected_value(need_cluster: str | None, kind: str, experience) -> tuple[float, str]:
    """Real historical success rate and real average cost for this
    (need_cluster, kind), from ``ResearchExperienceStore.similar_attempts``
    -- used to decide whether a question is worth opening/pursuing at all
    (Requirement 3), before any source is even scored. No prior experience
    means no claim either way: returns a neutral 0.5, not an invented low
    or high number."""
    attempts = experience.similar_attempts(need_cluster, kind=kind, limit=20)
    if not attempts:
        return 0.5, "no prior real experience for this (need_cluster, kind) -- neutral estimate"
    successes = sum(1 for a in attempts if a["outcome"] == "supported")
    lower, upper = wilson_interval(successes, len(attempts))
    p_success = (lower + upper) / 2  # point estimate; the interval itself is the honest uncertainty
    costs = [a["cost"].get("observation_units_spent", 0.0) for a in attempts]
    avg_cost = sum(costs) / len(costs) if costs else 0.0
    rationale = (
        f"{successes}/{len(attempts)} real prior attempts supported (wilson=[{lower:.2f},{upper:.2f}]), "
        f"avg cost {avg_cost:.1f} units"
    )
    return p_success, rationale


def capabilities_for(need_cluster: str | None, ledger, active_sources: list[dict[str, Any]]) -> list[str]:
    """What evidence is missing? Real capabilities of every source with any
    track record for this need_cluster, real or not-yet-excluded; falls
    back to every active capability only when this cluster has no history
    at all (pure exploration, not a guess about which capability matters).
    Takes an already-fetched ``active_sources`` list (not a registry
    handle) so a caller iterating many questions in one cycle does not
    re-run the same real SQL query once per question -- redundant-work
    removal, same real data, one real scan instead of N."""
    capabilities: set[str] = set()
    if need_cluster:
        for source in active_sources:
            record = ledger.get(source["source_id"], need_cluster)
            if record is not None:
                capabilities.add(source["observation_capability"])
    if capabilities:
        return sorted(capabilities)
    return sorted({s["observation_capability"] for s in active_sources})
