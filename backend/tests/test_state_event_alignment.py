"""Tests for PWA state/event alignment.

Verifies current_state and candle_event are computed correctly
for each pipeline stage, without modifying any trading rules.
"""

from unittest.mock import MagicMock

import pytest

from trading_lab.live.decision_trace import (
    build_candle_trace,
    trace_to_dict,
    _compute_current_state,
    _compute_candle_event,
)
from trading_lab.live.signal_detector import SignalResult, SignalStatus


def _bar(close=100.0):
    return {"time_ms": 1000, "open": close-0.5, "high": close+0.5,
            "low": close-1.0, "close": close, "volume": 1000}


def _result(failed_stage, ctx=None, status=SignalStatus.NO_SETUP):
    return SignalResult(status=status, failed_stage=failed_stage,
                        pipeline_stage=failed_stage or "SIGNAL",
                        stage_context=ctx or {})


class TestCurrentState:
    def test_building_orb(self):
        assert _compute_current_state("LEVEL_NOT_FOUND", False) == "BUILDING_ORB"

    def test_waiting_for_break(self):
        assert _compute_current_state("BREAK_NOT_FOUND", False) == "WAITING_FOR_BREAK"

    def test_waiting_for_displacement(self):
        assert _compute_current_state("DISPLACEMENT_TOO_SHORT", False) == "WAITING_FOR_DISPLACEMENT"

    def test_retest_too_early_is_waiting_displacement(self):
        assert _compute_current_state("RETEST_BEFORE_DISPLACEMENT", False) == "WAITING_FOR_DISPLACEMENT"

    def test_waiting_for_retest(self):
        assert _compute_current_state("RETEST_NOT_FOUND", False) == "WAITING_FOR_RETEST"

    def test_invalidated_is_waiting_for_break(self):
        assert _compute_current_state("SEQUENCE_INVALIDATED", False) == "WAITING_FOR_BREAK"

    def test_waiting_for_entry_candle(self):
        assert _compute_current_state("NO_QUALIFYING_REJECTION_CANDLE", False) == "WAITING_FOR_ENTRY_CANDLE"

    def test_signal(self):
        assert _compute_current_state(None, True) == "SIGNAL"


class TestCandleEvent:
    def test_building_orb(self):
        assert _compute_candle_event("LEVEL_NOT_FOUND", False, "INSIDE_ORB", {}) == "BUILDING_ORB"

    def test_inside_orb_no_break(self):
        e = _compute_candle_event("BREAK_NOT_FOUND", False, "INSIDE_ORB", {})
        assert e == "INSIDE_ORB"

    def test_displacement_progression(self):
        ctx = {"displacement_bars": 2, "displacement_required": 3, "direction": "SHORT"}
        e = _compute_candle_event("DISPLACEMENT_TOO_SHORT", False, "BELOW_ORB_LOW", ctx)
        assert e == "DISPLACEMENT_2_OF_3"

    def test_retest_too_early_event(self):
        e = _compute_candle_event("RETEST_BEFORE_DISPLACEMENT", False, "BELOW_ORB_LOW", {})
        assert e == "RETEST_TOO_EARLY"

    def test_outside_orb_waiting_retest(self):
        ctx = {"direction": "SHORT"}
        e = _compute_candle_event("RETEST_NOT_FOUND", False, "BELOW_ORB_LOW", ctx)
        assert e == "OUTSIDE_ORB_SHORT"

    def test_setup_invalidated(self):
        e = _compute_candle_event("SEQUENCE_INVALIDATED", False, "INSIDE_ORB", {})
        assert e == "SETUP_INVALIDATED"

    def test_entry_rejected(self):
        e = _compute_candle_event("NO_QUALIFYING_REJECTION_CANDLE", False, "BELOW_ORB_LOW", {})
        assert e == "ENTRY_REJECTED"

    def test_signal_event(self):
        assert _compute_candle_event(None, True, "BELOW_ORB_LOW", {}) == "SIGNAL"


class TestBuildTraceStateEvent:
    """Integration: build_candle_trace populates current_state and candle_event."""

    def test_inside_orb_trace(self):
        result = _result("BREAK_NOT_FOUND", {"orb_high": 101.0, "orb_low": 99.0})
        trace = build_candle_trace(_bar(100.0), result, 101.0, 99.0, "QQQ", "09:31")
        assert trace.current_state == "WAITING_FOR_BREAK"
        assert trace.candle_event == "INSIDE_ORB"

    def test_displacement_building_trace(self):
        ctx = {"orb_high": 101.0, "orb_low": 99.0, "break_bar_index": 5,
               "direction": "SHORT", "displacement_bars": 2, "displacement_required": 3}
        result = _result("DISPLACEMENT_TOO_SHORT", ctx)
        trace = build_candle_trace(_bar(98.5), result, 101.0, 99.0, "QQQ", "09:36")
        assert trace.current_state == "WAITING_FOR_DISPLACEMENT"
        assert trace.candle_event == "DISPLACEMENT_2_OF_3"

    def test_retest_too_early_not_persistent(self):
        """RETEST_TOO_EARLY is a candle event, state is WAITING_FOR_DISPLACEMENT."""
        ctx = {"orb_high": 101.0, "orb_low": 99.0, "break_bar_index": 5,
               "direction": "SHORT"}
        result = _result("RETEST_BEFORE_DISPLACEMENT", ctx)
        trace = build_candle_trace(_bar(98.8), result, 101.0, 99.0, "QQQ", "09:36")
        assert trace.current_state == "WAITING_FOR_DISPLACEMENT"
        assert trace.candle_event == "RETEST_TOO_EARLY"

    def test_waiting_for_retest_trace(self):
        ctx = {"orb_high": 101.0, "orb_low": 99.0, "break_bar_index": 5,
               "direction": "SHORT", "displacement_bars": 4}
        result = _result("RETEST_NOT_FOUND", ctx)
        trace = build_candle_trace(_bar(97.5), result, 101.0, 99.0, "QQQ", "09:39")
        assert trace.current_state == "WAITING_FOR_RETEST"
        assert trace.candle_event == "OUTSIDE_ORB_SHORT"

    def test_invalidated_goes_to_waiting_for_break(self):
        ctx = {"orb_high": 101.0, "orb_low": 99.0, "break_bar_index": 5,
               "direction": "SHORT", "invalidation_index": 12}
        result = _result("SEQUENCE_INVALIDATED", ctx)
        trace = build_candle_trace(_bar(100.0), result, 101.0, 99.0, "QQQ", "09:42")
        assert trace.current_state == "WAITING_FOR_BREAK"
        assert trace.candle_event == "SETUP_INVALIDATED"

    def test_signal_trace(self):
        result = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            pipeline_stage="SIGNAL", setup_key="SHORT:5000",
            stage_context={"orb_high": 101.0, "orb_low": 99.0,
                           "break_bar_index": 5, "direction": "SHORT"},
        )
        trace = build_candle_trace(_bar(98.0), result, 101.0, 99.0, "QQQ", "09:45")
        assert trace.current_state == "SIGNAL"
        assert trace.candle_event == "SIGNAL"


class TestTraceToDict:
    def test_dict_includes_state_and_event(self):
        ctx = {"orb_high": 101.0, "orb_low": 99.0, "break_bar_index": 5,
               "direction": "SHORT", "displacement_bars": 2, "displacement_required": 3}
        result = _result("DISPLACEMENT_TOO_SHORT", ctx)
        trace = build_candle_trace(_bar(98.5), result, 101.0, 99.0, "QQQ", "09:36")
        d = trace_to_dict(trace)
        assert d["current_state"] == "WAITING_FOR_DISPLACEMENT"
        assert d["candle_event"] == "DISPLACEMENT_2_OF_3"


class TestTracingReadOnly:
    def test_no_trading_rules_modified(self):
        """State/event computation is purely read-only."""
        result = _result("NO_QUALIFYING_REJECTION_CANDLE",
                         {"orb_high": 101.0, "orb_low": 99.0})
        before_status = result.status
        before_stage = result.failed_stage
        trace = build_candle_trace(_bar(98.5), result, 101.0, 99.0, "QQQ", "09:40")
        assert result.status == before_status
        assert result.failed_stage == before_stage
        assert trace.current_state == "WAITING_FOR_ENTRY_CANDLE"
