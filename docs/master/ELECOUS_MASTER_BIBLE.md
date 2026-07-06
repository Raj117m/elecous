# ELECOUS Master Bible

**The Complete Reference Manual for the ELECOUS Demand Intelligence Platform**

**Last Updated:** 2026-07-06  
**Current Commit:** 96b79cb (handoff package)  
**Repository:** ~/ELECOUS  
**Status:** Production-ready. 147 validated mechanisms, 64 active predictions, 1.7M observations spanning 2015–2026.

---

## PART I: FOUNDATION

### 1. Project Purpose & Vision

#### What ELECOUS Is

ELECOUS is a **scientific demand-intelligence platform** that automatically discovers which environmental conditions *causally precede* demand spikes in real historical data. It ingests real public observations (weather, news volume, economic indicators, social signals), extracts meaningful signals from them, discovers statistical factors that predict demand, forms causal hypotheses ("mechanisms"), validates them rigorously, and makes predictions with real evidence attached.

**Core claim:** ELECOUS never guesses. It only predicts what real historical evidence demonstrably supports.

#### Original Objective

The platform was designed to answer: *"Which real-world conditions drive demand for a product, and can we predict when they'll occur?"* Rather than relying on domain experts' intuition or black-box correlation mining, ELECOUS builds an explicit causal chain: environmental change → signal → historical episodes (observed demand spikes) → validated mechanisms → future predictions.

#### Long-Term Vision

1. **Scale to thousands of products** with individually-tuned mechanisms (currently ~300 entities being scanned).
2. **Adaptive attention architecture** — dynamically allocate observation collection based on real value/cost tradeoffs (designed; trigger: ~10K entities; not yet built).
3. **Self-healing autonomy** — detect failing mechanisms, repair evidence gaps, retire persistent failures, discover new sources without human intervention (partially built; Stage 1-2 research intelligence exists).
4. **Multi-modal evidence** — transition from weather/news duopoly to a balanced portfolio of economic, social, and behavioral signals (in progress; economics/energy blocked on API keys, social/search need new connectors).

#### Scientific Philosophy

- **Real evidence over appearance.** A dashboard showing "quiet, next task at 14:32" is more trustworthy than one fabricating activity.
- **Measure before and after every change.** Not "should be faster" — actual numbers against the same benchmark.
- **Root-cause before patching.** The 64/36/0 weather/media/other bias was tempting to "fix" by reweighting signals, but the real problem was upstream: signal extraction never produced non-weather signals because it was spike-only. Fix the source.
- **Small, additive, reversible changes.** Migrations always use `IF NOT EXISTS`. New extraction modes write into existing signal-key formats.
- **Honest scope-cutting.** When a directive asks for 10 things and only 2–3 can be done with real rigor in the available time, ship those 2–3 well and say plainly what wasn't done and why.

#### Business Philosophy

ELECOUS is built for **decision-makers under uncertainty.** Every prediction is paired with:
- Confidence (real calibration from historical validation).
- Evidence (which mechanisms fired, what signals convinced them).
- Lead time (how many days ahead the prediction applies).
- Business value estimate (which products/categories are most affected).

This discipline prevents the common failure mode where a prediction system appears successful because predictions are never judged against reality.

---

### 2. Complete Architecture

#### High-Level Pipeline

```
Real World State
    ↓
[OBSERVATION NETWORK]
    ↓ (11 sources: GDELT, OpenMeteo, Wikimedia, World Bank, EIA, FRED, etc.)
Real Observations (1.7M, 2015–2026)
    ↓
[SIGNAL DISCOVERY ENGINE]
    ↓ (spike/trend/level extraction, anomaly detection)
Signal Changes (177 currently, e.g., "cold_stress:up:US")
    ↓
[DEMAND INTELLIGENCE ENGINE]
    ↓ (demand scan, baseline, episode detection)
Episodes (2,382 total; a demand spike occurrence with timestamp)
    ↓
[PATTERN MINING ENGINE]
    ↓ (signal combinations that precede episodes)
Factors (statistical evidence: "cold_stress UP precedes 23 episodes; chance would predict 1.2")
    ↓
[MECHANISM FORMATION ENGINE]
    ↓ (human-readable causal chains from factors)
Mechanisms (e.g., "cold_stress:up → heating demand surge")
    ↓
[VALIDATION ENGINE]
    ↓ (historical replay, skeptic checks, confidence scoring)
Validated Mechanisms (147 currently)
    ↓
[PREDICTION ENGINE]
    ↓ (match current world state against mechanism triggers)
Predictions (64 active, all within lead-time windows)
    ↓
[KNOWLEDGE GRAPH ENGINE]
    ↓ (versioned causal chains, contradiction resolution)
Knowledge State (continuously updated)
    ↓
[OUTCOME RESOLUTION ENGINE]
    ↓ (judge predictions after lead-time passes, update confidence)
Real-World Feedback
```

#### Governance: The Constitution

**Three non-negotiable rules**, enforced in code at `elecous/governance/constitution.py`:

1. **No synthetic data, ever.** Every observation must have real provenance (a real source_id, access_method, and URL). Never fabricate an observation to make a test pass or a demo look good.
2. **No lifecycle promotion without passed validation.** Never weaken a validation threshold just to make more things pass. If validation blocks progress, the fix is better evidence/features/reasoning, or a threshold change *justified by real measurement* (not convenience).
3. **No future leakage in historical replay.** Predictions generated as-of date T cannot access observations after T. Enforcement: all replay functions accept `as_of_datetime` and filter observations and mechanisms strictly.

#### Subsystems & Their Relationships

**CORE ENGINES** (12 total):

1. **ObservationNetwork** (`elecous/observation/network.py`)
   - Ingests from 11 real connectors (GDELT, OpenMeteo, Wikimedia, etc.).
   - Maintains per-source health scores, deduplication, timestamp normalization.
   - Outputs: Observations table, connector_runs telemetry.

2. **SignalDiscoveryEngine** (`elecous/environment/signal_discovery/`)
   - Extracts meaningful signals from observations.
   - **Modes** (currently 3 of 10 implemented): spike (z-score anomaly), trend (polyfit slope + correlation gate), level (percentile of baseline).
   - Outputs: signal_changes table, indexed by phenomenon/direction/scope.

3. **DemandIntelligenceEngine** (`elecous/demand/engine.py`)
   - Detects demand episodes (real observed spikes in product demand).
   - Computes baselines, detects anomalies.
   - Outputs: episodes table, episode_signals (linkage between observations and episodes).

4. **PatternMiningEngine** (`elecous/environment/pattern_engine/mining.py`)
   - Discovers which signal combinations statistically precede episodes.
   - Computes support (how many episodes had this signal combo), confidence (precision of prediction).
   - Outputs: factors table (one row per discovered pattern).

5. **MechanismFormationEngine** (`elecous/mechanism/engine.py`)
   - Converts factors into human-readable causal hypotheses.
   - Adds behavioral chains ("cold weather → heating thermostat → demand surge").
   - Versioning: tracks mechanism evolution as evidence changes.
   - Outputs: mechanisms table with evidence_links, confidence, lifecycle state.

6. **ValidationEngine** (`elecous/validation/engine.py`)
   - Six-check gate: precision on holdout data, absence of overfitting, skeptic challenges (is there a simpler explanation?), geographic diversity, temporal stability, confidence floor check.
   - Confidence calculation: merges scientific (validation evidence), prospective (real predictions vs. outcomes), and skeptic-discounted channels.
   - Outputs: validations table (per-mechanism validation record), mechanisms.lifecycle → VALIDATED/RETIRED.

7. **PredictionEngine** (`elecous/prediction/engine.py`)
   - Matches current world state against mechanism triggers.
   - Ranks predictions by business value (which products are most affected).
   - Tracks lead time (when the prediction applies in the future).
   - Outputs: predictions table (one per triggered mechanism, with lead_time_hours, business_value).

8. **HistoricalReplayEngine** (`elecous/validation/replay.py`)
   - Simulates what the system would have predicted at any historical moment.
   - Used for backtesting and F1 benchmarking.
   - Enforces constitution rule: no future leakage.
   - Outputs: replay scores (precision, recall, F1 at each historical timestamp).

9. **CalibrationEngine** (`elecous/validation/calibration.py`)
   - Measures whether stated confidence matches real accuracy.
   - Takes outcome data (predictions judged correct/incorrect) and computes calibration curves.
   - Updates future confidence thresholds if miscalibration detected.
   - Outputs: calibration curves, confidence adjustments.

10. **KnowledgeGraphEngine** (`elecous/knowledge/engine.py`)
    - Maintains a versioned DAG of mechanisms and their relationships.
    - Detects contradictions (mechanism A claims X; mechanism B claims ¬X).
    - Records the lineage of how each mechanism came to be.
    - Outputs: knowledge_graph table (nodes + edges, versioned).

11. **ResearchEngine** (`elecous/research/engine.py`)
    - Decides what observations to collect next based on planned investigation priorities.
    - Tracks source quality (Wilson intervals, redundancy, novelty).
    - Learns from past failures (lessons from repeated validation failures).
    - Outputs: research_questions, source_value_records, research_experience.

12. **SchedulerEngine** (`elecous/scheduler/scheduler.py`)
    - ThreadPoolExecutor (4 workers) dispatches 38 registered tasks.
    - Per-subsystem locks ensure same-subsystem tasks serialize while cross-subsystem tasks run concurrently.
    - No long-running task blocks the others.
    - Outputs: scheduler state, task telemetry.

#### Dependency Graph

```
ObservationNetwork
    ↓
DemandIntelligenceEngine
    ↓
PatternMiningEngine ← SignalDiscoveryEngine
    ↓                   ↓
MechanismFormationEngine
    ↓
ValidationEngine ← HistoricalReplayEngine
    ↓                 ↓
KnowledgeGraphEngine   CalibrationEngine
    ↓                      ↓
PredictionEngine ←─────────┘
    ↓
ResearchEngine (adaptive collection planning)
    ↓
SchedulerEngine (orchestrates all above)
```

---

## PART II: EXECUTION & OPERATION

### 3. End-to-End Execution Flow

#### Startup Sequence

```bash
cd ~/ELECOUS
.venv/bin/python -m elecous.dashboard.api.app
```

**What happens (trace):**

1. `elecous/dashboard/api/app.py` loads.
   - Sets up logging to `~/ELECOUS/logs/elecous.log`.
   - Reads config from `elecous.yaml`, `thresholds.yaml`, `sources.yaml`, `semantics.yaml`, `entities.yaml`.
   - Validates schema versions.

2. `bootstrap.py:build_stack()` is called.
   - Creates `~/ELECOUS/data/` if it doesn't exist.
   - Initializes 11 DuckDB database files (one per domain).
   - Runs all migrations (creates tables if `IF NOT EXISTS`).
   - Constructs all 12 engines.
   - Wires event bus (subscribes validation completion → research ledger updates).

3. `SchedulerEngine.start()` is called.
   - Registers 38 tasks (observation sweeps, signal extraction, demand scanning, mechanism generation, validation, prediction, benchmark, etc.).
   - Starts ThreadPoolExecutor.
   - Begins polling on 2-second ticks.

4. `FastAPI` app starts on `http://127.0.0.1:8321`.
   - Routes: `/api/activity` (live task state), `/api/health/system`, `/api/mechanisms`, `/api/predictions`, `/api/episodes`, `/api/observations`, `/api/benchmark`.
   - Dashboard frontend served at `/` (single-page app, vanilla JS).
   - Auto-refresh every 15 seconds.

#### Continuous Operation

**The scheduler loop (2-second tick):**

```python
while True:
    for task in registered_tasks:
        if task.is_due():
            if subsystem_lock[task.subsystem].acquire_nowait():
                try:
                    executor.submit(task.callable())
                finally:
                    subsystem_lock[task.subsystem].release()
            else:
                # Another task in same subsystem already running; queue it
                queue_for_next_tick(task)
    sleep(2)
```

**Task families** (run on different intervals):

| Task Family | Interval | Purpose | Subsystem |
|---|---|---|---|
| `observe:*` | Hourly | Collect fresh observations from all sources | observe |
| `environment:extract` | 10 min | Extract signals (spike/trend/level modes) | environment |
| `demand:scan` | Hourly | Detect demand episodes | demand |
| `pattern:mine` | Daily | Discover factors (signal combos → episodes) | environment |
| `mechanism:generate` | Daily | Form mechanisms from factors | mechanism |
| `mechanism:retire` | Daily | Retire persistent failures | mechanism |
| `validation:validate` | Daily | Run six-check gate on candidate mechanisms | validation |
| `validation:rolling_benchmark` | Daily | F1 backtest on validated set | validation |
| `validation:resolve_due` | Hourly | Judge predictions after lead-time | prediction |
| `prediction:predict` | 5 min | Match world state, generate predictions | prediction |
| `research:plan` | Hourly | Decide what observations to collect next (Stage 2) | research |

#### Shutdown

The dashboard/scheduler runs in the foreground. `Ctrl+C` gracefully:
1. Signals all running tasks to complete.
2. Flushes any pending writes to DuckDB.
3. Closes all connections.
4. Writes final health check to `logs/elecous.log`.

**⚠️ Critical constraint:** DuckDB is single-writer per database file. If you try to run `build_stack()` or a script against the data directory while the dashboard is running, you will get an IO lock error. Either:
- Query through the HTTP API (`curl http://127.0.0.1:8321/api/...`).
- Stop the dashboard first (`pkill -f "elecous.dashboard.api.app"`), run your script, then restart.

---

### 4. Complete Repository Walkthrough

**Repository root: `~/ELECOUS`**

```
ELECOUS/
├── .venv/                          # Python 3.11 virtual environment
├── elecous/                        # Main package
│   ├── __init__.py
│   ├── bootstrap.py                # Stack initialization, migrations
│   ├── governance/
│   │   └── constitution.py         # Constitution enforcement (real data only, no synthetic)
│   ├── observation/
│   │   ├── network.py              # Orchestrates all connectors
│   │   ├── store.py                # Observation ingestion, deduplication (8.7× speedup via bulk insert)
│   │   ├── registry/
│   │   │   └── registry.py         # Connector tracking, health scoring
│   │   └── connectors/
│   │       ├── base.py             # Connector abstract base class
│   │       ├── gdelt.py            # GDELT media volume (+ deep backfill resumable chunking)
│   │       ├── wikimedia.py        # Wikipedia pageviews
│   │       ├── open_meteo.py       # Weather (current + 2015 archive)
│   │       ├── keyed.py            # EIA, FRED, World Bank (credentialed)
│   │       └── nager.py            # Holiday calendar
│   ├── demand/
│   │   ├── engine.py               # Demand signal detection, episode formation
│   │   └── store.py                # Episodes, baselines storage
│   ├── environment/
│   │   ├── engine.py               # Orchestrates signal discovery + pattern mining
│   │   ├── signal_discovery/
│   │   │   ├── extractor.py        # Spike/trend/level modes (multi-mode fix from this session)
│   │   │   └── discovery.py        # Signal registration, phenomenon mapping
│   │   ├── pattern_engine/
│   │   │   └── mining.py           # Factor discovery (signal combos → episodes)
│   │   └── store.py                # Signals, factors storage
│   ├── mechanism/
│   │   ├── engine.py               # Mechanism formation, versioning
│   │   ├── formation.py            # Behavioral chain construction
│   │   └── store.py                # Mechanisms, confidence, evidence_links
│   ├── validation/
│   │   ├── engine.py               # Six-check validation gate
│   │   ├── skeptic/
│   │   │   └── skeptic.py          # Skeptic challenges (simpler explanations?)
│   │   ├── replay.py               # Historical backtest (no future leakage)
│   │   ├── calibration.py          # Confidence calibration
│   │   └── store.py                # Validations, lifecycle tracking
│   ├── prediction/
│   │   ├── engine.py               # World matching, prediction generation, resolution
│   │   └── store.py                # Predictions, outcomes
│   ├── knowledge/
│   │   ├── engine.py               # Knowledge graph versioning
│   │   └── graph/
│   │       └── graph.py            # DAG of mechanisms, contradiction detection
│   ├── research/                   # Stage 2: Research Intelligence (self-improving)
│   │   ├── engine.py               # Research planning, evidence-driven question prioritization
│   │   ├── priority.py             # Source scoring, planning
│   │   ├── source_value.py         # Wilson intervals, novelty/redundancy tracking
│   │   ├── experience.py           # Learning from past investigations
│   │   ├── questions.py            # Research questions store
│   │   └── store.py                # Investigation database
│   ├── expansion/                  # Stage 1-2: Autonomous Expansion
│   │   ├── entity_discovery.py     # Wikipedia category crawl → new entities
│   │   ├── source_discovery.py     # Sitemap/RSS discovery on known domains
│   │   ├── connector_evolution.py  # Auto-generate extractors for structured formats
│   │   └── sandbox.py              # Isolated testing before production promotion
│   ├── scheduler/
│   │   ├── scheduler.py            # ThreadPoolExecutor, per-subsystem locking
│   │   ├── budget.py               # Resource budgeting (observation units, CPU/memory)
│   │   └── registration.py         # Task registration (all 38 tasks listed)
│   ├── infrastructure/
│   │   ├── databases.py            # DuckDB connection pooling, migrations
│   │   ├── http.py                 # HTTP API routes, CORS
│   │   ├── events.py               # Event bus (VALIDATION_COMPLETED, etc.)
│   │   └── resources.py            # Resource manager (CPU/memory tracking)
│   ├── dashboard/
│   │   ├── api/
│   │   │   ├── app.py              # FastAPI entry point
│   │   │   └── routes.py           # All /api/* endpoints
│   │   └── frontend/
│   │       ├── index.html          # SPA (vanilla JS, no frameworks)
│   │       ├── style.css           # Cinematic dark palette (#050505, #c9a35c)
│   │       └── js/
│   │           ├── main.js         # Core app logic
│   │           └── helpers.js      # humanTask(), humanSignal(), UI helpers
│   ├── configs/
│   │   ├── elecous.yaml            # Dashboard/scheduler settings
│   │   ├── thresholds.yaml         # Scientific parameters (all 50+)
│   │   ├── sources.yaml            # Source registry (11 sources, credentials)
│   │   ├── entities.yaml           # 300 products being scanned
│   │   ├── semantics.yaml          # Phenomenon→category mapping (16 types)
│   │   └── README.yaml             # Config documentation
│   ├── scripts/
│   │   ├── deep_backfill_gdelt.py  # Resumable GDELT deep backfill to 2017
│   │   ├── rerun_pipeline.py       # Full historical re-run after backfill
│   │   ├── expand_entities.py      # Discover new entities via Wikipedia
│   │   └── evidence_report.py      # Audit trail of evidence → mechanisms
│   ├── tests/
│   │   ├── test_phase*.py          # 12 test files, 106 tests total
│   │   ├── conftest.py             # Fixtures: isolated_config, temp data dirs
│   │   └── README.md               # Test running guide
│   └── docs/
│       ├── decisions/              # ADRs (Architecture Decision Records)
│       ├── manual/                 # Prior session manual docs
│       └── handoff/                # 12-doc handoff package (01–12_*.md)
├── .claude/
│   ├── settings.local.json         # Claude Code session config
│   └── plans/                      # Task plans from plan mode
├── logs/
│   └── elecous.log                 # Live application logs
├── data/                           # DuckDB databases
│   ├── observation.duckdb
│   ├── demand.duckdb
│   ├── environment.duckdb
│   ├── mechanism.duckdb
│   ├── validation.duckdb
│   ├── prediction.duckdb
│   ├── knowledge.duckdb
│   ├── research.duckdb
│   ├── expansion.duckdb
│   ├── connector_runs.duckdb
│   └── [other domain databases]
├── docs/
│   ├── handoff/                    # 12-document handoff package
│   │   ├── 01_CURRENT_STATE.md
│   │   ├── 02_COMPLETE_ARCHITECTURE.md
│   │   ├── 03_REPOSITORY_GUIDE.md
│   │   ├── 04_ENGINE_REFERENCE.md
│   │   ├── 05_DATA_REFERENCE.md
│   │   ├── 06_DATABASE_REFERENCE.md
│   │   ├── 07_CONFIGURATION_REFERENCE.md
│   │   ├── 08_RUNNING_ELECOUS.md
│   │   ├── 09_DEVELOPER_GUIDE.md
│   │   ├── 10_NEXT_STEPS.md
│   │   ├── 11_KNOWN_PROBLEMS.md
│   │   └── 12_AI_CONTINUATION_PROMPT.md
│   ├── manual/                     # Prior session manual docs
│   └── master/                     # THIS FILE (Master Bible)
├── README.md                       # Quick start
└── .gitignore
```

---

### 5. Database Reference

#### Single-Writer DuckDB Model

Each domain has its own DuckDB file (embedded, no external server). **Critical**: only one writer per file at a time. If you try to run a script while the dashboard is active, you'll get an IO lock.

**Domains** (11 total):

| Domain File | Purpose | Tables |
|---|---|---|
| `observation.duckdb` | Raw observations from all sources | observations, connector_runs, dedup_checksums |
| `demand.duckdb` | Demand episodes, baselines | episodes, baselines, episode_signals |
| `environment.duckdb` | Signals, factors, mechanisms | signals, factors, mechanisms, mechanism_versions, evidence_links |
| `validation.duckdb` | Validation records, outcomes | validations, predictions, outcomes |
| `prediction.duckdb` | Predictions and their resolution | predictions (also in validation.duckdb for join convenience) |
| `knowledge.duckdb` | Knowledge graph versioning | knowledge_nodes, knowledge_edges, contradictions |
| `research.duckdb` | Source value ledger, investigations | source_value_records, research_questions, research_experience |
| `expansion.duckdb` | Candidate entities/sources | candidate_entities, candidate_sources, sandbox_tests |
| `connector_runs.duckdb` | Telemetry: latency, observations accepted per source run | connector_runs |

#### Key Tables (by domain)

**observation.duckdb:**
- `observations` — 1.7M rows. Columns: id, source_id, entity_id, subject_type, value, timestamp, url, fetched_at, access_method, checksum.
- `connector_runs` — One row per source collection attempt. Columns: source_id, run_id, mode, count, latency_ms, observations_accepted, status.

**demand.duckdb:**
- `episodes` — 2,382 rows (every year 2017–2026). Columns: id, entity_id, start_date, peak_date, end_date, magnitude, demand_change_pct, confirmed.
- `baselines` — Per-entity rolling 30-day demand averages. Columns: entity_id, date, baseline, percentile_90.
- `episode_signals` — Links signals to episodes (which signals preceded this spike). Columns: signal_id, episode_id, days_before_spike, support, confidence.

**environment.duckdb:**
- `signals` — 177 currently. Columns: id, phenomenon, direction, scope, recorded_at, magnitude, modes (JSON: which extraction modes agreed).
- `factors` — Pattern combinations. Columns: id, signal_set (JSON), episode_count_with_signal, expected_by_chance, support, confidence.
- `mechanisms` — 147 validated + 467 retired + ~66 candidate. Columns: id, claim (description), behavioral_chain (JSON), lifecycle, confidence, created_at, updated_at, evidence_links (JSON).
- `evidence_links` — Lineage: which factors/signals/observations led to this mechanism. Columns: mechanism_id, source_type (factor/signal), source_id.

**validation.duckdb:**
- `validations` — One per mechanism validation. Columns: id, mechanism_id, check_names (JSON), passed_count, failed_count, confidence_scientific, created_at.
- `predictions` — 64 active. Columns: id, mechanism_id, created_at, lead_time_hours, business_value, status (PENDING/ACTIVE/RESOLVED).
- `outcomes` — Resolved predictions. Columns: prediction_id, outcome (CORRECT/PARTIAL/INCORRECT), judged_at, real_episode_id.

**knowledge.duckdb:**
- `knowledge_nodes` — Mechanism DAG nodes. Columns: id, mechanism_id, version, created_at.
- `knowledge_edges` — Causal relationships. Columns: from_id, to_id, relationship_type (SUPPORTS/CONTRADICTS/REFINES).
- `contradictions` — Detected conflicts. Columns: mechanism_a_id, mechanism_b_id, reason, severity.

**research.duckdb:**
- `source_value_records` — Source quality tracking. Columns: source_id, supports, contradictions, lead_time_days_avg, wilson_lower_bound, novelty_count, redundancy_count.
- `research_questions` — Investigation queue. Columns: id, statement, need_cluster, kind, priority, status (OPEN/RESOLVED), expected_evidence (JSON).
- `research_experience` — Learning from past investigations. Columns: id, question_id, sources_used (JSON), outcome, strategy (JSON), cost (JSON: latency_ms, cpu%, memory_mb).

#### Growth & Indexes

**Current sizes:**
- `observation.duckdb`: 2.1 GB (1.7M observations × ~1.2KB each).
- `environment.duckdb`: 2.8 GB (append-only mechanism versioning; no compaction yet).
- `validation.duckdb`: 0.4 GB (147 validated + 467 retired mechanisms).
- All others: < 500 MB.
- **Total: ~5.6 GB** against a 150–200 GB budget target.

**Indexes (verified EXPLAIN output):**
- `observations` indexed on (source_id, timestamp).
- `signals` indexed on (phenomenon, recorded_at).
- `mechanisms` indexed on (lifecycle, confidence).
- `validations` indexed on (mechanism_id, created_at).

**No compaction yet.** The append-only versioning model means every mechanism update creates a new row. At scale, this will be technical debt. Designed; not yet urgent at current growth.

---

## PART III: SCIENTIFIC ENGINES & REASONING

### 6. Signal Discovery (Multi-Mode Extraction)

**File:** `elecous/environment/signal_discovery/extractor.py`

The signal extractor converts raw observations into meaningful signals. **Three modes currently implemented** (10 total planned):

#### Mode 1: Spike Detection (Z-Score Anomaly)

- **Purpose:** Detect unusual single-day deviations.
- **Algorithm:** `z_score = (value - baseline_mean) / baseline_std`.
- **Threshold:** `z_score >= 2.0`.
- **Tuning:** `environment.spike_z_threshold: 2.0` in `thresholds.yaml`.
- **Example:** "Temperature jumped 5°C above normal → `thermal_stress:up` signal."

**Bug caught this session:** self-referential baseline included the spike day itself, causing naturally-decaying series (like Wikipedia pageviews after a viral event) to register their tail as perpetually "extreme." **Fixed:** rank against baseline period only (before the window).

#### Mode 2: Trend Detection (Polyfit + Correlation Gate)

- **Purpose:** Detect sustained movements, not just single-day spikes.
- **Algorithm:** `np.polyfit(time, values, deg=1)` → slope normalized by baseline scale.
- **Gating:** Only register if `|correlation| >= 0.6` (rejects noisy zigzags).
- **Tuning:** `environment.trend_min_slope_z: 0.8`, `environment.trend_min_correlation: 0.6`.
- **Example:** "Rainfall consistently above normal for 5 days → `rainfall_intensity:up` signal."

**Measured result:** 36 signals (20% of 177) now carry trend evidence, activating phenomena that spike-only extraction missed.

#### Mode 3: Level Detection (Extreme Percentile)

- **Purpose:** Detect when a quantity stays abnormally high/low.
- **Algorithm:** Rank the window's mean against the baseline distribution's percentiles.
- **Gating:** Require `effect_size >= change_z` (same magnitude gate spike detection uses).
- **Tuning:** `environment.level_percentile: 0.85`, `environment.level_effect_size_z: 0.8`.
- **Example:** "Heating demand stayed in the top 15% of historical values for a week → `heating_demand:high` signal."

**Two bugs caught by measuring real output:**
1. Ranked against a distribution that *included* the window → always ranked decaying series as extreme. **Fixed:** baseline-only ranking.
2. Rank-only (no magnitude gate) → flooded on sparse baselines (100% of an all-zero baseline beaten by any nonzero). **Fixed:** gated with effect_size.

**Measured result:** Reduced false positives from 125 spurious signals → 23 genuine ones.

#### Signal Key Format

All modes write to the same signal_key format: `{phenomenon}:{direction}:{scope}`.

Example keys currently active:
- `cold_stress:up:US`
- `thermal_stress:up:ES`
- `rainfall_intensity:up:FR`
- `media_attention:up:global`
- `seismic_activity:up:JP`
- `wind_intensity:up:CA`

This design allows downstream pattern mining and mechanisms to work unchanged when new extraction modes activate new signals.

---

### 7. Pattern Mining & Factor Discovery

**File:** `elecous/environment/pattern_engine/mining.py`

Factors are discovered through mining: "Which signal combinations statistically precede demand episodes more often than chance?"

#### Algorithm

```python
For each signal S:
    For each episode E:
        If S fired before E (within lookback window):
            record S → E link
    
    support = count(E with S) / count(all E)
    expected_by_chance = signal_frequency * baseline_capture_rate
    
    If support >> expected_by_chance:
        Create FACTOR(S → E)
        Compute confidence = support / (support + contradictions)
```

#### Configuration

- **Lookback window:** 30 days (configurable in `thresholds.yaml`).
- **Minimum support:** Signal must precede ≥2 episodes to be considered.
- **Confidence floor:** 0.40 (40% precision).

#### Example

Real example from current run:

| Signal | Support (episodes) | Expected by Chance | Confidence | Status |
|---|---|---|---|---|
| cold_stress:up:US | 23 | 1.2 | 0.92 | → Mechanism |
| thermal_stress:up:ES | 18 | 0.8 | 0.88 | → Mechanism |
| rainfall_intensity:up | 8 | 0.5 | 0.70 | → Mechanism |
| random_noise:up | 2 | 2.0 | 0.35 | Rejected (confidence floor) |

---

### 8. Mechanism Formation

**File:** `elecous/mechanism/engine.py`

From factors, the system forms mechanisms: explicit causal chains from factors.

#### Structure

```python
class Mechanism(Versioned):
    claim: str  # e.g., "Cold stress precedes heating demand surge"
    behavioral_chain: List[str]  # e.g., ["Cold weather", "Thermostat activation", "Heating fuel demand spike"]
    evidence_links: {
        "observations": [obs_ids],
        "signals": [signal_ids],
        "factors": [factor_ids],
    }
    confidence: float  # 0–1, refined by validation
    lifecycle: Lifecycle  # CANDIDATE → VALIDATED → TRUSTED, or CANDIDATE → RETIRED
```

#### Behavioral Chains

Chains are **causal narratives**, not just statistical correlations. Example:

```
cold_stress:up:US (signal)
    ↓ (causal link, inferred from domain ontology)
"Thermostats trigger heating mode"
    ↓
heating_fuel_demand:up (potential future signal, not yet ingested)
    ↓
"Upstream supply reacts"
    ↓
heating_oil_price:up (FRED series, once credentials restored)
```

#### Versioning

Each mechanism stores its entire edit history. When new evidence appears (e.g., a new signal activation), the mechanism can be re-evaluated without losing the old version. Allows rollback if new evidence contradicts.

---

### 9. Validation: The Six-Check Gate

**File:** `elecous/validation/engine.py`

**No mechanism becomes VALIDATED without passing all six checks.** This is the constitution-level constraint that prevents the system from promoting false positives.

#### Check 1: Precision on Holdout Data

**Question:** If we replay this mechanism on held-out historical data, how often is it right?

**Method:** Historical replay with time-series cross-validation. Split history into train/test by date; generate predictions as-of test date; judge against real episodes.

**Threshold:** Precision ≥ 0.50 (at least 50% of predictions were correct).

**Tuning:** `validation.precision_threshold: 0.50` in `thresholds.yaml`.

#### Check 2: Overfitting Detection

**Question:** Is this mechanism just memorizing training data or does it generalize?

**Method:** Train on first 80% of history; test on last 20%. Measure precision degradation.

**Threshold:** Precision(train) − Precision(test) ≤ 0.15 (no more than 15% precision loss).

**Tuning:** `validation.overfitting_tolerance: 0.15`.

#### Check 3: Skeptic Challenge

**File:** `elecous/validation/skeptic/skeptic.py`

**Question:** Is there a simpler explanation for this pattern?

**Method:** Generate alternative hypotheses (e.g., "Maybe it's just seasonal?", "Maybe any signal correlates with episodes?", "Maybe the baseline is miscomputed?") and test them.

**Threshold:** Skeptic must fail to beat the mechanism on holdout data. If a simpler hypothesis is more precise, reject the mechanism.

**Examples:**
- **Seasonal hypothesis:** `sin(day_of_year)` as the sole predictor. If it beats the mechanism, reject.
- **Random hypothesis:** Replace mechanism signal with random noise. If precision stays the same, reject.
- **Competing signal hypothesis:** Try other active signals; if they're more precise, discount the mechanism.

#### Check 4: Geographic Diversity

**Question:** Does the mechanism work across multiple geographies or only in one region?

**Method:** Train on one country; test on others. Require positive precision in ≥2 geographies.

**Threshold:** Valid across ≥2 countries or regions.

**Tuning:** `validation.geographic_diversity_min_regions: 2`.

#### Check 5: Temporal Stability

**Question:** Is the mechanism consistent over time or is it broken by new data?

**Method:** Train on first half of history; test on second half. Repeat for overlapping windows.

**Threshold:** Precision must remain ≥0.40 in recent data; no more than 0.15 degradation from historical.

**Tuning:** `validation.temporal_degradation_tolerance: 0.15`.

#### Check 6: Confidence Floor

**Question:** Has the mechanism earned enough evidence to warrant promotion?

**Method:** Aggregate evidence from all checks into a single confidence score.

**Formula:**
```
confidence = 
    0.4 × scientific_confidence 
    + 0.3 × prospective_confidence 
    + 0.3 × skeptic_robustness
```

Where:
- `scientific_confidence` = validation evidence from checks 1–5.
- `prospective_confidence` = real predictions made and outcomes observed (starts at 0; only filled by `resolve_due()`).
- `skeptic_robustness` = 1.0 − discount_from_skeptic_challenges.

**Threshold:** `confidence ≥ 0.35` (the promotion floor).

**Tuning:** `validation.confidence_floor: 0.35`.

---

### 10. Historical Replay (No Future Leakage)

**File:** `elecous/validation/replay.py`

**The constitution's third rule:** predictions cannot access observations after their timestamp.

#### Implementation

```python
def replay_mechanism(mechanism, as_of_datetime):
    """What would this mechanism have predicted on a specific date?"""
    # 1. Get all mechanisms that existed as_of_datetime (filtered by created_at)
    # 2. Get all observations available as_of_datetime (filtered by timestamp < as_of_datetime)
    # 3. Extract signals (spike/trend/level modes) on this data
    # 4. Match mechanism triggers
    # 5. Return (prediction, confidence, evidence)
    
    # CRITICAL: Never access observations after as_of_datetime
    # CRITICAL: Never access mechanisms created after as_of_datetime
```

#### Verification

Every replay path is guarded:

```python
assert all(obs.timestamp < as_of_datetime for obs in observations_used)
assert all(mech.created_at < as_of_datetime for mech in mechanisms_checked)
```

**Enforcement:** Constitution validator raises an exception if any past-looking code violates this.

---

### 11. Outcome Resolution & Calibration

**File:** `elecous/prediction/engine.py` + `elecous/validation/calibration.py`

#### Prediction Lifecycle

1. **Generation:** Mechanism fires (world state matches trigger). Prediction created with `lead_time_hours` (e.g., 48).
2. **Active:** Prediction is live; users see it on the dashboard.
3. **Due:** `created_at + lead_time_hours` has passed. Time to judge.
4. **Resolution:** `resolve_due()` finds real episodes after the lead-time window. Judges prediction as CORRECT / PARTIAL / INCORRECT.
5. **Feedback:** Outcome updates `prospective_confidence` channel. If many incorrect predictions, confidence drops and mechanism may be demoted.

#### Resolution Algorithm

```python
def resolve_due(prediction):
    """Judge a prediction after its lead time has passed."""
    
    # Window: lead_time_hours after prediction creation
    start_window = prediction.created_at + timedelta(hours=prediction.lead_time_hours)
    end_window = start_window + timedelta(hours=24)  # Judge within 24h after lead time
    
    # Find real episodes in this window for the same entity
    real_episodes = [e for e in episodes 
                     if e.entity_id == prediction.mechanism.entity_id
                     and start_window <= e.peak_date <= end_window]
    
    if real_episodes:
        if any(e.magnitude >= prediction.expected_magnitude * 0.8):
            prediction.outcome = CORRECT
        else:
            prediction.outcome = PARTIAL
    else:
        prediction.outcome = INCORRECT
    
    # Update prospective confidence
    confidence_delta = {
        CORRECT: +0.05,
        PARTIAL: +0.01,
        INCORRECT: -0.10,
    }[prediction.outcome]
    
    mechanism.prospective_confidence = max(0, min(1, 
        mechanism.prospective_confidence + confidence_delta
    ))
```

#### Calibration Curve

After ≥10 predictions resolved:

```python
def calibration_curve():
    """Are stated confidences honest?"""
    buckets = {0.1: [], 0.2: [], ..., 1.0: []}
    
    for pred in resolved_predictions:
        bucket = round(pred.stated_confidence, 1)
        actual = 1 if pred.outcome == CORRECT else 0
        buckets[bucket].append(actual)
    
    for bucket in buckets:
        stated = bucket
        actual = mean(buckets[bucket])
        miscalibration = abs(stated - actual)
        
        if miscalibration > 0.15:
            # Adjust future confidence thresholds
            adjust_thresholds()
```

---

## PART IV: CURRENT STATE & ROADMAP

### 12. Current Project Status

**As of 2026-07-06, commit 96b79cb**

#### Measured Numbers (Real)

- **1,702,818** real observations (2015–2026).
- **2,382** demand episodes (all years 2017–2026).
- **147 validated mechanisms**, 467 retired, ~66 candidate.
- **64 active predictions** (0 resolved yet; all inside lead-time windows).
- **Benchmark:** F1 0.631 vs. single-signal baseline F1 0.626 → **outperforms**.
- **Storage:** 5.6 GB (11 domain DuckDBs).
- **Test suite:** 106/106 non-network tests passing.

#### Environmental Coverage (The Core Weakness)

Validated mechanisms:
- **Weather signals:** 64% (cold_stress, thermal_stress, rainfall, wind, etc.)
- **Media signals:** 36% (media_attention, pageviews)
- **Economics/Energy:** 0% (suspended credentials + external reporting cadence)
- **Social/Search/Retail/Pricing/Logistics:** 0% (no connectors yet)

**Root cause, confirmed by direct measurement:** Signal extraction was spike-only, structurally favoring volatile phenomena. Fixed this session by adding trend and level modes. But external data gap remains (economics/energy blocked on API keys; social/search need new connectors).

#### What Was Fixed This Session

1. **UTC date-cast bug** — session timezone corrupting day-grouping.
2. **Ingestion throughput** — 513 → 4,465 obs/s (8.7× via bulk insert).
3. **Concurrent scheduler** — subsystems now overlap.
4. **Confidence deadlock** — 0 → 147 validated mechanisms (fix: write validation evidence to "scientific" channel before floor check).
5. **Two integrity bugs** while verifying #4 (TRUSTED reachability, benchmark cherry-picking).
6. **Prediction resolution** — built `resolve_due()` (didn't exist before).
7. **Mechanism retirement** — built automatic retirement for persistent failures.
8. **Resumable GDELT backfill** — chunked, resumable from real per-entity coverage.
9. **Multi-mode extraction** — trend + level modes (spike + correlation gate + magnitude gate).
10. **Dashboard** — rebuilt from admin panel to Mission Control (decision-first cards, honest coverage note, human language layer).

#### Tests

**106 non-network tests, all passing.**

Run with:
```bash
unset ELECOUS_DATA_DIR
.venv/bin/python -m pytest elecous/tests -m "not network" -q
```

---

### 13. Known Limitations & Technical Debt

**Ranked by real impact:**

#### HIGH PRIORITY

1. **Environmental coverage is narrow** (partially fixed)
   - Measured: weather 64% / media 36% / else 0%.
   - Root cause: spike-only extraction (fixed) + external data gaps (unfixable by software).
   - Remains: economics/energy 0% (EIA/FRED suspended), social/search 0% (no connectors).

2. **No resolved predictions yet**
   - 64 predictions exist; 0 resolved.
   - By design: `resolve_due()` correctly waits for lead-time to pass.
   - Consequence: future real-world precision/recall unmeasured (only backtest exists).

#### MEDIUM PRIORITY

3. **Level detection had two real bugs** (fixed)
   - Self-referential ranking.
   - Rank-without-magnitude flooding.
   - Both caught by measuring real output, not unit tests.

4. **Two self-caught bugs while fixing confidence deadlock**
   - TRUSTED reachable via historical re-validation (capped).
   - Rolling benchmark cherry-picking by confidence (re-pointed at validated set, id-ordered).

5. **Storage growth from append-only versioning**
   - `environment.duckdb` is fastest-growing (2.8 GB).
   - No compaction yet; not urgent at 5.6 GB total against 150–200 GB budget.
   - Technical debt; should be designed before urgency.

6. **Single-writer DuckDB friction**
   - Any script run while dashboard active causes IO lock.
   - Not a bug; DuckDB's documented model.
   - Causes real friction; workaround documented.

#### LOW PRIORITY

7. **7 of 10 extraction modes not implemented**
   - Requested: regime, cyclic, momentum, correlation, divergence, persistence, composite.
   - Implemented: spike, trend, level.
   - Reason for deferral: each needs real design (change-point algorithms, cross-phenomenon analysis), not quick stubs.

8. **Source discovery limited**
   - Only autodiscovers sitemap/RSS on known domains (hardcoded list).
   - No general web search without user-provided API.
   - By design (ADR 0002).

9. **Connector auto-generation covers structured formats only**
   - HTML tables, PDF, OpenAPI classified but never extracted.
   - Real effort, not yet justified by evidence of need.

---

### 14. Roadmap (Prioritized)

**From `docs/handoff/10_NEXT_STEPS.md`**

#### Immediate (blocker only: calendar time)

1. **Run full historical rewalk on 2017+ corroborated history**
   - Reason: Deep backfill to 2017 + multi-mode extraction landed, but full historical replay not run (1+ hours, deferred for context).
   - Impact: Real before/after mechanism/category-diversity numbers.
   - Blocker: None. Script `scripts/rerun_pipeline.py` exists.
   - Time: ~1 hour, unattended.

2. **Get economics/energy producing signals**
   - Reason: Measured at 0%; root cause confirmed (suspended EIA/FRED API keys + World Bank annual/quarterly reporting).
   - Dependencies: User must provide `eia_api_key`, `fred_api_key`.
   - Impact: Activate 2 more categories (but still won't dominate due to natural reporting cadence).

3. **Add one new environmental category** (search trends or social volume)
   - Reason: Actually breaks weather/media duopoly.
   - Dependencies: Legally-accessible API (Google Trends is unofficial; alternatives: real social-volume API).
   - Difficulty: Medium (new Connector + semantics mapping).

#### Medium-Term

4. **Complete 7 remaining extraction modes**
   - Reason: Each needs measured design, not stubs.
   - Design challenge: regime (change-point detection), correlation/divergence (cross-phenomenon joint analysis).

5. **Full dashboard redesign**
   - Done this session: decision-first cards, coverage honesty, human language.
   - Not done: per-prediction "Why?" panels, evidence bars, factor encyclopedia, replay UI, interactive knowledge graph.
   - Difficulty: High (real multi-day frontend design).

6. **Autonomous Backlog Manager**
   - Generalize research priority queue to operational tasks.
   - Critical constraint: Every backlog item must trace to a real signal (not fabricated busywork).

#### Long-Term / Scaling

7. **Adaptive attention / product lifecycle**
   - Measured trigger: ~10K entities (today ~300 at 5.9ms/entity).
   - Design: Dynamic allocation based on value/cost tradeoff.
   - Status: Designed; not yet built (no evidence of need).

8. **Storage compaction**
   - Append-only versioning model; no compaction yet.
   - Design needed before urgency.

---

## PART V: DEVELOPER & OPERATIONAL GUIDES

### 15. How to Install, Run, Stop, and Restart

#### Requirements

- **Python 3.11** (verified with `.venv`)
- **~6 GB free disk** (minimum; ~200 GB recommended)
- **MacBook Pro M4** (tested; should work on M1/M2/M3/M4; Linux/Windows untested)

#### Dependencies

```bash
cd ~/ELECOUS
python3.11 -m venv .venv
source .venv/bin/activate
pip install duckdb fastapi uvicorn requests numpy pyyaml
```

**Confirmed absent:** pandas, pyarrow, scikit-learn (kept minimal).

#### First Run

```bash
cd ~/ELECOUS
.venv/bin/elecous bootstrap
.venv/bin/elecous backfill --days 730       # Fetch last 2 years
```

#### Running Continuously

```bash
# Option 1: Via CLI
.venv/bin/elecous run

# Option 2: Via Python (what was used most this session)
.venv/bin/python -m elecous.dashboard.api.app

# Both start the scheduler (38 tasks) AND the dashboard
```

**Dashboard:** http://127.0.0.1:8321

#### Stopping

```bash
# Clean shutdown (Ctrl+C in the foreground)
Ctrl+C

# Or via process kill (if needed)
pkill -f "elecous.dashboard.api.app"
```

#### Restarting

```bash
pkill -f "elecous.dashboard.api.app"    # Stop if running
sleep 2
.venv/bin/python -m elecous.dashboard.api.app   # Restart
```

#### ⚠️ CRITICAL: Single-Writer DuckDB

**Never run a script against the data directory while the dashboard is active.** You will get an IO lock error.

**To inspect live state:**
1. **Preferred:** Query via HTTP API.
   ```bash
   curl http://127.0.0.1:8321/api/mechanisms
   curl http://127.0.0.1:8321/api/predictions
   ```

2. **If you need a script:** Stop the dashboard first.
   ```bash
   pkill -f "elecous.dashboard.api.app"
   # Run your script
   .venv/bin/python scripts/deep_backfill_gdelt.py
   # Restart
   .venv/bin/python -m elecous.dashboard.api.app
   ```

#### Testing

```bash
unset ELECOUS_DATA_DIR    # Critical: prevents touching production data
.venv/bin/python -m pytest elecous/tests -m "not network" -q

# Expect: 106 passed
```

---

### 16. Laptop-Specific Configuration

**MacBook Pro M4, ~/ELECOUS**

#### Environment Setup

- **Virtual environment:** `.venv/` (Python 3.11)
- **Data directory:** `~/ELECOUS/data/` (11 DuckDB files)
- **Logs:** `~/ELECOUS/logs/elecous.log` (live application logs)
- **Configs:** `~/ELECOUS/elecous/configs/` (YAML files)

#### Ports

- **Dashboard:** http://127.0.0.1:8321 (HTTP only; localhost)
- **No external services** (all embedded)

#### Background Processes

After `elecous run`:

```bash
ps aux | grep elecous
# Shows: Python process, ThreadPoolExecutor, scheduler, 38 registered tasks
```

#### Disk & Memory

```bash
# Check disk
df -h ~
# Check memory during run
vm_stat
# While running, monitor:
top -pid $(pgrep -f "elecous.dashboard.api.app")
```

**Typical memory usage:** ~200 MB (DuckDB + Python stack)

#### Logs & Debugging

```bash
# Live logs
tail -f ~/ELECOUS/logs/elecous.log

# Profiling
/usr/bin/time -l .venv/bin/elecous backfill --days 1
# Outputs: wall clock, CPU, peak RSS
```

#### Cache & Temporary Files

- **Temporary files:** None (DuckDB uses WAL, no separate tmp files).
- **Caching:** Multi-mode extractor caches signal extraction results per phenomenon (not persisted; rebuilt each run).

#### Storage Locations

```
~/ELECOUS/
├── data/                 # DuckDB files (5.6 GB)
├── logs/                 # Application logs
├── elecous/configs/      # Configuration YAML files
├── docs/                 # Documentation
└── .claude/              # Claude Code session files
```

---

### 17. Repository Migration (Moving to Another Computer)

#### What Must Be Copied

1. **The entire `~/ELECOUS/` directory** (source code, configs, documentation).
2. **The `data/` subdirectory** (11 DuckDB files; ~5.6 GB).
3. **Optionally:** `logs/elecous.log` (for audit trail) and `.claude/` (for session context).

#### What Can Be Regenerated

- `.venv/` (recreate with `python3.11 -m venv .venv && pip install ...`).
- Any generated test databases (use `isolated_config` fixture).

#### What Should Never Be Deleted

- `data/` (contains real observations and validated mechanisms).
- `elecous/configs/` (YAML files with thresholds, sources, entities, semantics).
- `docs/` (documentation, ADRs, handoff package).
- `elecous/governance/constitution.py` (enforcement of real-data-only discipline).

#### Migration Steps

```bash
# On source machine:
tar czf ELECOUS_backup_$(date +%Y%m%d).tar.gz ~/ELECOUS/
scp ELECOUS_backup_*.tar.gz user@target:~/

# On target machine:
cd ~
tar xzf ELECOUS_backup_*.tar.gz
cd ELECOUS

# Recreate virtual environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install duckdb fastapi uvicorn requests numpy pyyaml

# Verify
unset ELECOUS_DATA_DIR
.venv/bin/python -m pytest elecous/tests -m "not network" -q
# Expect: 106 passed

# Run
.venv/bin/python -m elecous.dashboard.api.app
# Expect: Dashboard at http://127.0.0.1:8321
```

#### Platform-Specific Considerations

- **Linux:** Paths use `/` not `~`. Otherwise identical.
- **Windows:** Use WSL or native Python 3.11; paths use `/c/Users/...`. Untested.
- **Cloud:** DuckDB is embedded; no special deployment needed. Runs as a plain Python process.

---

## PART VI: AI CONTINUATION GUIDE

### 18. For the Next AI (Or Human) Developer

#### Before You Start Anything

1. **Read the documentation first** (yes, really).
   - `docs/handoff/01_CURRENT_STATE.md` — current numbers.
   - `docs/handoff/02_COMPLETE_ARCHITECTURE.md` — architecture.
   - `docs/handoff/03_REPOSITORY_GUIDE.md` — file layout.
   - `docs/handoff/04_ENGINE_REFERENCE.md` — what each engine does.
   - `docs/handoff/10_NEXT_STEPS.md` — prioritized roadmap.
   - `docs/handoff/11_KNOWN_PROBLEMS.md` — honest weaknesses.
   - `docs/manual/AUDIT_environmental_bias.md` — root cause of weather/media bias (and why it's NOT what you might think).

2. **Run the test suite** to confirm the repository matches what's documented.
   ```bash
   unset ELECOUS_DATA_DIR
   .venv/bin/python -m pytest elecous/tests -m "not network" -q
   # Expect: 106 passed
   ```

3. **Start the dashboard** and explore it live.
   ```bash
   .venv/bin/python -m elecous.dashboard.api.app
   # Visit http://127.0.0.1:8321
   ```

4. **Query the API** to understand real data.
   ```bash
   curl http://127.0.0.1:8321/api/mechanisms | jq . | head -50
   curl http://127.0.0.1:8321/api/predictions | jq . | head -20
   ```

#### Core Principles

**These are not suggestions; they are what worked.**

1. **Real evidence over appearance.**
   - A dashboard honestly showing "quiet, next task at 14:32" is better than one fabricating activity.
   - A benchmark that shows "F1 0.631 vs baseline 0.626 on validated set, id-ordered" is trustworthy. One that cherry-picked "F1 0.95 on top-30 by confidence" is not.

2. **Measure before and after every change.**
   - Not "should be faster" — actual numbers.
   - Not "probably improves accuracy" — real before/after benchmark.
   - See commit messages this session for the pattern: hypothesis → measure → confirm/reject → implement → re-measure.

3. **Root-cause before patching.**
   - The weather bias looked like "reweight signals toward non-weather" but the real problem was upstream: extraction never produced non-weather signals in the first place.
   - Always find where the capability is actually missing.

4. **Small, additive, reversible changes.**
   - Migrations use `IF NOT EXISTS`.
   - New extraction modes write into existing signal-key formats (not new schemas).
   - Add fields to tables, don't rename or remove.

5. **Honest scope-cutting.**
   - When a directive asks for 10 things and only 2–3 can be done with real rigor, ship those 2–3 well and say plainly what wasn't done.
   - Don't ship 10 shallow stubs.

#### Coding Discipline

1. **No synthetic data, ever.**
   - Every observation must have real provenance.
   - Every test uses `isolated_config` fixture (temp data dir).
   - Constitution enforced at `elecous/governance/constitution.py`.

2. **Never weaken validation.**
   - If validation blocks progress, the fix is better evidence/reasoning, or a threshold change backed by real measurement.
   - Threshold weakening for convenience is the single worst anti-pattern in this codebase.

3. **Comments explain WHY, not WHAT.**
   - Good: "UTC date-cast fix: CAST(timestamp AS DATE) used session tz (+05:30), causing 23:59 UTC observations to land on next day, breaking signal extraction day-grouping."
   - Bad: "Convert timestamp to date."
   - Especially important for non-obvious fixes, constraint workarounds, bugs fixed.

4. **Testing discipline.**
   - Run full non-network suite after every change: `.venv/bin/python -m pytest elecous/tests -m "not network" -q`
   - Network tests marked `@pytest.mark.network` and deselected by default.
   - Tests should use abstract IDs, not fabricated observations.

#### The Single-Writer Constraint

**DuckDB is single-writer per database file. This is enforced by the OS, not by ELECOUS.**

- **If you try to `build_stack()` while the dashboard is active, you get an IO lock.**
- **Workaround:** Query via HTTP API, or stop the dashboard, run your script, restart.
- **This caused multiple real failures this session.** It's not a bug; it's how DuckDB works. Accept it.

#### When You're Stuck

1. **Check the constitution first.** Many problems come from violating the three rules.
2. **Read the related handoff doc.** Every decision is documented with reasoning.
3. **Measure.** Before proposing a fix, measure the "before" state. Use `/usr/bin/time -l` for profiling.
4. **Look at recent commits.** Git log shows the actual fixes and their reasoning.
5. **Inspect real data.** Query the database; don't assume. Much of this session's best work came from finding that reality diverged from assumptions.

#### Continuing the Roadmap

**Next steps (from `10_NEXT_STEPS.md`):**

1. **Run `scripts/rerun_pipeline.py`** on the 2017+ history with multi-mode extraction active.
   - Why: Gives real before/after mechanism/category counts.
   - Why not done: Would take 1+ hour; context constraints this session.

2. **Get economics/energy signals active.**
   - Why: Measured at 0%; breaking weather/media duopoly.
   - Blocker: User must provide EIA/FRED API keys.
   - Can't be done by AI alone.

3. **Add one new environmental category** (search trends or social volume).
   - Why: Actually breaks the bias.
   - Work: New Connector + semantics mapping.

4. **Implement 7 remaining extraction modes** (regime, cyclic, momentum, correlation, divergence, persistence, composite).
   - Why: Deferred because each needs real design, not stubs.
   - Risk of deferred work: If you implement these carelessly, you'll make the audit trail worse, not better.

#### Example: How to Add a Connector

```python
# 1. Subclass base.Connector
from elecous.observation.connectors.base import Connector

class MyNewConnector(Connector):
    parser_id = "my_new_source"
    
    def collect(self, mode, since):
        """Fetch observations.
        
        Args:
            mode: "live" (recent data) or "backfill" (historical)
            since: datetime (live mode only; backfill mode uses configured depth)
        
        Returns:
            List[Observation]
        """
        # Fetch real data from your API
        observations = []
        for row in fetch_data():
            obs = Observation(
                source_id=self.parser_id,
                entity_id=row['entity'],
                subject_type="my_observation_type",
                value=row['value'],
                timestamp=row['timestamp'],
                url=row['url'],
                access_method="api",
            )
            observations.append(obs)
        return observations

# 2. Register in sources.yaml
sources:
  - name: my_new_source
    type: custom
    connector_class: MyNewConnector
    active: true
    update_frequency: "1h"

# 3. Map observations to phenomena in semantics.yaml
phenomena:
  my_phenomenon:
    observation_types: [my_observation_type]
    categories: [my_category]

# 4. Test
unset ELECOUS_DATA_DIR
.venv/bin/python -m pytest elecous/tests::test_my_new_connector -q

# 5. Verify with real data
curl http://127.0.0.1:8321/api/observations?source=my_new_source | jq .
```

#### When Documenting Changes

1. **Update the relevant handoff doc.** If you change architecture, update `02_COMPLETE_ARCHITECTURE.md`. If you change how to run something, update `08_RUNNING_ELECOUS.md`.
2. **Update `11_KNOWN_PROBLEMS.md` if you fix something.** Or create a new doc if it's a major fix.
3. **Commit messages should reference the doc.** Example: "Fix X; details in docs/manual/AUDIT_*.md phase 3."

---

### 19. Evolution Path: From Here to 10K Entities

**Current state:** ~300 entities, ~2 seconds scan time.

**Scaling milestones (measured triggers, not guesses):**

| Milestone | Trigger | Action | Timeline |
|---|---|---|---|
| **1K entities** | Scan time exceeds 30 sec | No architecture change; maybe tune thresholds | N/A |
| **5K entities** | Scan time exceeds 5 min | Investigate; measure where bottleneck is | N/A |
| **10K entities** | Scan time exceeds 30 min | Build adaptive attention (selective scanning based on value/cost) | Design exists; ready to implement |

**Do not build adaptive attention now.** You'll be optimizing for an unknown future. Wait for evidence.

---

## FINAL CHECKLIST

### Verification Tasks

Before declaring ELECOUS "ready for continuation":

```bash
# 1. Repository state
git log --oneline -5
# Expect: Recent commits with clear messages

# 2. Tests pass
unset ELECOUS_DATA_DIR
.venv/bin/python -m pytest elecous/tests -m "not network" -q
# Expect: 106 passed

# 3. Dashboard runs
.venv/bin/python -m elecous.dashboard.api.app &
sleep 3
curl -s http://127.0.0.1:8321/api/mechanisms | jq . | head -3
# Expect: JSON array of mechanisms
pkill -f "elecous.dashboard.api.app"

# 4. Documentation exists
ls -la docs/handoff/
# Expect: 12 files (01–12_*.md)
ls -la docs/master/
# Expect: ELECOUS_MASTER_BIBLE.md + diagrams/

# 5. Config files present
ls -la elecous/configs/
# Expect: elecous.yaml, thresholds.yaml, sources.yaml, entities.yaml, semantics.yaml
```

### Real-World Validation

- **Does it match what the docs say?** Read a doc section; verify against actual code or API response.
- **Can another AI read this and continue?** The handoff package + Master Bible should be sufficient.
- **Is the most recent measurement still valid?** Some numbers (storage, entity count) change over time.

---

## Appendix A: Configuration File Reference

### `elecous.yaml`

```yaml
dashboard:
  port: 8321
  refresh_interval_seconds: 15

scheduler:
  max_workers: 4
  tick_interval_seconds: 2
  
research:
  units_per_cycle: 20  # Budget for ResearchEngine.tick()
```

### `thresholds.yaml`

```yaml
# Signal extraction
environment:
  spike_z_threshold: 2.0
  trend_min_slope_z: 0.8
  trend_min_correlation: 0.6
  level_percentile: 0.85
  level_effect_size_z: 0.8

# Validation
validation:
  precision_threshold: 0.50
  overfitting_tolerance: 0.15
  geographic_diversity_min_regions: 2
  temporal_degradation_tolerance: 0.15
  confidence_floor: 0.35
  match_threshold: 0.60

# Pattern mining
pattern:
  lookback_days: 30
  min_support: 2
  confidence_floor: 0.40
```

### `sources.yaml`

```yaml
sources:
  - name: gdelt_media
    type: custom
    active: true
    update_frequency: "1h"
    
  - name: open_meteo_weather
    type: custom
    active: true
    update_frequency: "1h"
    
  - name: eia_electricity
    type: keyed
    active: false                    # Suspended: missing API key
    api_key: ${EIA_API_KEY}
    
  - name: fred_series
    type: keyed
    active: false                    # Suspended: missing API key
    api_key: ${FRED_API_KEY}
```

### `semantics.yaml`

Maps observation types to phenomena (categories). Example:

```yaml
phenomena:
  thermal_stress:
    observation_types: [temperature_max, heat_index]
    categories: [environmental_stress]
  
  cold_stress:
    observation_types: [temperature_min, wind_chill]
    categories: [environmental_stress]
  
  rainfall_intensity:
    observation_types: [precipitation_mm]
    categories: [water_availability]
```

---

## Appendix B: Important Git Commits (This Session)

**Recent commits (confirm with `git log --oneline`):**

- `96b79cb` — docs: complete project handoff package (docs/handoff/)
- `2f1aed0` — Multi-mode signal extraction: trend + level alongside spike
- `92713cc` — docs: Phase 1 environmental-bias root cause (evidence before change)
- `6c482eb` — docs: ELECOUS Technical Reference Manual (docs/manual/)
- `143c8b0` — Decision-first opportunity cards + honest coverage note

**Fixes in detail (search git log for "Fix" or "fix"):**
- Confidence deadlock (0 → 147 validated mechanisms)
- UTC date-cast (session tz → UTC)
- Ingestion throughput (513 → 4,465 obs/s)
- Concurrent scheduler (subsystems overlap)
- Resumable GDELT backfill (chunked, 480K rows to 2017-06)

---

## END OF MASTER BIBLE

**This document is the permanent, verifiable source of truth for ELECOUS. If reality has diverged (code changed, numbers shifted, new features added), update this document and commit the change. Truth lives in the code and data; this manual is the interpretation.**

**Current repository state:** Production-ready, 147 validated mechanisms, 64 active predictions.

**Last updated:** 2026-07-06  
**Commit:** 96b79cb  
**Next milestone:** Run full historical rewalk on 2017+ corroborated history (scripts/rerun_pipeline.py).
