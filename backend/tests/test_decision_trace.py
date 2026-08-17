"""Tests for candle-by-candle decision trace.

Validates that the trace system correctly surfaces pipeline decisions
WITHOUT modifying any trading rules or thresholds.

Covers:
1. Candle inside ORB
2. Exit/break ORB high
3. Exit/break ORB low
4. Displacement progression
5. Retest detected
6. Rejected entry candle with reason
7. Accepted entry candle (SIGNAL)
8. Re-entry into ORB / invalidation
9. New setup after invalidation
10. Tracing produces same trading result (read-only)
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from trading_lab.live.decision_trace import (
    CandleDecision,
    RejectionDetail,
    build_candle_trace,
    format_trace_line,
    trace_to_dict,
    _orb_state,
    _parse_rejection_detail,
)
from trading_lab.live.signal_detector import SignalResult, SignalStatus


# ═════════════════════════════════════════════════════════════════════
# ORB state classification
# ═════════════════════════════════════════════════════════════════════


class TestOrbState:
    def test_inside_orb(self):
        assert _orb_state(732.50, 733.0, 732.0, 733.60, 731.80) == "INSIDE_ORB"

    def test_above_orb_high(self):
        assert _orb_state(734.00, 734.50, 733.50, 733.60, 731.80) == "ABOVE_ORB_HIGH"

    def test_below_orb_low(self):
        assert _orb_state(731.00, 731.50, 730.50, 733.60, 731.80) == "BELOW_ORB_LOW"

    def test_close_on_orb_high_is_inside(self):
        assert _orb_state(733.60, 734.0, 733.0, 733.60, 731.80) == "INSIDE_ORB"

    def test_close_on_orb_low_is_inside(self):
        assert _orb_state(731.80, 732.0, 731.0, 733.60, 731.80) == "INSIDE_ORB"


# ═════════════════════════════════════════════════════════════════════
# build_candle_trace — inside ORB / no break
# ═════════════════════════════════════════════════════════════════════


class TestTraceInsideOrb:
    def test_no_break_produces_inside_orb_trace(self):
        candle = {"time_ms": 1000, "open": 732.0, "high": 733.0,
                  "low": 731.5, "close": 732.50, "volume": 1000}
        result = SignalResult(
            status=SignalStatus.NO_SETUP,
            pipeline_stage="BUILDING ORB",
            failed_stage="ORB_BUILDING",
            stage_context={"orb_high": 733.60, "orb_low": 731.80},
        )
        trace = build_candle_trace(
            candle, result, orb_high=733.60, orb_low=731.80,
            symbol="QQQ", time_str="09:31",
        )
        assert trace.orb_state == "INSIDE_ORB"
        assert trace.symbol == "QQQ"
        assert not trace.break_detected
        assert not trace.signal_emitted


# ═════════════════════════════════════════════════════════════════════
# Break detected
# ═════════════════════════════════════════════════════════════════════


class TestTraceBreak:
    def test_break_below_orb_low(self):
        candle = {"time_ms": 5000, "open": 731.90, "high": 732.10,
                  "low": 731.20, "close": 731.50, "volume": 2000}
        result = SignalResult(
            status=SignalStatus.NO_SETUP,
            pipeline_stage="DISP BUILDING",
            failed_stage="DISPLACEMENT_BUILDING",
            stage_context={
                "orb_high": 733.60, "orb_low": 731.80,
                "break_bar_index": 5, "break_time_ms": 5000,
                "direction": "SHORT", "displacement_bars": 0,
                "displacement_required": 3,
            },
        )
        trace = build_candle_trace(
            candle, result, orb_high=733.60, orb_low=731.80,
            symbol="QQQ", time_str="09:35",
        )
        assert trace.orb_state == "BELOW_ORB_LOW"
        assert trace.break_detected
        assert trace.break_direction == "SHORT"
        assert not trace.displacement_confirmed


# ═════════════════════════════════════════════════════════════════════
# Displacement progression
# ═════════════════════════════════════════════════════════════════════


class TestTraceDisplacement:
    def test_displacement_building(self):
        candle = {"time_ms": 6000, "open": 731.30, "high": 731.60,
                  "low": 731.00, "close": 731.20, "volume": 1500}
        result = SignalResult(
            status=SignalStatus.NO_SETUP,
            pipeline_stage="DISP BUILDING",
            failed_stage="DISPLACEMENT_BUILDING",
            stage_context={
                "orb_high": 733.60, "orb_low": 731.80,
                "break_bar_index": 5, "break_time_ms": 5000,
                "direction": "SHORT",
                "displacement_bars": 2, "displacement_required": 3,
            },
        )
        trace = build_candle_trace(
            candle, result, orb_high=733.60, orb_low=731.80,
            symbol="QQQ", time_str="09:36",
        )
        assert trace.displacement_count == 2
        assert trace.displacement_required == 3
        assert not trace.displacement_confirmed
        assert "2/3" in trace.stage_detail

    def test_displacement_confirmed(self):
        candle = {"time_ms": 7000, "open": 731.10, "high": 731.30,
                  "low": 730.90, "close": 731.00, "volume": 1500}
        result = SignalResult(
            status=SignalStatus.NO_SETUP,
            pipeline_stage="RETEST NOT IN WINDOW",
            failed_stage="RETEST_NOT_IN_WINDOW",
            stage_context={
                "orb_high": 733.60, "orb_low": 731.80,
                "break_bar_index": 5, "break_time_ms": 5000,
                "direction": "SHORT",
                "displacement_bars": 3, "displacement_required": 3,
            },
        )
        trace = build_candle_trace(
            candle, result, orb_high=733.60, orb_low=731.80,
            symbol="QQQ", time_str="09:37",
        )
        assert trace.displacement_confirmed
        assert trace.displacement_count == 3


# ═════════════════════════════════════════════════════════════════════
# Retest detected
# ═════════════════════════════════════════════════════════════════════


class TestTraceRetest:
    def test_retest_with_no_qualifying_candle(self):
        candle = {"time_ms": 10000, "open": 731.50, "high": 731.90,
                  "low": 731.30, "close": 731.60, "volume": 2000}
        rej_data = {
            "status": "FAILED",
            "failed_stage": "NO_QUALIFYING_REJECTION_CANDLE",
            "failed_retests": [
                {
                    "candle_index": 10,
                    "candle": {"time_ms": 10000, "close": 731.60,
                               "open": 731.50, "high": 731.90, "low": 731.30},
                    "timestamp": 10000,
                    "geometry": {
                        "rejection_wick_ratio": 0.20,
                        "body_ratio": 0.40,
                        "favorable_close_location": 0.45,
                        "close_beyond_level_ticks": 2,
                        "body_outside_orb": True,
                        "wick_penetration_pct": 0.15,
                    },
                    "failed_rules": [
                        "REJECTION_WICK_RATIO_TOO_LOW",
                        "WICK_PENETRATION_PCT_TOO_LOW",
                    ],
                }
            ],
        }
        result = SignalResult(
            status=SignalStatus.NO_SETUP,
            pipeline_stage="NO ENTRY CANDLE",
            failed_stage="NO_QUALIFYING_REJECTION_CANDLE",
            stage_context={
                "orb_high": 733.60, "orb_low": 731.80,
                "break_bar_index": 5, "break_time_ms": 5000,
                "direction": "SHORT",
                "displacement_bars": 3, "displacement_required": 3,
                "retest_start_index": 8, "retest_end_index": 12,
            },
            rejection_detail=rej_data,
        )
        trace = build_candle_trace(
            candle, result, orb_high=733.60, orb_low=731.80,
            symbol="QQQ", time_str="09:40",
            rejection_data=rej_data,
        )
        assert trace.retest_detected
        assert trace.rejection_evaluated
        assert trace.rejection_detail is not None
        assert not trace.rejection_detail.qualifies
        assert "REJECTION_WICK_RATIO_TOO_LOW" in trace.rejection_detail.failed_rules
        assert trace.rejection_detail.wick_ratio == 0.20
        assert trace.rejection_detail.wick_ratio_pass is False
        assert trace.rejection_detail.body_outside_pass is True


# ═════════════════════════════════════════════════════════════════════
# SIGNAL (accepted entry candle)
# ═════════════════════════════════════════════════════════════════════


class TestTraceSignal:
    def test_signal_emitted(self):
        candle = {"time_ms": 12000, "open": 731.50, "high": 731.85,
                  "low": 731.20, "close": 731.30, "volume": 3000}
        result = SignalResult(
            status=SignalStatus.SIGNAL,
            direction="SHORT",
            pipeline_stage="SIGNAL",
            stage_context={
                "orb_high": 733.60, "orb_low": 731.80,
                "break_bar_index": 5, "break_time_ms": 5000,
                "direction": "SHORT",
                "displacement_bars": 3, "displacement_required": 3,
                "retest_start_index": 8, "retest_end_index": 14,
            },
            setup_key="SHORT:5000",
            trade_plan=MagicMock(),
            detection_result=MagicMock(),
        )
        trace = build_candle_trace(
            candle, result, orb_high=733.60, orb_low=731.80,
            symbol="QQQ", time_str="09:42",
        )
        assert trace.signal_emitted
        assert trace.setup_key == "SHORT:5000"
        assert "SIGNAL" in trace.stage_detail


# ═════════════════════════════════════════════════════════════════════
# Sequence invalidation (re-entry into ORB)
# ═════════════════════════════════════════════════════════════════════


class TestTraceInvalidation:
    def test_sequence_invalidated(self):
        candle = {"time_ms": 15000, "open": 731.90, "high": 732.50,
                  "low": 731.80, "close": 732.30, "volume": 2500}
        result = SignalResult(
            status=SignalStatus.NO_SETUP,
            pipeline_stage="SEQUENCE INVALIDATED",
            failed_stage="SEQUENCE_INVALIDATED",
            stage_context={
                "orb_high": 733.60, "orb_low": 731.80,
                "break_bar_index": 5, "break_time_ms": 5000,
                "direction": "SHORT",
                "displacement_bars": 3, "displacement_required": 3,
                "invalidation_index": 14,
            },
        )
        trace = build_candle_trace(
            candle, result, orb_high=733.60, orb_low=731.80,
            symbol="QQQ", time_str="09:45",
        )
        assert trace.orb_state == "INSIDE_ORB"
        assert "invalidated" in trace.stage_detail


# ═════════════════════════════════════════════════════════════════════
# format_trace_line
# ═════════════════════════════════════════════════════════════════════


class TestFormatTraceLine:
    def test_inside_orb_format(self):
        d = CandleDecision(
            time_ms=1000, time_str="09:31", symbol="QQQ",
            close=732.50, orb_state="INSIDE_ORB",
            pipeline_stage="BUILDING ORB", failed_stage="ORB_BUILDING",
            stage_detail="building ORB",
        )
        line = format_trace_line(d)
        assert "QQQ" in line
        assert "09:31" in line
        assert "INSIDE_ORB" in line

    def test_signal_format(self):
        d = CandleDecision(
            time_ms=12000, time_str="09:42", symbol="QQQ",
            close=731.30, orb_state="BELOW_ORB_LOW",
            break_detected=True, break_direction="SHORT",
            displacement_confirmed=True, displacement_count=3,
            retest_detected=True, signal_emitted=True,
            pipeline_stage="SIGNAL", stage_detail="SIGNAL",
        )
        line = format_trace_line(d)
        assert "SIGNAL ✓" in line

    def test_failed_rejection_format(self):
        rd = RejectionDetail(
            candle_index=10, time_ms=10000, close=731.60,
            wick_ratio=0.20, wick_ratio_pass=False,
            body_ratio=0.40, body_ratio_pass=True,
            body_outside_orb=True, body_outside_pass=True,
            wick_penetration_pct=0.15, wick_penetration_pass=False,
            favorable_close=0.45, favorable_close_pass=True,
            failed_rules=("REJECTION_WICK_RATIO_TOO_LOW",),
        )
        d = CandleDecision(
            time_ms=10000, time_str="09:40", symbol="QQQ",
            close=731.60, orb_state="BELOW_ORB_LOW",
            break_detected=True, break_direction="SHORT",
            displacement_confirmed=True, displacement_count=3,
            retest_detected=True, rejection_evaluated=True,
            rejection_detail=rd,
            pipeline_stage="NO ENTRY CANDLE",
            stage_detail="retest detected — no qualifying entry candle yet",
        )
        line = format_trace_line(d)
        assert "wick=✗" in line
        assert "body=✓" in line


# ═════════════════════════════════════════════════════════════════════
# trace_to_dict — JSON serialization
# ═════════════════════════════════════════════════════════════════════


class TestTraceToDict:
    def test_basic_dict_structure(self):
        d = CandleDecision(
            time_ms=1000, time_str="09:31", symbol="QQQ",
            close=732.50, orb_state="INSIDE_ORB",
            orb_high=733.60, orb_low=731.80,
        )
        out = trace_to_dict(d)
        assert out["time"] == "09:31"
        assert out["orb_state"] == "INSIDE_ORB"
        assert out["orb_high"] == 733.60
        assert out["signal"] is False

    def test_rejection_dict(self):
        rd = RejectionDetail(
            candle_index=10, time_ms=10000, close=731.60,
            wick_ratio=0.20, wick_ratio_pass=False,
            body_ratio=0.40, body_ratio_pass=True,
            failed_rules=("REJECTION_WICK_RATIO_TOO_LOW",),
        )
        d = CandleDecision(
            time_ms=10000, time_str="09:40", symbol="QQQ",
            close=731.60, orb_state="BELOW_ORB_LOW",
            rejection_evaluated=True, rejection_detail=rd,
        )
        out = trace_to_dict(d)
        assert "rejection" in out
        assert out["rejection"]["predicates"]["wick_ratio"]["pass"] is False
        assert out["rejection"]["predicates"]["body_ratio"]["pass"] is True


# ═════════════════════════════════════════════════════════════════════
# Tracing ON/OFF produces same trading result
# ═════════════════════════════════════════════════════════════════════


class TestTracingReadOnly:
    def test_trace_does_not_modify_signal_result(self):
        """Tracing is purely read-only — same SignalResult before and after."""
        result = SignalResult(
            status=SignalStatus.NO_SETUP,
            pipeline_stage="NO ENTRY CANDLE",
            failed_stage="NO_QUALIFYING_REJECTION_CANDLE",
            stage_context={"orb_high": 733.60, "orb_low": 731.80,
                           "break_bar_index": 5},
        )
        candle = {"time_ms": 1000, "open": 732.0, "high": 733.0,
                  "low": 731.5, "close": 732.50, "volume": 1000}

        # Build trace
        trace = build_candle_trace(
            candle, result, orb_high=733.60, orb_low=731.80,
            symbol="QQQ", time_str="09:40",
        )

        # SignalResult unchanged
        assert result.status == SignalStatus.NO_SETUP
        assert result.pipeline_stage == "NO ENTRY CANDLE"
        assert result.failed_stage == "NO_QUALIFYING_REJECTION_CANDLE"
        assert result.stage_context["break_bar_index"] == 5
