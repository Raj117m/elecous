# Operations Runbook

## Starting the platform

```bash
.venv/bin/elecous bootstrap        # idempotent; seeds registry only
.venv/bin/elecous backfill --days 365
.venv/bin/elecous run              # scheduler + dashboard, foreground
```

`elecous run` registers all 30 recurring tasks (`scheduler/registration.py`)
and starts the FastAPI dashboard on the configured host/port
(`configs/elecous.yaml: dashboard.host/port`). Stop with Ctrl-C; the
scheduler stops cleanly and DuckDB connections close.

## Health checks

```bash
.venv/bin/elecous status
```

Reports observation, knowledge, validation, scheduler and resource health
in one JSON document — this is exactly what
`AutonomousSupervisor.status()` returns and what the dashboard's
System Health panel displays.

## When a connector fails

The Supervisor's `supervisor:self_heal` task (every 30 minutes) already
retries failing sources once and suspends them if the retry fails, then
records an Observation Request via the Gap Engine. No action is normally
required. If a source has been suspended for more than a few days:

1. Check `GET /api/observation/sources` for `status: suspended` entries.
2. Check `GET /api/expansion` for open `observation_requests`.
3. If a credentialed source (EIA, FRED) is suspended, verify the API key in
   `elecous/configs/elecous.local.yaml`.
4. If a public API changed its schema or terms, the connector needs a code
   fix — check `elecous/data/logs/observation.log` for the specific error.

## When self-healing escalates

A `self_healing_exhausted` notification (`GET /api/notifications`) means the
Supervisor retried a subsystem 3 times without success. This is the one
case that requires human judgment (spec 13.14: humans are Scientific
Directors, not operators) — inspect the referenced subsystem's logs and
decide whether to fix, wait, or manually suspend further attempts.

## Disaster recovery

Follow spec §10.29 / §11.25: stop the affected subsystem, preserve the
current DuckDB files (`elecous/data/databases/*.duckdb`) as evidence, restore
from the most recent verified backup if corruption is confirmed, replay
missed observations via `elecous backfill`, and let the scheduler's
`validation:revalidate` and `knowledge:sync` tasks rebuild derived state.
Nothing in ELECOUS treats recovery as "restart the process" — knowledge
integrity must be reconfirmed before resuming autonomous operation.

## Adding a new observation source

1. Write a `Connector` subclass in `elecous/observation/connectors/`.
2. Add an entry to `elecous/configs/candidate_sources.yaml`.
3. Either wait for the Gap Engine to request that capability naturally, or
   run `elecous.expansion.run_expansion_cycle()` directly — it will sandbox
   and promote automatically if all checks pass. See ADR 0002 for why
   *discovery* of the candidate itself is a human/curator step.

## Configuration

Everything scientific is configuration, not code (spec 10.20):
`elecous/configs/{elecous,sources,entities,semantics,thresholds,behaviour,
candidate_sources}.yaml`. Machine-specific overrides (API keys, data
directory) go in `elecous/configs/elecous.local.yaml`, which is git-ignored.
