"""Canonical BDRR displacement detection — Stage 3.

Ported from ``findDisplacement`` in
estrategie/bdrr_engine.js (lines 398–539).

Scans candles strictly after the break candle for displacement bars
(low > level_price) until the first retest contact (low <= level_price).

Key rules matching JS exactly:

    - Breakout candle itself is NEVER a displacement bar.
    - A displacement bar: its low is strictly > level_price (raw float).
    - First retest contact: low <= level_price (equality counts as contact).
    - The retest-contact candle is NOT part of the displacement window.
    - RETEST_BEFORE_DISPLACEMENT: if the first post-break bar already
      touches or crosses the level (zero displacement bars) → FAILED.
    - RETEST_NOT_FOUND: no candle's low ever touches the level within
      the remaining session → FAILED.
    - min_displacement_ticks must be None (disabled) in config.
    - displacement_distance: max(bar.high_ticks - level_ticks) across
      displacement window bars.  Uses tick arithmetic (priceToTicks).
    - max_favorable_high: max(bar.high) across displacement bars (raw float).
    - displacement_window: list of original candle dicts (identity preserved).
    - Defensive chain validation: candles must match both orb and breakResult.

Output (OK):
    status, date, level_price, break_candle_index,
    displacement_start_index, displacement_end_index,
    displacement_bar_count, displacement_window,
    max_favorable_high, displacement_distance {points, ticks},
    first_retest_contact_index, first_retest_contact_candle,
    first_retest_contact_timestamp.

Output (FAILED):
    status, failed_stage, reason, plus additional fields depending
    on the failure type.
"""

from __future__ import annotations

from trading_lab.tick_arithmetic import price_to_ticks, ticks_to_points


# ── Config validation ────────────────────────────────────────────────────────

_REQUIRED_CONFIG_KEYS = (
    "timeframe_minutes", "timezone", "session_open", "orb_start",
    "orb_duration_minutes", "level_source", "direction", "tick_size",
)


def _assert_valid_config(config: object) -> None:
    if not isinstance(config, dict):
        raise TypeError("config must be a dict")
    for key in _REQUIRED_CONFIG_KEYS:
        if key not in config:
            raise TypeError(f"config.{key} is required")


# ── Primary function ─────────────────────────────────────────────────────────


def find_displacement(
    candles: list[dict],
    orb: dict,
    break_result: dict,
    config: dict,
) -> dict:
    """Scan for displacement bars and first retest contact.

    Matches JS ``findDisplacement(candles, orb, breakResult, config)``
    in bdrr_engine.js:398–539.
    """
    _assert_valid_config(config)

    # ── Failed upstream ──────────────────────────────────────────────────
    if not isinstance(orb, dict) or orb.get("status") != "OK":
        fs = "LEVEL_NOT_FOUND"
        rp = ""
        if isinstance(orb, dict):
            fs = orb.get("failed_stage", fs)
            rp = orb.get("reason", "")
        return {
            "status": "FAILED",
            "failed_stage": fs,
            "reason": f"cannot search for displacement: upstream ORB result failed ({rp})",
        }

    if not isinstance(break_result, dict) or break_result.get("status") != "OK":
        fs = "BREAK_NOT_FOUND"
        rp = ""
        if isinstance(break_result, dict):
            fs = break_result.get("failed_stage", fs)
            rp = break_result.get("reason", "")
        return {
            "status": "FAILED",
            "failed_stage": fs,
            "reason": f"cannot search for displacement: upstream break result failed ({rp})",
        }

    # ── Unsupported configuration ────────────────────────────────────────
    direction = config["direction"]
    if direction not in ("LONG", "SHORT"):
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                f'direction "{direction}" is not implemented in '
                f'this stage; only "LONG" and "SHORT" are supported'
            ),
        }

    _supported_sources = ("ORB_HIGH", "ORB_LOW")
    if config["level_source"] not in _supported_sources:
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                f'level_source "{config["level_source"]}" is not implemented '
                f'in this stage; only "ORB_HIGH" and "ORB_LOW" are supported'
            ),
        }

    mdt = config.get("min_displacement_ticks")
    if mdt is not None:
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                "min_displacement_ticks must be disabled (null/undefined) "
                "in this stage; no numeric displacement threshold is implemented"
            ),
        }

    # ── Input validation ─────────────────────────────────────────────────
    if not isinstance(candles, list):
        raise TypeError("candles must be a list")

    # ── Defensive chain validation ───────────────────────────────────────
    orb_idx = orb["orb_candle_index"]
    if (orb_idx >= len(candles) or
            candles[orb_idx]["time_ms"] != orb["orb_candle"]["time_ms"]):
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_INPUT",
            "reason": "candles does not match the array used to build orb",
        }

    brk_idx = break_result["break_candle_index"]
    if (brk_idx >= len(candles) or
            candles[brk_idx]["time_ms"] != break_result["break_candle"]["time_ms"]):
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_INPUT",
            "reason": "candles does not match the array used to build breakResult",
        }

    # ── Scan for displacement / retest contact ───────────────────────────
    level_price = orb["level_price"]
    level_ticks = orb["level_price_ticks"]
    tick_size = config["tick_size"]
    start_index = brk_idx + 1  # breakout candle never counted
    is_short = direction == "SHORT"

    # Find first retest contact
    # LONG: low <= level_price  (price returns toward level from above)
    # SHORT: high >= level_price (price returns toward level from below)
    first_contact_index = None
    for i in range(start_index, len(candles)):
        if is_short:
            if candles[i]["high"] >= level_price:
                first_contact_index = i
                break
        else:
            if candles[i]["low"] <= level_price:
                first_contact_index = i
                break

    # ── RETEST_NOT_FOUND ─────────────────────────────────────────────────
    if first_contact_index is None:
        contact_desc = (
            f"no candle with high >= level_price ({level_price})"
            if is_short
            else f"no candle with low <= level_price ({level_price})"
        )
        return {
            "status": "FAILED",
            "failed_stage": "RETEST_NOT_FOUND",
            "reason": (
                f"{contact_desc} found "
                f"after the break candle at index {brk_idx}; "
                f"displacement window cannot be closed within the provided candles"
            ),
            "date": orb["date"],
            "break_candle_index": brk_idx,
            "displacement_start_index": start_index,
            "displacement_bar_count": len(candles) - start_index,
        }

    displacement_bar_count = first_contact_index - start_index

    # ── Validate displacement bars ───────────────────────────────────────
    # LONG: displacement bar has low > level_price (stays above level)
    # SHORT: displacement bar has high < level_price (stays below level)
    # If first post-break bar already contacts level → no displacement
    if displacement_bar_count > 0:
        for j in range(start_index, first_contact_index):
            if is_short:
                if candles[j]["high"] >= level_price:
                    # This bar contacts level — truncate displacement here
                    first_contact_index = j
                    displacement_bar_count = j - start_index
                    break
            else:
                if candles[j]["low"] <= level_price:
                    first_contact_index = j
                    displacement_bar_count = j - start_index
                    break

    # ── RETEST_BEFORE_DISPLACEMENT ───────────────────────────────────────
    if displacement_bar_count == 0:
        return {
            "status": "FAILED",
            "failed_stage": "RETEST_BEFORE_DISPLACEMENT",
            "reason": (
                "first post-break bar contacted the level; no displacement "
                "phase existed before retest began"
            ),
            "date": orb["date"],
            "break_candle_index": brk_idx,
            "first_retest_contact_index": first_contact_index,
            "first_retest_contact_candle": candles[first_contact_index],
            "first_retest_contact_timestamp": candles[first_contact_index]["time_ms"],
        }

    # ── Minimum displacement bars (configurable, default 1) ──────────────
    min_bars = config.get("min_displacement_bars")
    if min_bars is None:
        min_bars = 1
    if displacement_bar_count < min_bars:
        return {
            "status": "FAILED",
            "failed_stage": "DISPLACEMENT_TOO_SHORT",
            "reason": (
                f"displacement has {displacement_bar_count} bar(s) completely "
                f"outside the level, but min_displacement_bars requires {min_bars}"
            ),
            "date": orb["date"],
            "break_candle_index": brk_idx,
            "displacement_bar_count": displacement_bar_count,
            "first_retest_contact_index": first_contact_index,
        }

    # ── Build displacement window ────────────────────────────────────────
    displacement_window = candles[start_index:first_contact_index]
    displacement_end_index = first_contact_index - 1

    if is_short:
        # SHORT: favorable movement is downward
        min_favorable_low = float("inf")
        max_distance_ticks = -2**63

        for bar in displacement_window:
            if bar["low"] < min_favorable_low:
                min_favorable_low = bar["low"]
            bar_low_ticks = price_to_ticks(bar["low"], tick_size)
            dist = level_ticks - bar_low_ticks
            if dist > max_distance_ticks:
                max_distance_ticks = dist

        return {
            "status": "OK",
            "date": orb["date"],
            "level_price": level_price,
            "break_candle_index": brk_idx,
            "displacement_start_index": start_index,
            "displacement_end_index": displacement_end_index,
            "displacement_bar_count": displacement_bar_count,
            "displacement_window": displacement_window,
            "max_favorable_low": min_favorable_low,
            "displacement_distance": {
                "points": ticks_to_points(max_distance_ticks, tick_size),
                "ticks": max_distance_ticks,
            },
            "first_retest_contact_index": first_contact_index,
            "first_retest_contact_candle": candles[first_contact_index],
            "first_retest_contact_timestamp": candles[first_contact_index]["time_ms"],
        }
    else:
        # LONG: favorable movement is upward
        max_favorable_high = -float("inf")
        max_distance_ticks = -2**63

        for bar in displacement_window:
            if bar["high"] > max_favorable_high:
                max_favorable_high = bar["high"]
            bar_high_ticks = price_to_ticks(bar["high"], tick_size)
            dist = bar_high_ticks - level_ticks
            if dist > max_distance_ticks:
                max_distance_ticks = dist

        return {
            "status": "OK",
            "date": orb["date"],
            "level_price": level_price,
            "break_candle_index": brk_idx,
            "displacement_start_index": start_index,
            "displacement_end_index": displacement_end_index,
            "displacement_bar_count": displacement_bar_count,
            "displacement_window": displacement_window,
            "max_favorable_high": max_favorable_high,
            "displacement_distance": {
                "points": ticks_to_points(max_distance_ticks, tick_size),
                "ticks": max_distance_ticks,
            },
            "first_retest_contact_index": first_contact_index,
            "first_retest_contact_candle": candles[first_contact_index],
            "first_retest_contact_timestamp": candles[first_contact_index]["time_ms"],
        }
