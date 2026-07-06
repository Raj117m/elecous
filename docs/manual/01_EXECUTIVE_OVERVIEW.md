# 01 — Executive Overview

*Verified from: `elecous/bootstrap.py`, `elecous/governance/constitution.py`,
`elecous/scheduler/registration.py`, and the engine packages named below.*

## What it is

ELECOUS discovers **why demand spikes** and predicts **where it will spike
next**, from real public data only. It is not a dashboard over a database; it
is a closed scientific loop: observe → discover factors → form mechanisms →
validate → predict → verify against reality → learn.

## Governing philosophy (`elecous/governance/constitution.py`)

- **No synthetic observations, ever.** `require_real_provenance` rejects any
  observation lacking a real public source_id + access method + URL.
- **No promotion without earned evidence.** `require_validated_promotion`
  enforces the lifecycle: nothing becomes VALIDATED/TRUSTED without passing
  validation; stages never skip.
- **No future leakage.** `require_no_future_leakage` guards historical replay
  so a backtest can never see data postdating its instant.

## The engines (each a package under `elecous/`)

| Layer | Package | Role |
|-------|---------|------|
| Observation | `observation/` | connectors, registry, store, validators, gap engine, sandbox |
| Demand | `demand/` | spike detection, baselines, episodes, controls |
| Environment | `environment/` | signal extraction, factor formation, states, concepts, world state |
| Mechanisms | `mechanisms/` | causal hypothesis generation, versioning, confidence |
| Validation | `validation/` | skeptic, historical replay, prospective, calibration, drift, benchmarks, retirement |
| Prediction | `prediction/` | world matching, opportunities, lead time, resolution |
| Knowledge | `knowledge/` | graph, compression, self-healing |
| Research | `research/` | source-value ledger, question planning, experiments |
| Expansion | `expansion/` | source discovery, connector evolution, curiosity |
| Infrastructure | `infrastructure/`, `scheduler/`, `storage/` | config, event bus, resources, supervisor, scheduler, DuckDB |
| Dashboard | `dashboard/` | FastAPI + single-file frontend |

## Lifecycles (verified from `elecous/common/vocabulary.py` LEGAL_TRANSITIONS)

- **Mechanism**: CANDIDATE → EXPERIMENTAL → VALIDATED → TRUSTED, with
  DECLINING/RETIRED/ARCHIVED exits. Historical validation promotes at most to
  VALIDATED; TRUSTED requires *prospective* (future) confirmation
  (`validation/engine.py`).
- **Prediction**: generated → pending → correct/incorrect/partially_correct,
  resolved automatically after its lead-time horizon
  (`prediction/engine.py::resolve_due`).

## End-to-end flow (verified: `scheduler/registration.py`)

38 scheduled tasks run this loop continuously on a concurrent worker pool:
`observe:*` connectors → `demand:scan` → `environment:research_cycle` →
`mechanisms:generate` → `validation:{revalidate,prospective,rolling_benchmark,retire}`
→ `prediction:{predict,resolve}` → `knowledge:sync` → `research:*`,
`curiosity:*`, `expansion` discovery. The dashboard entry point
(`dashboard/api/app.py::main`) starts this same scheduler, so opening the
dashboard *is* running the platform.
