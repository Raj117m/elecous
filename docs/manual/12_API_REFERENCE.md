# 12 — API Reference

**Scope**: every endpoint — purpose, inputs, outputs, consumer. **Read to
complete**: `elecous/dashboard/api/routes.py` (all `@router.get`/`@router.post`).

Verified: 32 routes. Generate the full table with
`grep -n "@router" elecous/dashboard/api/routes.py`. Notable non-obvious ones
added recently: `/activity` (minute/hour/day/week deltas + running/next tasks),
`/predictions/accuracy` (outcome ledger), `/validations/benchmark`,
`/discovery`, `/concepts`, `/experiments`. All read-only except
`/expansion/run`, `/health/self-healing/run`, notification acknowledge.
Routes perform zero scientific reasoning (spec 9.29) — they serialize what the
engines already produced.
