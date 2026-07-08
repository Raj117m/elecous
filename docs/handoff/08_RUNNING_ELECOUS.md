# 08 — Running ELECOUS

*Verified by direct execution throughout this session, 2026-07-06.*

## Requirements
Python 3.11, the repo's `.venv` (duckdb, fastapi, uvicorn, requests, numpy,
pyyaml). No pandas/pyarrow (confirmed absent — bulk ingest uses DuckDB's
native CSV reader). No external services — DuckDB is embedded.

## First run
```bash
cd ~/ELECOUS
.venv/bin/elecous bootstrap
.venv/bin/elecous backfill --days N
```

## Running continuously
```bash
.venv/bin/elecous run
# equivalent, and what was used most this session:
.venv/bin/python -m elecous.dashboard.api.app
```
Dashboard: **http://127.0.0.1:8321**. Both commands start the scheduler (38
tasks) AND the dashboard — opening the dashboard is running the platform.

## ⚠️ Critical operational rule: single-writer DuckDB
**Never run a second script against the same data directory while `elecous
run` / the dashboard is active** — you will get an IO lock error. This
happened repeatedly this session. To inspect live state:
1. Prefer the HTTP API (`curl http://127.0.0.1:8321/api/...`).
2. If you need a script, `pkill -f "elecous.dashboard.api.app"` first, run
   your script, then restart.

## Tests
```bash
unset ELECOUS_DATA_DIR
.venv/bin/python -m pytest elecous/tests -m "not network" -q   # 106 tests
```
Network tests are `@pytest.mark.network`, deselected by default (they hit
real public APIs). `isolated_config` fixture gives each test its own temp
data dir — never runs against real production data.

## Benchmarks
```python
# inside a script or REPL, with the stack built (no other process holding the lock)
stack.validation.run_rolling_benchmark(sample_size=40)
```
Samples the **validated** mechanism set, id-ordered (not by confidence —
that would cherry-pick, a real bug found and fixed this session). Scheduled
daily as `validation:rolling_benchmark`.

## Historical replay
```python
stack.validation.replay.replay_mechanism(mechanism_row)   # per-mechanism backtest
stack.prediction.replay(as_of_datetime)                    # what would have predicted, as of a date
```

## Backfills
```bash
.venv/bin/elecous backfill --days 730       # standard backfill via CLI
.venv/bin/python elecous/scripts/deep_backfill_gdelt.py   # resumable, chunked deep GDELT backfill
.venv/bin/python elecous/scripts/rerun_pipeline.py         # full historical rewalk after a backfill
```
`deep_backfill_gdelt.py` resumes from **real per-entity coverage already in
the database** (queries which entities already have pre-2018 rows) — never
from a separate checkpoint file. Safe to kill and re-run.

## Debugging / profiling
`/usr/bin/time -l <cmd>` gives wall/CPU/peak-RSS — used for every before/after
measurement this session. For per-stage timing inside a script, wrap stages
with `time.perf_counter()` (see `rerun_pipeline.py` for the pattern).

## Deployment
Not containerized; runs as a plain Python process on the developer's
MacBook Pro M4 (150–200 GB free SSD is the stated target hardware). No
multi-machine deployment exists or is currently justified (see
`docs/manual/AUDIT_environmental_bias.md` and prior V2 scaling discussion —
the adaptive-attention/distributed-architecture work has a measured trigger
of ~10K monitored entities, not yet reached).
