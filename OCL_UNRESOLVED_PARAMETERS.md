# OCL Unresolved Parameters Checklist

> **Date:** 2026-08-02 — **Status:** Discovery — no solutions proposed

Every parameter below must be resolved through labeled examples before the
OCL detector can be implemented. This checklist does not propose thresholds
or answers. It only catalogs what needs to be decided and why.

---

## 1. OCL Formation

| Parameter | Description | Why it matters | Status |
|---|---|---|---|
| `opposing_candle_count` | Must be exactly one | Defines the core pattern. Two or more opposing candles disqualify. | **Frozen: exactly 1** |
| `opposing_candle_color` | Bearish for LONG, bullish for SHORT | Determines which candle is the One Candle. | **Frozen** |
| `wick_direction` | Upper wick for LONG, lower wick for SHORT | Without the wick there is no zone. | **Frozen** |
| `wick_zone_definition` | LONG: open→high. SHORT: low→open | Defines the tradeable level. | **Frozen** |
| `doji_handling` | How to classify a candle where open ≈ close | A doji inside a trend could be mistaken for an opposing candle or skipped entirely. Neither rule exists yet. | Unresolved |
| `gap_handling` | Whether a gap between the opposing candle and its neighbors affects validity | A gap could change the wick zone geometry or indicate a different market condition. | Unresolved |

## 2. Momentum — Before the OCL

| Parameter | Description | Why it matters | Status |
|---|---|---|---|
| `min_trend_candles_before` | Minimum directional candles before the One Candle | Too few candles may not constitute "clear, fast momentum." | Unresolved |
| `momentum_distance_before` | Minimum price distance covered by the trend before the OCL | Small-range trends may not produce meaningful levels. | Unresolved |
| `momentum_measurement` | How to measure momentum (consecutive candles, ATR multiple, price range, candle body ratio, or other) | Different methods may identify different structures as valid. | Unresolved |
| `interruption_tolerance` | Whether a single small same-direction candle (weak but not opposing) breaks the momentum count | A tiny bullish candle in a LONG run is not opposing but may signal weakening. | Unresolved |

## 3. Continuation — After the OCL

| Parameter | Description | Why it matters | Status |
|---|---|---|---|
| `continuation_required` | Trend must resume after the One Candle | Without continuation, the One Candle may be the start of a reversal, not a pause. | **Frozen: yes** |
| `min_trend_candles_after` | Minimum directional candles after the One Candle | Determines when to confirm the OCL is valid. | Unresolved |
| `continuation_distance` | Minimum price distance covered after the OCL | A single small candle after may not be real continuation. | Unresolved |
| `max_delay_before_continuation` | Maximum bars allowed between the One Candle and the first continuation candle | If continuation is delayed, the structure may be a consolidation rather than a pause. | Unresolved |

## 4. Wick Requirements

| Parameter | Description | Why it matters | Status |
|---|---|---|---|
| `wick_must_exist` | The wick pointing in trend direction must be present (high > open for LONG bearish, low < open for SHORT bullish) | No wick = no zone = no level. | **Frozen: yes** |
| `min_wick_size` | Minimum absolute or relative size of the wick | A wick of 0.01 points technically exists but may not define a meaningful zone. | Unresolved |
| `wick_to_body_ratio` | Whether the wick must be some proportion of the candle body | Could distinguish between a meaningful wick and noise. | Unresolved |
| `wick_relative_to_atr` | Whether wick size should be measured against ATR or average candle range | Absolute sizes mean different things on a $5 stock vs a $500 stock. | Unresolved |

## 5. Retest

| Parameter | Description | Why it matters | Status |
|---|---|---|---|
| `min_move_away` | Minimum distance price must travel away from the OCL zone before a return qualifies as a retest | If price never leaves the zone, a "retest" is just continuation, not a return. | Unresolved |
| `min_bars_away` | Minimum bars between the OCL formation and the retest | Prevents the very next bar from being both continuation and retest. | Unresolved |
| `max_ocl_age` | Maximum bars or time elapsed before the OCL level expires | Old levels may lose relevance as market context changes. | Unresolved |
| `second_retest_allowed` | Whether the level can generate a second entry after the first retest | A level that holds once might hold again, or it might be consumed. | Unresolved |
| `touch_definition` | What constitutes "touching" the zone (wick enters, body enters, close enters) | The Entry Candle Engine needs a precise touch rule. | Unresolved |

## 6. Entry Candle

| Parameter | Description | Why it matters | Status |
|---|---|---|---|
| `entry_is_retest` | The entry/rejection candle itself performs the retest | Retest and rejection are one candle, not two. | **Frozen** |
| `entry_price` | Close of the entry candle | Standard entry model. | **Frozen** |
| `rejection_geometry` | Exact rules for what constitutes a valid rejection (body size, wick ratio, close position relative to zone) | Without this, "rejects at the level" is subjective. | Unresolved |
| `min_rejection_body` | Minimum body size of the entry candle | A tiny body may signal indecision rather than rejection. | Unresolved |
| `close_position_rule` | How far above (LONG) or below (SHORT) the zone the close must be | Closing barely outside the zone may not be convincing. | Unresolved |
| `wrong_color_rejection` | Whether an entry candle of the "wrong" color (e.g., bearish candle at a SUPPORT level) with a strong wick could still qualify | Some rejection candles close against their wick direction. | Unresolved |

## 7. Stop Placement

| Parameter | Description | Why it matters | Status |
|---|---|---|---|
| `stop_reference_modes` | `ENTRY_CANDLE` (below/above entry candle) or `FULL_ZONE` (below/above entire OCL zone) | Determines risk per trade. | **Frozen: both preserved for testing** |
| `stop_mode_selection` | Which mode to use by default, or whether both are tested in parallel | Cannot backtest without choosing at least one. | Unresolved |
| `stop_buffer` | Whether a fixed or ATR-based buffer is added beyond the stop reference | Exact stop placement without buffer may cause premature exits on noise. | Unresolved |

## 8. Target

| Parameter | Description | Why it matters | Status |
|---|---|---|---|
| `target_multiple` | R-multiple for the initial target | Determines reward expectation. | **Frozen: 2R** |
| `partial_exit` | Whether partial exits at 1R or other levels are tested | Could change the effective win rate and expectancy. | Unresolved |
| `trailing_stop` | Whether a trailing stop replaces or supplements the fixed target | Affects how much of a move is captured. | Unresolved |

## 9. Multiple OCL Handling

| Parameter | Description | Why it matters | Status |
|---|---|---|---|
| `multiple_active_levels` | Whether more than one OCL level can be active simultaneously in the same trend | Two valid OCLs in one move create ambiguity about which to trade. | Unresolved |
| `supersede_rule` | Whether a newer OCL invalidates an older one | Affects how many levels the system tracks at once. | Unresolved |
| `opposite_direction_coexistence` | Whether a LONG OCL and a SHORT OCL can coexist from different trend legs | Mixed-direction levels could signal conflicting conditions. | Unresolved |

## 10. Context and Confluence

| Parameter | Description | Why it matters | Status |
|---|---|---|---|
| `session_time_filter` | Whether OCLs are valid only during certain hours (e.g., regular session only, exclude first/last N minutes) | Market behavior differs at open, midday, and close. | Unresolved |
| `confluence_with_other_levels` | Whether OCL proximity to ORB, PDH/PDL, PMH/PML affects validity or quality | Confluence may strengthen or weaken the level. | Unresolved |
| `volume_filter` | Whether the One Candle or surrounding candles must meet a volume threshold | Low-volume candles may produce unreliable levels. | Unresolved |
| `atr_filter` | Whether session ATR or recent volatility affects OCL validity | The same wick size means different things in different volatility regimes. | Unresolved |
| `trend_context` | Whether the broader trend (higher timeframe or prior sessions) must align with the OCL direction | A LONG OCL inside a multi-day downtrend may behave differently. | Unresolved |

---

## Summary

| Category | Frozen | Unresolved |
|---|---|---|
| OCL Formation | 4 | 2 |
| Momentum Before | 0 | 4 |
| Continuation After | 1 | 3 |
| Wick Requirements | 1 | 3 |
| Retest | 0 | 5 |
| Entry Candle | 2 | 4 |
| Stop Placement | 1 | 2 |
| Target | 1 | 2 |
| Multiple OCL Handling | 0 | 3 |
| Context / Confluence | 0 | 5 |
| **Total** | **10** | **33** |

33 unresolved parameters. All must be informed by labeled discovery
examples before implementation.
