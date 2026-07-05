# 0004: Research Intelligence — Level-2 Scientific Memory (Rewrite Directive)

## Context

Three audits converged on one diagnosis: ELECOUS collects and validates real
data competently but has no mechanism that improves *how* it decides what to
collect or which strategies to trust. Concretely, verified in the running
codebase rather than assumed:

- `source_registry.historical_contribution` has existed since Phase 2 and was
  never once written by any code path.
- `EventType.VALIDATION_COMPLETED` fires on every validation and had zero
  subscribers.
- There was no code path from a validated factor/mechanism back to which
  `source_id`s produced it — `source_id` was discarded at the very first
  aggregation query in `EnvironmentalSignalExtractor._compute_changes`.
- `mechanisms.evidence_links` promised an `"observations"` key in its own
  schema comment that was never populated.
- Curiosity's surfaced "unknowns" had no consumer that turned them into a
  prioritized, budgeted evidence-collection action.

The owner's rewrite directive: stop optimizing for data volume; build a
system that reasons — question, predict where evidence should exist, collect
only the highest-value evidence, update both world-knowledge (Level 1) and
research-strategy knowledge (Level 2), repeat.

## Decision

Add a new `elecous/research/` package as a genuinely additive layer on top of
the existing, working pipeline — extend dead/unwired fields rather than
replace working infrastructure. Full design in the approved plan (see git
history); summarized:

- **Research Question** (`research/questions.py`) — a first-class,
  answerable claim, generalizing Curiosity's three hardcoded surfacing rules.
- **Source Value Ledger** (`research/source_value.py`,
  `source_value_records` table in the `observations` domain) — finally
  writes `historical_contribution`, keyed per (source, need_cluster) so a
  source's usefulness is measured relative to what it actually helped with.
- **Research Experience / Lessons** (`research/schema.py`, new `research`
  domain) — one experience row per resolved question; repeated failure
  patterns generalize into lessons that discount a future question's plan.
- **Evidence lineage** — `SignalChange.contributing_sources`,
  `episode_signals.source_ids`, and `mechanisms.evidence_links["observations"]`
  now capture which sources fed which evidence, closing the gap that made
  source crediting impossible.
- **Knowledge Graph** — `sync_source`/`sync_research_question` give
  `ObjectType.SOURCE` its first graph nodes and put the already-defined
  `OBSERVED_BY`/`DERIVED_FROM`/`REPLACES` relationships to their first real
  use, instead of inventing new vocabulary.

## Staged rollout (this decision covers Stage 1 only)

1. **Shadow schema** (this change) — new tables/columns, lineage capture,
   vocabulary/graph extensions. Nothing wired into `bootstrap.py`, the
   scheduler, or the event bus yet. All existing tests pass unmodified.
2. **Shadow subscribers** — instantiate the ledger/question store in the
   stack, subscribe to `VALIDATION_COMPLETED`/`UNKNOWN_RECORDED`, start
   writing real data with zero effect on existing scheduled behavior.
3. **Controlled activation** — wire question-driven demand into
   `retune_schedules()`'s budget allocator, behind a config flag, after
   inspecting Stage 2's real accumulated data.
4. **Optional cutover** — point Curiosity's question selection at the new
   store instead of `knowledge.research_agenda()` directly. The only stage
   that touches an existing working code path; kept last, one-line
   revertible.

## Also removed in this change

Four empty, never-implemented placeholder subpackages under `expansion/`
(`connector_generation/`, `evaluation/`, `promotion/`, `source_discovery/`,
`sandbox/`) — planned but never built; the real promotion/sandbox logic
already lives in `observation/sandbox/`. Consolidated a duplicated
`_season_of` helper (independently redefined in `environment/engine.py` and
`scripts/evidence_report.py`) into `common/timeutil.season_of`.

## Consequences

`historical_contribution` and the `VALIDATION_COMPLETED` event remain inert
until Stage 2 subscribes a listener — Stage 1 intentionally ships schema and
lineage plumbing only, verified to have zero behavioral effect on the
existing real-data pipeline.
