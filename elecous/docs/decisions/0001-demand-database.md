# ADR 0001 — Add demand.duckdb to the database set

**Status:** accepted · **Date:** 2026-07-03

## Context

Spec §10.14 lists eight example database files (observations, signals,
environment, mechanisms, knowledge, predictions, validation, dashboard) and
states the governing principle: *separate databases by responsibility*.
Spec §3.19 independently requires a Demand Intelligence Database holding
Research Episodes, baselines, spikes and controls, but §10.14's example list
does not name a file for it.

## Decision

Add `demand.duckdb` as a ninth domain database. Placing demand intelligence
inside `observations.duckdb` or `signals.duckdb` would violate §10.14's own
principle and couple unrelated failure domains.

## Consequences

`databases.domains` in `configs/elecous.yaml` lists nine domains. Everything
else follows §10.14 unchanged.
