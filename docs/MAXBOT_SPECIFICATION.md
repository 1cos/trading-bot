# MAXBOT_SPECIFICATION.md

> **Version:** 1.0 — **Date:** 2026-08-05
> **Status:** AUTHORITATIVE — SOURCE OF TRUTH
>
> This document is the technical constitution of the MaxBot project.
> It defines how MaxBot reasons about markets, not how the code is
> structured.
>
> **Binding rule:** No architectural or behavioral change may be
> implemented without first verifying consistency with this
> specification. If the code and this specification diverge, this
> specification prevails. Claude must read this document before any
> development task on the MaxBot project.
>
> **Change protocol:** This document may only be updated with
> explicit instruction from Max. Claude may propose amendments but
> must never apply them autonomously.

---

## §1 — Identity

MaxBot is NOT an ORB strategy.

MaxBot is a generic Break-and-Retest engine that applies one
universal trading logic to multiple sources of structural price
levels.

The trading logic never changes. Only the structural levels change.

ORB is one of many possible level sources. It was implemented first
because it is the simplest to detect. It has no architectural
privilege over any other level source.

---

## §2 — The Universal Trading Sequence

Every trade MaxBot takes follows exactly this sequence. No stage
may be skipped. No shortcut exists.

```
STRUCTURAL LEVEL
    ↓
BREAK
    ↓
DISPLACEMENT
    ↓
RETEST
    ↓
ENTRY CANDLE
    ↓
TRADE (stop from Entry Candle, target at R multiple)
```

This sequence is the same regardless of which level provider
generated the structural level. ORB, PDH, Pivot Wick, OCL — the
downstream logic is identical for all of them.

**Corollary:** When a new trading idea appears, the first question
is always: "Does this modify the Break-and-Retest sequence, or does
it only introduce a new structural level?" Almost every future
feature belongs to the second category. The engine must remain
stable. The providers may continuously evolve.

---

## §3 — Max's Daily Workflow

MaxBot replicates how Max trades each morning. The session is
divided into sequential phases. Each phase has strict
responsibilities and prohibitions.

### §3.1 — Pre-Market Analysis (before market open)

Before the opening bell, MaxBot builds the structural map for the
day by calculating:

- **PDH** — Previous Day High
- **PDL** — Previous Day Low
- **PMH** — Pre-Market High
- **PML** — Pre-Market Low

These levels exist before any candle of the current session prints.
They are static for the entire trading day.

Max also assesses the daily context: is the instrument in a trend
or in a range? This is a discretionary judgment supplied as an input
parameter (`TREND_UP`, `TREND_DOWN`, `RANGE`) until a reliable
mechanical proxy is validated.

**No trades are generated during this phase.** The pre-market phase
produces context only.

### §3.2 — ORB Construction (first 5 minutes of RTH)

The Opening Range is defined by the highest high and lowest low of
the first 5 minutes of regular trading hours.

- **ORB High** — highest price during the ORB window
- **ORB Low** — lowest price during the ORB window

The ORB timeframe is always 5 minutes regardless of the trading
timeframe (1m, 2m, 5m candles).

**No trades are permitted during ORB construction.** Observation
and level recording are allowed; entries are not.

### §3.3 — Post-ORB: Level Generation and Monitoring

After the ORB closes, the market begins to build new structure.
MaxBot must continuously detect and track:

- **Pivots** — classical swing highs and swing lows forming outside
  the ORB zone. Each pivot creates a Pivot Wick Zone defined by the
  pivot candle's wick. Multiple bounces on the same pivot increase
  the level's structural weight.

- **One Candle Levels (OCL)** — single opposing candles inside a
  strong directional move. The OCL zone is defined by the opposing
  candle's wick geometry (see ONE_CANDLE_LEVEL_SPEC.md).

These are Generated Levels — they do not exist at the open. They
emerge from the market's price action as the session unfolds.

**Critical constraint:** Generated Levels are only tracked outside
the ORB zone. Pivots and OCLs inside the ORB are not tradeable
structures.

### §3.4 — Trading Window

The trading window opens after ORB construction completes and
closes at the session end cutoff.

During this window, MaxBot continuously evaluates all active
structural levels (both pre-existing and generated) through the
universal trading sequence (§2).

### §3.5 — Session Rules

- Maximum 2 trades per session.
- After a winning trade, the session is done.
- After a losing trade, re-entry is permitted only if the original
  thesis remains structurally valid and a new Entry Candle appears.
- No new entries after the session end cutoff.

---

## §4 — Structural Level Providers

Every provider generates candidate structural levels. The
Break-and-Retest engine receives levels through a universal
contract (see LEVEL_PROVIDER_SPEC.md) and does NOT know which
provider created them.

### §4.1 — Provider Registry

**Static Levels** — exist before the session or are fixed at the
open. They do not change during the day.

| Provider       | Label      | Type | When Calculated        |
|----------------|------------|------|------------------------|
| ORB High       | `ORB_HIGH` | Line | At ORB window close    |
| ORB Low        | `ORB_LOW`  | Line | At ORB window close    |
| Previous Day H | `PDH`      | Line | Pre-market             |
| Previous Day L | `PDL`      | Line | Pre-market             |
| Pre-Market H   | `PMH`      | Line | Pre-market             |
| Pre-Market L   | `PML`      | Line | Pre-market             |

**Generated Levels** — created during the session by the market's
price action.

| Provider        | Label        | Type | When Created              |
|-----------------|--------------|------|---------------------------|
| Pivot Wick      | `PIVOT_WICK` | Zone | When a swing H/L forms    |
| One Candle Level| `OCL`        | Zone | When opposing candle forms |

**Future providers** (not yet specified):

| Provider        | Label   | Type |
|-----------------|---------|------|
| Fair Value Gap  | `FVG`   | Zone |
| Liquidity       | `LIQ`   | Zone |
| VWAP            | `VWAP`  | Line |

### §4.2 — Provider Contract

Every provider emits levels with exactly these fields:

```
price          float    Near edge of the level
price_far      float?   Far edge (null for line levels)
direction      string   SUPPORT or RESISTANCE
source         string   Provider label from the registry
created_at     int      Epoch milliseconds
```

Providers NEVER execute trades. Providers only generate levels.

---

## §5 — Break

A structural level becomes interesting only AFTER a valid break.

Without a break, there is no trade. A level that has never been
broken is context, not a setup.

A break is initially provisional (`BREAK_OBSERVED`). It becomes
confirmed only after displacement (§6).

---

## §6 — Displacement

Every break must demonstrate displacement — genuine visual
separation of price from the broken level.

Displacement is what distinguishes a real breakout from a false
one. A break without displacement is ignored.

**What displacement looks like:** Multiple candles opening and
closing fully on the breakout side of the level, creating visible
space between the current price and the broken level.

**What displacement is NOT:** Candles continuously resting on or
repeatedly crossing the level. A single candle poking through and
immediately returning.

Displacement validation is independent from the level provider.
Same logic everywhere.

**Two stages of displacement exist in a MaxBot session:**

1. **ORB Break displacement** — confirms the session's directional
   bias. Price breaks ORB High or ORB Low, then must show
   displacement to establish the session direction.

2. **Setup-defining displacement** — confirms the break of the
   specific level that will later be retested. This break occurs
   during the directional move after the ORB break.

Both stages follow the same principle. The difference is scope:
Stage 1 sets the session direction; Stage 2 defines the specific
level for a retest setup. In practice, they often overlap — the
ORB break and a structural level break happen in the same
directional move.

> **Implementation note:** The exact mechanical definition of
> displacement (minimum candle count, minimum separation distance,
> timeframe) is parametric and subject to ongoing calibration. The
> current engine uses `min_displacement_bars` as the primary gate.

---

## §7 — Retest

After displacement, the engine waits. No chasing. No breakout
entries. Only retests.

A retest occurs when price returns to the broken level from the
breakout side. The broken support becomes resistance; the broken
resistance becomes support.

The first retest of a broken level has the highest priority.
Subsequent retests are progressively less reliable.

---

## §8 — Entry Candle

The Entry Candle is the universal execution trigger of MaxBot.

It does NOT depend on which provider generated the level. ORB,
Pivot, PDH, OCL — the Entry Candle logic is identical for all.

**What makes a valid Entry Candle:**

1. The wick reaches into the level zone — not a mere touch, but
   meaningful penetration.
2. The body closes on the correct side of the level (above for
   LONG, below for SHORT).
3. The close is just outside the level — close to it, not far.
   A candle that closes too far creates an oversized stop and
   suggests the price will return to seek a tighter rejection.

**Geometry gates** (current engine parameters):
- `rejection_wick_ratio` — minimum ratio of rejection wick to total
  candle range
- `body_ratio_max` — maximum body-to-range ratio (large bodies
  indicate momentum, not rejection)
- `confirmation_wick_penetration_pct_min` — minimum percentage of
  the rejection wick that actually penetrates inside the level zone
- Body must be completely outside the ORB/level zone

See ENTRY_CANDLE_ENGINE_SPEC.md for the full interface contract.

**Multi-timeframe reading:** Max trades primarily on 1m. When the
1m candle at a level is messy, he checks 2m or 5m. If the same
price action produces a clean Entry Candle on a different
timeframe, he takes it. Multiple timeframe readings of the same
retest represent ONE entry opportunity, not separate candidates.

---

## §9 — Stop Loss

The stop is ALWAYS derived from the Entry Candle.

Never from the originating structural level.

- **LONG:** stop below the low of the Entry Candle
- **SHORT:** stop above the high of the Entry Candle

The level tells MaxBot where to look. The Entry Candle tells MaxBot
where to place the stop. These are separate concerns.

---

## §10 — Target

Risk management is expressed in multiples of R, where R is the
distance from entry price to stop price.

Current implementation: **2.1R**

Future versions may expose R:R as a configurable parameter.

---

## §11 — Pivot Wick Provider

### §11.1 — What a Pivot Is

A classical swing point where price reverses direction.

- **Pivot Low:** price makes a low, then reverses upward. At least
  one lower candle on each side of the pivot candle.
- **Pivot High:** price makes a high, then reverses downward. At
  least one higher candle on each side of the pivot candle.

Only pivots that form OUTSIDE the ORB zone are relevant.

### §11.2 — The Pivot Wick Zone

Every pivot creates a zone defined by the pivot candle's wick:

- **Pivot Low zone:** from the candle body's lower edge down to
  the wick low. This is the rejection area.
- **Pivot High zone:** from the candle body's upper edge up to
  the wick high.

The zone — not the single price point — is where MaxBot expects
future reactions.

### §11.3 — Pivot Strength Through Multiple Bounces

When price bounces off the same pivot multiple times, each bounce
increases the structural weight of that level. More bounces = the
market is recognizing this level = when it finally breaks, the
break-and-retest setup is stronger.

This is structural evidence, not a separate strategy.

### §11.4 — Pivot Activation

A pivot becomes eligible for a Break-and-Retest setup ONLY after
it is broken. Before the break, the pivot is just a reference
level.

After the break:
1. The pivot that was support becomes resistance (or vice versa).
2. The Pivot Wick Zone becomes the retest target.
3. The first retest from the opposite side is the highest quality
   opportunity.
4. A valid Entry Candle at the Pivot Wick Zone triggers the trade.

---

## §12 — One Candle Level (OCL) Provider

### §12.1 — What OCL Is

A single opposing candle inside a strong directional move. The
OCL marks the point where the market briefly paused before
continuing.

See ONE_CANDLE_LEVEL_SPEC.md for the complete formation rules
and zone geometry.

### §12.2 — OCL as Precision Tool

OCL operates at the micro level. It defines the precise price
point where MaxBot expects a reaction within a broader structural
zone.

When Max looks at a Pivot Wick Zone and sees an OCL inside it,
the OCL tells him exactly where within that pivot zone to place
the entry. The Pivot provides the structural context; the OCL
provides the precision.

### §12.3 — OCL Independence

OCL can also generate trades independently, without a surrounding
structural level. It follows the same universal trading sequence:
OCL forms → price breaks away → displacement → price retests the
OCL zone → Entry Candle → trade.

---

## §13 — Confluence

### §13.1 — Definition

Confluence is the coexistence of multiple structural levels in the
same price area. It is NOT a separate strategy. It is NOT a new
trading logic. It is simply the observation that two or more
independent structural reasons exist for the market to react at a
specific price.

### §13.2 — Observed Confluence Patterns

| Confluence              | Meaning                                                |
|-------------------------|--------------------------------------------------------|
| Pivot + OCL             | OCL inside Pivot Wick Zone → precise entry within structure |
| ORB + Pivot             | Pivot forms at ORB edge → double structural reference  |
| PDH/PDL + Pivot         | Prior day extreme aligns with intraday pivot           |
| PMH/PML + Pivot         | Pre-market extreme aligns with intraday pivot          |
| ORB + OCL               | OCL forms at ORB level during break                    |
| PDH/PDL + OCL           | OCL at prior day extreme                               |
| Three or more levels    | Rare but highest quality                               |

### §13.3 — How Confluence Affects Selectivity

Confluence does NOT change the Break-and-Retest logic. Every stage
of the universal sequence (§2) still applies.

What confluence changes is how selective MaxBot needs to be about
the Entry Candle:

- **Single level (e.g., Pivot alone):** MaxBot requires a
  near-perfect Entry Candle — deep wick penetration, decisive
  close, clear rejection geometry.

- **Confluence (e.g., Pivot + OCL):** The zone is structurally
  stronger. MaxBot can accept an Entry Candle that is good but not
  textbook-perfect, because the weight of the decision is
  distributed between zone quality and candle quality.

This is a continuous spectrum, not a binary switch:

```
Zone Quality + Entry Candle Quality = Trade Confidence

Weak zone    + Perfect candle  = Acceptable trade
Strong zone  + Good candle     = Good trade
Strong zone  + Perfect candle  = Best trade
Weak zone    + Weak candle     = No trade
```

### §13.4 — Level Hierarchy

Not all levels play the same role in a confluence:

**Structural (macro) levels** define WHERE to look for a trade:
ORB, PDH, PDL, PMH, PML, Pivot Wick.

**Precision (micro) levels** define WHERE EXACTLY within the macro
zone to expect the reaction: OCL.

When an OCL forms inside a Pivot Wick Zone, the correct reading
is: "I am trading a retest of the Pivot, and the OCL is showing
me the exact reaction point."

### §13.5 — Implementation Sequence

Confluence detection is Phase 7 of the implementation roadmap (§16).
It is deliberately last. Each level provider must work independently
before confluence is evaluated.

Initially, confluence only labels and records which levels overlap.
It does NOT modify entry criteria. Only after empirical data from
backtesting is collected will the decision be made whether
confluence permits a less strict Entry Candle threshold.

---

## §14 — Context and Bias

### §14.1 — Pre-Market Bias

Max determines directional bias by observing:

- Position of pre-market price relative to PDH/PDL
- Gap direction and magnitude
- Daily chart trend assessment

If price is above PDH → bullish contextual evidence.
If price is below PDL → bearish contextual evidence.
If price is inside the prior day range → neutral, wait for ORB.

### §14.2 — Intraday Overrides Daily

The daily trend is the starting point, not a law. Intraday price
action has the final word.

- **Daily and intraday agree:** highest quality setups. No
  hesitation.
- **Daily and intraday disagree:** MaxBot follows intraday IF the
  intraday evidence is strong — multiple levels broken in the
  counter-trend direction, clear displacement, convincing momentum.

The daily trend is a weight, not a gate. A counter-trend trade with
three broken levels intraday is still valid.

### §14.3 — Multi-Instrument Alignment

When trading individual names (TSLA, NVDA, etc.), MaxBot checks
that SPY and QQQ support the direction. When trading SPY or QQQ,
both should agree. When trading MES or MNQ, same rule.

Alignment is a timing tool, not a permission gate. If the
correlated instrument is approaching a key level in the opposite
direction, MaxBot waits to see how it reacts before entering.

---

## §15 — What MaxBot Is NOT

- MaxBot is NOT a strategy optimizer.
- MaxBot is NOT a breakout system (it waits for retests).
- MaxBot does NOT predict direction (it reads the story).
- MaxBot does NOT enter because a level exists (it enters because
  the full Break → Displacement → Retest → Entry Candle sequence
  completed).
- MaxBot does NOT score setups by inventing abstract quality
  metrics (it reads structural weight, confluence, and price
  action).

MaxBot is a Session Brain — an observer that reads the market's
story and acts only when the story produces a valid entry at a
level with demonstrated structural significance.

---

## §16 — Implementation Roadmap

Each phase is completed, tested, and committed before the next
begins. No phase may be started while the previous is incomplete.

### Phase 1 — Audit Current System
Verify exactly: what works today in the Lab, which levels are
calculated, where break/displacement/retest/entry are coded,
whether ORB is separable from the engine. **No code changes.**

### Phase 2 — Make Level Selectable
Refactor the engine to receive a level as input rather than
assuming ORB. ORB continues to work exactly as before. All
existing tests must pass.

### Phase 3 — Previous Day High/Low
Add PDH and PDL as level providers. Chart visualization. Lab
selection. Backtest with unchanged trading logic.

### Phase 4 — Pre-Market High/Low
Add PMH and PML. Define the pre-market window. Visualization,
selection, test.

### Phase 5 — Pivot Wick
Implement pivot detection (classical only). Parametric minimum
wick filter. Pivot Wick Zone as the level. Activation only after
break. First retest priority. Same Entry Candle, same stop,
target at R multiple. Initially on 1m timeframe. No double
pivots, no scoring, no exceptions.

### Phase 6 — One Candle Level
Implement OCL as an independent level provider. Chart
visualization. Independent backtesting with unchanged logic.

### Phase 7 — Confluence
Detect level overlap. Label confluences. Compare results: single
level vs Pivot+OCL vs ORB+Pivot vs PDH+Pivot vs PMH+Pivot.
Initially confluence does NOT modify entry criteria — it is
observation only. After data review, decide whether confluence
permits relaxed Entry Candle thresholds.

---

## §17 — Relationship to Other Documents

| Document                           | Role                                        |
|------------------------------------|---------------------------------------------|
| `docs/MAXBOT_SPECIFICATION.md`     | **This file.** Project constitution.        |
| `MAX_BOT_SPEC.md`                  | Session Decision Engine details (v0.3).     |
| `LEVEL_PROVIDER_SPEC.md`           | Universal level output contract.            |
| `ENTRY_CANDLE_ENGINE_SPEC.md`      | Entry Candle interface specification.       |
| `ONE_CANDLE_LEVEL_SPEC.md`         | OCL formation rules and zone geometry.      |
| `MAX_TRADING_DECISION_FLOW.md`     | Complete decision tree.                     |

If any of these documents contradict this specification, this
specification prevails and the contradicting document must be
updated.

---

## §18 — Evidence Base

This specification was derived from:

- Max's live trading sessions and screen recordings
- TradingView chart analysis with annotated Pivot Wick Zones and
  OCL zones (2026-08-05 session: MES/MNQ charts showing Pivot Low
  + OCL confluence with multiple bounces and rejections)
- MAX_BOT_SPEC.md v0.3 (2026-08-03)
- All prior session logs and discovery documents
- Direct voice-transcribed explanations from Max

### Key Evidence: 2026-08-05 Charts

Max demonstrated on live MES/MNQ 1m charts:

1. A Pivot Low forms, creating a Pivot Wick Zone (blue area).
2. Price bounces off the pivot multiple times, each bounce
   confirming its structural significance.
3. An OCL forms inside the Pivot Wick Zone — a precision level
   within the macro structure.
4. Price breaks below the pivot (the pivot that was support is now
   resistance).
5. Price retraces and retests the OCL zone, which sits inside the
   now-broken Pivot Wick Zone.
6. At the retest, two clear rejection candles at ~10:23 and ~10:32
   would have been valid SHORT entries.

This sequence demonstrates every principle in this specification:
structural level (Pivot) → Generated Level precision (OCL) →
confluence → break → displacement → retest → Entry Candle.

---

## §19 — Document History

| Version | Date       | Change                                       |
|---------|------------|----------------------------------------------|
| 1.0     | 2026-08-05 | Initial authoritative specification. Created from Max's direct instruction to establish the "Bibbia di MaxBot." Consolidates all prior discoveries, the 7-phase roadmap, and the daily workflow into one binding document. |

---

*END OF SPECIFICATION*
