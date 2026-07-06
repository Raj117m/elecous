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

## Phase 2 — Multi-mode extraction implemented and measured (2026-07-06)

Implemented in `environment/signal_discovery/extractor.py`: spike (existing,
unchanged) + **trend** (significant, consistent slope over the full fetched
series — `np.polyfit` slope normalized by baseline scale, gated on
`|correlation| >= 0.6` to reject noisy zigzags) + **level** (window mean an
extreme percentile of the *baseline* distribution — not self-referential).
All three write into the same `phenomenon:direction:scope` signal-key format,
so pattern mining, factors, and mechanisms need zero changes.

**Two real bugs caught by measuring against live data before shipping**
(documented in source comments at the fix site):
1. First level-detection draft ranked the window against a distribution that
   included itself — self-referential, so any naturally-decaying series (a
   Wikipedia article's pageviews falling from an initial burst) always ranked
   its own tail as "extreme." Fixed to rank against the baseline period only.
2. Rank-only thresholding still flooded on sparse/near-zero baselines (100%
   of an all-zero baseline is trivially beaten by any nonzero value). Fixed
   by gating on the same baseline-scale magnitude (`change_z`) spike detection
   already uses — "extreme rank" now also means "a real amount of movement."

**Measured result** (live, real data, 2026-07-06): 177 total signal changes;
36 (20%) now carry corroborating trend/level evidence in addition to or
instead of a bare spike — e.g. `cold_stress:up:US` and `thermal_stress:up:ES`
now register via sustained trend even on days with no single-day spike.

**Honest limitation confirmed by direct measurement, not assumption**:
economics/energy phenomena still register zero signals. Root cause is
*not* extraction methodology — `eia_electricity` and `fred_series` are
**suspended** (missing API keys, never configured), and `worldbank_macro`,
though active, produced **zero observations in the last 58 days** in the
real database (confirmed via direct query) because real macro indicators
report annually/quarterly. No extraction mode can produce a signal from data
that does not exist yet. This is the external-data-not-software distinction
Phase 1's evidence already predicted, now confirmed with a direct query.
