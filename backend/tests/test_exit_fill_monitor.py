"""Tests for exit fill monitor — confirms option exit and records WIN/LOSS.

All tests use mock Trade objects. No real IBKR connection.
"""

import pytest
from types import SimpleNamespace
from datetime import datetime, timezone

from trading_lab.live.exit_fill_monitor import (
    ExitFillState,
    ExitFillResult,
    check_exit_fill,
    ExitResultActivator,
)
from trading_lab.live.trade_manager import DailyTradeManager, TradeResult


# ── Mock helpers ─────────────────────────────────────────────────────────────

def _mock_exit_submission(
    exit_reason="TARGET",
    broker_status="PendingSubmit",
    filled=0.0,
    remaining=1.0,
    avg_price=0.0,
    exit_order_id=55,
    entry_order_id=42,
    con_id=123456,
    fills=None,
):
    order = SimpleNamespace(orderId=exit_order_id, permId=888)
    order_status = SimpleNamespace(
        status=broker_status, filled=filled,
        remaining=remaining, avgFillPrice=avg_price,
    )
    trade = SimpleNamespace(
        order=order, orderStatus=order_status,
        fills=fills or [], log=[],
    )
    return SimpleNamespace(
        trade=trade,
        exit_reason=exit_reason,
        entry_order_id=entry_order_id,
        con_id=con_id,
        underlying_stop_price=584.70,
        underlying_target_price=586.20,
    )


def _filled_exit(exit_reason="TARGET", avg_price=3.10, exit_order_id=55,
                  entry_order_id=42):
    fill_time = datetime(2026, 8, 11, 10, 15, 0, tzinfo=timezone.utc)
    fill = SimpleNamespace(time=fill_time)
    return _mock_exit_submission(
        exit_reason=exit_reason, broker_status="Filled",
        filled=1.0, remaining=0.0, avg_price=avg_price,
        exit_order_id=exit_order_id, entry_order_id=entry_order_id,
        fills=[fill],
    )


def _active_manager():
    """Manager with date set and one trade already opened."""
    m = DailyTradeManager()
    m.ensure_date("2026-08-11")
    m.record_trade_open()
    return m


# ── Test 1: PendingSubmit → PENDING ──────────────────────────────────────────

class TestPending:
    def test_pending_submit(self):
        r = check_exit_fill(_mock_exit_submission(broker_status="PendingSubmit"))
        assert r.state == ExitFillState.PENDING

    def test_submitted(self):
        r = check_exit_fill(_mock_exit_submission(broker_status="Submitted"))
        assert r.state == ExitFillState.PENDING


# ── Test 3: Full TARGET exit fill → FILLED ───────────────────────────────────

class TestTargetFilled:
    def test_filled(self):
        r = check_exit_fill(_filled_exit(exit_reason="TARGET"))
        assert r.state == ExitFillState.FILLED
        assert r.exit_reason == "TARGET"


# ── Test 4: Full STOP exit fill → FILLED ────────────────────────────────────

class TestStopFilled:
    def test_filled(self):
        r = check_exit_fill(_filled_exit(exit_reason="STOP"))
        assert r.state == ExitFillState.FILLED
        assert r.exit_reason == "STOP"


# ── Test 5: TARGET → WIN ────────────────────────────────────────────────────

class TestTargetWin:
    def test_win(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        r = check_exit_fill(_filled_exit(exit_reason="TARGET"))
        activator.apply_if_filled(r)
        assert m.state.wins == 1
        assert m.state.has_active_trade is False


# ── Test 6: STOP → LOSS ─────────────────────────────────────────────────────

class TestStopLoss:
    def test_loss(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        r = check_exit_fill(_filled_exit(exit_reason="STOP"))
        activator.apply_if_filled(r)
        assert m.state.losses == 1
        assert m.state.has_active_trade is False


# ── Test 7: Option exit premium preserved ────────────────────────────────────

class TestExitPremium:
    def test_premium(self):
        r = check_exit_fill(_filled_exit(avg_price=3.10))
        assert r.average_exit_fill_price == 3.10

    def test_pending_none(self):
        r = check_exit_fill(_mock_exit_submission())
        assert r.average_exit_fill_price is None


# ── Test 8: WIN/LOSS not from premium P&L ────────────────────────────────────

class TestNotFromPremium:
    def test_target_is_win_regardless_of_premium(self):
        """Even if option premium dropped, TARGET = WIN."""
        m = _active_manager()
        activator = ExitResultActivator(m)
        # Low exit premium doesn't matter
        r = check_exit_fill(_filled_exit(exit_reason="TARGET", avg_price=0.05))
        activator.apply_if_filled(r)
        assert m.state.wins == 1

    def test_stop_is_loss_regardless_of_premium(self):
        """Even if option premium rose, STOP = LOSS."""
        m = _active_manager()
        activator = ExitResultActivator(m)
        r = check_exit_fill(_filled_exit(exit_reason="STOP", avg_price=10.00))
        activator.apply_if_filled(r)
        assert m.state.losses == 1


# ── Test 9: Partial fill does not close trade ────────────────────────────────

class TestPartialFill:
    def test_partial(self):
        sub = _mock_exit_submission(
            broker_status="Filled", filled=0.5, remaining=0.5, avg_price=2.0,
        )
        r = check_exit_fill(sub)
        assert r.state == ExitFillState.PENDING

    def test_partial_no_mutation(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        sub = _mock_exit_submission(
            broker_status="Filled", filled=0.5, remaining=0.5,
        )
        r = check_exit_fill(sub)
        assert activator.apply_if_filled(r) is False
        assert m.state.has_active_trade is True


# ── Test 10: Cancelled exit does not close trade ─────────────────────────────

class TestCancelled:
    def test_cancelled(self):
        r = check_exit_fill(_mock_exit_submission(broker_status="Cancelled"))
        assert r.state == ExitFillState.CANCELLED

    def test_no_mutation(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        r = check_exit_fill(_mock_exit_submission(broker_status="Cancelled"))
        assert activator.apply_if_filled(r) is False
        assert m.state.has_active_trade is True


# ── Test 11: Rejected exit does not close trade ──────────────────────────────

class TestRejected:
    def test_rejected(self):
        r = check_exit_fill(_mock_exit_submission(broker_status="Inactive"))
        assert r.state == ExitFillState.REJECTED

    def test_no_mutation(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        r = check_exit_fill(_mock_exit_submission(broker_status="Inactive"))
        assert activator.apply_if_filled(r) is False
        assert m.state.has_active_trade is True


# ── Test 12: TARGET fill clears active trade ─────────────────────────────────

class TestTargetClearsActive:
    def test_clears(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        r = check_exit_fill(_filled_exit(exit_reason="TARGET"))
        activator.apply_if_filled(r)
        assert m.state.has_active_trade is False
        assert m.state.trades_used == 1


# ── Test 13: STOP fill clears active trade ───────────────────────────────────

class TestStopClearsActive:
    def test_clears(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        r = check_exit_fill(_filled_exit(exit_reason="STOP"))
        activator.apply_if_filled(r)
        assert m.state.has_active_trade is False


# ── Test 14: First TARGET win sets day_finished ──────────────────────────────

class TestTargetFinishesDay:
    def test_day_finished(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        r = check_exit_fill(_filled_exit(exit_reason="TARGET"))
        activator.apply_if_filled(r)
        assert m.state.day_finished is True
        assert m.state.can_trade is False


# ── Test 15: First LOSS permits second trade ─────────────────────────────────

class TestLossPermitsSecond:
    def test_can_trade_after_loss(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        r = check_exit_fill(_filled_exit(exit_reason="STOP"))
        activator.apply_if_filled(r)
        assert m.state.day_finished is False
        assert m.can_trade is True


# ── Test 16: Second LOSS finishes day ────────────────────────────────────────

class TestSecondLossFinishes:
    def test_finished(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        # First trade: LOSS
        r1 = check_exit_fill(_filled_exit(exit_reason="STOP", exit_order_id=55))
        activator.apply_if_filled(r1)
        # Second trade
        m.record_trade_open()
        r2 = check_exit_fill(_filled_exit(exit_reason="STOP", exit_order_id=66))
        activator.apply_if_filled(r2)
        assert m.state.day_finished is True
        assert m.state.losses == 2
        assert m.state.can_trade is False


# ── Test 17: Repeated fill is idempotent ─────────────────────────────────────

class TestIdempotent:
    def test_no_double_record(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        sub = _filled_exit(exit_reason="TARGET", exit_order_id=55)
        r1 = check_exit_fill(sub)
        r2 = check_exit_fill(sub)
        r3 = check_exit_fill(sub)
        assert activator.apply_if_filled(r1) is True
        assert activator.apply_if_filled(r2) is False
        assert activator.apply_if_filled(r3) is False
        assert m.state.wins == 1


# ── Test 18: record_trade_result called exactly once ─────────────────────────

class TestExactlyOnce:
    def test_once(self):
        m = _active_manager()
        activator = ExitResultActivator(m)
        r = check_exit_fill(_filled_exit(exit_reason="STOP"))
        activator.apply_if_filled(r)
        activator.apply_if_filled(r)
        assert m.state.losses == 1


# ── Test 19: entry_order_id preserved ────────────────────────────────────────

class TestEntryOrderId:
    def test_preserved(self):
        r = check_exit_fill(_filled_exit(entry_order_id=42))
        assert r.entry_order_id == 42


# ── Test 20: exit_order_id preserved ─────────────────────────────────────────

class TestExitOrderId:
    def test_preserved(self):
        r = check_exit_fill(_filled_exit(exit_order_id=55))
        assert r.exit_order_id == 55


# ── Test 21: conId preserved ─────────────────────────────────────────────────

class TestConId:
    def test_preserved(self):
        r = check_exit_fill(_mock_exit_submission(con_id=999))
        assert r.con_id == 999


# ── Test 22: No broker connection ────────────────────────────────────────────

class TestNoConnection:
    def test_no_connect(self):
        import inspect
        import trading_lab.live.exit_fill_monitor as mod
        source = inspect.getsource(mod)
        assert ".connect(" not in source
        assert "host=" not in source


# ── Test 23: No order submission ─────────────────────────────────────────────

class TestNoSubmission:
    def test_no_place_order(self):
        import inspect
        import trading_lab.live.exit_fill_monitor as mod
        source = inspect.getsource(mod)
        assert "placeOrder" not in source


# ── Test 24: No automatic retry ──────────────────────────────────────────────

class TestNoRetry:
    def test_no_retry(self):
        import inspect
        import trading_lab.live.exit_fill_monitor as mod
        source = inspect.getsource(mod)
        assert "def retry" not in source
        assert "while True" not in source


# ── Test 25: No option-P&L-based WIN/LOSS ────────────────────────────────────

class TestNoPnlLogic:
    def test_no_pnl_calculation(self):
        import inspect
        import trading_lab.live.exit_fill_monitor as mod
        source = inspect.getsource(mod)
        assert "pnl" not in source.lower()
        assert "profit" not in source.lower()


# ── Test: Fill time preserved ────────────────────────────────────────────────

class TestFillTime:
    def test_fill_time(self):
        r = check_exit_fill(_filled_exit())
        assert r.fill_time is not None

    def test_pending_no_fill_time(self):
        r = check_exit_fill(_mock_exit_submission())
        assert r.fill_time is None


# ── Test: Underlying levels preserved ────────────────────────────────────────

class TestUnderlyingLevels:
    def test_levels(self):
        r = check_exit_fill(_mock_exit_submission())
        assert r.underlying_stop_price == 584.70
        assert r.underlying_target_price == 586.20
