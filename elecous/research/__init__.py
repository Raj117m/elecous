"""Research Intelligence: Level-2 scientific memory (rewrite directive,
stage 1). Where the rest of ELECOUS learns facts about the world (Level 1 —
mechanisms, factors, the Knowledge Graph), this package learns how to do
research: which questions are worth investigating, which evidence-collection
strategies have already been tried, and what happened when they were.

Stage 1 (current): schema and pure stores only, exercised in isolation.
Nothing here is wired into the scheduler, the event bus, or ``bootstrap.py``
yet — see docs/decisions for the staged rollout."""
