# 10 — Validation System

**Scope**: every validator, benchmark, replay, expiry, resolution, retirement,
confidence change. **Read to complete**: `validation/engine.py` (facade),
`validation/skeptic/skeptic.py`, `validation/historical/replay.py`,
`validation/prospective/prospective.py`, `validation/calibration/calibration.py`,
`validation/drift/drift.py`, `validation/benchmarks/benchmark.py`.

Verified anchors:
- Six mechanism checks (`engine.py::validate_mechanism`): generalizes,
  repeatable, timing_stable, skeptic_survives, historical_precision_ok,
  survives_competing_explanations — all must pass; confidence floor is AND-ed.
- Historical replay: walk-forward, leakage-guarded, precision/recall/F1/FDR,
  plus a single-signal baseline on the same windows.
- Rolling benchmark samples the **validated** set id-ordered (de-biased).
- Prospective: TRUSTED only via real future confirmation.
