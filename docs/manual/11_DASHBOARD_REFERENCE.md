# 11 — Dashboard Reference

**Scope**: frontend architecture, API, refresh model, views, UX. **Read to
complete**: `elecous/dashboard/frontend/index.html` (single file, no framework),
`elecous/dashboard/api/app.py`, `elecous/dashboard/api/routes.py`.

Verified anchors:
- Single-file vanilla-JS frontend; CSS-variable theming (cinematic dark +
  muted gold). 15 views in the `VIEWS` array; `renderers` map view→async fn.
- Refresh: `setInterval(()=>render(current,true),15000)` — silent, no flicker,
  no full reload. Each render calls the API and rebuilds one view.
- Human translation layer: `humanTask()`, `humanSignal()` map internal names to
  plain English with raw name in tooltip.
- API app starts the scheduler (doc 14), so the dashboard is a live instance.
- Recommended next UX work: per-event drill-down panels, replay UI over the
  existing `/predict/replay` endpoint, knowledge-graph explorer.
