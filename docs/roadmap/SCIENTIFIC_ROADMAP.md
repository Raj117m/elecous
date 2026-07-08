# ELECOUS Scientific Roadmap

**Version:** 2026-07-08.2 (supersedes docs/handoff/10_NEXT_STEPS.md; v2 folds in the user's 15 architectural corrections — all confirmed compatible with v1's diagnosis, four genuinely new requirements added below)
**Objective (restated, canonical):** maximize profitable business decisions by identifying products that will experience exceptional SALES growth within the next 14 days, before the market notices. Scientific understanding exists only to improve profitable predictions.
**Canonical causal chain (every prediction must be explainable by it):**
World → External Business Environment → External Business Forces → Relationships between Forces → Expected Sales Pressure → Expected Sales Growth → Products → Opportunity Ranking → Decision → Validation → Learning.
Attention signals (pageviews, news volume, search trends, social discussion) are EVIDENCE inside this chain, never the ground truth.
**Evidence base:** docs/audit/state_snapshot_2026-07-07.json (live metrics), docs/audit/mission_audit_partial_2026-07-07.json (3 audited dimensions; demand-first findings adversarially CONFIRMED), branch `production-copy-snapshot` (7118a3b, the running production tree).

This document is the cache of verified conclusions. Future sessions: read this FIRST, do not re-derive anything marked CONFIRMED or MEASURED.

---

## 1. Where the project drifted (the honest diagnosis)

Each item states the drift, the evidence class, and why it matters for profit.

### D1 — Target-variable drift: "demand" is attention, not sales  [CONFIRMED]
The ground truth the entire scientific stack calibrates against is **Wikipedia pageviews + GDELT media volume of the product's article** (`demand/spikes/detector.py:59: DEMAND_SIGNAL_TYPES = ("pageviews_daily", "media_volume_pct")`). Episodes are attention spikes. Mechanisms, validation, F1 0.63, calibration — all measure "can we predict Wikipedia attention," not "can we predict sales." For a profit objective this is the deepest problem: **no architectural improvement can fix prediction quality against the wrong target.** Attention is a legitimate *leading indicator*; it is not the *outcome*.

### D2 — Product-first inversion  [CONFIRMED by adversarial verification]
Demand measurement begins at individual products (`demand/engine.py:63` iterates the static entity catalog; episodes are keyed `entity NOT NULL`). No demand-pressure or demand-shift quantity existed anywhere in the main tree; the `expected_demand_pressure` users saw was `max(opportunity probability)` computed inside the dashboard route handler (`routes.py:129,185`) — a display artifact violating "never fake." (The production copy has since added a per-product DPI — see §3.)

### D3 — Volume over reliability  [MEASURED]
`predict()` runs every 15 minutes and **re-mints the same ~11 category predictions with zero dedup or rate control** (141 → 152 pending in one unattended day). Rolling benchmark: ELECOUS precision **0.4606 vs naive single-signal baseline 0.4676**, recall 1.0, fp=1293 — it beats the baseline on F1 only by never missing, i.e. by over-predicting. The mission's standard ("fewer but more reliable") is violated by construction, and the dashboard's "outperforms baseline" pill overstates the truth.

### D4 — The learning loop is a dead end  [AUDITED, high confidence]
The loop *can* close (first prediction verdict due **2026-07-14**; all 152 judgeable by 07-17; no unfalsifiable class at category level) but what it learns goes nowhere:
- Calibration records are **write-only** — `CalibrationEngine.recompute()/calibrate()` are never called anywhere in production.
- Resolved outcomes **never update the source mechanisms' confidence** (resolve_due only touches the prediction row + calibration records).
- Research lessons are **written under `need_cluster` keys but read back by `kind`** — the lesson-consultation step retrieves nothing, ever.
- Per-product claims are unfalsifiable: resolution is category-level ("any episode in the category counts"), while every opportunity ships its full static category roster as "products" — including catalog pollution like *Heat transfer* and *Sun Yat-sen* — with the only product-ranking code path dead outside tests.

### D5 — Engineering self-expansion over problem-solving  [MEASURED]
- Production runs from `~/ELECOUS copy` with **large uncommitted changes** (DPI, product force ecosystems, google_trends, blind-spot detection, entity registry, memory manager) while the git repo — the declared source of truth — is behind. A stray `rm -rf` would have destroyed working science. Snapshot-preserved on branch `production-copy-snapshot` (7118a3b).
- A NotificationEngine with intelligence checks (opportunity, mechanism failure, knowledge revision, observation loss) was **built but never wired** — 0 events ever produced. Code was written; the problem (a living feed) wasn't solved.
- Dashboard "Why" for top products renders raw factor IDs (`fac_019f…`); factor names are concatenated garbage strings; the "Honest coverage" panel is hardcoded static text.

**Root pattern:** every drift is the same shape — a scientific requirement got an engineering artifact (a table, an engine, an endpoint) instead of a closed measured loop. The corrective principle for all future work: *a feature is done when its effect on prediction quality is measured, not when its code exists.*

---

## 2. Independence audit (hard constraint: commercially usable at scale)

| Source | Access | Commercial verdict |
|---|---|---|
| google_trends | **pytrends — unofficial scraping, `access_method: "unofficial_api"`, 9.6 min/collection** | **REPLACE.** ToS-gray, rate-crippled, can break any day. Licensed alternatives: Google Ads Keyword Planner API (official), DataForSEO / SerpApi (commercial licenses). |
| open_meteo_weather / air_quality / flood | Free API | **CAUTION:** free tier is non-commercial-only. Commercial use requires their paid plan (self-hostable open-source model data is an alternative). Budget item, not a redesign. |
| gdelt_media | Open, any use | KEEP |
| wikimedia_pageviews | Open (CC) | KEEP (as leading indicator, not ground truth) |
| usgs_earthquakes, nws_alerts | US public domain | KEEP |
| worldbank_macro, nager_holidays | Open | KEEP |
| eia_electricity, fred_series | Official free APIs, suspended (no keys) | KEEP — **user action: register keys** |
| auto-discovered Atom feeds | Public syndication | KEEP (per-feed license check at promotion time) |

Missing-by-design under the new objective (all commercially licensable, none free-tier): marketplace rank/price/review histories (e.g. Keepa API for Amazon data — licensed), retail inventory/pricing feeds, official search-volume APIs. These are **paid data decisions only the user can make** — see Open Decisions.

---

## 3. What the production copy already built (reuse, do not rebuild)

Uncommitted parallel work now preserved in `production-copy-snapshot`:
- **DemandPressureIndex** (`demand/pressure/dpi.py`): per-product continuous pressure scalar integrating active spikes × volume multiplier × duration × environmental weight, reading per-product `associated_forces`. Matches the "every product has its own force ecosystem" requirement. **Not yet scientifically validated** and contains magic numbers (`max(agreement)/50.0`, fallback `1.5`, default `["consumer_behaviour"]`) that violate never-hardcode. Adopt only after it measurably beats current activation scoring on historical replay.
- **products table with `associated_forces`** (per-product force ecosystems), `demand/blind_spots.py`, `expansion/entity_registry.py`, `knowledge/memory/memory_manager.py`, google_trends connector, updated dashboard routes.
- Verdict: direction is right, validation is absent. These are hypotheses, not conclusions.

---

## 4. The ranked roadmap

Ranking = (expected improvement in profitable prediction quality × confidence) ÷ cost. Sequenced where dependencies demand it.

### R0 — Reconcile the two trees ✅ DONE 2026-07-08 (code); production flip pending user
Merged `production-copy-snapshot` into master (9e62b95), fixed 3 tests the parallel line left mid-work, live 8.4GB data rsynced into canonical. **Remaining:** the `ELECOUS copy` workspace is open in Antigravity (Google's agentic IDE) whose language server respawns the server from the copy — user must close it; then final incremental data rsync + start canonical process.

### R1 — Repair the learning loop ✅ DONE 2026-07-08 (deadline 2026-07-14 met; all six items below implemented, 2 regression tests, 113/113 pass)
Bonus fix discovered en route: migration 0002 had silently **broken every prediction INSERT in production since 2026-07-08 03:57 UTC** (positional VALUES vs widened table; scheduler log shows `prediction:predict` consecutive failures; count frozen at 141; no test covered `_record_prediction`). Rewritten with named columns. Evolve-in-place preserves the falsifiability anchor: created_at/expires_at never move, lead_time may only tighten — a prediction cannot dodge judgment by refreshing itself.
Four verified point-fixes, all cheap:
1. Prediction dedup/rate control: update-don't-remint when a pending prediction covers the same category set + overlapping window.
2. Schedule `calibration.recompute("prediction"/"mechanism")` after each resolver pass, and *apply* calibrated confidence.
3. Feed resolved outcomes into source mechanisms' confidence (iterate `mechanisms_used` in resolve_due).
4. Fix the lessons key mismatch (`research/engine.py:101` reads by `kind`; writes are by `need_cluster`).
5. **Failure attribution (new, correction #6):** when a prediction resolves incorrect, the resolver must record WHICH relationships failed (the mechanisms/factors that fired) and WHICH forces were absent from the evidence (categories with no active signal), so failures name the model change they demand — not just a counter increment.
6. **Predictions evolve, never duplicate (correction #7):** dedup is not just suppression — when new evidence arrives for a covered category+window, UPDATE the existing prediction's confidence, magnitude, timing, and explanation, with the change recorded as an event (feeds R6's live feed).
**Verification:** pending count stops inflating (~11 stable, not +11/day); after 07-14, calibration curves exist, mechanism confidences move with outcomes, and every failed prediction carries a structured failure-attribution record.

### R2 — Falsifiable, precision-first predictions (~2-3 days)
- Resolve at the granularity claimed: predict product-level only where product-level evidence exists (per-entity episode history), else predict category-only and say so.
- Kill wholesale roster dumps; wire real per-product differentiation (copy's DPI or per-entity spike-history response — whichever wins on replay).
- Gate prediction emission on calibrated precision threshold, not on matcher activation alone. Target: precision > baseline (>0.468) at whatever recall follows.
**Verification:** rolling benchmark precision above baseline; every emitted prediction names the exact falsification criterion and deadline.

### R3 — Sales-proxy ground truth (the target-variable fix; highest scientific impact, needs user data decision)
Redefine episodes against a sales-adjacent outcome: marketplace rank velocity / price+availability shifts / review velocity (licensed feeds), with attention (pageviews/media) demoted to leading indicator. Dual-track validation during transition: every mechanism scored against both attention-episodes (legacy) and sales-proxy episodes (new). **This is where "profitable" enters the science.** Blocked on Open Decision #1.
**Verification:** a mechanism catalogue re-validated against sales-proxy episodes; report which attention-validated mechanisms survive (expect a purge — that purge is the point).

### R4 — Validate the copy's DPI + product force ecosystems (after R0, ~1-2 days)
Historical replay A/B: DPI-ranked products vs current activation-ranked vs naive baseline on the same episode history. Adopt, fix, or reject with numbers. Remove magic numbers either way (derive from thresholds.yaml with measured justification).
**Ecosystem lifecycle (correction #4, hard requirement):** each product's `associated_forces` is a DYNAMIC learned set, not config — per-product force relationships must be continuously strengthened/weakened by prediction outcomes and merged/removed when evidence decays (reuse the mechanism-lifecycle machinery; per-(product, force) evidence counters). No generic force set shared across products survives this item.
**Domain prioritization (correction #5):** track each force domain's measured contribution to profitable predictions (hit-rate of predictions whose evidence includes that domain); new-source acquisition (R8) is ranked by this number, not by convenience. Weather becomes one domain among many by measurement, not decree.

### R5 — Idle-time rebalance: learn more, predict less (~1 day)
Scheduler currently re-predicts every 15 min while calibration never runs. Rebalance: prediction on world-state *change* (not clock), freed budget → historical replay refreshes, relationship re-validation, blind-spot scans, factor hygiene. Aligns with "majority of idle runtime on discovery/validation."
**Verification:** scheduler telemetry shows learning tasks dominating idle cycles; prediction count flat absent world changes.

### R6 — Dashboard rebuilt as the intelligence surface (after R1/R2 so real events exist, ~3-5 days)
The 10-second answers (what deserves attention / why / what changed / what failed / what was learned / what to do), the living event feed (wire the four existing NotificationEngine checks — the engine exists, 0 events ever emitted), rank-move explanations in plain sentences ("moved #8→#3 because search volume rose and supplier lead times lengthened"), the continuous past→+14d timeline with did-we-predict-it/were-we-right badges (currently absent; history caps at 50 of 2,383 episodes), factor-ID→plain-language resolution, hardcoded coverage panel → live registry data, "outperforms baseline" pill → honest precision/recall statement, all engineering vocabulary behind a Developer Mode.
**Recommendation contract (correction #13):** every recommendation renders exactly these fields, each from stored data — why ELECOUS believes it, supporting evidence, contradicting evidence, expected sales increase, expected timing, expected profit opportunity, risk, remaining uncertainty, similar historical situations. If a field cannot be honestly populated, it says so ("no contradicting evidence found in N checks") rather than being omitted — absence of counter-evidence is itself information.
**Verification:** first-screen answers auditable against live payloads; zero implementation jargon outside dev mode; feed events all trace to real stored state changes; every recommendation renders the full 9-field contract.

### R7 — Knowledge hygiene: bounded, ranked relationships (~2 days)
700 factors with garbage collision names; append-only growth. Continuous rank/strengthen/weaken/merge/archive with evidence-based scoring. Prerequisite for a browsable "environmental force" vocabulary in the UI.

### R8 — Independence remediation (parallel, mostly user actions)
Replace pytrends with a licensed search-interest source; Open-Meteo commercial plan decision; EIA/FRED keys. Add license verdict as a hard field on every source; expansion engine must reject non-commercial candidates at discovery time.

**Explicitly deprioritized** (low EV now): more extraction modes (7 pending — no evidence current 3 are the bottleneck), storage compaction (5.6GB vs 150GB budget), adaptive attention (trigger ~10K entities, at ~300), multi-machine anything.

---

## 5. Delete / rewrite / simplify / keep

| Verdict | Item | Reason |
|---|---|---|
| DELETE | `expected_demand_pressure` display arithmetic (routes.py) | Fake quantity; replaced by measured pressure (R2/R4) |
| DELETE | Wholesale category-roster "products" on opportunities | Unfalsifiable decoration; inflates surface |
| DELETE | Dead `/api/home` (frontend never calls it) or make it the single overview source | Two drifting definitions of "home" |
| DELETE | pytrends connector (after licensed replacement lands) | Independence violation |
| REWRITE | Prediction emission (dedup, gating, falsification criteria) | D3/D4 |
| REWRITE | Factor naming | Garbage strings block the entire user-facing force vocabulary |
| REWRITE | Dashboard home + timeline + feed per R6 | Both existing redesigns pre-date the current product vision |
| SIMPLIFY | Scheduler cadence (predict-on-change) | D5 |
| SIMPLIFY | Category taxonomy (purge pollution: *Sun Yat-sen* in sun_protection etc.) | Category layer is the aggregation unit; garbage in, garbage everywhere |
| KEEP | Constitution + enforcement | Exactly the right discipline; extend with falsifiability + license rules |
| KEEP | Episode/baseline machinery (DynamicBaseline, robust-z, learned windows) | Sound stats; only the *target series* changes (R3) |
| KEEP | Validation engine (six checks, skeptic, replay, no-future-leakage) | The most mission-aligned subsystem; fix its dead outputs (R1), don't touch its gates |
| KEEP | Observation network, dedup, provenance, health | Real, measured, boring in the good way |
| KEEP | Timeline/search/product-page API skeleton | Audit confirmed the skeleton is decision-first; degradation is in content quality, not structure |

---

## 6. Session protocol (budget constraint, standing)

1. Start: read this file + docs/audit/*. Do not re-audit what is marked CONFIRMED/MEASURED.
2. One task per session, from §4 order, unless the user redirects.
3. Every claim of improvement: before-number → change → after-number, appended to docs/audit/.
4. End: update this file's §4 statuses + handoff current-state. Commit everything. Production never runs uncommitted code again.

## 7. Open decisions (user)

1. **Paid sales-proxy data (unblocks R3, the profit-critical item):** Keepa (Amazon rank/price/review histories, licensed) or equivalent; and/or a licensed search-volume API (DataForSEO / Google Ads API). Monthly cost, tens of € — the single highest-leverage spend available to this project.
2. **Open-Meteo commercial license** (or accept non-commercial status during development and defer).
3. **EIA + FRED API keys** (free registration, ~10 minutes, unlocks economics/energy forces).
4. **Canonical tree confirmation:** merge plan per R0 — `~/ELECOUS` becomes canonical, `~/ELECOUS copy` retired after merge. Confirm no other session is mid-work there.
