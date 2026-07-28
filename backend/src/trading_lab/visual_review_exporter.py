"""Visual review event exporter for the BDRR pipeline.

Converts Strategy Runner result records and their session candles into
deterministic JSON payloads suitable for candlestick chart rendering.

This module does NOT:
  - run detection logic;
  - score, filter, or apply policy;
  - render charts or produce HTML;
  - generate random IDs or timestamps.

Public API:

    export_visual_event(session_candles, runner_result) → dict
    export_visual_events(session_candle_map, runner_results) → list[dict]
    serialize_visual_events(events) → str   (deterministic JSON)

Determinism contract:
    Given identical inputs, output is byte-for-byte identical.
    UTF-8, sorted keys, compact separators, LF newlines,
    stable ordering, no generated timestamps.
"""

from __future__ import annotations

import json


# ── Attribute access helper ──────────────────────────────────────────────────


def _get(obj: object, attr: str, default: object = None) -> object:
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _get_ticks(obj: object, field: str) -> int | None:
    v = _get(obj, field)
    if v is None:
        return None
    if hasattr(v, "ticks"):
        return v.ticks
    if isinstance(v, dict):
        return v.get("ticks")
    return None


# ── Candle serialization ────────────────────────────────────────────────────


def _serialize_candle(candle: dict, index: int) -> dict:
    """Convert a raw session candle to the visual review format."""
    return {
        "index": index,
        "time_ms": candle["time_ms"],
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "close": candle["close"],
        "volume": candle.get("volume"),
    }


# ── Bar timestamp extraction ────────────────────────────────────────────────


def _bar_utc_ms(bar: object) -> int | None:
    """Extract bar_utc_ms from a canonical Bar or dict."""
    if bar is None:
        return None
    return _get(bar, "bar_utc_ms")


def _find_candle_index(candles: list[dict], time_ms: int | None) -> int | None:
    """Find the index of a candle by its time_ms."""
    if time_ms is None:
        return None
    for i, c in enumerate(candles):
        if c["time_ms"] == time_ms:
            return i
    return None


# ── Annotation builder ──────────────────────────────────────────────────────


def _build_annotations(
    candles: list[dict],
    runner_result: dict,
) -> dict:
    """Build the annotations dict from runner result and its embedded objects."""
    dr = runner_result.get("detection_result")
    tp = runner_result.get("trade_plan")
    to = runner_result.get("trade_outcome")

    ann: dict = {}

    # ── Break ────────────────────────────────────────────────────────────
    if dr is not None:
        break_bar = _get(dr, "break_bar")
        break_ms = _bar_utc_ms(break_bar)
        ann["break_candle_time_ms"] = break_ms
        ann["break_candle_index"] = _find_candle_index(candles, break_ms)

    # ── Displacement ─────────────────────────────────────────────────────
    if dr is not None:
        dw = _get(dr, "displacement_window")
        if dw is not None and len(dw) > 0:
            first_ms = _bar_utc_ms(dw[0])
            last_ms = _bar_utc_ms(dw[-1])
            ann["displacement_start_index"] = _find_candle_index(
                candles, first_ms)
            ann["displacement_end_index"] = _find_candle_index(
                candles, last_ms)
        else:
            ann["displacement_start_index"] = None
            ann["displacement_end_index"] = None

    # ── Retest ───────────────────────────────────────────────────────────
    if dr is not None:
        rw = _get(dr, "retest_window")
        if rw is not None and len(rw) > 0:
            first_ms = _bar_utc_ms(rw[0])
            last_ms = _bar_utc_ms(rw[-1])
            ann["retest_start_index"] = _find_candle_index(
                candles, first_ms)
            ann["retest_end_index"] = _find_candle_index(candles, last_ms)
        else:
            ann["retest_start_index"] = None
            ann["retest_end_index"] = None

    # ── Confirmation / rejection ─────────────────────────────────────────
    if dr is not None:
        conf_bar = _get(dr, "confirmation_bar")
        conf_ms = _bar_utc_ms(conf_bar)
        ann["confirmation_candle_time_ms"] = conf_ms
        ann["confirmation_candle_index"] = _find_candle_index(
            candles, conf_ms)

    # ── Trade plan ───────────────────────────────────────────────────────
    if tp is not None:
        ann["entry_price_ticks"] = _get_ticks(tp, "entry_price")
        ann["stop_price_ticks"] = _get_ticks(tp, "stop_price")
        ann["r2_price_ticks"] = _get_ticks(tp, "r2_price")
        ann["r3_price_ticks"] = _get_ticks(tp, "r3_price")
        ann["r4_price_ticks"] = _get_ticks(tp, "r4_price")
    else:
        ann["entry_price_ticks"] = None
        ann["stop_price_ticks"] = None
        ann["r2_price_ticks"] = None
        ann["r3_price_ticks"] = None
        ann["r4_price_ticks"] = None

    # ── Trade outcome ────────────────────────────────────────────────────
    if to is not None:
        exit_ms = _get(to, "exit_bar_utc_ms")
        ann["exit_candle_time_ms"] = exit_ms
        ann["exit_candle_index"] = _find_candle_index(candles, exit_ms)
        ann["exit_price_ticks"] = _get(to, "exit_price_ticks")
        ann["outcome"] = str(_get(to, "outcome"))
    else:
        ann["exit_candle_time_ms"] = None
        ann["exit_candle_index"] = None
        ann["exit_price_ticks"] = runner_result.get("exit_price_ticks")
        ann["outcome"] = str(runner_result.get("outcome", ""))

    # ── Failure info ─────────────────────────────────────────────────────
    ann["failed_stage"] = runner_result.get("failure_stage")

    failed_rules = runner_result.get("failed_rules", [])
    if failed_rules and hasattr(failed_rules[0], "rule_id"):
        ann["failed_rules"] = [str(_get(r, "rule_id")) for r in failed_rules]
    else:
        ann["failed_rules"] = [str(r) for r in failed_rules] if failed_rules else []

    return ann


# ── Primary export function ─────────────────────────────────────────────────


def export_visual_event(
    session_candles: list[dict],
    runner_result: dict,
) -> dict:
    """Export a single visual review event payload.

    Parameters
    ----------
    session_candles : list[dict]
        Raw session candles (same list used by the Strategy Runner).
    runner_result : dict
        One result record from ``run_bdrr_strategy``.

    Returns
    -------
    dict
        Deterministic event payload ready for JSON serialization.

    Does not mutate inputs.
    """
    dr = runner_result.get("detection_result")

    # Direction and level source from the detection result
    direction = None
    level_source = None
    level_price_ticks = None

    if dr is not None:
        d = _get(dr, "direction")
        direction = str(d) if d is not None else None
        ls = _get(dr, "level_source")
        level_source = str(ls) if ls is not None else None
        lp = _get(dr, "level_price")
        level_price_ticks = _get_ticks(dr, "level_price")

    # ORB High and Low from the level_bar (the ORB candle)
    orb_high_ticks = None
    orb_low_ticks = None
    if dr is not None:
        level_bar = _get(dr, "level_bar")
        if level_bar is not None:
            hb = _get(level_bar, "high")
            if hb is not None:
                orb_high_ticks = _get(hb, "ticks")
            lb = _get(level_bar, "low")
            if lb is not None:
                orb_low_ticks = _get(lb, "ticks")

    candles = [
        _serialize_candle(c, i) for i, c in enumerate(session_candles)
    ]

    annotations = _build_annotations(session_candles, runner_result)

    # Use detection_result_id as event_id (deterministic when available)
    event_id = runner_result.get("detection_result_id")

    return {
        "event_id": event_id,
        "symbol": runner_result.get("symbol"),
        "session_date": runner_result.get("session_date"),
        "direction": direction,
        "detection_status": runner_result.get("detection_status"),
        "failed_stage": runner_result.get("failure_stage"),
        "failed_rules": annotations["failed_rules"],
        "level_source": level_source,
        "level_price_ticks": level_price_ticks,
        "orb_high_ticks": orb_high_ticks,
        "orb_low_ticks": orb_low_ticks,
        "candles": candles,
        "annotations": annotations,
    }


def export_visual_events(
    session_candle_map: dict[str, list[dict]],
    runner_results: list[dict],
) -> list[dict]:
    """Export visual review events for multiple runner results.

    Parameters
    ----------
    session_candle_map : dict[str, list[dict]]
        Maps session_date to the session's raw candle list.
    runner_results : list[dict]
        Result records from ``run_bdrr_strategy``.

    Returns
    -------
    list[dict]
        Ordered list of event payloads.
    """
    events = []
    for result in runner_results:
        date = result.get("session_date")
        candles = session_candle_map.get(date, [])
        events.append(export_visual_event(candles, result))
    return events


# ── Deterministic serialization ─────────────────────────────────────────────


def serialize_visual_events(events: list[dict]) -> str:
    """Serialize event payloads to deterministic JSON.

    Guarantees:
        UTF-8 compatible, sorted keys, compact separators,
        LF newlines, no trailing whitespace.
    """
    return json.dumps(
        events,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
