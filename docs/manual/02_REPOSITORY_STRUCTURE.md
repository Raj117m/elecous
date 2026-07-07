# 02 — Repository Structure

*Verified: `ls elecous/`, `find elecous -name "*.py"` (196 modules), and the
per-package inspection below. Session 2026-07-06.*

## Top level (`elecous/`)

| Path | Purpose | Key files |
|------|---------|-----------|
| `bootstrap.py` | Constructs the full `ElecousStack` in the one legal dependency order; single wiring point | `build_stack()`, `ElecousStack` dataclass |
| `common/` | Shared primitives: ids, timeutil, vocabulary (lifecycles, relationships), errors | `vocabulary.py`, `ids.py`, `timeutil.py` |
| `governance/` | The constitution — provenance, promotion, leakage guards | `constitution.py` |
| `infrastructure/` | config, event bus, resources, logging, http, cache (TTLCache), supervisor | `config.py`, `event_bus.py`, `resources.py`, `supervisor.py`, `cache.py`, `http.py` |
| `scheduler/` | cooperative concurrent scheduler + task registration + observation budget | `scheduler.py`, `registration.py`, `budget.py` |
| `storage/` | DuckDB manager (one DB per domain, RLock-wrapped) + tiering | `database.py`, `tiers.py` |
| `observation/` | connectors, registry, store, validators, gap engine, sandbox, network | `network.py`, `store.py`, `registry/`, `connectors/`, `validators/`, `sandbox/`, `gap.py` |
| `demand/` | spike detector, baselines, episodes, eligibility, controls, scientist | `engine.py`, `spikes/detector.py`, `episodes/`, `baseline/` |
| `environment/` | signal extractor/discovery, pattern mining, factor store, states, concepts, world state, ontology | `engine.py`, `signal_discovery/`, `factor_discovery/`, `pattern_engine/`, `schema.py`, `world_state.py` |
| `signals/` | signal library (candidate→validated lifecycle) | `library.py` |
| `mechanisms/` | generation, behavioural reasoning, versioning store, confidence | `engine.py`, `versioning/store.py`, `reasoning/`, `schema.py` |
| `validation/` | facade engine + skeptic, historical replay, prospective, calibration, drift, negative learning, benchmarks | `engine.py`, `skeptic/`, `historical/replay.py`, `prospective/`, `calibration/`, `drift/`, `benchmarks/`, `schema.py` |
| `prediction/` | world matching, opportunities, lead time, uncertainty, business value, ranking | `engine.py`, `world_matching/matching.py`, `opportunities/`, `schema.py` |
| `knowledge/` | knowledge graph, compression, memory, self-healing | `engine.py`, `graph/graph.py`, `schema.py` |
| `research/` | source-value ledger, question store, priority, experience, experiments | `engine.py`, `source_value.py`, `priority.py`, `questions.py`, `experiments.py`, `schema.py` |
| `expansion/` | source discovery, connector evolution, curiosity, entity discovery, expansion engine | `source_discovery.py`, `connector_evolution.py`, `curiosity.py`, `entity_discovery.py`, `engine.py` |
| `dashboard/` | FastAPI app + routes + single-file frontend + notifications | `api/app.py`, `api/routes.py`, `frontend/index.html`, `monitor/notifications.py`, `schema.py` |
| `scripts/` | CLI + operational drivers (backfill, rerun pipeline, deep GDELT backfill, audits) | `cli.py`, `rerun_pipeline.py`, `deep_backfill_gdelt.py`, `backfill_source_value.py` |
| `configs/` | YAML: sources, candidate_sources, entities, thresholds, elecous.yaml | see doc 13 |
| `tests/` | pytest suites per phase (107 non-network tests) | `test_phase*.py`, `test_research.py` |
| `data/` | DuckDB files (one per domain) — runtime, ~5.6 GB | `databases/*.duckdb` |
| `docs/` | ADRs + this manual | `decisions/`, `manual/` |

## Schema-owning files (verified: `grep -rln "Migration("`)

15 files register migrations: `observation/store.py`, `observation/registry/registry.py`,
`observation/registry/source_value_schema.py`, `observation/gap.py`, `demand/schema.py`,
`environment/schema.py`, `signals/library.py`, `mechanisms/schema.py`, `validation/schema.py`,
`prediction/schema.py`, `knowledge/schema.py`, `research/schema.py`, `dashboard/schema.py`,
`expansion/source_discovery.py`, `expansion/connector_evolution.py`.

## Per-file deep documentation

Not yet enumerated function-by-function for all 196 modules. To generate:
`grep -rn "def \|class " <file>` per module. Prioritize the engine `engine.py`
files and the schema files — they carry the load-bearing logic.
