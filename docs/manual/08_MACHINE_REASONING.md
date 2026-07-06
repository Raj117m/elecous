# 08 — Machine Reasoning

**Scope**: factor generation → mechanism formation → validation → confidence →
ranking → uncertainty → replay → benchmark → retirement → self-healing.
**Read to complete**: `environment/factor_discovery/factors.py`,
`environment/pattern_engine/mining.py`, `mechanisms/engine.py`,
`mechanisms/versioning/store.py`, `validation/engine.py`,
`validation/historical/replay.py`, `validation/skeptic/skeptic.py`,
`prediction/world_matching/matching.py`, `infrastructure/supervisor.py`.

Verified anchors:
- Confidence blend (`store.py::update_confidence`): current = 0.3·recent +
  0.25·historical + 0.2·scientific + 0.15·regional + 0.1·category. The
  `scientific` channel is populated by validation evidence (replay precision,
  discounted by skeptic/competing-explanation failures).
- Activation measured at **phenomenon** level (not concrete signal) in both
  `matching.py` and `replay.py` — they must agree or replay tests the wrong rule.
- Retirement: candidates with ≥3 all-failed validation attempts →
  `validation/engine.py::retire_persistent_failures`.
