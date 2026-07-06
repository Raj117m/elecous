# 17 — Known Limitations

*Ranked by impact on the product's stated purpose. Each traces to source or to
a measurement made this session.*

### 1. Narrow environmental coverage (HIGH) — measured
Only weather-family + media + economics + energy + calendar + air quality +
seismic are ingested. Social media, search trends, retail/marketplace ranking,
pricing/commodities, shipping/logistics, disease, agriculture, financial
markets are absent. Validated mechanisms are ~64% weather, ~36% media.
*Why*: only these connectors exist (`observation/connectors/`).
*Effort*: medium per source (one connector class each; `AutoConnector` can
absorb any json/csv/rss source without new code).
*Fix*: add high-signal connectors — Google Trends, a social-volume feed, a
commodity/price feed — prioritized by expected prediction contribution.

### 2. No resolved prospective outcomes yet (HIGH) — by design
64 predictions exist; none have passed their lead-time horizon, so real
precision/recall on *future* data is still unmeasured. Backtest is the only
current proxy. *Why*: the platform is young; resolution needs calendar time.
*Fix*: none — wait, or lean on historical replay for now.

### 3. Source discovery cannot find unknown organizations (MEDIUM) — ADR 0002
`expansion/source_discovery.py` autodiscovers sitemap/RSS only on domains
already in `KNOWN_PROVIDER_HOMEPAGES`. No live web search.
*Fix*: integrate a search API, or citation/hyperlink-graph expansion.

### 4. Connector evolution covers only structured formats (MEDIUM)
`connector_evolution.py` extracts json/csv/tsv/rss/atom/sitemap. HTML tables,
PDF, OpenAPI are classified but not extracted.
*Fix*: per-format extractors, or foundation-model-assisted extraction.

### 5. Factor-version storage growth (MEDIUM) — technical debt
Append-only `new_version()` accumulates rows in `environment.duckdb`.
*Fix*: periodic compaction of superseded versions, or a retention window.

### 6. Single-machine DuckDB write lock (LOW at current scale)
One writer per domain file. Fine for one continuous instance; blocks parallel
writers. *Fix*: only if multi-process writes are ever needed (they are not
today).

### 7. Manual is incomplete (LOW)
Docs 03–13, 15, 18 are skeletons with source pointers, not full prose.
*Fix*: complete per each skeleton's stated scope.
