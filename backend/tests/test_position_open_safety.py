"""Audit tests — POSITION_OPEN requires positive fill evidence.

Verifies that no code path can transition to POSITION_OPEN without
confirmed fill quantity > 0.
"""

from unittest.mock import MagicMock
import pytest

from trading_lab.live.trade_orchestrator import (
    MaxBotTradeOrchestrator,
    LifecycleState,
)
from trading_lab.live.entry_fill_monitor import (
    FillState,
    EntryFillResult,
    check_fill,
)


def _make_orchestrator():
    sb = MagicMock()
    sb.current_session.return_value = {"date": "2026-01-15", "candles": [{"time_ms": 1000}]}
    sd = MagicMock()
    sd.last_result = None
    tm = MagicMock()
    tm.can_trade = True
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
        session_builder=sb, signal_detector=sd, trade_manager=tm,
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(),
    )
    # Put into ENTRY_SUBMITTED state
    orch._lifecycle = LifecycleState.ENTRY_SUBMITTED
    orch._entry_submission = MagicMock()
    orch._resolved_direction = "LONG"
    orch._underlying_triggers = MagicMock(
        stop_price=99.0, target_price=103.0,
    )
    return orch


def _mock_fill(state, filled=0.0, status="Cancelled"):
    return EntryFillResult(
        state=state, order_id=75, con_id=12345,
        filled_quantity=filled, remaining_quantity=1.0 - filled,
        average_fill_price=0.12 if filled > 0 else None,
        broker_status=status, fill_time=None,
    )


class TestCancelledNeverPositionOpen:
    def test_cancelled_goes_to_waiting(self):
        orch = _make_orchestrator()
        with MagicMock() as mock_check:
            from unittest.mock import patch
            with patch("trading_lab.live.trade_orchestrator.check_fill",
                       return_value=_mock_fill(FillState.CANCELLED)):
                orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.WAITING_FOR_SIGNAL

    def test_rejected_goes_to_waiting(self):
        orch = _make_orchestrator()
        from unittest.mock import patch
        with patch("trading_lab.live.trade_orchestrator.check_fill",
                   return_value=_mock_fill(FillState.REJECTED, status="Inactive")):
            orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.WAITING_FOR_SIGNAL


class TestFilledRequiresPositiveQuantity:
    def test_filled_with_quantity_goes_to_position_open(self):
        orch = _make_orchestrator()
        from unittest.mock import patch
        with patch("trading_lab.live.trade_orchestrator.check_fill",
                   return_value=_mock_fill(FillState.FILLED, filled=1.0, status="Filled")):
            orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.POSITION_OPEN

    def test_filled_with_zero_quantity_rejected(self):
        """SAFETY: Filled status but filled=0 → treat as cancelled."""
        orch = _make_orchestrator()
        from unittest.mock import patch
        with patch("trading_lab.live.trade_orchestrator.check_fill",
                   return_value=_mock_fill(FillState.FILLED, filled=0.0, status="Filled")):
            orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.WAITING_FOR_SIGNAL

    def test_pending_stays_entry_submitted(self):
        orch = _make_orchestrator()
        from unittest.mock import patch
        with patch("trading_lab.live.trade_orchestrator.check_fill",
                   return_value=_mock_fill(FillState.PENDING, status="Submitted")):
            orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED


class TestCheckFillMapping:
    """Verify the IBKR status → FillState mapping."""

    def test_cancelled_maps_correctly(self):
        sub = MagicMock()
        sub.trade.orderStatus.status = "Cancelled"
        sub.trade.orderStatus.filled = 0.0
        sub.trade.orderStatus.remaining = 0.0
        sub.trade.orderStatus.avgFillPrice = 0.0
        sub.trade.order.orderId = 75
        sub.con_id = 12345
        sub.trade.fills = []
        result = check_fill(sub)
        assert result.state == FillState.CANCELLED

    def test_inactive_maps_to_rejected(self):
        sub = MagicMock()
        sub.trade.orderStatus.status = "Inactive"
        sub.trade.orderStatus.filled = 0.0
        sub.trade.orderStatus.remaining = 1.0
        sub.trade.orderStatus.avgFillPrice = 0.0
        sub.trade.order.orderId = 75
        sub.con_id = 12345
        sub.trade.fills = []
        result = check_fill(sub)
        assert result.state == FillState.REJECTED

    def test_filled_with_zero_qty_becomes_pending(self):
        """Filled status but filled=0 → PENDING (not enough quantity)."""
        sub = MagicMock()
        sub.trade.orderStatus.status = "Filled"
        sub.trade.orderStatus.filled = 0.0
        sub.trade.orderStatus.remaining = 1.0
        sub.trade.orderStatus.avgFillPrice = 0.0
        sub.trade.order.orderId = 75
        sub.con_id = 12345
        sub.trade.fills = []
        result = check_fill(sub)
        # check_fill requires filled >= requested_quantity (1)
        # 0 < 1 → stays PENDING
        assert result.state == FillState.PENDING
