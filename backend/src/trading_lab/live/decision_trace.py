"""Candle-by-candle decision trace for MaxBot live sessions.

Converts the signal detector's pipeline results into a structured,
human-readable trace that explains WHY MaxBot did or did not enter
on each candle.

Architecture: purely read-only.  This module does NOT modify any
detector, threshold, or trading rule.  It reads the results that
the existing pipeline already computes and formats them for display.

Usage:

    trace = build_candle_trace(candle, signal_result, orb_high, orb_low)

The resulting ``CandleDecision`` captures:
- ORB relationship (inside, above high, below low)
- Pipeline stage reached
- Break/displacement/retest status
- Rejection candidate evaluation (PASS/FAIL per predicate)
- Setup identity
- Final reason for no entry
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RejectionDetail:
    """Per-predicate PASS/FAIL for one rejection candidate candle."""
    candle_index: int
    time_ms: int
    close: float
    wick_ratio: float | None = None
    wick_ratio_pass: bool | None = None
    body_ratio: float | None = None
    body_ratio_pass: bool | None = None
    favorable_close: float | None = None
    favorable_close_pass: bool | None = None
    close_beyond_level: float | None = None
    close_beyond_pass: bool | None = None
    body_outside_orb: bool | None = None
    body_outside_pass: bool | None = None
    wick_penetration_pct: float | None = None
    wick_penetration_pass: bool | None = None
    has_rejection_wick: bool | None = None
    has_wick_penetration: bool | None = None
    is_news_candle: bool = False
    failed_rules: tuple[str, ...] = ()
    qualifies: bool = False
    two_candle_attempted: bool = False
    two_candle_failed_rules: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandleDecision:
    """Decision trace for one completed candle."""
    time_ms: int
    time_str: str               # HH:MM display
    symbol: str
    close: float
    orb_state: str              # INSIDE_ORB, ABOVE_ORB_HIGH, BELOW_ORB_LOW
    orb_high: float | None = None
    orb_low: float | None = None
    pipeline_stage: str = ""    # deepest stage reached
    failed_stage: str = ""      # why it stopped
    stage_detail: str = ""      # human-readable one-liner
    break_detected: bool = False
    break_direction: str = ""
    break_time_ms: int = 0
    break_bar_index: int = -1
    displacement_confirmed: bool = False
    displacement_count: int = 0
    displacement_required: int = 3
    retest_detected: bool = False
    retest_window_start: int = -1
    retest_window_end: int = -1
    rejection_evaluated: bool = False
    rejection_detail: RejectionDetail | None = None
    all_failed_retests: tuple[RejectionDetail, ...] = ()
    setup_key: str = ""
    signal_emitted: bool = False


def _orb_state(close: float, high: float, low: float, orb_high: float, orb_low: float) -> str:
    """Classify candle's position relative to ORB."""
    if close > orb_high:
        return "ABOVE_ORB_HIGH"
    elif close < orb_low:
        return "BELOW_ORB_LOW"
    else:
        return "INSIDE_ORB"


def _parse_rejection_detail(rec: dict) -> RejectionDetail:
    """Build RejectionDetail from a rejection finder record."""
    geom = rec.get("geometry", {})
    failed = rec.get("failed_rules", [])
    cnd = rec.get("candle", {})
    two_cnd_failed = rec.get("two_candle_failed_rules", [])

    return RejectionDetail(
        candle_index=rec.get("candle_index", -1),
        time_ms=rec.get("timestamp", cnd.get("time_ms", 0)),
        close=cnd.get("close", 0.0),
        wick_ratio=geom.get("rejection_wick_ratio"),
        wick_ratio_pass="REJECTION_WICK_RATIO_TOO_LOW" not in failed,
        body_ratio=geom.get("body_ratio"),
        body_ratio_pass="BODY_RATIO_TOO_HIGH" not in failed,
        favorable_close=geom.get("favorable_close_location"),
        favorable_close_pass="FAVORABLE_CLOSE_LOCATION_TOO_LOW" not in failed,
        close_beyond_level=geom.get("close_beyond_level_ticks"),
        close_beyond_pass="CLOSE_BEYOND_LEVEL_TOO_LOW" not in failed,
        body_outside_orb=geom.get("body_outside_orb"),
        body_outside_pass="BODY_INSIDE_ORB" not in failed,
        wick_penetration_pct=geom.get("wick_penetration_pct"),
        wick_penetration_pass=(
            "WICK_PENETRATION_PCT_TOO_LOW" not in failed
            and "WICK_NO_PENETRATION" not in failed
        ),
        has_rejection_wick="NO_REJECTION_WICK" not in failed,
        has_wick_penetration="WICK_NO_PENETRATION" not in failed,
        is_news_candle="CANDLE_ATR_EXCEEDS_THRESHOLD" in failed,
        failed_rules=tuple(failed),
        qualifies=rec.get("qualifies", len(failed) == 0),
        two_candle_attempted=len(two_cnd_failed) > 0,
        two_candle_failed_rules=tuple(two_cnd_failed),
    )


def _stage_detail(stage: str, failed: str, ctx: dict) -> str:
    """Build a human-readable one-liner from pipeline stage."""
    if stage == "SIGNAL":
        return "SIGNAL — entry candle accepted"

    if not failed:
        return stage or ""

    # ORB building
    if failed in ("ORB_BUILDING", "ORB_NOT_COMPLETE", "LEVEL_NOT_FOUND"):
        return "building ORB"

    # Break
    if failed == "BREAK_NOT_FOUND":
        return "waiting for break"

    # Displacement
    if failed in ("DISPLACEMENT_NOT_CONFIRMED", "DISPLACEMENT_BUILDING",
                  "DISPLACEMENT_TOO_SHORT"):
        count = ctx.get("displacement_bars", 0)
        req = ctx.get("displacement_required", 3)
        return f"displacement {count}/{req}"

    # Retest too early
    if failed == "RETEST_BEFORE_DISPLACEMENT":
        return "retest too early — displacement not confirmed"

    # Waiting for retest
    if failed == "RETEST_NOT_FOUND":
        count = ctx.get("displacement_bars", "?")
        return f"waiting for retest (disp={count})"

    # Rejection
    if failed == "NO_QUALIFYING_REJECTION_CANDLE":
        return "retest — no qualifying entry candle"

    # Sequence invalidated
    if failed == "SEQUENCE_INVALIDATED":
        return "setup invalidated — price re-entered ORB"

    return failed


def build_candle_trace(
    candle: dict,
    signal_result,
    orb_high: float | None,
    orb_low: float | None,
    symbol: str = "",
    time_str: str = "",
    rejection_data: dict | None = None,
) -> CandleDecision:
    """Build a CandleDecision from a completed candle and its signal result.

    Parameters
    ----------
    candle : dict
        The completed candle {time_ms, open, high, low, close, volume}.
    signal_result : SignalResult
        The result from signal_detector.evaluate() for this bar.
    orb_high, orb_low : float | None
        ORB levels (from stage_context or runtime).
    symbol : str
        Underlying symbol.
    time_str : str
        Display time (HH:MM).
    rejection_data : dict | None
        Raw rejection finder result (contains failed_retests).
    """
    ctx = signal_result.stage_context or {}
    ps = signal_result.pipeline_stage or ""
    fs = signal_result.failed_stage or ""

    # ORB state
    c = candle["close"]
    h = candle["high"]
    l_ = candle["low"]
    oh = orb_high or ctx.get("orb_high")
    ol = orb_low or ctx.get("orb_low")
    orb_st = _orb_state(c, h, l_, oh, ol) if oh and ol else "UNKNOWN"

    # Break info
    break_detected = ctx.get("break_bar_index") is not None
    break_dir = ctx.get("direction", "")
    break_tms = ctx.get("break_time_ms", 0)
    break_idx = ctx.get("break_bar_index", -1)

    # Displacement
    disp_count = ctx.get("displacement_bars", 0)
    disp_req = ctx.get("displacement_required", 3)
    disp_confirmed = (
        disp_count >= disp_req
        if disp_count is not None and disp_req is not None
        else False
    )

    # Retest
    retest_start = ctx.get("retest_start_index", -1) or -1
    retest_end = ctx.get("retest_end_index", -1) or -1
    retest_detected = retest_start >= 0

    # Rejection details
    failed_retests_raw = []
    last_rejection = None
    rejection_evaluated = False

    if rejection_data and isinstance(rejection_data, dict):
        fr = rejection_data.get("failed_retests", [])
        if fr:
            rejection_evaluated = True
            failed_retests_raw = [_parse_rejection_detail(r) for r in fr]
            last_rejection = failed_retests_raw[-1] if failed_retests_raw else None

    # Stage detail
    detail = _stage_detail(ps, fs, ctx)

    return CandleDecision(
        time_ms=candle["time_ms"],
        time_str=time_str,
        symbol=symbol,
        close=c,
        orb_state=orb_st,
        orb_high=oh,
        orb_low=ol,
        pipeline_stage=ps,
        failed_stage=fs,
        stage_detail=detail,
        break_detected=break_detected,
        break_direction=break_dir,
        break_time_ms=break_tms or 0,
        break_bar_index=break_idx if break_idx is not None else -1,
        displacement_confirmed=disp_confirmed,
        displacement_count=disp_count or 0,
        displacement_required=disp_req or 3,
        retest_detected=retest_detected,
        retest_window_start=retest_start,
        retest_window_end=retest_end,
        rejection_evaluated=rejection_evaluated,
        rejection_detail=last_rejection,
        all_failed_retests=tuple(failed_retests_raw),
        setup_key=signal_result.setup_key or "",
        signal_emitted=signal_result.status.value == "SIGNAL" if signal_result.status else False,
    )


def format_trace_line(d: CandleDecision) -> str:
    """Format a CandleDecision as a compact single-line trace for terminal."""
    parts = [f"[{d.symbol}] {d.time_str} C={d.close:.2f}"]
    parts.append(d.orb_state)

    if not d.break_detected:
        parts.append(d.stage_detail)
        return " | ".join(parts)

    parts.append(f"BREAK {d.break_direction}")

    if not d.displacement_confirmed:
        parts.append(f"DISP {d.displacement_count}/{d.displacement_required}")
        parts.append(d.stage_detail)
        return " | ".join(parts)

    parts.append(f"DISP {d.displacement_count} ✓")

    if not d.retest_detected:
        parts.append(d.stage_detail)
        return " | ".join(parts)

    parts.append("RETEST")

    if d.signal_emitted:
        parts.append("SIGNAL ✓")
        return " | ".join(parts)

    if d.rejection_detail:
        rd = d.rejection_detail
        predicates = []
        if rd.wick_ratio_pass is not None:
            predicates.append(f"wick={'✓' if rd.wick_ratio_pass else '✗'}")
        if rd.body_ratio_pass is not None:
            predicates.append(f"body={'✓' if rd.body_ratio_pass else '✗'}")
        if rd.body_outside_pass is not None:
            predicates.append(f"outside={'✓' if rd.body_outside_pass else '✗'}")
        if rd.wick_penetration_pass is not None:
            predicates.append(f"pen={'✓' if rd.wick_penetration_pass else '✗'}")
        if rd.favorable_close_pass is not None:
            predicates.append(f"fclose={'✓' if rd.favorable_close_pass else '✗'}")
        parts.append(" ".join(predicates))
        if rd.failed_rules:
            parts.append(", ".join(rd.failed_rules))
    else:
        parts.append(d.stage_detail)

    return " | ".join(parts)


def trace_to_dict(d: CandleDecision) -> dict:
    """Convert CandleDecision to a JSON-serializable dict for PWA."""
    result = {
        "time_ms": d.time_ms,
        "time": d.time_str,
        "symbol": d.symbol,
        "close": d.close,
        "orb_state": d.orb_state,
        "orb_high": d.orb_high,
        "orb_low": d.orb_low,
        "stage": d.pipeline_stage,
        "failed_stage": d.failed_stage,
        "detail": d.stage_detail,
        "break": {
            "detected": d.break_detected,
            "direction": d.break_direction,
            "time_ms": d.break_time_ms,
        } if d.break_detected else None,
        "displacement": {
            "count": d.displacement_count,
            "required": d.displacement_required,
            "confirmed": d.displacement_confirmed,
        } if d.break_detected else None,
        "retest": {
            "detected": d.retest_detected,
            "window_start": d.retest_window_start,
            "window_end": d.retest_window_end,
        } if d.retest_detected else None,
        "setup_key": d.setup_key or None,
        "signal": d.signal_emitted,
    }

    if d.rejection_detail:
        rd = d.rejection_detail
        result["rejection"] = {
            "time_ms": rd.time_ms,
            "close": rd.close,
            "qualifies": rd.qualifies,
            "failed_rules": list(rd.failed_rules),
            "predicates": {
                "wick_ratio": {"value": rd.wick_ratio, "pass": rd.wick_ratio_pass},
                "body_ratio": {"value": rd.body_ratio, "pass": rd.body_ratio_pass},
                "favorable_close": {"value": rd.favorable_close, "pass": rd.favorable_close_pass},
                "close_beyond_level": {"value": rd.close_beyond_level, "pass": rd.close_beyond_pass},
                "body_outside_orb": {"value": rd.body_outside_orb, "pass": rd.body_outside_pass},
                "wick_penetration": {"value": rd.wick_penetration_pct, "pass": rd.wick_penetration_pass},
            },
            "is_news_candle": rd.is_news_candle,
        }

    if d.all_failed_retests:
        result["failed_retest_count"] = len(d.all_failed_retests)

    return result
