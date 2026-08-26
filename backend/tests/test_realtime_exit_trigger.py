"""Realtime TP/SL exit trigger (2026-08-26 execution audit).

Problem this fixes
------------------
Once a position was open, STOP and TARGET could only be detected when a
1-minute bar closed, and the bar the entry filled inside was discarded
whole. On the real MU LONG of 2026-08-26 that cost 100.9 seconds:

    12:28:09.960  entry filled at 7.95
    12:28:25      underlying touches 938.84 >= target 938.80
    12:29:05.880  bar 12:28 evaluated -> DISCARDED
                  (bar open 12:28:00 < fill 12:28:09.960)
    12:30:05.891  bar 12:29 evaluated -> TARGET_TRIGGERED, 100.9s late
    12:30:10.474  filled at 7.60 — a strategy WIN worth -$35

Two independent latencies, both removed here:

  1. candle-close detection      (0-60s)
  2. discarding the fill's bar   (0-60s)

Scope: only the exit, and only after POSITION_OPEN. Setup detection
stays candle-close based and is not touched by any test here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.trade_orchestrator import (
    LifecycleState,
    MaxBotTradeOrchestrator,
)
from trading_lab.live.underlying_exit_monitor import (
    ExitState,
    UnderlyingExitMonitor,
)


MS_0930 = 1786455000000
BAR_MS = 60_000


def _ms(minute: int) -> int:
    return MS_0930 + minute * BAR_MS


def _bar(minute: int, o, h, l, c) -> dict:
    return {"time_ms": _ms(minute), "open": o, "high": h,
            "low": l, "close": c, "volume": 1000}


def _monitor(direction="LONG", *, stop, target, fill_ms):
    return UnderlyingExitMonitor(
        direction=direction, stop_price=stop,
        target_price=target, activation_time_ms=fill_ms,
    )


# ═════════════════════════════════════════════════════════════════════
# T1-T4 — a live price crosses the level: immediate trigger
# ═════════════════════════════════════════════════════════════════════


class TestLivePriceTriggers:
    """No bar is ever evaluated in these tests. If the trigger needed a
    candle close, every one of them would return HOLD."""

    def test_t1_long_target(self):
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=_ms(5))
        assert m.evaluate_price(100.5).state == ExitState.HOLD
        r = m.evaluate_price(101.0)
        assert r.state == ExitState.TARGET_TRIGGERED
        assert r.trigger_source == "PRICE"
        assert r.trigger_price == 101.0

    def test_t2_long_stop(self):
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=_ms(5))
        assert m.evaluate_price(99.5).state == ExitState.HOLD
        r = m.evaluate_price(98.9)
        assert r.state == ExitState.STOP_TRIGGERED
        assert r.trigger_source == "PRICE"

    def test_t3_short_target(self):
        m = _monitor("SHORT", stop=101.0, target=99.0, fill_ms=_ms(5))
        assert m.evaluate_price(99.5).state == ExitState.HOLD
        assert m.evaluate_price(99.0).state == ExitState.TARGET_TRIGGERED

    def test_t4_short_stop(self):
        m = _monitor("SHORT", stop=101.0, target=99.0, fill_ms=_ms(5))
        assert m.evaluate_price(100.5).state == ExitState.HOLD
        assert m.evaluate_price(101.2).state == ExitState.STOP_TRIGGERED

    def test_no_bar_was_needed(self):
        """The decisive property: a crossing fires without any bar."""
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=_ms(5))
        r = m.evaluate_price(101.4)
        assert r.state == ExitState.TARGET_TRIGGERED
        assert r.trigger_bar_time_ms is None, (
            "the trigger must not depend on a bar having closed"
        )


# ═════════════════════════════════════════════════════════════════════
# T5 — a crossing BEFORE the fill must never close the position
# ═════════════════════════════════════════════════════════════════════


class TestPreFillProtection:
    def test_t5_bar_entirely_before_the_fill_is_ignored(self):
        # Fill at minute 5; bar 3 reached the target long before.
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=_ms(5))
        assert m.evaluate_bar(_bar(3, 100.0, 101.5, 99.8, 100.2)).state \
            == ExitState.HOLD

    def test_t5_pre_fill_extreme_inside_the_fill_bar_is_ignored(self):
        """The fill bar's high/low may predate the fill, so they are
        never used — only its close, which is after the fill."""
        fill_ms = _ms(5) + 30_000          # filled halfway through bar 5
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=fill_ms)
        # High 101.5 was reached in the first half — before the fill.
        # Close is back at 100.2, so nothing may trigger.
        assert m.evaluate_bar(_bar(5, 100.0, 101.5, 99.8, 100.2)).state \
            == ExitState.HOLD

    def test_t5_live_price_cannot_predate_the_fill_by_construction(self):
        """A monitor only exists once the fill is confirmed, so every
        price it can see is post-fill. Asserted through the orchestrator
        in TestOrchestratorLivePath."""
        orch, _sb = _make_orchestrator()
        assert orch._exit_monitor is None
        orch.on_price(999.0)               # must be a no-op, not a crash
        assert orch._lifecycle != LifecycleState.EXIT_SUBMITTED


# ═════════════════════════════════════════════════════════════════════
# T6 — filled mid-candle: a later crossing in that SAME candle triggers
# ═════════════════════════════════════════════════════════════════════


class TestSameCandleAfterFill:
    def test_t6_live_price_after_a_mid_candle_fill(self):
        """The exact MU shape: fill 10s into a bar, target reached 15s
        later. Pre-fix this waited for the NEXT bar to close."""
        fill_ms = _ms(5) + 10_000
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=fill_ms)
        r = m.evaluate_price(101.1)        # same minute, 15s after fill
        assert r.state == ExitState.TARGET_TRIGGERED

    def test_t6_fill_bar_close_is_still_honoured_as_backstop(self):
        """Even with no live tick at all, the fill bar's close now
        counts — pre-fix the whole bar was discarded."""
        fill_ms = _ms(5) + 10_000
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=fill_ms)
        r = m.evaluate_bar(_bar(5, 100.0, 101.5, 99.8, 101.2))
        assert r.state == ExitState.TARGET_TRIGGERED

    def test_t6_regression_the_mu_bar_is_no_longer_discarded(self):
        """Guard against the exact pre-fix rule returning:
        `bar["time_ms"] < activation_time_ms -> hold`."""
        fill_ms = _ms(5) + 10_000
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=fill_ms)
        assert _ms(5) < fill_ms, "fixture must reproduce the MU shape"
        assert m.evaluate_bar(_bar(5, 100.0, 101.5, 99.8, 101.2)).state \
            != ExitState.HOLD


# ═════════════════════════════════════════════════════════════════════
# T7/T8 — idempotence: one trigger, one exit, whatever arrives later
# ═════════════════════════════════════════════════════════════════════


class TestIdempotence:
    def test_t7_many_prices_after_target_stay_one_trigger(self):
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=_ms(5))
        first = m.evaluate_price(101.2)
        for p in (101.5, 102.0, 100.0, 98.0, 101.9):
            again = m.evaluate_price(p)
            assert again is first, "monitor must stay terminal"
        assert first.trigger_price == 101.2

    def test_t8_confirming_bar_after_a_live_trigger_changes_nothing(self):
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=_ms(5))
        live = m.evaluate_price(101.2)
        confirming = m.evaluate_bar(_bar(6, 100.9, 101.8, 100.5, 101.6))
        assert confirming is live
        assert confirming.trigger_source == "PRICE", (
            "the bar backstop must not overwrite the live trigger"
        )

    def test_stop_after_a_live_target_cannot_flip_the_result(self):
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=_ms(5))
        target = m.evaluate_price(101.2)
        assert m.evaluate_bar(_bar(7, 100.0, 100.2, 98.0, 98.5)) is target

    def test_bad_prices_never_trigger(self):
        m = _monitor("LONG", stop=99.0, target=101.0, fill_ms=_ms(5))
        for bad in (None, float("nan"), 0.0, -1.0, "abc"):
            assert m.evaluate_price(bad).state == ExitState.HOLD


# ═════════════════════════════════════════════════════════════════════
# Orchestrator wiring — one exit order, from the live path
# ═════════════════════════════════════════════════════════════════════


def _make_orchestrator(direction="LONG"):
    sb = LiveSessionBuilder("SPY", "America/New_York")
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="SPY", direction=direction, tick_size=0.01,
        session_builder=sb,
        signal_detector=LiveSignalDetector(
            symbol="SPY", direction=direction, tick_size=0.01),
        trade_manager=DailyTradeManager(),
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(),
    )
    return orch, sb


def _open_position(orch, *, stop, target, fill_ms, direction="LONG"):
    """Put the orchestrator in POSITION_OPEN with an armed monitor,
    without touching the entry path."""
    orch._lifecycle = LifecycleState.POSITION_OPEN
    orch._resolved_direction = direction
    orch._entry_order_id = 1
    orch._qualified_contract = MagicMock()
    orch._entry_con_id = 42
    orch._option_right = "C"
    orch._option_expiration = "20260826"
    orch._option_strike = 100.0
    orch._exit_monitor = UnderlyingExitMonitor(
        direction=direction, stop_price=stop,
        target_price=target, activation_time_ms=fill_ms,
    )
    sub = MagicMock()
    sub.order_id = 77
    sub.exit_reason = "TARGET"
    sub.status = "PendingSubmit"
    orch._exit_executor.submit_exit.return_value = sub
    return orch


class TestOrchestratorLivePath:
    def test_live_price_submits_the_exit(self):
        orch, _ = _make_orchestrator()
        _open_position(orch, stop=99.0, target=101.0, fill_ms=_ms(5))

        orch.on_price(100.4)
        assert orch._exit_executor.submit_exit.call_count == 0

        orch.on_price(101.1)
        assert orch._exit_executor.submit_exit.call_count == 1
        assert orch._lifecycle == LifecycleState.EXIT_SUBMITTED

    def test_only_one_exit_for_many_updates(self):
        orch, _ = _make_orchestrator()
        _open_position(orch, stop=99.0, target=101.0, fill_ms=_ms(5))
        for p in (101.1, 101.4, 102.0, 98.0, 101.2):
            orch.on_price(p)
        assert orch._exit_executor.submit_exit.call_count == 1

    def test_a_later_bar_does_not_add_a_second_exit(self):
        orch, sb = _make_orchestrator()
        _open_position(orch, stop=99.0, target=101.0, fill_ms=_ms(5))
        orch.on_price(101.1)
        orch.on_bar(_bar(6, 101.0, 101.9, 100.8, 101.5))
        assert orch._exit_executor.submit_exit.call_count == 1

    def test_no_position_means_no_exit(self):
        orch, _ = _make_orchestrator()
        orch.on_price(101.1)
        assert orch._exit_executor.submit_exit.call_count == 0

    def test_bar_backstop_still_works_without_any_live_price(self):
        orch, _ = _make_orchestrator()
        _open_position(orch, stop=99.0, target=101.0, fill_ms=_ms(5))
        orch.on_bar(_bar(6, 100.5, 101.4, 100.2, 101.0))
        assert orch._exit_executor.submit_exit.call_count == 1


# ═════════════════════════════════════════════════════════════════════
# T9 — recovery / restart: no regression
# ═════════════════════════════════════════════════════════════════════


class TestRecoverySafety:
    def test_on_price_is_inert_in_every_non_open_lifecycle(self):
        for state in (LifecycleState.WAITING_FOR_SIGNAL,
                      LifecycleState.ENTRY_SUBMITTED,
                      LifecycleState.EXIT_SUBMITTED,
                      LifecycleState.EXIT_FAILED,
                      LifecycleState.REQUIRES_ATTENTION,
                      LifecycleState.DONE_FOR_DAY):
            orch, _ = _make_orchestrator()
            _open_position(orch, stop=99.0, target=101.0, fill_ms=_ms(5))
            orch._lifecycle = state
            orch.on_price(101.5)
            assert orch._exit_executor.submit_exit.call_count == 0, state

    def test_monitor_absent_is_a_no_op(self):
        orch, _ = _make_orchestrator()
        orch._lifecycle = LifecycleState.POSITION_OPEN
        orch._exit_monitor = None
        orch.on_price(101.5)   # must not raise

    def test_exit_retry_state_is_untouched(self):
        """The executor still owns submit/fill/retry — the live path
        only decides WHEN to hand the trigger over."""
        orch, _ = _make_orchestrator()
        _open_position(orch, stop=99.0, target=101.0, fill_ms=_ms(5))
        orch.on_price(101.1)
        assert orch._exit_retry_count == 0
        assert orch._exit_max_retries == 3


# ═════════════════════════════════════════════════════════════════════
# Timing acceptance — the candle-close dependency is gone
# ═════════════════════════════════════════════════════════════════════


class TestTimingAcceptance:
    def test_mu_scenario_end_to_end(self):
        """Replay of the real MU shape, in bar-relative terms.

        Fill 10s into bar 5; target crossed 15s later, still inside
        bar 5. Pre-fix: bar 5 discarded, bar 6 evaluated at its close —
        the trigger could not arrive before minute 7. Post-fix: it
        arrives on the crossing price itself, with no bar at all.
        """
        orch, _ = _make_orchestrator()
        fill_ms = _ms(5) + 10_000
        _open_position(orch, stop=937.75, target=938.80, fill_ms=fill_ms)

        orch.on_price(938.84)     # the 12:28:25 tick

        assert orch._exit_executor.submit_exit.call_count == 1
        trigger = orch._exit_executor.submit_exit.call_args.kwargs["exit_trigger"]
        assert trigger.state == ExitState.TARGET_TRIGGERED
        assert trigger.trigger_source == "PRICE"
        assert trigger.trigger_bar_time_ms is None, (
            "a trigger that carries a bar timestamp waited for a bar"
        )

    def test_setup_detection_is_still_candle_close_based(self):
        """The other half of the requirement: entry logic untouched."""
        import inspect
        src = inspect.getsource(MaxBotTradeOrchestrator.on_bar)
        assert "_check_for_signal" in src
        assert "on_price" not in src, (
            "signal detection must never be driven by a price tick"
        )
