# Max Bot — Session Discoveries 2026-08-03

> **Status:** Raw discoveries from Max's live session. Not yet
> incorporated into MAX_BOT_SPEC.md V0.3. To be reviewed and
> selectively promoted to a future spec version.

---

## Discovery 1 — Displacement Is Classification, Not Confirmation

**Source:** Max's explanation while reading TSLA 2026-07-09

Without displacement, the same sequence of candles can be
interpreted as either a breakout or a rejection — it depends on
what happens next. Two candles that on 1 minute look like "break +
re-entry" might be, on 2 minutes, a single rejection candle. Or,
if a third candle follows with an engulfing and the price separates,
those same two candles become part of a confirmed breakout.

Displacement is not "waiting for confirmation." It is the time
required to determine what you are looking at.

**Principle:** The Max Bot does not classify events immediately.
It waits until the evolution of candles confirms which structure
they represent, even when viewed at higher timeframes. A breakout
must continue to look like a breakout; a rejection must continue
to look like a rejection. Time is part of classification, not a
passive delay.

**Applies to:** break assessment, displacement evaluation, retest
judgment, Max Entry Candle validation.

---

## Discovery 2 — Before vs After Displacement: Two Different Worlds

**Source:** Max correcting the TSLA 2026-07-09 analysis

**Before displacement is confirmed:**
- A candle re-entering the zone after a break → the break is still
  in question. It might be a false breakout. Wait.
- Multiple candles re-entering → break is almost certainly failed.
  Reset to standby.

**After displacement is confirmed:**
- A candle re-entering the zone → this is the **retest** the Max
  Bot was waiting for. Evaluate it for a Max Entry Candle.
- If it wicks in and closes outside → entry.
- If it closes inside but the next candle does an engulfing and
  closes outside → the two candles together on 2m form a Max Entry
  Candle. Entry.

The same physical event (candle entering the zone) means completely
different things depending on whether displacement has occurred.

---

## Discovery 3 — Multi-Timeframe as Classification Judge

**Source:** Max explaining entry candle and break evaluation

When the primary timeframe (1m) produces ambiguous structure, the
Max Bot consults higher timeframes (2m, 5m) to determine the true
nature of the price action.

This applies in two directions:

**Preventing false invalidation:** A 1m candle that closes inside
the zone after a retest looks like a failed entry. But if the next
1m candle does an engulfing, the two candles together on 2m may
form a valid Max Entry Candle. The 1m ambiguity does not
automatically invalidate the setup.

**Preventing false confirmation:** A 1m candle that breaks a level
looks like a breakout. But without displacement, that same candle
plus the next one, viewed on 2m or 5m, may form a rejection candle.
The 1m break does not automatically confirm the event.

**Proposed rule:** When the timeframe primary (1m) produces an
ambiguous structure during break or retest, the Max Bot may consult
higher timeframes (2m, 5m) to determine whether the entire price
action represents a valid event. The ambiguity of 1m does not
automatically invalidate or confirm the setup.

---

## Discovery 4 — Only Strong Marked Levels

**Source:** Max rejecting a valid-looking retest in TSLA 2026-07-09

Max identified a "perfect" retest at 12:30 ET of the 10:05 higher
high, but explicitly did not take it: "I only trade on strong
marked levels."

Strong marked levels are:
- ORB High / Low
- PMH / PML
- PDH / PDL
- OCL formed during the session

NOT strong marked levels:
- Intraday swing highs/lows
- Round numbers
- Levels derived from indicators

This is stricter than V0.3 currently implies. V0.3 says levels must
be "contextually connected to the active Market Story." Max adds:
they must also be from the defined set of Level Providers. No
improvised levels.

---

## Discovery 5 — "Too Late" as a Filter

**Source:** Max declining a valid OCL retest in TSLA 2026-07-09

At 13:30 ET, the price retested the 13:05 OCL with what appeared
to be a valid candle. Max considered entering ("maybe almost") but
decided against it: "It's too late. The market has already made an
important move upward."

"Too late" is a concept not present in V0.3. Its definition is not
yet clear:

- Time-based? (after a certain hour)
- Distance-based? (price has moved X from the entry zone)
- Exhaustion-based? (the move has consumed its energy)
- Intuitive? (Max "feels" it's too late)

This needs more examples to define. But it is clearly a real filter
in Max's process.

---

## Discovery 6 — OCL Exists by Necessity

**Source:** Max explaining why OCL matters after an acceleration

When the market breaks all structural levels (ORB, PMH, PDH) in
one direction, there are no more pre-marked levels ahead. The only
levels available are:

- A previous high/low from the current move
- An OCL (order block) formed during the move

The OCL is not an alternative technique. It is the logically
necessary level when structural levels have all been passed. This
is why OCL matters most in strong trending sessions — it's the
only level left.

---

## Discovery 7 — Failed Breaks Are Data, Not Noise

**Source:** TSLA 2026-07-09 had three break attempts

Break 1 (10:00) → failed, no displacement, re-entered ORB
Break 2 (11:00) → failed, no displacement, re-entered ORB
Break 3 (11:35) → succeeded, displacement confirmed at 11:45

Each failed break resets to zero. Failed attempts do not build
cumulative conviction ("it tried three times so the next will
work"). Each break must demonstrate displacement on its own.

However, the failed breaks are not meaningless — they show that
the market keeps trying the same direction. Max noted this but
did not let it override the displacement requirement.

---

## Discovery 8 — NO TRADE Days Are Frequent and Expected

**Source:** TSLA 2026-07-09 final decision = SKIP

A day with three break attempts, two failures, one late success,
and no entry is a normal day. The Max Bot must not force trades.
The discipline to skip is as important as the discipline to enter.

---

## Relationship to V0.3

These discoveries do not contradict V0.3. They deepen and clarify
concepts that V0.3 describes at a higher level of abstraction.

The most significant gap they reveal in V0.3:

1. V0.3 does not distinguish pre-displacement re-entry (break in
   question) from post-displacement re-entry (retest). This is
   Discovery 2.

2. V0.3 does not define "too late." This is Discovery 5.

3. V0.3's "contextually connected levels" is weaker than Max's
   actual rule of "only strong marked levels." This is Discovery 4.

These should be addressed in a future V0.4 when Max approves.
