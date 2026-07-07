# 10 — Next Steps (brutally honest roadmap)

*Prioritized by measured impact per this session's evidence.*

## Highest priority

### 1. Run the full historical pipeline rewalk on the 2017+ corroborated history
**Reason**: this session deep-backfilled GDELT to 2017 and fixed multi-mode
extraction, but did NOT re-run demand-scan → research-cycle → mechanism
generation → validation over the full deepened history (would take 1+ hours;
not run due to context constraints this turn).
**Dependencies**: none — `scripts/rerun_pipeline.py` already does this.
**Difficulty**: low (script exists), but **time-expensive** — must run
unattended in the background and be checked, not babysat synchronously.
**Expected impact**: high — likely surfaces many more validated mechanisms
now that trend/level extraction is live, and gives real (not projected)
before/after category-diversity numbers.
**Blocker**: none. Just needs someone with a session budget to run and wait.

### 2. Get economics/energy actually producing signals
**Reason**: measured at exactly 0% contribution; root cause confirmed
(suspended EIA/FRED credentials + World Bank's real annual/quarterly cadence).
**Dependencies**: user must obtain and configure `eia_api_key` / `fred_api_key`
(see `configs/sources.yaml`, `keyed.py`). This is NOT something an agent can
do — it needs the user's own API registration.
**Difficulty**: low once keys exist.
**Expected impact**: medium — activates 2 more real categories, but their
natural update cadence (daily for EIA, monthly for FRED) means they'll still
contribute far less volume than weather/media.

### 3. Add one genuinely new high-value category (search trends or social volume)
**Reason**: this is what actually breaks the weather/media duopoly, not
threshold tuning.
**Dependencies**: pick a real, legally accessible API (Google Trends has no
official free API — evaluate `pytrends` unofficial library legality/ToS
carefully, or a paid API; alternatives: a real social-volume API).
**Difficulty**: medium — needs a new `Connector` subclass + semantics mapping.
**Expected impact**: high if it's genuinely anomaly-rich or trend-rich in the
new multi-mode extractor.

## Medium priority

### 4. Complete the remaining extraction modes from the V4 directive
Regime, cyclic, momentum, correlation, divergence, persistence, composite —
7 of 10 requested modes not implemented (spike, trend, level are done).
**Reason for deferral**: each needs real design (e.g. regime detection needs
either a change-point algorithm or explicit state labels; correlation/
divergence need cross-phenomenon joint analysis, a different data shape than
the current per-phenomenon loop). Implementing shallow/fake versions of these
would produce a worse audit trail than not having them — this session
prioritized 2 solid, measured, bug-fixed modes over 7 stubs.
**Difficulty**: medium-high per mode.
**Expected impact**: uncertain until real data is examined — don't build
blind; use the same measure-first discipline this session followed for
trend/level.

### 5. Dashboard: full decision-first redesign
Landed this session: decision-first opportunity cards, coverage-honesty note,
human-language task/signal names, cinematic palette, live pulse bar.
Continuation landed: a user-facing Today view, unified intelligence timeline,
global search, continuous product ranking and read-only product intelligence
pages composed from existing opportunity/prediction/episode/mechanism rows.
**Not done**: deep per-prediction "Why?" panels over `/api/audit/{prediction_id}`,
factor encyclopedia pages, replay UI over `/api/predict/replay`, interactive
knowledge graph and true product-lifecycle/adaptive-attention modeling.
**Difficulty**: high (real multi-day frontend design work).
**Expected impact**: high for user trust/usability; zero for prediction
quality (pure presentation layer).

### 6. Autonomous Backlog Manager
Generalize `research/priority.py`'s value-ranked queue (built for research
questions) to operational improvement tasks, so the scheduler pulls the
highest-value backlog item when idle instead of appearing idle.
**Critical constraint carried from prior session**: every backlog item MUST
trace to a real published signal (a real skeptic failure, a real health
degradation, a real storage-growth measurement) — never fabricate work to
look busy.
**Difficulty**: medium.
**Expected impact**: medium (operational polish, not prediction quality).

## Long-term / scaling

### 7. Adaptive attention / product lifecycle architecture
**Measured trigger, not yet reached**: current per-entity scan cost is
5.9ms/entity (measured); at ~300 entities that's under 2 seconds. This only
becomes worth building at ~10K+ entities. Do not build ahead of the evidence.

### 8. Storage compaction for append-only mechanism/factor versions
Real technical debt; `environment.duckdb` is the fastest-growing domain.
No urgency at 5.6GB total against a 150-200GB budget, but should be designed
before it becomes urgent.

## What requires external data, not more engineering (do not attempt to code around these)

- Broader environmental categories (see 05_DATA_REFERENCE.md's "absent" list).
- Resolved prediction outcomes — need real calendar time, not code.
- Economics/energy density — needs API keys the agent cannot self-provision.
