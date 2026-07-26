"""Canonical BDRR retest window construction — Stage 4.

Ported from ``findRetestWindow`` in
estrategie/bdrr_engine.js (lines 566–672).

Begins at the first retest contact from Stage 3 and scans forward to
the last available candle, collecting all bars as the retest window and
marking bars whose low <= level_price as retest contacts with per-contact
metrics.

Key rules matching JS exactly:

    - Window starts at displacement_result["first_retest_contact_index"]
      (that candle IS included and IS the first retest contact).
    - Window ends at candles[-1] (last available candle).
    - ALL candles in the range are part of retest_window, regardless
      of whether they are contacts.
    - A retest contact: candle.low <= level_price (equality counts,
      raw float comparison).
    - Per-contact metrics (all in tick arithmetic):
        closest_directional_position_ticks: lowTicks - levelTicks (signed)
        penetration_through_level_ticks: max(0, levelTicks - lowTicks)
        penetration_through_level_points: ticksToPoints of above
        displacement_retracement_pct: penetration / displacement_distance
            (null if displacement_distance is 0)

    - Does NOT: apply rejection geometry, select confirmation candle,
      cap failed retests, cap setup age, use min_penetration_ticks,
      use min_close_beyond_level_ticks.  All that is Stage 5.

    - Defensive chain: validates candles match orb, breakResult, and
      displacementResult via timestamp at index.

Output (OK):
    status, date, level_price, retest_start_index, retest_start_timestamp,
    retest_window_start_index, retest_window_end_index, retest_window,
    retest_contacts, retest_contact_count.
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


def _upstream_fail(name: str, result: object, default_stage: str) -> dict:
    fs = default_stage
    rp = ""
    if isinstance(result, dict):
        fs = result.get("failed_stage", fs)
        rp = result.get("reason", "")
    return {
        "status": "FAILED",
        "failed_stage": fs,
        "reason": (
            f"cannot search for retest window: upstream {name} result "
            f"failed ({rp})"
        ),
    }


# ── Primary function ─────────────────────────────────────────────────────────


def find_retest_window(
    candles: list[dict],
    orb: dict,
    break_result: dict,
    displacement_result: dict,
    config: dict,
) -> dict:
    """Construct the retest window with per-contact metrics.

    Matches JS ``findRetestWindow(candles, orb, breakResult,
    displacementResult, config)`` in bdrr_engine.js:566–672.
    """
    _assert_valid_config(config)

    # ── Failed upstream ──────────────────────────────────────────────────
    if not isinstance(orb, dict) or orb.get("status") != "OK":
        return _upstream_fail("ORB", orb, "LEVEL_NOT_FOUND")

    if not isinstance(break_result, dict) or break_result.get("status") != "OK":
        return _upstream_fail("break", break_result, "BREAK_NOT_FOUND")

    if not isinstance(displacement_result, dict) or displacement_result.get("status") != "OK":
        return _upstream_fail("displacement", displacement_result, "RETEST_NOT_FOUND")

    # ── Unsupported configuration ────────────────────────────────────────
    if config["direction"] != "LONG":
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                f'direction "{config["direction"]}" is not implemented in '
                f'this stage; only "LONG" is supported'
            ),
        }

    if config["level_source"] != "ORB_HIGH":
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                f'level_source "{config["level_source"]}" is not implemented '
                f'in this stage; only "ORB_HIGH" is supported'
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

    contact_idx = displacement_result["first_retest_contact_index"]
    if (contact_idx >= len(candles) or
            candles[contact_idx]["time_ms"] !=
            displacement_result["first_retest_contact_candle"]["time_ms"]):
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_INPUT",
            "reason": "candles does not match the array used to build displacementResult",
        }

    # ── Build retest window ──────────────────────────────────────────────
    level_price = orb["level_price"]
    level_ticks = orb["level_price_ticks"]
    tick_size = config["tick_size"]
    retest_start_index = contact_idx
    window_end_index = len(candles) - 1
    displacement_ticks = displacement_result["displacement_distance"]["ticks"]

    retest_window = candles[retest_start_index: window_end_index + 1]
    retest_contacts: list[dict] = []

    for i in range(retest_start_index, window_end_index + 1):
        c = candles[i]
        if c["low"] <= level_price:
            low_ticks = price_to_ticks(c["low"], tick_size)
            cdp_ticks = low_ticks - level_ticks
            pen_ticks = max(0, level_ticks - low_ticks)
            pen_points = ticks_to_points(pen_ticks, tick_size)
            retrace_pct = (
                None if displacement_ticks == 0
                else pen_ticks / displacement_ticks
            )
            retest_contacts.append({
                "candle_index": i,
                "candle": c,
                "timestamp": c["time_ms"],
                "closest_directional_position_ticks": cdp_ticks,
                "penetration_through_level_ticks": pen_ticks,
                "penetration_through_level_points": pen_points,
                "displacement_retracement_pct": retrace_pct,
            })

    return {
        "status": "OK",
        "date": orb["date"],
        "level_price": level_price,
        "retest_start_index": retest_start_index,
        "retest_start_timestamp": candles[retest_start_index]["time_ms"],
        "retest_window_start_index": retest_start_index,
        "retest_window_end_index": window_end_index,
        "retest_window": retest_window,
        "retest_contacts": retest_contacts,
        "retest_contact_count": len(retest_contacts),
    }
