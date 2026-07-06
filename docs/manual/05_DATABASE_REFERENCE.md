# 05 — Database Reference

**Scope**: every table/field/index. **Read to complete** (each `CREATE TABLE`
lives in a Migration in these files): `observation/store.py`,
`observation/registry/registry.py`, `observation/registry/source_value_schema.py`,
`observation/gap.py`, `demand/schema.py`, `environment/schema.py`,
`signals/library.py`, `mechanisms/schema.py`, `validation/schema.py`,
`prediction/schema.py`, `knowledge/schema.py`, `research/schema.py`,
`dashboard/schema.py`, `expansion/source_discovery.py`,
`expansion/connector_evolution.py`.

Verified anchors:
- One DuckDB file per domain under `elecous/data/databases/` (`storage/database.py`
  `DatabaseManager.get(domain)`), RLock-wrapped, single-writer.
- Migrations are idempotent additive (`CREATE TABLE/INDEX IF NOT EXISTS`,
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) — pattern in `storage/database.py::Migration`.
- Largest table: `observations` (~1.7M rows). Indexed on dedup_key, timestamp,
  (entity,observation_type), category (`observation/store.py`).
- Append-only versioned tables: `mechanisms`, `environmental_factors` — carry a
  `version` column; latest = max(version) per id.
