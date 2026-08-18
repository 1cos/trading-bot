"""Tests for exit execution safety + consumed_setup_keys verification.

PART A: Exit orders must have explicit tif=DAY and openClose=C.
PART B: consumed_setup_keys blocks re-entry when running correct code.
"""

from unittest.mock import MagicMock, patch
import pytest

from trading_lab.live.option_exit_executor import OptionExitExecutor
from trading_lab.live.underlying_exit_monitor import ExitState
from ib_insync import MarketOrder


class TestExitOrderConstruction:
    """Verify exit SELL MARKET has explicit tif and openClose."""

    def test_exit_order_has_tif_day(self):
        ib = MagicMock()
        trade = MagicMock()
        trade.order.orderId = 99
        trade.order.permId = 199
        trade.orderStatus.status = "Submitted"
        ib.placeOrder.return_value = trade

        xe = OptionExitExecutor(ib)
        trigger = MagicMock()
        trigger.state = ExitState.STOP_TRIGGERED
        trigger.stop_price = 100.0
        trigger.target_price = 98.0
        trigger.trigger_bar_time_ms = 1000

        xe.submit_exit(
            qualified_contract=MagicMock(),
            exit_trigger=trigger,
            entry_order_id=42,
            con_id=12345,
            right="P",
            expiration="20260115",
            strike=100.0,
        )

        # Verify the order passed to placeOrder
        call_args = ib.placeOrder.call_args
        order = call_args[0][1]  # second positional arg
        assert order.action == "SELL"
        assert order.tif == "DAY"
        assert order.openClose == "C"

    def test_exit_order_quantity_matches(self):
        ib = MagicMock()
        trade = MagicMock()
        trade.order.orderId = 99
        trade.order.permId = 199
        trade.orderStatus.status = "Submitted"
        ib.placeOrder.return_value = trade

        xe = OptionExitExecutor(ib)
        trigger = MagicMock()
        trigger.state = ExitState.TARGET_TRIGGERED
        trigger.stop_price = 100.0
        trigger.target_price = 98.0
        trigger.trigger_bar_time_ms = 1000

        xe.submit_exit(
            qualified_contract=MagicMock(),
            exit_trigger=trigger,
            entry_order_id=42,
            quantity=1,
        )

        order = ib.placeOrder.call_args[0][1]
        assert order.totalQuantity == 1


class TestEntryOrderConstruction:
    """Verify entry BUY LIMIT has explicit tif and openClose."""

    def test_entry_order_has_tif_and_open_close(self):
        from trading_lab.live.ibkr_option_executor import IBKROptionExecutor
        from trading_lab.live.option_order_builder import OptionEntryOrderSpec
        ib = MagicMock()
        trade = MagicMock()
        trade.order.orderId = 42
        trade.order.permId = 142
        trade.orderStatus.status = "Submitted"
        ib.placeOrder.return_value = trade

        ee = IBKROptionExecutor(ib)
        spec = MagicMock(spec=OptionEntryOrderSpec)
        spec.qualified_contract = MagicMock()
        spec.limit_price = 1.50
        spec.action = "BUY"
        spec.order_type = "LMT"
        spec.quantity = 1

        ee.submit_entry(spec)

        order = ib.placeOrder.call_args[0][1]
        assert order.action == "BUY"
        assert order.tif == "DAY"
        assert order.openClose == "O"


class TestConsumedSetupKeysAudit:
    """Verify consumed_setup_keys mechanism works correctly."""

    def test_consumed_setup_blocks_reentry(self):
        """After setup consumed, same setup_key is rejected."""
        from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator
        from trading_lab.live.signal_detector import SignalResult, SignalStatus

        sb = MagicMock()
        sb.current_session.return_value = {
            "date": "2026-01-15",
            "candles": [{"time_ms": 1000, "open": 100, "high": 101,
                         "low": 99, "close": 100.5, "volume": 1000}],
        }
        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", signal_key="SHORT:1000:5000",
            pipeline_stage="SIGNAL", stage_context={"break_bar_index": 5},
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        sd = MagicMock()
        sd.evaluate.return_value = sig
        tm = MagicMock()
        tm.can_trade = True

        orch = MaxBotTradeOrchestrator(
            underlying_symbol="QQQ", direction="SHORT",
            tick_size=0.01, session_builder=sb,
            signal_detector=sd, trade_manager=tm,
            option_selector=MagicMock(), entry_executor=MagicMock(),
            exit_executor=MagicMock(),
        )

        # Pre-consume setup and signal
        orch._consumed_setups.add("SHORT:1000")
        orch._consumed_signals.add("SHORT:1000:5000")

        bar = {"time_ms": 2000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)

        # Should be blocked by BOTH consumed checks
        assert not orch.has_pending_signal

    def test_consumed_keys_survive_entry_cancel(self):
        """_clear_active_trade does NOT clear consumed sets."""
        from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator

        sb = MagicMock()
        sb.current_session.return_value = {"date": "2026-01-15", "candles": []}
        sd = MagicMock()
        tm = MagicMock()

        orch = MaxBotTradeOrchestrator(
            underlying_symbol="QQQ", direction="SHORT",
            tick_size=0.01, session_builder=sb,
            signal_detector=sd, trade_manager=tm,
            option_selector=MagicMock(), entry_executor=MagicMock(),
            exit_executor=MagicMock(),
        )
        orch._consumed_setups.add("SHORT:1000")
        orch._consumed_signals.add("SHORT:1000:5000")

        orch._clear_active_trade()

        # Consumed sets MUST survive
        assert "SHORT:1000" in orch._consumed_setups
        assert "SHORT:1000:5000" in orch._consumed_signals
