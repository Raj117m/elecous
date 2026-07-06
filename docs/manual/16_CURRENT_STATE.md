# 16 — Current State

*Verified via live API and direct measurement, session 2026-07-06.*

## Works, proven on real data

- **Ingestion**: 11 sources, 1,702,818 real observations, 2015–2026. Bulk CSV
  ingest path measured at 4,465 obs/s end-to-end (was 513). `observation/store.py`.
- **Demand episodes**: 2,382, every year 2017–2026 (was 500, none pre-2024).
  Corroboration across two independent signals (spec 3.9) is what unlocked the
  full-decade history. `demand/spikes/detector.py`.
- **Mechanisms**: 147 validated, 467 retired, rest candidate. Confidence
  deadlock fixed so historical evidence can move confidence
  (`validation/engine.py`), and TRUSTED correctly reserved for prospective
  confirmation only.
- **Predictions**: 64 generated, evidence-backed (confidence, lead time,
  matched signals). Automatic resolution against real episodes after horizon
  (`prediction/engine.py::resolve_due`).
- **Benchmark**: rolling, de-biased (samples the validated set, not top-by-
  confidence). ELECOUS F1 0.631 vs single-signal baseline 0.626 →
  `outperforms_baseline`. `validation/engine.py::run_rolling_benchmark`.
- **Concurrent scheduler**: subsystems overlap, same-subsystem tasks serialize,
  long network tasks don't starve others. Measured in a permanent test.
- **Retirement, self-healing, connector regeneration, source discovery,
  resumable backfill**: all built and exercised live this session.
- **Dashboard**: 15 views, live auto-refresh, human-readable task/signal names,
  decision-first opportunity cards, honest coverage note. 32 API routes.

## Partially works / unproven-by-design

- **Prospective outcomes**: 64 predictions registered but outcomes need real
  calendar time to resolve — none resolved yet. Not a bug; the resolver
  correctly never judges an open window early.
- **Autonomous connector generation**: works for json/csv/rss/atom/sitemap;
  HTML/PDF/OpenAPI classified but not extracted (`expansion/connector_evolution.py`).
- **Source discovery**: finds sitemap/RSS on already-known org domains only;
  no general web search (ADR 0002).

## Weakest area (from the product audit, measured)

- **Coverage bias**: validated mechanisms rest ~64% on weather-family
  phenomena, ~36% on media attention; social, search, retail, pricing,
  logistics, disease, agriculture, finance are **not ingested at all**. The
  dashboard now states this honestly rather than implying breadth.

## Technical debt

- `environment.duckdb` accumulates factor-version rows via append-only
  versioning (documented cost).
- Per-file function docs for all 196 modules not yet written (this manual).
- The V2 "adaptive attention" / product-lifecycle layer is designed-but-
  deferred with a measured trigger (~10K entities; scan is 5.9ms/entity today).
