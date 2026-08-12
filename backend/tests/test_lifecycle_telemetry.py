"""End-to-end lifecycle telemetry tests — proves events are auto-emitted.

Uses real live components with fake broker adapters.
"""

import json
import pytest
from types import SimpleNamespace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.live.event_stream import EventFactory, SessionEventLog, EventType
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector
from trading_lab.live.dual_signal_detector import DualSignalDetector
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator, LifecycleState
from trading_lab.live.observe_orchestrator import ObserveOrchestrator, ObserveLifecycle


ET = ZoneInfo("America/New_York")
_BASE = int(datetime(2026, 8, 11, 9, 30, 0, tzinfo=ET).timestamp() * 1000)


def _ms(offset): return _BASE + offset * 60_000


# ── Synthetic LONG bars ─────────────────────────────────────────────────────

def _orb_bars():
    return [{"time_ms": _ms(i), "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.5, "volume": 1000} for i in range(5)]

def _break_bar():
    return {"time_ms": _ms(5), "open": 100.8, "high": 101.6, "low": 100.7,
            "close": 101.5, "volume": 1000}

def _disp_bars():
    return [
        {"time_ms": _ms(6), "open": 101.55, "high": 101.8, "low": 101.2, "close": 101.6, "volume": 1000},
        {"time_ms": _ms(7), "open": 101.6, "high": 101.9, "low": 101.3, "close": 101.7, "volume": 1000},
        {"time_ms": _ms(8), "open": 101.7, "high": 101.85, "low": 101.1, "close": 101.4, "volume": 1000},
    ]

def _rejection_bar():
    return {"time_ms": _ms(9), "open": 101.10, "high": 101.30, "low": 100.80,
            "close": 101.20, "volume": 1000}

def _target_bar(off=10):
    return {"time_ms": _ms(off), "open": 101.50, "high": 102.10,
            "low": 101.40, "close": 102.05, "volume": 1000}

def _stop_bar(off=10):
    return {"time_ms": _ms(off), "open": 101.00, "high": 101.10,
            "low": 100.70, "close": 100.75, "volume": 1000}

def _hold_bar(off=10):
    return {"time_ms": _ms(off), "open": 101.25, "high": 101.40,
            "low": 101.10, "close": 101.30, "volume": 1000}

def _all_signal_bars():
    return _orb_bars() + [_break_bar()] + _disp_bars() + [_rejection_bar()]


# ── Fake broker ─────────────────────────────────────────────────────────────

class FakeOptionSelector:
    def select(self, **kw):
        return SimpleNamespace(
            underlying_symbol=kw.get("underlying_symbol", "QQQ"),
            underlying_price=101.20, right=kw.get("right", "C"),
            expiration="20260811", strike=101.0, exchange="SMART",
            trading_class="QQQ", multiplier="100", quantity=1,
            con_id=123456, qualified_contract=SimpleNamespace(conId=123456),
            bid=2.50, ask=2.70, spread=0.20,
        )


class FakeEntryExecutor:
    def __init__(self):
        self._status = SimpleNamespace(status="PendingSubmit", filled=0.0, remaining=1.0, avgFillPrice=0.0)
        self._order = SimpleNamespace(orderId=42, permId=999)
        self._fills = []
        self._trade = SimpleNamespace(order=self._order, orderStatus=self._status, fills=self._fills, log=[])

    def submit_entry(self, spec):
        return SimpleNamespace(
            trade=self._trade, con_id=123456, underlying_symbol="QQQ",
            right="C", expiration="20260811", strike=101.0, quantity=1,
            limit_price=2.70, order_id=42, perm_id=999, status="PendingSubmit",
        )

    def set_filled(self, price=2.65):
        self._status.status = "Filled"
        self._status.filled = 1.0
        self._status.remaining = 0.0
        self._status.avgFillPrice = price
        self._fills.append(SimpleNamespace(time=datetime(2026, 8, 11, 9, 42, tzinfo=timezone.utc)))


class FakeExitExecutor:
    def __init__(self):
        self._status = SimpleNamespace(status="PendingSubmit", filled=0.0, remaining=1.0, avgFillPrice=0.0)
        self._order = SimpleNamespace(orderId=55, permId=888)
        self._fills = []
        self._trade = SimpleNamespace(order=self._order, orderStatus=self._status, fills=self._fills, log=[])
        self._submitted = set()

    def submit_exit(self, qualified_contract, exit_trigger, *, entry_order_id, **kw):
        if entry_order_id in self._submitted:
            raise ValueError(f"Already submitted for {entry_order_id}")
        self._submitted.add(entry_order_id)
        from trading_lab.live.underlying_exit_monitor import ExitState
        reason = "TARGET" if exit_trigger.state == ExitState.TARGET_TRIGGERED else "STOP"
        return SimpleNamespace(
            trade=self._trade, exit_reason=reason, entry_order_id=entry_order_id,
            con_id=kw.get("con_id"), underlying_stop_price=exit_trigger.stop_price,
            underlying_target_price=exit_trigger.target_price,
            trigger_bar_time_ms=exit_trigger.trigger_bar_time_ms,
            order_id=55, perm_id=888, status="PendingSubmit",
        )

    def set_filled(self, price=3.10):
        self._status.status = "Filled"
        self._status.filled = 1.0
        self._status.remaining = 0.0
        self._status.avgFillPrice = price
        self._fills.append(SimpleNamespace(time=datetime(2026, 8, 11, 9, 50, tzinfo=timezone.utc)))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_wired_paper(ee=None, xe=None):
    factory = EventFactory("PAPER_EXECUTE")
    log = SessionEventLog(metadata={"execution_mode": "PAPER_EXECUTE", "watchlist": ["QQQ"]})
    sb = LiveSessionBuilder("QQQ")
    sd = LiveSignalDetector("QQQ", "LONG", 0.01)
    tm = DailyTradeManager(unlimited=True)
    ee = ee or FakeEntryExecutor()
    xe = xe or FakeExitExecutor()

    def emit(event_type, symbol="", direction=None, data=None):
        ev = factory.create(event_type, symbol=symbol, direction=direction, data=data)
        log.append(ev)
        return ev

    orch = MaxBotTradeOrchestrator(
        "QQQ", "LONG", 0.01, sb, sd, tm,
        FakeOptionSelector(), ee, xe, emit=emit,
    )
    return orch, log, ee, xe, tm


def _make_wired_observe():
    factory = EventFactory("OBSERVE_ONLY")
    log = SessionEventLog(metadata={"execution_mode": "OBSERVE_ONLY"})
    sb = LiveSessionBuilder("QQQ")
    sd = LiveSignalDetector("QQQ", "LONG", 0.01)

    def emit(event_type, symbol="", direction=None, data=None):
        ev = factory.create(event_type, symbol=symbol, direction=direction, data=data)
        log.append(ev)
        return ev

    orch = ObserveOrchestrator(
        "QQQ", "LONG", 0.01, sb, sd, FakeOptionSelector(), emit=emit,
    )
    return orch, log


# ── Test: Full PAPER WIN lifecycle ───────────────────────────────────────────

class TestPaperWinLifecycle:
    def test_complete_sequence(self):
        orch, log, ee, xe, tm = _make_wired_paper()

        # Feed signal bars
        for bar in _all_signal_bars():
            orch.on_bar(bar)
        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED

        # Simulate fill
        ee.set_filled(2.65)
        orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.POSITION_OPEN

        # Target bar
        orch.on_bar(_target_bar(10))
        assert orch.lifecycle == LifecycleState.EXIT_SUBMITTED

        # Exit fill
        xe.set_filled(3.10)
        orch.refresh_exit_status()

        # Verify event sequence
        types = [e.event_type for e in log.events]
        assert "SIGNAL" in types
        assert "OPTION_SELECTED" in types
        assert "ENTRY_ORDER_BUILT" in types
        assert "ENTRY_SUBMITTED" in types
        assert "ENTRY_FILLED" in types
        assert "POSITION_OPEN" in types
        assert "TARGET_TRIGGERED" in types
        assert "EXIT_SUBMITTED" in types
        assert "EXIT_FILLED" in types
        assert "TRADE_WIN" in types
        assert "TRADE_COMPLETED" in types

        # Verify ordering
        idx = {t: i for i, t in enumerate(types)}
        assert idx["SIGNAL"] < idx["OPTION_SELECTED"]
        assert idx["ENTRY_SUBMITTED"] < idx["ENTRY_FILLED"]
        assert idx["TARGET_TRIGGERED"] < idx["EXIT_SUBMITTED"]
        assert idx["EXIT_FILLED"] < idx["TRADE_COMPLETED"]


# ── Test: Full PAPER LOSS lifecycle ──────────────────────────────────────────

class TestPaperLossLifecycle:
    def test_stop_loss(self):
        orch, log, ee, xe, tm = _make_wired_paper()
        for bar in _all_signal_bars():
            orch.on_bar(bar)
        ee.set_filled(2.65)
        orch.refresh_entry_status()
        orch.on_bar(_stop_bar(10))
        xe.set_filled(1.90)
        orch.refresh_exit_status()

        types = [e.event_type for e in log.events]
        assert "STOP_TRIGGERED" in types
        assert "TRADE_LOSS" in types
        assert "TRADE_COMPLETED" in types

        # Verify TRADE_COMPLETED has actual premiums
        completed = [e for e in log.events if e.event_type == "TRADE_COMPLETED"][0]
        assert completed.data["result"] == "LOSS"
        assert completed.data["entry_fill_premium"] == 2.65
        assert completed.data["exit_fill_premium"] == 1.90
        assert completed.data["gross_pnl"] == -75.0


# ── Test: Observe lifecycle ──────────────────────────────────────────────────

class TestObserveLifecycle:
    def test_observe_events(self):
        orch, log = _make_wired_observe()
        for bar in _all_signal_bars():
            orch.on_bar(bar)
        orch.on_bar(_target_bar(10))

        types = [e.event_type for e in log.events]
        assert "SIGNAL" in types
        assert "OPTION_SELECTED" in types
        assert "ENTRY_ORDER_BUILT" in types
        assert "OBSERVE_ENTRY" in types
        assert "OBSERVE_TARGET" in types

        # No broker events
        assert "ENTRY_SUBMITTED" not in types
        assert "ENTRY_FILLED" not in types
        assert "EXIT_SUBMITTED" not in types
        assert "EXIT_FILLED" not in types


# ── Test: Duplicate prevention ───────────────────────────────────────────────

class TestDuplicatePrevention:
    def test_no_duplicate_fill(self):
        orch, log, ee, xe, tm = _make_wired_paper()
        for bar in _all_signal_bars():
            orch.on_bar(bar)
        ee.set_filled(2.65)
        orch.refresh_entry_status()
        orch.refresh_entry_status()  # duplicate
        orch.refresh_entry_status()  # duplicate
        filled_count = sum(1 for e in log.events if e.event_type == "ENTRY_FILLED")
        assert filled_count == 1
        open_count = sum(1 for e in log.events if e.event_type == "POSITION_OPEN")
        assert open_count == 1

    def test_no_duplicate_exit_fill(self):
        orch, log, ee, xe, tm = _make_wired_paper()
        for bar in _all_signal_bars():
            orch.on_bar(bar)
        ee.set_filled()
        orch.refresh_entry_status()
        orch.on_bar(_target_bar(10))
        xe.set_filled()
        orch.refresh_exit_status()
        orch.refresh_exit_status()
        orch.refresh_exit_status()
        assert sum(1 for e in log.events if e.event_type == "EXIT_FILLED") == 1
        assert sum(1 for e in log.events if e.event_type == "TRADE_WIN") == 1
        assert sum(1 for e in log.events if e.event_type == "TRADE_COMPLETED") == 1


# ── Test: Direction preserved ────────────────────────────────────────────────

class TestDirectionPreserved:
    def test_long_direction(self):
        orch, log, *_ = _make_wired_paper()
        for bar in _all_signal_bars():
            orch.on_bar(bar)
        signals = [e for e in log.events if e.event_type == "SIGNAL"]
        assert signals[0].direction == "LONG"

    def test_call_option(self):
        orch, log, *_ = _make_wired_paper()
        for bar in _all_signal_bars():
            orch.on_bar(bar)
        opts = [e for e in log.events if e.event_type == "OPTION_SELECTED"]
        assert opts[0].data["right"] == "C"


# ── Test: Multi-symbol shared log ────────────────────────────────────────────

class TestMultiSymbolLog:
    def test_shared_sequence(self):
        factory = EventFactory("OBSERVE_ONLY")
        log = SessionEventLog()

        def emit(event_type, symbol="", direction=None, data=None):
            ev = factory.create(event_type, symbol=symbol, direction=direction, data=data)
            log.append(ev)
            return ev

        sb1 = LiveSessionBuilder("QQQ")
        sd1 = LiveSignalDetector("QQQ", "LONG", 0.01)
        o1 = ObserveOrchestrator("QQQ", "LONG", 0.01, sb1, sd1, FakeOptionSelector(), emit=emit)

        sb2 = LiveSessionBuilder("SPY")
        sd2 = LiveSignalDetector("SPY", "LONG", 0.01)
        o2 = ObserveOrchestrator("SPY", "LONG", 0.01, sb2, sd2, FakeOptionSelector(), emit=emit)

        # Feed bars to QQQ only (to get a signal)
        for bar in _all_signal_bars():
            o1.on_bar(bar)

        all_events = log.events
        seqs = [e.seq for e in all_events]
        assert seqs == sorted(seqs)  # monotonic
        assert all(e.symbol == "QQQ" for e in all_events if e.symbol)


# ── Test: Cancelled entry event ──────────────────────────────────────────────

class TestCancelledEntry:
    def test_cancelled(self):
        ee = FakeEntryExecutor()
        orch, log, _, _, _ = _make_wired_paper(ee=ee)
        for bar in _all_signal_bars():
            orch.on_bar(bar)
        ee._status.status = "Cancelled"
        orch.refresh_entry_status()
        types = [e.event_type for e in log.events]
        assert "ENTRY_CANCELLED" in types


# ── Test: JSON export reconstructable ────────────────────────────────────────

class TestExportReconstruct:
    def test_full_lifecycle_json(self, tmp_path):
        orch, log, ee, xe, tm = _make_wired_paper()
        for bar in _all_signal_bars():
            orch.on_bar(bar)
        ee.set_filled(2.65)
        orch.refresh_entry_status()
        orch.on_bar(_target_bar(10))
        xe.set_filled(3.10)
        orch.refresh_exit_status()

        p = log.export_json(tmp_path / "lifecycle.json")
        data = json.loads(p.read_text())

        types = [e["event_type"] for e in data["events"]]
        assert "SIGNAL" in types
        assert "ENTRY_FILLED" in types
        assert "TARGET_TRIGGERED" in types
        assert "EXIT_FILLED" in types
        assert "TRADE_COMPLETED" in types

        completed = [e for e in data["events"] if e["event_type"] == "TRADE_COMPLETED"][0]
        assert completed["data"]["result"] == "WIN"
        assert completed["data"]["entry_fill_premium"] == 2.65
        assert completed["data"]["exit_fill_premium"] == 3.10


# ── Test: No strategy duplication ────────────────────────────────────────────

class TestNoDuplication:
    def test_no_strategy(self):
        # Event emission doesn't duplicate strategy logic
        import inspect
        import trading_lab.live.trade_orchestrator as mod
        source = inspect.getsource(mod)
        assert "find_break" not in source
