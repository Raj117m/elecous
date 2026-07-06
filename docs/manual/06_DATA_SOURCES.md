# 06 — Data Sources / Connectors

**Scope**: every connector's purpose, cadence, coverage, extracted variables,
weaknesses. **Read to complete**: `elecous/observation/connectors/*.py` and
`elecous/configs/sources.yaml` (per-source connector_config, backfill_days).

Verified connector inventory (`ls connectors/`): `open_meteo.py` (weather),
`open_meteo_flood.py` (flood/weather_hazard), `nws.py` (US weather alerts),
`usgs.py` (earthquakes/geophysical), `wikimedia.py` (pageviews/public_attention),
`gdelt.py` (news volume/media_attention), `holidays.py` (Nager/calendar),
`worldbank.py` (economics), `keyed.py` (EIA energy + FRED economics, needs key),
`auto.py` (AutoConnector — data-driven, for discovered sources), `base.py` (ABC).

Verified coverage bias (audit, this session): validated mechanisms driven
~64% by weather-family phenomena, ~36% media. Missing categories enumerated in
doc 17 §1.

Per-connector detail (rate limits, countries, reliability) — read each file's
module docstring + `collect()`; `sources.yaml` for cadence/coverage.
