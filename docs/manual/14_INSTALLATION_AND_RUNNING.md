# 14 — Installation and Running

*Verified: `elecous/scripts/cli.py`, `elecous/dashboard/api/app.py`,
observed commands run throughout session 2026-07-06.*

## Requirements

- Python 3.11 (the repo's `.venv` is 3.11; `duckdb`, `fastapi`, `uvicorn`,
  `requests`, `numpy`, `pyyaml` — installed in `.venv`).
- No external services. DuckDB is embedded (file-per-domain under
  `elecous/data/databases/`). No pandas/pyarrow (verified absent — the bulk
  ingest path uses DuckDB's native CSV reader instead).

## First run

```bash
cd ~/ELECOUS
.venv/bin/elecous bootstrap      # seed the source registry (cli.py::cmd_bootstrap)
.venv/bin/elecous backfill --days N   # real historical backfill of active sources
```

## Running the platform

```bash
.venv/bin/elecous run            # scheduler + dashboard, foreground (cli.py::cmd_run)
# OR, equivalently — the dashboard entry point now starts the scheduler too:
.venv/bin/python -m elecous.dashboard.api.app
```

Dashboard: **http://127.0.0.1:8321** (host/port from `configs/elecous.yaml`
`dashboard.*`). Opening it *is* running the platform: `app.py::main` calls
`register_all_schedules(stack)` then `stack.scheduler.start()`.

## Testing

```bash
unset ELECOUS_DATA_DIR   # use isolated test DBs, never the real data dir
.venv/bin/python -m pytest elecous/tests -m "not network" -q   # 106 tests
```

Network-dependent tests are marked `@pytest.mark.network` and deselected by
default. `ELECOUS_DATA_DIR` points the stack at an alternate data directory —
tests set it to a temp dir via the `isolated_config` fixture.

## Operational drivers (`elecous/scripts/`)

- `rerun_pipeline.py` — full historical rewalk (demand scan → research cycle →
  mechanisms → validation), prints per-stage counts.
- `deep_backfill_gdelt.py` — resumable chunked GDELT backfill to 2017; resume
  state derived from real per-entity coverage.
- `backfill_source_value.py` — populate the source-value ledger from history.

## Critical operational note (observed repeatedly)

DuckDB is **single-writer**. While `elecous run` or the dashboard holds the
lock, any second process calling `build_stack()` fails with an IO lock error.
To inspect live state, use the HTTP API; to run a script against the data dir,
stop the running process first.

## Profiling

`/usr/bin/time -l <cmd>` gives wall/CPU/peak-RSS (used for all this session's
before/after measurements). Per-stage timing: wrap each stage and print
`time.perf_counter()` deltas (see `rerun_pipeline.py` pattern).
