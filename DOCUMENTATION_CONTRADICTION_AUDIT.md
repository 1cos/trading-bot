# Documentation Contradiction Audit

> **Date:** 2026-08-02
> **Purpose:** Find contradictions, conflicts, and inconsistencies across
> all specifications and Visual Language documents.
> **Rule:** Do not solve anything. Do not propose fixes. Only list.

---

## 1. Terminology Contradictions

### 1.1 — Level Provider output contract: two incompatible definitions

**Document A:** `BDRR_GENERIC_LEVEL_ENGINE_STUDY.md` § Level Provider Concept

Defines a Level Provider output as a 12-field dict including `status`,
`level_price`, `level_price_ticks`, `level_candle_index`, `level_candle`,
`level_high`, `level_low`, `level_source`, `level_type`, `direction`,
`date`, `metadata`.

**Document B:** `LEVEL_PROVIDER_SPEC.md` § Universal Level Record

Defines a Level Provider output as a 5-field record: `price`, `price_far`,
`direction`, `source`, `created_at`.

These are two different contracts for the same concept. The field names
differ, the field count differs, and the BDRR study includes fields
(`status`, `level_candle`, `metadata`, `level_price_ticks`) that the
new spec explicitly excludes.

### 1.2 — Direction vocabulary: SUPPORT/RESISTANCE vs LONG/SHORT

**Document A:** `LEVEL_PROVIDER_SPEC.md`

Level direction is `SUPPORT` or `RESISTANCE`.

**Document B:** `ONE_CANDLE_LEVEL_SPEC.md`

OCL is defined as "LONG structure" and "SHORT structure." The OCL spec
never uses the terms SUPPORT or RESISTANCE.

**Document C:** `ENTRY_CANDLE_ENGINE_SPEC.md`

Maps `SUPPORT` → `LONG` and `RESISTANCE` → `SHORT`, bridging the two
vocabularies. But the OCL spec does not reference this mapping.

The OCL spec was written before the Level Provider spec. The terminology
has not been reconciled.

### 1.3 — "Rejection" vs "confirmation" candle

**Document A:** `BDRR_ARCHITECTURE_PHILOSOPHY.md`

References "Confirmation delay" as a detector parameter and uses
"Break → Displacement → Retest → Rejection" as the pipeline stages.
Rejection is a stage, not a candle.

**Document B:** `ONE_CANDLE_LEVEL_SPEC.md`

Calls the entry candle an "entry/rejection candle."

**Document C:** `BDRR_TRADING_LAB_PARAMETER_ROADMAP.md` § Retest

References "confirmation body" — the candle is called confirmation,
not rejection.

The BDRR system calls it a confirmation candle. The OCL system calls it
a rejection candle. These describe the same concept (a candle that proves
the level held) with different names.

---

## 2. Workflow Contradictions

### 2.1 — Entry model: one candle vs two candles

**Document A:** `ONE_CANDLE_LEVEL_SPEC.md` (post-revision)

Frozen: "The entry/rejection candle itself performs the retest. Retest
and rejection are one candle, not two."

**Document B:** `BDRR_TRADING_LAB_PARAMETER_ROADMAP.md` § Retest + § Confirmation

The BDRR Lab parameters define retest and confirmation as separate
events with separate parameters (`maximum_retest_window_bars` for
retest, `confirmation_delay` for the confirmation candle after retest).

These describe different entry models. OCL uses one-candle entry. BDRR
uses two-event entry (retest then confirmation). This is intentional —
different strategies — but never explicitly acknowledged as a divergence.

### 2.2 — Level Provider timing: "do not build yet" vs "built"

**Document A:** `BDRR_GENERIC_LEVEL_ENGINE_STUDY.md` § Decision

"No Level Provider framework will be built until the first non-ORB level
type is proven valuable through research."

**Document B:** `LEVEL_PROVIDER_SPEC.md`

Defines a complete Universal Contract specification for all Level
Providers with a frozen 5-field output.

The BDRR study says do not build until proven. The Level Provider spec
defines the build. The gate condition ("proven valuable through research")
has not been explicitly met or documented as met.

---

## 3. Assumptions That Changed

### 3.1 — OCL entry model changed mid-session

**Before revision:** `ONE_CANDLE_LEVEL_SPEC.md` originally said "Entry
requires a BDRR-style rejection/confirmation candle" — implying the
two-event BDRR model.

**After revision:** Frozen as "the entry/rejection candle itself
performs the retest — one candle, not two."

The change is documented in `OCL_SYNTHETIC_VALIDATION_EXAMPLES.md`
but the original wording no longer exists in the spec (correctly
replaced). However, no changelog or version history records this
evolution.

### 3.2 — OCL wick zone definition tightened

**Original spec language:** "The One Candle Level is initially defined
only by the upper wick."

**Revised spec language:** "OCL zone = bearish candle open through high"
(LONG) and "low through open" (SHORT).

The original said "upper wick" without defining boundaries. The revision
froze the zone as open-to-high. This is a meaningful tightening — "upper
wick" could be interpreted as high-minus-some-threshold, while
open-to-high is exact.

---

## 4. Duplicated Concepts with Different Names

| Concept | Name in Doc A | Name in Doc B |
|---|---|---|
| The candle that proves the level held | "rejection candle" (OCL Spec, Rejection VL) | "confirmation candle" (BDRR Lab Roadmap) |
| The standardized level output | "Universal Level Record" (Level Provider Spec) | "standardized level dict" (BDRR Generic Study) |
| The area where the level exists | "wick zone" (OCL Spec) | "ORB zone" (BDRR Lab Roadmap) | 
| Energy fading from a move | "candles not shrinking" (Momentum VL) | "loss of conviction" (Continuation VL) |
| Multiple independent levels at same price | "multiple levels stack" (Structure VL) | "several structures point to same area" (Confluence VL) |
| Past price reaction at an area | "price has been here before" (Structure VL) | "price already reacted at this area" (Confluence VL) |

---

## 5. Concepts Used Before Being Defined

| Concept | Used in | Defined in | Issue |
|---|---|---|---|
| "BDRR-style rejection" | `ONE_CANDLE_LEVEL_SPEC.md` v0.1 original | `BDRR_ARCHITECTURE_PHILOSOPHY.md` (as pipeline stage) | OCL referenced BDRR rejection before OCL had its own entry model frozen. Now updated but the reference chain is unclear. |
| "Level Provider" | `ONE_CANDLE_LEVEL_SPEC.md` § Architectural Position | `LEVEL_PROVIDER_SPEC.md` | OCL spec calls OCL a "future structural Level Provider" but the Level Provider contract was written after the OCL spec. |
| "Entry Engine" | `LEVEL_PROVIDER_SPEC.md` | `ENTRY_CANDLE_ENGINE_SPEC.md` | Level Provider spec references the Entry Engine as consumer, but both were created in the same session with no cross-reference. |
| "Trade Candidate" | `ENTRY_CANDLE_ENGINE_SPEC.md` (implied) | `TRADE_CANDIDATE_SPEC.md` | The Entry Engine output feeds the Trade Candidate, but the Entry Engine spec does not reference the Trade Candidate by name. |
| "Policy Engine" | `TRADE_CANDIDATE_SPEC.md` | Nowhere | Referenced as a future consumer but never defined or specified. |
| "Backtester" | `TRADE_CANDIDATE_SPEC.md` | Nowhere | Same — referenced but undefined. |
| "Risk Engine" | `TRADE_CANDIDATE_SPEC.md` | Nowhere | Same. |

---

## 6. Frozen Decisions That Conflict with Newer Documents

### 6.1 — BDRR detector stages vs OCL architecture

**Frozen:** `BDRR_ARCHITECTURE_PHILOSOPHY.md` defines the detector as
"Break → Displacement → Retest → Rejection."

**Newer:** `ONE_CANDLE_LEVEL_SPEC.md` defines OCL as "momentum →
One Candle → continuation → retest/rejection." The stages are different.
Break and Displacement do not exist in OCL.

This is not necessarily a conflict — they may be different strategies
with different pipelines. But the BDRR architecture philosophy claims
to govern "every future architectural decision in the BDRR project,"
and it is unclear whether OCL is inside or outside that governance.

### 6.2 — "Do not build Level Provider until proven" vs current state

**Frozen:** `BDRR_GENERIC_LEVEL_ENGINE_STUDY.md` gates the Level
Provider framework on a non-ORB level being "proven valuable through
research."

**Current:** OCL research has not yet produced labeled real examples.
The Level Provider spec exists as a design document. The gate condition
is ambiguous — does a design spec violate the freeze, or only code?

### 6.3 — ORB assumed as fixed direction in Level Provider

**Frozen:** `LEVEL_PROVIDER_SPEC.md` assigns `ORB_HIGH` as always
`RESISTANCE` and `ORB_LOW` as always `SUPPORT`.

**Potential conflict:** In the BDRR system, after a break above
ORB_HIGH, the old resistance may become new support (classical
support/resistance flip). The Level Provider spec does not account
for this — the direction is fixed at creation and the level is
immutable.
