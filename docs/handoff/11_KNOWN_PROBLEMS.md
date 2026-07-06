# 11 — Known Problems

*Every weakness found this session and in prior sessions, not hidden.
Ranked by real impact.*

### Environmental coverage is narrow and biased (HIGH — partially fixed)
Measured: validated mechanisms were 64% weather / 36% media / 0% else before
this session. Root cause was confirmed (spike-only extraction), and trend/
level modes were added and measured to activate 20% of real signals with
richer evidence. **Still true after the fix**: distinct phenomena firing in
live measurement remain `{cold_stress, media_attention, rainfall_intensity,
seismic_activity, thermal_stress, wind_intensity}` — economics/energy/
calendar still produce zero, confirmed by direct query to be a data-
availability problem (suspended credentials, real annual reporting cadence),
not an extraction bug. See `docs/manual/AUDIT_environmental_bias.md`.

### No resolved prediction outcomes (HIGH, by design, not a bug)
64 predictions exist; 0 resolved. `resolve_due()` correctly waits for the
real lead-time horizon to pass. This means the platform's real-world
precision/recall on *future* data is completely unmeasured — only the
historical backtest exists. Do not read the F1=0.631 benchmark number as
proof of future performance; it is a backtest score.

### Two bugs were self-caught mid-fix this session (documented so they aren't repeated)
1. Fixing the confidence deadlock made a dormant VALIDATED→TRUSTED path
   reachable via historical re-validation alone — this would have let
   mechanisms claim "trusted" without ever passing real future confirmation
   (a real overfitting/survivorship risk). Caught and capped before shipping.
2. The rolling benchmark sampled `list_latest()[:n]`, which orders by
   confidence — after the deadlock fix, confidence became correlated with
   the very thing being benchmarked (replay precision), so this would have
   silently cherry-picked top performers. Caught and re-pointed at the
   validated set, id-ordered.
**Lesson for future work**: any fix that changes what a "top" ordering means
should be re-checked against every consumer of that ordering, not just the
one you're fixing.

### Level-detection had two real bugs found by measuring against live data (fixed this session)
See `docs/manual/AUDIT_environmental_bias.md` Phase 2 section for the full
story — self-referential ranking, then rank-without-magnitude flooding.
**Lesson**: "does it run without error" is not "is the output meaningful" —
both bugs passed the type-checker and the test suite; only inspecting real
output caught them. Always eyeball real output of a new statistical method
before trusting its aggregate counts.

### Source discovery cannot find genuinely unknown organizations (MEDIUM, by design — ADR 0002)
Only autodiscovers sitemap/RSS on domains already in a hardcoded known-provider
list. No general web search integration exists or is planned without the
user providing a search API.

### Connector auto-generation covers structured formats only (MEDIUM)
HTML tables, PDF, OpenAPI are classified but never extracted. Real effort,
not yet justified by evidence of need.

### Storage growth from append-only versioning (MEDIUM, technical debt)
No compaction exists. Not urgent at 5.6GB/150-200GB budget but will need
addressing eventually (see 10_NEXT_STEPS.md #8).

### Single-writer DuckDB (LOW at current scale, but causes real friction)
Caused repeated "IO lock" failures this session whenever a script ran while
the dashboard was live. Not a bug — it's DuckDB's documented concurrency
model — but anyone continuing this project needs to internalize it
immediately (see 08_RUNNING_ELECOUS.md's warning box).

### 7 of 10 requested extraction modes not implemented (LOW-MEDIUM)
Regime, cyclic, momentum, correlation, divergence, persistence, composite
detection were requested but not built this session — explicit scope
decision to ship 2 well-measured modes over 7 unverified stubs. See
10_NEXT_STEPS.md #4 for why each needs real design work, not a quick add.

### Unresolved investigations
- Whether `worldbank_macro`'s real update cadence could ever populate the
  56-day window used for level/trend detection — not measured across a
  longer window; the 58-day gap might be normal or might indicate the
  connector itself needs a longer-interval refresh schedule. Worth checking
  `sources.yaml`'s update_frequency for this source against its real cadence.
- No assumption was made about production-readiness of the multi-mode
  extractor beyond the single real-time measurement taken this session
  (177 signals, one instant). It has not been run through a full historical
  replay to confirm it doesn't introduce false positives at scale — this is
  exactly Next Step #1.
