# 13 — Configuration

**Scope**: every config value and its effect. **Read to complete**:
`elecous/configs/*.yaml` and `elecous/infrastructure/config.py`.

Verified files: `elecous.yaml` (dashboard host/port, scheduler.tick_seconds,
scheduler.max_workers), `thresholds.yaml` (demand stage_z ladder,
evidence_threshold, default_window_days, false_spike rules; validation
historical_min_precision, revalidation_days, min_confidence_{experimental,
validated,trusted}; prediction match_threshold/activation_full/opportunity_expiry;
mechanisms min_supporting_episodes), `sources.yaml` (per-source connector +
connector_config incl. backfill_days), `candidate_sources.yaml` (expansion pool),
`entities.yaml` (monitored topics, locations, themes).

Env vars: `ELECOUS_DATA_DIR` (override data directory — used by tests for
isolation). CLI args: see `scripts/cli.py` argparse.
