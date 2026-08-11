"""Tests for LiveSignalDetector — incremental live signal evaluation.

Verifies:
  1. Empty/insufficient session → NO_SETUP.
  2. Partial setup before entry candle → NO_SETUP.
  3. Valid setup at entry candle → SIGNAL.
  4. Signal exposes correct direction.
  5. Signal exposes correct entry/stop/target from existing trade plan builder.
  6. Repeated evaluation of identical session is deterministic.
  7. Adding future bars is not required to create the signal.
  8. Detector does not call/use simulated trade outcome.
"""

import pytest
from decimal import Decimal

from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)
from trading_lab.live.session_builder_live import LiveSessionBuilder


# ── Timestamp helpers ────────────────────────────────────────────────────────

# 2026-08-11 EDT (UTC-4): 09:30 ET = 13:30 UTC
MS_0930 = 1786455000000


def _ms(minute_offset: int) -> int:
    """Return epoch ms for 09:30 + minute_offset minutes."""
    return MS_0930 + minute_offset * 60_000


# ── Synthetic LONG setup ─────────────────────────────────────────────────────
#
# With timeframe_minutes=1 and orb_duration_minutes=5, the ORB uses
# bars 0–4 (09:30–09:34). The ORB high/low are the max high / min low
# of those 5 bars. orb_candle_index = 4. Scanning starts from index 5.
#
# ORB bars (09:30–09:34):
#   All within range 99.00–101.00.
#   ORB high = 101.00, ORB low = 99.00.
#   level_price = 101.00 (ORB_HIGH for LONG).
#
# Break candle (09:35, index 5): close=101.50 > 101.00 → confirmed break
#
# Displacement bars (09:36–09:38, indices 6–8): price stays above level
#   low > 101.00, never touching level. 3 bars of displacement.
#
# Rejection/entry candle (09:39, index 9):
#   open=101.10, high=101.30, low=100.80, close=101.20
#   range = 50 ticks
#   body = 10 ticks → body_ratio = 0.20 ✓ (<= 0.40)
#   rejection_wick = 30 ticks → wick_ratio = 0.60 ✓ (>= 0.47)
#   favorable_close_location = 0.80 ✓ (>= 0.25)
#   body outside ORB: open=101.10 >= 101.00 ✓, close=101.20 > 101.00 ✓
#   wick penetration: (101.00 - 100.80) / 30 = 0.667 ✓ (>= 0.20)


def _orb_bars():
    """5 ORB bars (09:30–09:34) defining ORB high=101.00, low=99.00."""
    return [
        {"time_ms": _ms(0), "open": 100.00, "high": 101.00, "low": 99.00,
         "close": 100.50, "volume": 1000},
        {"time_ms": _ms(1), "open": 100.50, "high": 100.80, "low": 100.00,
         "close": 100.30, "volume": 1000},
        {"time_ms": _ms(2), "open": 100.30, "high": 100.70, "low": 99.80,
         "close": 100.40, "volume": 1000},
        {"time_ms": _ms(3), "open": 100.40, "high": 100.90, "low": 100.10,
         "close": 100.60, "volume": 1000},
        {"time_ms": _ms(4), "open": 100.60, "high": 100.95, "low": 100.20,
         "close": 100.70, "volume": 1000},
    ]


def _break_bar():
    """Break candle at 09:35 (index 5): close > ORB high."""
    return {
        "time_ms": _ms(5),
        "open": 100.80,
        "high": 101.60,
        "low": 100.70,
        "close": 101.50,
        "volume": 1000,
    }


def _displacement_bars():
    """3 displacement bars (09:36–09:38) staying above level (low > 101.00)."""
    return [
        {"time_ms": _ms(6), "open": 101.55, "high": 101.80, "low": 101.20,
         "close": 101.60, "volume": 1000},
        {"time_ms": _ms(7), "open": 101.60, "high": 101.90, "low": 101.30,
         "close": 101.70, "volume": 1000},
        {"time_ms": _ms(8), "open": 101.70, "high": 101.85, "low": 101.10,
         "close": 101.40, "volume": 1000},
    ]


def _rejection_bar():
    """Rejection/entry candle at 09:39 (index 9): touches level with proper geometry."""
    return {
        "time_ms": _ms(9),
        "open": 101.10,
        "high": 101.30,
        "low": 100.80,
        "close": 101.20,
        "volume": 1000,
    }


def _build_session_up_to(bars: list[dict]) -> dict:
    """Build a session dict from a list of bars via LiveSessionBuilder."""
    builder = LiveSessionBuilder("SPY")
    for bar in bars:
        builder.add_bar(bar)
    return builder.current_session()


def _all_bars_through_rejection():
    """All bars needed for a complete LONG setup."""
    return _orb_bars() + [_break_bar()] + _displacement_bars() + [_rejection_bar()]


def _make_detector(**kwargs):
    """Create a LONG detector with SPY defaults."""
    defaults = {
        "symbol": "SPY",
        "direction": "LONG",
        "tick_size": 0.01,
        "market_timezone": "America/New_York",
        "session_open": "09:30",
        "entry_model": "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0,
        "exit_target_r": 2,
    }
    defaults.update(kwargs)
    return LiveSignalDetector(**defaults)


# ── Test 1: Empty/insufficient session → NO_SETUP ───────────────────────────

class TestNoSetup:
    def test_none_session(self):
        d = _make_detector()
        result = d.evaluate(None)
        assert result.status == SignalStatus.NO_SETUP

    def test_empty_candles(self):
        d = _make_detector()
        result = d.evaluate({"candles": [], "symbol": "SPY", "date": "2026-08-11",
                             "market_timezone": "America/New_York",
                             "session_open_utc_ms": MS_0930,
                             "session_close_utc_ms": MS_0930,
                             "timeframe": "1m"})
        assert result.status == SignalStatus.NO_SETUP

    def test_only_orb_bars(self):
        d = _make_detector()
        sess = _build_session_up_to(_orb_bars())
        result = d.evaluate(sess)
        assert result.status == SignalStatus.NO_SETUP


# ── Test 2: Partial setup before entry candle → NO_SETUP ────────────────────

class TestPartialSetup:
    def test_break_only(self):
        d = _make_detector()
        sess = _build_session_up_to(_orb_bars() + [_break_bar()])
        result = d.evaluate(sess)
        assert result.status == SignalStatus.NO_SETUP

    def test_break_plus_displacement_no_retest(self):
        d = _make_detector()
        bars = _orb_bars() + [_break_bar()] + _displacement_bars()
        sess = _build_session_up_to(bars)
        result = d.evaluate(sess)
        assert result.status == SignalStatus.NO_SETUP


# ── Test 3: Valid setup at entry candle → SIGNAL ─────────────────────────────

class TestSignal:
    def test_signal_at_rejection(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert result.status == SignalStatus.SIGNAL

    def test_signal_has_trade_plan(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert result.trade_plan is not None
        assert result.detection_result is not None


# ── Test 4: Signal exposes correct direction ─────────────────────────────────

class TestDirection:
    def test_long_direction(self):
        d = _make_detector(direction="LONG")
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert result.direction == "LONG"


# ── Test 5: Signal exposes correct entry/stop/target ─────────────────────────

class TestPrices:
    def test_entry_price(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert result.status == SignalStatus.SIGNAL
        # CONFIRMATION_CLOSE, LONG, buffer=0: entry = close of rejection bar
        assert result.entry_price == Decimal("101.20")

    def test_stop_price(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        # LONG, buffer=0: stop = low of rejection bar = 100.80
        assert result.stop_price == Decimal("100.80")

    def test_target_2r(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        # risk = entry - stop = 101.20 - 100.80 = 0.40
        # target = entry + 2 * risk = 101.20 + 0.80 = 102.00
        assert result.target_price == Decimal("102.00")

    def test_entry_timestamp(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert result.entry_timestamp_ms == _ms(9)


# ── Test 6: Repeated evaluation is deterministic ────────────────────────────

class TestDeterminism:
    def test_same_session_same_result(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        r1 = d.evaluate(sess)
        r2 = d.evaluate(sess)
        assert r1.status == r2.status
        assert r1.entry_price == r2.entry_price
        assert r1.stop_price == r2.stop_price
        assert r1.target_price == r2.target_price
        assert r1.direction == r2.direction

    def test_no_setup_is_deterministic(self):
        d = _make_detector()
        sess = _build_session_up_to(_orb_bars() + [_break_bar()])
        r1 = d.evaluate(sess)
        r2 = d.evaluate(sess)
        assert r1.status == r2.status == SignalStatus.NO_SETUP


# ── Test 7: No future bars required ─────────────────────────────────────────

class TestNoLookahead:
    def test_signal_without_future_bars(self):
        """SIGNAL appears with exactly the bars through the entry candle.
        No post-entry bars are needed."""
        d = _make_detector()
        bars = _all_bars_through_rejection()
        assert len(bars) == 10  # 5 ORB + break + 3 disp + rejection

        sess = _build_session_up_to(bars)
        result = d.evaluate(sess)
        assert result.status == SignalStatus.SIGNAL

    def test_signal_appears_only_when_entry_candle_added(self):
        """Before the entry candle → NO_SETUP.
        After adding it → SIGNAL.
        Proves no future data leaks."""
        d = _make_detector()
        bars_before = _orb_bars() + [_break_bar()] + _displacement_bars()
        sess_before = _build_session_up_to(bars_before)
        assert d.evaluate(sess_before).status == SignalStatus.NO_SETUP

        bars_after = bars_before + [_rejection_bar()]
        sess_after = _build_session_up_to(bars_after)
        assert d.evaluate(sess_after).status == SignalStatus.SIGNAL

    def test_adding_more_bars_preserves_signal(self):
        """Adding post-entry bars doesn't break the signal."""
        d = _make_detector()
        bars = _all_bars_through_rejection() + [{
            "time_ms": _ms(10),
            "open": 101.25,
            "high": 101.50,
            "low": 101.15,
            "close": 101.35,
            "volume": 1000,
        }]
        sess = _build_session_up_to(bars)
        result = d.evaluate(sess)
        assert result.status == SignalStatus.SIGNAL


# ── Test 8: No trade outcome evaluation ──────────────────────────────────────

class TestNoOutcome:
    def test_result_has_no_outcome_fields(self):
        """SignalResult does not contain trade_outcome or realized_r."""
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert not hasattr(result, "trade_outcome")
        assert not hasattr(result, "realized_r")

    def test_signal_detector_does_not_import_outcome_evaluator(self):
        """Verify that signal_detector.py does not import
        evaluate_trade_outcome."""
        import trading_lab.live.signal_detector as mod
        # Check no import of the outcome evaluator module
        assert not hasattr(mod, "evaluate_trade_outcome")
        # Check module's direct imports don't include it
        import sys
        outcome_mod = "trading_lab.trade_outcome_evaluator"
        # The module should not have caused trade_outcome_evaluator to load
        # (it may already be loaded from other tests, so check the import lines)
        import inspect
        source = inspect.getsource(mod)
        assert "from trading_lab.trade_outcome_evaluator" not in source
        assert "import trading_lab.trade_outcome_evaluator" not in source


# ── Test: Failed stage reported on NO_SETUP ──────────────────────────────────

class TestFailedStage:
    def test_no_session_reports_stage(self):
        d = _make_detector()
        result = d.evaluate(None)
        assert result.failed_stage == "NO_SESSION"

    def test_no_break_reports_stage(self):
        d = _make_detector()
        sess = _build_session_up_to(_orb_bars())
        result = d.evaluate(sess)
        assert result.failed_stage is not None
