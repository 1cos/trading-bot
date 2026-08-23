"""Tests for MaxBotRunner — IBKR Paper live runner (multi-symbol).

All tests use mock IB. No real TWS/Gateway connection.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.live.bot_runner import (
    MaxBotRunner,
    verify_paper_account,
    ibkr_bar_to_candle,
    is_rth_bar,
)
from trading_lab.live.trade_orchestrator import LifecycleState
from trading_lab.live.watchlist import parse_symbols, SymbolRuntime


ET = ZoneInfo("America/New_York")


# ── Paper verification ──────────────────────────────────────────────────────

class TestPaperVerification:
    def test_rejects_live_account(self):
        ib = MagicMock()
        ib.managedAccounts.return_value = ["U1234567"]
        with pytest.raises(RuntimeError, match="No paper account"):
            verify_paper_account(ib)

    def test_rejects_empty_accounts(self):
        ib = MagicMock()
        ib.managedAccounts.return_value = []
        with pytest.raises(RuntimeError, match="No managed accounts"):
            verify_paper_account(ib)

    def test_accepts_paper(self):
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU1234567"]
        assert verify_paper_account(ib) == "DU1234567"


# ── Symbol parsing ──────────────────────────────────────────────────────────

class TestSymbolParsing:
    def test_comma_separated(self):
        assert parse_symbols("QQQ,SPY,NVDA") == ["QQQ", "SPY", "NVDA"]

    def test_deduplication(self):
        assert parse_symbols("QQQ,SPY,QQQ") == ["QQQ", "SPY"]

    def test_uppercase(self):
        assert parse_symbols("qqq,spy") == ["QQQ", "SPY"]

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            parse_symbols("")

    def test_whitespace_handled(self):
        assert parse_symbols(" QQQ , SPY ") == ["QQQ", "SPY"]


# ── Bar conversion ──────────────────────────────────────────────────────────

class TestBarConversion:
    def test_candle_format(self):
        dt = datetime(2026, 8, 11, 9, 30, 0, tzinfo=ET)
        bar = SimpleNamespace(date=dt, open=100.5, high=101.2,
                              low=99.8, close=100.9, volume=5000)
        candle = ibkr_bar_to_candle(bar, ET)
        assert "time_ms" in candle
        assert candle["open"] == 100.5


# ── RTH filtering ───────────────────────────────────────────────────────────

class TestRTH:
    def test_rth_accepted(self):
        ms = int(datetime(2026, 8, 11, 9, 30, 0, tzinfo=ET).timestamp() * 1000)
        assert is_rth_bar(ms, ET, "09:30", "16:00") is True

    def test_pre_market_rejected(self):
        ms = int(datetime(2026, 8, 11, 8, 0, 0, tzinfo=ET).timestamp() * 1000)
        assert is_rth_bar(ms, ET, "09:30", "16:00") is False


# ── Multi-symbol setup ──────────────────────────────────────────────────────

class TestMultiSymbolSetup:
    def test_multiple_runtimes(self):
        runner = MaxBotRunner(["QQQ", "SPY", "NVDA"])
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        assert len(runner._runtimes) == 3
        assert "QQQ" in runner._runtimes
        assert "SPY" in runner._runtimes
        assert "NVDA" in runner._runtimes

    def test_independent_session_builders(self):
        runner = MaxBotRunner(["QQQ", "SPY"])
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        sb_q = runner._runtimes["QQQ"].session_builder
        sb_s = runner._runtimes["SPY"].session_builder
        assert sb_q is not sb_s

    def test_independent_orchestrators(self):
        runner = MaxBotRunner(["QQQ", "SPY"])
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        o_q = runner._runtimes["QQQ"].orchestrator
        o_s = runner._runtimes["SPY"].orchestrator
        assert o_q is not o_s

    def test_independent_processed_times(self):
        runner = MaxBotRunner(["QQQ", "SPY"])
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        pt_q = runner._runtimes["QQQ"].processed_times
        pt_s = runner._runtimes["SPY"].processed_times
        assert pt_q is not pt_s


# ── Qualification isolation ─────────────────────────────────────────────────

class TestQualificationIsolation:
    def test_failure_disables_only_that_symbol(self):
        runner = MaxBotRunner(["QQQ", "BAD", "SPY"])
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]

        def qual_side_effect(*contracts):
            c = contracts[0]
            if c.symbol == "BAD":
                return []
            c.conId = 99
            return [c]

        ib.qualifyContracts.side_effect = qual_side_effect
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        runner._qualify_all()

        assert runner._runtimes["QQQ"].enabled is True
        assert runner._runtimes["BAD"].enabled is False
        assert runner._runtimes["SPY"].enabled is True
        assert runner.enabled_count == 2
        assert "BAD" in runner.disabled_symbols


# ── Shared IB connection ────────────────────────────────────────────────────

class TestSharedIB:
    def test_one_ib_instance(self):
        runner = MaxBotRunner(["QQQ", "SPY"], execution_mode="PAPER_EXECUTE")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        # Both executors share the same IB
        q_orch = runner._runtimes["QQQ"].orchestrator
        s_orch = runner._runtimes["SPY"].orchestrator
        assert q_orch._entry_executor._ib is ib
        assert s_orch._entry_executor._ib is ib


# ── Cross-symbol isolation ──────────────────────────────────────────────────

class TestCrossSymbolIsolation:
    def test_bar_routing(self):
        """QQQ bar never reaches SPY orchestrator."""
        runner = MaxBotRunner(["QQQ", "SPY"])
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()

        dt = datetime(2026, 8, 11, 9, 30, 0, tzinfo=ET)
        bar = SimpleNamespace(date=dt, open=100, high=101, low=99,
                              close=100.5, volume=1000)
        candle = ibkr_bar_to_candle(bar, ET)

        rt_q = runner._runtimes["QQQ"]
        rt_q.processed_times.add(candle["time_ms"])

        rt_s = runner._runtimes["SPY"]
        assert candle["time_ms"] not in rt_s.processed_times


# ── Observe mode multi-symbol ───────────────────────────────────────────────

class TestObserveMulti:
    def test_observe_setup(self):
        runner = MaxBotRunner(["QQQ", "SPY"], execution_mode="OBSERVE_ONLY")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        for rt in runner._runtimes.values():
            assert isinstance(rt.orchestrator, type(runner._runtimes["QQQ"].orchestrator))


# ── Paper execute multi-symbol ──────────────────────────────────────────────

class TestPaperMulti:
    def test_paper_setup(self):
        runner = MaxBotRunner(["QQQ", "SPY"], execution_mode="PAPER_EXECUTE")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        for rt in runner._runtimes.values():
            assert rt.orchestrator is not None


# ── Aggregate status ────────────────────────────────────────────────────────

class TestAggregateStatus:
    def test_all_symbols_reported(self):
        runner = MaxBotRunner(["QQQ", "SPY"])
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        statuses = runner.symbol_statuses
        assert "QQQ" in statuses
        assert "SPY" in statuses


# ── Trade limits test mode ──────────────────────────────────────────────────

class TestTradeLimits:
    def test_unlimited_by_default(self):
        runner = MaxBotRunner(["QQQ"], execution_mode="PAPER_EXECUTE")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        tm = runner._runtimes["QQQ"].trade_manager
        assert tm._unlimited is True

    def test_limits_enabled(self):
        runner = MaxBotRunner(["QQQ"], execution_mode="PAPER_EXECUTE",
                              trade_limits_enabled=True)
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        tm = runner._runtimes["QQQ"].trade_manager
        assert tm._unlimited is False


# ── Backward compatibility ──────────────────────────────────────────────────

class TestBackwardCompat:
    def test_single_string(self):
        runner = MaxBotRunner("QQQ")
        assert runner._symbols == ["QQQ"]

    def test_default_observe(self):
        runner = MaxBotRunner("QQQ")
        from trading_lab.live.observe_orchestrator import ExecutionMode
        assert runner._execution_mode == ExecutionMode.OBSERVE_ONLY


# ── Shutdown ────────────────────────────────────────────────────────────────

class TestShutdown:
    def test_clean_shutdown(self):
        runner = MaxBotRunner(["QQQ"])
        ib = MagicMock()
        ib.isConnected.return_value = True
        runner._ib = ib
        runner._shutdown()
        ib.disconnect.assert_called_once()


# ── No strategy duplication ─────────────────────────────────────────────────

class TestNoDuplication:
    def test_no_strategy(self):
        import inspect
        import trading_lab.live.watchlist as mod
        source = inspect.getsource(mod)
        assert "find_break" not in source


# ── _compute_context_levels retains previous_sessions ───────────────────────

class TestComputeContextLevelsRetainsSessions:
    """previous_sessions must be stored on the runtime, not discarded,
    without adding any second historical request."""

    def _make_runner_with_symbol(self):
        runner = MaxBotRunner(["QQQ"])
        runner._ib = MagicMock()
        rt = SymbolRuntime(symbol="QQQ")
        rt.underlying_contract = MagicMock()
        rt.enabled = True
        runner._runtimes["QQQ"] = rt
        return runner, rt

    def test_previous_sessions_stored_on_runtime(self):
        runner, rt = self._make_runner_with_symbol()
        fake_sessions = [{
            "date": "2026-08-10",
            "candles": [{"time_ms": 1, "open": 100.0, "high": 102.0,
                         "low": 97.0, "close": 101.0, "volume": 100}],
        }]
        with patch("trading_lab.live.bot_runner.fetch_previous_session_bars",
                   return_value=fake_sessions) as mock_fetch, \
             patch("trading_lab.live.bot_runner.fetch_premarket_bars",
                   return_value=[]):
            runner._compute_context_levels()

        assert rt.previous_sessions == fake_sessions
        mock_fetch.assert_called_once()

    def test_context_levels_still_computed_as_before(self):
        runner, rt = self._make_runner_with_symbol()
        fake_sessions = [{
            "date": "2026-08-10",
            "candles": [{"time_ms": 1, "open": 100.0, "high": 102.0,
                         "low": 97.0, "close": 101.0, "volume": 100}],
        }]
        with patch("trading_lab.live.bot_runner.fetch_previous_session_bars",
                   return_value=fake_sessions), \
             patch("trading_lab.live.bot_runner.fetch_premarket_bars",
                   return_value=[]):
            runner._compute_context_levels()

        assert rt.context_levels is not None
        assert rt.context_levels.pdh == 102.0
        assert rt.context_levels.pdl == 97.0

    def test_no_extra_historical_request_introduced(self):
        """Exactly one previous-session fetch and one premarket fetch
        per symbol — same call count as before this change."""
        runner, rt = self._make_runner_with_symbol()
        with patch("trading_lab.live.bot_runner.fetch_previous_session_bars",
                   return_value=[]) as mock_sessions, \
             patch("trading_lab.live.bot_runner.fetch_premarket_bars",
                   return_value=[]) as mock_premarket:
            runner._compute_context_levels()

        assert mock_sessions.call_count == 1
        assert mock_premarket.call_count == 1


# ── previous_sessions reaches rt.signal_detector, isolated per symbol ────────

class TestPreviousSessionsReachesDetector:
    """End-to-end: SymbolRuntime.previous_sessions -> _compute_context_levels
    -> rt.signal_detector.set_previous_sessions() -> detector._previous_sessions.
    """

    def _make_runner_with_symbol(self, symbol, signal_detector):
        runner = MaxBotRunner([symbol])
        runner._ib = MagicMock()
        rt = SymbolRuntime(symbol=symbol)
        rt.underlying_contract = MagicMock()
        rt.enabled = True
        rt.signal_detector = signal_detector
        runner._runtimes[symbol] = rt
        return runner, rt

    def test_previous_sessions_reaches_signal_detector(self):
        from trading_lab.live.signal_detector import LiveSignalDetector

        sd = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
        )
        runner, rt = self._make_runner_with_symbol("QQQ", sd)
        fake_sessions = [{
            "date": "2026-08-10",
            "candles": [{"time_ms": 1, "open": 100.0, "high": 102.0,
                         "low": 97.0, "close": 101.0, "volume": 100}],
        }]
        with patch("trading_lab.live.bot_runner.fetch_previous_session_bars",
                   return_value=fake_sessions), \
             patch("trading_lab.live.bot_runner.fetch_premarket_bars",
                   return_value=[]):
            runner._compute_context_levels()

        assert sd._previous_sessions == fake_sessions

    def test_orb_level_source_untouched_by_wiring(self):
        """Wiring must not flip level_source away from ORB."""
        from trading_lab.live.signal_detector import LiveSignalDetector

        sd = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
        )
        runner, rt = self._make_runner_with_symbol("QQQ", sd)
        with patch("trading_lab.live.bot_runner.fetch_previous_session_bars",
                   return_value=[]), \
             patch("trading_lab.live.bot_runner.fetch_premarket_bars",
                   return_value=[]):
            runner._compute_context_levels()

        assert sd._engine_config["level_source"] == "ORB_HIGH"

    def test_symbols_do_not_cross_contaminate(self):
        """Symbol A must not receive symbol B's previous_sessions, and
        vice versa — each detector only sees its own fetch result."""
        from trading_lab.live.signal_detector import LiveSignalDetector

        sd_qqq = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
        )
        sd_nvda = LiveSignalDetector(
            symbol="NVDA", direction="LONG", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
        )

        runner = MaxBotRunner(["QQQ", "NVDA"])
        runner._ib = MagicMock()

        rt_qqq = SymbolRuntime(symbol="QQQ")
        rt_qqq.underlying_contract = MagicMock(symbol="QQQ")
        rt_qqq.enabled = True
        rt_qqq.signal_detector = sd_qqq

        rt_nvda = SymbolRuntime(symbol="NVDA")
        rt_nvda.underlying_contract = MagicMock(symbol="NVDA")
        rt_nvda.enabled = True
        rt_nvda.signal_detector = sd_nvda

        runner._runtimes["QQQ"] = rt_qqq
        runner._runtimes["NVDA"] = rt_nvda

        sessions_qqq = [{"date": "2026-08-10", "candles": [
            {"time_ms": 1, "open": 585.0, "high": 590.0, "low": 580.0,
             "close": 586.0, "volume": 1000}]}]
        sessions_nvda = [{"date": "2026-08-10", "candles": [
            {"time_ms": 1, "open": 120.0, "high": 125.0, "low": 118.0,
             "close": 121.0, "volume": 1000}]}]

        def fake_fetch(ib, stock, tz):
            return sessions_qqq if stock.symbol == "QQQ" else sessions_nvda

        with patch("trading_lab.live.bot_runner.fetch_previous_session_bars",
                   side_effect=fake_fetch), \
             patch("trading_lab.live.bot_runner.fetch_premarket_bars",
                   return_value=[]):
            runner._compute_context_levels()

        assert rt_qqq.previous_sessions == sessions_qqq
        assert rt_nvda.previous_sessions == sessions_nvda
        assert sd_qqq._previous_sessions == sessions_qqq
        assert sd_nvda._previous_sessions == sessions_nvda
        # Cross-check: no contamination in either direction.
        assert sd_qqq._previous_sessions != sessions_nvda
        assert sd_nvda._previous_sessions != sessions_qqq
