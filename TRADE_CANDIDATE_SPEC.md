# Trade Candidate — Data Object Specification

> **Version:** 1.0 — **Date:** 2026-08-02 — **Status:** Design only, no code

## What It Is

A Trade Candidate is a frozen data record created at the moment a valid Max Entry Candle is detected at a level. It captures everything known at the instant of entry and nothing more. It is not a trade, not an order, not an approval. It is a fact: this entry happened at this level at this time.

## When It Is Created

A Trade Candidate exists only when both conditions are true:

1. A Level Provider has emitted a level.
2. The Entry Candle Engine has returned `entry_detected = true` for a bar at that level.

No other path creates a Trade Candidate. No manual override, no external signal, no scoring threshold.

## Fields

| Field | Type | Description |
|---|---|---|
| `candidate_id` | string | Unique identifier. Generated at creation. |
| `provider_name` | string | Source name from the Level Provider (e.g. `ORB_HIGH`, `PDL`, `OCL`). Stored, never interpreted. |
| `level_id` | string | Unique identifier of the level that triggered the entry. |
| `direction` | string | `LONG` or `SHORT`. From the Entry Candle Engine output. |
| `entry_timestamp` | int | Epoch milliseconds of the entry bar. From the Entry Candle Engine output. |
| `entry_price` | float | Close of the entry bar. From the Entry Candle Engine output. |
| `entry_bar_index` | int | Ordinal position of the entry bar within the session. |
| `level_price` | float | Near edge of the level. From the Level Provider output. |
| `level_price_far` | float or null | Far edge of the level zone, or null for a line. |
| `stop_reference` | string | `ENTRY_CANDLE` or `FULL_ZONE`. From the Entry Candle Engine output. |
| `stop_price` | float | The price at which the stop is placed. Derived mechanically: for `LONG`, the low of the entry bar (`ENTRY_CANDLE`) or the far edge of the zone (`FULL_ZONE`). For `SHORT`, the mirror. |

Eleven fields. Every field is populated at creation. No field is ever modified after creation.

## What It Does Not Contain

No target price. No R-multiple. No quality score. No approval status. No position size. No account reference. No outcome. No P&L.

These belong to downstream modules that receive the Trade Candidate as input. The Trade Candidate does not know what happens next.

## Immutability

Once created, a Trade Candidate is frozen. Downstream modules read it. They never write to it. If a Policy Engine rejects the candidate, that rejection is a separate record that references the candidate's `candidate_id`. The candidate itself does not change.

## Provider Blindness

Every downstream consumer receives the same object with the same fields in the same format regardless of which provider created the level. A Trade Candidate from an ORB level is structurally identical to one from a Previous Day level, a Pre-Market level, an OCL level, or any provider that does not yet exist. The `provider_name` field is metadata for analysis, not a routing key.

## Who Consumes It

Every future module that needs to know about an entry reads a Trade Candidate: Policy Engine, Backtester, Review Workspace, Statistics, Risk Engine. They all receive the same object. No special cases, no provider-specific variants, no version branches.
