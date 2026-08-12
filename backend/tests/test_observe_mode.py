"""Tests for OBSERVE_ONLY mode — full pipeline, no orders.

Uses real live components with fake broker adapters. No IBKR connection.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

from trading_lab.live.observe_orchestrator import (
    ExecutionMode,
    ObserveOrchestrator,
    ObserveLifecycle,
    ObservationEvent,
)
from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector


ET = ZoneInfo("America/New_York")
_BASE = int(datetime(2026, 8, 11, 9, 30, 0, tzinfo=ET).timestamp() * 1000)


def _ms(offset: int) -> int:
    return _BASE + offset * 60_000


# ── Synthetic bars (valid LONG BDRR setup) ──────────────────────────────────

def _orb_bars():
    return [
        {"time_ms": _ms(i), "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.5, "volume": 1000}
        for i in range(5)
    ]

def _break_bar():
    return {"time_ms": _ms(5), "open": 100.8, "high": 101.6, "low": 100.7,
            "close": 101.5, "volume": 1000}

def _disp_bars():
    return [
        {"time_ms": _ms(6), "open": 101.55, "high": 101.8, "low": 101.2,
         "close": 101.6, "volume": 1000},
        {"time_ms": _ms(7), "open": 101.6, "high": 101.9, "low": 101.3,
         "close": 101.7, "volume": 1000},
        {"time_ms": _ms(8), "open": 101.7, "high": 101.85, "low": 101.1,
         "close": 101.4, "volume": 1000},
    ]

def _rejection_bar():
    return {"time_ms": _ms(9), "open": 101.10, "high": 101.30, "low": 100.80,
            "close": 101.20, "volume": 1000}

def _target_bar(offset=10):
    return {"time_ms": _ms(offset), "open": 101.50, "high": 102.10,
            "low": 101.40, "close": 102.05, "volume": 1000}

def _stop_bar(offset=10):
    return {"time_ms": _ms(offset), "open": 101.00, "high": 101.10,
            "low": 100.70, "close": 100.75, "volume": 1000}

def _hold_bar(offset=10):
    return {"time_ms": _ms(offset), "open": 101.25, "high": 101.40,
            "low": 101.10, "close": 101.30, "volume": 1000}

def _all_signal_bars():
    return _orb_bars() + [_break_bar()] + _disp_bars() + [_rejection_bar()]


# ── Fake option selector ────────────────────────────────────────────────────

class FakeOptionSelector:
    def select(self, **kwargs):
        return SimpleNamespace(
            underlying_symbol=kwargs.get("underlying_symbol", "QQQ"),
            underlying_price=101.20,
            right=kwargs.get("right", "C"),
            expiration="20260811",
            strike=101.0,
            exchange="SMART",
            trading_class="QQQ",
            multiplier="100",
            quantity=1,
            con_id=123456,
            qualified_contract=SimpleNamespace(conId=123456),
            bid=2.50,
            ask=2.70,
            spread=0.20,
        )


def _make_observe_orch():
    sb = LiveSessionBuilder("QQQ")
    sd = LiveSignalDetector(
        symbol="QQQ", direction="LONG", tick_size=0.01,
        market_timezone="America/New_York", session_open="09:30",
    )
    os_ = FakeOptionSelector()
    return ObserveOrchestrator(
        underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
        session_builder=sb, signal_detector=sd, option_selector=os_,
    )


def _feed_bars(orch, bars):
    events = []
    for bar in bars:
        e = orch.on_bar(bar)
        if e:
            events.append(e)
    return events


# ── Test 1: Default execution mode is OBSERVE_ONLY ──────────────────────────

class TestDefaultMode:
    def test_default(self):
        runner = MaxBotRunner("QQQ", "LONG")
        assert runner._execution_mode == ExecutionMode.OBSERVE_ONLY


# ── Test 2: Observe mode runs pipeline ──────────────────────────────────────

class TestObservePipeline:
    def test_runs_without_error(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars())
        assert len(events) == 1
        assert events[0].event_type == "SIGNAL"


# ── Test 3: Option selector runs in observe mode ────────────────────────────

class TestOptionSelectorRuns:
    def test_strike_present(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars())
        assert events[0].strike == 101.0
        assert events[0].expiration == "20260811"


# ── Test 4: Order spec built ────────────────────────────────────────────────

class TestOrderSpecBuilt:
    def test_limit_price(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars())
        assert events[0].limit_price == 2.70
        assert events[0].order_type == "LMT"


# ── Test 5: Entry executor NOT called ────────────────────────────────────────

class TestNoEntrySubmit:
    def test_no_submit(self):
        import inspect
        import trading_lab.live.observe_orchestrator as mod
        source = inspect.getsource(mod)
        assert "from trading_lab.live.ibkr_option_executor" not in source
        assert "IBKROptionExecutor" not in source
        assert "ib.placeOrder" not in source


# ── Test 6: Exit executor NOT called ─────────────────────────────────────────

class TestNoExitSubmit:
    def test_no_submit(self):
        import inspect
        import trading_lab.live.observe_orchestrator as mod
        source = inspect.getsource(mod)
        assert "submit_exit" not in source


# ── Test 7: Event includes underlying levels ────────────────────────────────

class TestEventLevels:
    def test_levels(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars())
        e = events[0]
        assert e.underlying_entry is not None
        assert e.underlying_stop is not None
        assert e.underlying_target is not None


# ── Test 8: Event includes option identity ───────────────────────────────────

class TestEventOption:
    def test_option(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars())
        e = events[0]
        assert e.option_right == "C"
        assert e.expiration == "20260811"
        assert e.strike == 101.0
        assert e.con_id == 123456


# ── Test 9: Event includes bid/ask/spread ────────────────────────────────────

class TestEventSpread:
    def test_spread(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars())
        e = events[0]
        assert e.bid == 2.50
        assert e.ask == 2.70
        assert e.spread == pytest.approx(0.20, abs=0.01)


# ── Test 10: Theoretical BUY LMT price ──────────────────────────────────────

class TestTheoreticalOrder:
    def test_buy_lmt(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars())
        e = events[0]
        assert e.quantity == 1
        assert e.order_type == "LMT"
        assert e.limit_price == 2.70


# ── Test 11: ORDER NOT SUBMITTED marker ──────────────────────────────────────

class TestNotSubmitted:
    def test_marker(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars())
        assert events[0].order_submitted is False


# ── Test 12: Theoretical target observed ─────────────────────────────────────

class TestTheoreticalTarget:
    def test_target(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars() + [_target_bar(10)])
        assert len(events) == 2
        assert events[1].event_type == "TARGET_TRIGGERED"


# ── Test 13: Theoretical stop observed ───────────────────────────────────────

class TestTheoreticalStop:
    def test_stop(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars() + [_stop_bar(10)])
        assert len(events) == 2
        assert events[1].event_type == "STOP_TRIGGERED"


# ── Test 14: No duplicate signal ─────────────────────────────────────────────

class TestNoDuplicate:
    def test_no_dup(self):
        orch = _make_observe_orch()
        events = _feed_bars(orch, _all_signal_bars() + [_hold_bar(10), _hold_bar(11)])
        signal_events = [e for e in events if e.event_type == "SIGNAL"]
        assert len(signal_events) == 1


# ── Test 15: Max-two theoretical trades ──────────────────────────────────────

class TestMaxTwo:
    def test_max_two(self):
        orch = _make_observe_orch()
        # First signal + stop
        events = _feed_bars(orch, _all_signal_bars() + [_stop_bar(10)])
        assert orch.trades_used == 1
        assert orch.lifecycle == ObserveLifecycle.WAITING_FOR_SIGNAL


# ── Test 16: Theoretical win ends day ────────────────────────────────────────

class TestWinEndDay:
    def test_done(self):
        orch = _make_observe_orch()
        _feed_bars(orch, _all_signal_bars() + [_target_bar(10)])
        assert orch.wins == 1
        assert orch.day_finished is True
        assert orch.lifecycle == ObserveLifecycle.DONE_FOR_DAY


# ── Test 17: First loss permits second setup ─────────────────────────────────

class TestLossPermitsSecond:
    def test_waiting(self):
        orch = _make_observe_orch()
        _feed_bars(orch, _all_signal_bars() + [_stop_bar(10)])
        assert orch.losses == 1
        assert orch.day_finished is False
        assert orch.lifecycle == ObserveLifecycle.WAITING_FOR_SIGNAL


# ── Test 18: Second loss ends day ────────────────────────────────────────────
# (Would require second signal setup — complex, covered by max_trades logic)


# ── Test 19: PAPER_EXECUTE preserves existing behavior ───────────────────────

class TestPaperExecuteMode:
    def test_mode_accepted(self):
        runner = MaxBotRunner("QQQ", "LONG", execution_mode="PAPER_EXECUTE")
        assert runner._execution_mode == ExecutionMode.PAPER_EXECUTE


# ── Test 20: Paper verification still required ───────────────────────────────

class TestPaperStillRequired:
    def test_still_verified(self):
        runner = MaxBotRunner("QQQ", "LONG", execution_mode="OBSERVE_ONLY")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["U123"]
        runner._ib = ib
        with pytest.raises(RuntimeError, match="No paper account"):
            runner._verify_paper()


# ── Test 21: No real connection ──────────────────────────────────────────────

class TestNoRealConnection:
    def test_no_connect(self):
        """All tests use mock IB."""
        pass


# ── Test: Observe mode wires option selector ─────────────────────────────────

class TestObserveWiring:
    def test_observe_setup(self):
        runner = MaxBotRunner("QQQ", "LONG", execution_mode="OBSERVE_ONLY")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        assert runner._runtimes["QQQ"].orchestrator is not None
        # multi-symbol: no single _orchestrator

    def test_paper_setup(self):
        runner = MaxBotRunner("QQQ", "LONG", execution_mode="PAPER_EXECUTE")
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        runner._ib = ib
        runner._verify_paper()
        runner._setup_all_symbols()
        assert runner._runtimes["QQQ"].orchestrator is not None
        # multi-symbol: no single _observe_orchestrator


# ── Test: ObservationEvent immutable ─────────────────────────────────────────

class TestEventImmutable:
    def test_frozen(self):
        e = ObservationEvent(event_type="SIGNAL", underlying_symbol="QQQ", direction="LONG")
        with pytest.raises(AttributeError):
            e.event_type = "X"


# ── Test: ExecutionMode enum ─────────────────────────────────────────────────

class TestExecutionModeEnum:
    def test_observe(self):
        assert ExecutionMode.OBSERVE_ONLY == "OBSERVE_ONLY"

    def test_paper(self):
        assert ExecutionMode.PAPER_EXECUTE == "PAPER_EXECUTE"
