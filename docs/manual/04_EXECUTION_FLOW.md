# 04 — Execution Flow

**Scope**: chronological startup → steady state. **Read to complete**:
`elecous/scripts/cli.py::cmd_run`, `elecous/dashboard/api/app.py::main`,
`elecous/bootstrap.py::build_stack`, `elecous/scheduler/scheduler.py::start/run_pending`,
`elecous/scheduler/registration.py`.

Verified anchors:
- Entry: `elecous run` (cli) or `python -m elecous.dashboard.api.app` — both
  call `build_stack()`, `register_all_schedules(stack)`, `stack.scheduler.start()`.
- `build_stack` constructs engines in the dependency order (doc 03), each
  registering its DuckDB migrations on first touch.
- `Scheduler.start()` spawns one daemon thread ticking every
  `scheduler.tick_seconds` (default 5s), dispatching due tasks to a
  ThreadPoolExecutor (`scheduler.max_workers`, default 4) with per-subsystem
  serialization.
- All 38 tasks have `next_run` timestamps; existing candidates are already due,
  so the validation/prediction loops begin on the first ticks.
