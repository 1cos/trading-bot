# MaxBot — Retest, Entry, and Trade Management Specification

> **Version:** 1.2 — **Date:** 2026-08-09
> **Status:** AUTHORITATIVE EXTENSION — supplements MAXBOT_SPECIFICATION.md
>
> This document extends the project constitution
> (`docs/MAXBOT_SPECIFICATION.md`) with detailed rules for entry
> patterns, zone composition, trade grading, and dynamic trade
> management. It does NOT replace or override the constitution; it
> adds precision where the constitution says "see separate spec."
>
> **Binding rule:** Same as the constitution — no behavioral change
> without verifying consistency. If this document and the constitution
> diverge, the constitution prevails.
>
> **Change protocol:** Same as the constitution — only updated with
> explicit instruction from Max.
>
> **Evidence base:** Live trading session 2026-08-06 (SPY, QQQ, NVDA,
> TSLA, AMZN), voice-transcribed explanations from Max, and prior
> specification documents already in the repository.

---

## §1 — Scope

This document governs:

1. How MaxBot classifies entry patterns at a level.
2. How MaxBot composes overlapping levels into zones.
3. How MaxBot grades setup quality.
4. How MaxBot manages a live trade dynamically.
5. How MaxBot recognizes choppy/no-trade sessions.
6. How MaxBot uses intermarket alignment.

It does NOT govern:

- The universal trading sequence (see constitution §2).
- Level Provider contracts or provider-specific formation rules
  (see constitution §4, `LEVEL_PROVIDER_SPEC.md`,
  `ONE_CANDLE_LEVEL_SPEC.md`).
- The frozen BDRR pipeline stages 2–5 (break, displacement, retest
  window, rejection geometry).

---

## §2 — Glossary and Canonical Names

Every concept below has one canonical name. Code, tests, logs, and
UI must use these names. No aliases.

| Canonical Name | Type | Description |
|---|---|---|
| `SINGLE_CANDLE_REJECTION` | Entry Pattern | One candle that wicks into the zone and closes on the correct side. |
| `TWO_CANDLE_ENGULFING_RECOVERY` | Entry Pattern | Two consecutive 1m candles that, combined, form a rejection. |
| `RETEST_STRUCTURE` | Entry Pattern | Multi-candle microstructure that holds the level and eventually confirms. |
| `VALIDATED_PIVOT_ZONE` | Zone Type | Three or more Pivot/OB Wick contacts clustered at the same price area. |
| `COMPOSITE_CONFLUENCE_ZONE` | Zone Type | Multiple overlapping or near-overlapping levels merged into one operational area. |
| `REJECTION_WALL` | Structural Observation | Area where price has attempted to advance multiple times and been rejected. |
| `ACTIVE_RETEST_ZONE` | Zone State | The most recent and relevant zone for the next expected retest. |
| `CHOP_NO_TRADE` | Session Classification | Session with no valid directional structure; no trades permitted. |
| `EARLY_EXIT_REJECTION_WALL_FAILURE` | Trade Management Action | Exit before target/stop when a broken Rejection Wall fails to hold. |

### Level Source Labels (from existing `LevelSource` enum)

Already implemented: `ORB_HIGH`, `ORB_LOW`, `PREVIOUS_DAY_HIGH`,
`PREVIOUS_DAY_LOW`.

Already declared in enum but not implemented: `PMH`, `PML`, `OB`,
`SR`.

Declared in `KNOWN_FUTURE_SOURCES` in `level_provider.py`:
`PIVOT_WICK`, `OCL`.

New labels introduced by this spec (not yet in code):

| Label | Status |
|---|---|
| `TDH` (Today's Day High) | **NOT_IMPLEMENTED** — intraday, updates during session |
| `TDL` (Today's Day Low) | **NOT_IMPLEMENTED** — intraday, updates during session |
| `REJECTION_WALL` | **NOT_IMPLEMENTED** — structural observation, not a level provider |

> **Decision status: OPEN** — Whether TDH/TDL should be formal
> Level Providers or derived display values only. They update
> dynamically during the session, which differs from all current
> providers.

---

## §3 — State Machine: Break → Displacement → Retest → Entry

This section restates the universal sequence from the constitution
(§2) with additional detail about what happens at the retest stage.

```
STRUCTURAL LEVEL exists
    ↓
BREAK — close beyond level price
    ↓
DISPLACEMENT — genuine visual separation (§3.1)
    ↓
RETEST — price returns to level from breakout side
    ↓
ENTRY EVALUATION — one of:
    ├── SINGLE_CANDLE_REJECTION → ENTRY
    ├── TWO_CANDLE_ENGULFING_RECOVERY → ENTRY
    ├── RETEST_STRUCTURE → ENTRY (lower grade)
    ├── Retest fails (close materially through level) → INVALIDATED
    └── No qualifying candle → MISSED TRADE (no entry)
```

**Status:** The upstream stages (break, displacement, retest window)
are **ALREADY_IMPLEMENTED** in `break_finder.py`,
`displacement_finder.py`, `retest_window.py`. The entry evaluation
logic described below is **NOT_IMPLEMENTED** — the current
`rejection_finder.py` implements only `SINGLE_CANDLE_REJECTION`
with fixed thresholds (wick ratio ≥ 0.47, body ratio ≤ 0.40,
favorable close location ≥ 0.80).

### §3.1 — Displacement Clarification

**Status: FROZEN** (restates constitution §6)

Displacement requires genuine visual separation:

- At least a couple of candles opening and closing completely on
  the breakout side of the level.
- Visible space between the current price and the broken level.
- Continuity of direction.
- Absence of immediate deep re-entry into the structure.

A single large candle poking through and returning is NOT
displacement. This distinction matters because, on a higher
timeframe, such a candle would appear as a rejection wick, not a
breakout.

> **Decision status: OPEN** — The exact mechanical parameters
> (`min_displacement_bars`, minimum separation distance) are
> subject to ongoing calibration. Current implementation uses
> `min_displacement_bars` as the primary gate.

### §3.2 — Retest Failure and Re-entry

**Status: FROZEN** (derived from 2026-08-06 TSLA observation)

After a retest fails — meaning the price closes materially back
through the broken level — any subsequent recovery requires a
completely new sequence:

1. New break of the level.
2. New displacement.
3. New retest.
4. New Entry Candle.

The recovery MUST NOT be treated as continuation of the original
setup.

### §3.3 — Daily Trade Limits

**Status: ALREADY_IMPLEMENTED** (in `strategy_runner.py`)

1. Maximum 2 trades per session.
2. After a win, the session is over — no further entries.
3. After a loss, a new entry is permitted only if a valid thesis
   remains and a completely new setup forms.
4. Do not automatically duplicate exposure on correlated
   instruments. Each instrument must produce its own valid setup,
   even if another instrument is used as confirmation (see §13).

---

## §4 — Entry Pattern: SINGLE_CANDLE_REJECTION

**Status: ALREADY_IMPLEMENTED** (partially, in `rejection_finder.py`)

### §4.1 — Definition

A single candle that:

1. Wicks into the level or zone — meaningful penetration, not a
   mere touch.
2. Closes on the correct side of the level (above for LONG, below
   for SHORT).
3. Closes close to the level — not far away.
4. Is not anomalous (see §9 ATR filter).

Entry: at the close of the candle.

Stop: below the low of the entry candle (LONG) or above the high
(SHORT), per constitution §9.

### §4.2 — Wick Must Reach the Required Level

**Status: FROZEN** (derived from 2026-08-06 SPY observation)

When a setup requires the retest of a specific primary level (e.g.,
`ORB_HIGH`), a wick that touches only the upper portion of a
surrounding `COMPOSITE_CONFLUENCE_ZONE` but does not reach the
primary level is NOT a valid entry candle.

The zone identifies the area of interest. The primary level
determines whether the required retest has occurred.

### §4.3 — Current Implementation Notes

The existing `rejection_finder.py` evaluates geometry with three
fixed thresholds:

| Parameter | Current Value | Status |
|---|---|---|
| `rejection_wick_ratio` | ≥ 0.47 | **ALREADY_IMPLEMENTED** |
| `body_ratio` | ≤ 0.40 | **ALREADY_IMPLEMENTED** |
| `favorable_close_location` | ≥ 0.80 | **ALREADY_IMPLEMENTED** |

These thresholds were ported from the frozen JavaScript reference.
The constitution (§8) lists additional geometry gates
(`confirmation_wick_penetration_pct_min`, body outside zone) that
may or may not be fully wired. A detailed audit of the rejection
finder against the constitution §8 is deferred to the
implementation phase.

---

## §5 — Entry Pattern: TWO_CANDLE_ENGULFING_RECOVERY

**Status: NOT_IMPLEMENTED**

### §5.1 — Concept

Two consecutive 1m candles that, aggregated, form a strong
rejection candle on the 2m timeframe. This is why a 1m close inside
the zone does not automatically invalidate the retest: the
immediately following engulfing candle can transform the pair into
a powerful 2m rejection.

### §5.2 — LONG Sequence

**Status: FROZEN**

First candle (the penetration candle):

1. Penetrates the zone.
2. May close inside the zone — even deeply.
3. The body MUST NOT traverse the entire zone and close below the
   far edge. If the body closes completely through the zone on the
   opposite side, the setup is invalidated.
4. The wick MAY traverse the entire zone (even beyond the far
   edge). As long as the body closes inside the zone, the setup
   remains recoverable.

Second candle (the recovery candle), strictly the next candle:

1. Must be bullish.
2. Must completely engulf the body of the first candle. Engulfing
   the wick is NOT required.
3. Must close above the level/zone on the correct (LONG) side.
4. Must not close too far from the level (see §10 — open
   parameter).

Entry: at the close of the second candle.

Stop: below the low of the entire pair (i.e., below the deepest
wick of either candle).

### §5.3 — SHORT Sequence

**Status: FROZEN**

Exact mirror of §5.2:

- First candle penetrates upward into the zone; body must not
  close completely above the far edge.
- Second candle is bearish, engulfs the body of the first, closes
  below the zone.
- Stop above the high of the entire pair.

### §5.4 — Strict Consecutiveness

**Status: FROZEN**

The two candles must be strictly consecutive on the same timeframe.
No intermediate candle is permitted. If a candle appears between
them, the pattern is not `TWO_CANDLE_ENGULFING_RECOVERY`. The
sequence may still qualify as `RETEST_STRUCTURE` (§6) but must be
evaluated under those separate rules.

### §5.5 — Body Traversal vs Wick Traversal

**Status: FROZEN**

| Scenario | Valid? |
|---|---|
| Wick crosses entire zone, body closes inside zone | Yes |
| Body crosses entire zone and closes beyond far edge | No — invalidated |
| Body closes inside zone, next candle engulfs and recovers | Yes |
| Body closes beyond far edge, next candle engulfs | No — not recoverable |

### §5.6 — Synthetic 2m Interpretation

**Status: FROZEN**

The pair must be representable as a single candle on the 2m
timeframe. This synthetic candle should show:

- Open of the first candle.
- Close of the second candle.
- High = max(high₁, high₂).
- Low = min(low₁, low₂).

If the synthetic 2m candle meets `SINGLE_CANDLE_REJECTION` criteria,
the pair is valid. This is conceptual validation, not a requirement
to run the 2m aggregation at runtime — though the system may use it
for deduplication.

### §5.7 — No Double Signal

**Status: FROZEN**

A `TWO_CANDLE_ENGULFING_RECOVERY` on 1m and the corresponding
synthetic candle on 2m represent ONE entry opportunity, not two.
The system must not generate duplicate signals for the same price
action on different timeframes. (Restates constitution §8
multi-timeframe rule.)

---

## §6 — Entry Pattern: RETEST_STRUCTURE

**Status: NOT_IMPLEMENTED**

### §6.1 — Definition

When the retest does not produce a clean single-candle rejection or
a two-candle engulfing, the price may still hold the level through
a broader microstructure over multiple candles.

Possible characteristics:

- Penetration of the zone.
- The level holds — no material close through the far edge.
- Formation of a micro base or consolidation at the level.
- Eventually, price breaks above (LONG) or below (SHORT) the
  intermediate highs/lows of the microstructure.
- The breakout of the microstructure serves as the confirmation.

### §6.2 — Stop Placement

Stop below (LONG) or above (SHORT) the entire microstructure.

The wider stop means reduced position size. If stop and target
become excessively wide, the entry may be discarded entirely.

### §6.3 — Quality Implications

A `RETEST_STRUCTURE` entry is inherently lower quality than a clean
`SINGLE_CANDLE_REJECTION` or `TWO_CANDLE_ENGULFING_RECOVERY`. It
should be graded B or B+ at most, never A or A+.

### §6.4 — Open Parameters

| Parameter | Status |
|---|---|
| Maximum number of candles in the structure | **OPEN** |
| How to detect the confirmation breakout | **OPEN** |
| Maximum stop width relative to ATR | **OPEN** |

---

## §7 — Zone Composition

### §7.1 — VALIDATED_PIVOT_ZONE

**Status: NOT_IMPLEMENTED**

When three or more Pivot/OB Wick contacts cluster in the same price
area, they form a `VALIDATED_PIVOT_ZONE`.

Rules:

1. The contacts must be genuinely close in price. The tolerance for
   "close" is a configurable parameter.
2. The contacts can be tests as resistance, support, or a mix —
   they need not all be the same role.
3. Multiple tests strengthen the zone. More tests = stronger level.
4. After a break with displacement, the zone changes role
   (resistance → support or vice versa).
5. The first retest after the role change is the highest-quality
   opportunity.
6. The zone must not be artificially widened to include distant
   contacts.

**Example (SPY 2026-08-06):** Three Pivot/OB Wick contacts near
ORB High (~770.79–770.86). Each tested as resistance. After break
with displacement, the zone became support candidate for LONG
retest.

| Parameter | Status |
|---|---|
| Clustering tolerance (price distance) | **OPEN** |
| Minimum number of contacts | **FROZEN: 3** |
| Maximum zone width | **OPEN** |

### §7.2 — COMPOSITE_CONFLUENCE_ZONE

**Status: NOT_IMPLEMENTED**

When multiple levels from different providers are very close or
overlapping, they must be merged into a single
`COMPOSITE_CONFLUENCE_ZONE` rather than treated as separate setups.

Rules:

1. Each component level retains its identity and source label.
2. The zone has a primary level — the one the setup must actually
   retest for the entry to be valid.
3. The zone has operational bounds — the outer edges of the
   combined area.
4. A wick that touches only the edge of the zone but does not
   reach the primary level does NOT constitute a valid retest.
5. Component levels are traceable for audit and review.

**Example (NVDA 2026-08-06):** ORB High (~222.22) + PMH (~222.38) +
previous Pivot/OB (blue zone). Three providers, one composite area.

| Parameter | Status |
|---|---|
| Merge tolerance (max distance between components) | **OPEN** |
| How to identify the primary level automatically | **OPEN** |

### §7.3 — ACTIVE_RETEST_ZONE

**Status: NOT_IMPLEMENTED**

After a break with displacement, if a new Pivot/OB Wick forms that
is more recent, closer to the current price, and overlaps with a
structural level, it becomes the `ACTIVE_RETEST_ZONE`.

Rules:

1. The previous zone is not deleted. It remains in memory as a
   secondary level.
2. All memorized levels stay active until end of session.
3. A level can return to primary status if the price revisits it.
4. Levels do not carry over between sessions (see §8.1).

**Example (SPY 2026-08-06):** The blue zone was initially on a
previous pivot. After the break, it was repositioned to include
ORB High + the new Pivot/OB Wick formed during the advance.

---

## §8 — Level Lifecycle

### §8.1 — Intraday Scope

**Status: FROZEN** (from Max's explicit instruction)

All MaxBot levels are intraday and expire at end of session.

During the session:

- All levels remain in memory.
- Crossing a level does not delete it.
- A level may change role: support ↔ resistance.
- A level retains its history of tests and breaks.
- A break of the opposite ORB side does not cancel other levels.
- A level remains potentially tradeable until session close.

At end of session:

- All intraday levels (OCL, OB, Pivot Wick) are eliminated.
- ORB is eliminated.
- PDH/PDL and PMH/PML used that day are eliminated.
- Next day: MaxBot rebuilds from scratch.
- New PDH/PDL from the just-concluded session.
- New PMH/PML from the new pre-market.
- New ORB from the new first five minutes.

### §8.2 — Role Change Strengthening

**Status: FROZEN**

When a level that has been tested multiple times as resistance is
finally broken, it becomes a stronger support (and vice versa).
More tests before the break = stronger level after the break.

This may justify:

- Greater confidence in the zone.
- Acceptance of a slightly less perfect Entry Candle (per
  constitution §13.3 — confluence spectrum).
- But never automatic entry without an Entry Candle.

---

## §9 — Candle Anomaly Filter (News Candle)

**Status: NOT_IMPLEMENTED**

### §9.1 — Purpose

An excessively large candle may be caused by a news event rather
than a genuine structural reaction. Such candles produce unreliable
entries with oversized stops.

### §9.2 — Calculation

```
candle_range = High - Low
previous_ATR_14 = ATR(14) of the 14 completed candles BEFORE
                  the candle being evaluated
candle_atr_ratio = candle_range / previous_ATR_14
```

The candle being evaluated MUST NOT be included in its own ATR
calculation. Including it would inflate the ATR and artificially
lower the ratio.

### §9.3 — Classification

| Candle ATR Ratio | Classification | Status |
|---|---|---|
| ≤ 2.0 | Normal | FROZEN |
| 2.0 – 3.0 | Large but evaluable | FROZEN |
| > 3.0 | Anomalous / news candle — setup excluded | FROZEN (initial) |

### §9.4 — Configuration

```
max_entry_candle_atr = 3.0  # initial value, configurable
```

Must be backtested at: 2.0, 2.5, 3.0.

### §9.5 — Application to TWO_CANDLE_ENGULFING_RECOVERY

**Status: FROZEN**

BOTH candles in the pair must independently respect the ATR limit.
If either candle exceeds the threshold, the pattern is excluded.

A second candle that is too large may confirm the direction but
does not constitute a good entry — it would place the entry too
far from the level with an excessively wide stop.

### §9.6 — Future Enhancement

**Status: PROPOSED**

A calendar-based blackout around major macroeconomic news releases
could prevent exposure before the event. This is separate from the
ATR filter: the calendar avoids risk proactively; the ATR
recognizes anomalous candles retroactively.

---

## §10 — Maximum Entry Close Distance

**Status: OPEN — NOT a frozen rule**

An Entry Candle that closes too far from the level creates an
oversized stop and suggests the price may return for a tighter
rejection.

Initial hypothesis discussed: `max_entry_close_distance_atr = 0.25`

This value has NOT been approved. It must be:

- Configurable.
- Evaluated per timeframe.
- Backtested across multiple instruments.
- Verified visually on real examples.
- Possibly adaptive via ATR rather than based on absolute zone
  width.

Do not treat 0.25 ATR as a frozen parameter.

---

## §11 — REJECTION_WALL

**Status: NOT_IMPLEMENTED**

### §11.1 — Definition

A Rejection Wall is an area where price has attempted to advance
multiple times and been rejected, leaving clustered highs/lows and
rejection wicks in close proximity.

### §11.2 — Effect on Entry Quality (Grading Penalty)

**Status: FROZEN**

A technically valid setup cannot be graded A or A+ when one or more
Rejection Walls exist between the entry price and the first target.

Reasons:

- Active sellers/buyers immediately ahead.
- Insufficient clean space.
- Risk of compression between the retested level and the wall.
- Additional resistance/support immediately after.
- Effective risk/reward is worse than the nominal calculation.

The setup remains valid but is downgraded to B or B+.

### §11.3 — Effect on Trade Management

**Status: FROZEN** (from 2026-08-06 SPY trade)

After the price breaks through a Rejection Wall:

1. The wall changes role — it becomes a new management level.
2. A simple wick back through the wall does NOT force exit.
3. A close back through the wall on the wrong side signals failure.
4. If this happens near a major resistance/support (like PMH/TDH),
   exit the trade without waiting for the structural stop or
   target.

Rule name: `EARLY_EXIT_REJECTION_WALL_FAILURE`

LONG example:

- Price breaks above a Rejection Wall.
- Price reaches PMH/TDH resistance.
- A candle closes back below the wall level.
- Exit at that close.

SHORT: mirror logic.

---

## §12 — Quality Grading

**Status: PROPOSED** (framework; specific weights are open)

### §12.1 — Grading Categories

| Grade | Characteristics |
|---|---|
| A+ | Important structural level; confluence with Pivot/OB/OCL; extremely clean Entry Candle; clear prior displacement; clean space ahead; favorable intermarket alignment; no immediate obstacles. |
| A | Clean setup; valid level; correct Entry Candle; good displacement; sufficient space; obstacles not immediate. |
| B+ | Valid setup; slightly messy reaction; Rejection Wall nearby but not blocking; space somewhat reduced. |
| B | Valid setup; reaction distributed over multiple candles; confirmation delayed; Rejection Wall close; major resistance/support too near; reduced clean space. |

### §12.2 — Grading Penalties

| Condition | Penalty |
|---|---|
| Rejection Wall between entry and target | A/A+ → B/B+ |
| Entry via RETEST_STRUCTURE (multi-candle) | Cap at B+ |
| Intermarket misalignment | **OPEN** |
| Entry candle close distance > threshold | **OPEN** |

### §12.3 — Open Decisions

| Question | Status |
|---|---|
| Is intermarket alignment required for A+? | **OPEN** |
| Exact weight of alignment in grading? | **OPEN** |
| Mechanical criteria for each grade boundary? | **OPEN** |
| How to compute "clean space ahead"? | **OPEN** |

---

## §13 — Intermarket Alignment

**Status: NOT_IMPLEMENTED** (rules frozen from constitution §14.3
and 2026-08-06 session)

### §13.1 — Alignment Pairs

- QQQ/SPY: mutual confirmation.
- Individual tech names (TSLA, NVDA, etc.): confirmed by SPY/QQQ.
- MES/MNQ: both should agree.

### §13.2 — Alignment Rules

**Status: FROZEN**

1. Alignment does not need to occur in the same minute. One
   instrument can lead; the other confirms later. (SPY 2026-08-06:
   QQQ led bullish, SPY aligned later.)
2. Alignment increases quality but does not create an entry by
   itself.
3. Alignment does not replace the Entry Candle.
4. Alignment does not automatically authorize a second trade on
   the confirming instrument.
5. Each instrument must produce its own valid setup independently.

### §13.3 — Duplicate Exposure Rule

**Status: FROZEN**

Do not automatically duplicate exposure across correlated
instruments. If entering NVDA based on its own setup with SPY as
confirmation, entering SPY additionally requires SPY's own
independent Entry Candle at its own level. Considering the 2-trade
daily limit, this prevents wasting both trades on effectively the
same directional bet.

---

## §14 — CHOP_NO_TRADE

**Status: NOT_IMPLEMENTED**

### §14.1 — Recognition

A session is `CHOP_NO_TRADE` when:

- False breaks occur on both sides of the ORB.
- Price re-enters the ORB continuously.
- No displacement is established.
- Candles are heavily overlapping.
- Direction changes repeatedly.
- No level changes role credibly.

### §14.2 — Response

No trades. The market can become interesting only after:

1. A clean break.
2. Real displacement.
3. A retest.
4. A valid Entry Candle.

### §14.3 — Mechanical Threshold

**Status: OPEN** — No formula has been approved. Do not implement
heuristics without Max's approval. The current recognition is
visual/discretionary.

**Example (AMZN 2026-08-06):** False break above ORB High, immediate
re-entry. No displacement. Continuous crossing of both ORB sides.
Attempt below ORB Low also recovered. No trade.

---

## §15 — Dynamic Trade Management

### §15.1 — Existing Rules (from constitution)

| Rule | Status |
|---|---|
| Stop from Entry Candle (§9) | **ALREADY_IMPLEMENTED** |
| Target at R multiple — current 2.1R (§10) | **ALREADY_IMPLEMENTED** |
| Max 2 trades/session (§3.3) | **ALREADY_IMPLEMENTED** |
| Win → session done (§3.3) | **ALREADY_IMPLEMENTED** |
| Loss → re-entry only with valid new setup (§3.3) | **ALREADY_IMPLEMENTED** |

### §15.2 — New Rule: Early Exit on Rejection Wall Failure

**Status: FROZEN / NOT_IMPLEMENTED**

See §11.3 for full specification. This is a trade management rule,
not an entry rule. It must be modeled separately from entry logic.

### §15.3 — Trade Not Taken

**Status: FROZEN**

If the price moves away from the level without producing an Entry
Candle or without reaching the required level:

- The trade is missed.
- Do not chase.
- Do not retroactively widen the zone.
- Do not change the level to justify a late entry.

This is a correct outcome, not a system error.

---

## §16 — Canonical Examples: 2026-08-06 Session

These examples document the logic observed during the live session.
OHLC values are approximate (from screenshots); precise values must
be confirmed from 1m CSV data when available.

### §16.1 — NVDA: TWO_CANDLE_ENGULFING_RECOVERY at Triple Confluence

**Grade:** A+ (structural quality; ATR filter verification pending)

Approximate levels:

- ORB High: ~222.22
- PMH: ~222.38
- Previous Pivot/OB: visible as blue zone on chart

Sequence (~08:44–08:45 CT):

1. Break above ORB High with displacement.
2. Price returns to the confluence zone.
3. Red candle penetrates and closes inside the zone, below ORB
   High.
4. Immediately next candle: green, engulfs the body of the red
   candle.
5. Closes above ORB High and PMH — recovers the entire
   confluence.
6. Entry at the close of the green candle.
7. Stop below the low of the red candle (low of the pair).

The pair on 2m would form a single strong rejection candle with a
deep lower wick.

Triple confluence: ORB High + PMH + previous Pivot/OB.

### §16.2 — SPY: Validated Pivot Zone + ORB High Retest

**Grade:** B/B+

Approximate levels:

- ORB High: ~770.79
- Pivot: ~770.86
- Validated Pivot Zone: three Pivot/OB contacts near 770.79–770.86

Sequence:

1. QQQ led the bullish move.
2. SPY broke ORB High + Pivot confluence.
3. Displacement above the zone.
4. Three previous tests of the zone as resistance validated it.
5. Price returned to the zone for retest.
6. First green candle wicked into the zone but did NOT reach ORB
   High — not a valid Entry Candle.
7. Later confirmation was messier, distributed over multiple
   candles.
8. Entry ~771.13, stop ~770.61, target ~772.21, RR ~2.23.

Reasons for B/B+ instead of A:

- Messy, multi-candle reaction.
- Two Rejection Walls immediately above entry.
- TDH/PMH resistance very close (~1R away).
- Reduced clean space.

### §16.3 — SPY: Early Exit at Rejection Wall Failure

During the SPY trade:

1. Price broke above both Rejection Walls.
2. Reached PMH/TDH resistance area.
3. At 09:23 CT, a candle closed back below the Rejection Wall
   level (~771.43).
4. Trader exited at that close.
5. Subsequently the market sold off toward ORB High and below.

The exit was correct at the moment of the close, independent of
subsequent price action. The Rejection Wall, once broken, served
as the dynamic management level.

### §16.4 — TSLA: Failed ORB Retest

Sequence:

1. Reaction from PML.
2. Break above ORB High (~318.50).
3. Displacement toward TDH (~319.45).
4. Return: large red candle closed materially inside the ORB.
5. Next candle went deeper — no valid Entry Candle.
6. Subsequent recovery must be treated as a new sequence, not
   continuation.

### §16.5 — NVDA: Potential ORB Low Short Retest

Sequence:

1. Break below ORB Low (~220.03).
2. Displacement downward.
3. Price near TDL (~219.33).
4. Do NOT enter short chasing near TDL — no clean space.
5. Wait for bounce back toward ORB Low, wick into/above it, close
   back below.
6. If price recovers ORB Low with stable closes above, the short
   thesis is invalidated.

### §16.6 — AMZN: CHOP_NO_TRADE

- False break above ORB High.
- Immediate re-entry.
- False break below ORB Low, also recovered.
- Continuous crossing of both ORB sides.
- No displacement in either direction.
- No trade.

### §16.7 — QQQ: Leader Bullish + Multi-Timeframe Entry

Market Story:

1. Initial drop to OBL area.
2. Strong reaction, recovery of PML.
3. Decisive break of ORB High (~711.67).
4. Real displacement: multiple candles working above ORB.
5. Micro-retracement forms OCL/OB zone (~713.03–713.45).
6. Continuation toward TDH (~714.34).

Later entry on QQQ:

- On 1m the entry was not a clean engulfing.
- On 2m the same price action formed a clear Entry Candle with
  wick into the zone and close above.
- This is the multi-timeframe reading: one opportunity, not two.

---

## §17 — Cases That Invalidate an Entry

| Scenario | Result |
|---|---|
| Body of first candle traverses entire zone and closes beyond far edge | TWO_CANDLE pattern invalidated |
| Intermediate candle between penetration and engulfing | Not TWO_CANDLE; evaluate as RETEST_STRUCTURE |
| Either candle in pair exceeds 3 ATR | Pattern excluded |
| Wick touches zone edge but not primary level | Retest not achieved; entry invalid |
| Price never leaves the zone (no displacement) | No valid setup |
| False breaks on both ORB sides with no displacement | CHOP_NO_TRADE |
| Retest fails: close materially through level | Setup invalidated; new sequence required |
| Recovery after failed retest | Must produce entirely new break → displacement → retest → entry |
| Excessive close distance from level | Entry quality downgraded or excluded (threshold OPEN) |

---

## §18 — Open Parameters Summary

These must NOT be given invented values. Each must remain
configurable and marked as requiring validation.

| Parameter | Discussed Value | Status |
|---|---|---|
| `max_entry_candle_atr` | 3.0 (backtest: 2.0, 2.5, 3.0) | **PROPOSED** — initial |
| `max_entry_close_distance_atr` | 0.25 | **OPEN** — not approved |
| Pivot clustering tolerance | Not discussed | **OPEN** |
| Maximum Composite Zone width | Not discussed | **OPEN** |
| Minimum wick size for Pivot/OB | Not discussed | **OPEN** |
| RETEST_STRUCTURE max candle count | Not discussed | **OPEN** |
| CHOP mechanical threshold | Not discussed | **OPEN** |
| Intermarket alignment weight in grading | Not discussed | **OPEN** |
| Alignment required for A+? | Not decided | **OPEN** |
| Maximum stop width (absolute/ATR) | Not discussed | **OPEN** |
| "Close materially inside ORB" threshold | Not discussed | **OPEN** |
| Rejection Wall detection criteria | Not discussed | **OPEN** |
| Mechanical distinction: Pivot Wick vs OCL vs OB | Not discussed | **OPEN** |
| TDH/TDL: formal provider or display only? | Not discussed | **OPEN** |

---

## §19 — Acceptance Criteria

These criteria define what "done" means for each concept when
implemented.

### Entry Patterns

1. `SINGLE_CANDLE_REJECTION` detects LONG and SHORT with correct
   geometry, respects ATR filter, validates wick reaches primary
   level.
2. `TWO_CANDLE_ENGULFING_RECOVERY` passes all 15 minimum tests
   listed in §20.
3. `RETEST_STRUCTURE` identified and graded separately from the
   other two patterns.
4. No duplicate signal for the same price action across timeframes.

### Zones

5. Three nearby pivots create `VALIDATED_PIVOT_ZONE`.
6. Distant pivots do NOT cluster.
7. Overlapping levels from different providers merge into
   `COMPOSITE_CONFLUENCE_ZONE`.
8. Component levels remain traceable.
9. Primary level remains identifiable.
10. Wick into zone without reaching primary level = invalid retest.
11. Previous zone stays memorized when Active Retest Zone changes.

### Rejection Wall

12. Wall near entry penalizes grade from A to B/B+.
13. Distant wall does not penalize.
14. Break of wall followed by failure close produces early-exit
    reason.
15. Simple wick return through wall does NOT produce automatic
    exit.

### Anomaly Filter

16. ATR(14) computed without current candle.
17. Candle > 3 ATR excluded.
18. Both candles in TWO_CANDLE pair checked independently.

### Session Classification

19. CHOP_NO_TRADE recognized when defined criteria are met.

### Trade Management

20. Early exit on Rejection Wall failure generates
    `EARLY_EXIT_REJECTION_WALL_FAILURE` reason in trade record.

---

## §20 — Minimum Required Tests for TWO_CANDLE_ENGULFING_RECOVERY

1. LONG valid: first candle closes inside zone, immediate bullish
   engulfing.
2. SHORT valid: mirror of test 1.
3. Engulfing of body only (not wick) is sufficient.
4. Wick of first candle not engulfed: still valid.
5. Intermediate candle present: invalid for this pattern.
6. Wick traverses entire zone, body inside: valid.
7. Body traverses entire zone and closes beyond: invalid.
8. First candle > 3 ATR: invalid.
9. Second candle > 3 ATR: invalid.
10. ATR computed excluding current candle.
11. Second candle closes on wrong side: invalid.
12. Second candle does not engulf body of first: invalid.
13. Stop LONG placed below minimum of the pair.
14. Stop SHORT placed above maximum of the pair.
15. No duplicate signal on 1m and 2m for same structure.

---

## §21 — Gap Analysis: Specification vs Repository

> **Last updated:** v1.1 (2026-08-06), after B1–B5 + ATR warmup.

| # | Requirement | Status | Current File/Module | Current Test | Commit | Future Change Needed |
|---|---|---|---|---|---|---|
| 1 | `SINGLE_CANDLE_REJECTION` detection | **DONE** (partial — ATR gate added B4; primary-level validation satisfied by construction) | `rejection_finder.py` | `test_rejection_finder.py` | pre-B1 + `9d0db99` | `level_price == primary_level_price` by construction; composite zone tolerance implemented in B8 |
| 2 | `TWO_CANDLE_ENGULFING_RECOVERY` detection | **DONE** | `rejection_finder.py` | `test_rejection_finder.py` | `4568e6b` | — |
| 3 | `RETEST_STRUCTURE` detection | **BLOCKED** — criteria OPEN | — | — | — | Requires approved formation criteria (§18) |
| 4 | ATR(14) calculation | **DONE** | `atr.py` | `test_atr.py` | `0649871` | — |
| 5 | News candle filter (> 3 ATR) | **DONE** | `news_candle.py` + `rejection_finder.py` | `test_news_candle.py`, `test_rejection_finder.py` | `23c57ee` + `9d0db99` | — |
| 5b | ATR warmup from previous session | **DONE** | `rejection_finder.py`, `strategy_runner.py` | `test_atr_warmup.py` | `5d78005` | — |
| 5c | Equity RTH bar filter | **DONE** | `session_split.py` | `test_rth_bar_filter.py` | `af9bd77` | — |
| 6 | `VALIDATED_PIVOT_ZONE` clustering | **DONE** | `pivot_cluster.py` | `test_pivot_cluster.py` | `6d8f1bb` | Bounded iterative algorithm, no transitive chaining. |
| 7 | `COMPOSITE_CONFLUENCE_ZONE` merging | **DONE** | `confluence_zone_builder.py` | `test_confluence_zone_builder.py` | `3f84588` | Anchor-based merge around explicit primary. |
| 8 | Primary-level retest validation in zones | **SATISFIED** | `rejection_finder.py` | `test_rejection_finder.py` | — | `level_price == primary_level_price` by construction. End-to-end blocked by multi-provider. |
| 8b | Generic level sequence invalidation | **DONE** | `sequence_validator.py` | `test_sequence_validator.py` | `82b81aa` | PDH/PDL: consecutive closes on wrong side of level_price. |
| 9 | `REJECTION_WALL` detection | NOT_IMPLEMENTED | — | — | — | Mechanical criteria OPEN (§18). |
| 10 | Quality grading with penalties | NOT_IMPLEMENTED | `contracts/enums.py` has `QualityGrade` | `test_contract_enums.py` | — | New grading module. Depends on B9. |
| 11 | `EARLY_EXIT_REJECTION_WALL_FAILURE` | NOT_IMPLEMENTED | — | — | — | Trade management extension. Depends on B9. |
| 12 | `CHOP_NO_TRADE` classification | **BLOCKED** — thresholds OPEN | — | — | — | Requires approved mechanical thresholds (§18). |
| 13 | Intermarket alignment | NOT_IMPLEMENTED | — | — | — | Multi-symbol data feed needed. |
| 14 | Max close distance from level | NOT_IMPLEMENTED | — | — | — | Parameter OPEN (`0.25 ATR` proposed, not approved). |
| 15 | `LevelSource` enum: TDH, TDL | NOT_IMPLEMENTED | — | — | — | Decision OPEN: formal provider or display only. |
| 16 | Level lifecycle: intraday expiry | **DONE** (implicit) | `strategy_runner.py` | `test_strategy_runner.py` | pre-B1 | — |
| 17 | Synthetic 2m deduplication | PARTIALLY_IMPLEMENTED | `multi_timeframe_runner.py` | `test_multi_timeframe.py` | pre-B1 | Extend to prevent TWO_CANDLE + 2m double-count. |
| 18 | Pivot/OB Wick provider | NOT_IMPLEMENTED | `level_provider.py` declares future | — | — | Constitution Phase 5. |
| 19 | OCL provider | NOT_IMPLEMENTED | `level_provider.py` declares future | — | — | Constitution Phase 6. |
| 20 | PMH/PML provider | NOT_IMPLEMENTED | `level_provider.py` declares future | — | — | Constitution Phase 4. |
| 21 | Zone contracts (ZoneComponent, CompositeZone) | **DONE** | `contracts/zone.py` | `test_contract_zone.py` | `77211da` | — |
| 22 | Entry pattern contracts (EntryPatternResult) | **DONE** | `contracts/entry_pattern.py` | `test_contract_entry_pattern.py` | `77211da` | — |
| 23 | Entry pattern enums + zone enums | **DONE** | `contracts/enums.py` | `test_contract_enums.py` | `77211da` | — |
| 24 | Review Workspace metadata extensions | NOT_IMPLEMENTED | — | — | — | Depends on B6–B11. |

---

## §22 — Implementation Order (Phase B)

> **Last updated:** v1.1 — B1–B5 completed. B6+ approved for
> execution in this order.

| Task | Description | Status | Commit | Dependencies |
|---|---|---|---|---|
| B1 | Contracts and enum extensions | **DONE** | `77211da` | — |
| B2 | ATR(14) utility module | **DONE** | `0649871` | — |
| B3 | News candle classification | **DONE** | `23c57ee` | B2 |
| B4 | ATR gate in rejection finder | **DONE** | `9d0db99` | B2, B3 |
| B5 | TWO_CANDLE_ENGULFING_RECOVERY + stop override | **DONE** | `4568e6b` | B2, B3 |
| — | ATR warmup from previous session | **DONE** | `5d78005` | B2 |
| — | Equity RTH bar filter | **DONE** | `af9bd77` | — |
| B6 | Pivot clustering → VALIDATED_PIVOT_ZONE | **DONE** | `6d8f1bb` | B1 |
| — | PDH/PDL direction pair correction | **DONE** | `8c73d14` | — |
| B7 | COMPOSITE_CONFLUENCE_ZONE builder | **DONE** | `3f84588` | B6 |
| — | Generic level sequence invalidation (PDH/PDL) | **DONE** | `82b81aa` | — |
| B8 | ATR tolerance for operational composite confluence | **DONE** | `c911858` + `addc006` | B7. `build_operational_confluence`: overlap gate + distance gate (0.75 × ATR post-ORB). Builder is standalone; runner multi-provider integration not yet implemented. |
| B9 | REJECTION_WALL detection | PLANNED | — | Criteria OPEN (§18) |
| B10 | Grading penalties (wall, structure, alignment) | PLANNED | — | B9 |
| B11 | EARLY_EXIT_REJECTION_WALL_FAILURE | PLANNED | — | B9 |
| B12 | CHOP_NO_TRADE classifier | **BLOCKED** | — | Thresholds OPEN (§18) |
| B13 | RETEST_STRUCTURE detector | **BLOCKED** | — | Criteria OPEN (§18) |
| B14 | Review Workspace metadata extensions | PLANNED | — | B6–B11 |

---

## §23 — Relationship to Other Documents

| Document | Relationship |
|---|---|
| `docs/MAXBOT_SPECIFICATION.md` | Constitution. This spec extends it. Constitution prevails on conflicts. |
| `LEVEL_PROVIDER_SPEC.md` | Universal level output contract. This spec consumes levels; does not change the contract. |
| `ENTRY_CANDLE_ENGINE_SPEC.md` | Entry Candle interface. This spec defines additional entry patterns that supplement the existing interface. |
| `ONE_CANDLE_LEVEL_SPEC.md` | OCL formation rules. This spec may consume OCL levels but does not modify OCL rules. |
| `MAX_BOT_SPEC.md` | Session Decision Engine. This spec adds precision to entry evaluation and trade management. |
| `TRADE_CANDIDATE_SPEC.md` | Trade Candidate data object. Future TWO_CANDLE and RETEST_STRUCTURE entries will produce Trade Candidates with the same contract. |
| `docs/PHASE1_SYSTEM_AUDIT.md` | System audit. Confirmed that stages 2–5 are generic. This spec operates downstream of those stages. |
| `DOCUMENTATION_CONTRADICTION_AUDIT.md` | Known contradictions. This spec does not resolve them but avoids introducing new ones. |

---

## §24 — Document History

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-08-06 | Initial specification. Created from live trading session observations, voice-transcribed rules, and prior chat-based analysis. Covers entry patterns, zone composition, grading, trade management, and session classification. All examples from 2026-08-06 session. |
| 1.1 | 2026-08-06 | Status update after B1–B5 + ATR warmup + equity RTH filter. §21 gap analysis updated to reflect completions. §22 updated with commit hashes. §3.3 added (daily trade limits — already implemented, now formally documented). Fixed §3.5 references → §3.3. |
| 1.2 | 2026-08-09 | B6–B8 completion. B6: bounded iterative pivot clustering. PDH/PDL direction pairs corrected. B7: composite confluence zone builder with anchor-based merge. Generic line-level sequence invalidation for PDH/PDL. B8: `build_operational_confluence` with ATR-based tolerance (0.75 × ATR post-ORB, inclusive comparison, no floor/cap) and overlap gate (max displacement indices ≤ min max-valid indices). Builder is standalone — ATR post-ORB is received from caller, not verified internally. Runner multi-provider integration not yet implemented. Canonical SPY verification: 6 contemporaneous sessions, 2 COMPOSITE_CREATED, 4 EXCLUDED_DISTANCE. |

---

*END OF SPECIFICATION*
