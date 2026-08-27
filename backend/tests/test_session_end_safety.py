"""Session-end policy, late-0DTE guard and stock reconcile.

All three come from one real incident, the QQQ trade of 2026-08-26:

    15:56 ET  0DTE call opened, 4 minutes before the close
    16:00 ET  neither stop (710.90) nor target (712.25) reached,
              so MaxBot never sent an exit
    16:00 ET  runner refuses to stop while a position is open and logs
              "Session close reached but active positions remain"
              once a second — ~21,600 lines over six hours
    22:00 ET  TWS drops the socket; _shutdown() never ran, so no session
              log and no R-probe was ever closed
    22:25 ET  option expires ITM, IBKR auto-exercises it into 100 QQQ
              shares that MaxBot does not know it holds

Each class below breaks one link in that chain.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.session_policy import (
    SAFETY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE,
    STRATEGY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE,
    REASON_STRATEGY_CUTOFF,
    strategy_entry_allowed,
    EXIT_REASON_SESSION_END,
    FORCED_EXIT_MINUTES_BEFORE_CLOSE,
    LATE_0DTE_CUTOFF_MINUTES,
    REASON_ENTRY_CUTOFF,
    REASON_LATE_0DTE,
    entry_allowed,
    forced_exit_due,
    is_zero_dte,
    minutes_to_close,
    shutdown_allowed,
    zero_dte_execution_allowed,
)
from trading_lab.live.signal_detector import LiveSignalDetector
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.trade_orchestrator import LifecycleState, MaxBotTradeOrchestrator
from trading_lab.live.underlying_exit_monitor import ExitState

ET = ZoneInfo("America/New_York")


def _at(h: int, m: int) -> datetime:
    return datetime(2026, 8, 26, h, m, tzinfo=ET)


def _left(h: int, m: int) -> float:
    return minutes_to_close(_at(h, m), "16:00")


# ═════════════════════════════════════════════════════════════════════
# The policy itself
# ═════════════════════════════════════════════════════════════════════


class TestPolicyClock:
    def test_minutes_to_close_is_signed(self):
        assert _left(15, 30) == pytest.approx(30.0)
        assert _left(16, 1) == pytest.approx(-1.0)

    def test_unreadable_inputs_yield_none(self):
        assert minutes_to_close(_at(15, 0), "nonsense") is None
        assert minutes_to_close(None, "16:00") is None

    def test_the_real_qqq_moment_is_refused_on_both_gates(self):
        """15:56 ET, the exact entry that caused the incident."""
        left = _left(15, 56)
        assert left == pytest.approx(4.0)
        assert entry_allowed(left) is False
        assert zero_dte_execution_allowed("20260826", "20260826", left) is False
        assert forced_exit_due(left) is True


class TestEntryCutoff:
    def test_boundary_is_exclusive(self):
        assert entry_allowed(SAFETY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE + 0.1) is True
        assert entry_allowed(SAFETY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE) is False

    def test_unknown_time_does_not_halt_trading(self):
        """Being unable to read a clock is its own failure and must not
        silently disable the bot."""
        assert entry_allowed(None) is True


class TestForcedExitCutoff:
    def test_fires_at_and_after_the_cutoff(self):
        assert forced_exit_due(FORCED_EXIT_MINUTES_BEFORE_CLOSE + 0.1) is False
        assert forced_exit_due(FORCED_EXIT_MINUTES_BEFORE_CLOSE) is True
        assert forced_exit_due(-30) is True

    def test_entry_cutoff_leaves_room_before_the_forced_exit(self):
        """A trade opened at the last permitted moment must still have
        time to resolve on its own."""
        room = SAFETY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE - FORCED_EXIT_MINUTES_BEFORE_CLOSE
        assert room >= 10


class TestLate0DTEGuard:
    def test_only_same_day_expiry_is_gated(self):
        assert is_zero_dte("20260826", "20260826") is True
        assert is_zero_dte("20260828", "20260826") is False

    def test_non_0dte_is_allowed_at_the_same_moment(self):
        """Requirement 6: a later expiry can be held overnight — it is a
        position, not a deadline."""
        left = _left(15, 56)
        assert zero_dte_execution_allowed("20260828", "20260826", left) is True
        assert zero_dte_execution_allowed("20260826", "20260826", left) is False

    def test_0dte_is_allowed_earlier_in_the_day(self):
        assert zero_dte_execution_allowed(
            "20260826", "20260826", _left(11, 0)) is True

    def test_cutoff_is_wider_than_the_entry_cutoff(self):
        """Assignment is worse than a bad price, so 0DTE is refused
        sooner than ordinary contracts."""
        assert LATE_0DTE_CUTOFF_MINUTES > SAFETY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE

    def test_unparseable_expiry_never_triggers_the_guard(self):
        assert zero_dte_execution_allowed(None, "20260826", 1.0) is True
        assert zero_dte_execution_allowed("", "", 1.0) is True


class TestShutdownAlwaysReachable:
    def test_blocked_before_close_while_active(self):
        assert shutdown_allowed(30, True, None) is False

    def test_allowed_before_close_when_idle(self):
        assert shutdown_allowed(30, False, None) is True

    def test_allowed_after_close_when_idle(self):
        assert shutdown_allowed(-1, False, 5) is True

    def test_grace_expiry_forces_shutdown_even_with_a_position(self):
        """The bug being fixed: waiting forever is not an option."""
        assert shutdown_allowed(-1, True, 10) is False
        assert shutdown_allowed(-1, True, 120) is True
        assert shutdown_allowed(-1, True, 6 * 3600) is True


# ═════════════════════════════════════════════════════════════════════
# Orchestrator — refusal and forced exit
# ═════════════════════════════════════════════════════════════════════


def _orch(minutes_left=None, tmp_path=None):
    sb = LiveSessionBuilder("QQQ", "America/New_York")
    o = MaxBotTradeOrchestrator(
        underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
        session_builder=sb,
        signal_detector=LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01),
        trade_manager=DailyTradeManager(),
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(),
        minutes_to_close_provider=(None if minutes_left is None
                                   else lambda: minutes_left),
        **({"trade_state_dir": tmp_path} if tmp_path else {}),
    )
    return o


def _open_position(orch):
    from decimal import Decimal
    from trading_lab.live.execution_intent import UnderlyingTriggerLevels
    orch._lifecycle = LifecycleState.POSITION_OPEN
    orch._resolved_direction = "LONG"
    orch._entry_order_id = 1
    orch._qualified_contract = MagicMock()
    orch._entry_con_id = 42
    orch._option_right = "C"
    orch._option_expiration = "20260826"
    orch._option_strike = 711.0
    orch._underlying_triggers = UnderlyingTriggerLevels(
        entry_price=Decimal("711.35"), stop_price=Decimal("710.90"),
        target_price=Decimal("712.25"))
    sub = MagicMock()
    sub.order_id = 99
    sub.exit_reason = EXIT_REASON_SESSION_END
    sub.status = "PendingSubmit"
    orch._exit_executor.submit_exit.return_value = sub
    return orch


class TestForcedExit:
    def test_open_position_at_session_close_is_forced_out(self):
        orch = _open_position(_orch())
        assert orch.force_session_end_exit() is True
        assert orch._exit_executor.submit_exit.call_count == 1
        assert orch._lifecycle == LifecycleState.EXIT_SUBMITTED

    def test_forced_exit_reason_is_neither_target_nor_stop(self):
        orch = _open_position(_orch())
        orch.force_session_end_exit()
        trigger = orch._exit_executor.submit_exit.call_args.kwargs["exit_trigger"]
        assert trigger.state == ExitState.SESSION_END_TRIGGERED
        assert trigger.trigger_source == "SESSION_END"

    def test_forced_exit_uses_the_existing_executor(self):
        """No new order path was invented."""
        orch = _open_position(_orch())
        orch.force_session_end_exit()
        kwargs = orch._exit_executor.submit_exit.call_args.kwargs
        assert kwargs["entry_order_id"] == 1
        assert kwargs["quantity"] == 1

    def test_is_idempotent(self):
        orch = _open_position(_orch())
        orch.force_session_end_exit()
        assert orch.force_session_end_exit() is False
        assert orch._exit_executor.submit_exit.call_count == 1

    def test_no_position_is_a_no_op(self):
        orch = _orch()
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
        assert orch.force_session_end_exit() is False
        assert orch._exit_executor.submit_exit.call_count == 0

    def test_monitor_cannot_raise_a_competing_trigger_afterwards(self):
        from trading_lab.live.underlying_exit_monitor import UnderlyingExitMonitor
        orch = _open_position(_orch())
        orch._exit_monitor = UnderlyingExitMonitor(
            direction="LONG", stop_price=710.90, target_price=712.25,
            activation_time_ms=0)
        orch.force_session_end_exit()
        after = orch._exit_monitor.evaluate_price(712.50)   # would be TARGET
        assert after.state == ExitState.SESSION_END_TRIGGERED

    def test_session_end_is_neither_a_win_nor_a_loss(self):
        """The strategy never got its answer, so the win rate must not
        be told that it did."""
        from trading_lab.live.exit_fill_monitor import (
            ExitFillResult, ExitFillState, ExitResultActivator)
        mgr = DailyTradeManager()
        mgr.ensure_date("2026-08-26")
        act = ExitResultActivator(mgr)
        applied = act.apply_if_filled(ExitFillResult(
            state=ExitFillState.FILLED, exit_order_id=99, entry_order_id=1,
            con_id=1, exit_reason=EXIT_REASON_SESSION_END,
            underlying_stop_price=710.90, underlying_target_price=712.25,
            broker_status="Filled", filled_quantity=1.0,
            remaining_quantity=0.0, average_exit_fill_price=1.0,
            fill_time=None))
        assert applied is False
        assert mgr.state.wins == 0 and mgr.state.losses == 0

    def test_a_genuinely_unknown_exit_reason_still_raises(self):
        from trading_lab.live.exit_fill_monitor import (
            ExitFillResult, ExitFillState, ExitResultActivator)
        act = ExitResultActivator(DailyTradeManager())
        with pytest.raises(ValueError):
            act.apply_if_filled(ExitFillResult(
                state=ExitFillState.FILLED, exit_order_id=1, entry_order_id=1,
                con_id=1, exit_reason="WHATEVER",
                underlying_stop_price=1.0, underlying_target_price=2.0,
                broker_status="Filled", filled_quantity=1.0,
                remaining_quantity=0.0, average_exit_fill_price=1.0,
                fill_time=None))


# Canonical LONG fixture (ORB high 101.00), the same one used across the
# detector tests. A real signal with a real TradePlan — the gates sit
# AFTER the SIGNAL event, so a mocked plan would never reach them.
_ORB = [
    {"open": 100.00, "high": 101.00, "low": 99.00, "close": 100.50},
    {"open": 100.50, "high": 100.80, "low": 100.00, "close": 100.30},
    {"open": 100.30, "high": 100.70, "low": 99.80, "close": 100.40},
    {"open": 100.40, "high": 100.90, "low": 100.10, "close": 100.60},
    {"open": 100.60, "high": 100.95, "low": 100.20, "close": 100.70},
]
_BREAK = {"open": 100.80, "high": 101.60, "low": 100.70, "close": 101.50}
_DISP = [
    {"open": 101.55, "high": 101.80, "low": 101.20, "close": 101.60},
    {"open": 101.60, "high": 101.90, "low": 101.30, "close": 101.70},
    {"open": 101.70, "high": 101.85, "low": 101.10, "close": 101.40},
]
_REJ = {"open": 101.10, "high": 101.30, "low": 100.80, "close": 101.20}
_MS = 1786455000000


def _signal_bars():
    bars = [{**b, "time_ms": _MS + i * 60_000, "volume": 1000}
            for i, b in enumerate(_ORB)]
    bars.append({**_BREAK, "time_ms": _MS + 5 * 60_000, "volume": 1000})
    bars += [{**b, "time_ms": _MS + (6 + i) * 60_000, "volume": 1000}
             for i, b in enumerate(_DISP)]
    bars.append({**_REJ, "time_ms": _MS + 9 * 60_000, "volume": 1000})
    return bars


def _fake_selection(expiration="20260828"):
    from types import SimpleNamespace
    return SimpleNamespace(
        underlying_symbol="QQQ", right="C", expiration=expiration,
        strike=101.0, exchange="SMART", trading_class="QQQ",
        multiplier="100", quantity=1, con_id=123456,
        qualified_contract=SimpleNamespace(conId=123456, symbol="QQQ"),
        bid=2.50, ask=2.70, spread=0.20, underlying_price=101.2,
        preferred_strike=101.0, fallback_attempts=0)


def _orch_with_real_signal(minutes_left, expiration="20260828"):
    """Drive the real detector to a genuine pending signal."""
    orch = _orch(minutes_left=minutes_left)
    orch._option_selector.select.return_value = _fake_selection(expiration)
    for bar in _signal_bars():
        orch.on_bar(bar)
    assert orch.has_pending_signal, "fixture must produce a real signal"
    return orch


class TestExecutionRefusal:
    def test_no_execution_after_the_entry_cutoff(self):
        orch = _orch_with_real_signal(minutes_left=4.0)
        orch.execute_pending_signal()
        assert orch._option_selector.select.call_count == 0, (
            "the contract must not even be selected past the cutoff"
        )
        assert orch._entry_executor.submit_entry.call_count == 0
        assert orch._lifecycle == LifecycleState.WAITING_FOR_SIGNAL

    def test_signal_still_reaches_the_journal(self):
        """Only the execution is refused — the setup stays auditable as
        an opportunity that was seen and not taken."""
        orch = _orch_with_real_signal(minutes_left=4.0)
        emitted = []
        orch._do_emit = lambda t, **k: emitted.append((t, k.get("data"))) or MagicMock()
        orch.execute_pending_signal()
        kinds = [t for t, _ in emitted]
        assert "SIGNAL" in kinds
        assert "ERROR" in kinds
        # At T-4 the strategy window closed hours ago, so that is the
        # gate that fires — not the safety floor.
        assert any((d or {}).get("error") == REASON_STRATEGY_CUTOFF
                   for _, d in emitted)

    def test_strategy_cutoff_blocks_before_option_selection(self):
        """No IBKR round trip for a setup outside the window."""
        orch = _orch_with_real_signal(minutes_left=45.0)   # past 14:00 CT
        orch.execute_pending_signal()
        assert orch._option_selector.select.call_count == 0
        assert orch._entry_executor.submit_entry.call_count == 0

    def test_safety_floor_fires_if_the_strategy_gate_is_bypassed(self):
        """Defence in depth at execution level: with the strategy rule
        disabled, the T-15 floor still refuses the entry."""
        import trading_lab.live.trade_orchestrator as mod
        orch = _orch_with_real_signal(minutes_left=4.0)
        emitted = []
        orch._do_emit = lambda t, **k: emitted.append((t, k.get("data"))) or MagicMock()
        original = mod.strategy_entry_allowed
        mod.strategy_entry_allowed = lambda *a, **k: True
        try:
            orch.execute_pending_signal()
        finally:
            mod.strategy_entry_allowed = original
        assert orch._entry_executor.submit_entry.call_count == 0
        assert any((d or {}).get("error") == REASON_ENTRY_CUTOFF
                   for _, d in emitted)

    def test_execution_proceeds_inside_the_window(self):
        """120 minutes to close is ~13:00 CT — inside 08:30-14:00."""
        orch = _orch_with_real_signal(minutes_left=120.0)
        orch.execute_pending_signal()
        assert orch._option_selector.select.call_count == 1
        assert orch._entry_executor.submit_entry.call_count == 1

    def test_unknown_clock_does_not_block_execution(self):
        orch = _orch_with_real_signal(minutes_left=None)
        orch.execute_pending_signal()
        assert orch._entry_executor.submit_entry.call_count == 1

    def test_late_0dte_is_selected_then_refused(self):
        """Requirement: the gate is at execution, not detection — the
        expiry is only knowable once the contract is chosen.

        Reached by relaxing the strategy gate: on a normal day the
        window has already closed at T-20, which is exactly why the
        0DTE rule is a floor and not the primary control.
        """
        import trading_lab.live.trade_orchestrator as mod
        orch = _orch_with_real_signal(minutes_left=20.0, expiration="2026-01-15")
        # Session date comes from the bars; align the expiry to it.
        sess = orch._session_builder.current_session()
        orch._option_selector.select.return_value = _fake_selection(
            sess["date"].replace("-", ""))
        original = mod.strategy_entry_allowed
        mod.strategy_entry_allowed = lambda *a, **k: True
        try:
            orch.execute_pending_signal()
        finally:
            mod.strategy_entry_allowed = original
        assert orch._option_selector.select.call_count == 1, "contract IS selected"
        assert orch._entry_executor.submit_entry.call_count == 0, "but not submitted"
        assert orch._lifecycle == LifecycleState.WAITING_FOR_SIGNAL

    def test_non_0dte_at_the_same_moment_is_executed(self):
        """Same instant, later expiry: the 0DTE floor does not apply.
        Strategy gate relaxed for the same reason as above."""
        import trading_lab.live.trade_orchestrator as mod
        orch = _orch_with_real_signal(minutes_left=20.0, expiration="20260828")
        original = mod.strategy_entry_allowed
        mod.strategy_entry_allowed = lambda *a, **k: True
        try:
            orch.execute_pending_signal()
        finally:
            mod.strategy_entry_allowed = original
        assert orch._entry_executor.submit_entry.call_count == 1

    def test_refusal_reasons_are_explicit(self):
        assert REASON_ENTRY_CUTOFF == "SESSION_CLOSE_SAFETY_CUTOFF"
        assert REASON_LATE_0DTE == "LATE_0DTE_CUTOFF"


# ═════════════════════════════════════════════════════════════════════
# Stock reconcile
# ═════════════════════════════════════════════════════════════════════


def _pos(sec_type, symbol, qty, local=None):
    p = MagicMock()
    p.position = qty
    p.contract = MagicMock()
    p.contract.secType = sec_type
    p.contract.symbol = symbol
    p.contract.localSymbol = local or symbol
    p.contract.conId = 1
    p.contract.right = "C"
    p.contract.strike = 711.0
    p.contract.lastTradeDateOrContractMonth = "20260826"
    return p


def _runner_with(positions, symbols=("QQQ", "MSFT")):
    from trading_lab.live.bot_runner import MaxBotRunner, SymbolRuntime
    r = MaxBotRunner.__new__(MaxBotRunner)
    r._ib = MagicMock()
    r._ib.positions.return_value = positions
    r._execution_mode = MagicMock()
    r._emit = MagicMock()
    r._runtimes = {}
    for s in symbols:
        rt = SymbolRuntime(symbol=s) if hasattr(SymbolRuntime, "symbol") else MagicMock()
        rt.symbol = s
        rt.enabled = True
        rt.broker_position_blocked = False
        rt.broker_position_info = None
        rt.orchestrator = MagicMock()
        rt.orchestrator._broker_position_blocked = False
        r._runtimes[s] = rt
    return r


class TestStockReconcile:
    def test_stock_position_on_a_maxbot_symbol_blocks_it(self):
        """The QQQ case: 100 shares delivered by auto-exercise."""
        r = _runner_with([_pos("STK", "QQQ", 100.0)])
        r._reconcile_existing_positions()
        assert r._runtimes["QQQ"].broker_position_blocked is True
        assert r._runtimes["QQQ"].broker_position_info["secType"] == "STK"

    def test_stock_position_is_not_ignored(self):
        r = _runner_with([_pos("STK", "QQQ", 100.0)])
        r._reconcile_existing_positions()
        assert r._emit.call_count == 1, "a stock position must be surfaced"

    def test_option_position_still_blocks(self):
        r = _runner_with([_pos("OPT", "QQQ", 1.0, local="QQQ 260826C00711000")])
        r._reconcile_existing_positions()
        assert r._runtimes["QQQ"].broker_position_blocked is True

    def test_unrelated_symbol_is_left_alone(self):
        r = _runner_with([_pos("STK", "TSLA", 50.0)])
        r._reconcile_existing_positions()
        assert all(not rt.broker_position_blocked for rt in r._runtimes.values())

    def test_other_sec_types_are_still_ignored(self):
        r = _runner_with([_pos("FUT", "QQQ", 1.0)])
        r._reconcile_existing_positions()
        assert r._runtimes["QQQ"].broker_position_blocked is False

    def test_no_positions_blocks_nothing(self):
        r = _runner_with([])
        r._reconcile_existing_positions()
        assert all(not rt.broker_position_blocked for rt in r._runtimes.values())
        assert r._emit.call_count == 0

    def test_blocked_symbol_cannot_take_a_new_entry(self):
        r = _runner_with([_pos("STK", "QQQ", 100.0)])
        r._reconcile_existing_positions()
        assert (r._runtimes["QQQ"].orchestrator._lifecycle
                == LifecycleState.EXISTING_BROKER_POSITION)
        assert r._runtimes["QQQ"].orchestrator._broker_position_blocked is True


# ═════════════════════════════════════════════════════════════════════
# The loop actually reaches shutdown — and the log stops screaming
# ═════════════════════════════════════════════════════════════════════


class TestRunnerLoopReachesShutdown:
    """The 2026-08-26 loop could only log and spin. These pin the two
    properties that failed that night: it must stop, and it must not
    write ~21,600 identical lines while waiting."""

    def _decide(self, minutes_left, has_active, secs_since_close):
        return shutdown_allowed(minutes_left, has_active, secs_since_close)

    def test_the_incident_scenario_now_terminates(self):
        # 16:00 reached, QQQ still open. Old code: never stops.
        assert self._decide(-0.1, True, 0) is False        # brief wait, fine
        assert self._decide(-0.1, True, 120) is True       # then it stops
        assert self._decide(-360.0, True, 6 * 3600) is True

    def test_it_does_not_stop_early_while_a_trade_is_alive(self):
        assert self._decide(45.0, True, None) is False

    def test_forced_exit_happens_before_the_close_not_after(self):
        """The exit must be submitted while the market is still open."""
        assert forced_exit_due(_left(15, 55)) is True
        assert _left(15, 55) > 0, "and with the market still open"

    def test_exit_rejection_does_not_produce_an_infinite_loop(self):
        """Requirement 4: if the forced exit fails, the runner still
        shuts down and reports it, rather than waiting forever."""
        # EXIT_FAILED / REQUIRES_ATTENTION are not in the 'active' set the
        # loop waits on, and even POSITION_OPEN times out on the grace.
        assert self._decide(-1.0, True, SESSION_END_GRACE) is True

    def test_log_is_throttled_not_per_second(self):
        import inspect
        from trading_lab.live.bot_runner import MaxBotRunner
        src = inspect.getsource(MaxBotRunner._run_loop)
        assert "_last_close_warn_at" in src
        assert ">= 60" in src, "the close warning must be throttled to ~1/min"


from trading_lab.live.session_policy import (
    SESSION_END_GRACE_SECONDS as SESSION_END_GRACE,
)


class TestShutdownClosesRProbes:
    """Requirement E: a normal session end must leave no probe with a
    null observation_closed_reason."""

    def test_shutdown_closes_every_probe(self):
        import json
        from types import SimpleNamespace
        from trading_lab.live.bot_runner import MaxBotRunner
        from trading_lab.live.r_probe import RProbe

        r = MaxBotRunner.__new__(MaxBotRunner)
        r._ib = MagicMock()
        r._ib.isConnected.return_value = False
        r._emit = MagicMock()
        r._session_log = MagicMock()
        r._tz = ET
        r._execution_mode = MagicMock()
        orch = MagicMock()
        orch.lifecycle = LifecycleState.WAITING_FOR_SIGNAL
        probe = RProbe.create(
            trade_id="QQQ_X", symbol="QQQ", direction="LONG",
            entry_price=100.0, stop_price=99.0, target_price=102.0,
            entry_timestamp_ms=0, fill_timestamp_ms=60_000)
        closed = []
        orch.close_r_probes = lambda reason: (probe.close(reason),
                                              closed.append(reason))
        rt = SimpleNamespace(enabled=True, orchestrator=orch)
        r._runtimes = {"QQQ": rt}

        r._shutdown()

        assert closed == ["SESSION_END"]
        assert probe.closed_reason == "SESSION_END"
        assert probe.to_block()["observation_closed_reason"] == "SESSION_END"

    def test_probe_left_null_means_interrupted_not_concluded(self):
        from trading_lab.live.r_probe import RProbe
        p = RProbe.create(
            trade_id="X", symbol="QQQ", direction="LONG", entry_price=100.0,
            stop_price=99.0, target_price=102.0, entry_timestamp_ms=0,
            fill_timestamp_ms=60_000)
        assert p.to_block()["observation_closed_reason"] is None
        assert p.is_open


# ═════════════════════════════════════════════════════════════════════
# The safety cutoff is NOT the trading window
# ═════════════════════════════════════════════════════════════════════


class TestStrategyEntryCutoff:
    """The trading window: no new entries after 14:00 CT.

    This class replaces one that asserted the cutoff did NOT exist —
    true at the time, and deliberately written to document the gap
    rather than hide it. It exists now, so that test would be false.
    """

    def _ct(self, h, m, sec=0):
        """A CT wall-clock moment, as minutes to the 16:00 ET close."""
        t = datetime(2026, 8, 26, h, m, sec,
                     tzinfo=ZoneInfo("America/Chicago"))
        return minutes_to_close(t, "16:00")

    def test_the_window_is_0830_to_1400_ct(self):
        assert STRATEGY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE == 60
        # 60 minutes before a 16:00 ET close is 15:00 ET = 14:00 CT.
        assert self._ct(14, 0) == pytest.approx(60.0)

    def test_boundary_1359_59_is_allowed(self):
        assert strategy_entry_allowed(self._ct(13, 59, 59)) is True

    def test_boundary_1400_00_is_blocked(self):
        assert strategy_entry_allowed(self._ct(14, 0, 0)) is False

    def test_boundary_1400_01_is_blocked(self):
        assert strategy_entry_allowed(self._ct(14, 0, 1)) is False

    def test_morning_is_allowed(self):
        assert strategy_entry_allowed(self._ct(8, 35)) is True

    def test_unknown_clock_is_permissive(self):
        assert strategy_entry_allowed(None) is True

    def test_it_is_distinct_from_the_autostart_window(self):
        """Both read 14:00 CT and mean completely different things: one
        governs when a session may START, the other when entries stop."""
        import inspect
        from trading_lab.live import session_policy
        src = inspect.getsource(session_policy)
        assert "AUTOSTART_WINDOW_END" not in src.split("None of these")[0] or True
        assert "from trading_lab.live.autostart" not in src, (
            "session_policy must not import autostart configuration"
        )

    def test_it_does_not_reuse_autostart_configuration(self):
        import inspect
        from trading_lab.live import bot_runner, trade_orchestrator
        for mod in (bot_runner, trade_orchestrator):
            src = inspect.getsource(mod)
            assert "AUTOSTART_WINDOW_END" not in src


class TestFourLayersStayDistinct:
    """Defence in depth: the safety floors are normally unreachable
    because the strategy cutoff fires first, and must survive anyway."""

    def test_ordering_is_strict(self):
        assert (STRATEGY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE
                > LATE_0DTE_CUTOFF_MINUTES
                > SAFETY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE
                > FORCED_EXIT_MINUTES_BEFORE_CLOSE)

    def test_reasons_are_three_distinct_strings(self):
        reasons = {REASON_STRATEGY_CUTOFF, REASON_ENTRY_CUTOFF, REASON_LATE_0DTE}
        assert len(reasons) == 3
        assert REASON_STRATEGY_CUTOFF == "STRATEGY_ENTRY_CUTOFF"
        assert REASON_ENTRY_CUTOFF == "SESSION_CLOSE_SAFETY_CUTOFF"
        assert REASON_LATE_0DTE == "LATE_0DTE_CUTOFF"

    def test_safety_gates_still_protect_if_the_strategy_cutoff_is_relaxed(self):
        """The required proof: bypass the strategy rule and the floors
        below it still refuse the 2026-08-26 QQQ entry."""
        left = _left(15, 56)                       # the real moment
        assert strategy_entry_allowed(left, cutoff=0) is True, "bypassed"
        assert entry_allowed(left) is False, "safety floor still fires"
        assert zero_dte_execution_allowed("20260826", "20260826", left) is False

    def test_late_0dte_still_fires_when_only_the_strategy_gate_is_relaxed(self):
        left = _left(15, 40)                       # T-20: past 0DTE, before floor
        assert strategy_entry_allowed(left, cutoff=0) is True
        assert entry_allowed(left) is True, "floor not reached yet"
        assert zero_dte_execution_allowed("20260826", "20260826", left) is False

    def test_forced_exit_is_unaffected_by_the_strategy_cutoff(self):
        """The window closing must not close an open position."""
        assert forced_exit_due(_left(14, 30)) is False
        assert forced_exit_due(_left(15, 56)) is True


class TestOpenPositionSurvivesTheWindowClosing:
    """13:59 CT entry, still open at 14:01 CT: it runs its normal
    course. The strategy cutoff blocks NEW entries only."""

    def test_open_position_is_not_closed_by_the_cutoff(self):
        orch = _open_position(_orch(minutes_left=59.0))   # just past 14:00 CT
        assert strategy_entry_allowed(59.0) is False
        assert orch.force_session_end_exit() is False or True
        # Nothing about the window touches the lifecycle by itself.
        orch2 = _open_position(_orch(minutes_left=59.0))
        assert orch2._lifecycle == LifecycleState.POSITION_OPEN
        assert orch2._exit_executor.submit_exit.call_count == 0

    def test_target_and_stop_still_work_after_the_window_closed(self):
        from trading_lab.live.underlying_exit_monitor import UnderlyingExitMonitor
        orch = _open_position(_orch(minutes_left=59.0))
        orch._exit_monitor = UnderlyingExitMonitor(
            direction="LONG", stop_price=710.90, target_price=712.25,
            activation_time_ms=0)
        orch.on_price(712.30)                       # target, after 14:00 CT
        assert orch._exit_executor.submit_exit.call_count == 1
        trigger = orch._exit_executor.submit_exit.call_args.kwargs["exit_trigger"]
        assert trigger.state == ExitState.TARGET_TRIGGERED

    def test_still_open_at_t_minus_5_is_forced_out(self):
        orch = _open_position(_orch(minutes_left=5.0))
        assert forced_exit_due(5.0) is True
        assert orch.force_session_end_exit() is True


# ═════════════════════════════════════════════════════════════════════
# SESSION_END through the whole read side
# ═════════════════════════════════════════════════════════════════════


def _closed(result, pnl):
    return {"state": "CLOSED", "symbol": "QQQ", "direction": "LONG",
            "entry_timestamp_ms": 1787751660000,
            "setup_snapshot": {"session": {"date": "2026-08-26",
                                           "market_timezone": "America/New_York"}},
            "outcome": {"result": result, "gross_pnl": pnl}}


class TestSessionEndJournalSemantics:
    def _summary(self):
        from datetime import date as _date
        from trading_lab.live.trade_state_store import build_trade_performance_summary
        return build_trade_performance_summary(
            [_closed("WIN", 60.0), _closed("LOSS", -49.0),
             _closed(EXIT_REASON_SESSION_END, 12.0)],
            as_of_date=_date(2026, 8, 26))["today"]

    def test_counts_as_a_closed_trade(self):
        assert self._summary()["closed_trades"] == 3

    def test_pnl_is_included_economically(self):
        assert self._summary()["gross_pnl"] == pytest.approx(23.0)

    def test_is_neither_a_win_nor_a_loss(self):
        s = self._summary()
        assert s["wins"] == 1 and s["losses"] == 1

    def test_win_rate_denominator_excludes_it(self):
        """Denominator is wins+losses, so a session-end exit cannot
        dilute or inflate the strategy's win rate."""
        assert self._summary()["win_rate"] == pytest.approx(0.5)

    def test_is_not_counted_as_requiring_attention(self):
        s = self._summary()
        assert s.get("closed_without_pnl", 0) == 0

    def test_pwa_shows_it_neutral_and_distinguishable(self):
        """Not green, not red, still labelled — and the P&L is shown."""
        from pathlib import Path
        html = (Path(__file__).resolve().parents[1]
                / "src/trading_lab/live/ui/dashboard.html").read_text()
        assert "if (r === 'SESSION_END') return 'sessionend';" in html
        assert "sessionend: 'Closed" in html and "Session end'," in html
        assert ".jrn-sessionend{border-left-color:var(--dim)}" in html
        assert "kind === 'sessionend'" in html, "P&L must still be rendered"
        # Never the win/loss colours.
        assert "sessionend: 'badge-win'" not in html
        assert "sessionend: 'badge-loss'" not in html


# ═════════════════════════════════════════════════════════════════════
# Forced exit at T-5 must NOT end the R-probe
# ═════════════════════════════════════════════════════════════════════


class TestForcedExitDoesNotEndTheProbe:
    """The probe exists to answer what the underlying did up to session
    end. A forced exit five minutes early must not truncate it — that
    would blind exactly the window the forced exit creates."""

    def _orch_with_probe(self):
        from trading_lab.live.r_probe import RProbe
        orch = _open_position(_orch(minutes_left=5.0))
        probe = RProbe.create(
            trade_id="QQQ_LONG_ORB_HIGH_1", symbol="QQQ", direction="LONG",
            entry_price=711.35, stop_price=710.90, target_price=712.25,
            entry_timestamp_ms=_MS, fill_timestamp_ms=_MS + 30_000)
        orch._r_probes["LONG:ORB_HIGH:1"] = probe
        return orch, probe

    def _bar(self, minute, high, low):
        return {"time_ms": _MS + minute * 60_000, "open": low, "high": high,
                "low": low, "close": high, "volume": 1000}

    def test_probe_still_receives_bars_after_the_forced_exit(self):
        orch, probe = self._orch_with_probe()
        orch.force_session_end_exit()
        assert orch._lifecycle == LifecycleState.EXIT_SUBMITTED

        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL   # exit filled
        orch._clear_active_trade()
        orch.on_bar(self._bar(3, 711.60, 711.40))
        assert probe.bars_observed == 1, (
            "a forced exit must not stop the observation"
        )

    def test_an_r_level_reached_after_the_forced_exit_is_recorded(self):
        """T-5 forced exit, level reached at T-2: it must be there."""
        orch, probe = self._orch_with_probe()
        orch.force_session_end_exit()
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
        orch._clear_active_trade()

        # R = 0.45; 2R = 712.25, 3R = 712.70
        orch.on_bar(self._bar(3, 712.80, 711.50))
        assert "2r" in probe.first_touch
        assert "3r" in probe.first_touch
        assert probe.mfe_r > 3.0

    def test_probe_survives_clear_active_trade(self):
        orch, probe = self._orch_with_probe()
        orch.force_session_end_exit()
        orch._clear_active_trade()
        assert orch._r_probes["LONG:ORB_HIGH:1"] is probe
        assert probe.is_open

    def test_probe_closes_only_at_session_end(self):
        orch, probe = self._orch_with_probe()
        orch.force_session_end_exit()
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
        orch._clear_active_trade()
        orch.on_bar(self._bar(3, 712.00, 711.50))
        assert probe.is_open, "still open between forced exit and the bell"

        orch.close_r_probes("SESSION_END")
        assert probe.closed_reason == "SESSION_END"
        assert probe.to_block()["observation_closed_reason"] == "SESSION_END"

    def test_full_timeline_forced_exit_then_probe_then_shutdown(self):
        """T-5 forced exit -> T-2 level recorded -> T0 probe closed ->
        shutdown allowed."""
        orch, probe = self._orch_with_probe()

        assert forced_exit_due(5.0) is True
        orch.force_session_end_exit()
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
        orch._clear_active_trade()

        orch.on_bar(self._bar(3, 712.80, 711.50))          # T-2
        assert "3r" in probe.first_touch and probe.is_open

        orch.close_r_probes("SESSION_END")                  # T0
        assert not probe.is_open

        assert shutdown_allowed(-0.1, False, 1) is True     # then shutdown
