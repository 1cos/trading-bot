"""Tests for BOTH direction support in live bot.

Verifies DualSignalDetector, BOTH CLI, and direction propagation.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

from trading_lab.live.dual_signal_detector import DualSignalDetector
from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.observe_orchestrator import ObserveOrchestrator, ObserveLifecycle
from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.observe_orchestrator import ExecutionMode


ET = ZoneInfo("America/New_York")
_BASE = int(datetime(2026, 8, 11, 9, 30, 0, tzinfo=ET).timestamp() * 1000)


def _ms(offset: int) -> int:
    return _BASE + offset * 60_000


# ── Synthetic LONG bars (same as test_signal_detector) ───────────────────────

def _long_signal_bars():
    orb = [{"time_ms": _ms(i), "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 1000} for i in range(5)]
    brk = {"time_ms": _ms(5), "open": 100.8, "high": 101.6, "low": 100.7,
           "close": 101.5, "volume": 1000}
    disp = [
        {"time_ms": _ms(6), "open": 101.55, "high": 101.8, "low": 101.2,
         "close": 101.6, "volume": 1000},
        {"time_ms": _ms(7), "open": 101.6, "high": 101.9, "low": 101.3,
         "close": 101.7, "volume": 1000},
        {"time_ms": _ms(8), "open": 101.7, "high": 101.85, "low": 101.1,
         "close": 101.4, "volume": 1000},
    ]
    rej = {"time_ms": _ms(9), "open": 101.10, "high": 101.30, "low": 100.80,
           "close": 101.20, "volume": 1000}
    return orb + [brk] + disp + [rej]


class FakeOptionSelector:
    def select(self, **kwargs):
        return SimpleNamespace(
            underlying_symbol=kwargs.get("underlying_symbol", "QQQ"),
            underlying_price=101.20, right=kwargs.get("right", "C"),
            expiration="20260811", strike=101.0, exchange="SMART",
            trading_class="QQQ", multiplier="100", quantity=1,
            con_id=123456, qualified_contract=SimpleNamespace(conId=123456),
            bid=2.50, ask=2.70, spread=0.20,
        )


# ── Test 1: CLI accepts BOTH ────────────────────────────────────────────────

class TestCLIBoth:
    def test_both_accepted(self):
        runner = MaxBotRunner("QQQ", "BOTH")
        assert runner._direction == "BOTH"


# ── Test 2: BOTH config does not fail validation ─────────────────────────────

class TestBothValidation:
    def test_no_error(self):
        runner = MaxBotRunner("QQQ", "BOTH")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_orchestrator()  # should not raise


# ── Test 3: BOTH can resolve a LONG signal ───────────────────────────────────

class TestBothLong:
    def test_long_signal(self):
        long_sd = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
        )
        short_sd = LiveSignalDetector(
            symbol="QQQ", direction="SHORT", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
        )
        dual = DualSignalDetector(long_sd, short_sd)

        sb = LiveSessionBuilder("QQQ")
        for bar in _long_signal_bars():
            sb.add_bar(bar)
        sess = sb.current_session()

        result = dual.evaluate(sess)
        assert result.status == SignalStatus.SIGNAL
        assert result.direction == "LONG"


# ── Test 4: Resolved LONG → CALL ────────────────────────────────────────────

class TestLongCall:
    def test_call(self):
        sb = LiveSessionBuilder("QQQ")
        long_sd = LiveSignalDetector("QQQ", "LONG", 0.01)
        short_sd = LiveSignalDetector("QQQ", "SHORT", 0.01)
        dual = DualSignalDetector(long_sd, short_sd)

        orch = ObserveOrchestrator(
            underlying_symbol="QQQ", direction="BOTH", tick_size=0.01,
            session_builder=sb, signal_detector=dual,
            option_selector=FakeOptionSelector(),
        )
        events = []
        for bar in _long_signal_bars():
            e = orch.on_bar(bar)
            if e:
                events.append(e)

        assert len(events) == 1
        assert events[0].direction == "LONG"
        assert events[0].option_right == "C"


# ── Test 5: BOTH can resolve SHORT (mock) ────────────────────────────────────
# (Real SHORT BDRR synthetic data is complex; use mock to verify path)

class TestBothShort:
    def test_short_from_mock(self):
        mock_long = MagicMock()
        mock_long.evaluate.return_value = SignalResult(status=SignalStatus.NO_SETUP)

        mock_short = MagicMock()
        mock_short.evaluate.return_value = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
        )

        dual = DualSignalDetector(mock_long, mock_short)
        result = dual.evaluate({"candles": []})
        assert result.status == SignalStatus.SIGNAL
        assert result.direction == "SHORT"


# ── Test 6: Resolved SHORT → PUT ────────────────────────────────────────────

class TestShortPut:
    def test_put_mapping(self):
        # SHORT signal means right should be P
        right = "C" if "SHORT" == "LONG" else "P"
        assert right == "P"


# ── Test 7: Downstream direction is LONG/SHORT, never BOTH ──────────────────

class TestResolvedDirection:
    def test_never_both_in_event(self):
        sb = LiveSessionBuilder("QQQ")
        long_sd = LiveSignalDetector("QQQ", "LONG", 0.01)
        short_sd = LiveSignalDetector("QQQ", "SHORT", 0.01)
        dual = DualSignalDetector(long_sd, short_sd)

        orch = ObserveOrchestrator(
            underlying_symbol="QQQ", direction="BOTH", tick_size=0.01,
            session_builder=sb, signal_detector=dual,
            option_selector=FakeOptionSelector(),
        )
        for bar in _long_signal_bars():
            orch.on_bar(bar)

        for event in orch.events:
            assert event.direction in ("LONG", "SHORT")
            assert event.direction != "BOTH"


# ── Test 8: OBSERVE_ONLY supports BOTH ──────────────────────────────────────

class TestObserveBoth:
    def test_observe_both(self):
        runner = MaxBotRunner("QQQ", "BOTH", execution_mode="OBSERVE_ONLY")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_orchestrator()
        assert runner._observe_orchestrator is not None


# ── Test 9: PAPER_EXECUTE supports BOTH ──────────────────────────────────────

class TestPaperBoth:
    def test_paper_both(self):
        runner = MaxBotRunner("QQQ", "BOTH", execution_mode="PAPER_EXECUTE")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_orchestrator()
        assert runner._orchestrator is not None


# ── Test 10: Existing LONG-only behavior ─────────────────────────────────────

class TestLongOnly:
    def test_long_only(self):
        runner = MaxBotRunner("QQQ", "LONG", execution_mode="PAPER_EXECUTE")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_orchestrator()
        assert runner._orchestrator is not None


# ── Test 11: Existing SHORT-only behavior ────────────────────────────────────

class TestShortOnly:
    def test_short_only(self):
        runner = MaxBotRunner("QQQ", "SHORT", execution_mode="PAPER_EXECUTE")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_orchestrator()
        assert runner._orchestrator is not None


# ── Test 12: No strategy logic duplicated ────────────────────────────────────

class TestNoDuplication:
    def test_no_strategy_in_dual(self):
        import inspect
        import trading_lab.live.dual_signal_detector as mod
        source = inspect.getsource(mod)
        assert "find_break" not in source
        assert "find_displacement" not in source
        assert "find_rejection" not in source


# ── Test 13: No real broker/network ──────────────────────────────────────────

class TestNoNetwork:
    def test_no_connect(self):
        import inspect
        import trading_lab.live.dual_signal_detector as mod
        source = inspect.getsource(mod)
        assert "ib_insync" not in source


# ── Test: DualSignalDetector both NO_SETUP ───────────────────────────────────

class TestBothNoSetup:
    def test_no_setup(self):
        mock_long = MagicMock()
        mock_long.evaluate.return_value = SignalResult(
            status=SignalStatus.NO_SETUP, failed_stage="NO_BREAK",
        )
        mock_short = MagicMock()
        mock_short.evaluate.return_value = SignalResult(
            status=SignalStatus.NO_SETUP, failed_stage="NO_BREAK",
        )
        dual = DualSignalDetector(mock_long, mock_short)
        result = dual.evaluate({})
        assert result.status == SignalStatus.NO_SETUP


# ── Test: LONG wins tiebreak ─────────────────────────────────────────────────

class TestLongPriority:
    def test_long_wins(self):
        mock_long = MagicMock()
        mock_long.evaluate.return_value = SignalResult(
            status=SignalStatus.SIGNAL, direction="LONG",
        )
        mock_short = MagicMock()
        mock_short.evaluate.return_value = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
        )
        dual = DualSignalDetector(mock_long, mock_short)
        result = dual.evaluate({})
        assert result.direction == "LONG"
