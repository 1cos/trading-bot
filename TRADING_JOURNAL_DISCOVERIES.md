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
