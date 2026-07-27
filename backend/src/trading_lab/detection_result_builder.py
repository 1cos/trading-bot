"""Canonical DetectionResult/v1 schema adapter.

Ported from ``buildDetectionResult`` in
estrategie/bdrr_detection_result.js (lines 253–587).

Converts Stage 1–5 runtime plain-dict outputs into a canonical
``DetectionResult`` contract instance.

Status mapping:
    Runtime 'OK'     → canonical 'VALID'
    Runtime 'FAILED' → canonical 'INVALID'

This is a pure schema adapter. It does NOT run detection logic,
score the result, apply policy, build a TradePlan, or simulate
an outcome.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.distances import (
    AbsoluteTickDistance,
    DirectionalTickDistance,
)
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
    LevelSource,
    Stage,
    ValueType,
)
from trading_lab.contracts.primitives import PriceTicks, Rational
from trading_lab.contracts.rule_failure import RejectionAttempt, RuleFailure
from trading_lab.contracts.session_metadata import SessionMetadata
from trading_lab.tick_arithmetic import decimals_of, price_to_ticks


# ── Failure wrapper (mirrors JS fail()) ──────────────────────────────────────

def _fail(code: str, reason: str) -> dict:
    return {"status": "FAILED", "failure_code": code, "reason": reason}


# ── Metadata validation (matches JS validateMetadata exactly) ────────────────

def _validate_metadata(metadata: object) -> dict | None:
    if not isinstance(metadata, dict):
        return _fail("INVALID_METADATA", "metadata must be a non-null object")

    ts = metadata.get("tick_size")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return _fail("INVALID_METADATA",
                     "metadata.tick_size must be a finite positive number")
    if not math.isfinite(float(ts)) or float(ts) <= 0:
        return _fail("INVALID_METADATA",
                     "metadata.tick_size must be a finite positive number")

    pid = metadata.get("preset_id")
    if not isinstance(pid, str) or len(pid) == 0:
        return _fail("INVALID_METADATA",
                     "metadata.preset_id must be a non-empty string")

    ev = metadata.get("engine_version")
    if not isinstance(ev, str) or len(ev) == 0:
        return _fail("INVALID_METADATA",
                     "metadata.engine_version must be a non-empty string")

    s = metadata.get("session")
    if not isinstance(s, dict):
        return _fail("INVALID_METADATA",
                     "metadata.session must be a non-null object")

    import re
    _session_checks: list[tuple[str, object]] = [
        ("symbol", lambda v: isinstance(v, str) and len(v) > 0),
        ("date", lambda v: isinstance(v, str) and bool(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", v))),
        ("market_timezone", lambda v: isinstance(v, str) and len(v) > 0),
        ("session_open_utc_ms",
         lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
         and math.isfinite(float(v))),
        ("session_close_utc_ms",
         lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
         and math.isfinite(float(v))),
        ("timeframe_seconds",
         lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0),
    ]
    for field, test in _session_checks:
        if not test(s.get(field)):
            return _fail("INVALID_METADATA",
                         f"metadata.session.{field} is missing or invalid")

    return None


# ── Stage outputs validation (matches JS validateStageOutputs) ───────────────

def _validate_stage_outputs(so: object) -> dict | None:
    if not isinstance(so, dict):
        return _fail("INVALID_STAGE_OUTPUTS",
                     "stageOutputs must be a non-null object")

    rej = so.get("rej_result")
    if not isinstance(rej, dict):
        return _fail("INVALID_STAGE_OUTPUTS",
                     "stageOutputs.rejResult is required "
                     "(output of findRejection())")

    status = rej.get("status")
    if status not in ("OK", "FAILED"):
        return _fail("INVALID_STAGE_OUTPUTS",
                     f"stageOutputs.rejResult.status must be 'OK' or "
                     f"'FAILED'; got '{status}'")

    return None


# ── Candle-to-Bar conversion (matches JS buildBar in detection_result.js) ────

def _build_bar(candle: dict | None, tick_size: float) -> Bar | None:
    if not isinstance(candle, dict):
        return None

    # Python raw candles use time_ms; JS candles use .time (Date or number)
    bar_utc_ms = candle.get("time_ms")
    if bar_utc_ms is None:
        # JS fallback: candle.time
        t = candle.get("time")
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            bar_utc_ms = int(t)
        else:
            bar_utc_ms = None

    ts_str = str(tick_size)
    return Bar(
        bar_utc_ms=bar_utc_ms,
        open=PriceTicks(
            ticks=price_to_ticks(candle["open"], tick_size),
            tick_size=ts_str),
        high=PriceTicks(
            ticks=price_to_ticks(candle["high"], tick_size),
            tick_size=ts_str),
        low=PriceTicks(
            ticks=price_to_ticks(candle["low"], tick_size),
            tick_size=ts_str),
        close=PriceTicks(
            ticks=price_to_ticks(candle["close"], tick_size),
            tick_size=ts_str),
        volume=candle.get("volume"),
    )


# ── floatToRational (matches JS exactly, including Math.round semantics) ─────

def _float_to_rational(value: object) -> Rational | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if not math.isfinite(float(value)):
        return None
    denom = 1000000
    # JS Math.round: round half toward +∞ → math.floor(x + 0.5)
    num = int(math.floor(float(value) * denom + 0.5))
    return Rational(numerator=num, denominator=denom)


# ── Primary function ─────────────────────────────────────────────────────────


def build_detection_result(
    stage_outputs: dict,
    metadata: dict,
    *,
    id_factory=None,
) -> dict:
    """Convert Stage 1–5 runtime outputs into a canonical DetectionResult.

    Matches JS ``buildDetectionResult(stageOutputs, metadata)`` in
    bdrr_detection_result.js:253–587.

    Parameters
    ----------
    stage_outputs : dict
        Keys: ``orb``, ``break_result``, ``disp_result``,
        ``retest_result``, ``rej_result`` (all optional except
        ``rej_result``).
    metadata : dict
        Keys: ``tick_size``, ``preset_id``, ``engine_version``,
        ``session`` (dict with SessionMetadata fields).
    id_factory : callable, optional
        If provided, called as ``id_factory(identity_type, fields)``
        to generate ``result_id``.  Default: random UUID v4.

    Returns
    -------
    dict
        ``{"status": "OK", "detection_result": DetectionResult(...)}``
        or ``{"status": "FAILED", "failure_code": ..., "reason": ...}``.
    """

    # ── Step 1: validate inputs ──────────────────────────────────────────
    so_err = _validate_stage_outputs(stage_outputs)
    if so_err:
        return so_err

    md_err = _validate_metadata(metadata)
    if md_err:
        return md_err

    tick_size = float(metadata["tick_size"])
    ts_str = str(tick_size)

    rej = stage_outputs["rej_result"]
    orb = stage_outputs.get("orb") or None
    brk = stage_outputs.get("break_result") or None
    disp = stage_outputs.get("disp_result") or None
    retest = stage_outputs.get("retest_result") or None

    # ── Step 2: map runtime status ───────────────────────────────────────
    is_valid = rej["status"] == "OK"
    canonical_status = (DetectionStatus.VALID if is_valid
                        else DetectionStatus.INVALID)

    # ── Step 3: failed_stage and failed_rules ────────────────────────────
    failed_stage: FailedStage | None = None
    if not is_valid:
        fs_str = rej.get("failed_stage")
        if fs_str:
            try:
                failed_stage = FailedStage(fs_str)
            except ValueError:
                # Non-detection codes (e.g. UNSUPPORTED_CONFIGURATION)
                # passed through; but Python enum won't accept them.
                # For non-standard codes, we set None.
                failed_stage = None

    failed_rules: tuple[RuleFailure, ...] = ()

    # ── Step 4: level fields ─────────────────────────────────────────────
    raw_level = (rej.get("level_price")
                 if rej.get("level_price") is not None
                 else (orb.get("level_price")
                       if isinstance(orb, dict) and orb.get("status") == "OK"
                       else None))

    level_price: PriceTicks | None = None
    if raw_level is not None:
        level_price = PriceTicks(
            ticks=price_to_ticks(raw_level, tick_size),
            tick_size=ts_str)

    level_source: LevelSource | None = None
    level_bar: Bar | None = None
    direction_val: Direction | None = None

    if isinstance(orb, dict) and orb.get("status") == "OK":
        ls = orb.get("level_source")
        if ls:
            try:
                level_source = LevelSource(ls)
            except ValueError:
                level_source = None
        level_bar = _build_bar(orb.get("orb_candle"), tick_size)
        d = orb.get("direction")
        if d:
            try:
                direction_val = Direction(d)
            except ValueError:
                direction_val = None

    # ── Step 5: break fields ─────────────────────────────────────────────
    break_bar: Bar | None = None
    dir_break_dist: DirectionalTickDistance | None = None

    if isinstance(brk, dict) and brk.get("status") == "OK":
        break_bar = _build_bar(brk.get("break_candle"), tick_size)
        dbd = brk.get("directional_break_distance")
        if isinstance(dbd, dict) and "ticks" in dbd:
            dir_break_dist = DirectionalTickDistance(
                ticks=dbd["ticks"], tick_size=ts_str)

    # ── Step 6: displacement fields ──────────────────────────────────────
    displacement_window_bars: list[Bar] = []
    displacement_bar_count: int | None = None
    displacement_pts: AbsoluteTickDistance | None = None
    displacement_pct: Rational | None = None
    rsc_by_bar: tuple[DirectionalTickDistance, ...] | None = None
    min_rsc: DirectionalTickDistance | None = None
    avg_rsc: str | None = None

    if isinstance(disp, dict) and disp.get("status") == "OK":
        dbc = disp.get("displacement_bar_count")
        displacement_bar_count = dbc if dbc is not None else None

        dw = disp.get("displacement_window")
        if isinstance(dw, list):
            displacement_window_bars = [
                _build_bar(c, tick_size) for c in dw
            ]

        dd = disp.get("displacement_distance")
        if isinstance(dd, dict) and "ticks" in dd:
            displacement_pts = AbsoluteTickDistance(
                ticks=dd["ticks"], tick_size=ts_str)

        if (displacement_pts is not None and level_price is not None
                and level_price.ticks != 0):
            displacement_pct = Rational(
                numerator=displacement_pts.ticks,
                denominator=level_price.ticks)

        if level_price is not None and len(displacement_window_bars) > 0:
            clearances = [
                DirectionalTickDistance(
                    ticks=bar.low.ticks - level_price.ticks,
                    tick_size=ts_str)
                for bar in displacement_window_bars
            ]
            rsc_by_bar = tuple(clearances)

            ticks_list = [c.ticks for c in clearances]
            min_ticks = min(ticks_list)
            min_rsc = DirectionalTickDistance(
                ticks=min_ticks, tick_size=ts_str)

            tick_sum = sum(ticks_list)
            mean = tick_sum / len(ticks_list)
            decimals = decimals_of(tick_size)
            ndigits = max(decimals, 2)
            avg_val = round(mean * tick_size, ndigits)
            avg_rsc = f"{avg_val:.{ndigits}f}"

    # ── Step 7: retest fields ────────────────────────────────────────────
    retest_window_bars: list[Bar] = []
    retest_bar_count: int | None = None
    failed_retest_count: int | None = None
    failed_retests_list: list[RejectionAttempt] = []
    bars_break_to_first_retest: int | None = None
    bars_break_to_confirmation: int | None = None
    retest_closest: AbsoluteTickDistance | None = None
    retest_pen: AbsoluteTickDistance | None = None
    retest_retrace: Rational | None = None

    if isinstance(retest, dict) and retest.get("status") == "OK":
        retest_start_idx = retest.get("retest_window_start_index")
        canon_end_idx = (
            rej["confirmation_candle_index"]
            if is_valid and rej.get("confirmation_candle_index") is not None
            else retest.get("retest_window_end_index")
        )

        rw_raw = retest.get("retest_window")
        if isinstance(rw_raw, list):
            conf_time_ms = None
            if is_valid and isinstance(rej.get("confirmation_candle"), dict):
                conf_time_ms = rej["confirmation_candle"].get("time_ms")
            if conf_time_ms is not None:
                canon_raw = [
                    c for c in rw_raw if c["time_ms"] <= conf_time_ms
                ]
            else:
                canon_raw = list(rw_raw)
            retest_window_bars = [_build_bar(c, tick_size) for c in canon_raw]
            retest_bar_count = len(retest_window_bars)

        # Filter contacts to canonical window
        rc_list = retest.get("retest_contacts")
        canon_contacts = []
        if isinstance(rc_list, list) and canon_end_idx is not None:
            canon_contacts = [
                rc for rc in rc_list if rc["candle_index"] <= canon_end_idx
            ]

        if level_price is not None and len(canon_contacts) > 0:
            abs_dists = [
                abs(price_to_ticks(rc["candle"]["low"], tick_size)
                    - level_price.ticks)
                for rc in canon_contacts
            ]
            retest_closest = AbsoluteTickDistance(
                ticks=min(abs_dists), tick_size=ts_str)

            min_low_ticks = min(
                price_to_ticks(rc["candle"]["low"], tick_size)
                for rc in canon_contacts
            )
            pen_ticks = max(0, level_price.ticks - min_low_ticks)
            retest_pen = AbsoluteTickDistance(
                ticks=pen_ticks, tick_size=ts_str)

            if (displacement_pts is not None
                    and displacement_pts.ticks != 0):
                closest_dp = min(
                    price_to_ticks(rc["candle"]["low"], tick_size)
                    - level_price.ticks
                    for rc in canon_contacts
                )
                retraced = max(0, min(
                    displacement_pts.ticks,
                    displacement_pts.ticks - closest_dp))
                retest_retrace = Rational(
                    numerator=retraced,
                    denominator=displacement_pts.ticks)

    # failed_retests from rejResult (both OK and FAILED paths)
    fr_list = rej.get("failed_retests")
    if isinstance(fr_list, list):
        for fr in fr_list:
            bar = _build_bar(fr.get("candle"), tick_size)
            rules_raw = fr.get("failed_rules", [])
            rule_failures = tuple(
                RuleFailure(
                    rule_id=r if isinstance(r, str) else str(r),
                    stage=Stage.REJECTION_CANDLE,
                    value_type=ValueType.BOOLEAN,
                    actual_value=None,
                    operator=None,
                    required_value=None,
                    unit=None,
                    message=r if isinstance(r, str) else str(r),
                )
                for r in (rules_raw if isinstance(rules_raw, list) else [])
            )
            failed_retests_list.append(
                RejectionAttempt(bar=bar, failed_rules=rule_failures))
        failed_retest_count = len(failed_retests_list)

    # bars_break_to_first_retest / bars_break_to_confirmation
    if (isinstance(brk, dict) and brk.get("status") == "OK"
            and isinstance(retest, dict) and retest.get("status") == "OK"):
        bars_break_to_first_retest = (
            retest["retest_window_start_index"]
            - brk["break_candle_index"])
    if isinstance(brk, dict) and brk.get("status") == "OK" and is_valid:
        bars_break_to_confirmation = (
            rej["confirmation_candle_index"]
            - brk["break_candle_index"])

    # ── Step 8: rejection candle fields ──────────────────────────────────
    confirmation_bar: Bar | None = None
    confirmation_rej_wick: Rational | None = None
    confirmation_body: Rational | None = None
    confirmation_opp_wick: Rational | None = None
    confirmation_fcl: Rational | None = None
    confirmation_pen: AbsoluteTickDistance | None = None
    confirmation_cbl: DirectionalTickDistance | None = None

    if is_valid and isinstance(rej.get("confirmation_candle"), dict):
        confirmation_bar = _build_bar(rej["confirmation_candle"], tick_size)
        g = rej.get("geometry")
        if isinstance(g, dict):
            if g.get("rejection_wick_ratio") is not None:
                confirmation_rej_wick = _float_to_rational(
                    g["rejection_wick_ratio"])
            if g.get("body_ratio") is not None:
                confirmation_body = _float_to_rational(g["body_ratio"])
            if g.get("opposite_wick_ratio") is not None:
                confirmation_opp_wick = _float_to_rational(
                    g["opposite_wick_ratio"])
            if g.get("favorable_close_location") is not None:
                confirmation_fcl = _float_to_rational(
                    g["favorable_close_location"])
            if g.get("penetration_through_level_ticks") is not None:
                confirmation_pen = AbsoluteTickDistance(
                    ticks=g["penetration_through_level_ticks"],
                    tick_size=ts_str)
            if g.get("close_beyond_level_ticks") is not None:
                confirmation_cbl = DirectionalTickDistance(
                    ticks=g["close_beyond_level_ticks"],
                    tick_size=ts_str)

    # ── Step 9: assemble SessionMetadata ─────────────────────────────────
    ms = metadata["session"]
    session = SessionMetadata(
        symbol=ms["symbol"],
        date=ms["date"],
        market_timezone=ms["market_timezone"],
        session_open_utc_ms=ms["session_open_utc_ms"],
        session_close_utc_ms=ms["session_close_utc_ms"],
        timeframe_seconds=ms["timeframe_seconds"],
    )

    # ── Step 10: assemble DetectionResult/v1 ─────────────────────────────
    if id_factory is not None:
        _result_id = id_factory("DetectionResult/v1", {
            "symbol": ms.get("symbol", ""),
            "session_date": ms.get("date", ""),
            "preset_id": metadata["preset_id"],
            "engine_version": metadata["engine_version"],
            "direction": str(direction_val) if direction_val else "",
            "level_price_ticks": level_price.ticks if level_price else 0,
            "level_price_tick_size": level_price.tick_size if level_price else "",
            "break_bar_utc_ms": break_bar.bar_utc_ms if break_bar else 0,
            "confirmation_bar_utc_ms": (
                confirmation_bar.bar_utc_ms if confirmation_bar else 0
            ),
        })
    else:
        _result_id = str(uuid.uuid4())
    detection_result = DetectionResult(
        schema_version="DetectionResult/v1",
        result_id=_result_id,
        produced_at=datetime.now(timezone.utc).isoformat()
            .replace("+00:00", "Z"),
        session=session,
        preset_id=metadata["preset_id"],
        engine_version=metadata["engine_version"],

        status=canonical_status,
        failed_stage=failed_stage,
        failed_rules=failed_rules,

        level_price=level_price,
        level_source=level_source,
        level_bar=level_bar,
        direction=direction_val,

        break_bar=break_bar,
        directional_break_distance=dir_break_dist,

        displacement_window=tuple(displacement_window_bars),
        displacement_bar_count=displacement_bar_count,
        displacement_pts=displacement_pts,
        displacement_pct=displacement_pct,
        rejection_side_clearance_by_bar=rsc_by_bar,
        minimum_rejection_side_clearance=min_rsc,
        average_rejection_side_clearance=avg_rsc,

        retest_window=tuple(retest_window_bars),
        retest_bar_count=retest_bar_count,
        failed_retest_count=failed_retest_count,
        failed_retests=tuple(failed_retests_list),
        bars_break_to_first_retest=bars_break_to_first_retest,
        bars_break_to_confirmation=bars_break_to_confirmation,
        retest_closest_approach=retest_closest,
        retest_penetration_through_level=retest_pen,
        retest_displacement_retracement_pct=retest_retrace,

        confirmation_bar=confirmation_bar,
        confirmation_rej_wick=confirmation_rej_wick,
        confirmation_body=confirmation_body,
        confirmation_opp_wick=confirmation_opp_wick,
        confirmation_favorable_close_location=confirmation_fcl,
        confirmation_penetration=confirmation_pen,
        confirmation_close_beyond_level=confirmation_cbl,
    )

    return {"status": "OK", "detection_result": detection_result}
