"""Tests for feed health monitoring and per-symbol resubscription.

No real IBKR connection.
"""

import time
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

from trading_lab.live.watchlist import SymbolRuntime
from trading_lab.live.bot_runner import MaxBotRunner, is_rth_bar


ET = ZoneInfo("America/New_York")


def _make_runner():
    """Create a runner with mock IB for testing feed health."""
    runner = MaxBotRunner(["QQQ", "SPY"], execution_mode="PAPER_EXECUTE")
    runner._ib = MagicMock()
    runner._ib.managedAccounts.return_value = ["DU123"]
    runner._tz = ET
    runner._tz_str = "America/New_York"
    runner._session_open = "09:30"
    runner._session_close = "16:00"
    return runner


# ── Test 1: Stale detection ──────────────────────────────────────────────────

class TestStaleDetection:
    def test_no_bar_becomes_stale(self):
        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = MagicMock()
        rt.feed_status = "LIVE"
        rt.last_bar_time_ms = 0  # never received

        runner = _make_runner()
        runner._runtimes = {"SPY": rt}

        # Simulate RTH time check
        now_et = datetime(2026, 8, 12, 10, 0, 0, tzinfo=ET)
        runner._check_feed_health(now_et)

        assert rt.feed_status == "STALE"


# ── Test 2: Active feed stays LIVE ───────────────────────────────────────────

class TestLiveFeed:
    def test_recent_bar_stays_live(self):
        now_et = datetime(2026, 8, 12, 10, 0, 0, tzinfo=ET)
        now_ms = int(now_et.timestamp() * 1000)

        rt = SymbolRuntime(symbol="QQQ")
        rt.enabled = True
        rt.bars = MagicMock()
        rt.feed_status = "LIVE"
        rt.last_bar_time_ms = now_ms - 60_000  # 1 minute ago

        runner = _make_runner()
        runner._runtimes = {"QQQ": rt}
        runner._check_feed_health(now_et)

        assert rt.feed_status == "LIVE"


# ── Test 3: Stale only during RTH ───────────────────────────────────────────
# (tested via _run_loop only calling _check_feed_health when is_rth)

class TestStaleRTHOnly:
    def test_check_only_called_during_rth(self):
        """The _run_loop only calls _check_feed_health during RTH."""
        import inspect
        from trading_lab.live.bot_runner import MaxBotRunner
        source = inspect.getsource(MaxBotRunner._run_loop)
        assert "is_rth" in source
        assert "_check_feed_health" in source


# ── Test 4: Stale symbol resubscribes independently ─────────────────────────

class TestResubscribe:
    def test_resubscribes(self):
        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = MagicMock()
        rt.underlying_contract = MagicMock()
        rt.feed_status = "STALE"
        rt.last_bar_time_ms = 0

        runner = _make_runner()
        runner._runtimes = {"SPY": rt}

        # Mock IB methods
        new_bars = MagicMock()
        new_bars.__len__ = lambda s: 5
        new_bars.updateEvent = MagicMock()
        new_bars.updateEvent.__len__ = lambda s: 0
        new_bars.updateEvent.__iadd__ = lambda s, cb: None
        runner._ib.reqHistoricalData.return_value = new_bars

        runner._resubscribe_symbol(rt, time.monotonic())

        assert rt.resubscribe_count == 1
        runner._ib.cancelHistoricalData.assert_called_once()
        runner._ib.reqHistoricalData.assert_called_once()


# ── Test 5: Other symbols untouched ──────────────────────────────────────────

class TestOtherSymbolsUntouched:
    def test_qqq_unaffected(self):
        now_et = datetime(2026, 8, 12, 10, 0, 0, tzinfo=ET)
        now_ms = int(now_et.timestamp() * 1000)

        rt_qqq = SymbolRuntime(symbol="QQQ")
        rt_qqq.enabled = True
        rt_qqq.bars = MagicMock()
        rt_qqq.feed_status = "LIVE"
        rt_qqq.last_bar_time_ms = now_ms - 30_000

        rt_spy = SymbolRuntime(symbol="SPY")
        rt_spy.enabled = True
        rt_spy.bars = MagicMock()
        rt_spy.feed_status = "LIVE"
        rt_spy.last_bar_time_ms = 0  # stale

        runner = _make_runner()
        runner._runtimes = {"QQQ": rt_qqq, "SPY": rt_spy}
        runner._check_feed_health(now_et)

        assert rt_qqq.feed_status == "LIVE"
        assert rt_spy.feed_status == "STALE"


# ── Test 6: Duplicate bars after resubscribe ignored ─────────────────────────

class TestDedup:
    def test_processed_times_preserved(self):
        rt = SymbolRuntime(symbol="SPY")
        rt.processed_times = {100, 200, 300}
        rt.enabled = True
        rt.underlying_contract = MagicMock()
        rt.bars = MagicMock()

        runner = _make_runner()
        runner._runtimes = {"SPY": rt}

        new_bars = MagicMock()
        new_bars.__len__ = lambda s: 3
        new_bars.__iter__ = lambda s: iter([])
        new_bars.updateEvent = MagicMock()
        new_bars.updateEvent.__iadd__ = lambda s, cb: None
        runner._ib.reqHistoricalData.return_value = new_bars

        runner._resubscribe_symbol(rt, time.monotonic())

        # processed_times still has old entries
        assert 100 in rt.processed_times
        assert 200 in rt.processed_times


# ── Test 7: LIVE recovery ────────────────────────────────────────────────────

class TestLiveRecovery:
    def test_stale_to_live(self):
        now_et = datetime(2026, 8, 12, 10, 0, 0, tzinfo=ET)
        now_ms = int(now_et.timestamp() * 1000)

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = MagicMock()
        rt.feed_status = "STALE"
        rt.last_bar_time_ms = now_ms - 30_000  # 30 sec ago = recent

        runner = _make_runner()
        runner._runtimes = {"SPY": rt}
        runner._check_feed_health(now_et)

        assert rt.feed_status == "LIVE"


# ── Test 8: Rate-limited retries ─────────────────────────────────────────────

class TestRateLimited:
    def test_cooldown(self):
        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = MagicMock()
        rt.feed_status = "STALE"
        rt.last_bar_time_ms = 0
        rt.last_resubscribe_time = time.monotonic()  # just resubscribed

        now_et = datetime(2026, 8, 12, 10, 0, 0, tzinfo=ET)
        runner = _make_runner()
        runner._runtimes = {"SPY": rt}
        runner._check_feed_health(now_et)

        # Should NOT resubscribe (within cooldown)
        runner._ib.cancelHistoricalData.assert_not_called()
        assert rt.resubscribe_count == 0


# ── Test 9: Failed resubscribe remains STALE ─────────────────────────────────

class TestFailedResubscribe:
    def test_stays_stale(self):
        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.bars = MagicMock()
        rt.underlying_contract = MagicMock()
        rt.feed_status = "STALE"

        runner = _make_runner()
        runner._runtimes = {"SPY": rt}
        runner._ib.reqHistoricalData.side_effect = RuntimeError("pacing violation")

        runner._resubscribe_symbol(rt, time.monotonic())

        assert rt.feed_status == "STALE"
        assert rt.resubscribe_count == 1


# ── Test 10: API exposes feed status ─────────────────────────────────────────

class TestAPIExposure:
    def test_feed_status_in_api(self):
        from trading_lab.live.control_api import MaxBotController
        ctrl = MaxBotController()
        runner = MagicMock()
        runner._execution_mode = SimpleNamespace(__eq__=lambda s, o: True)

        rt = SymbolRuntime(symbol="SPY")
        rt.enabled = True
        rt.orchestrator = MagicMock()
        rt.orchestrator.lifecycle = "WAITING_FOR_SIGNAL"
        rt.feed_status = "STALE"
        rt.processed_bar_count = 5
        rt.resubscribe_count = 1
        rt.context_levels = None
        runner._runtimes = {"SPY": rt}
        ctrl._runner = runner
        ctrl._state = "RUNNING"

        symbols = ctrl.get_symbols()
        assert symbols[0]["feed_status"] == "STALE"
        assert symbols[0]["processed_bar_count"] == 5
        assert symbols[0]["resubscribe_count"] == 1


# ── Test 11: PWA shows LIVE/STALE ───────────────────────────────────────────

class TestPWAFeedStatus:
    def test_stale_in_html(self):
        from trading_lab.live.control_api import create_app, MaxBotController
        app = create_app(MaxBotController())
        client = app.test_client()
        html = client.get("/").data.decode()
        assert "STALE" in html
        assert "LIVE" in html


# ── Test 12: No strategy change ──────────────────────────────────────────────

class TestNoStrategyChange:
    def test_no_strategy(self):
        import inspect
        import trading_lab.live.watchlist as mod
        source = inspect.getsource(mod)
        assert "find_break" not in source


# ── Test 13: Zero order submission ───────────────────────────────────────────

class TestZeroOrders:
    def test_no_place_order(self):
        import inspect
        from trading_lab.live.bot_runner import MaxBotRunner
        source = inspect.getsource(MaxBotRunner._check_feed_health)
        assert "placeOrder" not in source
        source2 = inspect.getsource(MaxBotRunner._resubscribe_symbol)
        assert "placeOrder" not in source2


# ── Test: Initializing to LIVE transition ────────────────────────────────────

class TestInitializingToLive:
    def test_transition(self):
        now_et = datetime(2026, 8, 12, 10, 0, 0, tzinfo=ET)

        rt = SymbolRuntime(symbol="QQQ")
        rt.enabled = True
        rt.bars = MagicMock()
        rt.feed_status = "INITIALIZING"
        rt.last_bar_time_ms = int(now_et.timestamp() * 1000)

        runner = _make_runner()
        runner._runtimes = {"QQQ": rt}
        runner._check_feed_health(now_et)

        assert rt.feed_status == "LIVE"
