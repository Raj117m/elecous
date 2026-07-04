# ELECOUS

**A Scientific Demand Intelligence Platform.**

ELECOUS is not a chatbot, recommendation engine, or forecasting model. It
continuously observes the publicly observable world, discovers how external
changes influence product demand, validates those discoveries scientifically,
and uses validated knowledge to identify future demand opportunities before
they become obvious.

```
World → Observations → Signals → Environmental Factors → Mechanisms →
Validation → Knowledge → Prediction
```

Prediction is the last stage, not the objective. See
`elecous/docs/` for the full architecture specification this repository
implements chapter by chapter, and `elecous/docs/decisions/` for the two
points where a technical constraint required a documented, scientifically
equivalent substitution for the literal specification.

## The Scientific Constitution

- **Real public data only.** No synthetic observations, fake demand, or
  generated environmental factors — not even in tests. Every connector
  fetches from a real, legally accessible public source (Open-Meteo, USGS,
  NOAA/NWS, Wikimedia, GDELT, Nager.Date, World Bank, EIA, FRED).
- **Every observation has provenance.** Source, access method, extraction
  timestamp, and checksum, always.
- **Every prediction is explainable.** Traceable through mechanisms,
  environmental factors, signals, observations, and validation evidence.
- **Nothing is permanent.** Knowledge is versioned forever; confidence is
  earned and can be lost; lifecycle stages are never skipped.
- **Reality always wins.** Live observations override historical knowledge
  when they disagree.

These rules are enforced in code at `elecous/governance/constitution.py`,
not just documented — pipelines call these guards and refuse to proceed on
violation.

## Architecture

```
elecous/
  observation/    Source Registry, connectors, validators, sandbox, health
  demand/         Baselines, spike detection, Research Episodes, controls
  environment/    Signal extraction, pattern mining, Environmental Factors,
                  World State, World Conditions
  mechanisms/     Behavioural reasoning, hierarchy, versioned mechanism store
  validation/     Historical replay, prospective validation, skeptic engine,
                  calibration, concept drift, benchmarks
  knowledge/      Knowledge graph, memory, revision/aging, governance
  prediction/     Mechanism matching, opportunity formation, ranking, replay
  dashboard/      Read-only FastAPI backend + static frontend
  expansion/      Source discovery, sandbox promotion, curiosity engine
  infrastructure/ Config, logging, event bus, resources, supervisor
  scheduler/      Task scheduler, dynamic observation budget
  storage/        DuckDB manager (one database per responsibility), tiering
  governance/     Constitution guards
  configs/        All scientific thresholds, entities, sources — no
                  hardcoded assumptions in code
  tests/          One test module per phase; `network`-marked tests hit
                  real public APIs and skip cleanly when offline
```

Nine isolated DuckDB databases (`observations`, `demand`, `signals`,
`environment`, `mechanisms`, `knowledge`, `predictions`, `validation`,
`dashboard`) keep failure domains separate. Observations move through
hot → warm → cold storage tiers as they age.

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

# Seed the source registry (no network calls yet)
.venv/bin/elecous bootstrap

# Pull real historical data for every active source
.venv/bin/elecous backfill --days 365

# Start the scheduler + dashboard together
.venv/bin/elecous run
# Dashboard: http://127.0.0.1:8321

# One-shot health report
.venv/bin/elecous status
```

Two sources (`eia_electricity`, US energy demand; `fred_series`, US economic
series) require a free API key. Add them to
`elecous/configs/elecous.local.yaml` (git-ignored):

```yaml
credentials:
  eia_api_key: "your-key"
  fred_api_key: "your-key"
```

Until configured, those sources remain `suspended` in the registry — ELECOUS
waits for real data rather than inventing any.

## Testing

```bash
.venv/bin/pytest elecous/tests -q                 # offline-safe subset
.venv/bin/pytest elecous/tests -q -m network       # hits real public APIs
```

Tests never fabricate observations. Logic tests (hashing, lifecycle rules,
pattern-mining math, versioning) use abstract identifiers; everything that
touches demand, environmental, or knowledge data does so against real
backfilled public data and is marked `network`.

## What makes a build "done"

Per the specification (§11.26), ELECOUS is complete only when observation
coverage is stable, demand detection is reliable, environmental factors
consistently emerge, mechanisms survive prospective validation, predictions
remain calibrated, the dashboard reflects reality accurately, and
self-healing succeeds without manual intervention. All twelve subsystems in
`elecous/docs/PHASES.md` are implemented and tested against real data; the
knowledge base grows and self-corrects as more real history accumulates.
