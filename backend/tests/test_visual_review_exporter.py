"""Tests for the visual review event exporter.

Covers:
  1. Valid LONG event
  2. Valid SHORT event
  3. Failed event
  4. Candle ordering
  5. Annotation indexes
  6. Deterministic repeated export
  7. JSON serialization round-trip
  8. No mutation of input objects
"""

import copy
import json

import pytest

from trading_lab.strategy_runner import run_bdrr_strategy
from trading_lab.session_context import build_session_context
from trading_lab.visual_review_exporter import (
    export_visual_event,
    export_visual_events,
    serialize_visual_events,
)


# ── Timestamps (2026-07-01 ET) ──────────────────────────────────────────────

MS_0930 = 1782912600000
MS_0935 = 1782912900000
MS_0940 = 1782913200000
MS_0945 = 1782913500000
MS_0950 = 1782913800000
MS_0955 = 1782914100000
MS_1000 = 1782914400000


def c(time_ms, open_=100.0, high=101.0, low=99.0, close=100.5, volume=100):
    return {
        "time_ms": time_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


# ── LONG session that produces VALID detection ──────────────────────────────

LONG_CANDLES = [
    c(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5),
    c(MS_0935, open_=100.50, high=102.0, low=100.20, close=101.50),
    c(MS_0940, open_=101.60, high=102.50, low=101.20, close=102.00),
    c(MS_0945, open_=101.80, high=102.20, low=100.80, close=101.20),
    c(MS_0950, open_=101.30, high=101.50, low=100.90, close=101.40),
    c(MS_0955, open_=101.40, high=103.00, low=101.30, close=102.80),
]

LONG_SESSION = {
    "symbol": "SPY",
    "date": "2026-07-01",
    "market_timezone": "America/New_York",
    "session_open_utc_ms": MS_0930,
    "session_close_utc_ms": MS_1000,
    "timeframe": "5m",
    "candles": LONG_CANDLES,
}

LONG_PRESET = {
    "preset_id": "long_viz_test",
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "entry_model": "CONFIRMATION_CLOSE",
    "entry_buffer_ticks": 0,
    "stop_buffer_ticks": 0,
    "min_displacement_ticks": None,
    "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None,
    "min_displacement_bars": 1,
    "confirmation_wick_penetration_pct_min": 0,
}

CONFIG = {
    "tick_size": 0.01,
    "exit_target_r": 2,
    "engine_version": "1.0.0-test",
}


# ── SHORT session that produces VALID detection ─────────────────────────────

SHORT_CANDLES = [
    c(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5),
    c(MS_0935, open_=99.50, high=99.80, low=98.20, close=98.50),
    c(MS_0940, open_=98.40, high=98.60, low=97.80, close=97.90),
    c(MS_0945, open_=98.00, high=99.20, low=97.80, close=98.80),
    c(MS_0950, open_=98.70, high=99.10, low=98.50, close=98.55),
    c(MS_0955, open_=98.50, high=98.60, low=96.00, close=96.10),
]

SHORT_SESSION = {
    "symbol": "QQQ",
    "date": "2026-07-01",
    "market_timezone": "America/New_York",
    "session_open_utc_ms": MS_0930,
    "session_close_utc_ms": MS_1000,
    "timeframe": "5m",
    "candles": SHORT_CANDLES,
}

SHORT_PRESET = {
    **LONG_PRESET,
    "preset_id": "short_viz_test",
    "level_source": "ORB_LOW",
    "direction": "SHORT",
}


# ── FAILED session (no break) ──────────────────────────────────────────────

FAIL_CANDLES = [
    c(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5),
    c(MS_0935, open_=100.0, high=100.5, low=99.5, close=100.0),
    c(MS_0940, open_=100.0, high=100.8, low=99.8, close=100.2),
]

FAIL_SESSION = {
    "symbol": "AAPL",
    "date": "2026-07-01",
    "market_timezone": "America/New_York",
    "session_open_utc_ms": MS_0930,
    "session_close_utc_ms": MS_0945,
    "timeframe": "5m",
    "candles": FAIL_CANDLES,
}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _run_long():
    results = run_bdrr_strategy([LONG_SESSION], LONG_PRESET, CONFIG)
    return results[0]


def _run_short():
    results = run_bdrr_strategy([SHORT_SESSION], SHORT_PRESET, CONFIG)
    return results[0]


def _run_fail():
    results = run_bdrr_strategy([FAIL_SESSION], LONG_PRESET, CONFIG)
    return results[0]


# ══════════════════════════════════════════════════════════════════════════════
# 1. Valid LONG event
# ══════════════════════════════════════════════════════════════════════════════


class TestValidLongEvent:
    def test_top_level_fields(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        assert event["symbol"] == "SPY"
        assert event["session_date"] == "2026-07-01"
        assert event["direction"] == "LONG"
        assert event["detection_status"] == "VALID"
        assert event["failed_stage"] is None
        assert event["failed_rules"] == []
        assert event["level_source"] == "ORB_HIGH"
        assert isinstance(event["level_price_ticks"], int)
        assert event["event_id"] is not None

    def test_candles_present(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        assert len(event["candles"]) == len(LONG_CANDLES)

    def test_annotations_present(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        ann = event["annotations"]
        assert "break_candle_index" in ann
        assert "confirmation_candle_index" in ann
        assert "entry_price_ticks" in ann
        assert "stop_price_ticks" in ann
        assert "outcome" in ann


# ══════════════════════════════════════════════════════════════════════════════
# 2. Valid SHORT event
# ══════════════════════════════════════════════════════════════════════════════


class TestValidShortEvent:
    def test_top_level_fields(self):
        result = _run_short()
        event = export_visual_event(SHORT_CANDLES, result)
        assert event["symbol"] == "QQQ"
        assert event["direction"] == "SHORT"
        assert event["detection_status"] == "VALID"
        assert event["level_source"] == "ORB_LOW"

    def test_annotations_have_trade_fields(self):
        result = _run_short()
        event = export_visual_event(SHORT_CANDLES, result)
        ann = event["annotations"]
        assert ann["entry_price_ticks"] is not None
        assert ann["stop_price_ticks"] is not None
        assert ann["r2_price_ticks"] is not None
        # SHORT: stop > entry, targets < entry
        assert ann["stop_price_ticks"] > ann["entry_price_ticks"]
        assert ann["r2_price_ticks"] < ann["entry_price_ticks"]


# ══════════════════════════════════════════════════════════════════════════════
# 3. Failed event
# ══════════════════════════════════════════════════════════════════════════════


class TestFailedEvent:
    def test_failed_fields(self):
        result = _run_fail()
        event = export_visual_event(FAIL_CANDLES, result)
        assert event["detection_status"] != "VALID"
        assert event["candles"] is not None
        assert len(event["candles"]) == len(FAIL_CANDLES)

    def test_annotations_null_trade_fields(self):
        result = _run_fail()
        event = export_visual_event(FAIL_CANDLES, result)
        ann = event["annotations"]
        assert ann["entry_price_ticks"] is None
        assert ann["stop_price_ticks"] is None
        assert ann["exit_candle_index"] is None

    def test_failed_stage_propagated(self):
        result = _run_fail()
        event = export_visual_event(FAIL_CANDLES, result)
        ann = event["annotations"]
        # Either top-level or annotation should carry the failure info
        has_failure = (
            event["failed_stage"] is not None
            or ann["failed_stage"] is not None
        )
        assert has_failure


# ══════════════════════════════════════════════════════════════════════════════
# 4. Candle ordering
# ══════════════════════════════════════════════════════════════════════════════


class TestCandleOrdering:
    def test_candles_preserve_order(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        for i, ec in enumerate(event["candles"]):
            assert ec["index"] == i
            assert ec["time_ms"] == LONG_CANDLES[i]["time_ms"]

    def test_candles_ascending_time(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        times = [ec["time_ms"] for ec in event["candles"]]
        assert times == sorted(times)

    def test_candle_ohlcv_fields(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        for ec in event["candles"]:
            assert "open" in ec
            assert "high" in ec
            assert "low" in ec
            assert "close" in ec
            assert "volume" in ec
            assert "time_ms" in ec
            assert "index" in ec


# ══════════════════════════════════════════════════════════════════════════════
# 5. Annotation indexes
# ══════════════════════════════════════════════════════════════════════════════


class TestAnnotationIndexes:
    def test_break_index_matches_candle(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        ann = event["annotations"]
        bi = ann["break_candle_index"]
        bt = ann["break_candle_time_ms"]
        if bi is not None and bt is not None:
            assert event["candles"][bi]["time_ms"] == bt

    def test_confirmation_index_matches_candle(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        ann = event["annotations"]
        ci = ann["confirmation_candle_index"]
        ct = ann["confirmation_candle_time_ms"]
        if ci is not None and ct is not None:
            assert event["candles"][ci]["time_ms"] == ct

    def test_displacement_window_indexes(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        ann = event["annotations"]
        ds = ann.get("displacement_start_index")
        de = ann.get("displacement_end_index")
        if ds is not None and de is not None:
            assert ds <= de

    def test_exit_index_valid(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        ann = event["annotations"]
        ei = ann.get("exit_candle_index")
        et = ann.get("exit_candle_time_ms")
        if ei is not None and et is not None:
            assert event["candles"][ei]["time_ms"] == et


# ══════════════════════════════════════════════════════════════════════════════
# 6. Deterministic repeated export
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterministicExport:
    def test_same_output_twice(self):
        result = _run_long()
        e1 = export_visual_event(LONG_CANDLES, result)
        e2 = export_visual_event(LONG_CANDLES, result)
        s1 = serialize_visual_events([e1])
        s2 = serialize_visual_events([e2])
        assert s1 == s2

    def test_short_deterministic(self):
        result = _run_short()
        e1 = export_visual_event(SHORT_CANDLES, result)
        e2 = export_visual_event(SHORT_CANDLES, result)
        s1 = serialize_visual_events([e1])
        s2 = serialize_visual_events([e2])
        assert s1 == s2


# ══════════════════════════════════════════════════════════════════════════════
# 7. JSON serialization round-trip
# ══════════════════════════════════════════════════════════════════════════════


class TestJsonRoundTrip:
    def test_valid_json(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        serialized = serialize_visual_events([event])
        parsed = json.loads(serialized)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["symbol"] == "SPY"

    def test_round_trip_preserves_structure(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        serialized = serialize_visual_events([event])
        parsed = json.loads(serialized)[0]
        assert parsed["candles"][0]["index"] == 0
        assert "annotations" in parsed
        assert "break_candle_index" in parsed["annotations"]

    def test_sorted_keys(self):
        result = _run_long()
        event = export_visual_event(LONG_CANDLES, result)
        serialized = serialize_visual_events([event])
        # Verify keys are sorted by checking first key in JSON
        parsed = json.loads(serialized)
        keys = list(parsed[0].keys())
        assert keys == sorted(keys)

    def test_multiple_events(self):
        r_long = _run_long()
        r_short = _run_short()
        e_long = export_visual_event(LONG_CANDLES, r_long)
        e_short = export_visual_event(SHORT_CANDLES, r_short)
        serialized = serialize_visual_events([e_long, e_short])
        parsed = json.loads(serialized)
        assert len(parsed) == 2
        assert parsed[0]["direction"] == "LONG"
        assert parsed[1]["direction"] == "SHORT"


# ══════════════════════════════════════════════════════════════════════════════
# 8. No mutation of input objects
# ══════════════════════════════════════════════════════════════════════════════


class TestNoMutation:
    def test_candles_not_mutated(self):
        result = _run_long()
        candles_copy = copy.deepcopy(LONG_CANDLES)
        export_visual_event(candles_copy, result)
        assert candles_copy == LONG_CANDLES

    def test_runner_result_not_mutated(self):
        result = _run_long()
        result_copy = copy.deepcopy(result)
        export_visual_event(LONG_CANDLES, result)
        # Compare key fields
        assert result["symbol"] == result_copy["symbol"]
        assert result["session_date"] == result_copy["session_date"]
        assert result["detection_status"] == result_copy["detection_status"]
        assert result["outcome"] == result_copy["outcome"]
        assert result.get("entry_price_ticks") == result_copy.get("entry_price_ticks")


# ══════════════════════════════════════════════════════════════════════════════
# export_visual_events batch helper
# ══════════════════════════════════════════════════════════════════════════════


class TestBatchExport:
    def test_maps_sessions_correctly(self):
        r_long = _run_long()
        r_fail = _run_fail()
        candle_map = {
            "2026-07-01": LONG_CANDLES,
        }
        events = export_visual_events(candle_map, [r_long, r_fail])
        assert len(events) == 2
        # Both sessions are on same date
        assert events[0]["symbol"] == "SPY"
        assert events[1]["symbol"] == "AAPL"
