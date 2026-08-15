"""Tests for T19E — poll fallback for missed updateEvent bars.

Root cause: ib_insync updateEvent sometimes fails to fire on
BarDataList even when bars accumulate (SPY with Error 162).
Fix: periodic polling of BarDataList in main loop as fallback.

Covers:
1. Poll detects unprocessed completed bar → processes it
2. Already-processed bars not duplicated
3. Live/incomplete bar (last) not processed
4. Feed status transitions to LIVE on first polled bar
5. Signal detection and enqueue work via poll path
6. Other symbols with working updateEvent not affected
7. Non-RTH bars skipped
8. Poll error isolated per symbol
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from trading_lab.live.bot_runner import MaxBotRunner, ibkr_bar_to_candle
from trading_lab.live.watchlist import SymbolRuntime
from trading_lab.live.execution_queue import ExecutionQueue
from trading_lab.live.signal_detector import SignalStatus


ET = ZoneInfo("America/New_York")


def _make_runner():
    runner = MaxBotRunner.__new__(MaxBotRunner)
    runner._symbols = ["SPY"]
    runner._tz = ET
    runner._tz_str = "America/New_York"
    runner._session_open = "09:30"
    runner._session_close = "16:00"
    runner._execution_mode = MagicMock(__eq__=lambda s, o: True)
    runner._execution_queue = ExecutionQueue()
    runner._runtimes = {}
    return runner


def _make_bar(dt_utc, close=100.0):
    bar = MagicMock()
    bar.date = dt_utc
    bar.open = close - 0.5
    bar.high = close + 0.5
    bar.low = close - 1.0
    bar.close = close
    bar.volume = 1000
    return bar


class TestPollFallbackDetectsNewBar:
    def test_unprocessed_bar_gets_processed(self):
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.feed_status = "INITIALIZING"
        rt.orchestrator = MagicMock()
        rt.orchestrator.has_pending_signal = False
        rt.orchestrator.on_bar.return_value = MagicMock(lifecycle="WAITING_FOR_SIGNAL")
        rt.signal_detector = MagicMock()
        rt.signal_detector.last_result = None

        # Two bars: completed + live
        completed = _make_bar(datetime(2026, 1, 15, 14, 31, 0, tzinfo=timezone.utc), 100.0)
        live = _make_bar(datetime(2026, 1, 15, 14, 32, 0, tzinfo=timezone.utc), 100.5)
        rt.bars = [completed, live]

        runner._runtimes = {"SPY": rt}
        runner._poll_bars_fallback()

        rt.orchestrator.on_bar.assert_called_once()
        assert rt.feed_status == "LIVE"
        assert rt.processed_bar_count == 1


class TestPollFallbackDedup:
    def test_already_processed_bar_skipped(self):
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.orchestrator = MagicMock()
        rt.signal_detector = MagicMock()
        rt.signal_detector.last_result = None

        completed = _make_bar(datetime(2026, 1, 15, 14, 31, 0, tzinfo=timezone.utc))
        live = _make_bar(datetime(2026, 1, 15, 14, 32, 0, tzinfo=timezone.utc))
        rt.bars = [completed, live]

        # Pre-mark as processed
        candle = ibkr_bar_to_candle(completed, ET)
        rt.processed_times.add(candle["time_ms"])

        runner._runtimes = {"SPY": rt}
        runner._poll_bars_fallback()

        rt.orchestrator.on_bar.assert_not_called()


class TestPollFallbackLiveBarSkipped:
    def test_single_bar_not_processed(self):
        """Only 1 bar = it's the live bar, nothing to process."""
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.orchestrator = MagicMock()

        live = _make_bar(datetime(2026, 1, 15, 14, 32, 0, tzinfo=timezone.utc))
        rt.bars = [live]

        runner._runtimes = {"SPY": rt}
        runner._poll_bars_fallback()

        rt.orchestrator.on_bar.assert_not_called()


class TestPollFallbackFeedTransition:
    def test_init_to_live_on_first_polled_bar(self):
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.feed_status = "INITIALIZING"
        rt.orchestrator = MagicMock()
        rt.orchestrator.has_pending_signal = False
        rt.signal_detector = MagicMock()
        rt.signal_detector.last_result = None

        completed = _make_bar(datetime(2026, 1, 15, 14, 31, 0, tzinfo=timezone.utc))
        live = _make_bar(datetime(2026, 1, 15, 14, 32, 0, tzinfo=timezone.utc))
        rt.bars = [completed, live]

        runner._runtimes = {"SPY": rt}
        runner._poll_bars_fallback()

        assert rt.feed_status == "LIVE"

    def test_stale_to_live_on_polled_bar(self):
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.feed_status = "STALE"
        rt.orchestrator = MagicMock()
        rt.orchestrator.has_pending_signal = False
        rt.signal_detector = MagicMock()
        rt.signal_detector.last_result = None

        completed = _make_bar(datetime(2026, 1, 15, 14, 31, 0, tzinfo=timezone.utc))
        live = _make_bar(datetime(2026, 1, 15, 14, 32, 0, tzinfo=timezone.utc))
        rt.bars = [completed, live]

        runner._runtimes = {"SPY": rt}
        runner._poll_bars_fallback()

        assert rt.feed_status == "LIVE"


class TestPollFallbackSignalEnqueue:
    def test_signal_from_poll_enqueued(self):
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.feed_status = "LIVE"
        rt.orchestrator = MagicMock()
        rt.orchestrator.has_pending_signal = True
        rt.orchestrator.on_bar.return_value = MagicMock(lifecycle="WAITING_FOR_SIGNAL")
        rt.signal_detector = MagicMock()
        rt.signal_detector.last_result = MagicMock(
            pipeline_stage="SIGNAL", failed_stage=None,
            stage_context={}, status=SignalStatus.SIGNAL,
        )

        completed = _make_bar(datetime(2026, 1, 15, 14, 31, 0, tzinfo=timezone.utc))
        live = _make_bar(datetime(2026, 1, 15, 14, 32, 0, tzinfo=timezone.utc))
        rt.bars = [completed, live]

        runner._runtimes = {"SPY": rt}
        runner._poll_bars_fallback()

        assert runner._execution_queue.pending_count == 1


class TestPollFallbackOtherSymbolsUnaffected:
    def test_qqq_already_processed_not_duplicated(self):
        """QQQ with working updateEvent: poll sees already-processed bar."""
        runner = _make_runner()

        rt_qqq = SymbolRuntime(symbol="QQQ")
        rt_qqq.enabled = True
        rt_qqq.orchestrator = MagicMock()
        rt_qqq.signal_detector = MagicMock()

        completed = _make_bar(datetime(2026, 1, 15, 14, 31, 0, tzinfo=timezone.utc))
        live = _make_bar(datetime(2026, 1, 15, 14, 32, 0, tzinfo=timezone.utc))
        rt_qqq.bars = [completed, live]

        # QQQ already processed via callback
        candle = ibkr_bar_to_candle(completed, ET)
        rt_qqq.processed_times.add(candle["time_ms"])

        runner._runtimes = {"QQQ": rt_qqq}
        runner._poll_bars_fallback()

        rt_qqq.orchestrator.on_bar.assert_not_called()


class TestPollFallbackErrorIsolation:
    def test_error_on_one_symbol_does_not_crash(self):
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.orchestrator = MagicMock()
        rt.orchestrator.on_bar.side_effect = RuntimeError("crash")
        rt.signal_detector = MagicMock()
        rt.signal_detector.last_result = None

        completed = _make_bar(datetime(2026, 1, 15, 14, 31, 0, tzinfo=timezone.utc))
        live = _make_bar(datetime(2026, 1, 15, 14, 32, 0, tzinfo=timezone.utc))
        rt.bars = [completed, live]

        runner._runtimes = {"SPY": rt}
        # Should NOT raise
        runner._poll_bars_fallback()


class TestPollFallbackNonRTHSkipped:
    def test_pre_market_bar_not_processed(self):
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.orchestrator = MagicMock()

        # 08:30 ET = before 09:30 open
        pre_market = _make_bar(
            datetime(2026, 1, 15, 13, 30, 0, tzinfo=timezone.utc)
        )
        live = _make_bar(
            datetime(2026, 1, 15, 13, 31, 0, tzinfo=timezone.utc)
        )
        rt.bars = [pre_market, live]

        runner._runtimes = {"SPY": rt}
        runner._poll_bars_fallback()

        rt.orchestrator.on_bar.assert_not_called()
