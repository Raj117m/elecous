# ADR 0002 — Expansion Engine uses a declared candidate pool, not live web search

**Status:** accepted · **Date:** 2026-07-03

## Context

Spec §12.8 ("Expansion Engine") and §2.16 ("Source Discovery Engine")
describe ELECOUS continuously searching the open internet for new public
observation capabilities ("What public source measures campground usage?").
A static Python codebase has no legal or technical way to perform open-ended
internet discovery and identify licensing terms without a live web-search
and reasoning capability running as part of the platform itself — building
that would mean embedding an LLM-driven web agent inside the core science
pipeline, which Appendix B explicitly forbids ("LLMs should assist ELECOUS;
they should never replace scientific validation, mechanism discovery, or
knowledge").

## Decision

Implement everything the spec describes *after* discovery — connector
generation, the Observation Sandbox (schema stability, repeatability,
quality, freshness, performance, legal compliance), and promotion-only-on-
success — exactly as written. Discovery itself is represented by a declared,
human- or operator-curated candidate pool (`configs/candidate_sources.yaml`)
that the Expansion Engine matches against open Observation Requests from the
Gap Engine. This keeps the autonomous, evidence-gated pipeline fully real
and automatic while keeping "find new URLs on the internet" a human/curator
responsibility, consistent with §13.14 ("Humans become Scientific
Directors... Adding entirely new observation domains" is a named human
responsibility).

## Consequences

`ExpansionEngine.discover_candidates()` reads the candidate pool, not the
internet. A real new capability (Open-Meteo Flood / river discharge,
`open_meteo_flood`) is included as a genuine, previously-unregistered
example so the full discover → sandbox → promote pipeline is exercised
end-to-end with real data, not just described.
