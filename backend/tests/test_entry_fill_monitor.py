"""Tests for EntryFillMonitor — option entry fill detection for MaxBot v0.1.

All tests use mock Trade objects. No real IBKR connection.
"""

import pytest
from types import SimpleNamespace
from datetime import datetime, timezone

from trading_lab.live.entry_fill_monitor import (
    FillState,
    EntryFillResult,
    check_fill,
    FillActivator,
)
from trading_lab.live.trade_manager import DailyTradeManager, TradeResult


# ── Mock helpers ─────────────────────────────────────────────────────────────

def _mock_submission(
    broker_status="PendingSubmit",
    filled=0.0,
    remaining=1.0,
    avg_price=0.0,
    order_id=42,
    con_id=123456,
    fills=None,
):
    """Create a mock EntrySubmissionResult with embedded Trade."""
    order = SimpleNamespace(orderId=order_id, permId=999)
    order_status = SimpleNamespace(
        status=broker_status,
        filled=filled,
        remaining=remaining,
        avgFillPrice=avg_price,
    )
    trade = SimpleNamespace(
        order=order,
        orderStatus=order_status,
        fills=fills or [],
        log=[],
    )
    return SimpleNamespace(
        trade=trade,
        con_id=con_id,
        underlying_symbol="QQQ",
        right="C",
        expiration="20260811",
        strike=585.0,
        quantity=1,
        limit_price=2.70,
        order_id=order_id,
        perm_id=999,
        status=broker_status,
    )


def _filled_submission(avg_price=2.65, order_id=42, con_id=123456):
    fill_time = datetime(2026, 8, 11, 9, 45, 0, tzinfo=timezone.utc)
    fill = SimpleNamespace(time=fill_time)
    return _mock_submission(
        broker_status="Filled",
        filled=1.0,
        remaining=0.0,
        avg_price=avg_price,
        order_id=order_id,
        con_id=con_id,
        fills=[fill],
    )


# ── Test 1: PendingSubmit → PENDING ──────────────────────────────────────────

class TestPendingSubmit:
    def test_pending(self):
        r = check_fill(_mock_submission(broker_status="PendingSubmit"))
        assert r.state == FillState.PENDING


# ── Test 2: PreSubmitted → PENDING ───────────────────────────────────────────

class TestPreSubmitted:
    def test_pending(self):
        r = check_fill(_mock_submission(broker_status="PreSubmitted"))
        assert r.state == FillState.PENDING


# ── Test 3: Submitted → PENDING ─────────────────────────────────────────────

class TestSubmitted:
    def test_pending(self):
        r = check_fill(_mock_submission(broker_status="Submitted"))
        assert r.state == FillState.PENDING


# ── Test 4: Full fill → FILLED ──────────────────────────────────────────────

class TestFullFill:
    def test_filled(self):
        r = check_fill(_filled_submission())
        assert r.state == FillState.FILLED


# ── Test 5: Filled quantity preserved ────────────────────────────────────────

class TestFilledQuantity:
    def test_quantity(self):
        r = check_fill(_filled_submission())
        assert r.filled_quantity == 1.0


# ── Test 6: Average fill price preserved ─────────────────────────────────────

class TestAvgFillPrice:
    def test_price(self):
        r = check_fill(_filled_submission(avg_price=2.65))
        assert r.average_fill_price == 2.65

    def test_pending_has_none(self):
        r = check_fill(_mock_submission())
        assert r.average_fill_price is None


# ── Test 7: Remaining quantity preserved ─────────────────────────────────────

class TestRemainingQuantity:
    def test_filled_zero_remaining(self):
        r = check_fill(_filled_submission())
        assert r.remaining_quantity == 0.0

    def test_pending_has_remaining(self):
        r = check_fill(_mock_submission())
        assert r.remaining_quantity == 1.0


# ── Test 8: Order ID preserved ───────────────────────────────────────────────

class TestOrderId:
    def test_order_id(self):
        r = check_fill(_filled_submission(order_id=77))
        assert r.order_id == 77


# ── Test 9: conId preserved ──────────────────────────────────────────────────

class TestConId:
    def test_con_id(self):
        r = check_fill(_filled_submission(con_id=999888))
        assert r.con_id == 999888


# ── Test 10: Cancelled → CANCELLED ──────────────────────────────────────────

class TestCancelled:
    def test_cancelled(self):
        r = check_fill(_mock_submission(broker_status="Cancelled"))
        assert r.state == FillState.CANCELLED

    def test_api_cancelled(self):
        r = check_fill(_mock_submission(broker_status="ApiCancelled"))
        assert r.state == FillState.CANCELLED


# ── Test 11: Inactive → REJECTED ────────────────────────────────────────────

class TestRejected:
    def test_inactive(self):
        r = check_fill(_mock_submission(broker_status="Inactive"))
        assert r.state == FillState.REJECTED


# ── Test 12: Cancelled does not mutate DailyTradeManager ─────────────────────

class TestCancelledNoMutation:
    def test_no_open(self):
        mgr = DailyTradeManager()
        mgr.ensure_date("2026-08-11")
        activator = FillActivator(mgr)

        r = check_fill(_mock_submission(broker_status="Cancelled"))
        activated = activator.apply_if_filled(r)

        assert activated is False
        assert mgr.state.trades_used == 0
        assert mgr.state.has_active_trade is False


# ── Test 13: Partial fill does not activate trade ────────────────────────────

class TestPartialFill:
    def test_partial_stays_pending(self):
        sub = _mock_submission(
            broker_status="Filled",
            filled=0.5,
            remaining=0.5,
            avg_price=2.60,
        )
        r = check_fill(sub, requested_quantity=1)
        assert r.state == FillState.PENDING

    def test_partial_does_not_activate(self):
        mgr = DailyTradeManager()
        mgr.ensure_date("2026-08-11")
        activator = FillActivator(mgr)

        sub = _mock_submission(
            broker_status="Filled",
            filled=0.5,
            remaining=0.5,
        )
        r = check_fill(sub)
        activated = activator.apply_if_filled(r)

        assert activated is False
        assert mgr.state.trades_used == 0


# ── Test 14: Full fill activates DailyTradeManager ───────────────────────────

class TestFillActivation:
    def test_activates(self):
        mgr = DailyTradeManager()
        mgr.ensure_date("2026-08-11")
        activator = FillActivator(mgr)

        r = check_fill(_filled_submission())
        activated = activator.apply_if_filled(r)

        assert activated is True
        assert mgr.state.trades_used == 1
        assert mgr.state.has_active_trade is True


# ── Test 15: Trade count increments exactly once ─────────────────────────────

class TestExactlyOnce:
    def test_single_increment(self):
        mgr = DailyTradeManager()
        mgr.ensure_date("2026-08-11")
        activator = FillActivator(mgr)

        r = check_fill(_filled_submission(order_id=42))
        activator.apply_if_filled(r)

        assert mgr.state.trades_used == 1


# ── Test 16: Repeated FILLED checks do not increment twice ──────────────────

class TestIdempotent:
    def test_no_double_count(self):
        mgr = DailyTradeManager()
        mgr.ensure_date("2026-08-11")
        activator = FillActivator(mgr)

        sub = _filled_submission(order_id=42)
        r1 = check_fill(sub)
        r2 = check_fill(sub)
        r3 = check_fill(sub)

        assert activator.apply_if_filled(r1) is True
        assert activator.apply_if_filled(r2) is False
        assert activator.apply_if_filled(r3) is False

        assert mgr.state.trades_used == 1
        assert mgr.state.has_active_trade is True


# ── Test 17: Active-trade state becomes true exactly once ────────────────────

class TestActiveOnce:
    def test_active_true_once(self):
        mgr = DailyTradeManager()
        mgr.ensure_date("2026-08-11")
        activator = FillActivator(mgr)

        r = check_fill(_filled_submission())
        activator.apply_if_filled(r)
        activator.apply_if_filled(r)  # redundant

        assert mgr.state.has_active_trade is True
        assert mgr.state.trades_used == 1


# ── Test 18: Option fill price distinct from underlying TradePlan ────────────

class TestFillPriceDistinct:
    def test_fill_price_is_option_premium(self):
        """average_fill_price is the option premium, not the underlying
        strategy entry price from TradePlan."""
        r = check_fill(_filled_submission(avg_price=2.65))
        # 2.65 is clearly an option premium, not underlying 585.20
        assert r.average_fill_price == 2.65
        assert r.average_fill_price < 100  # sanity: option premium, not stock price


# ── Test 19: No exit order submitted ─────────────────────────────────────────

class TestNoExit:
    def test_no_exit_in_module(self):
        import inspect
        import trading_lab.live.entry_fill_monitor as mod
        source = inspect.getsource(mod)
        assert "placeOrder" not in source
        assert "SELL" not in source


# ── Test 20: No broker connection created ────────────────────────────────────

class TestNoConnection:
    def test_no_connect(self):
        import inspect
        import trading_lab.live.entry_fill_monitor as mod
        source = inspect.getsource(mod)
        assert ".connect(" not in source
        assert "host=" not in source


# ── Test 21: No automatic retry ──────────────────────────────────────────────

class TestNoRetry:
    def test_no_retry_in_module(self):
        import inspect
        import trading_lab.live.entry_fill_monitor as mod
        source = inspect.getsource(mod)
        # No retry/reprice functions or while-retry loops
        assert "def retry" not in source
        assert "def reprice" not in source
        assert "while True" not in source


# ── Test: Fill time preserved ────────────────────────────────────────────────

class TestFillTime:
    def test_fill_time(self):
        r = check_fill(_filled_submission())
        assert r.fill_time is not None

    def test_pending_no_fill_time(self):
        r = check_fill(_mock_submission())
        assert r.fill_time is None


# ── Test: Broker status preserved ────────────────────────────────────────────

class TestBrokerStatus:
    def test_raw_status(self):
        r = check_fill(_mock_submission(broker_status="Submitted"))
        assert r.broker_status == "Submitted"

    def test_filled_status(self):
        r = check_fill(_filled_submission())
        assert r.broker_status == "Filled"


# ── Test: Unknown status defaults to PENDING ─────────────────────────────────

class TestUnknownStatus:
    def test_unknown(self):
        r = check_fill(_mock_submission(broker_status="SomeNewStatus"))
        assert r.state == FillState.PENDING


# ── Test: Different order IDs tracked independently ──────────────────────────

class TestMultipleOrders:
    def test_two_different_fills(self):
        mgr = DailyTradeManager()
        mgr.ensure_date("2026-08-11")
        activator = FillActivator(mgr)

        r1 = check_fill(_filled_submission(order_id=42))
        activator.apply_if_filled(r1)
        assert mgr.state.trades_used == 1

        # Close the first trade to allow second
        mgr.record_trade_result(TradeResult.LOSS)

        r2 = check_fill(_filled_submission(order_id=43))
        activator.apply_if_filled(r2)
        assert mgr.state.trades_used == 2
