# 12 — AI Continuation Prompt

*A self-contained prompt for a future AI agent (or human) with no access to
prior conversation history. Everything below is verifiable against the
repository at commit `2f1aed0` (or later).*

---

## Copy-paste starting prompt for a new AI session

```
You are continuing development on ELECOUS, a scientific demand-intelligence
platform at ~/ELECOUS. Read docs/handoff/01_CURRENT_STATE.md through
11_KNOWN_PROBLEMS.md before doing anything else — they are verified against
the actual repository, not summarized from memory. Do not assume prior
conversation exists; there is none for you. Everything you need is either
in those docs or discoverable by reading source.

CONSTITUTION (non-negotiable, enforced in code at elecous/governance/constitution.py):
1. No synthetic data, ever. Every observation must have real provenance
   (a real source_id, access_method, and URL). Never fabricate an
   observation to make a test pass or a demo look good.
2. No lifecycle promotion without passed validation. Never weaken a
   validation threshold just to make more things pass — if validation
   blocks progress, the fix is better evidence/features/reasoning, or a
   threshold change JUSTIFIED BY REAL MEASUREMENT (not convenience).
3. No future leakage in historical replay.

EVIDENCE DISCIPLINE (the pattern that made this session's work trustworthy):
Before claiming ANY improvement, measure the real "before" number. After
implementing, measure the real "after" number. State both. If a measurement
surprises you (an optimization made something slower, a fix produced a
flood of noise, a "fix" revealed a NEW bug), investigate and report that
honestly rather than hiding it — several of the most valuable findings this
session were bugs caught in the act of measuring your own fix, not before.

OPERATIONAL WARNING: DuckDB is single-writer per domain file. If you try to
build_stack() while `elecous run` or the dashboard is already running, you
will get an IO lock error. Either query through the live HTTP API
(http://127.0.0.1:8321/api/...) or stop the running process first.

Current state, verified: 1,702,818 real observations (2015-2026), 2,382
episodes (every year 2017-2026), 147 validated mechanisms, 64 predictions
(0 resolved yet — by design, they haven't hit their lead-time horizon),
benchmark F1 0.631 vs baseline 0.626. Full detail in 01_CURRENT_STATE.md.

The single biggest open weakness: environmental coverage is measured at
~64% weather / ~36% media / ~0% everything else. Root cause is documented
in docs/manual/AUDIT_environmental_bias.md — read it before proposing any
fix, because the obvious-looking fixes (reweighting, more weather sources)
are NOT what the evidence points to.

Your first action should be to read the handoff docs, then run the test
suite (unset ELECOUS_DATA_DIR && .venv/bin/python -m pytest elecous/tests
-m "not network" -q — expect 107 passed) to confirm the repository state
matches what's documented, then consult docs/handoff/10_NEXT_STEPS.md for
prioritized work.
```

---

## What ELECOUS is (for a reader who has never seen it)

A platform that ingests real public observations (weather, news volume,
economic indicators, etc.), discovers which environmental conditions
*causally precede* demand spikes in real historical data, forms and
validates causal "mechanisms," and predicts future demand increases with
evidence attached — never guessing, only claiming what real historical
evidence supports.

## How it works (one paragraph)

Real observation → statistical signal (did this thing change meaningfully?)
→ episode (did demand actually spike?) → factor (which signal combinations
preceded spikes more than chance?) → mechanism (a causal hypothesis with a
behavioural chain) → six-check validation gate + confidence floor → matched
against live world state → prediction, with evidence → automatically
resolved against reality after its lead-time window → the outcome updates
future confidence. See `docs/handoff/02_COMPLETE_ARCHITECTURE.md` for
diagrams.

## Design philosophy

1. **Real evidence over appearance.** A dashboard that fabricates "activity"
   to look alive is worse than one that honestly shows "quiet, next task at
   14:32." A benchmark that cherry-picks its sample to look good is worse
   than an honest below-baseline number.
2. **Measure before and after every change.** Not "should be faster" —
   actual numbers, same benchmark, before and after.
3. **Root-cause before patching.** The weather bias could have been "fixed"
   by reweighting mechanisms toward non-weather signals — that would have
   been fake, since the real problem was upstream (extraction never
   produced non-weather signals in the first place). Always find where in
   the pipeline a capability is actually missing.
4. **Small, additive, reversible changes.** Migrations are always
   `IF NOT EXISTS`. New extraction modes write into the existing signal-key
   format rather than requiring downstream schema changes.
5. **Honest scope-cutting.** When a directive asks for 10 things and only 2-3
   can be done with real rigor in the available time, do those 2-3 well and
   say plainly what wasn't done and why — don't ship 10 shallow stubs.

## Repository conventions

- Tests: `unset ELECOUS_DATA_DIR`, use `isolated_config` fixture, network
  tests marked `@pytest.mark.network` and excluded by default.
- Commits: end with `Co-Authored-By: <model name> <noreply@anthropic.com>`.
  Only commit when the user asks (or when continuing an already-agreed
  pattern of "commit after each verified change" — check recent git log for
  the user's actual preference in a live session).
- Comments explain *why* (a non-obvious constraint, a bug that was fixed and
  why the fix looks the way it does) — not *what* the code visibly does.
- No new architecture/abstraction without evidence it's needed. This
  codebase has explicit history of directives demanding large speculative
  rebuilds; the discipline that survived was always "smallest real fix
  backed by a measurement," not "biggest possible reimplementation."

## Current priorities (see 10_NEXT_STEPS.md for full detail)

1. Run `scripts/rerun_pipeline.py` on the 2017+ corroborated history + new
   multi-mode extractor, measure real before/after mechanism/category counts.
2. Get real signal from economics/energy (needs the user to provide API
   keys — cannot be done by an agent alone).
3. Add one genuinely new, legally-accessible, real environmental category.

## How to verify this document is still accurate

Everything above should match:
```bash
git log --oneline -5
unset ELECOUS_DATA_DIR && .venv/bin/python -m pytest elecous/tests -m "not network" -q
curl -s http://127.0.0.1:8321/api/activity   # if the dashboard is running
```
If reality has diverged from this document, trust reality and update the
document — do not act on a memory of what used to be true.
