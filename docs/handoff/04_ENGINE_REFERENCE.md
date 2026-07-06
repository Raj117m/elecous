# 04 — Engine Reference

*Verified: constructor signatures in `bootstrap.py`, each engine's `engine.py`.
For deep per-method detail, read the named engine file directly — this
document gives purpose/inputs/outputs/limitations, not a line-by-line dump.*

## ObservationNetwork (`observation/network.py`)
Purpose: run connectors, validate + store observations, own the registry/gap
engine/sandbox. Input: nothing (pulls from real public APIs). Output:
`observations` table rows. DB: `observations` domain (shared with demand's
episode-adjacent tables). Limitation: single-writer DuckDB means only one
process can hold this domain at a time.

## DemandEngine (`demand/engine.py`)
Purpose: detect real demand spikes → episodes. Input: observations. Output:
`research_episodes` rows. Algorithm: robust z-score (MAD-based) vs a learned
baseline window, staged ladder (small_growth→acceleration→abnormal_acceleration),
requires a **second independent signal** at ≥acceleration to confirm an
abnormal spike (spec 3.9) — this corroboration requirement is *why* episode
formation was blocked pre-2024 until media coverage existed (fixed this
session by deep-backfilling GDELT to 2017).

## EnvironmentalEngine (`environment/engine.py`)
Purpose: turn episodes into factors, own states/concepts/world-state.
Sub-module just modified this session: `signal_discovery/extractor.py` — now
spike+trend+level multi-mode (see `docs/manual/AUDIT_environmental_bias.md`).
Limitation: `environment.duckdb` grows via append-only factor versioning
(technical debt, not yet compacted).

## MechanismEngine (`mechanisms/engine.py`)
Purpose: factor combinations → candidate causal mechanisms with a behavioural
chain. Confidence formula (`versioning/store.py::update_confidence`):
`current = 0.3·recent + 0.25·historical + 0.2·scientific + 0.15·regional +
0.1·category`. The `scientific` channel used to be dead (always 0.0) —
fixed this session to be populated from real validation evidence.

## ValidationEngine (`validation/engine.py`)
Purpose: the ONLY promoter (spec 6.30). Six checks + confidence floor gate
mechanism promotion. Owns skeptic, historical replay, prospective validator,
calibration, drift detection, rolling benchmark, retirement. This session
added `retire_persistent_failures()` and fixed the confidence-deadlock that
had blocked 100% of mechanisms from ever validating.

## PredictionEngine (`prediction/engine.py`)
Purpose: match validated mechanisms against current world state → predictions
+ opportunities. `resolve_due()` (added this session) judges predictions
against real episodes after their lead-time horizon.

## KnowledgeEngine (`knowledge/engine.py`) + KnowledgeGraph (`knowledge/graph/graph.py`)
Purpose: node/edge graph over sources, mechanisms, factors, research
questions, discovered sources. Self-healing (`detect_issues`/`self_heal`) for
broken edges / duplicate nodes.

## ResearchEngine (`research/engine.py`)
Purpose: Wilson-score source-value ledger + question-priority planning +
experience/lessons. Real precedent for the "Autonomous Backlog Manager"
concept requested in an earlier directive but not yet generalized — see
10_NEXT_STEPS.md.

## Expansion engines (`expansion/`)
- `source_discovery.py`: robots.txt/sitemap/RSS autodiscovery on **already-
  known** organization domains only (no general web search — ADR 0002).
- `connector_evolution.py`: classifies + auto-extracts json/csv/tsv/rss/atom/
  sitemap sources; HTML/PDF/OpenAPI classified but not extracted.
- `curiosity.py`: surfaces open research questions.

## Scheduler (`scheduler/scheduler.py`)
Concurrent, per-subsystem-serialized, bounded worker pool (default 4). See
02_COMPLETE_ARCHITECTURE.md for the full flow diagram.

## Dashboard (`dashboard/api/`, `dashboard/frontend/`)
FastAPI + single-file JS. Zero scientific reasoning in routes (spec 9.29).
