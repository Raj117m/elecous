# 15 — Developer Guide

**Scope**: how to add each extension point + standards. **Read to complete**:
the existing example of each, then mirror it.

Verified patterns:
- **Connector**: subclass `observation/connectors/base.py::Connector`, set
  `parser_id`, implement `collect()`; register in `configs/sources.yaml`. Or add
  a source whose format `AutoConnector` already handles (json/csv/rss/atom).
- **Scheduled task**: add a `scheduler.register(...)` line in
  `scheduler/registration.py`.
- **API endpoint**: add a `@router.get` in `dashboard/api/routes.py` (read-only;
  no reasoning) + add path to the phase-9 dashboard test.
- **Dashboard view**: add to `VIEWS` and `renderers` in `frontend/index.html`.
- **DB table**: add a `Migration` in the domain's schema file; register it in
  the store's `__init__`.
- **Validation/mechanism/prediction strategy**: extend the relevant `engine.py`;
  never weaken a check to pass more — fix evidence/features/reasoning (project law).

Standards (observed): real-data-only constitution; append-only versioning;
every claim measured before/after; tests use `isolated_config`; commits end
with the Co-Authored-By trailer.
