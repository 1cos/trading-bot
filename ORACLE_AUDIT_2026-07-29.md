# BDRR Oracle Audit — SPY LONG × 8 Examples

**Date:** 2026-07-29
**Reviewer:** Max (discretionary trader)
**Auditor:** Claude (engine analysis)
**Data source:** `dati/SPY_5m.csv` + engine pipeline output
**Scope:** All 8 VALID LONG detections from the review workspace

---

## Authoritative Core Rule (Max)

> The ORB is a directional state boundary.
> A LONG confirmation must close above ORB High.
> A SHORT confirmation must close below ORB Low.
> If the active direction closes materially back inside the ORB,
> the sequence is invalidated and a new break is required.

---

## Example 1: 2026-04-29

### Audit Data

| Field | Value |
|---|---|
| Session date | 2026-04-29 |
| ORB High | 711.19 |
| ORB Low | 709.92 |
| ORB Band | 1.27 |
| Break candle | [10] 10:20 — O=711.03 H=711.39 L=710.94 C=711.34 |
| Displacement bars | 7 bars: [11]→[17], all lows > 711.19, all closes above ORB_H |
| First close ≤ ORB_H | [18] 11:00 — C=711.11 (INSIDE ORB) |
| First close < ORB_L | [28] 11:50 — C=709.55 (BELOW ORB_L) |
| Closes INSIDE between break–conf | 21 |
| Closes BELOW ORB_L between break–conf | 17 |
| Engine confirmation | [56] 14:10 — O=708.84 H=709.23 L=708.46 C=709.09 |
| Conf close vs ORB | BELOW ORB_L (−0.83) |
| Outcome | STOPPED |

### Analysis

The initial LONG break at 10:20 and 7-bar displacement are genuine. However, at
11:00 (bar 18), the close drops back inside the ORB at 711.11. By 11:05 (bar 19),
the close is 710.62 — well inside the ORB. By 11:50 (bar 28), the close is 709.55
— below ORB Low. The LONG sequence is structurally dead.

The engine finds a "confirmation" at 14:10 at 709.09 — **1.83 points below ORB
High and 0.83 below ORB Low.** This is not even in the correct direction. The
engine kept searching for rejection geometry on any candle that touched the
level (711.19), but by this time the market has fully reversed.

### Defect Classification

| # | Category | Detail |
|---|---|---|
| 1 | **State management** | No directional state tracker. Once break is found, it is permanent. |
| 2 | **Invalidation** | No check for close returning inside ORB after displacement. |
| 3 | **Confirmation** | Confirmation close at 709.09 is BELOW ORB_L. Violates core rule. |
| 4 | **Opposite break** | Bar 28 closes below ORB_L at 709.55 — potential SHORT sequence. Engine ignores. |

### Contract clause responsible

`displacement_finder.py` line 12: "Scans candles strictly after the break candle
for displacement bars (low > level_price) until the first retest contact."

`retest_window.py` lines 15–16: "Window starts at first_retest_contact_index…
Window ends at candles[-1] (last available candle)."

`rejection_finder.py`: Scans the entire retest window (which extends to session
end) for any candle matching rejection geometry. No state check.

### Max's classification: **INVALID — sequence invalidated at 11:00**

---

## Example 2: 2026-04-30

### Audit Data

| Field | Value |
|---|---|
| Session date | 2026-04-30 |
| ORB High | 714.73 |
| ORB Low | 712.80 |
| ORB Band | 1.93 |
| Break candle | [32] 12:10 — O=714.72 H=715.39 L=714.65 C=715.31 |
| Displacement bars | 5 bars: [33]→[37], all closes above ORB_H |
| First close ≤ ORB_H | [40] 12:50 — C=714.36 (INSIDE ORB) |
| Closes INSIDE between break–conf | 1 |
| Closes BELOW ORB_L between break–conf | 0 |
| Engine confirmation | [41] 12:55 — O=714.36 H=714.38 L=713.90 C=714.35 |
| Conf close vs ORB | INSIDE ORB (712.80 ≤ 714.35 ≤ 714.73) |
| Outcome | TARGET_HIT |

### Analysis

Good break at 12:10, solid 5-bar displacement. The possible confirmation area is
around 12:45–12:55. However, the engine's confirmation candle at 12:55 has close
714.35 — **inside the ORB band** (ORB_L=712.80, ORB_H=714.73).

Under Max's core rule, a LONG confirmation must close above ORB High (714.73).
The close at 714.35 is 0.38 below ORB High. The large wick dips to 713.90 but
recovers only to 714.35 — not enough to clear the ORB boundary.

Note: despite being technically invalid by the core rule, this trade actually
hit TARGET. The structural sequence is mostly sound — the defect is narrow
(close fell 0.38 short of ORB_H).

### Defect Classification

| # | Category | Detail |
|---|---|---|
| 1 | **Confirmation** | Close at 714.35 is inside ORB. Must be above 714.73 to confirm LONG. |

### Contract clause responsible

`rejection_finder.py`: Checks rejection wick ratio, body ratio, and favorable
close location — but does NOT check whether the close is above the ORB_H
boundary. The geometry rules are satisfied (wick=49.4%, body=10.5%, fcl=81.8%)
but the directional state requirement is missing.

### Max's classification: **INVALID — confirmation close inside ORB**

---

## Example 3: 2026-05-18

### Audit Data

| Field | Value |
|---|---|
| Session date | 2026-05-18 |
| ORB High | 740.06 |
| ORB Low | 738.50 |
| ORB Band | 1.56 |
| Break candle | [4] 09:50 — O=738.84 H=740.92 L=738.84 C=740.88 |
| Displacement bars | 1 bar: [5] L=740.40 C=740.57 |
| First close ≤ ORB_H | [6] 10:00 — C=739.18 (INSIDE ORB) |
| First close < ORB_L | [12] 10:30 — C=737.43 |
| Closes INSIDE between break–conf | 4 |
| Closes BELOW ORB_L between break–conf | 1 |
| Engine confirmation | [13] 10:35 — O=737.42 H=738.10 L=736.37 C=737.93 |
| Conf close vs ORB | BELOW ORB_L (−0.57) |
| Outcome | STOPPED |

### Analysis

Break at 09:50 with a single displacement bar. By 10:00 (bar 6), close drops
to 739.18 — back inside ORB. By 10:30 (bar 12), close is at 737.43 — below ORB
Low. The market is heading toward ORB Low, not maintaining the LONG.

The engine's "confirmation" at 10:35 has close 737.93 — **below ORB Low** by
0.57. This is the opposite of a LONG confirmation. The market is moving toward
a potential SHORT at ORB Low, not confirming a LONG at ORB High.

### Defect Classification

| # | Category | Detail |
|---|---|---|
| 1 | **State management** | LONG invalidated at bar 6 (10:00). No state reset. |
| 2 | **Invalidation** | Close inside ORB immediately after 1-bar displacement. |
| 3 | **Confirmation** | Close at 737.93 is BELOW ORB_L. Impossible LONG. |
| 4 | **Displacement** | Only 1 bar of displacement before immediate reversal. |

### Max's classification: **INVALID — no valid LONG displacement; potential SHORT**

---

## Example 4: 2026-05-26

### Audit Data

| Field | Value |
|---|---|
| Session date | 2026-05-26 |
| ORB High | 750.44 |
| ORB Low | 749.36 |
| ORB Band | 1.08 |
| Break candle | [2] 09:40 — O=749.72 H=750.70 L=749.69 C=750.63 |
| Displacement bars | 1 bar: [3] L=750.64 C=750.98 |
| First close ≤ ORB_H | [4] 09:50 — C=750.14 (INSIDE ORB) |
| Closes INSIDE between break–conf | 1 |
| Closes BELOW ORB_L between break–conf | 0 |
| Engine confirmation | [19] 11:05 — O=750.77 H=750.97 L=750.36 C=750.89 |
| Conf close vs ORB | ABOVE ORB_H (+0.45) |
| Outcome | STOPPED |

### Analysis

Break at 09:40, 1 bar displacement. Bar 4 (09:50) close is 750.14 — inside ORB.
But then price recovers: bars 5–20 mostly close above ORB High. The confirmation
at bar 19 (11:05) closes at 750.89 — **above ORB High by 0.45**.

The wick dips to 750.36 (inside ORB by 0.08), but the close recovers above the
ORB boundary. This matches Max's core rule: wick may enter the ORB band, but
the candle must close outside on the correct side.

The one concern: bar 4 closes inside the ORB immediately after displacement.
However, the market quickly recovers and re-establishes the LONG structure. This
is a judgment call — under strict invalidation rules this would fail, but Max
accepts it.

### Defect Classification

No defect. The confirmation close is above ORB_H.

### Max's classification: **VALID — positive model example**

---

## Example 5: 2026-06-08

### Audit Data

| Field | Value |
|---|---|
| Session date | 2026-06-08 |
| ORB High | 744.51 |
| ORB Low | 742.15 |
| ORB Band | 2.36 |
| Break candle | [17] 10:55 — O=744.13 H=745.02 L=743.65 C=744.99 |
| Displacement bars | 3 bars: [18]→[20], all closes above ORB_H |
| Closes INSIDE between break–conf | 0 |
| Closes BELOW ORB_L between break–conf | 0 |
| Engine confirmation | [22] 11:20 — O=744.84 H=745.08 L=744.14 C=744.93 |
| Conf close vs ORB | ABOVE ORB_H (+0.42) |
| Outcome | STOPPED |

### Analysis

Break at 10:55, 3-bar displacement all above ORB_H. Confirmation at 11:20
closes at 744.93 — above ORB_H by 0.42. The wick dips to 744.14 (inside ORB
by 0.37) but recovers. No closes inside ORB between break and confirmation.

Structurally this is a clean LONG: break → displacement → retest → confirmation
above ORB_H. However, Max notes the displacement is not meaningful — the price
structure looks like resistance/compression and later moves sharply downward
(the session ends at 739.31, far below ORB_L).

The structural sequence passes. The displacement quality is a separate concern
(Phase C/D).

### Defect Classification

No structural defect. The displacement quality is questionable — all 3
displacement bars have lows barely above ORB_H (744.62, 744.57, 744.68 vs
744.51), so the "displacement" is only 0.11–0.17 points above the level.

### Max's classification: **Review displacement quality. Structurally borderline.**

---

## Example 6: 2026-06-29

### Audit Data

| Field | Value |
|---|---|
| Session date | 2026-06-29 |
| ORB High | 738.76 |
| ORB Low | 735.30 |
| ORB Band | 3.46 |
| Break candle | [2] 09:40 — O=738.01 H=739.25 L=737.44 C=739.24 |
| Displacement bars | 1 bar: [3] L=738.78 C=738.85 |
| First close ≤ ORB_H | [5] 09:55 — C=736.89 (INSIDE ORB) |
| First close < ORB_L | [7] 10:05 — C=735.05 (BELOW ORB_L) |
| Closes INSIDE between break–conf | 8 |
| Closes BELOW ORB_L between break–conf | 4 |
| Engine confirmation | [17] 10:55 — O=737.96 H=737.96 L=737.14 C=737.86 |
| Conf close vs ORB | INSIDE ORB (735.30 ≤ 737.86 ≤ 738.76) |
| Outcome | STOPPED |

### Analysis

Break at 09:40, single-bar displacement. By 09:55 (bar 5), close drops to
736.89 — inside ORB. By 10:05 (bar 7), close drops to 735.05 — **below ORB Low**.
The ORB band is wide (3.46 points), and price plunges through the entire range
plus below the low.

Bars 7–10 all close below ORB Low, reaching 732.70 at 10:10 — a 6.06 point
drop below ORB_H. The LONG sequence is completely dead.

The engine's "confirmation" at 10:55 has close 737.86 — **inside the ORB band**,
not above ORB_H. And this comes after 4 closes below ORB_L and 8 closes inside
the ORB.

### Defect Classification

| # | Category | Detail |
|---|---|---|
| 1 | **State management** | LONG invalidated at bar 5 (09:55). No state reset. |
| 2 | **Invalidation** | 4 closes below ORB_L ignored. |
| 3 | **Confirmation** | Close at 737.86 is INSIDE ORB. Must be above 738.76. |
| 4 | **Opposite break** | Bar 7 closes below ORB_L — potential SHORT. |

### Max's classification: **INVALID — sequence invalidated, conf inside ORB**

---

## Example 7: 2026-07-06

### Audit Data

| Field | Value |
|---|---|
| Session date | 2026-07-06 |
| ORB High | 749.48 |
| ORB Low | 748.12 |
| ORB Band | 1.36 |
| Break candle | [10] 10:20 — O=749.19 H=749.84 L=749.04 C=749.65 |
| Displacement bars | 2 bars: [11] C=749.94, [12] C=749.70 — both above ORB_H |
| First close ≤ ORB_H | None between break and conf |
| Closes INSIDE between break–conf | 0 |
| Closes BELOW ORB_L between break–conf | 0 |
| Engine confirmation | [14] 10:40 — O=749.65 H=749.76 L=749.10 C=749.67 |
| Conf close vs ORB | ABOVE ORB_H (+0.19) |
| Outcome | TARGET_HIT |

### Analysis

Clean LONG: break at 10:20, 2-bar displacement, no close inside or below ORB
between break and confirmation. Confirmation at 10:40 closes at 749.67 — above
ORB_H by 0.19. The wick dips to 749.10 (inside ORB by 0.38) but recovers.

After confirmation, approximately 3 candles close above ORB_H, then price
retests and confirms again. The entire session stays above ORB_H.

Max notes this is a valid LONG with small but acceptable displacement.
Displacement quality is reduced (bars only 0.08–0.46 above level) but the
structural sequence is correct.

### Defect Classification

No defect. Structurally valid.

### Max's classification: **VALID — small but acceptable displacement**

---

## Example 8: 2026-07-20

### Audit Data

| Field | Value |
|---|---|
| Session date | 2026-07-20 |
| ORB High | 748.05 |
| ORB Low | 746.80 |
| ORB Band | 1.25 |
| Break candle | [1] 09:35 — O=748.03 H=748.71 L=747.73 C=748.17 |
| Displacement bars | 1 bar: [2] L=748.06 C=748.56 |
| First close ≤ ORB_H | [3] 09:45 — C=747.58 (INSIDE ORB) |
| First close < ORB_L | [4] 09:50 — C=746.46 (BELOW ORB_L) |
| Closes INSIDE between break–conf | 1 |
| Closes BELOW ORB_L between break–conf | 19 |
| Engine confirmation | [23] 11:25 — O=745.72 H=745.97 L=745.22 C=745.87 |
| Conf close vs ORB | BELOW ORB_L (−0.93) |
| Outcome | STOPPED |

### Analysis

Break at 09:35, single-bar displacement. By 09:45 (bar 3), close drops inside
ORB at 747.58. By 09:50 (bar 4), close drops below ORB Low at 746.46. The LONG
is dead within 2 candles.

The session then spends the entire remainder below ORB Low — **19 consecutive
closes below ORB_L** between the break and the engine's "confirmation."

The engine's "confirmation" at 11:25 has close 745.87 — **below ORB Low by 0.93
and below ORB High by 2.18.** This is a complete structural impossibility for
a LONG confirmation.

Bar 4 (09:50) closes below ORB Low — the direction should switch to SHORT.
The historical LONG break can never be confirmed.

### Defect Classification

| # | Category | Detail |
|---|---|---|
| 1 | **State management** | LONG invalidated at bar 3 (09:45). No state reset. |
| 2 | **Invalidation** | 19 closes below ORB_L completely ignored. |
| 3 | **Confirmation** | Close at 745.87 is BELOW ORB_L. Impossible LONG. |
| 4 | **Opposite break** | Bar 4 closes below ORB_L — potential SHORT sequence starts. |

### Max's classification: **INVALID — sequence invalidated, opposite break occurred**

---

## Summary of Defects

| Example | Date | Engine | Max | Defect |
|---|---|---|---|---|
| 1 | 2026-04-29 | VALID | **INVALID** | State mgmt + invalidation + conf below ORB_L |
| 2 | 2026-04-30 | VALID | **INVALID** | Conf close inside ORB (−0.38 from ORB_H) |
| 3 | 2026-05-18 | VALID | **INVALID** | State mgmt + invalidation + conf below ORB_L |
| 4 | 2026-05-26 | VALID | **VALID** | ✓ No defect |
| 5 | 2026-06-08 | VALID | **Review** | Structurally borderline. Displacement quality. |
| 6 | 2026-06-29 | VALID | **INVALID** | State mgmt + invalidation + conf inside ORB |
| 7 | 2026-07-06 | VALID | **VALID** | ✓ No defect |
| 8 | 2026-07-20 | VALID | **INVALID** | State mgmt + invalidation + conf below ORB_L |

**Engine agreement with Max: 2/8 correct (Examples 4 and 7).**
**Engine false positives: 5/8 (Examples 1, 2, 3, 6, 8).**
**Example 5: borderline — needs displacement quality Phase C/D.**

---

## Root Cause Analysis

The engine has **no concept of directional state.** The pipeline stages are
executed sequentially and irrevocably:

1. `break_finder.py`: finds the first close > ORB_H. This is permanent.
2. `displacement_finder.py`: finds bars with low > level_price. This defines
   the displacement window and `first_retest_contact_index`.
3. `retest_window.py`: window extends from first retest contact to **end of
   session** (line 16: "Window ends at candles[-1]"). No state check.
4. `rejection_finder.py`: scans entire retest window for any candle matching
   geometry rules. No check on whether:
   - The close is above ORB_H
   - The LONG sequence is still alive
   - Price has returned inside or below the ORB
   - The opposite boundary has been broken

The fundamental problem is that once `break_finder` fires, the pipeline
assumes the LONG sequence is permanently valid and searches the entire
remaining session for a qualifying geometry. There is no mechanism to
invalidate the sequence, reset state, or switch direction.

### Contract clauses responsible

| Module | Line(s) | Issue |
|---|---|---|
| `retest_window.py` | 15–16 | Window extends to session end with no state boundary |
| `rejection_finder.py` | entire | No check that confirmation close is above ORB_H |
| All modules | — | No directional state object passed between stages |
| `strategy_runner.py` | — | No invalidation pass or state transition logic |

---

## Staged Correction Plan

### Phase A: Directional State + Invalidation + Sequence Reset

**Goal:** If the close returns inside the ORB after displacement, invalidate
the active sequence. If the opposite ORB boundary is subsequently broken,
begin evaluating the new direction.

**Scope:**
1. Add a directional state concept to the pipeline (or a pre-rejection
   validation pass).
2. After displacement, scan candles forward. If any candle's close is
   ≤ ORB_H (for LONG), the sequence is **invalidated** at that bar.
3. The retest window must not extend beyond the invalidation bar.
4. If no valid confirmation is found before invalidation, the result is
   INVALID with `failed_stage = "INVALIDATION_CLOSE_INSIDE_ORB"`.
5. No opposite-direction switching yet (that requires re-running the
   pipeline with reversed parameters).

**Implementation approach (smallest change):**

Add a new stage between displacement and retest_window — a
**`sequence_validator`** module that:

```
def validate_sequence(candles, orb_result, break_result, displacement_result, config):
    """Check if the LONG sequence remains alive after displacement.
    
    Scans from the first retest contact forward. If any candle's close
    is inside or below the ORB band (close <= orb_high for LONG),
    truncate the valid retest window at that candle.
    
    Returns:
        max_valid_index: last candle index that may contain a confirmation
        invalidation_index: the candle that killed the sequence (or None)
        invalidation_reason: "CLOSE_INSIDE_ORB" or None
    """
```

The `retest_window` module then uses `max_valid_index` instead of `len(candles)-1`.

**Test impact:** Examples 1, 3, 6, 8 would become INVALID.
Example 2 would remain VALID until Phase B (its invalidation candle IS the
confirmation candle — it closes inside but has rejection geometry).

### Phase B: Confirmation Must Close Outside ORB

**Goal:** The confirmation candle's close must be above ORB_H (LONG) or
below ORB_L (SHORT).

**Scope:**
1. In `rejection_finder.py`, add a final check after geometry passes:
   `confirmation_close > orb_high` (for LONG).
2. If the close is inside or below, the candle is a failed retest
   (new failure reason: `CLOSE_INSIDE_ORB`).

**Test impact:** Example 2 would become INVALID (close 714.35 < ORB_H 714.73).

### Phase C: Displacement Quality

**Goal:** Configurable minimum displacement distance and bar count.

**Scope:**
1. Add `min_displacement_distance_points` to config.
2. Add `min_displacement_bar_count` to config.
3. These are soft filters, not structural invalidations.

**Test impact:** Example 5 might be filtered. Example 7 (which Max accepts)
must NOT be filtered — thresholds must be calibrated carefully.

### Phase D: Scoring + Discretionary Refinement

**Goal:** Use Accept/Reject/Skip decisions to train the Scorer.

**Scope:**
1. Load exported decisions JSON.
2. Build feature vector from detection result.
3. Train a simple classifier (logistic regression or decision tree).
4. Use Scorer output as a quality signal, not a hard filter.

---

## Proposed Phase A Change (Awaiting Max's Approval)

Create a new module `backend/src/trading_lab/sequence_validator.py`:

**Input:** candles, orb_result, break_result, displacement_result, config

**Logic:**
- Starting from the first retest contact (displacement_result["first_retest_contact_index"])
- Scan forward candle by candle
- For LONG: if `candle.close <= orb_high`, mark that as invalidation_index
- For SHORT: if `candle.close >= orb_low`, mark as invalidation_index
- Return `max_valid_index` (the bar BEFORE the invalidation) and the
  invalidation metadata

**Integration:**
- `strategy_runner.py` calls `validate_sequence()` after displacement_finder
- If invalidation_index exists, cap the retest_window at max_valid_index
- If no valid confirmation is found in the truncated window, result is
  INVALID with `failed_stage = "SEQUENCE_INVALIDATED"`

**Important edge case — Example 4 (2026-05-26):**
Bar 4 closes at 750.14 (inside ORB). Under strict Phase A rules, this
would invalidate the sequence. But Max accepts this example.

**Options:**
1. **Strict:** invalidate immediately on first close inside ORB → Example 4 fails
2. **Tolerant:** allow one close inside ORB if the next bar recovers above ORB_H
3. **Threshold:** require N consecutive closes inside ORB before invalidation
4. **Material:** only invalidate if close is more than X ticks inside ORB

**Recommendation:** Start with option 1 (strict) and see how many examples
survive. If Example 4 is the only false negative, implement option 2 (recovery
allowance). Max should decide.

---

**No code has been modified. No tests changed. No commits. No pushes.**
**Awaiting Max's approval on Phase A approach before implementation.**
