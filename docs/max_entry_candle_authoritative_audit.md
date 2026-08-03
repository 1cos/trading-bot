# Max Entry Candle — Authoritative Audit

**STATUS: AUDIT ONLY — NOT YET APPROVED AS FINAL STRATEGY SPEC**

*Generated from codebase analysis on 2026-08-03.*
*Source of truth: executable Python code and passing tests, not Markdown docs.*

---

## 1. Terminology

The engine uses multiple terms. Today they all refer to **the same single candle**:

| Term | Where used | Meaning |
|---|---|---|
| **confirmation candle** | `rejection_finder.py`, `visual_review_exporter.py`, Lab UI | The candle selected by Stage 5 |
| **confirmation_bar** | `DetectionResult` contract, `trade_plan_builder.py` | Same candle, frozen as a `Bar` dataclass |
| **rejection candle** | `rejection_finder.py` docstring | Same candle (must show "rejection" geometry) |
| **signal candle** | `trade_plan_builder.py` (`BREAK_OF_SIGNAL_BAR` entry model) | Same candle (entry taken on break of its H/L) |
| **retest candle** | Informal use only | Same candle (it's the one that retests the level) |
| **entry candle** | Not used in code | The confirmation candle determines entry price |

**There is no separate "retest candle" vs "rejection candle" vs "confirmation candle".** A single candle must simultaneously: (a) contact the level, (b) show rejection geometry, and (c) serve as confirmation for entry. The first candle in the retest window that satisfies all conditions is selected; scanning stops immediately.

### Which candle determines what

| Decision | Candle | Stage |
|---|---|---|
| Entry price | confirmation_bar.close (CONFIRMATION_CLOSE model) | Trade Plan Builder |
| Stop price | confirmation_bar.low (LONG) / confirmation_bar.high (SHORT) | Trade Plan Builder |
| Geometry validation | Same candle | Rejection Finder (Stage 5) |

---

## 2. Full Pipeline Sequence

```
Stage 1: Session Context    → file: session_context.py      → build_session_context()
         Input: raw candles, config
         Output: sorted candles, date, validation
         Gate: candles exist and span one calendar date

Stage 2: ORB Builder         → file: orb_builder.py          → build_orb()
         Input: candles, session_context, config
         Output: orb_high, orb_low, level_price, orb_candle_index
         Gate: enough candles to fill ORB window

Stage 3: Break Finder        → file: break_finder.py         → find_break()
         Input: candles, orb, config
         Output: break_candle_index, break_candle
         Gate: LONG: close > level_price; SHORT: close < level_price

Stage 3b: Displacement       → file: displacement_finder.py  → find_displacement()
          Input: candles, orb, break_result, config
          Output: displacement_window, first_retest_contact_index
          Gate: min_displacement_bars consecutive bars fully beyond level (default 3)

Stage 4: Retest Window       → file: retest_window.py        → find_retest_window()
         Input: candles, orb, break_result, displacement_result, config
         Output: retest_window (all candles from first contact to end of session)
         Gate: displacement result OK

Stage 5: Rejection Finder    → file: rejection_finder.py     → find_rejection()
         Input: candles, orb, break_result, displacement_result, retest_result, config
         Output: confirmation_candle, geometry, failed_retests
         Gate: first candle passing ALL geometry thresholds (see §3)

Stage 6: Trade Plan          → file: trade_plan_builder.py   → build_trade_plan()
         Input: detection_result, config
         Output: entry_price, stop_price, risk, targets
         Gate: entry strictly better than stop

Stage 7: Outcome Evaluation  → file: trade_outcome_evaluator.py → evaluate_trade_outcome()
         Input: post-confirmation candles, trade_plan, detection_result
         Output: outcome (TARGET_HIT / STOPPED / OPEN / etc.)
```

---

## 3. Confirmation Candle Geometry — LONG

### Contact Gate (the ONLY contact requirement)

```
candle.low <= level_price       (raw float comparison, <= means equality counts)
```

If `candle.low > level_price`: the candle is **skipped entirely** — not evaluated, not recorded as a failed retest. It simply does not exist for Stage 5.

**There is no minimum penetration gate.** A candle that merely touches the level (low == level_price, penetration = 0 ticks) passes the contact gate.

### Geometry Formulas

All arithmetic is performed in ticks (integer) via `price_to_ticks()`.

```
Given: LONG confirmation candle with O, H, L, C (all in ticks), level L_t

range_ticks     = H - L
body_ticks      = |C - O|

rejection_wick  = min(O, C) - L          ← the LOWER WICK of the candle
opposite_wick   = H - max(O, C)          ← the UPPER WICK

wick_ratio      = rejection_wick / range  ← lower wick as % of total range
body_ratio      = body / range            ← body as % of total range
fcl             = (C - L) / range         ← favorable close location

penetration     = max(0, L_t - L)         ← how far below level (REPORTED only)
close_beyond    = C - L_t                 ← close distance above level (REPORTED only)
```

### Textual Diagram (LONG)

```
    H ─────── ┐
              │ opposite_wick (upper wick)
  max(O,C) ── ┤
              │
              │ body
              │
  min(O,C) ── ┤
              │
              │ rejection_wick (lower wick)
              │         ← THIS is what wick_ratio measures
    L ─────── ┘
              ·
   level ─ ─ ─ ─ ─ ─   ← ORB High
              ·
              · penetration = level - L (if L < level)
```

### Three Qualification Thresholds

| Rule | Formula | Default | Gating? |
|---|---|---|---|
| `rejection_wick_ratio` | `rejection_wick / range` | ≥ 0.47 | **YES** |
| `body_ratio` | `body / range` | ≤ 0.40 | **YES** |
| `favorable_close_location` | `(C - L) / range` | ≥ 0.80 | **YES** |
| `penetration_through_level` | `max(0, level - L)` | — | **NO** (reported only) |
| `close_beyond_level` | `C - level` | — | Only if `min_close_beyond_level_ticks` set (default None) |

All three must pass simultaneously. Zero-range candles (doji with H==L) automatically fail.

### Numeric Example (LONG, passes)

```
Level = 100.00 (10000 ticks, tick_size = 0.01)

Candle: O=100.50  H=101.00  L=99.80  C=100.80

range       = 10100 - 9980 = 120 ticks
body        = |10080 - 10050| = 30 ticks
rej_wick    = min(10050, 10080) - 9980 = 70 ticks
opp_wick    = 10100 - max(10050, 10080) = 20 ticks

wick_ratio  = 70/120 = 0.583  ≥ 0.47 ✓
body_ratio  = 30/120 = 0.250  ≤ 0.40 ✓
fcl         = (10080 - 9980)/120 = 0.833  ≥ 0.80 ✓
penetration = max(0, 10000 - 9980) = 20 ticks (reported, not gated)
close_beyond = 10080 - 10000 = 80 ticks

→ QUALIFIES
```

### Numeric Example (LONG, fails — body too large)

```
Level = 100.00

Candle: O=99.90  H=101.00  L=99.50  C=100.80

range       = 150, body = 90, rej_wick = 40, opp_wick = 20

wick_ratio  = 40/150 = 0.267  < 0.47 ✗
body_ratio  = 90/150 = 0.600  > 0.40 ✗
fcl         = (10080-9950)/150 = 0.867  ≥ 0.80 ✓

→ FAILS (REJECTION_WICK_RATIO_TOO_LOW, BODY_RATIO_TOO_HIGH)
```

---

## 4. Confirmation Candle Geometry — SHORT

Exact directional mirror of LONG.

### Contact Gate

```
candle.high >= level_price      (raw float comparison, >= means equality counts)
```

### Geometry Formulas

```
Given: SHORT confirmation candle with O, H, L, C (ticks), level L_t

range_ticks     = H - L
body_ticks      = |C - O|

rejection_wick  = H - max(O, C)          ← the UPPER WICK
opposite_wick   = min(O, C) - L          ← the LOWER WICK

wick_ratio      = rejection_wick / range  ← upper wick as % of range
body_ratio      = body / range
fcl             = (H - C) / range         ← close near the low is favorable

penetration     = max(0, H - L_t)         ← how far above level (REPORTED only)
close_beyond    = L_t - C                 ← close distance below level
```

### Textual Diagram (SHORT)

```
    H ─────── ┐
              │ rejection_wick (upper wick)
              │         ← THIS is what wick_ratio measures
  max(O,C) ── ┤
              │ body
  min(O,C) ── ┤
              │ opposite_wick (lower wick)
    L ─────── ┘
              ·
   level ─ ─ ─ ─ ─ ─   ← ORB Low
              ·
              · penetration = H - level (if H > level)
```

### Numeric Example (SHORT, passes — QQQ 2026-07-27)

```
Level = 689.19 (68919 ticks)

Candle: O=688.67  H=689.22  L=688.46  C=688.58

range       = 68922 - 68846 = 76 ticks
body        = |68858 - 68867| = 9 ticks
rej_wick    = 68922 - max(68867, 68858) = 55 ticks
opp_wick    = min(68867, 68858) - 68846 = 12 ticks

wick_ratio  = 55/76 = 0.724  ≥ 0.47 ✓
body_ratio  = 9/76  = 0.118  ≤ 0.40 ✓
fcl         = (68922-68858)/76 = 0.842  ≥ 0.80 ✓
penetration = max(0, 68922-68919) = 3 ticks
close_beyond = 68919 - 68858 = 61 ticks

→ QUALIFIES (wick enters 3 ticks into ORB zone)
```

---

## 5. Parameter Semantics

### Minimum Wick 47% (`rejection_wick_ratio_min = 0.47`)

- **Numerator**: rejection wick size in ticks = `min(O,C) - L` (LONG) or `H - max(O,C)` (SHORT)
- **Denominator**: total candle range in ticks = `H - L`
- **Formula**: `rejection_wick / range >= 0.47`
- **What it measures**: the proportion of the candle that is **lower wick** (LONG) or **upper wick** (SHORT) — i.e., **candle shape**
- **What it does NOT measure**: how deeply the wick penetrates into the ORB zone. A candle with a long lower wick that stays entirely above the level satisfies this ratio but would be skipped by the contact gate. Conversely, a candle barely touching the level with a long wick passes both.

### Maximum Body 40% (`body_ratio_max = 0.40`)

- **Numerator**: absolute body size = `|C - O|` in ticks
- **Denominator**: total candle range = `H - L`
- **Formula**: `body / range <= 0.40`
- **What it measures**: how much of the candle is body vs wick
- **What it does NOT measure**: distance of the body from the level

### Favorable Close Location (`FAVORABLE_CLOSE_LOCATION_MIN = 0.80`)

- **Value**: 0.80 (hardcoded, not exposed to UI)
- **LONG formula**: `(C - L) / (H - L)` — close must be in the upper 20% of the range
- **SHORT formula**: `(H - C) / (H - L)` — close must be in the lower 20% of the range
- **Guarantees**: the close is on the "right side" — for LONG, close is near the high; for SHORT, close is near the low

### Min Close Beyond Level (`min_close_beyond_level_ticks`)

- **Default**: `None` (disabled)
- **When set**: close must be at least N ticks beyond the level
- **Semantics**: LONG: `close - level >= N`; SHORT: `level - close >= N`
- **Difference from "body completely outside"**: this checks only the close, not the open. The open could be inside the ORB zone.

---

## 6. Retest Contact — The Critical Analysis

### What proves the candle retested the ORB?

**LONG**: `candle.low <= level_price`

This is a `<=` comparison on raw float prices. Equality counts as contact.

**SHORT**: `candle.high >= level_price`

Same: `>=`, equality counts.

### Breakdown of contact types

| Scenario (LONG) | Contact? | Penetration ticks | Engine behavior |
|---|---|---|---|
| low = level - 0.05 | YES | 5 | Evaluated, may qualify |
| low = level | YES | 0 | Evaluated, may qualify |
| low = level + 0.01 | **NO** | — | **Skipped entirely** |
| low = level + 0.00001 | **NO** | — | Skipped (float > level) |

### Does the engine measure % of wick inside the ORB?

**No.** The engine computes:
- `rejection_wick_ticks`: size of the lower wick (LONG) — this is a **candle shape** metric
- `penetration_through_level_ticks`: how far the low goes below the level — this is **reported** but never used as a gate

There is no metric for "what percentage of the rejection wick is inside the ORB zone." These are fundamentally different:

```
                                    ← rejection_wick measures THIS entire segment
  min(O,C) ──┐                     
              │ part above level    ← no metric for this portion
   level ─ ─ ┤ ─ ─ ─ ─ ─ ─ ─      
              │ penetration         ← penetration measures only THIS portion
      L ──────┘                     
```

A candle can have `rejection_wick = 50 ticks` but `penetration = 2 ticks`, meaning 96% of the wick is above the level. The engine accepts this as long as the three shape ratios pass.

---

## 7. Entry and Stop

### Entry Model (active): `CONFIRMATION_CLOSE`

```
LONG:   entry_price = confirmation_bar.close + entry_buffer_ticks
SHORT:  entry_price = confirmation_bar.close - entry_buffer_ticks
```

Entry is on the close of the confirmation candle. Not on a break of the next candle.

### Stop Price

```
LONG:   stop_price = confirmation_bar.low - stop_buffer_ticks
SHORT:  stop_price = confirmation_bar.high + stop_buffer_ticks
```

Stop is at the extreme of the confirmation candle (the wick tip).

### Risk Scenario Analysis

Can the current logic produce the scenario Max described (wick barely touches, body far away, stop near level, next candle sweeps stop)?

**Yes.** Consider LONG:

```
Level = 100.00
Candle: O=100.40  H=100.60  L=100.00  C=100.50

range = 60, rej_wick = min(10040,10050)-10000 = 40
wick_ratio = 40/60 = 0.667 ✓, body_ratio = 10/60 = 0.167 ✓, fcl = 50/60 = 0.833 ✓
penetration = 0 ticks (wick just touches)

Entry = 100.50 (close), Stop = 100.00 (low)
Risk = 50 ticks

Next candle dips to 99.99 → stop hit at 100.00
Then rallies to 101.50 → would have been +2R
```

This is a legitimate concern. The stop sits exactly at the ORB level with zero margin.

---

## 8. Tests That Freeze Confirmation Geometry

| Test file | What it proves |
|---|---|
| `test_rejection_finder.py` (44 tests) | All three thresholds independently gating; SHORT mirror; at/below/above boundary values; zero-range; failed retest tracking; first-qualifying-wins scan order |
| `test_configurable_wick_body.py` (28 tests) | Configurable wick_ratio_min and body_ratio_max; raising/lowering thresholds changes results; LONG and SHORT; validation of 0/1/negative/above-1 values |
| `test_short_direction.py` (39 tests) | SHORT geometry is exact directional mirror of LONG |
| `test_trade_plan_builder.py` | Entry = close + buffer; stop = low - buffer (LONG) |

### Contradictions found

None between Python code, JavaScript oracle, and tests. The Python was ported to match JS exactly and validated against 1,531 fixture outputs. The UI labels ("Confirm", "Wick Depth") match the engine values. The Review Workspace Inspection panel now shows the exact engine values.

---

## 9. Real Examples

### QQQ 2026-07-27 SHORT / ORB_LOW (1m)

```
Level: 689.19 (68919 ticks)
Confirm bar [347] @ 09:49  O=688.67 H=689.22 L=688.46 C=688.58

range=76  body=9  rej_wick=55  opp_wick=12
wick_ratio = 0.724 ✓  body_ratio = 0.118 ✓  fcl = 0.842 ✓
penetration = 3 ticks   close_beyond = 61 ticks
wick_depth = 3 ticks    failed_retests = 0

Entry = 688.58 (close)  Stop = 689.22 (high)  Risk = 64 ticks
Penetration/wick = 3/55 = 5.5% of rejection wick is inside the ORB zone
```

**Assessment**: good setup. Wick enters 3 ticks into ORB, close is 61 ticks below level. Clean.

### NVDA 2026-07-30 LONG / ORB_HIGH (1m)

```
Level: 193.50 (19350 ticks)
Confirm bar [446] @ 11:26  O=193.82 H=193.84 L=193.44 C=193.83

range=40  body=0  rej_wick=39  opp_wick=1
wick_ratio = 0.975 ✓  body_ratio = 0.000 ✓  fcl = 0.975 ✓
penetration = 6 ticks   close_beyond = 33 ticks
wick_depth = 6 ticks    failed_retests = 1

Entry = 193.83 (close)  Stop = 193.44 (low)  Risk = 39 ticks
Penetration/wick = 6/39 = 15.4% of rejection wick is inside the ORB zone
```

**Assessment**: near-perfect doji. 6-tick penetration. Close 33 ticks above level.

### AAPL 2026-07-30 SHORT / ORB_LOW (1m) — Borderline

```
Level: 310.26 (31026 ticks)
Lowest penetration valid SHORT in dataset: 2 ticks

The wick enters only 2 ticks into the ORB zone.
98% of the rejection wick is OUTSIDE the ORB zone.
The engine accepts this.
```

**Assessment**: this is the weakest type of setup the engine currently allows. A discretionary trader might reject it for insufficient level contact.

---

## 10. Conclusions

### A. Rule Actually Implemented Today

For a LONG setup, the engine scans the retest window chronologically. For each candle whose **low touches or goes below** the ORB High (`low <= level_price`), it evaluates three shape ratios:

1. Lower wick must be ≥ 47% of total range
2. Body must be ≤ 40% of total range
3. Close must be in the upper 20% of the range

The first candle passing all three is the confirmation. Entry is at the close, stop is at the low. Penetration depth is reported but never gated. The wick ratio measures **candle shape** (lower wick proportion), not **ORB penetration depth**. No minimum penetration into the ORB zone is required — touching is sufficient.

### B. Differences vs Max's Vision

Based on the visual review sessions and feedback:

| Max expects | Engine does | Gap |
|---|---|---|
| Wick must enter **materially** into the ORB zone | Wick must touch or go below level (0 ticks OK) | **No minimum penetration enforced** |
| Percentage of wick inside the ORB matters | Not measured at all | **Missing metric** |
| Body should be clearly outside the ORB | Not checked — body position relative to level is unverified | **No body-vs-level gate** |
| Body distance from level affects quality | Not measured | **Missing metric** |
| Risk/reward quality relative to stop placement | Not assessed | **No R:R quality filter** |
| A candle sitting ON the level with zero penetration should not qualify | It qualifies if shape ratios pass | **Contact gate is too permissive** |

### C. Candidate Modifications (NOT implemented, ordered minimal → structural)

1. **Minimum penetration gate**: add `min_penetration_ticks` support in Stage 5 (currently raises UNSUPPORTED). Simplest change — a floor of 1-2 ticks would eliminate the "touching but not entering" problem.

2. **Penetration ratio gate**: new metric `penetration / rejection_wick`. Would filter candles where only a tiny fraction of the wick actually enters the ORB. Example: require ≥ 15-20% of wick inside the zone.

3. **Body-above-level gate** (LONG) / body-below-level gate (SHORT): require both open and close to be beyond the level. Would eliminate cases where the body straddles the ORB boundary.

4. **Close distance minimum**: activate `min_close_beyond_level_ticks` with a non-zero default. Would ensure the close has meaningful separation from the level.

5. **Stop quality filter**: reject setups where stop is within N ticks of the level, or where risk is disproportionate to the wick depth. Most structural change.

---

*This document supersedes all prior fragments about entry candle geometry.*
*For strategy changes, this audit must first be approved by Max.*
