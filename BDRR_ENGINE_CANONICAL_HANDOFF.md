# BDRR Engine — Canonical Architecture Handoff Document
### Session-Derived Architecture Record
### Status: Contracts Frozen — Implementation Not Started

---

## Purpose

This document is the sole authoritative record of all architectural decisions made for the BDRR (Break–Displacement–Retest–Rejection) trading engine. It is self-contained and designed to restore complete architectural context in a new session without relying on chat history.

---

## How to Read This Document

Every item carries exactly one status label.

- **FROZEN** — decided and closed. Cannot be changed without a versioned revision and explicit approval.
- **CONFIGURABLE VALUE** — the parameter exists and its meaning is frozen, but its numeric or categorical value is set per preset and has not been decided.
- **OPEN** — acknowledged as a necessary decision that was explicitly deferred.
- **NOT STARTED** — work that is authorized but not yet begun.

---

---

# Part 1 — System Identity

**FROZEN** — Engine name: Break–Displacement–Retest–Rejection (BDRR) engine.

**FROZEN** — The engine implements one universal detection pattern:

```
Important Level
→ Confirmed Break
→ Displacement
→ Retest
→ Rejection Candle
→ Entry Trigger
→ Stop Beyond Rejection
```

**FROZEN** — This pattern is level-agnostic, timeframe-agnostic, instrument-agnostic, and direction-agnostic. The logic never changes. The parameters change.

---

---

# Part 2 — Inter-Layer Architecture

## 2.1 Layer Definitions

**FROZEN** — The pipeline has exactly four processing layers:

```
Layer 1 — Detection Engine
Layer 2 — Quality Scorer
Layer 3 — Decision Policy
Layer 4 — Storage / Display / Execution / Backtesting
```

**FROZEN** — Data flows strictly downward. No layer may send data upward. No layer may modify an object written by a layer above it.

---

## 2.2 Layer Responsibilities

**FROZEN** — Detection Engine: runs a deterministic structural algorithm against raw market data. Determines VALID or INVALID for every candidate examined. Produces `DetectionResult/v1` for all candidates and `SetupCandidate/v1` for VALID candidates only. Has no knowledge of quality, policy, or execution.

**FROZEN** — Quality Scorer: reads `SetupCandidate/v1` as its sole pipeline input. Runs core quality modules and contextual modules independently. Computes `core_quality_score` and `core_quality_grade` from core modules only. Reports contextual module results separately. Has no knowledge of the active Decision Policy mode. Produces `ScoredSetup/v1`.

**FROZEN** — Decision Policy: reads `ScoredSetup/v1`. Selects `grade_basis`. Evaluates eligibility. Determines action. Applies confluence mode. Composes policy-level score when applicable using already-stored module outputs — never by re-running scoring functions or reading raw market data. Produces `DecisionOutcome/v1`.

**FROZEN** — Layer 4 components (storage, display, backtesting, execution) may read finalized objects from any layer. They never write to pipeline objects. The execution sub-layer receives candidates only when `action = EXECUTE`.

---

## 2.3 Module Classification

**FROZEN** — Quality Scorer modules are partitioned into two disjoint categories:

**Core quality modules** — measure setup geometry and structure. Their scores are combined into `core_quality_score` and `core_quality_grade`. Examples: displacement clearance, momentum, empty space, retest penetration depth, reclaim strength, time elapsed, failed retest count, visual cleanliness.

**Contextual modules** — measure external or policy-controlled information. Their scores are reported separately and never included in `core_quality_score`. The only currently defined contextual module is cross-market confluence. The Decision Policy decides how each contextual module's output is used.

**FROZEN** — The key sets of `core_module_scores` and `contextual_module_results` are disjoint. A module identifier may never appear in both.

---

---

# Part 3 — Frozen Inter-Layer Contracts

## 3.1 Contract Inventory

**FROZEN** — Four contracts govern all inter-layer data transfer:

```
DetectionResult/v1
SetupCandidate/v1      composed of DetectionResult/v1 + TradePlan/v1
ScoredSetup/v1
DecisionOutcome/v1
```

**FROZEN** — Versioning rules: adding a nullable field with a defined null default is a patch release. Any field removal, rename, type change, or semantic change requires incrementing the schema version and publishing a migration document. A consumer receiving an unrecognized `schema_version` must raise `SCHEMA_VERSION_MISMATCH` and refuse processing.

**FROZEN** — `SetupCandidate/v1` requires exactly `DetectionResult/v1` and `TradePlan/v1` as component versions. Cross-version composition requires a new outer schema version.

---

## 3.2 Auxiliary Type Library

**FROZEN**

```
Decimal {
    value: string       exact decimal string, e.g. "0.01", "749.4850"
}

Rational {
    numerator:      int
    denominator:    int     always > 0; never zero
    as_decimal():   Decimal computed on demand, never stored
}

DirectionalTickDistance {
    ticks:      int64       signed; positive = favorable direction
    tick_size:  Decimal
    to_price(): Decimal     ticks × tick_size
}

AbsoluteTickDistance {
    ticks:      int64       unsigned; always >= 0
    tick_size:  Decimal
    to_price(): Decimal     ticks × tick_size
}

PriceTicks {
    ticks:      int64       signed integer count from zero
    tick_size:  Decimal
    to_price(): Decimal     ticks × tick_size
}

Bar {
    bar_utc_ms:     int64       Unix milliseconds UTC, start of bar
    open:           PriceTicks
    high:           PriceTicks
    low:            PriceTicks
    close:          PriceTicks
    volume:         int64 | null
}

RuleFailure {
    rule_id:        string
    stage:          enum    LEVEL | BREAK | DISPLACEMENT | RETEST | REJECTION_CANDLE
    value_type:     enum    DECIMAL | INTEGER | BOOLEAN | ENUM | MISSING
    actual_value:   string | null   null if value_type = MISSING
    operator:       enum | null     GT | GTE | LT | LTE | EQ | NEQ
                                    null if not applicable
    required_value: string | null   null if value_type = MISSING
    unit:           string | null   "ticks"|"ratio"|"bars"|"pct"|"pts"|null
    message:        string          always present
}

RejectionAttempt {
    bar:            Bar
    failed_rules:   RuleFailure[]
}

SessionMetadata {
    symbol:                 string
    date:                   string      "YYYY-MM-DD" in market_timezone
    market_timezone:        string      "America/New_York"
    session_open_utc_ms:    int64
    session_close_utc_ms:   int64
    timeframe_seconds:      int
}

ModuleResult {
    enabled:                bool
    evaluation_status:      enum
        NOT_RUN             enabled=false or skipped
        SCORED              score is non-null, in [0.0, 1.0]
        DATA_UNAVAILABLE    required data absent or stale
        ERROR               internal failure; error_detail non-null
    score:                  Decimal | null  non-null iff SCORED
    weight:                 Decimal
    weighted_score:         Decimal | null  non-null iff SCORED; = score × weight
    input_fields:           string[]
    error_detail:           string | null   non-null iff ERROR
    notes:                  string
}
```

---

## 3.3 `DetectionResult/v1`

**FROZEN**

```
schema_version:         "DetectionResult/v1"
result_id:              string      UUID v4
produced_at:            string      ISO 8601 UTC processing timestamp
session:                SessionMetadata
preset_id:              string
engine_version:         string

status:                 enum        VALID | INVALID
failed_stage:           enum | null
    LEVEL_NOT_FOUND | BREAK_NOT_FOUND
    | DISPLACEMENT_MINIMUM_NOT_MET
    | RETEST_BEFORE_DISPLACEMENT
    | RETEST_NOT_FOUND | NO_QUALIFYING_REJECTION_CANDLE
    null if VALID
failed_rules:           RuleFailure[]   empty if VALID

── Level ──
level_price:            PriceTicks | null
level_source:           enum | null
    ORB_HIGH | ORB_LOW | PDH | PDL | PMH | PML | OB | SR
level_bar:              Bar | null
direction:              enum | null     LONG | SHORT

── Break ──
break_bar:                          Bar | null
directional_break_distance:         DirectionalTickDistance | null
    LONG:  close.ticks - level_price.ticks
    SHORT: level_price.ticks - close.ticks

── Displacement ──
displacement_window:                    Bar[]
displacement_bar_count:                 int | null
displacement_pts:                       AbsoluteTickDistance | null
    Maximum directional distance from level during displacement.
    LONG:  max over displacement_window bars of
           (bar.high.ticks - level_price.ticks)
    SHORT: max over displacement_window bars of
           (level_price.ticks - bar.low.ticks)
displacement_pct:                       Rational | null
    displacement_pts.to_price() / level_price.to_price()
rejection_side_clearance_by_bar:        DirectionalTickDistance[] | null
    Parallel to displacement_window[].
    LONG:  bar.low.ticks  - level_price.ticks
    SHORT: level_price.ticks - bar.high.ticks
    Signed; negative = bar crossed back through level.
    Sign is data; pass/fail is determined by preset RuleFailure.
minimum_rejection_side_clearance:       DirectionalTickDistance | null
    Minimum value across rejection_side_clearance_by_bar[].
average_rejection_side_clearance:       Decimal | null
    Mean of rejection_side_clearance_by_bar[].to_price() values.

── Retest ──
retest_window:                      Bar[]
retest_bar_count:                   int | null
failed_retest_count:                int | null
failed_retests:                     RejectionAttempt[]
bars_break_to_first_retest:         int | null
bars_break_to_confirmation:         int | null

retest_closest_approach:            AbsoluteTickDistance | null
    Smallest absolute distance between retest rejection-side
    extreme and the level across all retest_window bars.
    LONG:  min of abs(bar.low.ticks  - level_price.ticks)
    SHORT: min of abs(bar.high.ticks - level_price.ticks)
    Always non-negative. Zero = extreme touched level exactly.

retest_penetration_through_level:   AbsoluteTickDistance | null
    LONG:  max(0, level_price.ticks - min(retest bar low.ticks))
    SHORT: max(0, max(retest bar high.ticks) - level_price.ticks)
    Always non-negative. Zero if level was never crossed.

retest_displacement_retracement_pct: Rational | null
    Step 1 — closest_directional_position:
        LONG:  min over retest_window of (bar.low.ticks  - level_price.ticks)
        SHORT: min over retest_window of (level_price.ticks - bar.high.ticks)
        Most negative value = deepest retest.
    Step 2 — retraced_ticks:
        clamp(displacement_pts.ticks - closest_directional_position,
              0, displacement_pts.ticks)
    Step 3 — ratio:
        retraced_ticks / displacement_pts.ticks
    Meaning: 0.0 = retest at displacement peak; 1.0 = level reached.
    Null when displacement_pts is null or zero.

── Rejection Candle ──
confirmation_bar:                           Bar | null
confirmation_rej_wick:                      Rational | null
    LONG:  (min(open,close) - low)  / (high - low)
    SHORT: (high - max(open,close)) / (high - low)
    null if candle range is zero
confirmation_body:                          Rational | null
    abs(close - open) / (high - low); null if zero range
confirmation_opp_wick:                      Rational | null
    opposite-side wick / (high - low); null if zero range
confirmation_favorable_close_location:      Rational | null
    LONG:  (close - low)  / (high - low)   1.0 = close at high
    SHORT: (high - close) / (high - low)   1.0 = close at low
    null if zero range
confirmation_penetration:                   AbsoluteTickDistance | null
    LONG:  max(0, level_price.ticks - confirmation_bar.low.ticks)
    SHORT: max(0, confirmation_bar.high.ticks - level_price.ticks)
    Always non-negative.
confirmation_close_beyond_level:            DirectionalTickDistance | null
    LONG:  confirmation_bar.close.ticks - level_price.ticks
    SHORT: level_price.ticks - confirmation_bar.close.ticks
    Positive = close reclaimed the level in favorable direction.
```

---

## 3.4 `TradePlan/v1`

**FROZEN**

```
schema_version:         "TradePlan/v1"
entry_model:            enum
    CONFIRMATION_CLOSE
    BREAK_OF_SIGNAL_BAR
        LONG:  confirmation_bar.high.ticks + entry_buffer_ticks
        SHORT: confirmation_bar.low.ticks  - entry_buffer_ticks
entry_buffer_ticks:     int     >= 0
stop_buffer_ticks:      int     >= 0
tick_size:              Decimal
entry_price:            PriceTicks
stop_price:             PriceTicks
risk:                   AbsoluteTickDistance
    abs(entry_price.ticks - stop_price.ticks); always non-negative
r2_price:               PriceTicks
r3_price:               PriceTicks
r4_price:               PriceTicks
    LONG:  entry_price.ticks + R × risk.ticks
    SHORT: entry_price.ticks - R × risk.ticks
```

---

## 3.5 `SetupCandidate/v1`

**FROZEN**

```
schema_version:         "SetupCandidate/v1"
candidate_id:           string      UUID v4, distinct from result_id
composed_at:            string      ISO 8601 UTC processing timestamp
detection_result:       DetectionResult/v1
    Embedded in full. Unchanged. status must equal VALID.
    schema_version must equal "DetectionResult/v1" exactly.
trade_plan:             TradePlan/v1
    schema_version must equal "TradePlan/v1" exactly.
```

**FROZEN** — SetupCandidate is fully immutable after composition. The Quality Scorer is the only processing layer that consumes it as direct pipeline input. It accesses the embedded DetectionResult only through `SetupCandidate.detection_result`; there is no separate DetectionResult transfer to the Quality Scorer. Storage, audit, display, and backtesting systems may read finalized SetupCandidate records. No downstream component may modify any field.

---

## 3.6 `ScoredSetup/v1`

**FROZEN**

```
schema_version:             "ScoredSetup/v1"
scored_id:                  string      UUID v4
scored_at:                  string      ISO 8601 UTC processing timestamp
scorer_version:             string
setup:                      SetupCandidate/v1   embedded, unchanged

core_quality_score:         Decimal
    Σ(weighted_score for SCORED core modules)
    / Σ(weight for SCORED core modules)
    Range [0.0, 1.0]. null if no core module is SCORED.

core_quality_grade:         enum    A_PLUS | A | B | C | D
    Permanent. Never modified downstream. SKIP never appears here.
    Null if core_quality_score is null.

core_grade_thresholds:      dict
    { "A_PLUS": Decimal, "A": Decimal, "B": Decimal,
      "C": Decimal, "D": Decimal }
    Snapshot at scoring time.

core_module_scores:         dict[string, ModuleResult]
core_weights_snapshot:      dict[string, Decimal]
    One entry per key in core_module_scores.

contextual_module_results:  dict[string, ModuleResult]
contextual_weights_snapshot: dict[string, Decimal]
    One entry per key in contextual_module_results.

confluence_result:          ConfluenceResult | null
    null only if no confluence module is registered.
    Never null due to a policy setting.

ConfluenceResult {
    evaluation_status:      enum    SCORED | DATA_UNAVAILABLE | ERROR
    score:                  Decimal | null  non-null iff SCORED
    confluence_status:      enum
        CONFIRMING | NEUTRAL | CONFLICTING | DATA_UNAVAILABLE
    data_source:            string | null
    data_timestamp_utc_ms:  int64 | null
    input_fields:           string[]
}

module_features:            dict[string, any]
    Pre-computed features for reuse within scoring pass.
    Written by Quality Scorer modules only.
    Read by Decision Policy for display only.
    Must not influence eligibility or action decisions.
```

---

## 3.7 `DecisionOutcome/v1`

**FROZEN**

```
schema_version:         "DecisionOutcome/v1"
outcome_id:             string      UUID v4
decided_at:             string      ISO 8601 UTC processing timestamp
policy_id:              string
policy_version:         string
scored_setup:           ScoredSetup/v1   embedded, unchanged

quality_grade:          enum    A_PLUS | A | B | C | D
    Copied from scored_setup.core_quality_grade. Never modified.

── Policy Configuration Snapshot ──
policy_mode:            enum
    SHOW_ALL | FILTER_BY_GRADE | REQUIRE_CONFIRMATION | AUTO_EXECUTE

minimum_grade:          enum | null
    null if policy_mode = SHOW_ALL

grade_basis:            enum
    CORE_QUALITY        eligibility evaluated against quality_grade
    POLICY_COMPOSED     eligibility evaluated against policy_composed_grade
    Rules:
        If no active contextual module is SCORE_ONLY:
            grade_basis must be CORE_QUALITY.
        If one or more SCORE_ONLY modules exist:
            policy may choose either basis.
        Neither basis modifies core_quality_score or core_quality_grade.

contextual_policies:    dict[string, ContextualModulePolicy]

ContextualModulePolicy {
    module_id:              string
    mode:                   enum
        OFF | SCORE_ONLY | REQUIRED_CONFIRMATION | MANUAL_REVIEW
    threshold:              Decimal | null
    staleness_limit_ms:     int64 | null
}

── Policy-Composed Score ──
policy_composed_score:      Decimal | null
    Non-null iff at least one SCORE_ONLY contextual module is SCORED.
    = (Σ active core weighted_scores + Σ SCORE_ONLY SCORED weighted_scores)
      / (Σ active core weights + Σ SCORE_ONLY SCORED weights)
    Computed from stored weighted_score values only.
    No scoring function called. No market data read.

policy_composed_grade:      enum | null
    Derived from policy_composed_score using core_grade_thresholds.
    null if policy_composed_score is null.
    Advisory when grade_basis = CORE_QUALITY.
    Used for eligibility when grade_basis = POLICY_COMPOSED.
    Never replaces quality_grade.

── Contextual Application Records ──
contextual_applications:    dict[string, ContextualApplicationRecord]

ContextualApplicationRecord {
    module_id:              string
    mode_applied:           enum    OFF | SCORE_ONLY | REQUIRED_CONFIRMATION | MANUAL_REVIEW
    evaluation_status:      enum    NOT_RUN | SCORED | DATA_UNAVAILABLE | ERROR
    score_used:             Decimal | null  non-null iff SCORED
    data_available:         bool
    staleness_check_passed: bool | null
    threshold_applied:      Decimal | null
    outcome:                string
}

── Eligibility ──
eligibility:            enum
    ELIGIBLE | INELIGIBLE | MANUAL_REVIEW_REQUIRED

eligibility_reasons:    EligibilityReason[]
    Empty iff eligibility = ELIGIBLE.

EligibilityReason {
    reason_id:      string
    description:    string
}

── Action ──
action:             enum    DISPLAY | FLAG_FOR_REVIEW | EXECUTE | SKIP
action_reason:      string

── Display ──
display_config:     DisplayConfig {
    show_detection_audit:           bool
    show_failed_rules:              bool
    show_quality_breakdown:         bool
    show_module_features:           bool
    show_contextual_detail:         bool
    show_policy_composed_score:     bool
    show_eligibility_reasons:       bool
    highlight_grade:                bool
    chart_phase_default:            enum
        ALL | LEVEL | BREAK | DISPLACEMENT | RETEST | ENTRY
}
```

---

---

# Part 4 — Ownership Matrix

**FROZEN**

```
                      DetectionResult  SetupCandidate  ScoredSetup  DecisionOutcome
                      /v1              /v1             /v1          /v1
─────────────────────────────────────────────────────────────────────────────────────
Detection Engine      WRITE            WRITE           —            —

DetectionAuditStore   READ (all)       —               —            —

Quality Scorer        —                READ            WRITE        —
                                       (accesses
                                       DetectionResult
                                       only through
                                       SetupCandidate;
                                       no separate
                                       DetectionResult
                                       input)

Decision Policy       —                —               READ         WRITE

Storage/Display/      READ             READ            READ         READ
Backtesting/Audit

Execution Layer       —                —               —            READ
                                                                    (action=EXECUTE
                                                                    only)
─────────────────────────────────────────────────────────────────────────────────────
WRITE = produced once; immutable immediately after production
READ  = any field; must not modify
—     = no access of any kind
```

---

---

# Part 5 — Four Confluence Modes

**FROZEN**

**OFF** — The Decision Policy ignores `confluence_result.score` entirely. It may read `evaluation_status` for coverage logging. The module's weighted_score is excluded from `policy_composed_score`. `quality_grade` is unchanged. `score_used = null` in the application record.

**SCORE_ONLY** — The Decision Policy reads `weighted_score` from each SCORE_ONLY contextual module where `evaluation_status = SCORED`. Modules with `DATA_UNAVAILABLE` or `ERROR` are excluded from both numerator and denominator. If at least one SCORE_ONLY module is SCORED, `policy_composed_score` and `policy_composed_grade` are computed. When `grade_basis = POLICY_COMPOSED`, `policy_composed_grade` is used against `minimum_grade` for eligibility. `quality_grade` is never changed. If all SCORE_ONLY modules are unavailable, `policy_composed_score` is null and an explicit configured fallback applies.

**REQUIRED_CONFIRMATION** — `quality_grade` is preserved. If confluence `score >= threshold`, data is available, and data is not stale: normal eligibility evaluation continues. If confluence `score < threshold`, or `evaluation_status ≠ SCORED`, or staleness check fails: `eligibility = INELIGIBLE`, `action = SKIP`, reason recorded. `policy_composed_score` is null in this mode.

**MANUAL_REVIEW** — Regardless of confluence score value, `eligibility = MANUAL_REVIEW_REQUIRED` and `action = FLAG_FOR_REVIEW`. The trader receives the full confluence detail and decides. No automatic execution. `quality_grade` is preserved.

In every mode: `core_quality_score` and `core_quality_grade` are untouched. Confluence affects eligibility and action only.

---

---

# Part 6 — Frozen Invariants

**FROZEN** — 44 invariants. These are the acceptance test targets for implementation.

---

## Detection Engine (INV-D)

```
INV-D-01    Every DetectionResult carries a unique result_id (UUID v4).

INV-D-02    status=VALID → failed_stage=null, failed_rules=[].

INV-D-03    status=INVALID → failed_stage≠null, failed_rules non-empty.

INV-D-04    All price fields use PriceTicks with explicit tick_size.
            No raw float, integer, or string price values in any
            DetectionResult price field.

INV-D-05    len(rejection_side_clearance_by_bar)
            == len(displacement_window).
            Length consistency only. Sign of values unconstrained.

INV-D-06    Each rejection_side_clearance_by_bar[i] uses:
            LONG:  displacement_window[i].low.ticks  - level_price.ticks
            SHORT: level_price.ticks - displacement_window[i].high.ticks
            Whether negative values constitute rule failure is
            determined by the active preset, recorded in RuleFailure.
            The schema does not assert it.

INV-D-07    bar_utc_ms values within a single session are
            strictly increasing.

INV-D-08    status=VALID → confirmation_bar ≠ null.

INV-D-09    Every RuleFailure references a stage ≤ failed_stage.

INV-D-10    produced_at is ISO 8601 UTC. It must not be derived from
            or substituted for any bar_utc_ms. Coincidental equality
            is not a violation.

INV-D-11    retest_closest_approach.ticks >= 0 always.

INV-D-12    retest_penetration_through_level.ticks >= 0 always.

INV-D-13    retest_penetration_through_level = 0 when no retest bar's
            rejection-side extreme crossed through the level.

INV-D-14    confirmation_penetration.ticks >= 0 always.

INV-D-15a   retest_displacement_retracement_pct is null iff
            displacement_pts is null or displacement_pts.ticks = 0.

INV-D-15b   closest_directional_position is the minimum value across
            all retest_window bars using the direction-neutral formula.
            It is the deepest retest, not the shallowest.

INV-D-15c   retraced_ticks = clamp(
                displacement_pts.ticks - closest_directional_position,
                0, displacement_pts.ticks).
            Guarantees retraced_ticks ∈ [0, displacement_pts.ticks].

INV-D-15d   Resulting Rational: numerator=retraced_ticks,
            denominator=displacement_pts.ticks.
            Value ∈ [0, 1]. Denominator never zero given INV-D-15a.

INV-D-16    RuleFailure with value_type=MISSING has
            actual_value=null and required_value=null.

INV-D-17    RuleFailure with value_type=BOOLEAN or ENUM
            has operator=null where comparison semantics do not apply.

INV-D-18    If confirmation_bar has high.ticks == low.ticks (zero range),
            confirmation_rej_wick, confirmation_body, confirmation_opp_wick,
            and confirmation_favorable_close_location are all null.

INV-D-19    A zero-range confirmation_bar produces a RuleFailure with
            rule_id="REJECTION_CANDLE.ZERO_RANGE", value_type=BOOLEAN,
            actual_value="true", required_value="false", operator=null.

INV-D-20    Rational.denominator is never zero. Operations producing
            a zero denominator yield null and a RuleFailure.
```

---

## SetupCandidate (INV-C)

```
INV-C-01    detection_result.status == VALID always.
            Consumer must reject any SetupCandidate where this fails.

INV-C-02    detection_result.schema_version == "DetectionResult/v1" exactly.
            trade_plan.schema_version == "TradePlan/v1" exactly.
            Unrecognized component versions → SCHEMA_VERSION_MISMATCH.

INV-C-03    trade_plan.risk.ticks
            == abs(entry_price.ticks - stop_price.ticks).

INV-C-04    LONG:  rN_price.ticks == entry_price.ticks + N × risk.ticks
            SHORT: rN_price.ticks == entry_price.ticks - N × risk.ticks
            for N ∈ {2, 3, 4}.

INV-C-05    trade_plan.tick_size == detection_result.level_price.tick_size.

INV-C-06    candidate_id ≠ detection_result.result_id.

INV-C-07    No field of detection_result or trade_plan is modified
            after composed_at is set.

INV-C-08    composed_at is ISO 8601 UTC. Not derived from any bar_utc_ms.
            Coincidental equality is not a violation.
```

---

## ScoredSetup (INV-S)

```
INV-S-01    scored_setup.setup is semantically identical to the
            SetupCandidate received as input. Every field unchanged.

INV-S-02    ModuleResult with evaluation_status=SCORED:
            score ∈ [0.0, 1.0], weighted_score = score × weight.

INV-S-03    core_quality_score includes only SCORED core modules
            in numerator and denominator.

INV-S-04    core_quality_grade is the correct bucket for
            core_quality_score given core_grade_thresholds.

INV-S-05    ModuleResult with evaluation_status ∈
            {NOT_RUN, DATA_UNAVAILABLE, ERROR}:
            score = null, weighted_score = null.

INV-S-06    core_weights_snapshot: one entry per key in core_module_scores.
            contextual_weights_snapshot: one entry per key in
            contextual_module_results.

INV-S-07    confluence_result is null only if no confluence module
            is registered. Never null due to a policy setting.

INV-S-08    confluence_result.evaluation_status ≠ SCORED →
            score=null, confluence_status=DATA_UNAVAILABLE.

INV-S-09    core_quality_grade ∈ {A_PLUS, A, B, C, D}. SKIP never appears.

INV-S-10    core_module_scores and contextual_module_results have
            disjoint key sets.

INV-S-11    scored_at is ISO 8601 UTC. Not derived from any bar_utc_ms.
            Coincidental equality is not a violation.

INV-S-12    module_features contains no references that modify
            SetupCandidate. All entries are independently computed.

INV-S-13    core_quality_score uses only SCORED modules in numerator
            and denominator.

INV-S-14    If no core module achieves SCORED, core_quality_score=null
            and core_quality_grade=null. This is a pipeline error.

INV-S-15    ModuleResult with evaluation_status=ERROR: error_detail ≠ null.

INV-S-16    ModuleResult with evaluation_status ≠ ERROR: error_detail=null.
```

---

## DecisionOutcome (INV-O)

```
INV-O-01    scored_setup is semantically identical to the ScoredSetup
            received as input.

INV-O-02    quality_grade == scored_setup.core_quality_grade. Never modified.

INV-O-03    confluence modes appear only in contextual_policies inside
            DecisionOutcome. Absent from ScoredSetup and SetupCandidate.

INV-O-04    Contextual module with mode=REQUIRED_CONFIRMATION:
            if score_used < threshold_applied, or evaluation_status ≠ SCORED,
            or staleness_check_passed=false → eligibility=INELIGIBLE,
            action=SKIP.

INV-O-05    Contextual module with mode=OFF:
            score_used=null; module excluded from policy_composed_score.

INV-O-06    policy_composed_score is non-null iff at least one SCORE_ONLY
            contextual module has evaluation_status=SCORED.

INV-O-07    Contextual module with mode=MANUAL_REVIEW →
            eligibility=MANUAL_REVIEW_REQUIRED, action=FLAG_FOR_REVIEW.

INV-O-08    action=SKIP → outcome not forwarded to execution layer.

INV-O-09    action=EXECUTE → eligibility=ELIGIBLE.

INV-O-10    policy_mode=SHOW_ALL → minimum_grade=null,
            action ∈ {DISPLAY, FLAG_FOR_REVIEW}.

INV-O-11    decided_at is ISO 8601 UTC. Not derived from any bar_utc_ms.
            Coincidental equality is not a violation.

INV-O-12    policy_composed_grade, when non-null, derived from
            policy_composed_score using core_grade_thresholds.
            Does not replace quality_grade.

INV-O-13    eligibility_reasons is empty iff eligibility=ELIGIBLE.

INV-O-14    grade_basis=POLICY_COMPOSED →
            policy_composed_score ≠ null, policy_composed_grade ≠ null.

INV-O-15    grade_basis=CORE_QUALITY →
            eligibility evaluated against quality_grade.

INV-O-16    grade_basis=POLICY_COMPOSED →
            eligibility evaluated against policy_composed_grade.

INV-O-17    grade_basis=POLICY_COMPOSED may only be set when at least
            one active contextual module has mode=SCORE_ONLY.

INV-O-18    reason_id="POLICY_COMPOSED_GRADE_BELOW_MINIMUM" applies
            only to policy_composed_grade, never to quality_grade.

INV-O-19    grade_basis=POLICY_COMPOSED and policy_composed_score=null →
            Decision Policy must apply an explicit configured fallback.
            Implicit or unrecorded behavior is not permitted.
```

---

## Cross-Schema (INV-X)

```
INV-X-01    result_id, candidate_id, scored_id, outcome_id are all
            distinct UUID v4 values within one pipeline run.

INV-X-02    produced_at ≤ composed_at ≤ scored_at ≤ decided_at.

INV-X-03    Unrecognized schema_version → SCHEMA_VERSION_MISMATCH,
            refuse processing.

INV-X-04    tick_size is identical across all price fields within
            one pipeline run for one candidate.

INV-X-05    No layer modifies any object it did not write.

INV-X-06    core_module_scores and contextual_module_results have
            disjoint key sets across the entire pipeline.

INV-X-07    produced_at, composed_at, scored_at, decided_at are
            ISO 8601 UTC strings. bar_utc_ms are Unix ms integers.
            Neither may be derived from or substituted for the other.
```

---

---

# Part 7 — Frozen Trading Logic Specification

## 7.1 Detection Stages and Their Frozen Meaning

**FROZEN** — The Detection Engine evaluates every candidate through exactly these stages in order, stopping at the first failure:

**Stage 1 — Level.** Identify the price level from the configured level source. If no level can be established, status = INVALID, failed_stage = LEVEL_NOT_FOUND.

**Stage 2 — Confirmed Break.** A candle must close beyond the level. LONG: `close > level`. SHORT: `close < level`. A wick that extends beyond the level without a closing confirmation does not qualify. First qualifying bar is the `break_bar`.

**Stage 3 — Displacement.** After the break bar, price must move sufficiently far away from the level before the retest begins. Displacement ends when the first retest bar's rejection-side extreme touches or crosses the level.

The engine computes `displacement_pts` (type `AbsoluteTickDistance`): the maximum directional distance, in ticks, reached during the displacement window.

```
LONG:  displacement_pts.ticks =
           max over displacement_window bars of
           (bar.high.ticks - level_price.ticks)

SHORT: displacement_pts.ticks =
           max over displacement_window bars of
           (level_price.ticks - bar.low.ticks)
```

The minimum-distance gate is a binary pass/fail rule:

```
PASS:  displacement_pts.ticks >= min_displacement_ticks
FAIL:  displacement_pts.ticks <  min_displacement_ticks
```

If the gate fails, status = INVALID, failed_stage = DISPLACEMENT_MINIMUM_NOT_MET. A structured RuleFailure is recorded with stage = DISPLACEMENT, actual_value = displacement_pts.ticks, operator = GTE, required_value = min_displacement_ticks, unit = "ticks". The DetectionResult is retained in the DetectionAuditStore for research.

Passing this gate does not automatically satisfy any additional independent Stage-3 rules that may be defined in future presets. If such rules exist and are enabled, each is evaluated independently. Failure of any enabled Stage-3 rule produces INVALID with its own RuleFailure. The minimum-distance gate is the only currently defined Stage-3 rule.

**Structural classification — IMMEDIATE_BREAK_RETEST** `FROZEN`

A displacement phase must exist BEFORE the first retest contact begins. This means at least one completed post-break bar must have its LOW strictly above the level before any bar touches or penetrates the level from above.

If the first post-break bar immediately contacts the level (its LOW ≤ level), the structural failure is:

```
failure_reason:          RETEST_BEFORE_DISPLACEMENT
sequence_classification: IMMEDIATE_BREAK_RETEST
```

Definition: the first post-break contact with the level occurred before any completed post-break displacement phase existed.

This is not a geometry failure and not a numeric threshold failure. It is a structural classification: the level was never abandoned before it was retested. The Break, Displacement, Retest, and Rejection phases cannot all begin and end within one or two candles. A valid BDRR requires chronological separation between the break and the retest.

IMMEDIATE_BREAK_RETEST sequences produce a DetectionResult with:
```
status:       INVALID
failed_stage: RETEST_BEFORE_DISPLACEMENT
failed_rules: [{rule_id:      "DISP.RETEST_BEFORE_DISPLACEMENT",
                value_type:   INTEGER,
                actual_value: "0",
                operator:     GTE,
                required_value: "1",
                unit:         "bars",
                message:      "first post-break bar contacted the level;
                               no displacement phase existed before retest began"}]
```

Strategies may later define a separate preset category for IMMEDIATE_BREAK_RETEST if they wish to trade them, but they are not BDRR setups.

**Stage 4 — Retest.** Price returns toward the level. A retest begins when a bar's rejection-side extreme reaches or crosses the level. All bars from first retest contact through the confirmation bar comprise the `retest_window`.

**Stage 5 — Rejection Candle.** The engine scans retest bars in order. A bar that touches the level but fails geometry rules is recorded as a `RejectionAttempt` and the scan continues. The first bar that satisfies all geometry thresholds becomes the `confirmation_bar`. If no qualifying bar is found, status = INVALID, failed_stage = NO_QUALIFYING_REJECTION_CANDLE.

**FROZEN** — The six geometry fields evaluated on every retest bar:
- `confirmation_rej_wick` — rejection-side wick ratio
- `confirmation_body` — body ratio
- `confirmation_opp_wick` — opposite-side wick ratio
- `confirmation_favorable_close_location` — close position toward continuation extreme
- `confirmation_penetration` — wick crossed beyond the level
- `confirmation_close_beyond_level` — close reclaimed the level

**FROZEN** — A zero-range candle automatically fails Stage 5 with a structured RuleFailure. Its ratios are null.

---

## 7.2 Entry and Stop Computation

**FROZEN** — Two entry models:

`CONFIRMATION_CLOSE`: entry = close of confirmation_bar (adjusted by entry_buffer_ticks × tick_size in the favorable direction).

`BREAK_OF_SIGNAL_BAR`: entry = confirmation_bar.high + entry_buffer_ticks × tick_size (LONG) or confirmation_bar.low − entry_buffer_ticks × tick_size (SHORT). Triggered intrabar — does not wait for next bar close.

**FROZEN** — Stop: confirmation_bar.low − stop_buffer_ticks × tick_size (LONG) or confirmation_bar.high + stop_buffer_ticks × tick_size (SHORT).

**FROZEN** — Risk: `abs(entry_price.ticks − stop_price.ticks)`.

**FROZEN** — Targets: 2R, 3R, 4R computed using direction sign.

---

## 7.3 Direction Neutrality

**FROZEN** — All schema fields, formulas, and invariants apply identically to LONG and SHORT. No field has a meaning that changes between directions. Direction is read from `detection_result.direction`.

---

---

# Part 8 — Configurable Parameters

All parameters below have frozen meanings. Their values are set per preset and have not been numerically decided in this session unless explicitly noted.

---

## 8.1 Level Source

**CONFIGURABLE VALUE**
```
parameter:      level_source
type:           enum
allowed values: ORB_HIGH | ORB_LOW | PDH | PDL | PMH | PML | OB | SR
meaning:        which price level the Detection Engine identifies as
                the structural reference for the setup
frozen value:   ORB_HIGH (used in prototype only; not the production default)
```

## 8.2 ORB Duration

**CONFIGURABLE VALUE**
```
parameter:      orb_duration_minutes
type:           int
meaning:        number of minutes comprising the opening range window,
                starting from session open
frozen value:   5 (used in prototype only)
```

## 8.3 Chart Timeframe

**CONFIGURABLE VALUE**
```
parameter:      timeframe_seconds
type:           int
allowed values: 60 | 120 | 300 | 600 | 900
meaning:        bar duration in seconds
frozen value:   300 (used in prototype only)
```

## 8.4 Direction

**CONFIGURABLE VALUE**
```
parameter:      direction
type:           enum
allowed values: LONG | SHORT | BOTH
meaning:        which trade direction(s) the engine searches for
frozen value:   LONG (used in prototype only)
```

## 8.5 Minimum Displacement

**CONFIGURABLE VALUE**

```
parameter:      min_displacement_ticks
type:           int
constraint:     >= 1
meaning:        minimum value that displacement_pts.ticks must reach
                during the displacement window for this gate to pass.
pass condition: displacement_pts.ticks >= min_displacement_ticks
layer:          Detection Engine, Stage 3 (binary gate)
status:         CONFIGURABLE VALUE — threshold value not yet decided
```

This is the sole absolute tick-distance criterion within this gate. Percentage distance, ATR multiple, minimum bar count, momentum threshold, and volume threshold are not part of this parameter. They remain separate undecided possible Stage-3 rules.

## 8.6 Rejection Candle Geometry Thresholds

**CONFIGURABLE VALUE**
```
min_rejection_wick_ratio        Rational    minimum rejection-side wick / range
    frozen prototype value: 0.47

max_body_ratio                  Rational    maximum body / range
    frozen prototype value: 0.40

min_favorable_close_location    Rational    minimum close toward continuation extreme
    frozen prototype value: 0.80

min_penetration_ticks           int         minimum wick crossing of level in ticks
    not decided

min_close_beyond_level_ticks    int         minimum close reclaim distance in ticks
    not decided
```

## 8.7 Entry Model

**CONFIGURABLE VALUE**
```
parameter:      entry_model
type:           enum
allowed values: CONFIRMATION_CLOSE | BREAK_OF_SIGNAL_BAR
frozen prototype value: CONFIRMATION_CLOSE
```

## 8.8 Entry Buffer

**CONFIGURABLE VALUE**
```
parameter:      entry_buffer_ticks
type:           int         >= 0
meaning:        tick buffer added to entry price beyond confirmation extreme
frozen prototype value: 0
```

## 8.9 Stop Buffer

**CONFIGURABLE VALUE**
```
parameter:      stop_buffer_ticks
type:           int         >= 0
meaning:        tick buffer beyond confirmation candle extreme for stop
frozen prototype value: 0
```

## 8.10 Tick Size

**CONFIGURABLE VALUE**
```
parameter:      tick_size
type:           Decimal
meaning:        instrument minimum price increment
frozen prototype value: "0.01" (SPY equity, NYSE Arca)
note:           must not use CME futures tick size for equity instruments
```

## 8.11 Targets

**CONFIGURABLE VALUE**
```
parameter:      active_targets
type:           set of enum values  {R2, R3, R4}
meaning:        which R-multiple targets to compute and display
frozen prototype value: {R2, R3, R4}
note:           the production target selection model (fixed R, trailing,
                partial exits) has not been decided
```

## 8.12 Session Window

**CONFIGURABLE VALUE**
```
parameters:
    session_open_utc_ms     int64
    session_close_utc_ms    int64
    market_timezone         string
meaning:        the time window within which bars are considered
```

## 8.13 Core Quality Module Weights

**CONFIGURABLE VALUE** — per module, per preset
```
modules (all weights open):
    displacement.rejection_side_clearance
    displacement.momentum
    displacement.empty_space
    retest.penetration_depth
    retest.reclaim_strength
    retest.time_elapsed
    structure.failed_retest_count
    structure.visual_cleanliness
```

## 8.14 Quality Grade Thresholds

**CONFIGURABLE VALUE**
```
A_PLUS threshold    Decimal     not decided
A threshold         Decimal     not decided
B threshold         Decimal     not decided
C threshold         Decimal     not decided
D threshold         Decimal     0.00 (floor, effectively frozen)
```

## 8.15 Contextual Module Policies

**CONFIGURABLE VALUE** — per contextual module, per preset
```
mode:               enum    OFF | SCORE_ONLY | REQUIRED_CONFIRMATION | MANUAL_REVIEW
threshold:          Decimal | null
staleness_limit_ms: int64 | null
```

## 8.16 Grade Basis

**CONFIGURABLE VALUE**
```
parameter:      grade_basis
type:           enum
allowed values: CORE_QUALITY | POLICY_COMPOSED
constraint:     POLICY_COMPOSED requires at least one active SCORE_ONLY module
```

## 8.17 Minimum Policy Grade

**CONFIGURABLE VALUE**
```
parameter:      minimum_grade
type:           enum    A_PLUS | A | B | C | D | null
meaning:        grade floor for eligibility under the active grade_basis
```

## 8.18 Policy Mode

**CONFIGURABLE VALUE**
```
parameter:      policy_mode
type:           enum
allowed values: SHOW_ALL | FILTER_BY_GRADE | REQUIRE_CONFIRMATION | AUTO_EXECUTE
```

---

---

# Part 9 — Explicitly Open Decisions

**OPEN** — Whether additional independent Stage-3 displacement rules will be defined, and if so, which. Candidates identified but not decided:
- minimum percentage distance from level
- minimum ATR multiple
- minimum bar count during displacement
- momentum or body threshold
- volume threshold

Each would be evaluated independently if enabled. None is a sub-parameter of `min_displacement_ticks`. None has been specified or approved.

**OPEN** — Definition of "empty space" as a computable feature. Intuition established (gap between level and displacement candles), formula not defined.

**OPEN** — Displacement quality scoring module: formula, inputs, and weight.

**OPEN** — Visual cleanliness scoring module: definition and formula.

**OPEN** — Cross-market confluence module: which instruments, what comparison, what formula.

**OPEN** — Market structure module: definition not started.

**OPEN** — All quality module weights and all grade threshold values.

**OPEN** — Production target selection model: whether to use fixed R-multiples, trailing stops, partial exits, or a combination.

**OPEN** — Opening noise delay: whether to exclude bars within N minutes of session open from entry consideration.

**OPEN** — Maximum time between breakout and entry: whether a setup ages out.

**OPEN** — Multi-retest behavior: whether the engine continues scanning after more than one failed retest, and whether a maximum failed retest count invalidates the setup in Layer 1 or only penalizes it in Layer 2.

**OPEN** — SHORT direction validation: the prototype was built and verified for LONG only. SHORT detection logic has not been tested against real data.

**FROZEN** — QQQ 5-minute cross-instrument validation completed. Same frozen rules, no threshold changes. 60 sessions scanned, 4 valid BDRR setups found (2026-04-29, 2026-05-06, 2026-05-13, 2026-07-14). RETEST_BEFORE_DISPLACEMENT structural gate excluded 29 sessions (48%). All geometry thresholds applied identically. QQQ is an ETF, not a futures instrument — futures validation (MES, MNQ) remains pending. Tick size for QQQ: $0.01.

**OPEN** — Instruments beyond QQQ: MES, MNQ futures tick sizes and session parameters; NVDA, TSLA, AMZN, META, MSFT, GOOGL, MU equities. Futures cross-instrument validation remains pending.

**OPEN** — DetectionAuditStore: storage format, query interface, and retention policy.

**OPEN** — SCORE_ONLY null fallback policy: the contract requires an explicit configured fallback when all SCORE_ONLY modules are unavailable; the allowed fallback options have not been enumerated.

**OPEN** — Error handling policy when `core_quality_score = null` (no SCORED core modules).

---

---

# Part 10 — Implementation Work

## Completed

**FROZEN** — Prototype: `brr_prototype.html` — isolated static page demonstrating Break–Retest–Rejection detection against the SPY 5-minute dataset for three candidates. Commit `42a722b`.

**FROZEN** — Candidate review: `orb_candidate_review.html` — isolated specification reference page. Commit `87b3b28`, revised at `cdbaea8`.

**FROZEN** — Dashboard link: `index.html` header updated with links to both review pages. No production strategy logic changed.

**FROZEN** — Prototype detection logic (Python, single-session script, not production engine):
```
Level source:       ORB High (09:30 candle)
Timeframe:          SPY 5-minute
Direction:          LONG
Break rule:         close > level
Displacement:       bars with low > level before retest (prototype approximation)
Geometry preset:    rej_wick >= 0.47, body <= 0.40, close_loc >= 0.80
Entry model:        CONFIRMATION_CLOSE, 0 tick buffer
Stop:               candle low, 0 tick buffer
Targets:            2R, 3R, 4R
Dataset:            SPY_5m.csv, 60 days, 2026-04-24 to 2026-07-21
Candidates found:   exactly 3
```

**FROZEN** — Three validated prototype candidates:
```
2026-05-26  level 750.44  bk 09:40  2 failed retests  entry 11:05
            rej_wick 67%  body 20%  close_loc 87%
            entry 750.89  stop 750.37  risk 0.52
            2R 751.94  3R 752.47  4R 752.99

2026-06-08  level 744.51  bk 10:55  1 failed retest   entry 11:20
            rej_wick 74%  body 10%  close_loc 84%
            entry 744.93  stop 744.14  risk 0.79
            2R 746.51  3R 747.30  4R 748.09

2026-07-06  level 749.49  bk 10:20  1 failed retest   entry 10:40
            rej_wick 84%  body  3%  close_loc 87%
            entry 749.67  stop 749.10  risk 0.57
            2R 750.81  3R 751.38  4R 751.95  (2R reached)
```


**FROZEN** — SPY blind validation candidate (2026-05-05) — rejected by structural rule:
```
Date:         2026-05-05
Level:        722.30 (ORB High)
Break:        09:50 · C=722.37 · directional_break_distance = +7 ticks
Displacement: 0 bars · displacement_pts = 49 ticks (breakout bar high only)
Retest:       09:55 (first post-break bar) · L=722.29 ≤ 722.30
Classification: IMMEDIATE_BREAK_RETEST (RETEST_BEFORE_DISPLACEMENT)
Result:       INVALID — no displacement phase existed before retest began
Review page:  brr_validation_20260505.html
Commits:      ce3da52 (initial), 6b11ac3, c0c038b, 046fdcb (visual fixes)
```

**FROZEN** — RETEST_BEFORE_DISPLACEMENT structural rule validated by full rescan:
```
Dataset:      SPY 5-minute, 60 sessions (all dates except 3 documented)
Rule applied: at least 1 completed post-break bar with LOW > level
              must exist before first retest contact
Results:
  IMMEDIATE_BREAK_RETEST:           35 sessions (58%)
  NO_QUALIFYING_REJECTION_CANDLE:   12 sessions
  BREAK_NOT_FOUND:                   8 sessions
  RETEST_NOT_FOUND:                  2 sessions
  Valid BDRR (excluding documented):  0 sessions
Conclusion:   The 60-day SPY dataset contains exactly 3 BDRR setups.
              The structural rule excludes the majority of candidate
              sequences as market-structure failures, not geometry failures.
Commit:       7c4d298
```

**FROZEN** — QQQ 5-minute cross-instrument validation:
```
Dataset:      QQQ_5m.csv, 60 sessions, 2026-04-24 to 2026-07-21
Instrument:   QQQ ETF (Nasdaq-100 proxy, not futures)
Rules:        Identical to SPY validation — no threshold changes
Results:
  Sessions scanned:                  60
  Valid BDRR setups:                  4
  RETEST_BEFORE_DISPLACEMENT:        29 (48%)
  NO_QUALIFYING_REJECTION_CANDLE:    12
  BREAK_NOT_FOUND:                    9
  RETEST_NOT_FOUND:                   6
Conclusion:   Break→Displacement→Retest→Rejection structure is not
              SPY-specific. RETEST_BEFORE_DISPLACEMENT gate applies
              meaningfully on a second instrument with unchanged rules.
Review page:  bdrr_qqq_validation.html
Commits:      3ab7df8 (initial), c33634f, 94ba64c (fixes)
```

**FROZEN** — Four QQQ valid candidates with chronological outcomes:
```
2026-04-29  level 658.84  bk 09:50  disp 1bar/43t  failed 6  entry 11:55
            rej_wick 73.4%  body 14.9%  close_loc 88.3%
            entry 659.37  stop 658.54  risk 0.83/83t
            2R 661.03  3R 661.86  4R 662.69
            Outcome: STOPPED at 12:20 (L=658.53 ≤ 658.54) before any target
            MFE before stop: +0.79 pts (12:10)
            Realized: −0.83 pts / −1R

2026-05-06  level 689.16  bk 09:40  disp 1bar/133t  failed 0  entry 09:50
            rej_wick 58.1%  body 31.5%  close_loc 89.7%
            entry 690.28  stop 688.46  risk 1.82/182t
            2R 693.92  3R 695.74  4R 697.56
            Outcome: STOPPED at 10:00 (L=688.28 ≤ 688.46) before any target
            MFE before stop: +0.24 pts (09:55)
            Realized: −1.82 pts / −1R

2026-05-13  level 709.95  bk 10:50  disp 6bars/158t  failed 0  entry 11:25
            rej_wick 81.6%  body 18.4%  close_loc 81.6%
            entry 710.57  stop 709.85  risk 0.72/72t
            2R 712.01  3R 712.73  4R 713.45
            Outcome: 4R reached (12:20, H=713.66); no stop before any target
            MFE before exit: +6.08 pts  MAE before exit: −0.62 pts

2026-07-14  level 720.29  bk 11:05  disp 6bars/199t  failed 15  entry 13:15
            rej_wick 61%  body 38%  close_loc 99%
            entry 720.825  stop 720.14  risk 0.685/69t
            2R 722.195  3R 722.88  4R 723.565
            Outcome: STOPPED at 14:40 (L=720.07 ≤ 720.14) before any target
            Realized: −0.685 pts / −1R
```

**FROZEN** — Chronological outcome evaluation rule:
```
For a LONG trade, chronological priority:
  1. If bar.low <= stop before any target is reached: STOPPED OUT.
     No subsequent target may be reported as achieved.
  2. If bar.high >= target before stop is reached: TARGET HIT.
  3. If bar.low <= stop AND bar.high >= target in the same 5-minute bar
     and intrabar order is unavailable: AMBIGUOUS (not a win).
MFE and MAE accumulate only until the first terminal event (stop or final target).
Post-exit price movement is not attributed to the trade.
```

---

## Authorized but Not Started

**NOT STARTED** — Production Detection Engine implementing `DetectionResult/v1` and `SetupCandidate/v1` exactly as specified.

**NOT STARTED** — Quality Scorer implementing `ScoredSetup/v1`.

**NOT STARTED** — Decision Policy implementing `DecisionOutcome/v1`.

**NOT STARTED** — Full Strategy Builder UI with configurable presets.

**NOT STARTED** — DetectionAuditStore for INVALID candidate research.

**NOT STARTED** — Backtesting pipeline consuming finalized pipeline objects.

**NOT STARTED** — SHORT direction implementation and validation.

**NOT STARTED** — Multi-instrument dataset integration.

**NOT STARTED** — Acceptance tests derived from the 44 invariants.

---

---

# Part 11 — Last Milestone and Next Deliverable

**FROZEN** — Last completed milestone: Full blind validation of the BDRR detection logic on two instruments (SPY and QQQ, 60 sessions each). The RETEST_BEFORE_DISPLACEMENT structural rule is frozen and confirmed meaningful on both instruments. Chronological outcome evaluation rules are frozen. The four-contract architecture, 44 invariants, and frozen preset are unchanged. All validation review pages are committed to the repository.

**FROZEN** — Validated SPY setup count: 3 (all documented with geometry, entry, stop, targets, and outcomes).

**FROZEN** — Validated QQQ setup count: 4 (all documented; outcomes corrected to chronological stop-first evaluation).

**FROZEN** — Next agreed deliverable: Production Detection Engine, implemented to populate `DetectionResult/v1` and `SetupCandidate/v1` exactly as specified, verified against the three SPY prototype candidates and the four QQQ candidates as the acceptance test suite (7 total). The implementation must pass all INV-D and INV-C invariants. The RETEST_BEFORE_DISPLACEMENT structural rule must be enforced before any geometry check is reached.

---

---

# Part 12 — Current Executable Implementation Status

**FROZEN** — This section is a status record of code and tests as they exist in this repository at the end of this session. It documents what now runs, not a new specification decision. It does not alter, override, or supersede any FROZEN threshold, formula, or validation finding recorded earlier in this document (Parts 1–11). Historical validation findings and oracle values are unchanged.

## 12.1 Starting Point

**FROZEN** — At the start of this session, the repository contained the frozen BDRR specification (this document) and static validation review pages (`brr_prototype.html`, `brr_validation_20260505.html`, `bdrr_qqq_validation.html`, `orb_candidate_review.html`) with hand-computed, hardcoded results. No executable BDRR detection engine existed anywhere in the repository.

## 12.2 Engine Module

**FROZEN** — A new, isolated, deterministic engine module now exists at `estrategie/bdrr_engine.js`. It is separate from `index.html` and from the PDH/PDL and ORB strategy code already in the repository. It has no UI integration.

*(Note on path: the instruction text for this task said `strategie/bdrr_engine.js`. The actual, existing directory in this repository is `estrategie/`. This document records the real path so a new session is not sent looking for a nonexistent folder.)*

## 12.3 Implemented and Tested Stages

**Stage 1 — Session Context**
- accepts a single trading session's candles (one ET calendar date) per call
- timezone: America/New_York
- session open: 09:30

**Stage 1 — ORB Construction**
- 5-minute ORB window
- uses only the first session candle beginning at 09:30
- ORB High is the active detection level
- ORB Low is returned for completeness but is not an active detection level

**Stage 2 — Break**
- LONG direction only
- first candle whose close is strictly greater than ORB High
- a wick beyond the level without a qualifying close does not count
- no numeric minimum break-distance threshold

**Stage 3 — Displacement**
- the breakout candle itself is never counted as a displacement bar
- displacement begins on the first post-break candle
- a displacement bar exists only while its low is strictly greater than the level
- the first candle whose low is less than or equal to the level begins the retest and ends the displacement window
- `RETEST_BEFORE_DISPLACEMENT` is a mandatory structural failure when zero displacement bars exist before that first contact
- `min_displacement_ticks` is disabled in the initial preset (no numeric threshold applied)

**Stage 4 — Retest Window**
- begins at the first retest contact (that candle is included in the window)
- multiple retest attempts are allowed
- no maximum failed-retest count is imposed
- no setup-age limit is imposed
- penetration-through-level and displacement-retracement-percentage metrics are computed and reported per contact
- no rejection qualification of any kind is performed in Stage 4

**Stage 5 — Rejection Qualification**
- LONG / ORB_HIGH only
- the first qualifying rejection candle is selected chronologically; scanning stops immediately once it is found
- minimum rejection wick ratio: 0.47
- maximum body ratio: 0.40
- minimum favorable close location: 0.80
- minimum penetration threshold: disabled (reported, not gated)
- minimum close-beyond-level threshold: disabled (reported, not gated)
- opposite wick ratio is reported only, never gated
- a zero-range candle cannot qualify
- no candle after the confirmation candle is inspected (no look-ahead)

## 12.4 Current Exported Functions

**FROZEN** — `estrategie/bdrr_engine.js` currently exports exactly:
```
buildSessionContext
buildORB
findBreak
findDisplacement
findRetestWindow
findRejection
```
No entry, stop, target, position-sizing, or outcome-evaluation function exists in this module.

## 12.5 Current Validated Oracle Fixtures

**FROZEN** — Machine-readable oracle fixtures exist at:
```
dati/bdrr_spy_oracle.json
dati/bdrr_qqq_oracle.json
```
Candidate coverage:
- SPY: 3 VALID candidates + 1 INVALID research candidate (2026-05-05, structurally rejected under `RETEST_BEFORE_DISPLACEMENT`; its trade-plan-shaped fields are explicitly marked non-executable/hypothetical)
- QQQ: 4 VALID candidates

These fixtures transcribe already-documented values only; no new detection results were computed to build them beyond two explicitly frozen corrections (SPY 2026-05-26 stop-breach timestamp; QQQ 2026-05-13 displacement window and MFE-through-terminal-target).

## 12.6 Current Tests and Latest Reported Results

**FROZEN** — Test files (actual path `estrategie/`, not `strategie/` — see note in §12.2):
```
estrategie/test_bdrr_oracle_validation.js
estrategie/test_bdrr_stage1_stage2.js
estrategie/test_bdrr_stage3.js
estrategie/test_bdrr_stage4.js
estrategie/test_bdrr_stage5.js
```
Latest reported results:
- oracle validation: 263 checks, 0 failures
- Stage 1/2: 35 checks, 0 failures
- Stage 3: 39 checks, 0 failures
- Stage 4: 41 checks, 0 failures
- Stage 5: 127 checks, 0 failures

## 12.7 Not Yet Implemented

**NOT STARTED** — Full pipeline orchestrator (single call chaining Stage 1 through Stage 5 across a multi-day dataset).

**NOT STARTED** — Trade plan construction.

**NOT STARTED** — Entry calculation module.

**NOT STARTED** — Stop calculation module.

**NOT STARTED** — Target calculation module.

**NOT STARTED** — Chronological outcome evaluation.

**NOT STARTED** — Same-bar ambiguity evaluation.

**NOT STARTED** — MFE/MAE engine.

**NOT STARTED** — Multi-day runner.

**NOT STARTED** — Backtest aggregation.

**NOT STARTED** — Strategy configuration UI.

**NOT STARTED** — ORB Low execution (structurally present in the schema; not exercised by the engine).

**NOT STARTED** — SHORT execution (structurally present in the schema; not exercised by the engine).

**NOT STARTED** — 1-minute data validation.

**NOT STARTED** — MES/MNQ futures validation.

**NOT STARTED** — Paper or live bot execution.

## 12.8 Next Session Starting Point

**FROZEN** — The next Claude session must begin with trade planning and chronological outcome evaluation, built on top of the existing Stage 1–5 engine (`estrategie/bdrr_engine.js`) unchanged. It should not re-derive or re-validate Stage 1–5; those are tested and frozen as of this section.

---

---

# Architectural Freeze Statement

**Detection determines whether the setup exists.** The Detection Engine runs a deterministic structural algorithm against raw market data. It produces a `DetectionResult` for every candidate examined and a `SetupCandidate` for every VALID result. Distance fields that are declared non-negative use formulas that guarantee non-negativity. Retracement is measured from the deepest directional position, not the shallowest. The Stage-3 minimum-distance gate is a binary rule using `displacement_pts.ticks`. The Stage-3 structural rule (`RETEST_BEFORE_DISPLACEMENT`) is a second binary gate: at least one completed post-break bar must have its LOW strictly above the level before any retest contact. Sequences that fail this rule are classified as `IMMEDIATE_BREAK_RETEST` and are INVALID regardless of geometry. Both Stage-3 rules are frozen. The engine's output is immutable the moment it is written.

**Quality measures the setup's intrinsic worth.** The Quality Scorer reads the `SetupCandidate` and computes `core_quality_score` and `core_quality_grade` from core structural modules only, using only those modules that achieved `evaluation_status = SCORED`. These values are a permanent record of the setup's quality at the moment it was scored. They are written once and never changed by any downstream process. The Quality Scorer has no knowledge of the active Decision Policy mode. It does not decide what to do when contextual data is unavailable — it records the status and reports it.

**Contextual modules measure external or policy-controlled information.** Confluence and any future contextual module compute independent scores that the Quality Scorer reports alongside the core quality record. A contextual module that cannot produce a score records `evaluation_status = DATA_UNAVAILABLE` or `ERROR`. The Quality Scorer does not decide the consequence of that condition. It measures what it can and reports what it found.

**Decision Policy decides eligibility and action.** The Decision Policy reads the `ScoredSetup` and applies the active configuration. It selects a `grade_basis` and evaluates eligibility accordingly. For SCORE_ONLY modules, it composes a policy-level score from already-produced `weighted_score` values, excluding any module that did not achieve SCORED status. If no SCORE_ONLY module was successfully scored, it applies an explicitly configured fallback. All decisions — including how to handle missing contextual data — are declared in the policy configuration, recorded in `DecisionOutcome`, and traceable. The Decision Policy never calls a scoring function, never reads raw market data, and never changes `core_quality_grade`.

**No downstream layer rewrites upstream truth.** Every pipeline object is immutable after the layer that owns it writes it. The quality grade the scorer computed, the candidate the engine built, and the detection result the engine recorded are permanent facts. They do not change because policy changed, because weights changed, or because contextual data became available or unavailable after scoring. The pipeline produces a complete, consistent, and reprocessable audit trail at every stage.

---

*End of canonical handoff document. All gaps are labeled. No content derived from outside the originating session.*
