"""Canonical BDRR rejection qualification — Stage 5.

Ported from ``findRejection`` in
estrategie/bdrr_engine.js (lines 674–917).

Scans the retest window chronologically.  Only candles whose
low <= level_price are retest attempts and may qualify; candles
whose low is above level_price are skipped entirely (they are not
retest attempts and never appear in failed_retests).  The first
attempt candle whose geometry satisfies all three thresholds is
returned as the confirmation candle and scanning stops immediately.

Qualification (LONG, all three required):
    rejection_wick_ratio   >= 0.47
    body_ratio             <= 0.40
    favorable_close_location >= 0.80

Penetration and close-beyond-level are computed and reported but never
gate qualification (except when ``min_close_beyond_level_ticks`` is set
in config).  A zero-range candle (range_ticks == 0) cannot qualify; its
ratio fields are None and its only failure reason is ZERO_RANGE_CANDLE.

Key rules matching JS exactly:

    - Defensive chain: validates candles match orb, breakResult,
      displacementResult, AND retestResult via timestamp at index.
    - min_penetration_ticks must be null/None (unsupported in stage 5).
    - min_close_beyond_level_ticks: when non-None, close must be at
      least that many ticks above level (LONG).

Output (OK):
    status, date, level_price, confirmation_candle_index,
    confirmation_candle, confirmation_timestamp, geometry,
    failed_retests, failed_retest_count.
"""

from __future__ import annotations

from trading_lab.tick_arithmetic import price_to_ticks, ticks_to_points


# ── Constants ─────────────────────────────────────────────────────────────────

REJECTION_WICK_RATIO_MIN = 0.47
BODY_RATIO_MAX = 0.40
FAVORABLE_CLOSE_LOCATION_MIN = 0.80


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
            f"cannot search for rejection: upstream {name} result "
            f"failed ({rp})"
        ),
    }


# ── Primary function ─────────────────────────────────────────────────────────


def find_rejection(
    candles: list[dict],
    orb: dict,
    break_result: dict,
    displacement_result: dict,
    retest_result: dict,
    config: dict,
) -> dict:
    """Scan retest window for the first qualifying rejection candle.

    Matches JS ``findRejection(candles, orb, breakResult,
    displacementResult, retestResult, config)`` in bdrr_engine.js:674–917.
    """
    _assert_valid_config(config)

    # ── Failed upstream ──────────────────────────────────────────────────
    if not isinstance(orb, dict) or orb.get("status") != "OK":
        return _upstream_fail("ORB", orb, "LEVEL_NOT_FOUND")

    if not isinstance(break_result, dict) or break_result.get("status") != "OK":
        return _upstream_fail("break", break_result, "BREAK_NOT_FOUND")

    if not isinstance(displacement_result, dict) or displacement_result.get("status") != "OK":
        return _upstream_fail("displacement", displacement_result, "RETEST_NOT_FOUND")

    if not isinstance(retest_result, dict) or retest_result.get("status") != "OK":
        return _upstream_fail("retest window", retest_result, "RETEST_NOT_FOUND")

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

    min_pen = config.get("min_penetration_ticks")
    if min_pen is not None:
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                "min_penetration_ticks must be disabled (null/undefined) "
                "in this stage; penetration is reported but never gated"
            ),
        }

    min_close_beyond = config.get("min_close_beyond_level_ticks")
    if min_close_beyond is not None:
        if (not isinstance(min_close_beyond, int)
                or min_close_beyond < 0):
            return {
                "status": "FAILED",
                "failed_stage": "UNSUPPORTED_CONFIGURATION",
                "reason": (
                    f"min_close_beyond_level_ticks must be a non-negative "
                    f"integer or null; got {min_close_beyond}"
                ),
            }

    # Configurable rejection thresholds (default to frozen constants)
    wick_min = config.get("rejection_wick_ratio_min")
    if wick_min is None:
        wick_min = REJECTION_WICK_RATIO_MIN
    body_max = config.get("body_ratio_max")
    if body_max is None:
        body_max = BODY_RATIO_MAX

    # ── Input validation ─────────────────────────────────────────────────
    if not isinstance(candles, list):
        raise TypeError("candles must be a list")

    # ── Defensive chain validation ───────────────────────────────────────
    orb_idx = orb["orb_candle_index"]
    if (orb_idx >= len(candles)
            or candles[orb_idx]["time_ms"] != orb["orb_candle"]["time_ms"]):
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_INPUT",
            "reason": "candles does not match the array used to build orb",
        }

    brk_idx = break_result["break_candle_index"]
    if (brk_idx >= len(candles)
            or candles[brk_idx]["time_ms"] != break_result["break_candle"]["time_ms"]):
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_INPUT",
            "reason": "candles does not match the array used to build breakResult",
        }

    contact_idx = displacement_result["first_retest_contact_index"]
    if (contact_idx >= len(candles)
            or candles[contact_idx]["time_ms"]
            != displacement_result["first_retest_contact_candle"]["time_ms"]):
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_INPUT",
            "reason": "candles does not match the array used to build displacementResult",
        }

    retest_start_idx = retest_result["retest_window_start_index"]
    if (retest_start_idx >= len(candles)
            or candles[retest_start_idx]["time_ms"]
            != retest_result["retest_start_timestamp"]):
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_INPUT",
            "reason": "candles does not match the array used to build retestResult",
        }

    # ── Geometry evaluation ──────────────────────────────────────────────
    level_price = orb["level_price"]
    level_ticks = orb["level_price_ticks"]
    tick_size = config["tick_size"]
    is_short = direction == "SHORT"

    def evaluate_geometry(cnd: dict) -> dict:
        high_ticks = price_to_ticks(cnd["high"], tick_size)
        low_ticks = price_to_ticks(cnd["low"], tick_size)
        open_ticks = price_to_ticks(cnd["open"], tick_size)
        close_ticks = price_to_ticks(cnd["close"], tick_size)

        range_ticks = high_ticks - low_ticks

        if is_short:
            # SHORT: penetration is how far above level, close_beyond is
            # how far below level the close is (positive = favorable)
            penetration_ticks = max(0, high_ticks - level_ticks)
            close_beyond_level_ticks = level_ticks - close_ticks
        else:
            # LONG: penetration is how far below level
            penetration_ticks = max(0, level_ticks - low_ticks)
            close_beyond_level_ticks = close_ticks - level_ticks

        if range_ticks == 0:
            return {
                "geometry": {
                    "range_ticks": 0,
                    "body_ticks": 0,
                    "rejection_wick_ticks": 0,
                    "opposite_wick_ticks": 0,
                    "rejection_wick_ratio": None,
                    "body_ratio": None,
                    "favorable_close_location": None,
                    "opposite_wick_ratio": None,
                    "penetration_through_level_ticks": penetration_ticks,
                    "penetration_through_level_points": ticks_to_points(
                        penetration_ticks, tick_size
                    ),
                    "close_beyond_level_ticks": close_beyond_level_ticks,
                    "close_beyond_level_points": ticks_to_points(
                        close_beyond_level_ticks, tick_size
                    ),
                },
                "failed_rules": ["ZERO_RANGE_CANDLE"],
                "qualifies": False,
            }

        body_ticks = abs(close_ticks - open_ticks)

        if is_short:
            # SHORT rejection wick: upper wick that reaches into level
            # rejection_wick = high - max(open, close)
            rejection_wick_ticks = high_ticks - max(open_ticks, close_ticks)
            # opposite wick = lower wick
            opposite_wick_ticks = min(open_ticks, close_ticks) - low_ticks
            # favorable close location: close near the low (bearish)
            # = (high - close) / range
            favorable_close_location = (high_ticks - close_ticks) / range_ticks
        else:
            # LONG rejection wick: lower wick that reaches into level
            rejection_wick_ticks = min(open_ticks, close_ticks) - low_ticks
            opposite_wick_ticks = high_ticks - max(open_ticks, close_ticks)
            favorable_close_location = (close_ticks - low_ticks) / range_ticks

        rejection_wick_ratio = rejection_wick_ticks / range_ticks
        body_ratio = body_ticks / range_ticks
        opposite_wick_ratio = opposite_wick_ticks / range_ticks

        failed_rules: list[str] = []
        if rejection_wick_ratio < wick_min:
            failed_rules.append("REJECTION_WICK_RATIO_TOO_LOW")
        if body_ratio > body_max:
            failed_rules.append("BODY_RATIO_TOO_HIGH")
        if favorable_close_location < FAVORABLE_CLOSE_LOCATION_MIN:
            failed_rules.append("FAVORABLE_CLOSE_LOCATION_TOO_LOW")

        # Close-beyond-level gate
        if min_close_beyond is not None:
            if close_beyond_level_ticks < min_close_beyond:
                failed_rules.append("CLOSE_BEYOND_LEVEL_TOO_LOW")

        return {
            "geometry": {
                "range_ticks": range_ticks,
                "body_ticks": body_ticks,
                "rejection_wick_ticks": rejection_wick_ticks,
                "opposite_wick_ticks": opposite_wick_ticks,
                "rejection_wick_ratio": rejection_wick_ratio,
                "body_ratio": body_ratio,
                "favorable_close_location": favorable_close_location,
                "opposite_wick_ratio": opposite_wick_ratio,
                "penetration_through_level_ticks": penetration_ticks,
                "penetration_through_level_points": ticks_to_points(
                    penetration_ticks, tick_size
                ),
                "close_beyond_level_ticks": close_beyond_level_ticks,
                "close_beyond_level_points": ticks_to_points(
                    close_beyond_level_ticks, tick_size
                ),
            },
            "failed_rules": failed_rules,
            "qualifies": len(failed_rules) == 0,
        }

    # ── Scan retest window ───────────────────────────────────────────────
    failed_retests: list[dict] = []
    window_start = retest_result["retest_window_start_index"]
    window_end = retest_result["retest_window_end_index"]

    for i in range(window_start, window_end + 1):
        cnd = candles[i]
        # LONG: only candles with low <= level are retest attempts
        # SHORT: only candles with high >= level are retest attempts
        if is_short:
            if cnd["high"] < level_price:
                continue  # not a retest attempt
        else:
            if cnd["low"] > level_price:
                continue  # not a retest attempt

        result = evaluate_geometry(cnd)

        if result["qualifies"]:
            # Record wick depth: how far the wick penetrated the ORB boundary
            if is_short:
                wick_depth = max(0, price_to_ticks(cnd["high"], tick_size) - level_ticks)
            else:
                wick_depth = max(0, level_ticks - price_to_ticks(cnd["low"], tick_size))

            return {
                "status": "OK",
                "date": orb["date"],
                "level_price": level_price,
                "confirmation_candle_index": i,
                "confirmation_candle": cnd,
                "confirmation_timestamp": cnd["time_ms"],
                "geometry": result["geometry"],
                "wick_depth_ticks": wick_depth,
                "failed_retests": failed_retests,
                "failed_retest_count": len(failed_retests),
            }

        failed_retests.append({
            "candle_index": i,
            "candle": cnd,
            "timestamp": cnd["time_ms"],
            "geometry": result["geometry"],
            "failed_rules": result["failed_rules"],
        })

    return {
        "status": "FAILED",
        "failed_stage": "NO_QUALIFYING_REJECTION_CANDLE",
        "reason": (
            "no retest-attempt candle satisfied all three rejection "
            "geometry thresholds within the retest window "
            f"(indices {window_start}-{window_end})"
        ),
        "failed_retests": failed_retests,
        "failed_retest_count": len(failed_retests),
    }
