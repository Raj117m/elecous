# 09 — Developer Guide

*Verified: existing implementations of each pattern below, cited by file.*

## Adding a connector
- Format-driven (json/csv/tsv/rss/atom/sitemap): may not need code —
  `expansion/connector_evolution.py`'s `AutoConnector` can classify and
  extract these automatically once discovered via `source_discovery.py`.
- Custom format: subclass `observation/connectors/base.py::Connector`, set
  `parser_id`, implement `collect(mode, since)`. Register in `configs/sources.yaml`.
- Add a `phenomena` entry in `configs/semantics.yaml` for any new observation
  type so it can become a signal.

## Adding a scheduler task
One line in `scheduler/registration.py`:
```python
scheduler.register("subsystem:task_name", "subsystem", callable, timedelta(...), priority=N)
```
Priority 1 = highest. Tasks in the same "subsystem" string serialize against
each other; different subsystems run concurrently.

## Adding a dashboard page
`dashboard/frontend/index.html`: add `["key","Label"]` to `VIEWS`, add
`key: renderFn` to `renderers`, write an async function that fetches from
`/api/...` and returns an HTML string using the existing `panel()`/`row()`/
`kpi()`/`pill()` helpers for visual consistency.

## Adding an API endpoint
`dashboard/api/routes.py`: add a `@router.get`/`@router.post`. **Rule**:
zero scientific reasoning in routes — only serialize what an engine already
computed (spec 9.29). Add the new path to the phase-9 dashboard test's
endpoint list so it's covered by the "returns 200" smoke test.

## Adding a validation rule
Extend `validation/engine.py::validate_mechanism`'s `checks` dict, or add a
new sub-validator under `validation/`. **Never weaken an existing threshold
to make more things pass** — this project's evidence discipline treats that
as the single worst anti-pattern. If validation blocks too much, the fix is
better features/evidence/reasoning, or a *justified* threshold change backed
by real measurement (see the `match_threshold` fix this session for the
correct pattern: two independent code paths had silently diverged, and
aligning them was a bug fix, not a weakening).

## Adding a database table
Add a `Migration(name, domain, (sql_statements,))` in the owning package's
schema file; register it via `databases.register_schema(...)` in that
store's `__init__`. Always additive (`CREATE ... IF NOT EXISTS`).

## Adding an environmental extraction mode
See `environment/signal_discovery/extractor.py`'s `_compute_changes` —
each mode is a block that calls `register(direction, magnitude, at, mode_name)`;
multiple modes can agree on the same `signal_key` and are unioned. This
session added `trend` and `level` alongside the original `spike`; the
directive that requested this also asked for regime/cyclic/momentum/
correlation/divergence/persistence/composite — **not yet implemented**, real
scope for the next session (see 10_NEXT_STEPS.md).

## Coding standards observed in this codebase
- Real-data-only constitution — never fabricate an observation, even for tests.
- Tests use `isolated_config`/temp data dirs — never touch real production data.
- Every performance or bias claim in this codebase is backed by a direct
  measurement (see commit messages this session for the pattern: hypothesis
  → measure → confirm/reject → implement → re-measure).
- Commits end with a `Co-Authored-By` trailer.
- Comments explain *why*, especially for non-obvious fixes (e.g. the UTC
  date-cast comment in `extractor.py`, the level-detection self-reference
  bug comment) — not *what* the code does.

## Testing standards
`unset ELECOUS_DATA_DIR` before any test run. Network-touching tests are
`@pytest.mark.network` and excluded by default. Run the full non-network
suite after every change: `.venv/bin/python -m pytest elecous/tests -m "not network" -q`.
