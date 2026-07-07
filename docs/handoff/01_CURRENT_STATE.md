# 01 — Current State

*Verified via live API (`/api/activity`, `/api/health/system`, `/api/mechanisms`)
and direct source/DB inspection. Last updated 2026-07-07.*

## Measured numbers (real, not estimated)

- **1,702,818** real observations, spanning 2015–2026 (deep GDELT backfill to
  2017-06 landed this session; weather/pageviews to 2015).
- **2,382** demand episodes, every year 2017–2026 (was 500, zero pre-2024,
  before this session's corroboration fix).
- **147 validated mechanisms**, 467 retired, ~66 candidate (of ~680 total
  fleet). Confidence-deadlock bug fixed this session (see §"Fixed this session"
  below) — previously **zero** mechanisms had ever validated.
- **64 predictions generated.** Zero resolved yet (all still inside their
  lead-time windows — the resolver correctly never judges early).
- **Rolling benchmark**: ELECOUS F1 0.631 vs single-signal-baseline F1 0.626
  → `outperforms_baseline` (de-biased: samples the validated set id-ordered,
  not top-by-confidence).
- **Storage**: ~5.6 GB. **Sources**: 11 registered, 9 active, 2 suspended
  (`eia_electricity`, `fred_series` — missing API keys).
- **Tests**: 107/107 non-network tests passing.

## Environmental coverage (the core weakness this session addressed)

Measured composition of validated-mechanism signal-slots:
**weather-family ≈64%, media ≈36%, everything else ≈0%** (see
`docs/manual/AUDIT_environmental_bias.md` for the full root-cause analysis).

Root cause, confirmed by direct measurement (not assumed): the signal
extractor was **spike-only** (anomaly z-score). That structurally favors
volatile phenomena (weather, news) and silently discards slow-moving ones —
even though 16 observation types across 9+ categories were *already mapped*
in `configs/semantics.yaml` and *already being ingested*.

**Fixed this session**: added trend and level extraction modes alongside
spike (`environment/signal_discovery/extractor.py`). Measured result: 177
real signals, 36 (20%) now carry trend/level evidence beyond a bare spike.
**Confirmed separately by direct query**: economics/energy still register
zero signals — not an extraction problem but a real data-availability one
(suspended credentialed sources; World Bank macro data reports
annually/quarterly, zero real observations in the last 58 days).

## Fixed this session (chronological, each with real before/after numbers)

1. **UTC date-cast bug** — bare `CAST(timestamp AS DATE)` used session
   timezone, corrupting day-grouping in extractor + storage tiering.
2. **Ingestion throughput**: 513 → 4,465 obs/s (bulk CSV-staged native insert,
   after 4 measured hypotheses eliminated by direct benchmark).
3. **Concurrent scheduler**: subsystems now overlap; same-subsystem tasks
   serialize; a long network task no longer starves the other 37.
4. **Confidence deadlock** (the biggest single fix): mechanism confidence was
   frozen below the promotion floor because the only writer of confidence ran
   *after* validation, which required confidence *above* the floor — a true
   circular deadlock. Fixed by writing real validation evidence (replay
   precision) into the previously-dead `scientific` confidence channel.
   Result: 0 → 147 validated mechanisms, 0 → 64 predictions.
5. **Two self-caught integrity bugs** while verifying #4: TRUSTED was
   reachable via historical re-validation alone (should require real
   prospective/future confirmation) — capped; the rolling benchmark sampled
   top-by-confidence, which post-fix meant cherry-picking best performers —
   re-pointed at the validated set, id-ordered.
6. **Prediction outcome resolution**: `predictions.outcome` had a comment
   saying "filled by resolve" but no resolver existed anywhere. Built
   `resolve_due()` — judges every prediction against real episodes after its
   own lead-time horizon.
7. **Mechanism retirement**: `Lifecycle.RETIRED` was legal but unreachable for
   mechanisms (only signals had real automatic retirement). Added
   `retire_persistent_failures()`.
8. **Resumable GDELT backfill**: first 2017-deep attempt was killed and lost
   its entire ~60-minute batch (ingests only at sweep end). Rebuilt as
   chunked (40 entities/chunk), resume state derived from real per-entity
   coverage, not a state file.
9. **Multi-mode signal extraction** (this turn) — see above.
10. **Dashboard**: rebuilt from a developer admin panel into a live-updating
    demand intelligence surface — decision-first Today view, unified timeline,
    global search, product ranking, product intelligence detail pages, honest
    coverage disclosure, human-language task/signal names, and a scheduler-live
    dashboard entry point (previously only `elecous run` did — dashboard-only
    mode always looked idle).

## Not done / explicitly deferred (see 10_NEXT_STEPS.md and 11_KNOWN_PROBLEMS.md)

- Full historical pipeline rerun on the deepened 2017+ corroborated history
  (would take 1+ hours; not run this turn due to context constraints).
- Broader environmental categories (social, search, retail, pricing,
  logistics, disease, agriculture, finance) — need new connectors, i.e.
  external data, not a software fix.
- Product-lifecycle / adaptive-attention architecture — designed, deferred
  with a measured trigger (~10K entities; today's scan is 5.9ms/entity).
- Deep Bloomberg/Palantir-grade drill-downs (per-prediction evidence panels,
  replay UI, interactive knowledge graph, adaptive product-lifecycle modeling)
  — product intelligence now exists as a read-only composition layer, but the
  deeper scientific/product lifecycle layers remain future work.
