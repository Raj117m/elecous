# Environmental Bias — Root Cause (Phase 1 evidence)

*Measured 2026-07-06 via live API + source inspection. Produced before any
change, per directive.*

## The measured bias

Validated-mechanism signal-slots: **weather 63.7%** (thermal_stress,
cold_stress, wind_intensity, rainfall_intensity), **media 36.3%**
(media_attention), **everything else 0%**.

## It is NOT a mapping gap

`elecous/configs/semantics.yaml` already maps **16 observation types across 9+
categories** to phenomena — including economics (`economic_growth`,
`price_pressure`, `labor_stress`), energy (`energy_demand`), calendar
(`calendar_event`, `calendar_proximity`), air quality (`air_pollution`),
seismic (`seismic_activity`), flood (`flood_risk`), weather hazard, and public
attention. The connectors for these exist and are ingesting (economics, energy,
holidays, air quality, earthquakes all have live rows).

**Yet 11 of those 16 mapped phenomena appear in zero validated mechanisms.**

## The real root cause: anomaly-based extraction selects for volatility

`elecous/environment/signal_discovery/extractor.py` emits a signal only when a
phenomenon's robust z-score clears `thresholds.yaml: change_z`. That
architecturally rewards **high-variance, frequently-spiking** phenomena and
ignores **slow-moving structural** ones:

- **Weather** (temperature, rain, wind) swings sharply and seasonally → fires
  constantly → forms many factors → many mechanisms. One connector fans into 4
  volatile phenomena.
- **Media** (news volume) spikes on events → also volatile → survives.
- **Economics** (GDP, inflation, unemployment), **energy demand**, **holidays**
  change slowly or cyclically → rarely produce a sharp z-score anomaly → rarely
  become signals → never reach the mechanism layer.

So the convergence on weather is **not** missing connectors, poor weighting, or
missing causal reasoning. It is that the **signal-extraction stage is an
anomaly detector**, and only weather + media are anomaly-rich. Slow structural
indicators need a *different* extraction mode (level/trend/regime, not spike).

## Targeted fixes this implies (not yet implemented)

1. **Add trend/level/regime extraction** alongside spike extraction, so
   slow-moving economics/energy/calendar phenomena can become signals via
   sustained change or threshold crossing rather than sharp z-scores.
   *This is the single highest-leverage change* — it activates 11 already-mapped,
   already-ingested phenomena with no new connectors.
2. **Then** add genuinely new anomaly-rich categories (search trends, social
   volume, retail ranking) which the current extractor can already handle.
3. Weighting/reasoning changes would **not** help — the bias is upstream of
   them, at the point where observations do or do not become signals.

## Honest scope note

Phases 2–10 of the V4 directive (structured taxonomy UI, source-expansion
implementation, per-product pages, decision-layer + full dashboard redesign,
self-analysis-by-category) are a multi-week build. This document delivers what
Phase 1 explicitly demanded — **evidence before change** — and pinpoints the
one architectural fix (multi-mode extraction) that most directly reduces the
measured 64/36/0 bias. The remaining weaknesses split cleanly: extraction mode
is a *software* fix; broader categories (social, search, retail, pricing,
logistics, disease, agriculture, finance) require *external data sources* that
do not yet exist in the platform.
