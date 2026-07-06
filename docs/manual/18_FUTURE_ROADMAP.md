# 18 — Future Roadmap

**Scope**: prioritized forward plan. Grounded in doc 16 (state) + doc 17
(limitations) + measurements this session.

Immediate (highest prediction-value per effort):
1. Add high-signal connectors beyond weather+news — search trends, social
   volume, commodity/price — to break the ~64% weather bias (doc 17 §1).
2. Let prospective outcomes resolve (calendar time) and report real
   precision/recall by horizon; wire `/predictions/accuracy` breakdowns.

Medium:
3. Complete the decision-first dashboard: per-opportunity "Why?" from
   `behavioural_chain`, evidence bars by category, factor encyclopedia,
   replay UI over `/predict/replay`.
4. Autonomous Backlog Manager — generalize `research/priority.py`'s
   value-ranked queue to operational improvement tasks; scheduler pulls the
   top backlog item when idle (never fabricate work — every item from a real
   published signal).
5. Extend connector evolution to HTML/PDF/OpenAPI extraction.

Long-term / scaling:
6. Adaptive attention engine — trigger at ~10K entities (measured: scan is
   5.9ms/entity today, so a fixed cost until then). Product lifecycle +
   clustering so similar products share learning.
7. Storage compaction for append-only factor versions.
8. GDELT via BigQuery when catalog crosses ~10K entities (bulk beats API only
   at that scale — measured in the acquisition audit).
