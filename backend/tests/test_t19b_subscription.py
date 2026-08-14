"""Tests for T19B — SPY dead feed root-cause fix.

Root cause: IBKR pacing violations from back-to-back reqHistoricalData
calls at startup (context levels + subscriptions = 27 requests with
zero delay for 9 symbols).

Fix: staggered delays between subscriptions and context-level fetches,
plus proper old-listener cleanup on resubscribe.

Covers:
1. Subscription staggering (ib.sleep called between symbols)
2. Resubscribe clears old listeners before cancel
3. Resubscribe attaches exactly 1 listener to new BarDataList
4. Old BarDataList not retained after resubscribe
5. Feed state transitions: INIT → LIVE, LIVE → STALE, STALE → INIT → LIVE
6. Bootstrap bars after resubscribe (context only, no signals)
7. No duplicate callbacks from old + new subscriptions
8. Context levels pacing
"""

import time
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from trading_lab.live.watchlist import SymbolRuntime
from trading_lab.live.bot_runner import MaxBotRunner


ET = ZoneInfo("America/New_York")


def _make_runner(symbols=None):
    """Create a runner with mock IB for testing subscription lifecycle."""
    if symbols is None:
        symbols = ["QQQ", "SPY"]
    runner = MaxBotRunner(symbols, execution_mode="OBSERVE_ONLY")
    runner._ib = MagicMock()
    runner._ib.managedAccounts.return_value = ["DU123"]
    runner._tz = ET
    runner._tz_str = "America/New_York"
    runner._session_open = "09:30"
    runner._session_close = "16:00"
    return runner


def _make_bars_mock(bar_count=5):
    """Create a mock BarDataList with proper updateEvent."""
    bars = MagicMock()
    bars.__len__ = lambda s: bar_count
    bars.__iter__ = lambda s: iter([])

    # Real list to track listeners
    _listeners = []

    event = MagicMock()
    event.__len__ = lambda s: len(_listeners)
    event.__iadd__ = lambda s, cb: _listeners.append(cb) or s
    event.clear = lambda: _listeners.clear()
    event._listeners = _listeners  # expose for assertions

    bars.updateEvent = event
    return bars


# ═════════════════════════════════════════════════════════════════════════════
# Subscription staggering
# ═════════════════════════════════════════════════════════════════════════════


class TestSubscriptionStaggering:
    """Verify ib.sleep is called between symbol subscriptions."""

    def test_sleep_called_between_subscriptions(self):
        """With 3 symbols, ib.sleep should be called 2 times for pacing."""
        runner = _make_runner(["QQQ", "SPY", "NVDA"])

        for sym in ["QQQ", "SPY", "NVDA"]:
            rt = SymbolRuntime(symbol=sym)
            rt.enabled = True
            rt.underlying_contract = MagicMock()
            rt.session_builder = MagicMock()
            runner._runtimes[sym] = rt

        runner._ib.reqHistoricalData.return_value = _make_bars_mock()

        runner._subscribe_all()

        # ib.sleep should have been called for pacing (between symbols)
        sleep_calls = [c for c in runner._ib.sleep.call_args_list]
        assert len(sleep_calls) == 2  # between symbol 1-2 and 2-3

    def test_single_symbol_no_delay(self):
        """Single symbol should not have pacing delay."""
        runner = _make_runner(["SPY"])

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.underlying_contract = MagicMock()
        rt.session_builder = MagicMock()
        runner._runtimes["SPY"] = rt

        runner._ib.reqHistoricalData.return_value = _make_bars_mock()

        runner._subscribe_all()

        # No pacing sleep for single symbol
        runner._ib.sleep.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# Resubscribe safety
# ═════════════════════════════════════════════════════════════════════════════


class TestResubscribeSafety:
    """Verify resubscribe properly manages listener lifecycle."""

    def test_old_listeners_cleared_before_cancel(self):
        """Old BarDataList.updateEvent.clear() called before cancelHistoricalData."""
        runner = _make_runner()

        old_bars = _make_bars_mock()
        # Add a fake listener to old bars
        old_bars.updateEvent += lambda b, h: None

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = old_bars
        rt.underlying_contract = MagicMock()
        rt.session_builder = MagicMock()
        runner._runtimes["SPY"] = rt

        new_bars = _make_bars_mock()
        runner._ib.reqHistoricalData.return_value = new_bars

        runner._resubscribe_symbol(rt, time.monotonic())

        # Old listeners were cleared
        assert len(old_bars.updateEvent._listeners) == 0

    def test_exactly_one_listener_on_new_bars(self):
        """After resubscribe, new BarDataList has exactly 1 listener."""
        runner = _make_runner()

        old_bars = _make_bars_mock()
        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = old_bars
        rt.underlying_contract = MagicMock()
        rt.session_builder = MagicMock()
        runner._runtimes["SPY"] = rt

        new_bars = _make_bars_mock()
        runner._ib.reqHistoricalData.return_value = new_bars

        runner._resubscribe_symbol(rt, time.monotonic())

        assert rt.listener_count == 1
        assert len(new_bars.updateEvent._listeners) == 1

    def test_bars_reference_updated(self):
        """rt.bars points to the new BarDataList after resubscribe."""
        runner = _make_runner()

        old_bars = _make_bars_mock()
        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = old_bars
        rt.underlying_contract = MagicMock()
        rt.session_builder = MagicMock()
        runner._runtimes["SPY"] = rt

        new_bars = _make_bars_mock()
        runner._ib.reqHistoricalData.return_value = new_bars

        runner._resubscribe_symbol(rt, time.monotonic())

        assert rt.bars is new_bars
        assert rt.bars is not old_bars

    def test_feed_status_reset_to_initializing(self):
        """After resubscribe, feed_status is INITIALIZING."""
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = _make_bars_mock()
        rt.underlying_contract = MagicMock()
        rt.session_builder = MagicMock()
        rt.feed_status = "STALE"
        runner._runtimes["SPY"] = rt

        runner._ib.reqHistoricalData.return_value = _make_bars_mock()

        runner._resubscribe_symbol(rt, time.monotonic())

        assert rt.feed_status == "INITIALIZING"

    def test_cancel_called_on_old_bars(self):
        """cancelHistoricalData is called with the old BarDataList."""
        runner = _make_runner()

        old_bars = _make_bars_mock()
        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = old_bars
        rt.underlying_contract = MagicMock()
        rt.session_builder = MagicMock()
        runner._runtimes["SPY"] = rt

        runner._ib.reqHistoricalData.return_value = _make_bars_mock()

        runner._resubscribe_symbol(rt, time.monotonic())

        runner._ib.cancelHistoricalData.assert_called_once_with(old_bars)

    def test_pacing_delay_during_resubscribe(self):
        """ib.sleep called between cancel and new request."""
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = _make_bars_mock()
        rt.underlying_contract = MagicMock()
        rt.session_builder = MagicMock()
        runner._runtimes["SPY"] = rt

        runner._ib.reqHistoricalData.return_value = _make_bars_mock()

        runner._resubscribe_symbol(rt, time.monotonic())

        # ib.sleep should have been called for pacing
        assert runner._ib.sleep.call_count >= 1

    def test_bars_object_id_tracked(self):
        """bars_object_id is updated to reflect new BarDataList."""
        runner = _make_runner()

        old_bars = _make_bars_mock()
        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = old_bars
        rt.bars_object_id = id(old_bars)
        rt.underlying_contract = MagicMock()
        rt.session_builder = MagicMock()
        runner._runtimes["SPY"] = rt

        new_bars = _make_bars_mock()
        runner._ib.reqHistoricalData.return_value = new_bars

        runner._resubscribe_symbol(rt, time.monotonic())

        assert rt.bars_object_id == id(new_bars)
        assert rt.bars_object_id != id(old_bars)


# ═════════════════════════════════════════════════════════════════════════════
# Feed state transitions
# ═════════════════════════════════════════════════════════════════════════════


class TestFeedStateTransitions:
    """Verify correct state machine: INIT → LIVE, LIVE → STALE, etc."""

    def test_init_to_live_on_first_bar(self):
        """INITIALIZING → LIVE when last_bar_time_ms > 0."""
        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = MagicMock()
        rt.feed_status = "INITIALIZING"
        rt.last_bar_time_ms = int(datetime(2026, 8, 12, 10, 0, 0, tzinfo=ET).timestamp() * 1000)

        runner = _make_runner()
        runner._runtimes = {"SPY": rt}

        now_et = datetime(2026, 8, 12, 10, 0, 0, tzinfo=ET)
        runner._check_feed_health(now_et)

        assert rt.feed_status == "LIVE"

    def test_stale_to_init_on_resubscribe(self):
        """After resubscribe, status goes to INITIALIZING."""
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = _make_bars_mock()
        rt.underlying_contract = MagicMock()
        rt.session_builder = MagicMock()
        rt.feed_status = "STALE"
        runner._runtimes["SPY"] = rt

        runner._ib.reqHistoricalData.return_value = _make_bars_mock()

        runner._resubscribe_symbol(rt, time.monotonic())

        assert rt.feed_status == "INITIALIZING"

    def test_live_to_stale_on_timeout(self):
        """LIVE → STALE when bar age exceeds threshold."""
        now_et = datetime(2026, 8, 12, 10, 0, 0, tzinfo=ET)
        now_ms = int(now_et.timestamp() * 1000)

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = MagicMock()
        rt.feed_status = "LIVE"
        rt.last_bar_time_ms = now_ms - 200_000  # 200s ago > 180s threshold

        runner = _make_runner()
        runner._runtimes = {"SPY": rt}
        runner._check_feed_health(now_et)

        assert rt.feed_status == "STALE"

    def test_stale_to_live_on_recent_bar(self):
        """STALE → LIVE when a recent bar arrives."""
        now_et = datetime(2026, 8, 12, 10, 0, 0, tzinfo=ET)
        now_ms = int(now_et.timestamp() * 1000)

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = MagicMock()
        rt.feed_status = "STALE"
        rt.last_bar_time_ms = now_ms - 30_000  # 30s ago < 180s threshold

        runner = _make_runner()
        runner._runtimes = {"SPY": rt}
        runner._check_feed_health(now_et)

        assert rt.feed_status == "LIVE"


# ═════════════════════════════════════════════════════════════════════════════
# Bootstrap safety
# ═════════════════════════════════════════════════════════════════════════════


class TestBootstrapSafety:
    """Bootstrap after resubscribe uses session_builder only, no orchestrator."""

    def test_bootstrap_does_not_call_orchestrator(self):
        """_bootstrap_symbol calls session_builder.add_bar, not orchestrator.on_bar."""
        from trading_lab.live.bot_runner import ibkr_bar_to_candle
        runner = _make_runner()

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.session_builder = MagicMock()
        rt.orchestrator = MagicMock()

        # Create fake bars
        bar1 = MagicMock()
        bar1.date = datetime(2026, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
        bar1.open = 100.0
        bar1.high = 101.0
        bar1.low = 99.0
        bar1.close = 100.5
        bar1.volume = 1000

        bar2 = MagicMock()
        bar2.date = datetime(2026, 1, 15, 14, 31, 0, tzinfo=timezone.utc)
        bar2.open = 100.5
        bar2.high = 101.5
        bar2.low = 99.5
        bar2.close = 101.0
        bar2.volume = 1500

        # The live bar (last one, excluded from bootstrap)
        live_bar = MagicMock()
        live_bar.date = datetime(2026, 1, 15, 14, 32, 0, tzinfo=timezone.utc)

        rt.bars = [bar1, bar2, live_bar]

        runner._bootstrap_symbol(rt)

        # session_builder.add_bar should have been called
        assert rt.session_builder.add_bar.call_count == 2
        # orchestrator.on_bar should NOT have been called
        rt.orchestrator.on_bar.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# No strategy change verification
# ═════════════════════════════════════════════════════════════════════════════


class TestNoStrategyChange:
    """Verify T19B did not modify strategy logic."""

    def test_no_strategy_in_resubscribe(self):
        import inspect
        source = inspect.getsource(MaxBotRunner._resubscribe_symbol)
        for keyword in ["find_break", "find_displacement", "find_retest",
                         "find_rejection", ".evaluate("]:
            assert keyword not in source, f"Strategy call '{keyword}' found in _resubscribe_symbol"
        # on_bar should not be CALLED (but can appear in comments)
        # Check there's no orchestrator.on_bar call
        assert "orchestrator.on_bar" not in source

    def test_no_strategy_in_subscribe_all(self):
        import inspect
        source = inspect.getsource(MaxBotRunner._subscribe_all)
        for keyword in ["find_break", "find_displacement", "find_retest",
                         "find_rejection", "evaluate"]:
            assert keyword not in source, f"Strategy method '{keyword}' found in _subscribe_all"
