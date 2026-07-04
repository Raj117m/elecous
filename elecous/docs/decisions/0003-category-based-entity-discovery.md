# ADR 0003 — Category-based Wikipedia discovery, not free-text search

**Status:** accepted · **Date:** 2026-07-04

## Context

Real validation runs against the initial 20-entity catalog (1-2 entities per
product category) showed the Skeptic Engine rejecting 100% of discovered
factors/mechanisms for `sampling_bias`: with only one entity per category,
no claim can be shown to generalize beyond that single product. The fix
required expanding entity coverage per phenomenon by an order of magnitude,
using only real, verifiable public evidence — never hand-typed guesses.

## Decision

Two candidate discovery strategies were implemented and compared against
real Wikipedia data before choosing:

**Free-text search** (`action=query&list=search`, article namespace) —
tested against the query `"portable cooling device"`. Top 15 results
included `Mobile device`, `Laptop`, `Computer hardware`, `Portable
application`, and `Mobile computing` — Wikipedia's relevance ranking
correctly matched the word "portable" to consumer electronics far more
often than to cooling products, because free-text relevance conflates every
sense of an ambiguous query term.

**Category-based discovery** (search the Category namespace for a real
existing category name, then list its real article members) — tested
against the same phenomenon. Searching the Category namespace for
`"cooling"` surfaced real, existing categories (`Category:Cooling
technology`, `Category:District cooling`, `Category:Cooling towers`).
Listing `Category:Cooling technology`'s real members returned `Air
conditioning`, `Ceiling fan`, `Attic fan`, `Chiller`, `Absorption
refrigerator` — zero false positives in spot checks across multiple
categories (cooling, camping equipment, ventilation).

## Quantitative comparison

| Method | False-positive rate (spot-checked) | Precision on first page |
|---|---|---|
| Free-text search, "portable cooling device" | 5 of 15 (33%) unrelated to cooling products | Low |
| Category search + membership, "cooling" → Category:Cooling technology | 0 of 20 (0%) unrelated | High |

## Decision

`EntityDiscoveryEngine.discover()` uses category-based discovery as the
primary path (`find_categories` → `category_members`) and falls back to
free-text search only when no matching real category exists for a
phenomenon keyword. Every candidate from either path is additionally
gated by a real, measured pageview-volume check (`real_median_pageviews`)
before being trusted — an entity with a plausible-sounding title but no
genuine measurable public attention contributes no evidence and is
discarded, never fabricated or assumed.

## Consequences

Running this pipeline across 16 phenomenon categories added 268 real,
individually pageview-verified entities (from an initial 20), all traceable
to the exact real category and search query that surfaced them
(`discovery_query`, `discovery_median_pageviews` fields in
`configs/entities.yaml`). A minority of discovered entities are real
category members that are technical/engineering concepts rather than
purchasable consumer products (e.g. "Auto-defrost", "Cascade
refrigeration") — these are genuine, verifiable Wikipedia articles with
real measured attention, not fabrications, but are weaker demand-signal
candidates than consumer products. No further filtering was applied beyond
the pageview-volume gate, since doing so would require subjective judgment
calls this ADR does not attempt to encode; this is recorded as an honest
quality caveat rather than silently smoothed over.
