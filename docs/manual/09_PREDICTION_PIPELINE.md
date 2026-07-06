# 09 — Prediction Pipeline

**Scope**: trace one prediction observation→validation. **Read to complete**:
`observation/store.py` → `demand/spikes/detector.py` →
`environment/{signal_discovery,factor_discovery}/` → `mechanisms/engine.py` →
`prediction/engine.py` → `validation/engine.py::resolve_prospective` +
`prediction/engine.py::resolve_due` → `knowledge/engine.py::sync`.

Verified chain: raw observation (real, provenance-checked) → z-score spike →
episode → phenomenon-level signal → factor (co-occurrence pattern) → mechanism
(factor combination + behavioural chain) → validated → world match (activation
≥ thresholds.yaml prediction.match_threshold=0.6) → opportunity + prediction row
→ prospective registration → resolution after lead-time horizon → confidence
update + knowledge-graph sync.
