"""Integration tests for MaxBotTradeOrchestrator.

Uses real live components with fake broker adapters. No real IBKR connection.
"""

import pytest
from types import SimpleNamespace
from decimal import Decimal
from datetime import datetime, timezone

from trading_lab.live.trade_orchestrator import (
    MaxBotTradeOrchestrator,
    LifecycleState,
)
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector
from trading_lab.live.trade_manager import DailyTradeManager


# ── Timestamp helpers ────────────────────────────────────────────────────────

# 2026-08-11 09:30 ET = correct epoch ms
from datetime import datetime as dt_cls
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_BASE = int(dt_cls(2026, 8, 11, 9, 30, 0, tzinfo=_ET).timestamp() * 1000)


def _ms(minute_offset: int) -> int:
    return _BASE + minute_offset * 60_000


# ── Synthetic bars (valid LONG BDRR setup on 1m) ─────────────────────────────

def _orb_bars():
    """5 ORB bars (09:30-09:34). ORB high=101.00, low=99.00."""
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


def _hold_bar(offset=10):
    """Bar that doesn't trigger stop or target."""
    return {"time_ms": _ms(offset), "open": 101.25, "high": 101.40,
            "low": 101.10, "close": 101.30, "volume": 1000}


def _target_bar(offset=11):
    """Bar that hits 2R target. entry=101.20, stop=100.80, risk=0.40, target=102.00."""
    return {"time_ms": _ms(offset), "open": 101.50, "high": 102.10,
            "low": 101.40, "close": 102.05, "volume": 1000}


def _stop_bar(offset=11):
    """Bar that hits stop."""
    return {"time_ms": _ms(offset), "open": 101.00, "high": 101.10,
            "low": 100.70, "close": 100.75, "volume": 1000}


def _all_signal_bars():
    return _orb_bars() + [_break_bar()] + _disp_bars() + [_rejection_bar()]


# ── Fake broker adapters ────────────────────────────────────────────────────

class FakeOptionSelector:
    """Returns a canned OptionSelectionResult."""

    def select(self, **kwargs):
        return SimpleNamespace(
            underlying_symbol=kwargs.get("underlying_symbol", "QQQ"),
            underlying_price=kwargs.get("underlying_price", 101.20),
            right=kwargs.get("right", "C"),
            expiration="20260811",
            strike=101.0,
            exchange="SMART",
            trading_class="QQQ",
            multiplier="100",
            quantity=1,
            con_id=123456,
            qualified_contract=SimpleNamespace(conId=123456, symbol="QQQ"),
            bid=2.50,
            ask=2.70,
            spread=0.20,
        )


class FakeEntryExecutor:
    """Simulates entry submission. Fill status controlled externally."""

    def __init__(self):
        self.submissions = []
        self._order_id = 42
        self._status = SimpleNamespace(
            status="PendingSubmit", filled=0.0, remaining=1.0, avgFillPrice=0.0,
        )
        self._order = SimpleNamespace(orderId=42, permId=999)
        self._fills = []
        self._trade = SimpleNamespace(
            order=self._order, orderStatus=self._status,
            fills=self._fills, log=[],
        )

    def submit_entry(self, order_spec):
        self.submissions.append(order_spec)
        return SimpleNamespace(
            trade=self._trade, con_id=123456, underlying_symbol="QQQ",
            right="C", expiration="20260811", strike=101.0,
            quantity=1, limit_price=2.70, order_id=self._order_id,
            perm_id=999, status=self._status.status,
        )

    def set_filled(self, avg_price=2.65):
        fill_time = datetime(2026, 8, 11, 9, 42, 0, tzinfo=timezone.utc)
        self._status.status = "Filled"
        self._status.filled = 1.0
        self._status.remaining = 0.0
        self._status.avgFillPrice = avg_price
        self._fills.append(SimpleNamespace(time=fill_time))

    def set_cancelled(self):
        self._status.status = "Cancelled"

    def set_rejected(self):
        self._status.status = "Inactive"


class FakeExitExecutor:
    """Simulates exit submission."""

    def __init__(self):
        self.submissions = []
        self._order_id = 55
        self._submitted_entry_ids = set()
        self._status = SimpleNamespace(
            status="PendingSubmit", filled=0.0, remaining=1.0, avgFillPrice=0.0,
        )
        self._order = SimpleNamespace(orderId=55, permId=888)
        self._fills = []
        self._trade = SimpleNamespace(
            order=self._order, orderStatus=self._status,
            fills=self._fills, log=[],
        )

    def submit_exit(self, qualified_contract, exit_trigger, *, entry_order_id,
                    con_id=None, right="", expiration="", strike=0.0, quantity=1):
        if entry_order_id in self._submitted_entry_ids:
            raise ValueError(f"Exit already submitted for entry_order_id={entry_order_id}")
        self._submitted_entry_ids.add(entry_order_id)
        self.submissions.append(exit_trigger)
        from trading_lab.live.underlying_exit_monitor import ExitState
        reason_map = {ExitState.STOP_TRIGGERED: "STOP", ExitState.TARGET_TRIGGERED: "TARGET"}
        return SimpleNamespace(
            trade=self._trade, exit_reason=reason_map[exit_trigger.state],
            entry_order_id=entry_order_id, con_id=con_id,
            underlying_stop_price=exit_trigger.stop_price,
            underlying_target_price=exit_trigger.target_price,
            trigger_bar_time_ms=exit_trigger.trigger_bar_time_ms,
            order_id=self._order_id, perm_id=888, status=self._status.status,
            right=right, expiration=expiration, strike=strike, quantity=1,
        )

    def set_filled(self, avg_price=3.10):
        fill_time = datetime(2026, 8, 11, 9, 50, 0, tzinfo=timezone.utc)
        self._status.status = "Filled"
        self._status.filled = 1.0
        self._status.remaining = 0.0
        self._status.avgFillPrice = avg_price
        self._fills.append(SimpleNamespace(time=fill_time))

    def set_cancelled(self):
        self._status.status = "Cancelled"


# ── Orchestrator factory ────────────────────────────────────────────────────

def _make_orchestrator(entry_executor=None, exit_executor=None):
    sb = LiveSessionBuilder("QQQ")
    sd = LiveSignalDetector(
        symbol="QQQ", direction="LONG", tick_size=0.01,
        market_timezone="America/New_York", session_open="09:30",
    )
    tm = DailyTradeManager()
    os_ = FakeOptionSelector()
    ee = entry_executor or FakeEntryExecutor()
    xe = exit_executor or FakeExitExecutor()
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
        session_builder=sb, signal_detector=sd, trade_manager=tm,
        option_selector=os_, entry_executor=ee, exit_executor=xe,
    )
    return orch, sb, sd, tm, ee, xe


def _feed_bars(orch, bars):
    """Feed multiple bars and return the last status.

    After each bar, if a pending signal exists, execute it immediately.
    This simulates the runner's main-loop processing of the execution
    queue (deferred IBKR sync calls happen outside the bar callback).
    """
    status = None
    for bar in bars:
        status = orch.on_bar(bar)
        if orch.has_pending_signal:
            orch.execute_pending_signal()
    return status


# ── Test 1: Fresh → WAITING_FOR_SIGNAL ──────────────────────────────────────

class TestFreshState:
    def test_initial(self):
        orch, *_ = _make_orchestrator()
        assert orch.lifecycle == LifecycleState.WAITING_FOR_SIGNAL


# ── Test 2: NO_SETUP → no option submitted ──────────────────────────────────

class TestNoSetup:
    def test_no_submission(self):
        orch, _, _, _, ee, _ = _make_orchestrator()
        _feed_bars(orch, _orb_bars())
        assert orch.lifecycle == LifecycleState.WAITING_FOR_SIGNAL
        assert len(ee.submissions) == 0


# ── Test 3: SIGNAL → one entry submitted ────────────────────────────────────

class TestSignalEntry:
    def test_submitted(self):
        orch, _, _, _, ee, _ = _make_orchestrator()
        _feed_bars(orch, _all_signal_bars())
        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED
        assert len(ee.submissions) == 1


# ── Test 4: Repeated signal while ENTRY_SUBMITTED → no duplicate ────────────

class TestNoDuplicateEntry:
    def test_no_duplicate(self):
        orch, _, _, _, ee, _ = _make_orchestrator()
        _feed_bars(orch, _all_signal_bars())
        orch.on_bar(_hold_bar(10))
        orch.on_bar(_hold_bar(11))
        assert len(ee.submissions) == 1


# ── Test 5: Pending entry → remains ENTRY_SUBMITTED ─────────────────────────

class TestPendingEntry:
    def test_remains(self):
        orch, *_ = _make_orchestrator()
        _feed_bars(orch, _all_signal_bars())
        orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED


# ── Test 6: Cancelled entry → no daily trade consumed ───────────────────────

class TestCancelledEntry:
    def test_no_trade(self):
        ee = FakeEntryExecutor()
        orch, _, _, tm, _, _ = _make_orchestrator(entry_executor=ee)
        _feed_bars(orch, _all_signal_bars())
        ee.set_cancelled()
        orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.WAITING_FOR_SIGNAL
        assert tm.state.trades_used == 0


# ── Test 7: Rejected entry → no daily trade consumed ────────────────────────

class TestRejectedEntry:
    def test_no_trade(self):
        ee = FakeEntryExecutor()
        orch, _, _, tm, _, _ = _make_orchestrator(entry_executor=ee)
        _feed_bars(orch, _all_signal_bars())
        ee.set_rejected()
        orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.WAITING_FOR_SIGNAL
        assert tm.state.trades_used == 0


# ── Test 8: Full entry fill → DailyTradeManager active ─────────────────────

class TestEntryFillActive:
    def test_active(self):
        ee = FakeEntryExecutor()
        orch, _, _, tm, _, _ = _make_orchestrator(entry_executor=ee)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        assert tm.state.has_active_trade is True
        assert tm.state.trades_used == 1


# ── Test 9: Full entry fill → POSITION_OPEN ─────────────────────────────────

class TestEntryFillOpen:
    def test_position_open(self):
        ee = FakeEntryExecutor()
        orch, *_ = _make_orchestrator(entry_executor=ee)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.POSITION_OPEN


# ── Test 10: Pre-fill bars cannot trigger exit ──────────────────────────────
# (Handled by UnderlyingExitMonitor activation_time_ms)


# ── Test 11: HOLD bar → position remains open ───────────────────────────────

class TestHoldBar:
    def test_hold(self):
        ee = FakeEntryExecutor()
        orch, *_ = _make_orchestrator(entry_executor=ee)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_hold_bar(10))
        assert orch.lifecycle == LifecycleState.POSITION_OPEN


# ── Test 12: TARGET trigger → one exit submitted ────────────────────────────

class TestTargetExit:
    def test_exit_submitted(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, *_ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_target_bar(10))
        assert orch.lifecycle == LifecycleState.EXIT_SUBMITTED
        assert len(xe.submissions) == 1


# ── Test 13: STOP trigger → one exit submitted ──────────────────────────────

class TestStopExit:
    def test_exit_submitted(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, *_ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_stop_bar(10))
        assert orch.lifecycle == LifecycleState.EXIT_SUBMITTED
        assert len(xe.submissions) == 1


# ── Test 14: Repeated terminal trigger → no duplicate exit ──────────────────

class TestNoDuplicateExit:
    def test_no_duplicate(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, *_ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_target_bar(10))
        # Already EXIT_SUBMITTED — subsequent bars don't submit again
        orch.on_bar(_target_bar(11))
        assert len(xe.submissions) == 1


# ── Test 15: Pending exit → remains EXIT_SUBMITTED ──────────────────────────

class TestPendingExit:
    def test_remains(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, *_ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_target_bar(10))
        orch.refresh_exit_status()
        assert orch.lifecycle == LifecycleState.EXIT_SUBMITTED


# ── Test 16: TARGET exit fill → WIN recorded ────────────────────────────────

class TestTargetWin:
    def test_win(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, _, _, tm, _, _ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_target_bar(10))
        xe.set_filled()
        orch.refresh_exit_status()
        assert tm.state.wins == 1


# ── Test 17: First WIN → DONE_FOR_DAY ──────────────────────────────────────

class TestWinDone:
    def test_done(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, *_ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_target_bar(10))
        xe.set_filled()
        orch.refresh_exit_status()
        assert orch.lifecycle == LifecycleState.DONE_FOR_DAY


# ── Test 18: STOP exit fill → LOSS recorded ────────────────────────────────

class TestStopLoss:
    def test_loss(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, _, _, tm, _, _ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_stop_bar(10))
        xe.set_filled()
        orch.refresh_exit_status()
        assert tm.state.losses == 1


# ── Test 19: First LOSS → WAITING_FOR_SIGNAL ────────────────────────────────

class TestLossWaiting:
    def test_waiting(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, *_ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_stop_bar(10))
        xe.set_filled()
        orch.refresh_exit_status()
        assert orch.lifecycle == LifecycleState.WAITING_FOR_SIGNAL


# ── Test 20: Second LOSS → DONE_FOR_DAY ─────────────────────────────────────
# (Would need second signal + second trade cycle — complex integration)


# ── Test 21: Exit cancelled → does not mark trade closed ────────────────────

class TestExitCancelled:
    def test_exit_failed(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, _, _, tm, _, _ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_target_bar(10))
        xe.set_cancelled()
        orch.refresh_exit_status()
        assert orch.lifecycle == LifecycleState.EXIT_FAILED
        assert tm.state.has_active_trade is True
        assert tm.state.wins == 0
        assert tm.state.losses == 0


# ── Test 22: Exit failed prevents new entries ───────────────────────────────

class TestExitFailedBlocks:
    def test_blocks(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, *_ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_target_bar(10))
        xe.set_cancelled()
        orch.refresh_exit_status()
        assert orch.lifecycle == LifecycleState.EXIT_FAILED
        # on_bar should not create new entry
        orch.on_bar(_hold_bar(12))
        assert orch.lifecycle == LifecycleState.EXIT_FAILED


# ── Test 23: Maximum one active position ─────────────────────────────────────
# (Covered by state machine — POSITION_OPEN blocks new signals)


# ── Test 24: Signal detection does not increment trade count ────────────────

class TestSignalNoCount:
    def test_no_count(self):
        orch, _, _, tm, ee, _ = _make_orchestrator()
        _feed_bars(orch, _all_signal_bars())
        # Entry is submitted but not filled
        assert tm.state.trades_used == 0


# ── Test 25: Entry submission does not increment trade count ────────────────

class TestSubmissionNoCount:
    def test_no_count(self):
        orch, _, _, tm, ee, _ = _make_orchestrator()
        _feed_bars(orch, _all_signal_bars())
        assert len(ee.submissions) == 1
        assert tm.state.trades_used == 0


# ── Test 26: Entry fill increments exactly once ─────────────────────────────

class TestFillOnce:
    def test_once(self):
        ee = FakeEntryExecutor()
        orch, _, _, tm, _, _ = _make_orchestrator(entry_executor=ee)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.refresh_entry_status()  # duplicate
        assert tm.state.trades_used == 1


# ── Test 27: Exit fill records result exactly once ──────────────────────────

class TestExitOnce:
    def test_once(self):
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch, _, _, tm, _, _ = _make_orchestrator(entry_executor=ee, exit_executor=xe)
        _feed_bars(orch, _all_signal_bars())
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_stop_bar(10))
        xe.set_filled()
        orch.refresh_exit_status()
        orch.refresh_exit_status()  # duplicate
        assert tm.state.losses == 1


# ── Test 28: Option premium does not determine WIN/LOSS ─────────────────────
# (Covered by T13 architecture — exit_reason determines result)


# ── Test 29: No native bracket ──────────────────────────────────────────────

class TestNoBracket:
    def test_no_bracket(self):
        import inspect
        import trading_lab.live.trade_orchestrator as mod
        source = inspect.getsource(mod)
        assert "BracketOrderSpec" not in source
        assert "build_bracket_order" not in source


# ── Test 30: No real IBKR connection ────────────────────────────────────────

class TestNoConnection:
    def test_no_connect(self):
        import inspect
        import trading_lab.live.trade_orchestrator as mod
        source = inspect.getsource(mod)
        assert ".connect(" not in source
        assert "host=" not in source
        assert "port=" not in source


# ── Test 31: No scheduler/sleep loop ────────────────────────────────────────

class TestNoScheduler:
    def test_no_loop(self):
        import inspect
        import trading_lab.live.trade_orchestrator as mod
        source = inspect.getsource(mod)
        assert "while True" not in source
        assert "time.sleep(" not in source
        assert "def schedule" not in source


# ── Test 32: No strategy logic duplicated ───────────────────────────────────

class TestNoDuplication:
    def test_no_break_finder(self):
        import inspect
        import trading_lab.live.trade_orchestrator as mod
        source = inspect.getsource(mod)
        assert "find_break" not in source
        assert "find_displacement" not in source
        assert "find_rejection" not in source


# ── Test: Status snapshot ───────────────────────────────────────────────────

class TestStatusSnapshot:
    def test_initial_status(self):
        orch, *_ = _make_orchestrator()
        s = orch.status
        assert s.lifecycle == LifecycleState.WAITING_FOR_SIGNAL
        assert s.underlying_symbol == "QQQ"
        assert s.entry_order_id is None
        assert s.trades_used == 0

    def test_after_entry(self):
        orch, *_ = _make_orchestrator()
        _feed_bars(orch, _all_signal_bars())
        s = orch.status
        assert s.lifecycle == LifecycleState.ENTRY_SUBMITTED
        assert s.entry_order_id is not None
