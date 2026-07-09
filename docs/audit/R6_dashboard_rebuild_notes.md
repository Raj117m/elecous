# R6 — Dashboard rebuild notes (2026-07-08)

Built by a background agent against the spec in this session; the agent hit
the API session limit after finishing implementation + tests but before its
final verification run and this notes file — both completed and verified by
the main session. **126/126 non-network tests pass** (118 before rebuild).

## What shipped

- **New `/api/home`**: attention list (what deserves attention + why),
  do_next actions, verdicts block (judged count / accuracy / first-verdict
  due date — all real, `null`/0 at zero state), benchmark block with honest
  tone: the current reality renders as *"Higher recall, LOWER precision
  than the naive baseline"* (`tone: mixed`) — a green "outperforms" is only
  possible when both precision AND recall beat the baseline (routes.py
  `_benchmark_summary`).
- **`/api/feed`**: live intelligence events derived at request time from
  real versioned rows (opportunity version deltas with probability/stage
  changes, prediction evolutions/resolutions incl. failure attribution,
  mechanism failures, validations, new sources/factors) — each with a WHY
  sentence. No new state, no fabricated activity.
- **`/api/coverage`**: derived from the live source registry capabilities
  (replaces the hardcoded "honest coverage" text the audit flagged).
- **`/api/timeline`** with `since`/`until`/`limit` pagination (the old
  route silently capped at 50 of 2,382 episodes) — continuous past →
  present → +14d future; episodes join to resolved predictions for
  did-we-predict-it badges as verdicts land.
- **`/api/products` ranking + product pages**: every monitored product has
  a position; sorts backed by real data only; per-sort explanations.
- **Faceted `/api/search`** (kind/category/region/status/min_confidence).
- **Frontend rewritten** (single self-contained index.html, no external
  requests): 10-second home answers, sliding feed (15s polling with
  client-side diffing, no reloads), timeline backbone with infinite
  scroll, Developer mode toggle (localStorage) gating ALL engineering
  surfaces.
- **Jargon gate enforced by test** (`test_frontend_jargon_gate`): raw IDs
  (fac_/mech_/opp_) and implementation vocabulary never render outside
  the developer-mode block.

## Tests added (all passing)

zero-state honesty of new home payload; timeline pagination; feed
derivation from two inserted opportunity versions (asserts a delta event
with a WHY string); product routes 200 + 404s; jargon gate on shipped HTML;
all pre-existing dashboard tests kept green (payload-shape assertions
updated only where the new payload is the intentional replacement).

## Honest limitations

- **Not yet visually verified in a browser** — the live port is still held
  by the Antigravity-managed process running old code from `~/ELECOUS
  copy`; TestClient coverage only. Visual pass required after the
  production flip.
- Similar-products/related-products sections render "not enough evidence
  yet" until real cross-product force data accumulates (by design — never
  fake).
- Accuracy/verdict surfaces show real zeros until first outcomes land
  (2026-07-14+).
