# Level Provider — Universal Contract Specification

> **Version:** 1.0 — **Date:** 2026-08-02 — **Status:** Design only, no code

## The Problem

The system has multiple sources of price levels (ORB, prior-day extremes, pre-market extremes, OCL). Each source currently uses its own format. The Entry Engine must understand every format individually, coupling it to every provider. Adding a new provider means changing the Entry Engine.

## The Rule

Every Level Provider emits levels through one universal output contract. The Entry Engine consumes levels without knowing or caring which provider created them. A level is a level.

## Universal Level Record

Every level emitted by any provider contains exactly these fields and nothing else:

| Field | Type | Description |
|---|---|---|
| `price` | float | The level price. For zone-based levels, this is the near edge (the edge price approaches first). |
| `price_far` | float or null | The far edge of a zone. Null when the level is a single price line. |
| `direction` | string | `SUPPORT` or `RESISTANCE`. What the level acts as — not the trade direction. |
| `source` | string | Provider name. Fixed per provider, never changes. |
| `created_at` | int | Epoch milliseconds when the level became active. |

Five fields. No entry logic, no momentum, no scoring, no trade decisions. Those belong downstream.

## What Direction Means

`SUPPORT` — price is expected to bounce upward off this level. The level is below current price.

`RESISTANCE` — price is expected to reject downward off this level. The level is above current price.

Direction describes the level's role, not a trade signal. The Entry Engine decides what to do with it.

## What Source Means

A fixed string identifying the provider. The Entry Engine can filter, prioritize, or weight by source but never needs to parse it. Values for initial providers:

`ORB_HIGH` · `ORB_LOW` · `PMH` · `PML` · `PDH` · `PDL` · `OCL`

## Zone vs Line

If `price_far` is null, the level is a line (PDH, PDL, PMH, PML). If `price_far` is set, the level is a zone with thickness (ORB, OCL). The Entry Engine handles both — a line is simply a zone with zero width.

## Initial Providers

| Provider | Source | Price | Price Far | Direction | Notes |
|---|---|---|---|---|---|
| ORB 5m High | `ORB_HIGH` | ORB high | null | RESISTANCE | Computed from first 5 minutes |
| ORB 5m Low | `ORB_LOW` | ORB low | null | SUPPORT | Computed from first 5 minutes |
| Pre-Market High | `PMH` | PM high | null | RESISTANCE | From pre-market session |
| Pre-Market Low | `PML` | PM low | null | SUPPORT | From pre-market session |
| Previous Day High | `PDH` | Prior session high | null | RESISTANCE | From prior regular session |
| Previous Day Low | `PDL` | Prior session low | null | SUPPORT | From prior regular session |
| One Candle Level | `OCL` | Near wick edge | Far wick edge | SUPPORT or RESISTANCE | Placeholder — provider not yet implemented |

## What a Level Provider Does Not Do

A Level Provider does not evaluate momentum, score quality, decide entries, manage risk, filter by confluence, or determine trade direction. It emits levels. Everything else is someone else's job.
