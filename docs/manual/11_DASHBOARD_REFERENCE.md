# 11 — Dashboard Reference

**Scope**: frontend architecture, API, refresh model, views, UX. **Read to
complete**: `elecous/dashboard/frontend/index.html` (single file, no framework),
`elecous/dashboard/api/app.py`, `elecous/dashboard/api/routes.py`.

Verified anchors:
- Single-file vanilla-JS frontend; no framework or build step. The first-level
  views are now user-facing: Today, Timeline, Products, Search, Opportunities,
  Predictions, Validation, Environment, Knowledge, Operations.
- Refresh: `setInterval(()=>render(current,true),30000)` — silent, no flicker,
  no full reload. Each render calls the API and rebuilds one view.
- Global search is visible in the header and calls `/api/search` across
  products, opportunities, relationships, factors and historical spikes.
- Product intelligence uses `/api/products` and `/api/products/{product_id}`:
  every monitored entity gets a rank, demand-pressure summary, confidence,
  historical spikes, predictions and category relationships. This is a
  read-only composition of existing opportunity/prediction/episode/mechanism
  rows, not a new scientific scoring engine.
- Timeline uses `/api/timeline.events` to put historical spikes, present world
  changes, future opportunities, predictions and validations into one
  continuous event stream.
- API app starts the scheduler (doc 14), so the dashboard is a live instance.
- Recommended next UX work: deeper per-prediction "why" audit panels over
  `/api/audit/{prediction_id}`, replay UI over `/predict/replay`, and a
  knowledge-graph explorer.
