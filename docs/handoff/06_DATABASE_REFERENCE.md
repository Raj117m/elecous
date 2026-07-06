# 06 — Database Reference

*Verified: `storage/database.py`, per-domain schema files (15 files register
migrations — full list in `docs/manual/02_REPOSITORY_STRUCTURE.md`), live
`du -sh` and row counts 2026-07-06.*

## Storage model

One DuckDB file per domain under `elecous/data/databases/`, opened via
`DatabaseManager.get(domain)`. **Single writer per file** — this is the most
operationally important fact in this whole manual; see 08_RUNNING_ELECOUS.md.
Each connection is `threading.RLock`-wrapped for in-process thread safety.

Migrations are idempotent additive: `CREATE TABLE/INDEX IF NOT EXISTS`,
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (`storage/database.py::Migration`).
Never destructive; safe to re-run.

## Measured sizes (2026-07-06)

Total ~5.6 GB. Largest table: `observations` (1,702,818 rows). Environment
domain (factors/states/concepts) is the fastest-growing due to append-only
mechanism/factor versioning — each `new_version()` call inserts a new row
rather than updating in place (real audit trail, real cost).

## Key tables (non-exhaustive — read the schema file for full DDL)

- `observations` (`observation/store.py`) — indexed on dedup_key, timestamp,
  (entity, observation_type), category. Dedup via checksum comparison.
- `research_episodes` (`demand/schema.py`) — versioned, append-only.
- `environmental_factors`, `candidate_states`, `candidate_concepts`
  (`environment/schema.py`) — versioned.
- `mechanisms` (`mechanisms/schema.py`) — versioned; `lifecycle` column
  (candidate/experimental/validated/trusted/declining/retired/archived).
- `validations`, `prospective_predictions`, `benchmarks`
  (`validation/schema.py`) — append-only history.
- `predictions`, `opportunities` (`prediction/schema.py`) — `outcome` column
  now actually populated by `resolve_due()` (was dead before this session).
- `knowledge_nodes`, `knowledge_edges` (`knowledge/schema.py`).
- `discovered_sources`, `connector_generation_attempts`
  (`expansion/source_discovery.py`, `expansion/connector_evolution.py`).

## Retention / compaction

**Not implemented.** Append-only versioning has no compaction or retention
window yet — flagged as technical debt in 11_KNOWN_PROBLEMS.md. `storage/tiers.py`
exists for hot/warm/cold tiering of `observations` by age but does not compact
mechanism/factor version history.

## Migration history

No separate migration-history document exists; migrations are named
(e.g. `"observations.0006.create_discovered_sources"`) and tracked in a
`MIGRATIONS_TABLE` inside each domain's own DuckDB file (verified:
`storage/database.py` creates this table on connect). To see applied
migrations for a domain: query that table directly.

## Verifying live vs. querying directly

DuckDB single-writer means you cannot `build_stack()` a second process while
`elecous run` / the dashboard holds the lock. Either stop that process, or
read through its HTTP API (`/api/*`) — this is what was done throughout this
session to avoid corrupting the live run.
