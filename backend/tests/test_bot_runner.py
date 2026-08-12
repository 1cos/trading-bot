"""Tests for MaxBotRunner — IBKR Paper live runner (multi-symbol).

All tests use mock IB. No real TWS/Gateway connection.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.live.bot_runner import (
    MaxBotRunner,
    verify_paper_account,
    ibkr_bar_to_candle,
    is_rth_bar,
)
from trading_lab.live.trade_orchestrator import LifecycleState
from trading_lab.live.watchlist import parse_symbols


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
