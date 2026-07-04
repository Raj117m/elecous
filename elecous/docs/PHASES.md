# Implementation Phases

Built in the exact order the specification requires (§11.2-11.14): each
phase depends only on validated output from the previous one.

| Phase | Subsystem | Key modules |
|---|---|---|
| 1 | Infrastructure | `infrastructure/`, `storage/`, `scheduler/`, `governance/` |
| 2 | Observation Network | `observation/` |
| 3 | Demand Intelligence | `demand/` |
| 4 | Environmental Intelligence | `environment/`, `signals/` |
| 5 | Mechanism Intelligence | `mechanisms/` |
| 6 | Scientific Validation | `validation/` |
| 7 | Knowledge Engine | `knowledge/` |
| 8 | Prediction Engine | `prediction/` |
| 9 | Dashboard | `dashboard/` |
| 10 | Autonomous Evolution | `infrastructure/supervisor.py`, `expansion/` |
| 11 | Optimization | `infrastructure/cache.py`, storage tiering |
| 12 | Production | `scripts/cli.py`, `scheduler/registration.py` |

## Architectural decisions

Two points required a documented substitution for the literal specification
text, per the "closest scientifically equivalent solution" instruction:

- **[ADR 0001](decisions/0001-demand-database.md)** — a `demand.duckdb`
  database was added; §10.14's example list omits it despite §3.19 requiring
  a Demand Intelligence Database, and §10.14's own principle (separate
  databases by responsibility) requires the addition.
- **[ADR 0002](decisions/0002-expansion-candidate-pool.md)** — the Expansion
  Engine's source *discovery* step uses a declared candidate pool rather
  than live web search, since open-ended internet discovery inside the core
  pipeline would require embedding an LLM-driven agent in scientific
  reasoning, which Appendix B explicitly forbids. Everything after discovery
  (connector generation, sandbox evaluation, promotion) is fully automatic
  and demonstrated end-to-end against a real, previously unregistered public
  API (Open-Meteo Flood).

## Real data sources wired

| Source | Capability | License | Auth |
|---|---|---|---|
| Open-Meteo (forecast + archive) | weather | CC BY 4.0 | none |
| Open-Meteo Air Quality | air_quality | CC BY 4.0 | none |
| Open-Meteo Flood | hydrology | CC BY 4.0 | none |
| USGS FDSN | geophysical (earthquakes) | US public domain | none |
| NOAA/NWS Alerts | weather_hazard | US public domain | none |
| Wikimedia Pageviews | public_attention | CC0/public API | none |
| GDELT DOC 2.0 | media_attention | Open (GDELT terms) | none |
| Nager.Date | calendar (holidays) | MIT | none |
| World Bank Open Data | economics | CC BY 4.0 | none |
| US EIA | energy_demand | US public domain | free key |
| FRED | economics | FRED API terms | free key |

## Known scope boundaries

- Product-level demand signals beyond public attention (Wikipedia pageviews)
  and media volume (GDELT) — review velocity, inventory, ranking — require
  retailer-specific data partnerships not available as open public APIs;
  the demand engine's signal registry is built to accept them the moment a
  legal connector exists (spec 3.3's full candidate list).
- Business Value's "market size" field is `None` by design (see
  `mechanisms/engine.py::business_value`) — no free legal public dataset
  provides it; attention volume is used as a documented, honest proxy
  instead of inventing a number.
