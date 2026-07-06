# 07 — Configuration Reference

*Verified: `elecous/configs/*.yaml`, `elecous/infrastructure/config.py`.*

All configuration is YAML (no JSON/TOML/INI in this repo). Loaded via
`Config.load_file(name)`; `active_config()` returns the process-wide instance.

## `elecous.yaml` (root config)
- `dashboard.host` / `dashboard.port` — default 127.0.0.1:8321.
- `scheduler.tick_seconds` — daemon loop interval, default 5s. Lower = more
  responsive dispatch, higher = less wasted CPU checking for due tasks.
- `scheduler.max_workers` — ThreadPoolExecutor size, default 4. Raising this
  increases real concurrency (and CPU/memory pressure) — the concurrency
  fix this session assumed this stays modest for a single MacBook.

## `thresholds.yaml` (the most consequential file — changing these changes science, not cosmetics)

- `demand.stage_z.*` — the acceleration ladder (small_growth/expected_growth/
  acceleration/abnormal_acceleration) in robust z-score units. Raising these
  makes episode detection stricter (fewer, higher-confidence episodes).
- `demand.evidence_threshold` — score in [0,100] to call a candidate spike.
- `demand.default_window_days` — default lookback if a category hasn't
  learned its own window yet.
- `environment.change_z` — spike threshold for signal extraction.
- `environment.trend_min_slope_z` / `trend_min_correlation` — **added this
  session**. Lowering `trend_min_slope_z` makes trend detection more
  sensitive (more signals, more false positives); raising
  `trend_min_correlation` makes it stricter about rejecting noisy zigzags.
- `environment.level_percentile` — **added this session**. How extreme
  (vs. the baseline distribution) a level must be to register. This is
  gated by `change_z` as a magnitude floor too (see extractor.py comments —
  a real bug was caught here by measuring, not assuming, thresholds work).
- `validation.historical_min_precision` — mechanisms must clear this
  precision in backtest replay. **Do not lower this to make more mechanisms
  pass** — that is exactly the anti-pattern this project's whole session
  discipline exists to prevent. If validation blocks too much, the fix is
  more/better evidence, not a lower bar.
- `validation.revalidation_days.{experimental,validated,trusted}` — how often
  each lifecycle stage gets automatically re-checked.
- `mechanisms.min_confidence_{experimental,validated,trusted}` — the rising
  confidence floor a mechanism must clear at each promotion (AND-ed with the
  six validation checks — both must pass).
- `prediction.match_threshold` — world-state/mechanism activation threshold
  for a live prediction to fire. **Must stay consistent with
  `validation/historical/replay.py`'s replay threshold** — a past bug had
  these silently diverge (0.99 hardcoded in replay vs 0.6 live), making
  `historical_precision_ok` vacuously pass. Fixed by deriving replay's
  default from this same config value — do not reintroduce a second
  hardcoded threshold.

## `sources.yaml` / `candidate_sources.yaml`
Per-source `connector` (class path) + `connector_config` dict (including
`backfill_days`). `candidate_sources.yaml` is the curated pool `expansion/`
matches against open observation gaps (ADR 0002: no general web search).

## `entities.yaml`
Monitored topics/locations/themes — the real product/entity catalog
(currently ~295-300 entries per `docs/manual` measurement).

## `semantics.yaml`
The `phenomena` map (observation_type → phenomenon name) — see
05_DATA_REFERENCE.md for the full current mapping. Adding a category
requires an entry here even if a connector already ingests the raw data.

## Environment variables
`ELECOUS_DATA_DIR` — overrides the data directory. Tests always `unset` this
or use the `isolated_config` fixture to avoid touching real production data.
