# 12 — API Reference

**Scope**: every endpoint — purpose, inputs, outputs, consumer. **Read to
complete**: `elecous/dashboard/api/routes.py` (all `@router.get`/`@router.post`).

Verified: 38 routes. Generate the full table with
`grep -n "@router" elecous/dashboard/api/routes.py`. Notable non-obvious ones
added recently: `/activity` (minute/hour/day/week deltas + running/next tasks),
`/predictions/accuracy` (outcome ledger), `/validations/benchmark`,
`/discovery`, `/concepts`, `/experiments`, `/products`,
`/products/{product_id}`, enriched `/timeline` with an `events` stream, and
expanded `/search` with kind/category/region/status/min-confidence filters.
All read-only except `/expansion/run`, `/health/self-healing/run`, notification
acknowledge.

Routes perform zero scientific reasoning (spec 9.29) — they serialize or
compose what the engines already produced. Product ranking is dashboard
composition over monitored entities, active opportunities, predictions,
episodes and mechanisms; it does not create new mechanisms or predictions.
