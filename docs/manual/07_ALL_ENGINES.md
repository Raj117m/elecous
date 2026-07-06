# 07 — All Engines

**Scope**: per engine — inputs, outputs, DB usage, algorithms, limitations,
inter-engine comms. **Read to complete**: the `engine.py` in each package plus
the sub-modules listed in doc 02.

Verified anchors (constructor signatures in `bootstrap.py` show the wiring):
- `DemandEngine` (`demand/engine.py`): observations → episodes via
  `spikes/detector.py` (robust z-score vs learned baseline, spec 3.7 stage ladder).
- `EnvironmentalEngine` (`environment/engine.py`): episodes → factors via
  pattern mining; owns states, concepts, world state, `extractor.changes()`.
- `MechanismEngine` (`mechanisms/engine.py`): factors → candidate mechanisms;
  confidence blend in `versioning/store.py::update_confidence`.
- `ValidationEngine` (`validation/engine.py`): facade over skeptic, replay,
  prospective, calibration, drift, benchmark, retirement — the only promoter.
- `PredictionEngine` (`prediction/engine.py`): validated mechanisms + world
  state → opportunities/predictions; `resolve_due` judges outcomes.
- `KnowledgeEngine`, `ResearchEngine`, expansion engines: see doc 02.

Communication is via the event bus (`infrastructure/event_bus.py`) + direct
store reads. No engine calls `promote` except ValidationEngine (spec 6.30).
