# Max Bot — Session Decision Engine Specification

> **Version:** 0.3 — **Date:** 2026-08-03 — **Status:** AUTHORITATIVE DRAFT — NOT FROZEN
>
> This document describes how Max trades. It is not a software
> specification. It is a reference that the Max Bot must replicate.
> Nothing in this document should be coded until Max confirms it is
> correct and complete.

---

## What the Max Bot Is

The Max Bot is not a detector. It is not a single strategy. It is a
Session Brain that observes the market the way Max does, from the
moment he sits down before the open until he is done for the day.

It combines multiple Level Providers, a universal Entry Engine, and a
set of contextual rules into one stateful session observer.

The Max Bot is the "director of the orchestra." The instruments are
the Level Providers, the Entry Engine, the Risk Engine, and the
Market Context. The Max Bot tells them when to play and listens to
what they report.

---

## Session Phases

The Max Bot's day is divided into distinct phases. Each phase has
different responsibilities.

| Phase | Time (CT) | What Happens |
|---|---|---|
| Pre-Open | Before 8:30 | Calculate PDH, PDL, PMH, PML. Receive Max's daily context input. |
| ORB Formation | 8:30 — 8:34 | Observe the first 5 minutes. Mark ORB High and ORB Low. No entries. |
| Trading Window | 8:35 — 14:00 | Entry-eligible. Observe, detect, and enter setups. |
| End of Session | After 14:00 | No new entries. Session is closed. |

Pre-open preparation happens before 8:30 — Structural Levels (PDH,
PDL, PMH, PML) are calculated during this phase. The Max Bot is
active before 8:30 for preparation, but no entries are permitted
until the ORB is complete at 8:35.

---

## Pre-Open Routine (Before 8:30 CT)

Before the market opens, Max builds his mental map. The Max Bot must
do the same.

### Step 1 — Mark Previous Day Levels

From the prior regular session, record:

- **PDH** — Previous Day High
- **PDL** — Previous Day Low

### Step 2 — Assess Trend vs Range

Look at the daily chart. The question is simple: is this instrument
in a trend or in a range?

This is a **discretionary assessment**, not a mechanical rule. Max
does not count higher highs or lower lows. He looks at the daily
chart and forms an impression: is the market going somewhere, or is
it stuck?

There is no formula. A trend is visible when you see it. A range is
visible when you see it. Attempting to reduce this to a mechanical
rule (e.g. "3 consecutive lower lows = downtrend") would produce
false precision and miss the nuance Max sees.

> **For the bot:** Until a reliable mechanical proxy for this
> assessment is validated through testing, the daily context should
> be supplied as an input parameter (TREND_UP, TREND_DOWN, RANGE),
> not computed automatically. Max sets it each morning. Future
> versions may automate this if a robust rule is found.

An additional heuristic: if the current price is above the PDH,
this adds bullish contextual evidence. If below the PDL, this adds
bearish contextual evidence. This heuristic supplements but does not
automatically replace the complete daily and intraday reading.

**What this assessment produces:**

- In **trend mode**, Max starts the day with a directional bias. He
  mentally aligns with the trend. He still trades against it if the
  intraday action is strong enough (see "Intraday Overrides Daily"
  below), but his default expectation follows the trend.

- In **range mode**, Max starts the day with no directional bias. He
  prepares two scenarios — one long, one short — and waits for the
  market to choose. In range mode, he pays special attention to
  pre-market gaps, because in a range the price often fills the gap
  and then moves in the opposite direction.

### Step 3 — Mark Pre-Market Levels

From the pre-market session (starting at 03:00 CT), record:

- **PMH** — Pre-Market High
- **PML** — Pre-Market Low

### Step 4 — Wait for the Open

No trading happens before 8:30. The map is drawn. Max waits.

---

## The Open — ORB Formation (8:30 — 8:34 CT)

At 8:30, the market opens. Max watches the first five minutes
(8:30 through 8:34 inclusive) to form the Opening Range.

- **ORB High** — highest price in the first 5 minutes
- **ORB Low** — lowest price in the first 5 minutes

The ORB timeframe is 5 minutes regardless of the trading timeframe.
Max may trade on 1m, 2m, or 5m candles, but the ORB is always
defined by the first 5 minutes of the session.

No entries are permitted during ORB formation.

---

## The Absolute Rules

These are hard filters. If any of them says no, nothing else matters.

### Rule 1 — No Entries Without a Valid ORB Break

Before a valid ORB break, the Max Bot cannot create an entry-eligible
setup.

The Max Bot may still observe and record price action during this
time: structural interactions, failed break attempts, Generated
Levels forming, and other context needed to understand the later
Market Story. Observation is not blocked — only entries are blocked.

> **Open question:** If Generated Levels form before a valid ORB
> break, are they eligible for retest entries after the break
> occurs? This requires examples to resolve. Do not assume they are
> automatically valid or automatically invalid.

### Rule 2 — Direction Follows the Active Story

An ORB break starts a provisional directional story:

- Price breaks **above ORB High** → provisional **LONG** story
- Price breaks **below ORB Low** → provisional **SHORT** story

The break is observed (BREAK_OBSERVED) but the story is not yet
confirmed. Displacement — genuine visual separation from the broken
ORB level — must follow to confirm the story
(DISPLACEMENT_CONFIRMED). Only after confirmed displacement is the
session direction established and the Max Bot eligible to pursue
entry setups in that direction.

This is a directional context, not a permanent authorization. The
active story can be **invalidated** if the price materially re-enters
through the broken level (see "Story Invalidation" below).

> **Open question:** What happens when the opposite side of the ORB
> is later broken? Does this start a new story in the opposite
> direction? Does it invalidate the first story? Can both coexist?
> This requires examples from Max to resolve.

### Rule 3 — Maximum 2 Trades Per Day

The Max Bot takes at most 2 trades per session. After 2 trades, the
day is done regardless of what the market shows.

### Rule 4 — After a Win, Stop

If a trade hits its target, the day is done. No second trade needed.

### Rule 5 — After a Loss, Re-Enter Only on Conviction

If a trade is stopped out but the original thesis is still valid —
the levels are intact, the direction has not changed, the market
offers a second setup — the Max Bot may re-enter. This is the only
scenario where a second trade happens.

### Rule 6 — Trading Window

No entries before 8:35 CT (ORB must be complete) or after 14:00 CT.

---

## Active Levels

Once the ORB is broken with confirmed displacement, the Max Bot
observes all available levels. However, not every observed level
automatically becomes an entry candidate. Only levels that are
**contextually connected to the active Market Story** may become
entry candidates.

A level is contextually connected when the market's unfolding
narrative gives it relevance — for example, a level that the price
broke through during displacement, a level where an OCL formed
during the directional move, or a level that the price is
approaching as part of the story's retest phase.

> **Status:** The exact definition of "contextual connection" is not
> yet mechanical. Max evaluates this instinctively. Formalizing the
> criteria is future work.

When multiple levels produce valid Max Entry Candles in close
proximity, the Max Bot does not simply take the first one. A
Decision Engine evaluates which setup has the highest probability
of success based on confluence, context, and the market story.

> **Status:** The Decision Engine's exact evaluation criteria are not
> yet defined. Today Max makes this choice instinctively. Extracting
> the rules that drive that instinct is future work. Until then, the
> system records all valid setups and lets Max review them.

### Level Sources — Two Families

Levels are not all the same. They belong to two distinct families.

**Structural Levels** — exist before the session or are fixed at
the open. They are static. They do not change during the day.

| Source | Label | Type |
|---|---|---|
| ORB High | `ORB_HIGH` | Line |
| ORB Low | `ORB_LOW` | Line |
| Pre-Market High | `PMH` | Line |
| Pre-Market Low | `PML` | Line |
| Previous Day High | `PDH` | Line |
| Previous Day Low | `PDL` | Line |

**Generated Levels** — created during the session by the market's
price action. They do not exist at the open. New types will be
added in the future.

| Source | Label | Type |
|---|---|---|
| One Candle Level | `OCL` | Zone (wick) |
| *(future: FVG)* | — | — |
| *(future: Liquidity)* | — | — |

This separation matters because Generated Levels are conceptually
different: they emerge from the market's story as it unfolds. The
Level Provider contract is the same for both families — the Entry
Engine does not care which family a level belongs to — but the
Decision Engine may weight them differently.

### OCL Formation During the Session

The One Candle Level is not drawn before the open. It forms during
the session when the Max Bot observes:

1. A directional move with momentum (price covering ground, bodies
   large, one-sided)
2. A single opposing candle inside that move — the One Candle
3. Continuation after the One Candle in the original direction

The OCL zone is derived from the One Candle's wick geometry. The
exact definition of the OCL zone — which wick edge forms the near
boundary, which forms the far boundary, and how this differs between
bullish and bearish setups — is governed by the authoritative OCL
specification document and requires confirmation here before
implementation. Do not implement from this summary alone.

**Important:** The OCL does not require a strict sequence of
"momentum first, then One Candle." In practice, momentum and the
One Candle creation can happen simultaneously — the break itself can
create the order block with force. The Max Bot must not enforce a
rigid ordering.

---

## Two Stages of Break and Displacement

Displacement is not evaluated only at the ORB. There are two
distinct stages where break and displacement matter:

### Stage 1 — The ORB Break

The ORB break enables directional observation for the session. Price
crosses ORB High or ORB Low (BREAK_OBSERVED), then must show
displacement away from the ORB level (DISPLACEMENT_CONFIRMED) to
establish the session direction.

### Stage 2 — The Setup-Defining Break

During the directional move, the price may encounter and break
through a specific structural or generated level (PML, PDH, etc.).
This is the level that will later be retested as part of the entry
setup.

This second break also benefits from displacement. A structural
level that is crossed and immediately re-crossed has not been
convincingly broken. A level where the price breaks through and
then visibly separates — candles opening and closing fully on the
other side — has been broken with conviction.

Both stages follow the same principle: a break is provisional until
displacement confirms it. The difference is scope:

- Stage 1 sets the session direction
- Stage 2 defines the specific level for a retest setup

In Max's examples, these two stages often overlap — the ORB break
and the structural level break happen in the same directional move,
with displacement confirming both simultaneously.

---

## Confluence — The Quality Multiplier

A level by itself is a signal. A level that coincides with another
independent structure is a stronger signal. This is confluence.

### How Confluence Works

When the OCL forms at or near a structural level (ORB, PML, PDH,
etc.), the retest of the OCL is simultaneously a retest of that
structural level. Two independent reasons to expect a rejection at
the same price.

### Observed Confluence Patterns (from Max's examples)

**MNQ 31 July 2026:** OCL forms exactly where the price is breaking
through PML. Retest of OCL = retest of broken PML. Short entry.

**MNQ 29 July 2026:** Same pattern two days earlier. ORB break
downward, price fights PML, breaks it, OCL forms at the break point,
retest hits OCL + PML together. Short entry.

**TSLA counter-trend example:** Price in daily downtrend, but
intraday breaks ORB High, PMH, and PDH upward. Retest of PMH
produces Max Entry Candle. Long entry despite daily downtrend —
because three levels were broken in the same direction.

**TSLA trend-aligned example:** Daily downtrend, price breaks ORB
Low, OCL forms near ORB Low. Retest has confluence of ORB Low + OCL.
Short entry. The simplest, highest quality scenario.

### Two Tiers of Setup

The Max Bot recognizes two distinct setup types:

- **Level Retest** — the price returns to a structural level (ORB,
  PML, PDH, etc.) and produces a Max Entry Candle. This is a valid
  trade.

- **Level Retest + OCL** — the price returns to a structural level
  where a One Candle Level also exists. The retest hits both the
  structural level and the OCL. This pattern has been observed as
  high-quality in Max's examples.

However, a final quality classification (A+, A, B) cannot be
assigned from confluence alone. Break quality, displacement strength,
retest character, rejection quality, and broader context all
contribute. The quality grading system requires empirical validation
through the Strategy Tester before it can be frozen.

The OCL alone without a structural level is a weaker signal. The
structural level alone without an OCL is a valid signal.

### Confluence Weighting

Confluence is not simply "more levels = better." Different levels
carry different weight. For example:

- PDH + PMH at the same price is not the same as OCL + PMH
- A Structural Level + a Generated Level together may be stronger
  than two Structural Levels, because they represent independent
  confirmation from different sources

The exact weighting system is not yet defined. For now, the Max Bot
records which levels are near the entry zone, their family
(Structural vs Generated), and their source. This data is stored
in the Trade Candidate for future analysis, so that patterns in
what combinations produce the best results can be discovered
empirically through the Strategy Tester.

---

## The Market Story

The most important concept in the Max Bot is not a level, a candle,
or a rule. It is the **story the market is telling**.

When Max looks at a chart, he does not evaluate a checklist of
independent conditions. He reads a narrative:

```
The market opened
    → it broke below the ORB (BREAK_OBSERVED)
    → it displaced away from the broken level (DISPLACEMENT_CONFIRMED)
    → during the move, it broke through the Pre-Market Low
    → that break also showed displacement
    → at the point of that structural break, one candle paused (OCL)
    → the push continued, making new lows
    → the market pulled back
    → it returned to the zone where the OCL and the PML converge
    → a candle wicked into that zone and rejected
    → now I can enter
```

This is not a list of if-then rules. It is a sequence of **events**
that build on each other. Each event only makes sense in the context
of what came before.

### Displacement — An Essential Event

A break is not confirmed merely because price crosses a level or
makes a new extreme. **Displacement** is the evidence that the break
is real.

Displacement requires genuine visual separation from the broken
level: typically at least a couple of candles opening and closing
fully outside the level, creating visible space between the current
price and the broken level. Candles continuously resting on or
repeatedly crossing the level do not establish strong displacement.

Displacement applies at both stages: the ORB break (Stage 1) and
the setup-defining structural/generated level break (Stage 2). A
break at either stage is provisional (BREAK_OBSERVED) until
displacement confirms it (DISPLACEMENT_CONFIRMED).

> **Status:** The exact mechanical definition of displacement (how
> many candles, how much separation, in what timeframe) is not yet
> frozen. Max reads this visually. A mechanical proxy will need to
> be validated through examples and testing.

### Story Invalidation

The active Market Story can be invalidated. A **material close back
through the broken level** means the break has failed. The story
is no longer active.

After invalidation, a new valid break and displacement sequence is
required before a new retest entry can exist. The Max Bot does not
continue trading the old story after it has been invalidated.

> **Status:** The exact threshold for "material re-entry" (one
> candle close? multiple candles? how far back through the level?)
> is not yet frozen.

### Story Approach vs Checklist Approach

The Max Bot must be built to follow stories, not evaluate isolated
conditions. The difference is fundamental:

**Checklist approach (wrong):**
- ORB broken? yes
- OCL exists? yes
- Retest happened? yes
- Entry candle valid? yes
→ trade

**Story approach (correct):**
- The market broke the ORB with conviction
- Displacement confirmed the ORB break (visible separation)
- During the move, it broke a structural level
- Displacement confirmed that structural break too
- At the point of that structural break, a pause occurred (OCL)
- After the pause, the market confirmed by making new extremes
- The market then returned to the break zone
- At the break zone, a candle showed rejection
→ this story says: the market tested a level, broke it, confirmed
  the break, and now the broken level is holding as new
  support/resistance. Enter.

### Conceptual State Sequence — Market Story Lifecycle

The Market Story progresses through a sequence of states. These
states are conceptual — they describe the story's progression, not
a frozen software contract:

```
WAITING_FOR_BREAK
    → BREAK_OBSERVED
    → DISPLACEMENT_PENDING
    → DISPLACEMENT_CONFIRMED
    → RETEST_PENDING
    → RETEST_IN_PROGRESS
    → REJECTION_CONFIRMED
    → ENTRY_ELIGIBLE
```

Terminal and interruption states for the **Market Story**:

```
STORY_INVALIDATED   — material re-entry through the broken level
STORY_EXPIRED       — session window ended (14:00 CT)
```

A Market Story can remain active even after a trade is taken and
resolved. The story describes the market's structural condition;
the trade is one attempt to profit from that condition.

### Conceptual State Sequence — Trade Lifecycle

A Trade is a separate lifecycle from the Market Story that produced
it:

```
TRADE_ENTERED
    → TRADE_ACTIVE
    → TRADE_WON (target hit)
    or
    → TRADE_STOPPED_OUT (stop hit)
```

**TRADE_STOPPED_OUT does not automatically mean STORY_INVALIDATED.**
The market story may remain active and structurally valid after a
stop-out. If the original thesis is intact — the levels are not
broken, the direction has not reversed, the market offers a new
valid entry — a second trade is permitted (subject to the 2-trade
daily limit).

A second trade requires a **new valid entry opportunity**: a new
Max Entry Candle at a contextually connected level while the
original Market Story remains active. It is not a re-entry at the
same price.

> **Status:** All state names in both lifecycles are conceptual
> illustrations. They are not a frozen software contract. The exact
> state machine definition is future work.

---

## Intraday Overrides Daily

The daily trend is the starting point, not a law. The intraday
action has the final word.

**If daily and intraday agree:** highest quality. No hesitation.

**If daily and intraday disagree:** the Max Bot follows the intraday
action, provided the intraday evidence is strong — multiple levels
broken in the opposite direction, clear momentum and displacement,
convincing price action.

The daily trend is a weight, not a gate. It influences the quality
assessment but does not block trades. A setup against the daily trend
with three broken levels intraday is still a valid trade.

### Supporting Heuristic

Price above PDH adds bullish contextual evidence. Price below PDL
adds bearish contextual evidence. This supplements but does not
automatically replace the complete daily and intraday reading. All
available context — daily trend, price relative to PDH/PDL, intraday
story, displacement quality — is considered together.

---

## Multi-Instrument Alignment

When trading individual tech names (TSLA, NVDA, AAPL, etc.), Max
checks that SPY and QQQ support his direction.

When trading SPY or QQQ, Max checks that both move in the same
direction.

When trading MES or MNQ, same rule — both should agree.

### How Alignment Works in Practice

This is not a binary filter. It is a timing mechanism.

**If SPY/QQQ are moving in your direction:** no problem, proceed.

**If SPY/QQQ are approaching a support/resistance in the opposite
direction:** Max waits. He watches them reach the level and sees how
they react. If they bounce back toward his direction, he enters. If
they break through, he may reconsider.

**Example:** Max wants to go long on TSLA. He sees QQQ is pulling
back toward a support level. He waits for QQQ to reach that support.
If QQQ bounces off the support (confirming the long bias), he enters
TSLA. If QQQ breaks through the support, the long thesis weakens.

This is sophisticated: the Max Bot uses correlated instruments as
a timing tool, not as a permission gate.

### Alignment Timing

Alignment is monitored throughout the retest and rejection
development — not only after risk calculations are complete. If
alignment is unresolved at the moment a Max Entry Candle closes,
the candidate enters a conceptual WAITING_FOR_ALIGNMENT state.

> **Open questions for alignment waiting:**
>
> - **Candidate expiration:** How long can a candidate remain in
>   WAITING_FOR_ALIGNMENT before it expires? Is there a time limit
>   or a price-movement limit?
> - **New entry candle:** If alignment resolves after the original
>   entry candle, does the original candle remain valid, or is a
>   new Max Entry Candle required?
> - **Permissible price movement:** How far can price move from the
>   entry candle's close while waiting for alignment before the
>   candidate is invalidated?
> - **State transition:** How does WAITING_FOR_ALIGNMENT interact
>   with the Market Story lifecycle? Can the story progress to a
>   different retest while one candidate waits?

> **Status:** This behavior is observed but needs more examples to
> fully specify. The exact rules for alignment are not yet frozen.

---

## The Entry

The entry is always the same, regardless of which level generates it.

A **Max Entry Candle** is a candle that:

1. Reaches into the level zone with its wick — the wick should be
   well present inside the zone, not just a touch
2. Closes on the correct side of the level (above for LONG, below
   for SHORT)
3. Closes **just outside** the level — close to it, not far from it

**Why not far from the level?** A candle that closes too far from the
zone creates two problems: (a) the stop loss becomes too large,
and (b) the price is likely to return to the zone seeking a better
rejection. The ideal entry candle wicks well into the zone and closes
just barely past it — tight stop, immediate move in the right
direction.

The geometry rules for what constitutes a valid rejection are defined
in the Entry Candle Engine specification, not here.

### Multi-Timeframe Entry

Max trades primarily on 1 minute. But if the 1-minute candle at a
level is messy (two small candles, unclear bodies), he checks 2
minutes or 5 minutes. If the same price action produces a clean Max
Entry Candle on a different timeframe, he takes it.

A 1m, 2m, and 5m reading of the same retest represents **one entry
opportunity**, not three separate candidates. The different
timeframes are lenses on the same price action. The Max Bot uses
them to find the clearest reading of a single event.

> **Open questions for multi-timeframe handling:**
>
> - **Deduplication:** How does the system recognize that three
>   timeframe readings of the same retest are one opportunity?
> - **Primary timeframe:** Is 1m always checked first, with 2m and
>   5m as fallbacks? Or are all evaluated simultaneously?
> - **Confirmation timing:** A 5m candle closes later than the 1m
>   candles it contains. If the 5m shows a valid entry but the 1m
>   did not, at what moment is the entry taken?
> - **Entry-price validity:** If a valid Max Entry Candle appears on
>   5m but price has already moved away by the time the 5m candle
>   closes, is the entry still valid at the 5m close price?

---

## Risk Management

The entry decision is never driven by risk size. Max enters because
**the candle is right** — the market story supports it and the entry
candle geometry is correct.

However, risk must be fully calculated and validated before an order
is executed. The conceptual order is:

```
1. Validate the Market Story (active, not invalidated)
2. Validate the Max Entry Candle (geometry correct)
3. Check multi-instrument alignment
4. Calculate stop price and target price
5. Calculate position size and monetary risk
6. Apply operational risk limits
7. Execute or reject the trade
```

The story, the candle, and the alignment determine whether the trade
is valid. Risk management determines whether the trade is executable.

### Stop Loss

Confirmed stop modes:

- **ENTRY_CANDLE** — stop placed at the extreme of the entry candle
  (below the low for LONG, above the high for SHORT)
- **FULL_ZONE** — stop placed at the far edge of the level zone.
  Confirmed for OCL-based entries (the far wick edge of the One
  Candle).

> **Open question:** For entries on Structural Line levels (ORB High,
> ORB Low, PMH, PML, PDH, PDL), the exact stop treatment is not yet
> confirmed. Do not assume that a LONG at ORB High uses ORB Low as
> its stop. This requires confirmation from Max.

### Target

**2R** — the target is 2 times the risk (distance from entry to
stop). This is the fixed target policy of the current Max Bot
version. Future versions or discretionary overrides by Max may use
different target management; this has not been explicitly explored
yet.

---

## The Max Bot Decision Flow — Complete Sequence

```
PRE-OPEN (before 8:30)
    Calculate PDH, PDL
    Calculate PMH, PML
    Receive Max's daily context (TREND_UP / TREND_DOWN / RANGE)

ORB FORMATION (8:30 — 8:34)
    Watch ORB form (5 minutes)
    Mark ORB High, ORB Low
    Observe price action (no entries)

TRADING WINDOW (8:35 — 14:00)

    Is there an active Market Story?
        NO →
            Has the ORB been broken?
                NO → observe, record context, wait
                YES (BREAK_OBSERVED) →
                    Has displacement confirmed the break?
                        NO (DISPLACEMENT_PENDING) → wait
                        YES (DISPLACEMENT_CONFIRMED) →
                            Active story begins, direction set

        YES →
            Has the story been invalidated?
                YES → story reset, wait for new break + displacement
                NO → continue

    Observe all levels (Structural + Generated as they form)
    Identify levels contextually connected to the active story

    For each completed candle on 1m (also check 2m, 5m as one
    opportunity):
        Does any contextually connected level have a Max Entry
        Candle?
            NO → continue watching
            YES →
                How many trades today?
                    Already had a win → STOP, day is done
                    Already had 2 trades → STOP, day is done
                    Otherwise →
                        Validate Market Story is active
                        Check alignment (SPY/QQQ if applicable)
                            Aligned → proceed
                            Not aligned → WAITING_FOR_ALIGNMENT
                            Opposing → SKIP or reduce quality
                        Calculate stop, target, position size
                        Apply risk limits
                        EXECUTE or REJECT

    After 14:00 → STOP, day is done
```

---

## What the Max Bot Is Not

The Max Bot is not a strategy. It is not an optimizer. It does not
predict direction. It is a **Session Brain** — an observer that
reads the market's story and makes decisions based on context,
levels, and price action.

It observes the market exactly as Max does:

- Draw the map
- Wait for the break and displacement
- Read the story the market is telling
- When the story aligns with a contextually connected level and
  produces a valid entry candle, enter
- Manage the trade mechanically
- Stop after one win or two trades

---

## Relationship to the Strategy Tester

The Max Bot and the Strategy Tester serve different purposes.

**The Strategy Tester** runs one Level Provider at a time with the
Max Entry Candle and measures results. It answers: "How does ORB
alone perform? How does OCL alone perform? How does PML alone
perform?"

**The Max Bot** runs all Level Providers simultaneously and applies
the full contextual rules (trend, alignment, confluence, session
limits). It answers: "How would Max have performed today?"

Both use the same Entry Engine, the same Level Provider contracts,
and the same Trade Candidate format. The difference is the policy
layer on top.

---

## Open Questions

These aspects of Max's process have been observed but not yet
specified precisely enough to freeze:

1. **Displacement mechanical definition:** How many candles of
   separation constitute valid displacement? How much distance from
   the broken level? On which timeframe? Max reads this visually —
   a mechanical proxy needs validation.

2. **Material re-entry / invalidation threshold:** How far back
   through the broken level must price close to invalidate the
   active story? One candle close? Multiple candles? A percentage
   of the move?

3. **Opposite ORB side break:** If the price breaks ORB Low with
   displacement (SHORT story active), then later breaks ORB High,
   what happens? Does the SHORT story get invalidated? Does a new
   LONG story begin? Can both coexist? Requires examples from Max.

4. **Alignment waiting:** When a Max Entry Candle appears but
   alignment is unresolved — how long can the candidate wait?
   Does it expire? Is a new entry candle required if alignment
   resolves later? How far can price move while waiting?

5. **Multi-timeframe deduplication:** How does the system recognize
   that 1m, 2m, and 5m readings of the same retest are one
   opportunity? What is the primary timeframe? What happens when a
   slower timeframe confirms after price has moved?

6. **Gap filling in range mode:** Max noted that in a range, the
   pre-market gap often fills and then reverses. The mechanics of
   detecting and trading this pattern are not yet described.

7. **OCL detection rigidity:** The One Candle Level formation
   criteria reference the OCL spec, but the relationship between
   momentum, break, and OCL creation "happening simultaneously"
   needs clearer definition.

8. **Structural-line stop placement:** For entries at Structural
   Line levels (ORB, PMH, PML, PDH, PDL), the exact stop treatment
   is not confirmed. Does ENTRY_CANDLE always apply? Is FULL_ZONE
   ever used? What defines the zone for a line level?

9. **Generated Levels formed before ORB break:** If an OCL or other
   Generated Level forms during the ORB period or before a valid
   break, is it eligible for entry after the break? Examples needed.

10. **Decision Engine evaluation criteria:** When multiple levels
    produce valid setups simultaneously, how does Max choose? Most
    confluence? Best story? Closest to price? This requires more
    examples to extract the instinctive rules.

11. **Contextual connection definition:** What precisely makes a
    level "contextually connected" to the active Market Story vs
    merely present on the chart? This is currently instinctive.

12. **Quality scoring:** The Max Bot currently does not score setups.
    Future versions may assign quality grades based on confluence,
    displacement strength, trend alignment, and other factors. This
    is deferred until empirical data from the Strategy Tester is
    available.

13. **Discretionary target management:** The current fixed 2R target
    policy may not cover all scenarios Max would manage
    differently. Has not been explored.

---

## Evidence Base

This specification was extracted from the following examples
provided by Max on 2026-08-03:

| # | Instrument | Date | Timeframe | Direction | Key Pattern |
|---|---|---|---|---|---|
| 1 | MNQ | 2026-07-31 | 1m | SHORT | ORB break + OCL at PML + retest |
| 2 | MNQ | 2026-07-29 | 1m | SHORT | Same pattern, PML as support→resistance |
| 3 | TSLA | 2026-07-09 | 5m | LONG | Daily downtrend overridden by intraday, retest PMH |
| 4 | TSLA | various | 1m | SHORT | Trend-aligned, ORB Low + OCL confluence |
| 5 | TSLA | 2026-02-02 | 1m | — | No ORB break, no trade, day skipped |
| 6 | TSLA | 2026-01-29 | 1m/2m | SHORT | Multi-timeframe entry, 1m messy, 2m clean |

---

## Document History

| Version | Date | Change | Status |
|---|---|---|---|
| 0.1 | 2026-08-03 | Initial draft from Max's live session examples | SUPERSEDED by 0.3 |
| 0.2 | 2026-08-03 | Added displacement, story invalidation, state sequence, corrected session phases, risk ordering, stop modes, open questions. Removed mechanical trend definition. | SUPERSEDED by 0.3 |
| 0.3 | 2026-08-03 | Separated BREAK/DISPLACEMENT consistently. Distinguished ORB break from setup-defining break. Contextual connection for level candidacy. OCL zone deferred to authoritative spec. Alignment timing with WAITING state. Multi-TF as one opportunity. Separated Market Story and Trade lifecycles. Softened PDH/PDL heuristic. 2R described as current policy. Status changed to AUTHORITATIVE DRAFT — NOT FROZEN. | CURRENT |
