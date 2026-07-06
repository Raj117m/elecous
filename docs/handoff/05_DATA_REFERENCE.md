# 05 — Data Reference (Connectors)

*Verified: `elecous/observation/connectors/*.py`, `elecous/configs/sources.yaml`,
live registry query 2026-07-06.*

| Connector | Capability | Status | Countries | Notes |
|---|---|---|---|---|
| `open_meteo.py` | weather | active | global (per configured location) | Archive API, one request per location covers full history |
| `open_meteo_flood.py` | weather_hazard | active | global | river discharge / flood risk |
| `nws.py` | weather (US alerts) | active | US | real-time alert severity |
| `usgs.py` | geophysical | active | global | earthquake magnitude |
| `wikimedia.py` | public_attention | active | global (per-article) | pageviews; deep-backfilled to 2015-11 this session |
| `gdelt.py` | media_attention | active | global (per-entity query) | news volume; deep-backfilled to 2017-06 this session via resumable chunking |
| `holidays.py` | calendar | active | multi | Nager.Date, keyless |
| `worldbank.py` | economics | active | multi | GDP/inflation/unemployment — **but 0 real observations in the last 58 days** (annual/quarterly reporting lag) |
| `keyed.py` (EIA) | energy | **suspended** | US | needs `eia_api_key`, never configured |
| `keyed.py` (FRED) | economics | **suspended** | US | needs `fred_api_key`, never configured |
| `auto.py` | any (data-driven) | — | — | `AutoConnector`: generic connector for auto-discovered json/csv/rss/atom/sitemap sources |

## Environmental categories present vs. absent

**Present** (real, ingesting): weather, weather_hazard, air_quality,
geophysical, calendar, economics (sparse), energy (suspended), media,
public_attention.

**Absent entirely** (need new connectors — external data, not software):
social media, search trends (Google Trends), retail/marketplace ranking,
pricing/commodities, shipping/logistics, disease/healthcare, agriculture,
financial markets, sports/entertainment, population, technology, regulation,
housing/construction, crime.

## The measured bias (see AUDIT doc for full analysis)

Validated-mechanism signal-slots: weather-family ≈64%, media ≈36%, else ≈0%.
Root cause was spike-only extraction (fixed this session, partially — trend/
level added). Economics/energy remain at zero due to suspended credentials +
real reporting-lag sparsity, confirmed by direct query, not extraction design.

## Semantic mapping (`configs/semantics.yaml`)

16 observation types mapped to phenomena — already covers more categories
than currently produce mechanisms (this is the smoking gun for the audit's
"not a mapping gap" finding):
```
temperature_max_c → thermal_stress       precipitation_sum_mm → rainfall_intensity
temperature_min_c → cold_stress          windspeed_max_kmh → wind_intensity
pm2_5_ugm3 → air_pollution                earthquake_magnitude → seismic_activity
weather_alert_severity → weather_hazard   river_discharge_m3s → flood_risk
pageviews_daily → public_attention        media_volume_pct → media_attention
public_holiday → calendar_event           days_to_next_holiday → calendar_proximity
electricity_demand_mwh → energy_demand    gdp_growth_pct → economic_growth
inflation_pct → price_pressure            unemployment_pct → labor_stress
```

## Adding a new source

1. If it's json/csv/rss/atom/sitemap, `AutoConnector` may handle it via
   `expansion/connector_evolution.py` — no new code needed.
   Otherwise: subclass `observation/connectors/base.py::Connector`.
2. Add a `phenomena` mapping entry in `configs/semantics.yaml` if it's a new
   observation type.
3. Register in `configs/sources.yaml`.
4. Real historical acquisition audit note (see prior session): for catalogs
   under ~10K entities, per-entity aggregation APIs beat bulk distribution by
   3-4 orders of magnitude on transfer/requests/disk (measured for GDELT and
   Wikimedia this session). Prefer API-style sources; bulk (e.g. BigQuery)
   only pays off at much larger scale.
