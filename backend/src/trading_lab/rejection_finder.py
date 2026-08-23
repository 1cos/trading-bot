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

from trading_lab.atr import atr_series
from trading_lab.news_candle import classify_candle_atr
from trading_lab.tick_arithmetic import price_to_ticks, ticks_to_points


# ── Constants ─────────────────────────────────────────────────────────────────

REJECTION_WICK_RATIO_MIN = 0.47
BODY_RATIO_MAX = 0.40
FAVORABLE_CLOSE_LOCATION_MIN = 0.80


# ── Max Entry Candle / SINGLE_CANDLE_REJECTION geometry (extracted) ───────────
#
# This is the exact geometry logic that lived as the nested
# evaluate_geometry() closure inside find_rejection(). It is extracted
# here, unmodified rule-for-rule, as a standalone reusable pure
# function so it can be called outside find_rejection()'s full
# break/displacement/retest chain (e.g. for evaluating a single
# candidate candle directly). find_rejection() itself now calls this
# function for its SINGLE_CANDLE_REJECTION path instead of a nested
# closure — no rule, threshold, or tick-handling behavior changed.


def evaluate_single_candle_rejection_geometry(
    candle: dict,
    direction: str,
    level_price: float,
    tick_size: float,
    *,
    rejection_wick_ratio_min: float = REJECTION_WICK_RATIO_MIN,
    body_ratio_max: float = BODY_RATIO_MAX,
    favorable_close_location_min: float = FAVORABLE_CLOSE_LOCATION_MIN,
    min_close_beyond_level_ticks: int | None = None,
    confirmation_wick_penetration_pct_min: float = 0.20,
) -> dict:
    """Evaluate Max Entry Candle / SINGLE_CANDLE_REJECTION geometry for
    one candle against one level. Pure function — no side effects, no
    upstream break/displacement/retest chain required.

    This is identical, rule-for-rule, to the geometry previously
    computed only inside find_rejection()'s nested evaluate_geometry()
    closure: same wick penetration, body geometry, close position,
    direction handling, tick handling, thresholds, and pass/fail
    result. No new parameter changes strategic behavior — the keyword
    parameters here simply surface the exact same values find_rejection()
    already resolved from config (defaulting to the same frozen
    constants) so the function is callable standalone.

    Parameters
    ----------
    candle : dict
        A single candle (open/high/low/close/time_ms).
    direction : str
        "LONG" or "SHORT".
    level_price : float
        The level being evaluated against (raw price).
    tick_size : float
        Instrument tick size.
    rejection_wick_ratio_min, body_ratio_max, favorable_close_location_min :
        Same three qualification thresholds find_rejection() already
        uses (defaults are the same frozen module constants).
    min_close_beyond_level_ticks : int | None
        Same optional gate find_rejection() already supports (default
        None — disabled).
    confirmation_wick_penetration_pct_min : float
        Same wick-penetration-into-level-zone gate find_rejection()
        already uses (default 0.20, matching today's default).

    Returns
    -------
    dict
        {"geometry": {...}, "failed_rules": [...], "qualifies": bool}
        — identical shape to what evaluate_geometry() previously
        returned.
    """
    is_short = direction == "SHORT"
    level_ticks = price_to_ticks(level_price, tick_size)

    high_ticks = price_to_ticks(candle["high"], tick_size)
    low_ticks = price_to_ticks(candle["low"], tick_size)
    open_ticks = price_to_ticks(candle["open"], tick_size)
    close_ticks = price_to_ticks(candle["close"], tick_size)

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
    if rejection_wick_ratio < rejection_wick_ratio_min:
        failed_rules.append("REJECTION_WICK_RATIO_TOO_LOW")
    if body_ratio > body_ratio_max:
        failed_rules.append("BODY_RATIO_TOO_HIGH")
    if favorable_close_location < favorable_close_location_min:
        failed_rules.append("FAVORABLE_CLOSE_LOCATION_TOO_LOW")

    # Close-beyond-level gate
    if min_close_beyond_level_ticks is not None:
        if close_beyond_level_ticks < min_close_beyond_level_ticks:
            failed_rules.append("CLOSE_BEYOND_LEVEL_TOO_LOW")

    # ── Body-outside-ORB gate ────────────────────────────────────────
    # LONG: both open and close must be >= level (open allowed on level)
    # SHORT: both open and close must be <= level (open allowed on level)
    if is_short:
        body_outside = (open_ticks <= level_ticks and close_ticks < level_ticks)
    else:
        body_outside = (open_ticks >= level_ticks and close_ticks > level_ticks)

    # ── Wick penetration percentage gate ─────────────────────────────
    # Measures what fraction of the rejection wick is inside the ORB.
    if is_short:
        wick_pen_ticks = max(0, high_ticks - level_ticks)
        rej_wick_for_pen = rejection_wick_ticks
    else:
        wick_pen_ticks = max(0, level_ticks - low_ticks)
        rej_wick_for_pen = rejection_wick_ticks

    if rej_wick_for_pen > 0 and wick_pen_ticks > 0:
        wick_penetration_pct = wick_pen_ticks / rej_wick_for_pen
    else:
        wick_penetration_pct = 0.0

    if confirmation_wick_penetration_pct_min > 0:
        if not body_outside:
            failed_rules.append("BODY_INSIDE_ORB")
        if rej_wick_for_pen <= 0:
            failed_rules.append("NO_REJECTION_WICK")
        elif wick_pen_ticks <= 0:
            failed_rules.append("WICK_NO_PENETRATION")
        elif wick_penetration_pct < confirmation_wick_penetration_pct_min:
            failed_rules.append("WICK_PENETRATION_PCT_TOO_LOW")

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
            "body_outside_orb": body_outside,
            "wick_penetration_pct": round(wick_penetration_pct, 4),
        },
        "failed_rules": failed_rules,
        "qualifies": len(failed_rules) == 0,
    }


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
    *,
    _atr_cache: list[float | None] | None = None,
) -> dict:
    """Scan retest window for the first qualifying rejection candle.

    Matches JS ``findRejection(candles, orb, breakResult,
    displacementResult, retestResult, config)`` in bdrr_engine.js:674–917.

    Parameters
    ----------
    _atr_cache : list[float | None] or None
        Pre-computed ``atr_series(candles, 14)`` result, optionally
        including warm-up from a prior session.  When provided, the
        internal ``atr_series`` call is skipped and this cache is used
        directly.  Must have the same length as ``candles``.
        When ``None`` (default), ATR is computed from ``candles`` alone
        (backward-compatible; first 13 indices have no ATR at 5m).
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

    from trading_lab.level_provider import IMPLEMENTED_SOURCES
    if config["level_source"] not in IMPLEMENTED_SOURCES:
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                f'level_source "{config["level_source"]}" is not implemented; '
                f"supported: {sorted(IMPLEMENTED_SOURCES)}"
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

    # Wick penetration percentage: how much of the rejection wick must
    # enter inside the ORB zone.  Default 0.20 (20%).
    wick_pen_min = config.get("confirmation_wick_penetration_pct_min")
    if wick_pen_min is None:
        wick_pen_min = 0.20
    else:
        wick_pen_min = float(wick_pen_min)
    if not (0 <= wick_pen_min <= 1):
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                f"confirmation_wick_penetration_pct_min must be between "
                f"0 and 1; got {wick_pen_min}"
            ),
        }

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

    # ── ATR cache (O(n), computed once) ─────────────────────────────────
    # Used by the News Candle filter (spec §9).  previous_atr for candle
    # i is atr_cache[i-1]; candle i is never included in its own ATR.
    # When _atr_cache is provided (warm-up path), use it directly.
    # Otherwise compute from session candles alone (legacy path).
    news_threshold = config.get("news_threshold", 3.0)
    if _atr_cache is not None:
        if len(_atr_cache) != len(candles):
            raise ValueError(
                f"_atr_cache length ({len(_atr_cache)}) must match "
                f"candles length ({len(candles)})"
            )
        atr_cache = _atr_cache
    else:
        atr_cache = atr_series(candles, 14)

    # ── Zone edges for TWO_CANDLE (spec §5, STRUCTURAL-ZONE RECOVERY) ──
    # TWO_CANDLE requires near_edge and far_edge.  Only ORB sources
    # provide both today.  For line sources (PDH/PDL) far_edge is None
    # and TWO_CANDLE is not evaluated.
    has_zone_edges = ("orb_high" in orb and "orb_low" in orb)
    if has_zone_edges:
        if is_short:
            # SHORT: near_edge = orb_low, far_edge = orb_high
            zone_far_edge = orb["orb_high"]
            zone_far_edge_ticks = price_to_ticks(zone_far_edge, tick_size)
        else:
            # LONG: near_edge = orb_high, far_edge = orb_low
            zone_far_edge = orb["orb_low"]
            zone_far_edge_ticks = price_to_ticks(zone_far_edge, tick_size)

    timeframe_ms = config["timeframe_minutes"] * 60 * 1000

    def _classify_atr(idx: int, cnd: dict) -> object:
        """Classify a candle's ATR status.  Returns CandleAtrClassification."""
        prev_atr_val = atr_cache[idx - 1] if idx >= 1 else None
        return classify_candle_atr(
            cnd, prev_atr_val, news_threshold=news_threshold,
        )

    def _inject_atr(result: dict, atr_class: object) -> bool:
        """Inject ATR metadata and return True if NEWS_CANDLE."""
        result["geometry"]["candle_atr_status"] = atr_class.status.value
        result["geometry"]["candle_atr_ratio"] = atr_class.ratio
        result["geometry"]["candle_atr_previous"] = atr_class.previous_atr
        result["geometry"]["candle_atr_threshold"] = atr_class.news_threshold
        return atr_class.status.value == "NEWS_CANDLE"

    def _build_ok(
        idx, cnd, result, *,
        entry_pattern_type="SINGLE_CANDLE_REJECTION",
        pair_stop_basis_ticks=None,
        penetration_candle_index=None,
        penetration_candle_geometry=None,
    ):
        if is_short:
            wick_depth = max(0, price_to_ticks(cnd["high"], tick_size) - level_ticks)
        else:
            wick_depth = max(0, level_ticks - price_to_ticks(cnd["low"], tick_size))
        ok = {
            "status": "OK",
            "date": orb["date"],
            "level_price": level_price,
            "confirmation_candle_index": idx,
            "confirmation_candle": cnd,
            "confirmation_timestamp": cnd["time_ms"],
            "geometry": result["geometry"],
            "wick_depth_ticks": wick_depth,
            "failed_retests": failed_retests,
            "failed_retest_count": len(failed_retests),
            "entry_pattern_type": entry_pattern_type,
        }
        if pair_stop_basis_ticks is not None:
            ok["pair_stop_basis_ticks"] = pair_stop_basis_ticks
        if penetration_candle_index is not None:
            ok["penetration_candle_index"] = penetration_candle_index
        if penetration_candle_geometry is not None:
            ok["penetration_candle_geometry"] = penetration_candle_geometry
        return ok

    # ── Scan retest window ───────────────────────────────────────────────
    failed_retests: list[dict] = []
    window_start = retest_result["retest_window_start_index"]
    window_end = retest_result["retest_window_end_index"]

    i = window_start
    while i <= window_end:
        cnd = candles[i]

        # Retest attempt filter
        if is_short:
            if cnd["high"] < level_price:
                i += 1
                continue
        else:
            if cnd["low"] > level_price:
                i += 1
                continue

        result = evaluate_single_candle_rejection_geometry(
            cnd, direction, level_price, tick_size,
            rejection_wick_ratio_min=wick_min,
            body_ratio_max=body_max,
            min_close_beyond_level_ticks=min_close_beyond,
            confirmation_wick_penetration_pct_min=wick_pen_min,
        )
        atr_class = _classify_atr(i, cnd)
        is_nc = _inject_atr(result, atr_class)

        if is_nc:
            result["failed_rules"].append("CANDLE_ATR_EXCEEDS_THRESHOLD")
            result["qualifies"] = False

        # ── SINGLE_CANDLE_REJECTION (priority) ───────────────────────
        if result["qualifies"]:
            return _build_ok(i, cnd, result)

        # ── TWO_CANDLE_ENGULFING_RECOVERY ────────────────────────────
        # Only when zone edges exist and first candle is not NEWS_CANDLE
        tc_failed_rules: list[str] = []
        tc_attempted = False

        if has_zone_edges and not is_nc:
            j = i + 1
            if j <= window_end:
                cnd2 = candles[j]

                # Consecutiveness: timestamp exactly one timeframe apart
                ts_diff = cnd2["time_ms"] - cnd["time_ms"]
                if ts_diff != timeframe_ms:
                    tc_failed_rules.append("TWO_CANDLE_NOT_CONSECUTIVE")
                else:
                    tc_attempted = True

                    # First candle body-traversal check (close-based)
                    close1_ticks = price_to_ticks(cnd["close"], tick_size)
                    open1_ticks = price_to_ticks(cnd["open"], tick_size)
                    high1_ticks = price_to_ticks(cnd["high"], tick_size)
                    low1_ticks = price_to_ticks(cnd["low"], tick_size)
                    if is_short:
                        # SHORT: close must not be above far_edge (orb_high)
                        if close1_ticks > zone_far_edge_ticks:
                            tc_failed_rules.append("TWO_CANDLE_BODY_TRAVERSES_ZONE")
                        # Candle #1 must show real interaction with the near
                        # edge (level): its high must actually penetrate
                        # beyond the level, not merely touch it exactly
                        # (high1 == level is zero penetration, not rejection).
                        if high1_ticks <= level_ticks:
                            tc_failed_rules.append("TWO_CANDLE_NO_LEVEL_PENETRATION")
                    else:
                        # LONG: close must not be below far_edge (orb_low)
                        if close1_ticks < zone_far_edge_ticks:
                            tc_failed_rules.append("TWO_CANDLE_BODY_TRAVERSES_ZONE")
                        # Candle #1 must show real interaction with the near
                        # edge (level): its low must actually penetrate
                        # beyond the level, not merely touch it exactly
                        # (low1 == level is zero penetration, not rejection).
                        if low1_ticks >= level_ticks:
                            tc_failed_rules.append("TWO_CANDLE_NO_LEVEL_PENETRATION")

                    if not tc_failed_rules:
                        # Second candle evaluation
                        open2_ticks = price_to_ticks(cnd2["open"], tick_size)
                        close2_ticks = price_to_ticks(cnd2["close"], tick_size)

                        body1_high = max(open1_ticks, close1_ticks)
                        body1_low = min(open1_ticks, close1_ticks)

                        if is_short:
                            # Bearish
                            if close2_ticks >= open2_ticks:
                                tc_failed_rules.append("TWO_CANDLE_NOT_BEARISH")
                            # Engulfs body — STRICT: candle #2 must extend
                            # beyond both body edges of candle #1.  Exact
                            # equality (open2 == body1_high or
                            # close2 == body1_low) is NOT engulfing.
                            if not (open2_ticks > body1_high and close2_ticks < body1_low):
                                if "TWO_CANDLE_NOT_BEARISH" not in tc_failed_rules:
                                    tc_failed_rules.append("TWO_CANDLE_ENGULFING_INSUFFICIENT")
                            # Recovery: close below near_edge
                            if close2_ticks >= level_ticks:
                                tc_failed_rules.append("TWO_CANDLE_RECOVERY_WRONG_SIDE")
                        else:
                            # Bullish
                            if close2_ticks <= open2_ticks:
                                tc_failed_rules.append("TWO_CANDLE_NOT_BULLISH")
                            # Engulfs body — STRICT: candle #2 must extend
                            # beyond both body edges of candle #1.  Exact
                            # equality (open2 == body1_low or
                            # close2 == body1_high) is NOT engulfing.
                            if not (open2_ticks < body1_low and close2_ticks > body1_high):
                                if "TWO_CANDLE_NOT_BULLISH" not in tc_failed_rules:
                                    tc_failed_rules.append("TWO_CANDLE_ENGULFING_INSUFFICIENT")
                            # Recovery: close above near_edge
                            if close2_ticks <= level_ticks:
                                tc_failed_rules.append("TWO_CANDLE_RECOVERY_WRONG_SIDE")

                        # ATR check on second candle
                        if not tc_failed_rules:
                            atr_class2 = _classify_atr(j, cnd2)
                            if atr_class2.status.value == "NEWS_CANDLE":
                                tc_failed_rules.append("TWO_CANDLE_SECOND_ATR_EXCEEDS")

                    # ── TWO_CANDLE qualifies? ────────────────────────
                    if tc_attempted and not tc_failed_rules:
                        # Build geometry for second candle
                        result2 = evaluate_single_candle_rejection_geometry(
                            cnd2, direction, level_price, tick_size,
                            rejection_wick_ratio_min=wick_min,
                            body_ratio_max=body_max,
                            min_close_beyond_level_ticks=min_close_beyond,
                            confirmation_wick_penetration_pct_min=wick_pen_min,
                        )
                        atr_class2_final = _classify_atr(j, cnd2)
                        _inject_atr(result2, atr_class2_final)

                        # Stop basis: extreme of the pair
                        low1_ticks = price_to_ticks(cnd["low"], tick_size)
                        low2_ticks = price_to_ticks(cnd2["low"], tick_size)
                        high1_ticks = price_to_ticks(cnd["high"], tick_size)
                        high2_ticks = price_to_ticks(cnd2["high"], tick_size)
                        if is_short:
                            pair_stop = max(high1_ticks, high2_ticks)
                        else:
                            pair_stop = min(low1_ticks, low2_ticks)

                        return _build_ok(
                            j, cnd2, result2,
                            entry_pattern_type="TWO_CANDLE_ENGULFING_RECOVERY",
                            pair_stop_basis_ticks=pair_stop,
                            penetration_candle_index=i,
                            penetration_candle_geometry=result["geometry"],
                        )

        # ── Record failed retest ─────────────────────────────────────
        failed_record = {
            "candle_index": i,
            "candle": cnd,
            "timestamp": cnd["time_ms"],
            "geometry": result["geometry"],
            "failed_rules": result["failed_rules"],
        }
        if tc_failed_rules:
            failed_record["two_candle_failed_rules"] = tc_failed_rules
        failed_retests.append(failed_record)

        i += 1

    return {
        "status": "FAILED",
        "failed_stage": "NO_QUALIFYING_REJECTION_CANDLE",
        "reason": (
            "no retest-attempt candle satisfied rejection geometry or "
            "two-candle engulfing recovery within the retest window "
            f"(indices {window_start}-{window_end})"
        ),
        "failed_retests": failed_retests,
        "failed_retest_count": len(failed_retests),
    }
