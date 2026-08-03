# Max Bot — Evidence Validation

> **Date:** 2026-08-03
> **Reference:** MAX_BOT_SPEC.md Version 0.3 (AUTHORITATIVE DRAFT — NOT FROZEN)
>
> This document is a companion to the Max Bot specification. Its
> purpose is to validate or challenge the specification against real
> trading sessions reviewed by Max.
>
> This document does not contain trading rules. It contains evidence.

---

## Judgment Categories

Every data point in a session record carries one of three labels:

| Label | Meaning |
|---|---|
| **OBSERVED FACT** | Directly visible in market data (prices, timestamps, candle geometry). Verifiable from CSV or chart. |
| **MAX LABEL** | Supplied by Max from discretionary review (e.g. "I would enter here", "this is A+ quality", "daily context is TREND_DOWN"). Cannot be derived from data alone. |
| **DERIVED INTERPRETATION** | Inferred by applying the current V0.3 draft to observed facts (e.g. "this constitutes displacement" per the spec's conceptual definition). May be wrong. |

A DERIVED INTERPRETATION must never be presented as though Max
confirmed it. If Max later reviews a session and disagrees with a
derived interpretation, that disagreement is recorded in the
Contradiction Log, not silently resolved by changing the spec.

---

## Session Record Schema

Each reviewed session captures the following fields. Fields marked
with `[judgment]` must carry one of: OBSERVED_FACT, MAX_LABEL, or
DERIVED_INTERPRETATION.

```
SESSION IDENTIFICATION
    session_id              — unique identifier (format: INSTRUMENT_YYYY-MM-DD)
    instrument              — ticker symbol [OBSERVED_FACT]
    date                    — trading date [OBSERVED_FACT]
    data_source             — where the data comes from (CSV path, TradingView screenshot, etc.)

DAILY CONTEXT
    daily_context           — TREND_UP / TREND_DOWN / RANGE [MAX_LABEL]
    daily_context_rationale — Max's explanation of why [MAX_LABEL]
    price_vs_pdh_pdl        — above PDH / below PDL / between [OBSERVED_FACT]

STRUCTURAL LEVELS (PRE-OPEN)
    pdh                     — Previous Day High price [OBSERVED_FACT]
    pdl                     — Previous Day Low price [OBSERVED_FACT]
    pmh                     — Pre-Market High price [OBSERVED_FACT]
    pml                     — Pre-Market Low price [OBSERVED_FACT]

ORB
    orb_high                — ORB High price [OBSERVED_FACT]
    orb_low                 — ORB Low price [OBSERVED_FACT]

ORB BREAK
    orb_break_direction     — LONG / SHORT / NONE [OBSERVED_FACT]
    orb_break_timestamp     — time of first close outside ORB [OBSERVED_FACT]
    orb_break_status        — OBSERVED / DISPLACEMENT_PENDING /
                              DISPLACEMENT_CONFIRMED [DERIVED_INTERPRETATION]
    orb_displacement_evidence
                            — description of candles/separation supporting
                              or rejecting displacement [DERIVED_INTERPRETATION]

ACTIVE MARKET STORY
    story_direction         — LONG / SHORT / NONE [DERIVED_INTERPRETATION]
    story_status            — WAITING / BREAK_OBSERVED / DISPLACEMENT_PENDING /
                              DISPLACEMENT_CONFIRMED / RETEST_PENDING /
                              RETEST_IN_PROGRESS / REJECTION_CONFIRMED /
                              ENTRY_ELIGIBLE / STORY_INVALIDATED /
                              STORY_EXPIRED [DERIVED_INTERPRETATION]

SETUP-DEFINING LEVEL
    setup_level_source      — which level (ORB_HIGH, PML, OCL, etc.) [OBSERVED_FACT or MAX_LABEL]
    setup_level_price       — price of the level [OBSERVED_FACT]
    setup_level_price_far   — far edge if zone, null if line [OBSERVED_FACT]
    setup_level_family      — STRUCTURAL / GENERATED [OBSERVED_FACT]
    setup_break_timestamp   — when the setup level was broken [OBSERVED_FACT]
    setup_displacement_evidence
                            — description of displacement at the setup level
                              [DERIVED_INTERPRETATION]

CONTEXTUALLY CONNECTED LEVELS
    structural_levels_connected
                            — list of Structural Levels contextually connected
                              to the story [DERIVED_INTERPRETATION]
    generated_levels_connected
                            — list of Generated Levels contextually connected
                              to the story [DERIVED_INTERPRETATION]
    ocl_reference           — OCL identifier if applicable, null otherwise
                              [OBSERVED_FACT]

RETEST AND REJECTION
    retest_start_timestamp  — when price begins returning to the setup level
                              [OBSERVED_FACT]
    retest_development      — description of how the retest unfolds
                              [OBSERVED_FACT]
    rejection_event         — description of the rejection candle/action
                              [OBSERVED_FACT]

ENTRY OPPORTUNITY
    entry_opportunity_id    — unified identifier for this single entry
                              opportunity across all timeframes
    tf_1m_reading           — Max Entry Candle judgment on 1m
                              (VALID / INVALID / MESSY / NOT_APPLICABLE)
                              [DERIVED_INTERPRETATION]
    tf_1m_candle_timestamp  — timestamp of the 1m candle evaluated [OBSERVED_FACT]
    tf_2m_reading           — Max Entry Candle judgment on 2m [DERIVED_INTERPRETATION]
    tf_2m_candle_timestamp  — [OBSERVED_FACT]
    tf_5m_reading           — Max Entry Candle judgment on 5m [DERIVED_INTERPRETATION]
    tf_5m_candle_timestamp  — [OBSERVED_FACT]

ALIGNMENT
    alignment_state         — ALIGNED / WAITING / OPPOSING / NOT_APPLICABLE
                              [OBSERVED_FACT or MAX_LABEL]
    alignment_instruments   — which instruments were checked [MAX_LABEL]
    alignment_detail        — description of what was observed [MAX_LABEL]

MAX DECISION
    max_decision            — ENTER / SKIP / WAIT / AMBIGUOUS [MAX_LABEL]
    max_decision_rationale  — Max's plain-language explanation [MAX_LABEL]

TRADE DETAILS (if entered)
    entry_price             — actual or intended entry price [OBSERVED_FACT or MAX_LABEL]
    entry_timestamp         — [OBSERVED_FACT or MAX_LABEL]
    stop_mode               — ENTRY_CANDLE / FULL_ZONE [MAX_LABEL]
    stop_price              — actual or intended stop price [OBSERVED_FACT or MAX_LABEL]
    target_2r_price         — calculated 2R target [DERIVED_INTERPRETATION]
    outcome                 — WIN / LOSS / SCRATCH / NOT_TAKEN / UNKNOWN
                              [OBSERVED_FACT]

STORY CONTINUATION
    story_invalidation_events
                            — list of events that invalidated the story, if any
                              [OBSERVED_FACT]
    stopped_trade_story_active
                            — after a stop-out, did the Market Story remain
                              active? YES / NO / NOT_APPLICABLE [DERIVED_INTERPRETATION]
    second_entry_opportunity — did a second valid entry opportunity appear?
                              YES / NO / NOT_APPLICABLE [DERIVED_INTERPRETATION]

SPECIFICATION VALIDATION
    spec_claims_supported   — list of V0.3 claims this example supports
                              [DERIVED_INTERPRETATION]
    spec_claims_contradicted
                            — list of V0.3 claims this example contradicts or
                              leaves unresolved [DERIVED_INTERPRETATION]
    new_questions_raised    — open questions surfaced by this example
                              [DERIVED_INTERPRETATION]
```

---

## Seeded Sessions

The six sessions from the V0.3 Evidence Base, initialized with
available information. Missing data marked as REQUIRES_MAX_REVIEW
or UNKNOWN.

---

### Session 1: MNQ_2026-07-31

```
SESSION IDENTIFICATION
    session_id:             MNQ_2026-07-31
    instrument:             MNQ (Micro E-mini Nasdaq-100 Futures)
    date:                   2026-07-31
    data_source:            TradingView screenshots provided by Max (1m)

DAILY CONTEXT
    daily_context:          REQUIRES_MAX_REVIEW
    daily_context_rationale: REQUIRES_MAX_REVIEW
    price_vs_pdh_pdl:       UNKNOWN — PDH/PDL not marked on screenshots

STRUCTURAL LEVELS (PRE-OPEN)
    pdh:                    UNKNOWN — not visible on screenshots
    pdl:                    UNKNOWN — not visible on screenshots
    pmh:                    UNKNOWN — not marked on screenshots
    pml:                    ~28,542.50 [OBSERVED_FACT — visible on chart as
                            red line labeled "PML"]

ORB
    orb_high:               ~28,711.50 [OBSERVED_FACT — green line on chart]
    orb_low:                ~28,542.50 [OBSERVED_FACT — red line labeled
                            "orb low", near PML]

ORB BREAK
    orb_break_direction:    SHORT [OBSERVED_FACT — price moved below ORB Low]
    orb_break_timestamp:    UNKNOWN — exact time not visible
    orb_break_status:       DISPLACEMENT_CONFIRMED [DERIVED_INTERPRETATION —
                            price moved well below ORB Low with force]
    orb_displacement_evidence:
                            Multiple candles closing fully below ORB Low,
                            visible separation. Max described it as "pushing
                            like crazy downward." [DERIVED_INTERPRETATION
                            from MAX_LABEL description]

ACTIVE MARKET STORY
    story_direction:        SHORT [DERIVED_INTERPRETATION]
    story_status:           ENTRY_ELIGIBLE [DERIVED_INTERPRETATION — Max
                            entered the trade]

SETUP-DEFINING LEVEL
    setup_level_source:     OCL + PML confluence [MAX_LABEL — Max identified
                            both levels]
    setup_level_price:      ~28,542.50 area [OBSERVED_FACT]
    setup_level_price_far:  UNKNOWN — OCL wick edges not precisely measured
    setup_level_family:     STRUCTURAL (PML) + GENERATED (OCL)
    setup_break_timestamp:  UNKNOWN — part of the ORB break move
    setup_displacement_evidence:
                            Price broke PML during the same move that broke
                            ORB Low. Multiple candles below PML before
                            retest. [DERIVED_INTERPRETATION]

CONTEXTUALLY CONNECTED LEVELS
    structural_levels_connected:
                            ORB_LOW, PML [DERIVED_INTERPRETATION]
    generated_levels_connected:
                            OCL (formed during break) [OBSERVED_FACT from
                            Max's description]
    ocl_reference:          One candle at ~28,542 area — first green candle
                            in the downward move [MAX_LABEL]

RETEST AND REJECTION
    retest_start_timestamp: UNKNOWN — price pulled back after making lows
    retest_development:     Price descended, consolidated, then returned
                            to the OCL/PML zone [OBSERVED_FACT from chart]
    rejection_event:        Candle at 8:57 — red candle wicking into
                            OCL zone and rejecting completely [MAX_LABEL +
                            OBSERVED_FACT]

ENTRY OPPORTUNITY
    entry_opportunity_id:   MNQ_2026-07-31_001
    tf_1m_reading:          VALID [MAX_LABEL — Max identified this as his
                            "perfect setup"]
    tf_1m_candle_timestamp: ~08:57 CT [MAX_LABEL]
    tf_2m_reading:          UNKNOWN — not evaluated
    tf_2m_candle_timestamp: UNKNOWN
    tf_5m_reading:          UNKNOWN — not evaluated
    tf_5m_candle_timestamp: UNKNOWN

ALIGNMENT
    alignment_state:        UNKNOWN — not discussed for this session
    alignment_instruments:  UNKNOWN
    alignment_detail:       UNKNOWN

MAX DECISION
    max_decision:           ENTER [MAX_LABEL]
    max_decision_rationale: "This is my perfect setup. The market broke
                            the ORB downward, pushed through PML, created
                            an order block at PML, continued lower, then
                            returned to retest the OCL which also retests
                            PML. The 8:57 candle wicks in and rejects
                            completely." [MAX_LABEL — paraphrased from
                            voice transcript]

TRADE DETAILS
    entry_price:            UNKNOWN — close of the 8:57 candle
    entry_timestamp:        ~08:57 CT [MAX_LABEL]
    stop_mode:              REQUIRES_MAX_REVIEW — Max mentioned both
                            ENTRY_CANDLE and FULL_ZONE as options
    stop_price:             REQUIRES_MAX_REVIEW
    target_2r_price:        UNKNOWN — depends on stop
    outcome:                UNKNOWN — not discussed

STORY CONTINUATION
    story_invalidation_events:  NONE observed [OBSERVED_FACT from chart]
    stopped_trade_story_active: NOT_APPLICABLE
    second_entry_opportunity:   NOT_APPLICABLE

SPECIFICATION VALIDATION
    spec_claims_supported:
        - ORB break establishes session direction
        - Displacement is visible (multiple candles below)
        - OCL forms during the break move
        - Confluence of Structural (PML) + Generated (OCL) at same price
        - Retest of OCL = retest of broken PML
        - Max Entry Candle: wick inside zone, close outside
        - Market Story as narrative sequence, not checklist
        - Setup-defining break (PML) distinct from ORB break
    spec_claims_contradicted:
        NONE identified from this example
    new_questions_raised:
        - Max mentioned two stop options — which did he choose?
        - What was the outcome of the trade?
        - What was the daily context assessment?
```

---

### Session 2: MNQ_2026-07-29

```
SESSION IDENTIFICATION
    session_id:             MNQ_2026-07-29
    instrument:             MNQ
    date:                   2026-07-29
    data_source:            TradingView screenshot provided by Max (1m)

DAILY CONTEXT
    daily_context:          REQUIRES_MAX_REVIEW — Max mentioned
                            "downtrend but ranging" in general
    daily_context_rationale: REQUIRES_MAX_REVIEW
    price_vs_pdh_pdl:       UNKNOWN

STRUCTURAL LEVELS (PRE-OPEN)
    pdh:                    UNKNOWN
    pdl:                    UNKNOWN
    pmh:                    UNKNOWN
    pml:                    ~27,819 area [OBSERVED_FACT — blue line
                            labeled "PML" on chart]

ORB
    orb_high:               ~27,965 area [OBSERVED_FACT — green line]
    orb_low:                ~27,850 area [OBSERVED_FACT — red line]

ORB BREAK
    orb_break_direction:    SHORT [OBSERVED_FACT]
    orb_break_timestamp:    UNKNOWN
    orb_break_status:       DISPLACEMENT_CONFIRMED [DERIVED_INTERPRETATION]
    orb_displacement_evidence:
                            Price broke below ORB Low and continued
                            downward with momentum [DERIVED_INTERPRETATION]

ACTIVE MARKET STORY
    story_direction:        SHORT [DERIVED_INTERPRETATION]
    story_status:           ENTRY_ELIGIBLE [DERIVED_INTERPRETATION]

SETUP-DEFINING LEVEL
    setup_level_source:     PML + OCL confluence [MAX_LABEL]
    setup_level_price:      ~27,819 area [OBSERVED_FACT]
    setup_level_price_far:  UNKNOWN
    setup_level_family:     STRUCTURAL (PML) + GENERATED (OCL)
    setup_break_timestamp:  UNKNOWN
    setup_displacement_evidence:
                            Price bounced on PML a couple of times
                            (acting as support), then broke below and
                            separated [MAX_LABEL + OBSERVED_FACT]

CONTEXTUALLY CONNECTED LEVELS
    structural_levels_connected:
                            ORB_LOW, PML [DERIVED_INTERPRETATION]
    generated_levels_connected:
                            OCL at 8:49 [MAX_LABEL]
    ocl_reference:          One Candle at 8:49 near PML break point
                            [MAX_LABEL]

RETEST AND REJECTION
    retest_start_timestamp: UNKNOWN
    retest_development:     Price pulled back to PML/OCL zone
                            [OBSERVED_FACT]
    rejection_event:        Candle wicking into OCL/PML zone and
                            rejecting [MAX_LABEL]

ENTRY OPPORTUNITY
    entry_opportunity_id:   MNQ_2026-07-29_001
    tf_1m_reading:          VALID [MAX_LABEL — "must open short
                            immediately"]
    tf_1m_candle_timestamp: UNKNOWN — near the retest
    tf_2m_reading:          UNKNOWN
    tf_2m_candle_timestamp: UNKNOWN
    tf_5m_reading:          UNKNOWN
    tf_5m_candle_timestamp: UNKNOWN

ALIGNMENT
    alignment_state:        UNKNOWN
    alignment_instruments:  UNKNOWN
    alignment_detail:       UNKNOWN

MAX DECISION
    max_decision:           ENTER [MAX_LABEL]
    max_decision_rationale: "Same pattern as two days later. Price breaks
                            ORB, bounces on PML, breaks PML, retests PML
                            and the one candle at 8:49. Must open short
                            immediately." [MAX_LABEL — paraphrased]

TRADE DETAILS
    entry_price:            UNKNOWN
    entry_timestamp:        UNKNOWN
    stop_mode:              REQUIRES_MAX_REVIEW
    stop_price:             REQUIRES_MAX_REVIEW
    target_2r_price:        UNKNOWN
    outcome:                UNKNOWN

STORY CONTINUATION
    story_invalidation_events:  UNKNOWN
    stopped_trade_story_active: UNKNOWN
    second_entry_opportunity:   UNKNOWN

SPECIFICATION VALIDATION
    spec_claims_supported:
        - PML acts as support before break, then resistance after
        - OCL forms at the structural break point
        - Confluence Structural + Generated
        - Same pattern repeats across sessions (structural consistency)
        - Break → displacement → retest → rejection sequence
    spec_claims_contradicted:
        NONE identified
    new_questions_raised:
        - PML bounced twice before breaking — does the number of
          bounces before break affect displacement quality?
        - Support-to-resistance flip: should this be documented as
          a distinct concept in the spec?
```

---

### Session 3: TSLA_2026-07-09

```
SESSION IDENTIFICATION
    session_id:             TSLA_2026-07-09
    instrument:             TSLA
    date:                   2026-07-09
    data_source:            TradingView screenshots provided by Max (5m, 1m zoom)

DAILY CONTEXT
    daily_context:          TREND_DOWN [MAX_LABEL — Max drew downtrend
                            arrow on daily chart]
    daily_context_rationale: "Downtrend on daily" [MAX_LABEL]
    price_vs_pdh_pdl:       UNKNOWN — need to check if price was
                            above/below PDH at open

STRUCTURAL LEVELS (PRE-OPEN)
    pdh:                    ~399 area [OBSERVED_FACT — green line
                            labeled "PDH" on chart]
    pdl:                    ~391 area [OBSERVED_FACT — red line
                            labeled "PDL"]
    pmh:                    ~396 area [OBSERVED_FACT — green line
                            labeled "PMH"]
    pml:                    UNKNOWN — not visible on screenshots

ORB
    orb_high:               UNKNOWN — not explicitly marked
    orb_low:                UNKNOWN — not explicitly marked

ORB BREAK
    orb_break_direction:    LONG [DERIVED_INTERPRETATION — price moved
                            upward through multiple levels]
    orb_break_timestamp:    UNKNOWN
    orb_break_status:       DISPLACEMENT_CONFIRMED [DERIVED_INTERPRETATION —
                            price broke through ORB, PMH, and PDH]
    orb_displacement_evidence:
                            Price broke through three levels upward in
                            sequence [OBSERVED_FACT from chart]

ACTIVE MARKET STORY
    story_direction:        LONG [DERIVED_INTERPRETATION — despite daily
                            downtrend, intraday action overrides]
    story_status:           ENTRY_ELIGIBLE [DERIVED_INTERPRETATION]

SETUP-DEFINING LEVEL
    setup_level_source:     PMH [MAX_LABEL — blue arrow points to
                            retest of PMH]
    setup_level_price:      ~396 area [OBSERVED_FACT]
    setup_level_price_far:  null (line level) [OBSERVED_FACT]
    setup_level_family:     STRUCTURAL
    setup_break_timestamp:  UNKNOWN — during the upward move
    setup_displacement_evidence:
                            Price broke above PMH, continued to break
                            PDH, then returned to PMH [OBSERVED_FACT]

CONTEXTUALLY CONNECTED LEVELS
    structural_levels_connected:
                            ORB_HIGH, PMH, PDH [DERIVED_INTERPRETATION]
    generated_levels_connected:
                            Max mentioned OCL possibilities during the
                            move but did not explicitly identify one
                            [REQUIRES_MAX_REVIEW]
    ocl_reference:          REQUIRES_MAX_REVIEW

RETEST AND REJECTION
    retest_start_timestamp: UNKNOWN
    retest_development:     Price pulled back to PMH after reaching
                            above PDH [OBSERVED_FACT]
    rejection_event:        Candle wicking into PMH and closing above
                            — blue arrow marks this [MAX_LABEL +
                            OBSERVED_FACT]

ENTRY OPPORTUNITY
    entry_opportunity_id:   TSLA_2026-07-09_001
    tf_1m_reading:          UNKNOWN — 1m not explicitly evaluated
    tf_1m_candle_timestamp: UNKNOWN
    tf_2m_reading:          UNKNOWN
    tf_2m_candle_timestamp: UNKNOWN
    tf_5m_reading:          VALID [MAX_LABEL — Max identified the
                            entry on the 5m view]
    tf_5m_candle_timestamp: UNKNOWN

ALIGNMENT
    alignment_state:        UNKNOWN — not discussed
    alignment_instruments:  UNKNOWN
    alignment_detail:       UNKNOWN

MAX DECISION
    max_decision:           ENTER [MAX_LABEL — "I would enter there"]
    max_decision_rationale: "Daily is downtrend but the market broke
                            ORB High, PMH, and PDH upward. When it
                            pulls back to PMH, look at that candle —
                            it wicks in and closes above. I enter
                            long." [MAX_LABEL — paraphrased]

TRADE DETAILS
    entry_price:            UNKNOWN
    entry_timestamp:        UNKNOWN
    stop_mode:              REQUIRES_MAX_REVIEW
    stop_price:             REQUIRES_MAX_REVIEW
    target_2r_price:        UNKNOWN
    outcome:                UNKNOWN

STORY CONTINUATION
    story_invalidation_events:  UNKNOWN
    stopped_trade_story_active: UNKNOWN
    second_entry_opportunity:   UNKNOWN

SPECIFICATION VALIDATION
    spec_claims_supported:
        - Intraday overrides daily trend when evidence is strong
        - Multiple levels broken in same direction = strong conviction
        - Daily trend is weight, not gate
        - Max Entry Candle geometry: wick in, close outside
        - Entry on Structural Level without OCL is valid
    spec_claims_contradicted:
        NONE identified
    new_questions_raised:
        - How many levels must break to override the daily trend?
        - Was there an OCL during this move that Max chose not to
          use, or was the PMH retest sufficient alone?
        - Stop placement on a structural line level (PMH) — which
          mode?
```

---

### Session 4: TSLA_various (trend-aligned, ORB Low + OCL)

```
SESSION IDENTIFICATION
    session_id:             TSLA_VARIOUS_TREND_ALIGNED
    instrument:             TSLA
    date:                   UNKNOWN — Max did not specify exact date
    data_source:            TradingView screenshot provided by Max (1m zoom)

DAILY CONTEXT
    daily_context:          TREND_DOWN [MAX_LABEL — daily downtrend
                            confirmed]
    daily_context_rationale: REQUIRES_MAX_REVIEW
    price_vs_pdh_pdl:       UNKNOWN

STRUCTURAL LEVELS (PRE-OPEN)
    pdh:                    UNKNOWN
    pdl:                    UNKNOWN
    pmh:                    UNKNOWN
    pml:                    UNKNOWN

ORB
    orb_high:               UNKNOWN — not labeled on zoomed screenshot
    orb_low:                Visible as orange line labeled "ORB LOW"
                            [OBSERVED_FACT]

ORB BREAK
    orb_break_direction:    SHORT [OBSERVED_FACT]
    orb_break_timestamp:    UNKNOWN
    orb_break_status:       DISPLACEMENT_CONFIRMED [DERIVED_INTERPRETATION]
    orb_displacement_evidence:
                            Large red candle breaking well below ORB Low
                            with clear separation [OBSERVED_FACT]

ACTIVE MARKET STORY
    story_direction:        SHORT [DERIVED_INTERPRETATION]
    story_status:           ENTRY_ELIGIBLE [DERIVED_INTERPRETATION]

SETUP-DEFINING LEVEL
    setup_level_source:     ORB_LOW + OCL confluence [MAX_LABEL]
    setup_level_price:      ORB Low area [OBSERVED_FACT]
    setup_level_price_far:  UNKNOWN
    setup_level_family:     STRUCTURAL (ORB_LOW) + GENERATED (OCL)
    setup_break_timestamp:  UNKNOWN
    setup_displacement_evidence:
                            Price broke aggressively below ORB Low
                            [OBSERVED_FACT]

CONTEXTUALLY CONNECTED LEVELS
    structural_levels_connected:    ORB_LOW [DERIVED_INTERPRETATION]
    generated_levels_connected:     OCL near ORB_LOW [MAX_LABEL]
    ocl_reference:          Order block labeled on chart [OBSERVED_FACT]

RETEST AND REJECTION
    retest_start_timestamp: UNKNOWN
    retest_development:     Price returned to ORB Low / OCL zone
                            [OBSERVED_FACT]
    rejection_event:        Candle labeled "ORDER BLOCK RETESTED" on
                            chart — wick into zone, rejection
                            [OBSERVED_FACT + MAX_LABEL]

ENTRY OPPORTUNITY
    entry_opportunity_id:   TSLA_VARIOUS_001
    tf_1m_reading:          VALID [MAX_LABEL]
    tf_1m_candle_timestamp: UNKNOWN
    tf_2m_reading:          UNKNOWN
    tf_2m_candle_timestamp: UNKNOWN
    tf_5m_reading:          UNKNOWN
    tf_5m_candle_timestamp: UNKNOWN

ALIGNMENT
    alignment_state:        UNKNOWN
    alignment_instruments:  UNKNOWN
    alignment_detail:       UNKNOWN

MAX DECISION
    max_decision:           ENTER [MAX_LABEL]
    max_decision_rationale: "Simple case. Daily downtrend, ORB breaks
                            short, OCL forms near ORB Low, retest has
                            confluence. Short, stop above entry candle,
                            2R target." [MAX_LABEL — paraphrased]

TRADE DETAILS
    entry_price:            UNKNOWN
    entry_timestamp:        UNKNOWN
    stop_mode:              ENTRY_CANDLE [MAX_LABEL — "stop above the
                            entry candle"]
    stop_price:             UNKNOWN — above the high of the entry candle
    target_2r_price:        UNKNOWN
    outcome:                UNKNOWN

STORY CONTINUATION
    story_invalidation_events:  UNKNOWN
    stopped_trade_story_active: UNKNOWN
    second_entry_opportunity:   UNKNOWN

SPECIFICATION VALIDATION
    spec_claims_supported:
        - Trend-aligned setup is the simplest/highest quality
        - ORB_LOW + OCL confluence
        - ENTRY_CANDLE stop mode confirmed for this type
        - 2R target policy
    spec_claims_contradicted:
        NONE identified
    new_questions_raised:
        - This is the only session where Max explicitly confirmed
          ENTRY_CANDLE stop mode. Is this his default?
```

---

### Session 5: TSLA_2026-02-02

```
SESSION IDENTIFICATION
    session_id:             TSLA_2026-02-02
    instrument:             TSLA
    date:                   2026-02-02
    data_source:            TradingView Replay screenshot provided by Max (1m)

DAILY CONTEXT
    daily_context:          REQUIRES_MAX_REVIEW
    daily_context_rationale: REQUIRES_MAX_REVIEW
    price_vs_pdh_pdl:       UNKNOWN

STRUCTURAL LEVELS (PRE-OPEN)
    pdh:                    UNKNOWN
    pdl:                    UNKNOWN
    pmh:                    UNKNOWN
    pml:                    UNKNOWN

ORB
    orb_high:               Visible on chart [OBSERVED_FACT]
    orb_low:                Visible on chart, labeled "ORB LOW"
                            [OBSERVED_FACT]

ORB BREAK
    orb_break_direction:    NONE [MAX_LABEL — "price stays inside ORB,
                            makes range, I do nothing"]
    orb_break_timestamp:    N/A
    orb_break_status:       N/A — no break occurred
    orb_displacement_evidence: N/A

ACTIVE MARKET STORY
    story_direction:        NONE [DERIVED_INTERPRETATION]
    story_status:           WAITING_FOR_BREAK [DERIVED_INTERPRETATION]

SETUP-DEFINING LEVEL
    setup_level_source:     N/A
    All remaining fields:   N/A — no trade, no setup

MAX DECISION
    max_decision:           SKIP [MAX_LABEL — "I do nothing"]
    max_decision_rationale: "Price stays inside the ORB and ranges all
                            session. No break, no trade." [MAX_LABEL]

TRADE DETAILS
    All fields:             N/A — no trade taken

SPECIFICATION VALIDATION
    spec_claims_supported:
        - No entries without a valid ORB break (Rule 1)
        - Day can end with zero trades — this is acceptable
        - ORB break is the prerequisite for any setup
    spec_claims_contradicted:
        NONE identified
    new_questions_raised:
        NONE from this example
```

---

### Session 6: TSLA_2026-01-29

```
SESSION IDENTIFICATION
    session_id:             TSLA_2026-01-29
    instrument:             TSLA
    date:                   2026-01-29
    data_source:            TradingView Replay screenshots provided by Max
                            (1m and 2m views of same session)

DAILY CONTEXT
    daily_context:          REQUIRES_MAX_REVIEW
    daily_context_rationale: REQUIRES_MAX_REVIEW
    price_vs_pdh_pdl:       UNKNOWN

STRUCTURAL LEVELS (PRE-OPEN)
    pdh:                    UNKNOWN
    pdl:                    UNKNOWN
    pmh:                    UNKNOWN
    pml:                    UNKNOWN

ORB
    orb_high:               Visible on chart [OBSERVED_FACT]
    orb_low:                Visible on chart [OBSERVED_FACT]

ORB BREAK
    orb_break_direction:    SHORT [OBSERVED_FACT — price broke below]
    orb_break_timestamp:    UNKNOWN
    orb_break_status:       DISPLACEMENT_CONFIRMED [DERIVED_INTERPRETATION —
                            strong downward move visible on chart]
    orb_displacement_evidence:
                            Large candles breaking well below ORB Low
                            [OBSERVED_FACT]

ACTIVE MARKET STORY
    story_direction:        SHORT [DERIVED_INTERPRETATION]
    story_status:           ENTRY_ELIGIBLE [DERIVED_INTERPRETATION]

SETUP-DEFINING LEVEL
    setup_level_source:     OCL (5m order block) [MAX_LABEL]
    setup_level_price:      UNKNOWN — approximate from chart
    setup_level_price_far:  UNKNOWN
    setup_level_family:     GENERATED
    setup_break_timestamp:  N/A — OCL is the pause level, not broken
    setup_displacement_evidence: N/A for OCL itself

CONTEXTUALLY CONNECTED LEVELS
    structural_levels_connected:
                            REQUIRES_MAX_REVIEW — no structural levels
                            explicitly identified for this session
    generated_levels_connected:
                            OCL (5m order block) [MAX_LABEL]
    ocl_reference:          Order block visible on 5m, formed during
                            downward move [OBSERVED_FACT]

RETEST AND REJECTION
    retest_start_timestamp: UNKNOWN
    retest_development:     Price returned to the OCL zone
                            [OBSERVED_FACT]
    rejection_event:        On 1m: two candles in the zone, messy.
                            On 2m: one candle, wick inside zone, close
                            outside. [OBSERVED_FACT from screenshots]

ENTRY OPPORTUNITY
    entry_opportunity_id:   TSLA_2026-01-29_001
    tf_1m_reading:          MESSY [MAX_LABEL — "those two candles don't
                            look like my candle"]
    tf_1m_candle_timestamp: UNKNOWN
    tf_2m_reading:          VALID [MAX_LABEL — "on 2 minutes that candle
                            looks like my candle, wicks inside, closes
                            outside"]
    tf_2m_candle_timestamp: UNKNOWN
    tf_5m_reading:          UNKNOWN — not explicitly evaluated
    tf_5m_candle_timestamp: UNKNOWN

ALIGNMENT
    alignment_state:        UNKNOWN
    alignment_instruments:  UNKNOWN
    alignment_detail:       UNKNOWN

MAX DECISION
    max_decision:           ENTER [MAX_LABEL — "maybe I'd take this
                            on 2 minutes, or also on 1 minute"]
    max_decision_rationale: "On 1 minute the candles are messy. But on
                            2 minutes it becomes one candle that wicks
                            inside and closes outside — that's my
                            candle. I'd probably take it." [MAX_LABEL]

TRADE DETAILS
    entry_price:            UNKNOWN
    entry_timestamp:        UNKNOWN
    stop_mode:              REQUIRES_MAX_REVIEW
    stop_price:             REQUIRES_MAX_REVIEW
    target_2r_price:        UNKNOWN
    outcome:                UNKNOWN

STORY CONTINUATION
    story_invalidation_events:  UNKNOWN
    stopped_trade_story_active: UNKNOWN
    second_entry_opportunity:   UNKNOWN

SPECIFICATION VALIDATION
    spec_claims_supported:
        - Multi-timeframe entry: 1m messy, 2m clean = same opportunity
        - 1m, 2m, 5m readings are one entry opportunity, not three
        - Timeframe does not generate the setup, it reads the candle
        - Generated Level (OCL) as sole setup level is valid but
          Max showed some hesitation ("maybe", "probably")
    spec_claims_contradicted:
        NONE identified — but Max's hesitation ("maybe") on a pure
        OCL setup without structural confluence is consistent with
        the spec's statement that OCL alone is a weaker signal
    new_questions_raised:
        - When Max says "maybe" or "probably" — does this map to a
          lower quality grade, or is it just conversational hedging?
        - Is there a structural level near this OCL that Max didn't
          mention? Would its presence change his confidence?
        - Which timeframe determines the entry price when 1m is
          rejected but 2m is accepted?
```

---

## Contradiction Log

Purpose: record examples where Version 0.3 of the specification
fails to describe Max's actual decision process. Contradictions are
not resolved by changing the spec — they are documented here for
future review.

| # | Session | Spec Claim | Observed Behavior | Status |
|---|---|---|---|---|
| — | — | — | No contradictions recorded yet | EMPTY |

### How to use this log

When reviewing a session, if Max's actual behavior or stated
reasoning contradicts a claim in V0.3:

1. Record the session ID
2. Quote or reference the specific V0.3 claim
3. Describe the observed behavior or Max's statement
4. Set status to OPEN
5. Do NOT modify MAX_BOT_SPEC.md to resolve the contradiction

Contradictions are resolved only through explicit discussion with
Max, resulting in either:
- A spec revision (new version number, documented in history)
- A clarification that the example was an exception, not a rule
- A new open question added to the spec

---

## Data Gaps Summary

Information required from Max to complete the evidence records:

| Session | Missing Information |
|---|---|
| MNQ_2026-07-31 | Daily context, PDH, PDL, PMH, stop mode, stop price, outcome, alignment |
| MNQ_2026-07-29 | Daily context, PDH, PDL, PMH, stop mode, stop price, outcome, alignment, exact OCL/retest timestamps |
| TSLA_2026-07-09 | ORB High/Low values, PML, exact timestamps, stop mode, outcome, alignment, OCL presence |
| TSLA_VARIOUS | Exact date, all structural levels, exact timestamps, outcome |
| TSLA_2026-02-02 | Daily context, all structural levels (none needed for trade, but useful for completeness) |
| TSLA_2026-01-29 | Daily context, all structural levels, stop mode, outcome, alignment, exact timestamps, which timeframe for entry price |

**Common gaps across all sessions:**
- Multi-instrument alignment was not discussed for any MNQ or TSLA session
- Exact timestamps are approximate (from screenshot visual inspection)
- No MNQ CSV data exists in the repository — only screenshots
- TSLA CSV data covers 2026-04-24 to 2026-07-21, so sessions 5 and 6 (January/February 2026) are outside the CSV range
