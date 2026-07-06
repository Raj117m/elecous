# 03 — Repository Guide

*Verified: `find elecous -name "*.py"` (196 modules), `ls` per package,
`grep -rln "Migration("`. See also `docs/manual/02_REPOSITORY_STRUCTURE.md`
(same content, generated the prior session — cross-checked, unchanged).*

## Top-level layout

```
elecous/
  bootstrap.py          — the ONE wiring point; build_stack() constructs everything
  common/                — ids, timeutil, vocabulary (lifecycles/relationships), errors
  governance/             — constitution.py: provenance/promotion/leakage guards
  infrastructure/         — config, event_bus, resources, supervisor, cache (TTLCache), http
  scheduler/              — scheduler.py (concurrent), registration.py (38 tasks), budget.py
  storage/                — database.py (DuckDB manager), tiers.py
  observation/            — connectors/, registry/, store.py, validators/, sandbox/, gap.py, network.py
  demand/                 — engine.py, spikes/detector.py, episodes/, baseline/
  environment/            — engine.py, signal_discovery/ (extractor.py — just modified),
                            factor_discovery/, pattern_engine/, world_state.py, schema.py
  signals/                — library.py (signal candidate→validated lifecycle)
  mechanisms/              — engine.py, versioning/store.py, reasoning/, schema.py
  validation/              — engine.py (facade), skeptic/, historical/replay.py,
                            prospective/, calibration/, drift/, benchmarks/, schema.py
  prediction/               — engine.py, world_matching/matching.py, opportunities/, schema.py
  knowledge/                — engine.py, graph/graph.py, schema.py
  research/                  — engine.py, source_value.py, priority.py, questions.py, experiments.py
  expansion/                  — source_discovery.py, connector_evolution.py, curiosity.py, entity_discovery.py
  dashboard/                   — api/app.py, api/routes.py, frontend/index.html, monitor/notifications.py
  scripts/                      — cli.py, rerun_pipeline.py, deep_backfill_gdelt.py, backfill_source_value.py
  configs/                       — sources.yaml, candidate_sources.yaml, entities.yaml,
                                  thresholds.yaml, semantics.yaml, elecous.yaml
  tests/                          — pytest, 106 non-network tests + network-marked tests
  data/                            — runtime DuckDB files, ~5.6 GB
  docs/                             — decisions/ (ADRs), manual/ (prior session), handoff/ (this)
```

## Entry points

- `elecous run` (`scripts/cli.py::cmd_run`) — full platform, foreground.
- `python -m elecous.dashboard.api.app` (`dashboard/api/app.py::main`) — same
  full platform (starts the scheduler too, as of this session).
- `elecous bootstrap` / `elecous backfill --days N` — one-shot seeding/backfill.
- `scripts/rerun_pipeline.py`, `scripts/deep_backfill_gdelt.py` — operational
  drivers, run directly with `.venv/bin/python`.

## Configuration files (see 07_CONFIGURATION_REFERENCE.md for values)

`configs/*.yaml` — no JSON/TOML/INI config in this repo (verified: `find
elecous/configs -type f` shows only `.yaml`). `elecous.yaml` is the root;
others load via `Config.load_file(name)`.

## Database files (runtime, not committed)

`elecous/data/databases/*.duckdb` — one per domain: `observations`, `demand`
(episodes), `environment` (factors/states/concepts), `signals`, `mechanisms`,
`validation`, `predictions`, `knowledge`, `research`. Sizes and growth in
`docs/manual/05_DATABASE_REFERENCE.md`.

## API routes

`dashboard/api/routes.py` — 32 `@router.get`/`@router.post` decorators.
Generate the current list with:
```bash
grep -n "@router" elecous/dashboard/api/routes.py
```

## Frontend

`dashboard/frontend/index.html` — single file, no build step, no framework.
`VIEWS` array + `renderers` map define the 15 views.

## No deprecated/experimental code found

Direct inspection this session and the prior manual session found no files
marked deprecated or clearly dead. The closest historical case: four empty
`elecous/expansion/{connector_generation,evaluation,promotion,source_discovery}/__init__.py`
placeholder packages were found and deleted in an earlier session (their
concepts were later built for real as `connector_evolution.py` and
`source_discovery.py` — confirm these deletions are still reflected if you
`git log` those paths).
