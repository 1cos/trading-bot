"""Shadow metrics for the Weekly Strategy Lab — observation only.

Every audit so far has run into the same wall: the questions are good and
the sample is three sessions. This module exists to fix the sample, not
the strategy. It computes, for each entry candidate the detector meets,
what four different TWO_CANDLE semantics would have said — and records
the geometry each verdict rests on — so that in a month the comparison
can be made on real data instead of on a geometry-only CSV screen.

Nothing here decides anything. There is no import from the live package,
no IO, no state, and no path back into execution: a caller gets a dict
and can only write it down. M1_CURRENT is reported alongside the others
purely so the four can be compared like for like; the trade that is
actually taken still comes from ``find_rejection()``, which this module
never calls and never influences.

The four semantics
------------------
M1_CURRENT
    Today's rule, transcribed from ``rejection_finder.py`` lines 582-648.
    Kept here as a *mirror*, not as a second source of truth —
    ``two_candle_shadow()`` accepts the engine's own verdict and flags a
    disagreement rather than quietly substituting its own.

M2_RECOVERY_BASED
    M1 without the ``open2`` requirement. Candle 2 must still close
    beyond candle 1's far body edge, so the body is genuinely recovered;
    it just no longer has to *open* beyond the near edge. That single
    gate rejects ~91% of pairs which satisfy everything else, and on
    1-minute bars its margin is the tick-level gap between one bar's
    close and the next bar's open — see the module tests for the
    algebra.

M3_LEVEL_BASED
    Pair semantics stated in terms of the level rather than the bodies:
    candle 1 penetrates, the structure is not traversed, candle 2 closes
    clearly beyond the level and beyond candle 1's own close. No
    engulfing requirement at all.

M4_COMBINED_2M
    Candle 1 and candle 2 fused into one synthetic 2-minute bar, judged
    by the SINGLE geometry at its existing thresholds. This is the only
    model that applies geometric gates to a TWO_CANDLE pair — today
    neither candle is checked for wick, body or close location. Entry
    would still be candle 2's close; the merge is for the geometry only.

None of the four introduces a new threshold. M2 and M3 remove or replace
existing gates; M4 reuses ``evaluate_single_candle_rejection_geometry``
exactly as configured.

Structural classification
-------------------------
A pair is labelled from its *shape*, before any outcome is known, so
that the weekly report can ask whether the forms behave differently
instead of discovering the forms in the answer.
"""

from __future__ import annotations

from trading_lab.atr import atr_series
from trading_lab.news_candle import classify_candle_atr
from trading_lab.rejection_finder import (
    evaluate_single_candle_rejection_geometry,
)
from trading_lab.tick_arithmetic import price_to_ticks

SCHEMA_VERSION = "StrategyLab/v1"

MODELS = ("M1_CURRENT", "M2_RECOVERY_BASED", "M3_LEVEL_BASED", "M4_COMBINED_2M")

# Structural classes. Thresholds here describe *shape*, never
# tradability — nothing downstream may gate on them.
CLASS_MICRO_GAP = "MICRO_GAP"
CLASS_TRUE_RETEST = "TRUE_RETEST_RECOVERY"
CLASS_DEEP_PENETRATION = "DEEP_PENETRATION"
CLASS_OTHER = "OTHER"

MICRO_GAP_MAX_MARGIN_TICKS = 2      # verdict rests on the close1->open2 gap
DEEP_PENETRATION_MIN_RANGE_FRACTION = 0.5
TRUE_RETEST_MIN_CLOSE_MARGIN_TICKS = 3

# Report buckets. Kept next to the metrics they slice so the aggregation
# can never drift from the definition.
MARGIN_OPEN2_BUCKETS = ((0, 1, "<=1t"), (2, 2, "2t"), (3, 4, "3-4t"), (5, None, "5+t"))
PENETRATION_BUCKETS = ((1, 5, "1-5t"), (6, 12, "6-12t"), (13, 25, "13-25t"), (26, None, "26+t"))
SINGLE_RANGE_BUCKETS = ((0, 12, "<=12t"), (13, 20, "13-20t"), (21, None, "21+t"))

R_LEVELS = (2.0, 2.5, 3.0, 3.5, 4.0)


def bucket_of(value, buckets):
    """Label `value` against a (lo, hi, label) table. None hi = open end."""
    if value is None:
        return None
    for lo, hi, label in buckets:
        if value >= lo and (hi is None or value <= hi):
            return label
    return None


# ── small helpers ───────────────────────────────────────────────────────────

def _t(price, tick_size):
    return price_to_ticks(price, tick_size)


def _ohlc(candle):
    return (candle["open"], candle["high"], candle["low"], candle["close"])


def _ratio(numerator, denominator):
    """Guarded division — a zero denominator is missing data, not zero."""
    if denominator in (None, 0) or numerator is None:
        return None
    return numerator / denominator


# ── the four semantics ──────────────────────────────────────────────────────

def _m1_current(c1, c2, level_price, far_edge, tick_size, is_short):
    """Mirror of rejection_finder.py:582-648. Rule for rule, order kept."""
    o1, h1, l1, cl1 = (_t(v, tick_size) for v in _ohlc(c1))
    o2, _h2, _l2, cl2 = (_t(v, tick_size) for v in _ohlc(c2))
    lv = _t(level_price, tick_size)
    fe = _t(far_edge, tick_size) if far_edge is not None else None
    body_hi, body_lo = max(o1, cl1), min(o1, cl1)

    failed = []
    if is_short:
        if fe is not None and cl1 > fe:
            failed.append("TWO_CANDLE_BODY_TRAVERSES_ZONE")
        if h1 <= lv:
            failed.append("TWO_CANDLE_NO_LEVEL_PENETRATION")
    else:
        if fe is not None and cl1 < fe:
            failed.append("TWO_CANDLE_BODY_TRAVERSES_ZONE")
        if l1 >= lv:
            failed.append("TWO_CANDLE_NO_LEVEL_PENETRATION")
    if failed:
        return False, failed

    if is_short:
        if cl2 >= o2:
            failed.append("TWO_CANDLE_NOT_BEARISH")
        elif not (o2 > body_hi and cl2 < body_lo):
            failed.append("TWO_CANDLE_ENGULFING_INSUFFICIENT")
        if cl2 >= lv:
            failed.append("TWO_CANDLE_RECOVERY_WRONG_SIDE")
    else:
        if cl2 <= o2:
            failed.append("TWO_CANDLE_NOT_BULLISH")
        elif not (o2 < body_lo and cl2 > body_hi):
            failed.append("TWO_CANDLE_ENGULFING_INSUFFICIENT")
        if cl2 <= lv:
            failed.append("TWO_CANDLE_RECOVERY_WRONG_SIDE")
    return (not failed), failed


def _m2_recovery(c1, c2, level_price, far_edge, tick_size, is_short):
    """M1 minus `open2`. The body must still be fully recovered."""
    _ok, failed = _m1_current(c1, c2, level_price, far_edge, tick_size, is_short)
    failed = [f for f in failed if f != "TWO_CANDLE_ENGULFING_INSUFFICIENT"]

    o1, _h1, _l1, cl1 = (_t(v, tick_size) for v in _ohlc(c1))
    _o2, _h2, _l2, cl2 = (_t(v, tick_size) for v in _ohlc(c2))
    body_hi, body_lo = max(o1, cl1), min(o1, cl1)
    recovered = cl2 < body_lo if is_short else cl2 > body_hi
    if not recovered and "TWO_CANDLE_NOT_BEARISH" not in failed \
            and "TWO_CANDLE_NOT_BULLISH" not in failed:
        failed.append("BODY_NOT_RECOVERED")
    return (not failed), failed


def _m3_level(c1, c2, level_price, far_edge, tick_size, is_short):
    """Level semantics: enter, hold the structure, close clearly out."""
    o1, h1, l1, cl1 = (_t(v, tick_size) for v in _ohlc(c1))
    o2, _h2, _l2, cl2 = (_t(v, tick_size) for v in _ohlc(c2))
    lv = _t(level_price, tick_size)
    fe = _t(far_edge, tick_size) if far_edge is not None else None

    failed = []
    if is_short:
        if fe is not None and cl1 > fe:
            failed.append("TWO_CANDLE_BODY_TRAVERSES_ZONE")
        if h1 <= lv:
            failed.append("TWO_CANDLE_NO_LEVEL_PENETRATION")
        if cl2 >= lv:
            failed.append("TWO_CANDLE_RECOVERY_WRONG_SIDE")
        if cl2 >= cl1:
            failed.append("NO_REJECTION_VS_PENETRATION_CANDLE")
        if cl2 >= o2:
            failed.append("TWO_CANDLE_NOT_BEARISH")
    else:
        if fe is not None and cl1 < fe:
            failed.append("TWO_CANDLE_BODY_TRAVERSES_ZONE")
        if l1 >= lv:
            failed.append("TWO_CANDLE_NO_LEVEL_PENETRATION")
        if cl2 <= lv:
            failed.append("TWO_CANDLE_RECOVERY_WRONG_SIDE")
        if cl2 <= cl1:
            failed.append("NO_REJECTION_VS_PENETRATION_CANDLE")
        if cl2 <= o2:
            failed.append("TWO_CANDLE_NOT_BULLISH")
    return (not failed), failed


def merged_bar(c1, c2):
    """The pair as one synthetic bar. Open from the first, close from the
    second, extremes from both — what a 2-minute chart would draw."""
    return {
        "open": c1["open"],
        "high": max(c1["high"], c2["high"]),
        "low": min(c1["low"], c2["low"]),
        "close": c2["close"],
        "time_ms": c1.get("time_ms"),
    }


def _m4_combined(c1, c2, level_price, _far_edge, tick_size, is_short, **geom_kwargs):
    """SINGLE geometry on the merged bar, thresholds untouched."""
    result = evaluate_single_candle_rejection_geometry(
        merged_bar(c1, c2), "SHORT" if is_short else "LONG",
        level_price, tick_size, **geom_kwargs,
    )
    return result["qualifies"], list(result["failed_rules"]), result["geometry"]


# ── TWO_CANDLE shadow record ────────────────────────────────────────────────

def two_candle_shadow(c1, c2, *, direction, level_price, level_source,
                      far_edge, tick_size, atr=None, engine_verdict=None,
                      geometry_kwargs=None):
    """Four verdicts and the geometry each one rests on, for one pair.

    `engine_verdict` is the answer ``find_rejection()`` actually gave for
    this pair, when the caller knows it. It is never used to decide
    anything — it is compared against the M1 mirror and any disagreement
    is recorded in ``m1_matches_engine``. A mirror that silently drifts
    from the engine would poison every weekly comparison built on it, so
    the drift is made loud instead of being papered over.

    `atr` is the ATR *of the bar before candle 1*, matching what the
    engine's NEWS filter uses. None is fine — the normalised fields
    become None rather than being faked.
    """
    is_short = direction == "SHORT"
    geom_kwargs = dict(geometry_kwargs or {})

    o1, h1, l1, cl1 = (_t(v, tick_size) for v in _ohlc(c1))
    o2, h2, l2, cl2 = (_t(v, tick_size) for v in _ohlc(c2))
    lv = _t(level_price, tick_size)
    body_hi, body_lo = max(o1, cl1), min(o1, cl1)

    pen1 = max(0, (h1 - lv) if is_short else (lv - l1))
    pen2 = max(0, (h2 - lv) if is_short else (lv - l2))
    range1, range2 = h1 - l1, h2 - l2

    # Entry and stop as the live trade plan would build them: entry on
    # candle 2's close, stop at the extreme of the pair.
    entry_price = c2["close"]
    stop_price = max(c1["high"], c2["high"]) if is_short else min(c1["low"], c2["low"])
    stop_distance = abs(entry_price - stop_price)

    margin_open2 = (o2 - body_hi) if is_short else (body_lo - o2)
    margin_close2 = (body_lo - cl2) if is_short else (cl2 - body_hi)
    recovery_beyond_level = (lv - cl2) if is_short else (cl2 - lv)
    gap_close1_open2 = o2 - cl1

    body1_direction = "BEARISH" if cl1 < o1 else ("BULLISH" if cl1 > o1 else "DOJI")

    verdicts = {}
    m1_ok, m1_failed = _m1_current(c1, c2, level_price, far_edge, tick_size, is_short)
    verdicts["M1_CURRENT"] = {"verdict": "PASS" if m1_ok else "FAIL",
                              "failed_rules": m1_failed}
    m2_ok, m2_failed = _m2_recovery(c1, c2, level_price, far_edge, tick_size, is_short)
    verdicts["M2_RECOVERY_BASED"] = {"verdict": "PASS" if m2_ok else "FAIL",
                                     "failed_rules": m2_failed}
    m3_ok, m3_failed = _m3_level(c1, c2, level_price, far_edge, tick_size, is_short)
    verdicts["M3_LEVEL_BASED"] = {"verdict": "PASS" if m3_ok else "FAIL",
                                  "failed_rules": m3_failed}
    m4_ok, m4_failed, m4_geom = _m4_combined(
        c1, c2, level_price, far_edge, tick_size, is_short, **geom_kwargs)
    verdicts["M4_COMBINED_2M"] = {"verdict": "PASS" if m4_ok else "FAIL",
                                  "failed_rules": m4_failed}

    if margin_open2 <= MICRO_GAP_MAX_MARGIN_TICKS:
        classification = CLASS_MICRO_GAP
    elif range1 and pen1 / range1 >= DEEP_PENETRATION_MIN_RANGE_FRACTION:
        classification = CLASS_DEEP_PENETRATION
    elif margin_close2 >= TRUE_RETEST_MIN_CLOSE_MARGIN_TICKS:
        classification = CLASS_TRUE_RETEST
    else:
        classification = CLASS_OTHER

    return {
        "schema_version": SCHEMA_VERSION,
        "pattern": "TWO_CANDLE",
        "direction": direction,
        "level_source": level_source,
        "level_price": level_price,
        "far_edge": far_edge,
        "tick_size": tick_size,
        "candle1": dict(c1),
        "candle2": dict(c2),
        "entry_price": entry_price,
        "stop_price": stop_price,
        "metrics": {
            "penetration_ticks_c1": pen1,
            "penetration_ticks_c2": pen2,
            "penetration_atr_c1": _ratio(pen1 * tick_size, atr),
            "range_ticks_c1": range1,
            "range_ticks_c2": range2,
            "range_atr_c1": _ratio(range1 * tick_size, atr),
            "stop_distance": stop_distance,
            "stop_distance_atr": _ratio(stop_distance, atr),
            "body1_direction": body1_direction,
            "body1_low_ticks": body_lo,
            "body1_high_ticks": body_hi,
            "body1_low": body_lo * tick_size,
            "body1_high": body_hi * tick_size,
            "gap_close1_open2_ticks": gap_close1_open2,
            "margin_open2_ticks": margin_open2,
            "margin_close2_ticks": margin_close2,
            "recovery_beyond_level_ticks": recovery_beyond_level,
            "penetration_over_range_c1": _ratio(pen1, range1),
            "atr": atr,
            "combined_2m": m4_geom,
        },
        "shadow_verdicts": verdicts,
        "structural_classification": classification,
        "buckets": {
            "margin_open2": bucket_of(margin_open2, MARGIN_OPEN2_BUCKETS),
            "penetration_c1": bucket_of(pen1, PENETRATION_BUCKETS),
        },
        "engine_verdict": engine_verdict,
        "m1_matches_engine": (None if engine_verdict is None
                              else (engine_verdict == verdicts["M1_CURRENT"]["verdict"])),
    }


# ── SINGLE shadow record ────────────────────────────────────────────────────

_PERTURBED_FIELDS = ("open", "high", "low", "close")


def flip_analysis(candle, direction, level_price, tick_size,
                  max_ticks=6, **geom_kwargs):
    """How far a single OHLC field must move before the verdict changes.

    Feeds disagree at the tick. A candle that passes by one tick is not
    the same finding as one that passes by six, and until now nothing
    recorded which kind a trade was. Returns the smallest |N| that flips
    the verdict and the rule that broke first at that distance.

    A perturbation is clamped so the bar stays well formed — a low
    pushed above the body becomes the body edge, not an impossible bar.
    """
    base = evaluate_single_candle_rejection_geometry(
        candle, direction, level_price, tick_size, **geom_kwargs)
    baseline = base["qualifies"]

    for n in range(1, max_ticks + 1):
        for field in _PERTURBED_FIELDS:
            for delta in (n, -n):
                probe = dict(candle)
                probe[field] = probe[field] + delta * tick_size
                probe["high"] = max(probe["high"], probe["open"], probe["close"])
                probe["low"] = min(probe["low"], probe["open"], probe["close"])
                result = evaluate_single_candle_rejection_geometry(
                    probe, direction, level_price, tick_size, **geom_kwargs)
                if result["qualifies"] != baseline:
                    broke = [r for r in result["failed_rules"]
                             if r not in base["failed_rules"]]
                    return {
                        "flip_ticks": n,
                        "flip_field": field,
                        "flip_delta_ticks": delta,
                        "first_failing_gate": (broke or result["failed_rules"] or [None])[0],
                        "baseline_qualifies": baseline,
                    }
    return {
        "flip_ticks": None,
        "flip_field": None,
        "flip_delta_ticks": None,
        "first_failing_gate": None,
        "baseline_qualifies": baseline,
    }


def single_shadow(candle, *, direction, level_price, level_source, tick_size,
                  atr=None, stop_price=None, engine_verdict=None,
                  geometry_kwargs=None):
    """Geometry, normalisation and tick-fragility for one SINGLE candle.

    The engine already computes the geometry; what it never recorded is
    how *close* the verdict was. `flip_ticks` and `first_failing_gate`
    are the whole point of this record.
    """
    geom_kwargs = dict(geometry_kwargs or {})
    result = evaluate_single_candle_rejection_geometry(
        candle, direction, level_price, tick_size, **geom_kwargs)
    geom = result["geometry"]
    range_ticks = geom["range_ticks"]
    pen = geom["penetration_through_level_ticks"]

    if stop_price is None:
        stop_price = candle["high"] if direction == "SHORT" else candle["low"]
    stop_distance = abs(candle["close"] - stop_price)

    flip = flip_analysis(candle, direction, level_price, tick_size, **geom_kwargs)
    verdict = "PASS" if result["qualifies"] else "FAIL"

    return {
        "schema_version": SCHEMA_VERSION,
        "pattern": "SINGLE",
        "direction": direction,
        "level_source": level_source,
        "level_price": level_price,
        "tick_size": tick_size,
        "candle": dict(candle),
        "entry_price": candle["close"],
        "stop_price": stop_price,
        "metrics": {
            "range_ticks": range_ticks,
            "range_atr": _ratio(range_ticks * tick_size, atr),
            "penetration_ticks": pen,
            "penetration_atr": _ratio(pen * tick_size, atr),
            "penetration_over_range": _ratio(pen, range_ticks),
            "stop_distance": stop_distance,
            "stop_distance_atr": _ratio(stop_distance, atr),
            "rejection_wick_ratio": geom["rejection_wick_ratio"],
            "body_ratio": geom["body_ratio"],
            "favorable_close_location": geom["favorable_close_location"],
            "wick_penetration_pct": geom.get("wick_penetration_pct"),
            "atr": atr,
            **flip,
        },
        "shadow_verdicts": {"M1_CURRENT": {"verdict": verdict,
                                           "failed_rules": list(result["failed_rules"])}},
        "buckets": {"single_range": bucket_of(range_ticks, SINGLE_RANGE_BUCKETS)},
        "engine_verdict": engine_verdict,
        "m1_matches_engine": (None if engine_verdict is None
                              else (engine_verdict == verdict)),
    }


# ── bar-level scan ──────────────────────────────────────────────────────────

def is_retest_attempt(candle, direction, level_price):
    """The engine's own filter (rejection_finder.py:536-545): a bar that
    never reaches the level is not a retest attempt at all."""
    if direction == "SHORT":
        return candle["high"] >= level_price
    return candle["low"] <= level_price


def scan_pair(candles, index, *, direction, level_price, level_source,
              far_edge, tick_size, timeframe_ms=60_000, atr_cache=None,
              news_threshold=3.0, geometry_kwargs=None):
    """Shadow records for the pair starting at `index`, or None.

    Mirrors the engine's preconditions — retest attempt, consecutiveness,
    NEWS filter, and the fact that a candle passing SINGLE is never
    offered to TWO_CANDLE — so that the candidate population being
    measured is the one the detector actually sees, not a wider set that
    would flatter the alternative models.
    """
    if index < 0 or index + 1 >= len(candles):
        return None
    c1, c2 = candles[index], candles[index + 1]
    if not is_retest_attempt(c1, direction, level_price):
        return None
    if c2.get("time_ms") is not None and c1.get("time_ms") is not None:
        if c2["time_ms"] - c1["time_ms"] != timeframe_ms:
            return None

    if atr_cache is None:
        atr_cache = atr_series(candles, 14)
    prev_atr = atr_cache[index - 1] if index >= 1 else None
    atr_at_c1 = atr_cache[index] if index < len(atr_cache) else None

    if classify_candle_atr(c1, prev_atr, news_threshold=news_threshold
                           ).status.value == "NEWS_CANDLE":
        return None
    if classify_candle_atr(c2, atr_at_c1, news_threshold=news_threshold
                           ).status.value == "NEWS_CANDLE":
        return None

    geom_kwargs = dict(geometry_kwargs or {})
    single = evaluate_single_candle_rejection_geometry(
        c1, direction, level_price, tick_size, **geom_kwargs)
    if single["qualifies"]:
        return None                       # SINGLE has priority in the engine

    if far_edge is None:
        return None                       # line sources have no zone: no TWO_CANDLE

    return two_candle_shadow(
        c1, c2, direction=direction, level_price=level_price,
        level_source=level_source, far_edge=far_edge, tick_size=tick_size,
        atr=prev_atr, geometry_kwargs=geom_kwargs,
    )


# ── forward settlement ──────────────────────────────────────────────────────

def settle_r_outcome(candles, entry_index, entry_price, stop_price, direction,
                     r_levels=R_LEVELS):
    """Walk forward and record how far the trade got before the stop.

    Within a single bar both the stop and a target level can be touched
    and the bar cannot say which came first, so the stop is assumed to
    win. That understates the models uniformly, which is what a
    comparison needs.
    """
    r_distance = abs(entry_price - stop_price)
    if r_distance <= 0:
        return None
    is_short = direction == "SHORT"

    mfe_r = 0.0
    mae_r = 0.0
    reached = {}
    stop_first = False
    stop_index = None

    for i in range(entry_index + 1, len(candles)):
        bar = candles[i]
        favorable = ((entry_price - bar["low"]) if is_short
                     else (bar["high"] - entry_price)) / r_distance
        adverse = ((bar["high"] - entry_price) if is_short
                   else (entry_price - bar["low"])) / r_distance
        mae_r = max(mae_r, adverse)
        stopped = (bar["high"] >= stop_price) if is_short else (bar["low"] <= stop_price)
        if stopped:
            stop_first = True
            stop_index = i
            mae_r = max(mae_r, 1.0)
            break
        mfe_r = max(mfe_r, favorable)
        for level in r_levels:
            key = _r_key(level)
            if key not in reached and favorable >= level:
                reached[key] = bar.get("time_ms", i)

    return {
        "mfe_r": round(mfe_r, 4),
        "mae_r": round(-mae_r, 4),
        "r_reached": {_r_key(level): (_r_key(level) in reached) for level in r_levels},
        "r_first_touch": reached,
        # `reached` only ever collects levels seen before the loop broke
        # on the stop, so a non-empty dict already means "target first".
        "stop_first": stop_first,
        "target_first": bool(reached),
        "stop_index": stop_index,
        "bars_observed": max(0, len(candles) - entry_index - 1),
    }


def _r_key(level):
    return f"{level:g}".replace(".", "_") + "r"
