"""Critical tests — historical bootstrap must NEVER trigger execution.

Root cause (2026-08-18): bootstrap skipped bars[-1] (15:59 yesterday).
When market opened, bars[-2] was this unprocessed historical bar.
Callback processed it → SIGNAL → EXECUTION_WORK_ENQUEUED on yesterday's
setup. 3 symbols got false orders.

Defense-in-depth:
1. Bootstrap marks ALL bars in processed_times (including last)
2. _is_live_bar rejects bars from previous session dates
3. Both _on_bar_update and _poll_bars_fallback check live boundary
"""

from datetime import datetime, timezone, timedelta
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
    runner._symbols = ["QQQ"]
    runner._tz = ET
    runner._tz_str = "America/New_York"
    runner._session_open = "09:30"
    runner._session_close = "16:00"
    runner._execution_mode = MagicMock()
    runner._execution_queue = ExecutionQueue()
    runner._runtimes = {}
    return runner


def _make_bar(dt_utc, close=730.0):
    bar = MagicMock()
    bar.date = dt_utc
    bar.open = close - 0.5
    bar.high = close + 0.5
    bar.low = close - 1.0
    bar.close = close
    bar.volume = 1000
    return bar


def _yesterday_1559():
    """15:59 ET yesterday — last RTH bar from previous session."""
    yesterday = datetime.now(ET).date() - timedelta(days=1)
    return datetime(yesterday.year, yesterday.month, yesterday.day,
                    15, 59, 0, tzinfo=ET).astimezone(timezone.utc)


def _today_0930():
    """09:30 ET today — first RTH bar of current session."""
    today = datetime.now(ET).date()
    return datetime(today.year, today.month, today.day,
                    9, 30, 0, tzinfo=ET).astimezone(timezone.utc)


def _today_0931():
    today = datetime.now(ET).date()
    return datetime(today.year, today.month, today.day,
                    9, 31, 0, tzinfo=ET).astimezone(timezone.utc)


# ═══════════════════════════════════════════════════════════════════
# 1. _is_live_bar
# ═══════════════════════════════════════════════════════════════════


class TestIsLiveBar:
    def test_yesterday_bar_is_not_live(self):
        runner = _make_runner()
        rt = SymbolRuntime(symbol="QQQ")
        bar = _make_bar(_yesterday_1559(), 729.85)
        candle = ibkr_bar_to_candle(bar, ET)
        assert not runner._is_live_bar(candle, rt)

    def test_today_bar_is_live(self):
        runner = _make_runner()
        rt = SymbolRuntime(symbol="QQQ")
        bar = _make_bar(_today_0930(), 730.50)
        candle = ibkr_bar_to_candle(bar, ET)
        assert runner._is_live_bar(candle, rt)


# ═══════════════════════════════════════════════════════════════════
# 2. Bootstrap marks ALL bars including last
# ═══════════════════════════════════════════════════════════════════


class TestBootstrapMarksAll:
    def test_last_bar_in_processed_times(self):
        """bars[-1] must be in processed_times after bootstrap."""
        runner = _make_runner()
        rt = SymbolRuntime(symbol="QQQ")
        rt.session_builder = MagicMock()

        bar1 = _make_bar(_yesterday_1559() - timedelta(minutes=1), 729.0)
        bar2 = _make_bar(_yesterday_1559(), 729.85)  # the last bar
        rt.bars = [bar1, bar2]

        runner._bootstrap_symbol(rt)

        c1 = ibkr_bar_to_candle(bar1, ET)
        c2 = ibkr_bar_to_candle(bar2, ET)
        assert c1["time_ms"] in rt.processed_times
        assert c2["time_ms"] in rt.processed_times  # THIS was missing before


# ═══════════════════════════════════════════════════════════════════
# 3. Historical bar in callback → no execution
# ═══════════════════════════════════════════════════════════════════


class TestCallbackRejectsHistorical:
    def test_yesterday_bar_no_enqueue(self):
        """A historical bar arriving via callback must NOT enqueue."""
        runner = _make_runner()

        rt = SymbolRuntime(symbol="QQQ")
        rt.enabled = True
        rt.orchestrator = MagicMock()
        rt.orchestrator.has_pending_signal = True  # would trigger enqueue
        rt.orchestrator.on_bar.return_value = MagicMock(lifecycle="SIGNAL")
        rt.signal_detector = MagicMock()
        rt.signal_detector.last_result = MagicMock(
            pipeline_stage="SIGNAL", failed_stage=None,
            stage_context={}, status=SignalStatus.SIGNAL,
        )
        rt.session_builder = MagicMock()

        # Bars: yesterday 15:58, yesterday 15:59, today live
        bar_old = _make_bar(_yesterday_1559(), 729.85)
        bar_live = _make_bar(_today_0930(), 730.50)
        bars = [bar_old, bar_old, bar_live]  # bars[-2] = yesterday

        runner._runtimes = {"QQQ": rt}
        runner._on_bar_update(rt, bars, True)

        # Orchestrator should NOT have been called (bar is historical)
        rt.orchestrator.on_bar.assert_not_called()
        assert runner._execution_queue.pending_count == 0

    def test_today_bar_does_enqueue(self):
        """A genuine live bar arriving via callback DOES enqueue."""
        runner = _make_runner()

        rt = SymbolRuntime(symbol="QQQ")
        rt.enabled = True
        rt.orchestrator = MagicMock()
        rt.orchestrator.has_pending_signal = True
        rt.orchestrator.on_bar.return_value = MagicMock(lifecycle="SIGNAL")
        rt.signal_detector = MagicMock()
        rt.signal_detector.last_result = MagicMock(
            pipeline_stage="SIGNAL", failed_stage=None,
            stage_context={}, status=SignalStatus.SIGNAL,
        )

        bar_today1 = _make_bar(_today_0930(), 730.50)
        bar_today2 = _make_bar(_today_0931(), 731.00)
        bars = [bar_today1, bar_today1, bar_today2]

        runner._runtimes = {"QQQ": rt}
        runner._on_bar_update(rt, bars, True)

        rt.orchestrator.on_bar.assert_called_once()
        assert runner._execution_queue.pending_count == 1


# ═══════════════════════════════════════════════════════════════════
# 4. Poll fallback rejects historical bars
# ═══════════════════════════════════════════════════════════════════


class TestPollRejectsHistorical:
    def test_poll_skips_yesterday_bar(self):
        runner = _make_runner()

        rt = SymbolRuntime(symbol="QQQ")
        rt.enabled = True
        rt.orchestrator = MagicMock()
        rt.orchestrator.has_pending_signal = True
        rt.signal_detector = MagicMock()
        rt.signal_detector.last_result = None
        rt.session_builder = MagicMock()

        bar_old = _make_bar(_yesterday_1559(), 729.85)
        bar_live = _make_bar(_today_0930(), 730.50)
        rt.bars = [bar_old, bar_live]  # bars[-2] = yesterday

        runner._runtimes = {"QQQ": rt}
        runner._poll_bars_fallback()

        rt.orchestrator.on_bar.assert_not_called()
        assert runner._execution_queue.pending_count == 0


# ═══════════════════════════════════════════════════════════════════
# 5. Resubscribe bootstrap → no execution
# ═══════════════════════════════════════════════════════════════════


class TestResubscribeBootstrapSafe:
    def test_bootstrap_never_calls_orchestrator(self):
        runner = _make_runner()
        rt = SymbolRuntime(symbol="QQQ")
        rt.session_builder = MagicMock()
        rt.orchestrator = MagicMock()

        bar1 = _make_bar(_yesterday_1559() - timedelta(minutes=1), 729.0)
        bar2 = _make_bar(_yesterday_1559(), 729.85)
        rt.bars = [bar1, bar2]

        runner._bootstrap_symbol(rt)

        rt.orchestrator.on_bar.assert_not_called()
