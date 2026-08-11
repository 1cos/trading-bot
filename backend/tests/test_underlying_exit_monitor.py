"""Tests for UnderlyingExitMonitor — structural exit triggers for MaxBot v0.1.

All tests use synthetic candle dicts. No IBKR connection.
"""

import pytest

from trading_lab.live.underlying_exit_monitor import (
    ExitState,
    ExitTriggerResult,
    UnderlyingExitMonitor,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

T0 = 1786455000000  # activation time
T1 = T0 + 60_000   # first bar after activation
T2 = T0 + 120_000
T3 = T0 + 180_000

BEFORE = T0 - 60_000  # pre-activation


def _bar(time_ms, open_=100.0, high=101.0, low=99.0, close=100.5, volume=1000):
    return {
        "time_ms": time_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _long_monitor(stop=99.0, target=103.0, activation=T0):
    return UnderlyingExitMonitor("LONG", stop, target, activation)


def _short_monitor(stop=103.0, target=99.0, activation=T0):
    return UnderlyingExitMonitor("SHORT", stop, target, activation)


# ── Test 1: LONG normal bar → HOLD ──────────────────────────────────────────

class TestLongHold:
    def test_hold(self):
        m = _long_monitor()
        r = m.evaluate_bar(_bar(T1, high=102.0, low=99.5))
        assert r.state == ExitState.HOLD


# ── Test 2: LONG low touches stop → STOP_TRIGGERED ──────────────────────────

class TestLongStop:
    def test_stop(self):
        m = _long_monitor(stop=99.0)
        r = m.evaluate_bar(_bar(T1, high=101.0, low=98.5))
        assert r.state == ExitState.STOP_TRIGGERED

    def test_stop_bar_preserved(self):
        m = _long_monitor(stop=99.0)
        r = m.evaluate_bar(_bar(T1, open_=100.0, high=101.0, low=98.5, close=99.5))
        assert r.trigger_bar_time_ms == T1
        assert r.trigger_bar_low == 98.5


# ── Test 3: LONG high touches target → TARGET_TRIGGERED ─────────────────────

class TestLongTarget:
    def test_target(self):
        m = _long_monitor(target=103.0)
        r = m.evaluate_bar(_bar(T1, high=103.5, low=100.0))
        assert r.state == ExitState.TARGET_TRIGGERED


# ── Test 4: SHORT normal bar → HOLD ─────────────────────────────────────────

class TestShortHold:
    def test_hold(self):
        m = _short_monitor()
        r = m.evaluate_bar(_bar(T1, high=102.0, low=100.0))
        assert r.state == ExitState.HOLD


# ── Test 5: SHORT high touches stop → STOP_TRIGGERED ────────────────────────

class TestShortStop:
    def test_stop(self):
        m = _short_monitor(stop=103.0)
        r = m.evaluate_bar(_bar(T1, high=103.5, low=100.0))
        assert r.state == ExitState.STOP_TRIGGERED


# ── Test 6: SHORT low touches target → TARGET_TRIGGERED ─────────────────────

class TestShortTarget:
    def test_target(self):
        m = _short_monitor(target=99.0)
        r = m.evaluate_bar(_bar(T1, high=102.0, low=98.5))
        assert r.state == ExitState.TARGET_TRIGGERED


# ── Test 7: Exact equality at stop triggers ──────────────────────────────────

class TestExactStop:
    def test_long_exact(self):
        m = _long_monitor(stop=99.0)
        r = m.evaluate_bar(_bar(T1, high=101.0, low=99.0))
        assert r.state == ExitState.STOP_TRIGGERED

    def test_short_exact(self):
        m = _short_monitor(stop=103.0)
        r = m.evaluate_bar(_bar(T1, high=103.0, low=100.0))
        assert r.state == ExitState.STOP_TRIGGERED


# ── Test 8: Exact equality at target triggers ────────────────────────────────

class TestExactTarget:
    def test_long_exact(self):
        m = _long_monitor(target=103.0)
        r = m.evaluate_bar(_bar(T1, high=103.0, low=100.0))
        assert r.state == ExitState.TARGET_TRIGGERED

    def test_short_exact(self):
        m = _short_monitor(target=99.0)
        r = m.evaluate_bar(_bar(T1, high=102.0, low=99.0))
        assert r.state == ExitState.TARGET_TRIGGERED


# ── Test 9: LONG same bar stop+target → STOP_TRIGGERED ──────────────────────

class TestLongSameBar:
    def test_conservative_stop(self):
        m = _long_monitor(stop=99.0, target=103.0)
        r = m.evaluate_bar(_bar(T1, high=103.5, low=98.5))
        assert r.state == ExitState.STOP_TRIGGERED
        assert r.same_bar_ambiguity is True


# ── Test 10: SHORT same bar stop+target → STOP_TRIGGERED ────────────────────

class TestShortSameBar:
    def test_conservative_stop(self):
        m = _short_monitor(stop=103.0, target=99.0)
        r = m.evaluate_bar(_bar(T1, high=103.5, low=98.5))
        assert r.state == ExitState.STOP_TRIGGERED
        assert r.same_bar_ambiguity is True


# ── Test 11: Same-bar ambiguity flag preserved ───────────────────────────────

class TestAmbiguityFlag:
    def test_no_ambiguity_on_stop_only(self):
        m = _long_monitor(stop=99.0, target=103.0)
        r = m.evaluate_bar(_bar(T1, high=101.0, low=98.5))
        assert r.same_bar_ambiguity is False

    def test_no_ambiguity_on_target_only(self):
        m = _long_monitor(stop=99.0, target=103.0)
        r = m.evaluate_bar(_bar(T1, high=103.5, low=100.0))
        assert r.same_bar_ambiguity is False

    def test_hold_no_ambiguity(self):
        m = _long_monitor()
        r = m.evaluate_bar(_bar(T1, high=102.0, low=100.0))
        assert r.same_bar_ambiguity is False


# ── Test 12: Pre-activation bar ignored ──────────────────────────────────────

class TestPreActivation:
    def test_before_activation(self):
        m = _long_monitor(stop=99.0, activation=T0)
        r = m.evaluate_bar(_bar(BEFORE, high=101.0, low=98.0))
        assert r.state == ExitState.HOLD

    def test_at_activation_evaluated(self):
        """Bar at activation_time_ms is evaluated (>= not >)."""
        m = _long_monitor(stop=99.0, activation=T0)
        r = m.evaluate_bar(_bar(T0, high=101.0, low=98.0))
        assert r.state == ExitState.STOP_TRIGGERED


# ── Test 13: First eligible post-fill bar ────────────────────────────────────

class TestFirstEligible:
    def test_pre_ignored_post_evaluated(self):
        m = _long_monitor(stop=99.0, target=103.0, activation=T1)
        # Bar before activation — would trigger stop but ignored
        r0 = m.evaluate_bar(_bar(T0, high=101.0, low=98.0))
        assert r0.state == ExitState.HOLD

        # First eligible bar — normal, no trigger
        r1 = m.evaluate_bar(_bar(T1, high=102.0, low=100.0))
        assert r1.state == ExitState.HOLD

        # Second eligible bar — target hit
        r2 = m.evaluate_bar(_bar(T2, high=103.5, low=100.0))
        assert r2.state == ExitState.TARGET_TRIGGERED


# ── Test 14: Repeated terminal evaluation is idempotent ──────────────────────

class TestIdempotent:
    def test_same_result_repeated(self):
        m = _long_monitor(target=103.0)
        r1 = m.evaluate_bar(_bar(T1, high=103.5, low=100.0))
        assert r1.state == ExitState.TARGET_TRIGGERED

        r2 = m.evaluate_bar(_bar(T2, high=104.0, low=101.0))
        assert r2.state == ExitState.TARGET_TRIGGERED
        assert r2.trigger_bar_time_ms == T1  # original trigger bar

        r3 = m.evaluate_bar(_bar(T3, high=90.0, low=85.0))
        assert r3.state == ExitState.TARGET_TRIGGERED


# ── Test 15: Target cannot become stop after terminal target ─────────────────

class TestNoFlip:
    def test_target_stays_target(self):
        m = _long_monitor(stop=99.0, target=103.0)
        r1 = m.evaluate_bar(_bar(T1, high=103.5, low=100.0))
        assert r1.state == ExitState.TARGET_TRIGGERED

        # Later bar would hit stop — but already terminal
        r2 = m.evaluate_bar(_bar(T2, high=100.0, low=97.0))
        assert r2.state == ExitState.TARGET_TRIGGERED


# ── Test 16: Stop cannot become target after terminal stop ───────────────────

class TestNoFlipStop:
    def test_stop_stays_stop(self):
        m = _long_monitor(stop=99.0, target=103.0)
        r1 = m.evaluate_bar(_bar(T1, high=101.0, low=98.0))
        assert r1.state == ExitState.STOP_TRIGGERED

        r2 = m.evaluate_bar(_bar(T2, high=105.0, low=101.0))
        assert r2.state == ExitState.STOP_TRIGGERED


# ── Test 17: Structural levels preserved exactly ─────────────────────────────

class TestLevelsPreserved:
    def test_long_levels(self):
        m = _long_monitor(stop=584.70, target=586.20)
        r = m.evaluate_bar(_bar(T1, high=585.0, low=584.9))
        assert r.stop_price == 584.70
        assert r.target_price == 586.20

    def test_short_levels(self):
        m = _short_monitor(stop=586.20, target=584.70)
        r = m.evaluate_bar(_bar(T1, high=585.5, low=585.0))
        assert r.stop_price == 586.20
        assert r.target_price == 584.70


# ── Test 18: No option premium used ──────────────────────────────────────────

class TestNoOptionPremium:
    def test_no_premium_fields(self):
        m = _long_monitor()
        r = m.evaluate_bar(_bar(T1, high=103.5, low=100.0))
        assert not hasattr(r, "option_stop")
        assert not hasattr(r, "option_target")
        assert not hasattr(r, "premium")


# ── Test 19: No broker interaction ───────────────────────────────────────────

class TestNoBroker:
    def test_no_ib_import(self):
        import inspect
        import trading_lab.live.underlying_exit_monitor as mod
        source = inspect.getsource(mod)
        assert "ib_insync" not in source
        assert "placeOrder" not in source


# ── Test 20: No SELL order submitted ─────────────────────────────────────────

class TestNoSell:
    def test_no_sell(self):
        import inspect
        import trading_lab.live.underlying_exit_monitor as mod
        source = inspect.getsource(mod)
        assert "SELL" not in source


# ── Test 21: No DailyTradeManager WIN/LOSS mutation ──────────────────────────

class TestNoTradeResult:
    def test_no_record_trade_result(self):
        import inspect
        import trading_lab.live.underlying_exit_monitor as mod
        source = inspect.getsource(mod)
        assert "record_trade_result" not in source
        assert "TradeResult" not in source
        assert "WIN" not in source
        assert "LOSS" not in source


# ── Test: Invalid direction ──────────────────────────────────────────────────

class TestInvalidDirection:
    def test_bad_direction(self):
        with pytest.raises(ValueError, match="direction"):
            UnderlyingExitMonitor("BOTH", 99.0, 103.0, T0)


# ── Test: Direction preserved in result ──────────────────────────────────────

class TestDirectionPreserved:
    def test_long(self):
        m = _long_monitor()
        r = m.evaluate_bar(_bar(T1))
        assert r.direction == "LONG"

    def test_short(self):
        m = _short_monitor()
        r = m.evaluate_bar(_bar(T1))
        assert r.direction == "SHORT"
