# Max Entry Candle Engine — Interface Specification

> **Version:** 1.0 — **Date:** 2026-08-02 — **Status:** Design only, no code

## The Question It Answers

Given a level and a candle: did this candle produce a valid Max Entry Candle at this level?

Nothing else. The engine does not know where the level came from, does not evaluate momentum, does not score quality, does not manage the trade. It receives a level, watches candles, and says yes or no.

## Input

| Field | Type | Description |
|---|---|---|
| `price` | float | Near edge of the level (from Level Provider). |
| `price_far` | float or null | Far edge of the level, or null for a line. |
| `direction` | string | `SUPPORT` or `RESISTANCE` (from Level Provider). |
| `bar` | candle | A single completed price bar: open, high, low, close, timestamp. |

The engine evaluates one bar at a time against one level at a time. The caller decides which levels to check and when. The engine has no memory between calls.

## Output

| Field | Type | Description |
|---|---|---|
| `entry_detected` | bool | True if this bar is a valid Max Entry Candle at this level. |
| `entry_direction` | string | `LONG` or `SHORT`. Derived mechanically from the level direction: `SUPPORT` → `LONG`, `RESISTANCE` → `SHORT`. |
| `entry_bar_timestamp` | int | Epoch milliseconds of the bar. Echoed from input. |
| `stop_reference` | string | `ENTRY_CANDLE` or `FULL_ZONE`. Which stop mode applies. |
| `entry_price` | float | The close of the entry bar. |

When `entry_detected` is false, the remaining fields are null.

## Responsibilities

**The engine does:**

- Determine whether the bar touched the level (price or zone).
- Determine whether the bar rejected in the correct direction.
- Report the stop reference mode.
- Return a complete, self-contained entry record.

**The engine does not:**

- Know which provider created the level.
- Evaluate momentum before or after the bar.
- Score the quality of the entry.
- Decide position size, target, or risk.
- Track whether the level has been tested before.
- Filter by time of day, session, or market conditions.

## How Direction Maps

The level's direction determines what rejection looks like:

| Level direction | Expected rejection | Entry direction |
|---|---|---|
| `SUPPORT` | Bar touches level, closes above it | `LONG` |
| `RESISTANCE` | Bar touches level, closes below it | `SHORT` |

The mapping is mechanical and fixed. The engine does not override it.

## Zone vs Line Handling

If `price_far` is null, the level is a line. The bar must touch that exact price. If `price_far` is set, the bar must enter the zone (between `price` and `price_far`). The "touched the level" test is the only place this distinction matters. Everything else is identical.

## What Is Not Defined Yet

The internal geometry rules that determine whether a bar constitutes a valid rejection are not specified in this document. They will be defined separately through discovery. This specification freezes only the interface — what goes in, what comes out, and the boundary of responsibility.
