# OCL Synthetic Validation Examples

> **Status:** Accepted — confirms correct understanding of OCL v0.1
> **Created:** 2026-08-02
> **Purpose:** These 15 synthetic examples validate that the OCL concept
> has been interpreted correctly before any code is written.
> They are not real market data and must not be used as training data.

---

## Frozen Definitions Applied

### LONG

- 1-minute timeframe
- Strong upward momentum
- Exactly one bearish candle
- Bearish candle has an upper wick (pointing in trend direction)
- Trend continues afterward
- OCL zone = bearish candle open through high
- A later bullish entry/rejection candle itself touches the OCL wick zone
- That same candle rejects upward

### SHORT

- Exact mirror
- Exactly one bullish candle
- Lower wick (pointing in trend direction)
- OCL zone = low through open
- A bearish entry/rejection candle itself retests the wick zone and rejects
  downward

### Separate Quality Axes

- **Formation quality** judges the One Candle structure (momentum, candle
  geometry, wick, continuation) — independent of whether a trade occurs.
- **Trade quality** judges the retest and entry candle — independent of
  formation quality. A perfect formation with no retest produces no trade.

---

## Discovery Parameters (Unresolved)

These questions remain open. Do not freeze answers.

- Minimum wick size
- Momentum threshold (what qualifies as "strong" momentum)
- Continuation candle count (how many candles must follow)
- Continuation distance (how far price must move after)
- Minimum move away before retest qualifies
- Maximum OCL age (staleness)
- Multiple active OCLs in the same trend
- Whether second retests are allowed
- Exact rejection geometry thresholds
- Stop mode

---

## Examples

### #1 — LONG — Setup perfetto

```
C1  ▲  100.0→101.5  (H101.8  L99.8)
C2  ▲  101.5→103.2  (H103.5  L101.3)
C3  ▲  103.2→105.0  (H105.2  L103.0)
C4  ▼  105.0→104.0  (H106.0  L103.8)  ← ONE CANDLE
C5  ▲  104.0→106.5  (H106.8  L103.9)
C6  ▲  106.5→108.0  (H108.3  L106.2)
C7  ▲  108.0→109.5  (H109.8  L107.8)
          ...prezzo torna giù...
C8  ▼  109.0→107.0  (H109.2  L106.8)
C9  ▼  107.0→106.2  (H107.2  L106.0)
C10 ▲  105.8→107.5  (H107.8  L105.5) ← ENTRY CANDLE
```

**One Candle:** C4. Unica bearish dentro 3 bullish forti. Upper wick
105.0–106.0 punta nella direzione del trend.

**OCL zone:** 105.0–106.0 (open through high of bearish candle).

**Continuation:** C5–C7 portano il prezzo a 109.5.

| Field | Value |
|---|---|
| OCL formation valid | YES |
| Retest present | YES |
| Entry candle itself touches OCL wick | YES — C10 low 105.5 enters zone 105.0–106.0 |
| Entry valid | YES — C10 touches zone, closes bullish at 107.5 above the level |
| Formation quality | 9/10 — clean momentum, single opposing candle, clear wick, strong continuation |
| Trade quality | 9/10 — entry candle itself retests zone and rejects with force |

**Explanation:** C10 is the entry candle because it is the candle that
retests the wick zone and shows rejection. It does not require a separate
retest candle followed by a confirmation candle. The low of C10 enters the
zone, the close is bullish and well above — this is the signal.

---

### #2 — SHORT — Setup perfetto (mirror)

```
C1  ▼  200.0→198.0  (H200.3  L197.8)
C2  ▼  198.0→196.0  (H198.2  L195.5)
C3  ▼  196.0→194.0  (H196.5  L193.8)
C4  ▲  194.0→195.0  (H195.2  L193.0)  ← ONE CANDLE
C5  ▼  195.0→192.5  (H195.3  L192.0)
C6  ▼  192.5→190.0  (H192.8  L189.5)
          ...prezzo risale...
C7  ▲  190.5→192.8  (H193.0  L190.3)
C8  ▼  193.5→191.5  (H194.2  L191.2)  ← ENTRY CANDLE
```

**One Candle:** C4. Unica bullish nel downtrend. Lower wick 193.0–194.0
punta nella direzione del trend (giù).

**OCL zone:** 193.0–194.0 (low through open of bullish candle).

**Continuation:** C5–C6 portano il prezzo fino a 189.5.

| Field | Value |
|---|---|
| OCL formation valid | YES |
| Retest present | YES |
| Entry candle itself touches OCL wick | YES — C8 high 194.2 enters zone 193.0–194.0 |
| Entry valid | YES — C8 touches zone, closes bearish at 191.5 below the level |
| Formation quality | 9/10 — exact mirror of LONG case |
| Trade quality | 9/10 — entry candle penetrates zone, rejects, closes with force |

---

### #3 — LONG — Formazione perfetta, nessun retest

```
C1  ▲  100.0→102.0  (H102.3  L99.8)
C2  ▲  102.0→104.5  (H104.8  L101.8)
C3  ▼  104.5→103.5  (H105.5  L103.2)  ← ONE CANDLE
C4  ▲  103.5→106.0  (H106.2  L103.3)
C5  ▲  106.0→108.5  (H108.8  L105.8)
C6  ▲  108.5→110.0  (H110.5  L108.2)
C7  ▲  110.0→112.0  (H112.3  L109.8)
```

**One Candle:** C3. Upper wick 104.5–105.5.

**OCL zone:** 104.5–105.5.

**Continuation:** C4–C7 portano il prezzo a 112.

| Field | Value |
|---|---|
| OCL formation valid | YES |
| Retest present | NO — price never returns to the zone |
| Entry candle itself touches OCL wick | NO |
| Entry valid | NO — no candle retests the level |
| Formation quality | 9/10 — perfect One Candle in every respect |
| Trade quality | N/A — valid level, no trade generated |

**Explanation:** The formation is excellent. The level exists and is
legitimate. But no candle returns to touch it, so no entry is possible.
Formation quality and trade quality are separate judgments.

---

### #4 — LONG — Borderline: momentum debole

```
C1  ▲  100.0→100.5  (H100.8  L99.8)
C2  ▲  100.5→101.0  (H101.2  L100.3)
C3  ▲  101.0→101.3  (H101.5  L100.8)
C4  ▼  101.3→100.8  (H101.8  L100.5)  ← ONE CANDLE?
C5  ▲  100.8→101.5  (H101.8  L100.6)
C6  ▲  101.5→101.8  (H102.0  L101.3)
          ...prezzo torna...
C7  ▲  101.2→101.9  (H102.0  L101.1)  ← ENTRY?
```

**One Candle:** C4. Structurally present — single bearish, upper wick at
101.8.

**OCL zone:** 101.3–101.8.

**Continuation:** C5–C6 resume the direction.

| Field | Value |
|---|---|
| OCL formation valid | UNSURE |
| Retest present | YES — C7 low 101.1 passes below the zone |
| Entry candle itself touches OCL wick | YES — C7 range 101.1–102.0 passes through zone 101.3–101.8 |
| Entry valid | UNSURE |
| Formation quality | 4/10 — structure exists geometrically but "clear, fast momentum" is questionable. Range 0.3–0.5 per candle. This is the type of example Max must label |
| Trade quality | 4/10 — entry candle is also small. Judgment depends on the momentum definition which remains open |

**Explanation:** A mechanical filter would find this candidate. The question
is whether the context represents "strong momentum." Not declared invalid —
submitted to Max for labeling.

---

### #5 — SHORT — Wick piccolo

```
C1  ▼  200.0→197.5  (H200.3  L197.2)
C2  ▼  197.5→195.0  (H197.8  L194.5)
C3  ▲  195.0→196.0  (H196.2  L194.8)  ← ONE CANDLE
C4  ▼  196.0→193.5  (H196.3  L193.0)
C5  ▼  193.5→191.0  (H193.8  L190.5)
          ...prezzo risale...
C6  ▼  195.2→193.0  (H195.5  L192.8)  ← ENTRY CANDLE
```

**One Candle:** C3. Single bullish in downtrend. Lower wick = 194.8–195.0,
only 0.2 points.

**OCL zone:** 194.8–195.0 (low through open).

**Continuation:** C4–C5 carry price to 190.5 — strong.

| Field | Value |
|---|---|
| OCL formation valid | UNSURE |
| Retest present | YES |
| Entry candle itself touches OCL wick | YES — C6 opens at 195.2, high 195.5, passes through zone 194.8–195.0 |
| Entry valid | UNSURE |
| Formation quality | 6/10 — momentum is strong, structure is present, continuation is clear. The only question is wick size, which remains an open parameter |
| Trade quality | 6/10 — entry candle touches zone and closes bearish with force. If the small wick is acceptable, the trade is good |

**Explanation:** Wick size is not declared invalid. Minimum wick size is an
unresolved discovery parameter. The momentum and continuation are excellent.
This is a case Max must judge to establish the threshold.

---

### #6 — LONG — Due bearish, non una

```
C1  ▲  100.0→102.0  (H102.5  L99.8)
C2  ▲  102.0→104.5  (H104.8  L101.8)
C3  ▼  104.5→103.0  (H105.0  L102.8)  ← bearish #1
C4  ▼  103.0→102.0  (H103.5  L101.5)  ← bearish #2
C5  ▲  102.0→104.0  (H104.2  L101.8)
C6  ▲  104.0→106.0  (H106.5  L103.8)
```

**One Candle:** None — C3 and C4 are two consecutive bearish candles.

**OCL zone:** Does not exist.

| Field | Value |
|---|---|
| OCL formation valid | NO |
| Retest present | NO |
| Entry candle itself touches OCL wick | NO |
| Entry valid | NO |
| Formation quality | 0/10 — definition requires exactly one opposing candle. Two consecutive bearish candles is a two-candle pullback, not a One Candle |
| Trade quality | N/A |

---

### #7 — LONG — Bearish senza upper wick

```
C1  ▲  100.0→102.5  (H102.8  L99.8)
C2  ▲  102.5→105.0  (H105.3  L102.3)
C3  ▼  105.0→103.5  (H105.0  L103.0)  ← candidata?
C4  ▲  103.5→106.0  (H106.5  L103.3)
C5  ▲  106.0→108.0  (H108.3  L105.8)
```

**One Candle:** No. C3 opens at 105.0 and high is 105.0 — zero upper wick.

**OCL zone:** Does not exist. Without a wick there is no level.

| Field | Value |
|---|---|
| OCL formation valid | NO |
| Retest present | NO |
| Entry candle itself touches OCL wick | NO |
| Entry valid | NO |
| Formation quality | 0/10 — frozen rule: "wick must point in trend direction." High = Open means no upper wick |
| Trade quality | N/A |

---

### #8 — LONG — Nessuna candela opposta

```
C1  ▲  100.0→101.5  (H101.8  L99.5)
C2  ▲  101.5→103.0  (H103.5  L101.3)
C3  ▲  103.0→105.0  (H105.3  L102.8)
C4  ▲  105.0→106.5  (H106.8  L104.8)
C5  ▲  106.5→108.0  (H108.5  L106.2)
C6  ▲  108.0→109.0  (H109.5  L107.5)
```

**One Candle:** None — all candles are bullish.

| Field | Value |
|---|---|
| OCL formation valid | NO |
| Retest present | NO |
| Entry candle itself touches OCL wick | NO |
| Entry valid | NO |
| Formation quality | 0/10 — no opposing candle, no level possible |
| Trade quality | N/A |

---

### #9 — SHORT — Mercato choppy, nessun trend

```
C1  ▼  200.0→199.0  (H200.5  L198.5)
C2  ▲  199.0→200.0  (H200.5  L198.8)
C3  ▼  200.0→199.5  (H200.2  L199.0)
C4  ▲  199.5→200.2  (H200.5  L199.2)
C5  ▼  200.2→199.8  (H200.5  L199.5)
C6  ▼  199.8→199.5  (H200.0  L199.2)
```

**One Candle:** None — no directional trend exists.

| Field | Value |
|---|---|
| OCL formation valid | NO |
| Retest present | NO |
| Entry candle itself touches OCL wick | NO |
| Entry valid | NO |
| Formation quality | 0/10 — prerequisite missing: "clear, fast momentum." Without a trend, searching for a One Candle is meaningless |
| Trade quality | N/A |

---

### #10 — LONG — Due possibili One Candle nello stesso trend

```
C1  ▲  100.0→102.0  (H102.3  L99.8)
C2  ▲  102.0→104.0  (H104.3  L101.8)
C3  ▼  104.0→103.2  (H104.8  L103.0)  ← candidata A
C4  ▲  103.2→105.5  (H105.8  L103.0)
C5  ▲  105.5→107.0  (H107.3  L105.2)
C6  ▼  107.0→106.0  (H107.5  L105.8)  ← candidata B
C7  ▲  106.0→108.5  (H108.8  L105.8)
C8  ▲  108.5→110.0  (H110.3  L108.2)
```

**Candidate A — C3:** OCL zone 104.0–104.8 (open through high). Single
bearish, momentum before (C1–C2), continuation after (C4–C5).

**Candidate B — C6:** OCL zone 107.0–107.5 (open through high). Single
bearish, momentum before (C4–C5), continuation after (C7–C8).

| Field | Value |
|---|---|
| OCL formation valid | YES for both — each individually satisfies the structure |
| Retest present | Not shown |
| Entry candle itself touches OCL wick | Not shown |
| Entry valid | Not evaluable without retest |
| Formation quality A | 7/10 — solid structure |
| Formation quality B | 7/10 — solid structure |
| Trade quality | N/A — no retest shown |

**Explanation:** Both candidates are individually valid as formations. Whether
both remain active simultaneously, whether the newer one replaces the older,
or whether they coexist is an open question. Not assuming an answer — this is
a case to submit to Max during discovery.

---

### #11 — LONG — Il trend fallisce dopo la One Candle

```
C1  ▲  100.0→102.0  (H102.5  L99.8)
C2  ▲  102.0→104.5  (H104.8  L101.8)
C3  ▲  104.5→106.5  (H106.8  L104.3)
C4  ▼  106.5→105.5  (H107.2  L105.2)  ← ONE CANDLE?
C5  ▲  105.5→106.0  (H106.2  L105.3)
C6  ▼  106.0→104.5  (H106.2  L104.0)
C7  ▼  104.5→103.0  (H104.8  L102.5)
```

**One Candle:** C4 — single bearish, upper wick at 107.2, momentum before
is strong.

**OCL zone:** 106.5–107.2 (open through high).

**Continuation:** C5 gains +0.5, then C6–C7 reverse.

| Field | Value |
|---|---|
| OCL formation valid | UNSURE |
| Retest present | NO — price does not return to zone 106.5–107.2 |
| Entry candle itself touches OCL wick | NO |
| Entry valid | NO |
| Formation quality | 3/10 — momentum before is good (C1–C3), but continuation after C4 is the critical point. C5 makes a minimal attempt then price reverses. How strong continuation must be remains open, but the trend does not resume convincingly here |
| Trade quality | N/A — no retest |

**Explanation:** Formation is doubtful because continuation is nearly absent.
The threshold for "momentum continues after the candle" is not satisfied
convincingly, but the exact threshold remains a discovery parameter. Marked
UNSURE, not NO.

---

### #12 — SHORT — Continuazione minima, poi inversione

```
C1  ▼  200.0→197.5  (H200.2  L197.0)
C2  ▼  197.5→195.0  (H197.8  L194.5)
C3  ▲  195.0→196.0  (H196.2  L194.0)  ← ONE CANDLE?
C4  ▼  196.0→195.5  (H196.2  L195.2)
C5  ▲  195.5→197.0  (H197.5  L195.3)
C6  ▲  197.0→199.0  (H199.5  L196.8)
C7  ▲  199.0→201.0  (H201.5  L198.8)
```

**One Candle:** C3. Single bullish, lower wick at 194.0, strong momentum
before.

**OCL zone:** 194.0–195.0 (low through open).

**Continuation:** C4 drops 0.5, then C5–C7 reverse completely.

| Field | Value |
|---|---|
| OCL formation valid | UNSURE |
| Retest present | NO — price does not return to zone 194.0–195.0 after reversal |
| Entry candle itself touches OCL wick | NO |
| Entry valid | NO |
| Formation quality | 3/10 — same theme as #11. Strong momentum before, but continuation is a single weak candle followed by full reversal. Continuation threshold is open, but the trend does not resume |
| Trade quality | N/A — no retest |

---

### #13 — LONG — Retest molto distante nel tempo

```
C1  ▲  100.0→102.0  (H102.5  L99.8)
C2  ▲  102.0→104.5  (H104.8  L101.8)
C3  ▼  104.5→103.5  (H105.5  L103.2)  ← ONE CANDLE
C4  ▲  103.5→106.0  (H106.3  L103.3)
C5  ▲  106.0→108.0  (H108.5  L105.8)
C6  ▲  108.0→110.0  (H110.5  L107.8)
       ...50 candele dopo, contesto diverso...
C57 ▼  112.0→109.0  (H112.5  L108.5)
C58 ▼  109.0→106.0  (H109.5  L105.5)
C59 ▲  105.2→107.5  (H107.8  L104.8)  ← ENTRY?
```

**One Candle:** C3. Upper wick 104.5–105.5.

**OCL zone:** 104.5–105.5 (open through high).

**Continuation:** C4–C6 carry price to 110 — excellent.

| Field | Value |
|---|---|
| OCL formation valid | YES |
| Retest present | YES — 55+ candles later, C59 touches the zone |
| Entry candle itself touches OCL wick | YES — C59 low 104.8, enters zone 104.5–105.5, closes bullish at 107.5 |
| Entry valid | UNSURE |
| Formation quality | 9/10 — the formation is perfect at the time it is created |
| Trade quality | UNSURE — the formation is valid, the entry candle touches the zone and reacts. The question is whether a level 55+ candles old retains significance. OCL staleness is an open discovery parameter. Not declared invalid |

**Explanation:** Formation is excellent. Entry candle respects the mechanics
(touches zone, closes bullish). The only question is elapsed time and changed
context (price fell from 112, not from 110). Max must decide whether this
type of late retest has value.

---

### #14 — LONG — Entry candle tocca la zona ma non convince

```
C1  ▲  100.0→102.5  (H102.8  L99.5)
C2  ▲  102.5→105.0  (H105.3  L102.2)
C3  ▼  105.0→104.0  (H106.0  L103.8)  ← ONE CANDLE
C4  ▲  104.0→107.0  (H107.3  L103.8)
C5  ▲  107.0→109.0  (H109.5  L106.8)
        ...prezzo torna...
C6  ▼  108.0→106.5  (H108.2  L106.2)
C7  ▲  105.8→106.1  (H106.2  L105.5)  ← ENTRY?
```

**One Candle:** C3. Upper wick 105.0–106.0.

**OCL zone:** 105.0–106.0 (open through high).

**Continuation:** C4–C5 carry price to 109 — strong.

| Field | Value |
|---|---|
| OCL formation valid | YES |
| Retest present | YES |
| Entry candle itself touches OCL wick | YES — C7 low 105.5 enters zone 105.0–106.0 |
| Entry valid | UNSURE |
| Formation quality | 8/10 — solid One Candle in every respect |
| Trade quality | 4/10 — C7 touches the zone but body is 0.3 points (105.8→106.1), total range 0.7. Does not show rejection force. The candle is in the zone and closes barely above — indecision more than rejection. BDRR rejection implies the level repels price with conviction. Here the reaction is anemic |

**Explanation:** Excellent formation, mechanically present entry (candle
touches zone). But the quality of the rejection is weak. A discretionary
trader might not enter on such a feeble candle. Exact rejection geometry
thresholds remain an open discovery parameter.

---

### #15 — SHORT — Zona violata, no rejection

```
C1  ▼  200.0→197.5  (H200.3  L197.0)
C2  ▼  197.5→195.0  (H197.8  L194.5)
C3  ▲  195.0→196.0  (H196.3  L193.5)  ← ONE CANDLE
C4  ▼  196.0→193.0  (H196.3  L192.5)
C5  ▼  193.0→190.5  (H193.3  L190.0)
        ...prezzo risale...
C6  ▲  191.0→193.0  (H193.2  L190.8)
C7  ▲  193.0→194.0  (H194.2  L192.8)
C8  ▲  194.0→195.5  (H196.0  L193.8)  ← touches zone, closes bullish above
```

**One Candle:** C3. Single bullish, lower wick at 193.5.

**OCL zone:** 193.5–195.0 (low through open).

**Continuation:** C4–C5 carry price to 190 — strong.

| Field | Value |
|---|---|
| OCL formation valid | YES |
| Retest present | YES — C7 and C8 enter the zone |
| Entry candle itself touches OCL wick | YES — C8 range 193.8–196.0 passes through zone 193.5–195.0 |
| Entry valid | NO |
| Formation quality | 8/10 — solid One Candle, strong momentum and continuation |
| Trade quality | 1/10 — C8 touches the zone but closes bullish at 195.5 above the level. No rejection — price passes through the zone and continues rising. We need a bearish candle that touches the wick and rejects downward. Here the zone does not hold |

**Explanation:** Formation is good. The level exists. But when price arrives,
it does not react — it passes through. The entry candle must show rejection
in the trade direction. Closing on the wrong side of the level is not
rejection.

---

## Summary Table

| # | Dir | Formation valid | Retest | Entry touches wick | Entry valid | Form Q | Trade Q | Case type |
|---|---|---|---|---|---|---|---|---|
| 1 | LONG | YES | YES | YES | YES | 9 | 9 | Perfect |
| 2 | SHORT | YES | YES | YES | YES | 9 | 9 | Perfect mirror |
| 3 | LONG | YES | NO | NO | NO | 9 | N/A | No retest |
| 4 | LONG | UNSURE | YES | YES | UNSURE | 4 | 4 | Weak momentum |
| 5 | SHORT | UNSURE | YES | YES | UNSURE | 6 | 6 | Small wick |
| 6 | LONG | NO | NO | NO | NO | 0 | N/A | Two bearish |
| 7 | LONG | NO | NO | NO | NO | 0 | N/A | No wick |
| 8 | LONG | NO | NO | NO | NO | 0 | N/A | No opposing candle |
| 9 | SHORT | NO | NO | NO | NO | 0 | N/A | Choppy |
| 10 | LONG | YES×2 | N/A | N/A | N/A | 7+7 | N/A | Two OCLs, open question |
| 11 | LONG | UNSURE | NO | NO | NO | 3 | N/A | Weak continuation |
| 12 | SHORT | UNSURE | NO | NO | NO | 3 | N/A | Minimal continuation |
| 13 | LONG | YES | YES | YES | UNSURE | 9 | UNSURE | Late retest |
| 14 | LONG | YES | YES | YES | UNSURE | 8 | 4 | Weak rejection |
| 15 | SHORT | YES | YES | YES | NO | 8 | 1 | Zone violated |
