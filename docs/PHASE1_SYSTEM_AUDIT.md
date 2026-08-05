# Phase 1 — System Audit

> **Date:** 2026-08-05
> **Status:** Complete — no code changes
> **Purpose:** Answer the 5 questions required before Phase 2

---

## Question 1: What levels does the engine calculate today?

**Only ORB.** The engine calculates ORB High and ORB Low via
`orb_builder.py`. No other level provider exists in executable code.

PDH/PDL are calculated in `backtest_server.py` (lines 1171–1182)
for chart display only — they are drawn as price lines on the Lab
chart but are never fed into the Break-and-Retest pipeline.

PMH/PML, Pivot Wick, and OCL do not exist anywhere in the codebase.

### Level calculation summary

| Level    | Calculated? | Where                     | Fed into B&R pipeline? |
|----------|-------------|---------------------------|------------------------|
| ORB_HIGH | Yes         | `orb_builder.py`          | Yes                    |
| ORB_LOW  | Yes         | `orb_builder.py`          | Yes                    |
| PDH      | Yes         | `backtest_server.py`      | **No** (chart only)    |
| PDL      | Yes         | `backtest_server.py`      | **No** (chart only)    |
| PMH      | No          | —                         | —                      |
| PML      | No          | —                         | —                      |
| PIVOT    | No          | —                         | —                      |
| OCL      | No          | —                         | —                      |

---

## Question 2: What is visible in the Lab UI?

The Lab UI (`lab/index.html`) exposes:

- **Level Source selector:** hidden field, auto-derived from
  Direction. LONG → `ORB_HIGH`, SHORT → `ORB_LOW`, BOTH → runs
  both. The user cannot manually select a different level source.

- **Chart overlays:**
  - ORB High / ORB Low lines (orange, thick)
  - ORB zone shading (area series)
  - PDH / PDL lines (brown, dashed) — display only
  - Break, Confirm, Exit markers
  - Trade entry/stop/target lines

- **Parameters exposed:** Direction, Symbol, Timeframe, ORB
  Duration, Displacement Bars, Consecutive ORB Closes, Wick Ratio,
  Body Ratio, Wick Penetration %, R:R target, Date range.

- **Level Source is NOT user-selectable** — it is derived
  automatically and shown as a read-only display field.

---

## Question 3: Where are break, displacement, retest, and entry candle coded?

Each stage is a separate module with a single public function:

| Stage              | Module                    | Function               | Parameter named `orb` |
|--------------------|---------------------------|------------------------|-----------------------|
| 1a. Session context| `session_context.py`      | `build_session_context`| No                    |
| 1b. ORB/Level      | `orb_builder.py`          | `build_orb`            | Produces it           |
| 2. Break           | `break_finder.py`         | `find_break`           | Yes                   |
| 3. Displacement    | `displacement_finder.py`  | `find_displacement`    | Yes                   |
| 3b. Seq validation | `sequence_validator.py`   | `validate_sequence`    | Yes (uses orb_high/orb_low) |
| 4. Retest window   | `retest_window.py`        | `find_retest_window`   | Yes                   |
| 5. Rejection/Entry | `rejection_finder.py`     | `find_rejection`       | Yes                   |

The orchestrator is `strategy_runner.py` → `_process_one_session()`,
which calls stages 1a → 1b → 2 → 3 → 3b → 4 → 5 in strict order.

---

## Question 4: Is ORB separable from the downstream logic?

**Almost completely yes.** This is the critical finding.

### What downstream stages actually read from the `orb` dict

Every downstream stage (break, displacement, retest, rejection)
reads **exactly 5 fields** from the `orb` dict:

```
orb["level_price"]        float   — the price to break/retest
orb["level_price_ticks"]  int     — same in ticks
orb["orb_candle_index"]   int     — index of last ORB bar (scan starts after this)
orb["orb_candle"]         dict    — for cross-check (time_ms match)
orb["date"]               str     — session date string
```

These fields are **generic**. They describe "a level at a price,
starting after a certain candle index." Nothing about ORB
specifically.

### The ONE exception: `sequence_validator.py`

This module reads `orb["orb_high"]` and `orb["orb_low"]` to check
for consecutive closes back inside the ORB band. This is
ORB-specific logic — the concept of "price returning inside the
ORB zone" only makes sense for ORB levels.

For non-ORB levels (PDH, Pivot, OCL), sequence invalidation would
need a different rule (e.g., consecutive closes back through the
broken level itself). This is the only place where ORB-specific
semantics leak into the downstream pipeline.

### Chart/visualization modules

`backtest_server.py`, `visual_review_exporter.py`,
`review_workspace.py`, and `visual_review_html.py` all reference
`orb_high_ticks` and `orb_low_ticks` for chart overlays. These are
display concerns, not pipeline logic. They would need to be made
conditional (show ORB zone only when the level source is ORB).

### Summary: Separation score

| Component                   | ORB-agnostic? | Effort to generalize          |
|-----------------------------|---------------|-------------------------------|
| `break_finder.py`           | **Yes**       | Zero — reads only level_price |
| `displacement_finder.py`    | **Yes**       | Zero — reads only level_price |
| `retest_window.py`          | **Yes**       | Zero — reads only level_price |
| `rejection_finder.py`       | **Yes**       | Zero — reads only level_price |
| `trade_plan_builder.py`     | **Yes**       | Zero — receives DetectionResult |
| `trade_outcome_evaluator.py`| **Yes**       | Zero — receives TradePlan     |
| `sequence_validator.py`     | **No**        | Needs ORB-specific branch     |
| `orb_builder.py`            | **No**        | This IS the ORB provider      |
| `strategy_runner.py`        | **Partial**   | Calls build_orb; needs to accept alternative level builders |
| `backtest_server.py`        | **Partial**   | PDH/PDL chart display exists; level_source UI locked to ORB |
| Chart/viz modules           | **Partial**   | ORB zone display is conditional |

---

## Question 5: What is needed for Phase 2 (make level selectable)?

Based on this audit, Phase 2 requires these changes:

### 5.1 — Rename the `orb` parameter (conceptual, optional)

The `orb` dict that flows through the pipeline is really a
"level result" — it contains `level_price`, `level_price_ticks`,
a start index, and a date. Renaming it to `level` in stages 2–5
would improve clarity but is a large diff. An alternative is to
keep the parameter name `orb` but document that it means "the
level output dict, regardless of provider." **Decision needed
from Max.**

### 5.2 — Extract a level builder interface

Create a dispatcher that, given `level_source` in config:
- `ORB_HIGH` / `ORB_LOW` → calls existing `build_orb()`
- `PDH` / `PDL` → calls a new `build_pdh_pdl_level()` (Phase 3)
- `PIVOT_WICK` → calls a new `build_pivot_level()` (Phase 5)
- etc.

The dispatcher returns a dict with the same 5 fields that
downstream stages already consume. No downstream changes needed.

### 5.3 — Handle `sequence_validator.py`

Two options:
- **Option A:** Skip sequence validation for non-ORB levels (since
  "consecutive closes inside the ORB" is meaningless for PDH).
- **Option B:** Generalize to "consecutive closes back through the
  broken level" for all providers.

Option A is simpler and preserves existing behavior for ORB.
**Recommended for Phase 2.**

### 5.4 — Make `level_source` selectable in the Lab UI

Change the hidden `pLevelSource` field to a dropdown with the
supported options. Initially: `ORB_HIGH`, `ORB_LOW`. Phase 3 adds
`PDH`, `PDL`. The server must accept the new values without
crashing.

### 5.5 — Conditional ORB zone display

Chart visualization of the ORB zone overlay should only render
when the level source is `ORB_HIGH` or `ORB_LOW`. For other
levels, the zone overlay should show the relevant level's zone
(or nothing for line levels).

---

## Architecture Diagram (current)

```
CSV Data
  ↓
parse_csv_candles / load_candles_for_timeframe
  ↓
split_into_sessions (date-keyed)
  ↓
Per session:
  ┌──────────────────────────────────────────────────┐
  │ session_context.build_session_context()           │ ← Stage 1a
  │   validates candles, sorts, assigns date          │
  ├──────────────────────────────────────────────────┤
  │ orb_builder.build_orb()                          │ ← Stage 1b
  │   finds ORB candle(s), computes orb_high/low     │    *** ORB-SPECIFIC ***
  │   selects level_price based on level_source      │
  │   returns "orb" dict                             │
  ├──────────────────────────────────────────────────┤
  │ break_finder.find_break(candles, orb, config)    │ ← Stage 2
  │   scans for first close beyond level_price       │    GENERIC (uses only level_price)
  ├──────────────────────────────────────────────────┤
  │ displacement_finder.find_displacement(...)        │ ← Stage 3
  │   counts bars staying beyond level_price         │    GENERIC
  ├──────────────────────────────────────────────────┤
  │ sequence_validator.validate_sequence(...)         │ ← Stage 3b
  │   checks consecutive closes inside ORB band      │    *** ORB-SPECIFIC ***
  ├──────────────────────────────────────────────────┤
  │ retest_window.find_retest_window(...)            │ ← Stage 4
  │   identifies retest contact candles              │    GENERIC
  ├──────────────────────────────────────────────────┤
  │ rejection_finder.find_rejection(...)             │ ← Stage 5
  │   evaluates Entry Candle geometry                │    GENERIC
  ├──────────────────────────────────────────────────┤
  │ detection_result_builder → trade_plan_builder    │
  │ → trade_outcome_evaluator                        │    ALL GENERIC
  └──────────────────────────────────────────────────┘
```

**Key finding:** Stages 2, 3, 4, 5 and the trade pipeline are
already fully generic. Only Stage 1b (level construction) and
Stage 3b (sequence validation) contain ORB-specific logic. The
refactor surface for Phase 2 is very small.

---

## Conclusion

The current system is in excellent shape for generalization.
The original JS-parity architecture accidentally created a clean
separation: the `orb` dict is effectively a generic "level result"
that downstream stages consume through exactly 5 fields. Only
`orb_builder.py` and `sequence_validator.py` are ORB-aware.

Phase 2 can be implemented with minimal risk by:
1. Creating a level builder dispatcher (new file, ~50 lines)
2. Making sequence validation conditional on level_source
3. Opening the Lab UI dropdown
4. All 2200+ existing tests continue to pass unchanged

No existing module needs to be rewritten. No frozen contract
is violated.

---

*END OF AUDIT*
