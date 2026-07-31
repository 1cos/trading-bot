# BDRR Trading Lab — Parameter Roadmap

**Last updated:** 2026-07-30
**Purpose:** Preserve architecture decisions and configurable parameter definitions between sessions.
**Status:** Documentation only. No parameters implemented yet.

---

## Core Principle

The detector identifies **candidate structures** from raw market data.

The Trading Lab / policy layer decides **which candidates are accepted** according to configurable parameters.

Observations from manual review do NOT immediately become frozen detector rules. They become **adjustable Lab parameters** so we can:

- Test different configurations
- Compare results side by side
- Measure which settings are profitable
- Measure which settings best match Max's discretionary decisions

### Architecture

```
Raw market data
      ↓
Candidate detection (frozen BDRR engine)
      ↓
Candidate list (all structural matches)
      ↓
Configurable policy / Lab filters
      ↓
Filtered trade plan
      ↓
Outcome evaluation
      ↓
Metrics and comparison
```

The detector exposes enough candidate information for the Lab to test different policies without rewriting detection logic each time.

---

## 1. Session and Trade Frequency

| Parameter | Type | Example Values | Description |
|---|---|---|---|
| `max_setups_per_session` | int | 1, 2, 3, unlimited | How many setups can be taken in a single trading day |
| `allow_new_sequence_after_invalidation` | bool | true / false | After a sequence is invalidated, can a new break start a new sequence? |
| `allow_new_sequence_after_completed_trade` | bool | true / false | After a trade exits (win or loss), can a new setup be taken? |
| `cooldown_bars_after_trade` | int | 0, 5, 10 | Minimum bars to wait after a trade exit before accepting a new setup |
| `earliest_entry_time` | time | 09:35, 09:45, 10:00 | No entries before this time |
| `latest_entry_time` | time | 15:00, 15:30, 15:45 | No entries after this time |
| `minimum_minutes_after_market_open` | int | 5, 15, 30 | Minimum elapsed minutes after 09:30 before accepting entries |
| `maximum_minutes_after_market_open` | int | 60, 120, 360 | Maximum elapsed minutes — no entries after this window |
| `maximum_trades_per_day` | int | 1, 2, 3, unlimited | Hard daily cap |
| `stop_trading_after_first_loss` | bool | true / false | Stop all trading for the session after the first losing trade |
| `daily_max_drawdown_limit` | float | -1R, -2R, -3R | Stop trading when cumulative daily loss reaches this threshold |

**Important:** The system must not permanently assume only one setup per session. One, two, or more must be selectable and testable.

**Current detector limitation:** The detector currently finds at most one setup per session. After invalidation, it stops. This must be restructured to support `max_setups_per_session > 1`.

---

## 2. Displacement

| Parameter | Type | Example Values | Description |
|---|---|---|---|
| `min_displacement_bars` | int | 0, 1, 2, 3 | Minimum number of bars fully beyond the ORB level |
| `min_displacement_distance_ticks` | int | 0, 5, 10, 20 | Minimum distance the displacement reaches from the level |
| `min_displacement_distance_pct` | float | 0%, 0.1%, 0.2% | Same as above expressed as percentage of price |
| `require_full_candle_outside_orb` | bool | true / false | Must the displacement candle's entire range be outside the ORB? |
| `require_body_outside_orb` | bool | true / false | Must the candle body (open-close) be fully outside? |
| `allow_immediate_retest` | bool | true / false | Allow a retest with zero displacement bars |
| `maximum_bars_before_retest` | int | 5, 10, 20, unlimited | If no retest occurs within N bars, invalidate |
| `minimum_visible_gap_from_orb` | int | 0, 2, 5 ticks | Minimum visible space between ORB line and displacement candles |

### Max's discretionary note

> A true displacement is not simply a close beyond the ORB. Max generally expects **visible separation** between the ORB line and subsequent candles. Ideally, one or more candles should open and close fully outside the level, creating visible space between price and the ORB. However, this definition is still under review and must not yet be frozen.

**Current detector limitation:** The detector requires at least one displacement bar. The `RETEST_BEFORE_DISPLACEMENT` failure stage rejects setups where the break is followed immediately by a retest with no clean displacement bar. TSLA 2026-07-28 SHORT was rejected for this reason despite Max considering it a valid setup.

---

## 3. Retest

| Parameter | Type | Example Values | Description |
|---|---|---|---|
| `min_wick_depth_ticks` | int | 0, 2, 5, 10 | Minimum wick penetration into the ORB zone |
| `max_wick_depth_ticks` | int | 20, 50, 100, unlimited | Maximum wick penetration (too deep = failed structure) |
| `min_wick_depth_pct` | float | 0%, 0.05%, 0.1% | Minimum penetration as percentage |
| `require_wick_to_enter_orb_zone` | bool | true / false | Must the wick physically enter the ORB band? |
| `allow_orb_touch_without_penetration` | bool | true / false | Is touching the ORB line (without crossing) sufficient? |
| `max_body_distance_from_orb_ticks` | int | 10, 20, 50, unlimited | Maximum distance between the body and the ORB level |
| `max_body_distance_from_orb_pct` | float | 0.1%, 0.2%, 0.5% | Same expressed as percentage |
| `require_body_close_outside_orb` | bool | true / false | Must the confirmation body close outside the ORB? |
| `maximum_retest_window_bars` | int | 10, 20, 50, unlimited | Maximum bars after displacement to wait for a retest |

### Review observations

Several reviewed setups were rejected manually because:

- The wick only touched the ORB line without entering
- The wick did not penetrate deep enough into the ORB zone
- The confirmation body was too far from the ORB line

These observations should become measurable parameters, not immediate frozen rules.

---

## 4. Confirmation

| Parameter | Type | Example Values | Description |
|---|---|---|---|
| `confirmation_max_delay_bars` | int | 5, 10, 20, unlimited | Maximum bars after break before confirmation must occur |
| `require_engulfing_confirmation` | bool | true / false | Must the confirmation candle engulf the previous candle? |
| `engulfing_mode` | enum | required / optional / ignored | How strictly to enforce engulfing |
| `require_break_of_previous_candle` | bool | true / false | Must the confirmation candle break the high/low of the prior candle? |
| `entry_mode` | enum | confirmation_close / confirmation_hl_break / next_candle_open / limit_retest | How the entry is triggered |
| `minimum_confirmation_body_ticks` | int | 0, 5, 10 | Minimum body size of the confirmation candle |
| `minimum_confirmation_body_pct` | float | 0%, 0.05%, 0.1% | Same as percentage |
| `maximum_confirmation_range_ticks` | int | 20, 50, 100, unlimited | Maximum range of the confirmation candle |
| `body_to_wick_ratio` | float | 0.3, 0.5, 0.7 | Minimum ratio of body to total range |

### Review observation

Max often identified a later candle as the real confirmation, even when the detector had selected an earlier candle. Therefore confirmation timing and confirmation quality must be testable.

---

## 5. Market Context

| Parameter | Type | Example Values | Description |
|---|---|---|---|
| `spy_alignment_mode` | enum | required / optional / ignored | Whether SPY direction must agree |
| `qqq_alignment_mode` | enum | required / optional / ignored | Whether QQQ direction must agree |
| `require_both_market_indexes_aligned` | bool | true / false | Both SPY and QQQ must agree, not just one |
| `previous_day_direction_filter` | enum | any / bullish_only / bearish_only / neutral_only | Filter by previous day classification |
| `previous_day_classification_filter` | list | bullish, bearish, range | Accepted previous-day classifications |
| `opening_gap_min` | float | -1%, 0%, 0.1% | Minimum opening gap percentage |
| `opening_gap_max` | float | 0.5%, 1%, 2% | Maximum opening gap percentage |
| `premarket_filter` | enum | any / aligned / ignored | Premarket direction filter (requires premarket data) |
| `order_block_confluence_mode` | enum | required / optional / ignored | Order Block confluence requirement |

**Order Block detection is not implemented yet. Do not implement now. Preserved for future phase.**

---

## 6. Risk and Exit

| Parameter | Type | Example Values | Description |
|---|---|---|---|
| `stop_mode` | enum | beyond_confirmation_wick / beyond_retest_structure / fixed_ticks / atr_based | How the stop is placed |
| `stop_buffer_ticks` | int | 0, 1, 2, 5 | Extra ticks beyond the stop level |
| `target_r_multiple` | float | 1.5, 2.0, 3.0, 4.0 | Target as multiple of risk |
| `partial_profit_targets` | list | [1R: 50%, 2R: 50%] | Partial exit levels |
| `break_even_trigger_r` | float | 1.0, 1.5 | Move stop to break-even after reaching this R |
| `trailing_stop_mode` | enum | none / bar_by_bar / swing / atr | Trailing stop strategy |
| `time_based_exit` | bool | true / false | Exit after N bars regardless of P&L |
| `force_exit_before_market_close` | bool | true / false | Force flat before session end |
| `latest_trade_exit_time` | time | 15:45, 15:55, 16:00 | Latest time a trade can remain open |

---

## First Implementation Priority

After the audit phase, these 12 parameters should be implemented first:

| Priority | Parameter | Section |
|---|---|---|
| 1 | `max_setups_per_session` | Session |
| 2 | `allow_new_sequence_after_invalidation` | Session |
| 3 | `earliest_entry_time` | Session |
| 4 | `latest_entry_time` | Session |
| 5 | `minimum_minutes_after_market_open` | Session |
| 6 | `min_displacement_bars` | Structure |
| 7 | `min_displacement_distance_ticks` | Structure |
| 8 | `allow_immediate_retest` | Structure |
| 9 | `min_wick_depth_ticks` | Confirmation |
| 10 | `max_body_distance_from_orb_ticks` | Confirmation |
| 11 | `confirmation_max_delay_bars` | Confirmation |
| 12 | `require_engulfing_confirmation` | Confirmation |

---

## UI Organization (Future)

The Trading Lab sidebar should eventually organize parameters into four main sections:

1. **Session** — trade frequency, time filters, daily limits
2. **Structure** — displacement, retest geometry
3. **Confirmation** — entry model, engulfing, body/wick ratios
4. **Risk & Exit** — stop placement, targets, trailing

Market Context may be either a fifth section or a subsection inside Structure.

Do not redesign the UI now. Only preserve the intended organization.

---

## Current Next Step

The immediate next task remains the **Detector Audit Batch**:

1. Expose selected **rejected candidates** alongside valid ones
2. Review why each was rejected
3. Only after reviewing those rejected candidates should the project decide which parameters to implement first
4. Only after that should any frozen detector rule be considered for restructuring

---

## Review History

| Date | Action |
|---|---|
| 2026-07-30 | Document created. All parameters documented. No implementation. |
