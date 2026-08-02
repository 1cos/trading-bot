# Trading Journal — Discoveries

> This is the scientific research journal of the BDRR project.
> Every observation discovered during manual reviews, backtesting, or detector audits must be recorded here before becoming code.
> This document is not a backlog, not a TODO list, not a design document.

---

## Purpose

This journal exists to prevent emotional or premature changes to the detector.

Every idea follows the same lifecycle:

```
Observation
    ↓
Evidence
    ↓
Hypothesis
    ↓
Lab Parameter
    ↓
Backtest
    ↓
Validation
    ↓
Possible Detector Change
```

Ideas must mature through evidence. They never skip directly into implementation.

---

## Core Principle

One interesting chart is not enough.

One losing trade is not enough.

One winning trade is not enough.

The purpose of this journal is to accumulate evidence over time.

---

## Discovery Lifecycle

Every discovery must have one of these statuses:

| Status | Meaning |
|---|---|
| **NEW** | First observation |
| **OBSERVED** | Seen multiple times |
| **REPEATED** | Consistently appearing |
| **LAB PARAMETER** | Implemented only as a configurable parameter |
| **BACKTESTED** | Performance measured |
| **VALIDATED** | Supported by data |
| **OPTIONAL DETECTOR CHANGE** | Only if detector modification becomes objectively justified |

Detector changes are always the final step.

---

## Confidence Scale

| Level | Meaning |
|---|---|
| **Very Low** | Interesting single example |
| **Low** | Observed a few times |
| **Medium** | Repeated across multiple symbols |
| **High** | Repeated frequently and supported by backtests |
| **Very High** | Stable behavior confirmed |

---

## Review Sources

Discoveries may originate from:

- Manual Review
- Detector Audit
- Backtest Lab
- Statistical Analysis
- Trade Dataset Analysis
- Future Live Trading

---

## Entry Template

Every discovery must follow exactly this structure:

```
### DISCOVERY-NNN: <Short descriptive title>

- **Date:** YYYY-MM-DD
- **Status:** NEW | OBSERVED | REPEATED | LAB PARAMETER | BACKTESTED | VALIDATED | OPTIONAL DETECTOR CHANGE
- **Category:** Displacement | Retest | Confirmation | Risk | Market Context | Order Blocks | Session Management | Exit | Detector | Lab | Performance | Other
- **Origin:** Manual Review | Detector Audit | Backtest | Live Trading | Statistical Analysis
- **Current Confidence:** Very Low | Low | Medium | High | Very High

**Observation:**
What was noticed. Only objective description.

**Evidence:**
Which review batches, which symbols, which dates, how many examples.
Never use vague statements.

**Examples:**
- <setup_id_1>
- <setup_id_2>

**Possible Explanations:**
List possible reasons. Do NOT assume they are true.

**Possible Lab Parameter:**
Parameter name if applicable, or "None yet."

**Backtesting Required:** YES | NO

**Detector Modification Required:** NOT YET
(Never write YES immediately. Only after extensive evidence.)

**Decision:**
Current project decision. Examples: Collect more examples. Wait. Needs 50 reviews. Needs detector audit. Implement as Lab parameter. Rejected hypothesis.

**Notes:**
Free text.
```

---

## Discoveries

---

### DISCOVERY-001: Confirmation body too far from ORB

- **Date:** 2026-07-30
- **Status:** OBSERVED
- **Category:** Confirmation
- **Origin:** Manual Review
- **Current Confidence:** Low

**Observation:**
During manual review, several setups showed confirmation bars with bodies positioned far from the ORB level. These setups felt visually weak despite passing all detector stages.

**Evidence:**
Appeared repeatedly during Review Batch #1 across multiple symbols and timeframes.

**Examples:**
- To be catalogued from training batch reviews

**Possible Explanations:**
- Price extended too far before confirming, reducing the risk/reward profile
- A confirmation bar far from the ORB may indicate momentum exhaustion rather than continuation

**Possible Lab Parameter:**
`max_body_distance_from_orb_ticks`

**Backtesting Required:** YES

**Detector Modification Required:** NOT YET

**Decision:**
Collect more evidence. Needs systematic measurement across all 20 reviewed setups.

**Notes:**
This is a prime candidate for a Lab parameter rather than a detector rule — it reflects a discretionary preference about setup quality, not an objective structural requirement.

---

### DISCOVERY-002: Late-session entries frequently rejected manually

- **Date:** 2026-07-30
- **Status:** NEW
- **Category:** Session Management
- **Origin:** Manual Review
- **Current Confidence:** Very Low

**Observation:**
Setups that complete their BDRR sequence late in the trading session appear to be rejected more often during manual review.

**Evidence:**
Initial impression from early reviews. Not yet quantified.

**Examples:**
- To be catalogued

**Possible Explanations:**
- Reduced time remaining for the trade to reach target
- Afternoon sessions may have different volatility characteristics
- Discretionary bias against late entries due to experience

**Possible Lab Parameter:**
`latest_entry_time`

**Backtesting Required:** YES

**Detector Modification Required:** NOT YET

**Decision:**
Continue collecting examples. Need to define what "late" means in terms of specific time thresholds.

**Notes:**
Time-based filtering is a clear policy-layer concern per the Architecture Philosophy (Section 3). Should never become a detector rule.

---

### DISCOVERY-003: Strong Order Block confluence increases confidence

- **Date:** 2026-07-30
- **Status:** NEW
- **Category:** Order Blocks
- **Origin:** Manual Review
- **Current Confidence:** Very Low

**Observation:**
Some manually accepted setups appear to have retest zones that coincide with prior Order Block areas, potentially adding confluence to the signal.

**Evidence:**
Anecdotal observation. No Order Block detection exists yet to measure this systematically.

**Examples:**
- To be catalogued once Order Block engine is available

**Possible Explanations:**
- Order Blocks represent institutional interest zones that add structural significance to retest levels
- Coincidental overlap with no causal relationship

**Possible Lab Parameter:**
None yet. Requires Order Block detection engine first.

**Backtesting Required:** YES

**Detector Modification Required:** NOT YET

**Decision:**
No implementation. Requires Order Block engine as a prerequisite. Park as a long-term research direction.

**Notes:**
This is a downstream feature that depends on infrastructure not yet built. Recorded here to prevent the idea from being lost.

---

### DISCOVERY-004: RETEST_BEFORE_DISPLACEMENT dominates rejected candidates (60%)

- **Date:** 2026-07-31
- **Status:** OBSERVED
- **Category:** Displacement
- **Origin:** Detector Audit (A5.3 distribution analysis)
- **Current Confidence:** Medium

**Observation:**
Across 9 symbols, 60 sessions, 1080 pipeline runs (5m timeframe), 60.5% of all audit-worthy rejected candidates fail with RETEST_BEFORE_DISPLACEMENT. Price breaks the ORB level, but retests it before any displacement occurs.

**Evidence:**
A5.3 analysis: 438 out of 724 audit-worthy rejected records. Present across all 9 symbols and both directions (LONG and SHORT). Not concentrated in a single symbol or date range.

**Examples:**
- SPY 2026-04-24 5m LONG
- QQQ 2026-04-24 5m SHORT
- NVDA 2026-04-27 5m LONG

**Possible Explanations:**
- The ORB break definition may be too sensitive, triggering on marginal breaks that lack momentum
- Markets frequently test a breakout level immediately, and the detector requires displacement before any retest
- The single-bar ORB (5m) may not establish a strong enough level for clean displacement
- This may be normal market behavior — most breakouts fail quickly

**Possible Lab Parameter:**
`allow_immediate_retest` — permit one retest before displacement
`min_break_distance_ticks` — require stronger break before starting displacement scan

**Backtesting Required:** YES

**Detector Modification Required:** NOT YET

**Decision:**
Review the 30 balanced RETEST_BEFORE_DISPLACEMENT samples in the audit batch. Determine whether Max would have taken any of these trades. If many are "NO — correct rejection," the detector is working as intended. If many are "NO — I would have traded this," the displacement requirement may need a Lab parameter.

**Notes:**
This is the highest-priority discovery from the first audit. The 60% dominance is not necessarily a problem — it may reflect genuine market structure. Manual review will clarify.

---

### DISCOVERY-005: NO_QUALIFYING_REJECTION_CANDLE represents 27.6% of audit-worthy rejections

- **Date:** 2026-07-31
- **Status:** OBSERVED
- **Category:** Confirmation
- **Origin:** Detector Audit (A5.3 distribution analysis)
- **Current Confidence:** Low

**Observation:**
200 out of 724 audit-worthy rejected records reach the full rejection scan but fail because no retest candle satisfies all three geometry thresholds (rejection wick ≥ 50%, body ≤ 50%, favorable close location ≥ 80%).

**Evidence:**
A5.3 analysis. These setups have complete structure: break, displacement, and retest — but the rejection candle geometry is not clean enough.

**Examples:**
- SPY 2026-04-29 5m LONG
- SPY 2026-04-30 5m LONG
- SPY 2026-05-11 5m LONG

**Possible Explanations:**
- The 80% favorable close location threshold may be too strict for certain market conditions
- Retest candles may show rejection intent but with less extreme wick proportions
- The geometry rules were designed for textbook rejection candles; real market candles may be messier

**Possible Lab Parameter:**
`favorable_close_location_min` — currently hardcoded at 0.80
`rejection_wick_ratio_min` — currently hardcoded at 0.50
`body_ratio_max` — currently hardcoded at 0.50

**Backtesting Required:** YES

**Detector Modification Required:** NOT YET

**Decision:**
Review the 30 balanced NO_QUALIFYING_REJECTION_CANDLE samples. Examine which specific rule fails most often (FAVORABLE_CLOSE_LOCATION_TOO_LOW vs REJECTION_WICK_RATIO_TOO_LOW vs BODY_RATIO_TOO_HIGH). If one rule dominates, it becomes a candidate for threshold tuning via Lab parameter.

---

## Monthly Review

Once per month, review all discoveries and answer:

- Which discoveries became stronger with new evidence?
- Which discoveries disappeared or stopped recurring?
- Which discoveries deserve a Lab parameter?
- Which discoveries should be marked REJECTED?
- Which discoveries are ready for detector discussion?

### Review Log

| Month | Discoveries Reviewed | Promoted | Rejected | Notes |
|---|---|---|---|---|
| *No reviews yet* | — | — | — | — |

---

## Important Rules

- **Never delete discoveries.** If a hypothesis is disproven, mark it **REJECTED** and explain why. Rejected ideas remain valuable.
- **Never skip the lifecycle.** Ideas must progress through evidence before becoming code.
- **Never mix concerns.** This document is not for bug reports, feature requests, coding tasks, git TODOs, or architecture decisions. Those belong elsewhere.

---

## Project Philosophy

> *The detector evolves through accumulated evidence.*
> *Never through isolated examples.*

---

## Strategic Decisions

### 2026-08-02 — Level Provider Research Track Change

**Decision:** Order Block research is paused. One Candle Level (OCL) is
the active Level Provider research track.

**What this means:**
- All existing OB documents and files are preserved — nothing deleted.
- No OB detector, provider, or workspace development occurs.
- OCL is defined in `ONE_CANDLE_LEVEL_SPEC.md` (v0.1).
- Future OB work may resume separately.

**Reason:** OCL is Max's specific continuation structure. Generic Order
Block definitions introduce conflicting internet terminology. OCL uses
an internal, precise definition owned by the project.
