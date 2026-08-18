"""Tests for T19A — event loop reentrancy fix.

Covers:
1. ExecutionQueue mechanics (FIFO, dedup, per-symbol isolation)
2. Callback does not execute IBKR sync calls
3. Signal produces ExecutionWorkItem (enqueued)
4. Work item processed outside callback
5. No nested run_until_complete
6. One signal → one execution
7. Duplicate signal suppression
8. Execution error isolation per symbol
9. Callback continues after execution error
10. No double signal evaluation
11. last_signal_result updated correctly
12. OBSERVE_ONLY path semantically unchanged
13. PAPER_EXECUTE path semantically unchanged
"""

import time
from unittest.mock import MagicMock, patch, call

import pytest

from trading_lab.live.execution_queue import (
    ExecutionQueue,
    ExecutionWorkItem,
    WorkItemStatus,
    WorkItemType,
)
from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)
from trading_lab.live.dual_signal_detector import DualSignalDetector


# ═════════════════════════════════════════════════════════════════════════════
# ExecutionQueue unit tests
# ═════════════════════════════════════════════════════════════════════════════


class TestExecutionQueue:
    """Core queue mechanics."""

    def _make_item(self, symbol="SPY", bar_time_ms=1000):
        return ExecutionWorkItem(
            symbol=symbol,
            work_type=WorkItemType.SIGNAL_EXECUTION,
            signal_result=None,
            bar_time_ms=bar_time_ms,
        )

    def test_enqueue_returns_true(self):
        q = ExecutionQueue()
        item = self._make_item()
        assert q.enqueue(item) is True
        assert q.pending_count == 1

    def test_duplicate_suppressed(self):
        """Same symbol + bar_time is rejected."""
        q = ExecutionQueue()
        item1 = self._make_item("SPY", 1000)
        item2 = self._make_item("SPY", 1000)
        assert q.enqueue(item1) is True
        assert q.enqueue(item2) is False
        assert q.pending_count == 1

    def test_different_bar_times_accepted(self):
        q = ExecutionQueue()
        assert q.enqueue(self._make_item("SPY", 1000)) is True
        assert q.enqueue(self._make_item("SPY", 2000)) is True
        assert q.pending_count == 2

    def test_different_symbols_accepted(self):
        q = ExecutionQueue()
        assert q.enqueue(self._make_item("SPY", 1000)) is True
        assert q.enqueue(self._make_item("QQQ", 1000)) is True
        assert q.pending_count == 2

    def test_drain_fifo_order(self):
        q = ExecutionQueue()
        q.enqueue(self._make_item("SPY", 1000))
        q.enqueue(self._make_item("QQQ", 1000))
        q.enqueue(self._make_item("AMZN", 1000))
        items = q.drain()
        assert [i.symbol for i in items] == ["SPY", "QQQ", "AMZN"]

    def test_drain_marks_active(self):
        q = ExecutionQueue()
        q.enqueue(self._make_item("SPY", 1000))
        items = q.drain()
        assert items[0].status == WorkItemStatus.STARTED
        assert "SPY" in q.active_symbols

    def test_active_symbol_blocks_second_item(self):
        """While SPY is active, another SPY item is rejected on enqueue."""
        q = ExecutionQueue()
        q.enqueue(self._make_item("SPY", 1000))
        q.drain()  # SPY now active
        assert q.enqueue(self._make_item("SPY", 2000)) is False

    def test_complete_releases_symbol(self):
        q = ExecutionQueue()
        q.enqueue(self._make_item("SPY", 1000))
        items = q.drain()
        q.complete(items[0])
        assert "SPY" not in q.active_symbols
        assert items[0].status == WorkItemStatus.COMPLETED

    def test_fail_releases_symbol(self):
        q = ExecutionQueue()
        q.enqueue(self._make_item("SPY", 1000))
        items = q.drain()
        q.fail(items[0], "option chain empty")
        assert "SPY" not in q.active_symbols
        assert items[0].status == WorkItemStatus.FAILED
        assert items[0].error == "option chain empty"

    def test_error_isolation_per_symbol(self):
        """Failing one symbol does not block others."""
        q = ExecutionQueue()
        q.enqueue(self._make_item("SPY", 1000))
        q.enqueue(self._make_item("QQQ", 1000))
        items = q.drain()
        # Fail SPY, complete QQQ
        q.fail(items[0], "error")
        q.complete(items[1])
        # Both released
        assert q.active_symbols == set()

    def test_clear(self):
        q = ExecutionQueue()
        q.enqueue(self._make_item("SPY", 1000))
        q.clear()
        assert q.pending_count == 0


# ═════════════════════════════════════════════════════════════════════════════
# Signal detector last_result tests
# ═════════════════════════════════════════════════════════════════════════════


class TestSignalDetectorLastResult:
    """Verify last_result is cached after evaluate."""

    def test_last_result_none_before_first_call(self):
        sd = LiveSignalDetector(
            symbol="SPY", direction="LONG", tick_size=0.01,
        )
        assert sd.last_result is None

    def test_last_result_set_after_evaluate(self):
        sd = LiveSignalDetector(
            symbol="SPY", direction="LONG", tick_size=0.01,
        )
        result = sd.evaluate(None)
        assert sd.last_result is result
        assert result.status == SignalStatus.NO_SETUP

    def test_last_result_updated_on_each_call(self):
        sd = LiveSignalDetector(
            symbol="SPY", direction="LONG", tick_size=0.01,
        )
        r1 = sd.evaluate(None)
        r2 = sd.evaluate({"candles": []})
        assert sd.last_result is r2
        assert sd.last_result is not r1


class TestDualSignalDetectorLastResult:
    """Verify last_result is cached for DualSignalDetector."""

    def test_last_result_cached(self):
        long_sd = LiveSignalDetector(
            symbol="SPY", direction="LONG", tick_size=0.01,
        )
        short_sd = LiveSignalDetector(
            symbol="SPY", direction="SHORT", tick_size=0.01,
        )
        dual = DualSignalDetector(long_sd, short_sd)
        assert dual.last_result is None
        result = dual.evaluate(None)
        assert dual.last_result is result


# ═════════════════════════════════════════════════════════════════════════════
# TradeOrchestrator: signal split into pure + deferred
# ═════════════════════════════════════════════════════════════════════════════


class TestTradeOrchestratorSignalSplit:
    """Verify _check_for_signal stores pending, execute_pending_signal runs IBKR."""

    def _make_orchestrator(self, signal_result=None):
        """Create orchestrator with mocked dependencies."""
        from trading_lab.live.trade_orchestrator import (
            MaxBotTradeOrchestrator,
            LifecycleState,
        )

        sb = MagicMock()
        sb.current_session.return_value = {
            "date": "2026-01-15",
            "candles": [{"time_ms": 1000, "open": 100, "high": 101,
                         "low": 99, "close": 100.5, "volume": 1000}],
        }
        sb.current_date = "2026-01-15"

        sd = MagicMock()
        sd.last_result = signal_result
        if signal_result:
            sd.evaluate.return_value = signal_result
        else:
            no_setup = SignalResult(
                status=SignalStatus.NO_SETUP, direction="LONG",
                pipeline_stage="BREAK_NOT_FOUND", failed_stage="BREAK_NOT_FOUND",
            )
            sd.evaluate.return_value = no_setup

        tm = MagicMock()
        tm.can_trade = True
        tm.state = MagicMock(
            trading_date="2026-01-15", trades_used=0,
            wins=0, losses=0, day_finished=False,
        )

        os_ = MagicMock()
        ee = MagicMock()
        xe = MagicMock()

        orch = MaxBotTradeOrchestrator(
            underlying_symbol="SPY", direction="LONG",
            tick_size=0.01, session_builder=sb,
            signal_detector=sd, trade_manager=tm,
            option_selector=os_, entry_executor=ee,
            exit_executor=xe,
        )
        return orch, sd, os_, ee

    def test_on_bar_no_signal_no_pending(self):
        orch, sd, os_, ee = self._make_orchestrator()
        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        assert not orch.has_pending_signal
        # option_selector should NOT have been called
        os_.select.assert_not_called()
        ee.submit_entry.assert_not_called()

    def test_on_bar_signal_stores_pending_no_ibkr(self):
        """Signal detected → stored as pending, NO IBKR calls."""
        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="LONG",
            pipeline_stage="SIGNAL", failed_stage=None,
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        orch, sd, os_, ee = self._make_orchestrator(signal_result=sig)
        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        assert orch.has_pending_signal
        # NO IBKR calls from on_bar
        os_.select.assert_not_called()
        ee.submit_entry.assert_not_called()

    @patch("trading_lab.live.trade_orchestrator.build_option_execution_intent")
    def test_execute_pending_signal_calls_ibkr(self, mock_intent):
        """execute_pending_signal runs the IBKR sync work."""
        from decimal import Decimal
        triggers = MagicMock()
        triggers.entry_price = Decimal("100.50")
        triggers.stop_price = Decimal("99.00")
        triggers.target_price = Decimal("103.50")
        mock_intent.return_value = MagicMock(underlying_triggers=triggers)

        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="LONG",
            pipeline_stage="SIGNAL", failed_stage=None,
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        orch, sd, os_, ee = self._make_orchestrator(signal_result=sig)

        selection = MagicMock(
            right="C", expiration="20260115", strike=100.0,
            con_id=123, exchange="SMART", multiplier="100",
            bid=1.50, ask=1.60, spread=0.10,
        )
        os_.select.return_value = selection
        submission = MagicMock(
            order_id=42, perm_id=99, status="Submitted",
            con_id=123,
        )
        ee.submit_entry.return_value = submission

        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        assert orch.has_pending_signal

        orch.execute_pending_signal()
        assert not orch.has_pending_signal
        os_.select.assert_called_once()
        ee.submit_entry.assert_called_once()

    @patch("trading_lab.live.trade_orchestrator.build_option_execution_intent")
    def test_one_signal_one_execution(self, mock_intent):
        """Calling execute_pending_signal twice does not double-execute."""
        from decimal import Decimal
        triggers = MagicMock()
        triggers.entry_price = Decimal("100.50")
        triggers.stop_price = Decimal("99.00")
        triggers.target_price = Decimal("103.50")
        mock_intent.return_value = MagicMock(underlying_triggers=triggers)

        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="LONG",
            pipeline_stage="SIGNAL", failed_stage=None,
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        orch, sd, os_, ee = self._make_orchestrator(signal_result=sig)
        selection = MagicMock(
            right="C", expiration="20260115", strike=100.0,
            con_id=123, exchange="SMART", multiplier="100",
            bid=1.50, ask=1.60, spread=0.10,
        )
        os_.select.return_value = selection
        ee.submit_entry.return_value = MagicMock(
            order_id=42, perm_id=99, status="Submitted", con_id=123,
        )

        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        orch.execute_pending_signal()
        orch.execute_pending_signal()  # second call — no-op
        assert os_.select.call_count == 1
        assert ee.submit_entry.call_count == 1

    def test_no_double_signal_evaluation(self):
        """on_bar calls signal_detector.evaluate exactly once."""
        orch, sd, os_, ee = self._make_orchestrator()
        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        sd.evaluate.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
# ObserveOrchestrator: same split
# ═════════════════════════════════════════════════════════════════════════════


class TestObserveOrchestratorSignalSplit:
    """Verify ObserveOrchestrator defers IBKR calls."""

    def _make_orchestrator(self, signal_result=None):
        from trading_lab.live.observe_orchestrator import ObserveOrchestrator

        sb = MagicMock()
        sb.current_session.return_value = {
            "date": "2026-01-15",
            "candles": [{"time_ms": 1000, "open": 100, "high": 101,
                         "low": 99, "close": 100.5, "volume": 1000}],
        }
        sb.current_date = "2026-01-15"

        sd = MagicMock()
        sd.last_result = signal_result
        if signal_result:
            sd.evaluate.return_value = signal_result
        else:
            no_setup = SignalResult(
                status=SignalStatus.NO_SETUP, direction="LONG",
                pipeline_stage="BREAK_NOT_FOUND", failed_stage="BREAK_NOT_FOUND",
            )
            sd.evaluate.return_value = no_setup

        os_ = MagicMock()

        orch = ObserveOrchestrator(
            underlying_symbol="SPY", direction="LONG",
            tick_size=0.01, session_builder=sb,
            signal_detector=sd, option_selector=os_,
        )
        return orch, sd, os_

    def test_on_bar_signal_no_ibkr_calls(self):
        """Signal detected → pending, NO option_selector.select."""
        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="LONG",
            pipeline_stage="SIGNAL", failed_stage=None,
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        orch, sd, os_ = self._make_orchestrator(signal_result=sig)
        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        assert orch.has_pending_signal
        os_.select.assert_not_called()

    @patch("trading_lab.live.observe_orchestrator.build_option_execution_intent")
    def test_execute_pending_signal_calls_ibkr(self, mock_intent):
        from decimal import Decimal
        triggers = MagicMock()
        triggers.entry_price = Decimal("100.50")
        triggers.stop_price = Decimal("99.00")
        triggers.target_price = Decimal("103.50")
        mock_intent.return_value = MagicMock(underlying_triggers=triggers)

        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="LONG",
            pipeline_stage="SIGNAL", failed_stage=None,
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        orch, sd, os_ = self._make_orchestrator(signal_result=sig)
        selection = MagicMock(
            right="C", expiration="20260115", strike=100.0,
            con_id=123, exchange="SMART", multiplier="100",
            bid=1.50, ask=1.60, spread=0.10,
        )
        os_.select.return_value = selection

        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        orch.execute_pending_signal()
        assert not orch.has_pending_signal
        os_.select.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
# Bot runner integration: callback → enqueue → process
# ═════════════════════════════════════════════════════════════════════════════


class TestBotRunnerCallbackEnqueue:
    """Integration tests for the callback → queue → execution path."""

    def _make_runner_with_rt(self, execution_mode="PAPER_EXECUTE"):
        """Build a MaxBotRunner with minimal mocked internals."""
        from trading_lab.live.bot_runner import MaxBotRunner
        from trading_lab.live.observe_orchestrator import ExecutionMode

        runner = MaxBotRunner.__new__(MaxBotRunner)
        runner._symbols = ["SPY"]
        runner._direction = "LONG"
        runner._tz_str = "America/New_York"
        runner._tz = __import__("zoneinfo").ZoneInfo("America/New_York")
        runner._session_open = "09:30"
        runner._session_close = "16:00"
        runner._execution_mode = ExecutionMode(execution_mode)
        runner._execution_queue = ExecutionQueue()
        runner._event_factory = MagicMock()
        runner._session_log = MagicMock()
        runner._runtimes = {}
        runner._running = False

        # Build a SymbolRuntime
        from trading_lab.live.watchlist import SymbolRuntime
        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.session_builder = MagicMock()
        rt.session_builder.current_session.return_value = {
            "date": "2026-01-15",
            "candles": [{"time_ms": 1000, "open": 100, "high": 101,
                         "low": 99, "close": 100.5, "volume": 1000}],
        }

        sd = MagicMock()
        sd.last_result = None
        rt.signal_detector = sd

        orch = MagicMock()
        orch.has_pending_signal = False
        rt.orchestrator = orch

        runner._runtimes["SPY"] = rt
        return runner, rt, orch

    def _make_bar_list(self):
        """Create a bar list that ibkr_bar_to_candle can process."""
        from datetime import datetime, timezone, timedelta
        # Use TODAY's date so _is_live_bar passes
        now_utc = datetime.now(timezone.utc)
        today_1035 = now_utc.replace(hour=14, minute=35, second=0, microsecond=0)
        bar = MagicMock()
        bar.date = today_1035  # 10:35 ET today
        bar.open = 100.0
        bar.high = 101.0
        bar.low = 99.0
        bar.close = 100.5
        bar.volume = 1000
        # bars[-2] = completed bar, bars[-1] = live bar
        return [bar, bar, MagicMock()]

    def test_callback_enqueues_on_signal(self):
        """When orchestrator.has_pending_signal is True, item is enqueued."""
        runner, rt, orch = self._make_runner_with_rt("PAPER_EXECUTE")

        orch.on_bar.return_value = MagicMock(lifecycle="WAITING_FOR_SIGNAL")
        orch.has_pending_signal = True

        bars = self._make_bar_list()
        runner._on_bar_update(rt, bars, True)
        assert runner._execution_queue.pending_count == 1

    def test_process_queue_calls_execute(self):
        """_process_execution_queue calls orchestrator.execute_pending_signal."""
        runner, rt, orch = self._make_runner_with_rt("PAPER_EXECUTE")

        item = ExecutionWorkItem(
            symbol="SPY",
            work_type=WorkItemType.SIGNAL_EXECUTION,
            signal_result=None,
            bar_time_ms=1000,
        )
        runner._execution_queue.enqueue(item)
        runner._process_execution_queue()

        orch.execute_pending_signal.assert_called_once()

    def test_process_queue_error_isolation(self):
        """Error on one symbol does not block another."""
        from trading_lab.live.watchlist import SymbolRuntime

        runner, rt_spy, orch_spy = self._make_runner_with_rt("PAPER_EXECUTE")

        # Add QQQ
        rt_qqq = SymbolRuntime(symbol="QQQ")
        rt_qqq.enabled = True
        orch_qqq = MagicMock()
        rt_qqq.orchestrator = orch_qqq
        runner._runtimes["QQQ"] = rt_qqq

        # SPY will fail
        orch_spy.execute_pending_signal.side_effect = RuntimeError("chain error")

        item_spy = ExecutionWorkItem(
            symbol="SPY", work_type=WorkItemType.SIGNAL_EXECUTION,
            signal_result=None, bar_time_ms=1000,
        )
        item_qqq = ExecutionWorkItem(
            symbol="QQQ", work_type=WorkItemType.SIGNAL_EXECUTION,
            signal_result=None, bar_time_ms=1000,
        )
        runner._execution_queue.enqueue(item_spy)
        runner._execution_queue.enqueue(item_qqq)

        runner._process_execution_queue()

        # Both were attempted
        orch_spy.execute_pending_signal.assert_called_once()
        orch_qqq.execute_pending_signal.assert_called_once()

        # SPY failed, QQQ completed
        assert item_spy.status == WorkItemStatus.FAILED
        assert item_qqq.status == WorkItemStatus.COMPLETED

    def test_observe_only_enqueues_on_signal(self):
        """OBSERVE_ONLY mode also uses the execution queue."""
        runner, rt, orch = self._make_runner_with_rt("OBSERVE_ONLY")
        orch.on_bar.return_value = None
        orch.has_pending_signal = True

        bars = self._make_bar_list()
        runner._on_bar_update(rt, bars, True)
        assert runner._execution_queue.pending_count == 1

    def test_callback_does_not_call_ibkr_sync(self):
        """Verify _on_bar_update never calls execute_pending_signal."""
        runner, rt, orch = self._make_runner_with_rt("PAPER_EXECUTE")
        orch.on_bar.return_value = MagicMock(lifecycle="WAITING_FOR_SIGNAL")
        orch.has_pending_signal = False

        bars = self._make_bar_list()
        runner._on_bar_update(rt, bars, True)
        orch.execute_pending_signal.assert_not_called()

    def test_callback_continues_after_orchestrator_error(self):
        """Exception in orchestrator.on_bar is caught, callback returns."""
        runner, rt, orch = self._make_runner_with_rt("PAPER_EXECUTE")
        orch.on_bar.side_effect = RuntimeError("strategy error")

        bars = self._make_bar_list()
        # Should NOT raise — caught internally
        runner._on_bar_update(rt, bars, True)
