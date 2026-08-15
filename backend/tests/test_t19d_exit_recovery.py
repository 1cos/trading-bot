"""Tests for T19D — EXIT_FAILED recovery.

Covers:
1. Normal exit → DONE_FOR_DAY (unchanged)
2. First SELL fails → retry after cooldown → fill → DONE
3. Multiple retries without duplicate SELL orders
4. Retries exhausted → REQUIRES_ATTENTION
5. No false TRADE_COMPLETED without confirmed fill
6. Error isolation per symbol
7. PAPER_EXECUTE normal path unchanged
8. allow_resubmit on OptionExitExecutor
"""

import time
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

import pytest

from trading_lab.live.trade_orchestrator import (
    MaxBotTradeOrchestrator,
    LifecycleState,
)
from trading_lab.live.exit_fill_monitor import ExitFillState, ExitFillResult
from trading_lab.live.option_exit_executor import OptionExitExecutor


def _make_orchestrator(*, exit_executor=None):
    """Create orchestrator with mocked deps ready for exit testing."""
    sb = MagicMock()
    sb.current_session.return_value = {
        "date": "2026-01-15",
        "candles": [{"time_ms": 1000, "open": 100, "high": 101,
                     "low": 99, "close": 100.5, "volume": 1000}],
    }
    sb.current_date = "2026-01-15"

    sd = MagicMock()
    sd.last_result = None

    tm = MagicMock()
    tm.can_trade = True
    tm.state = MagicMock(
        trading_date="2026-01-15", trades_used=1,
        wins=0, losses=0, day_finished=False,
    )

    os_ = MagicMock()
    ee = MagicMock()
    xe = exit_executor or MagicMock()

    orch = MaxBotTradeOrchestrator(
        underlying_symbol="NVDA", direction="SHORT",
        tick_size=0.01, session_builder=sb,
        signal_detector=sd, trade_manager=tm,
        option_selector=os_, entry_executor=ee,
        exit_executor=xe,
    )
    return orch, xe


def _setup_position_open(orch):
    """Put orchestrator into POSITION_OPEN state with exit context."""
    orch._lifecycle = LifecycleState.POSITION_OPEN
    orch._entry_con_id = 12345
    orch._entry_order_id = 42
    orch._qualified_contract = MagicMock()
    orch._option_right = "P"
    orch._option_expiration = "20260115"
    orch._option_strike = 225.0
    orch._resolved_direction = "SHORT"
    orch._exit_monitor = MagicMock()


def _make_exit_fill(state, broker_status="Filled", exit_reason="STOP"):
    return ExitFillResult(
        state=state,
        exit_reason=exit_reason,
        entry_order_id=42,
        exit_order_id=99,
        con_id=12345,
        filled_quantity=1.0 if state == ExitFillState.FILLED else 0.0,
        remaining_quantity=0.0 if state == ExitFillState.FILLED else 1.0,
        average_exit_fill_price=2.50 if state == ExitFillState.FILLED else None,
        broker_status=broker_status,
        fill_time=None,
        underlying_stop_price=226.50,
        underlying_target_price=222.00,
    )


# ═══════════════════════════════════════════════════════════════════════
# 1. Normal exit → DONE
# ═══════════════════════════════════════════════════════════════════════


class TestNormalExit:
    def test_filled_exit_goes_to_waiting(self):
        orch, xe = _make_orchestrator()
        _setup_position_open(orch)

        # Submit exit
        exit_sub = MagicMock(exit_reason="STOP", entry_order_id=42,
                             order_id=99, trade=MagicMock())
        orch._exit_submission = exit_sub
        orch._lifecycle = LifecycleState.EXIT_SUBMITTED

        with patch("trading_lab.live.trade_orchestrator.check_exit_fill") as mock_check:
            mock_check.return_value = _make_exit_fill(ExitFillState.FILLED)
            orch.refresh_exit_status()

        assert orch.lifecycle in (LifecycleState.WAITING_FOR_SIGNAL,
                                   LifecycleState.DONE_FOR_DAY)


# ═══════════════════════════════════════════════════════════════════════
# 2. First SELL fails → retry → fill → DONE
# ═══════════════════════════════════════════════════════════════════════


class TestExitRetry:
    def test_cancelled_triggers_exit_failed(self):
        """CANCELLED exit → EXIT_FAILED (first step of retry cycle)."""
        orch, xe = _make_orchestrator()
        _setup_position_open(orch)

        exit_sub = MagicMock(exit_reason="STOP", entry_order_id=42,
                             order_id=99, trade=MagicMock())
        orch._exit_submission = exit_sub
        orch._lifecycle = LifecycleState.EXIT_SUBMITTED

        with patch("trading_lab.live.trade_orchestrator.check_exit_fill") as mock_check:
            mock_check.return_value = _make_exit_fill(
                ExitFillState.CANCELLED, broker_status="Cancelled"
            )
            orch.refresh_exit_status()

        assert orch.lifecycle == LifecycleState.EXIT_FAILED

    def test_retry_resubmits_after_cooldown(self):
        """After cooldown, EXIT_FAILED retries by calling submit_exit."""
        orch, xe = _make_orchestrator()
        _setup_position_open(orch)
        orch._lifecycle = LifecycleState.EXIT_FAILED
        orch._exit_retry_cooldown_secs = 0  # no cooldown for test
        orch._last_exit_trigger = MagicMock()  # stored from original trigger

        retry_sub = MagicMock(order_id=100, exit_reason="STOP")
        xe.submit_exit.return_value = retry_sub

        orch.refresh_exit_status()

        xe.allow_resubmit.assert_called_once_with(42)
        xe.submit_exit.assert_called_once()
        assert orch.lifecycle == LifecycleState.EXIT_SUBMITTED
        assert orch._exit_retry_count == 1

    def test_retry_then_fill_completes(self):
        """Retry submits → fill on next check → trade completed."""
        orch, xe = _make_orchestrator()
        _setup_position_open(orch)
        orch._lifecycle = LifecycleState.EXIT_FAILED
        orch._exit_retry_cooldown_secs = 0
        orch._last_exit_trigger = MagicMock()

        retry_sub = MagicMock(exit_reason="STOP", entry_order_id=42,
                              order_id=100, trade=MagicMock())
        xe.submit_exit.return_value = retry_sub

        # Retry re-submits
        orch.refresh_exit_status()
        assert orch.lifecycle == LifecycleState.EXIT_SUBMITTED

        # Next check: filled
        with patch("trading_lab.live.trade_orchestrator.check_exit_fill") as mock_check:
            mock_check.return_value = _make_exit_fill(ExitFillState.FILLED)
            orch.refresh_exit_status()

        assert orch.lifecycle in (LifecycleState.WAITING_FOR_SIGNAL,
                                   LifecycleState.DONE_FOR_DAY)


# ═══════════════════════════════════════════════════════════════════════
# 3. No duplicate SELL orders
# ═══════════════════════════════════════════════════════════════════════


class TestNoDuplicateSells:
    def test_cooldown_prevents_rapid_retries(self):
        """Within cooldown window, no retry is attempted."""
        orch, xe = _make_orchestrator()
        _setup_position_open(orch)
        orch._lifecycle = LifecycleState.EXIT_FAILED
        orch._exit_retry_cooldown_secs = 30
        orch._exit_last_retry_time = time.monotonic()  # just retried
        orch._last_exit_trigger = MagicMock()

        orch.refresh_exit_status()

        xe.submit_exit.assert_not_called()
        assert orch.lifecycle == LifecycleState.EXIT_FAILED

    def test_allow_resubmit_called_before_submit(self):
        """Duplicate protection is cleared before each retry."""
        orch, xe = _make_orchestrator()
        _setup_position_open(orch)
        orch._lifecycle = LifecycleState.EXIT_FAILED
        orch._exit_retry_cooldown_secs = 0
        orch._last_exit_trigger = MagicMock()
        xe.submit_exit.return_value = MagicMock(order_id=100)

        orch.refresh_exit_status()

        xe.allow_resubmit.assert_called_once_with(42)


# ═══════════════════════════════════════════════════════════════════════
# 4. Retries exhausted → REQUIRES_ATTENTION
# ═══════════════════════════════════════════════════════════════════════


class TestRetriesExhausted:
    def test_max_retries_transitions_to_requires_attention(self):
        orch, xe = _make_orchestrator()
        _setup_position_open(orch)
        orch._lifecycle = LifecycleState.EXIT_FAILED
        orch._exit_retry_count = 3  # max reached
        orch._exit_max_retries = 3

        orch.refresh_exit_status()

        assert orch.lifecycle == LifecycleState.REQUIRES_ATTENTION
        xe.submit_exit.assert_not_called()

    def test_requires_attention_is_terminal(self):
        """Once in REQUIRES_ATTENTION, further calls don't change state."""
        orch, xe = _make_orchestrator()
        _setup_position_open(orch)
        orch._lifecycle = LifecycleState.REQUIRES_ATTENTION

        # refresh_exit_status should return immediately for non-EXIT states
        orch.refresh_exit_status()
        assert orch.lifecycle == LifecycleState.REQUIRES_ATTENTION


# ═══════════════════════════════════════════════════════════════════════
# 5. No false TRADE_COMPLETED
# ═══════════════════════════════════════════════════════════════════════


class TestNoFalseCompletion:
    def test_exit_failed_does_not_emit_trade_completed(self):
        """EXIT_FAILED must NOT emit TRADE_COMPLETED."""
        events = []
        def capture_emit(event_type, **kw):
            events.append(event_type)
            return MagicMock()

        orch, xe = _make_orchestrator()
        orch._emit_fn = capture_emit
        _setup_position_open(orch)

        exit_sub = MagicMock(exit_reason="STOP", entry_order_id=42,
                             order_id=99, trade=MagicMock())
        orch._exit_submission = exit_sub
        orch._lifecycle = LifecycleState.EXIT_SUBMITTED

        with patch("trading_lab.live.trade_orchestrator.check_exit_fill") as mock_check:
            mock_check.return_value = _make_exit_fill(
                ExitFillState.REJECTED, broker_status="Inactive"
            )
            orch.refresh_exit_status()

        assert "TRADE_COMPLETED" not in events
        assert orch.lifecycle == LifecycleState.EXIT_FAILED

    def test_retry_failure_does_not_emit_trade_completed(self):
        """Failed retry must NOT emit TRADE_COMPLETED."""
        events = []
        def capture_emit(event_type, **kw):
            events.append(event_type)
            return MagicMock()

        orch, xe = _make_orchestrator()
        orch._emit_fn = capture_emit
        _setup_position_open(orch)
        orch._lifecycle = LifecycleState.EXIT_FAILED
        orch._exit_retry_cooldown_secs = 0
        orch._last_exit_trigger = MagicMock()
        xe.submit_exit.side_effect = RuntimeError("IBKR error")

        orch.refresh_exit_status()

        assert "TRADE_COMPLETED" not in events


# ═══════════════════════════════════════════════════════════════════════
# 6. Error isolation per symbol
# ═══════════════════════════════════════════════════════════════════════


class TestErrorIsolation:
    def test_retry_exception_does_not_propagate(self):
        """Exception during retry stays contained — no crash."""
        orch, xe = _make_orchestrator()
        _setup_position_open(orch)
        orch._lifecycle = LifecycleState.EXIT_FAILED
        orch._exit_retry_cooldown_secs = 0
        orch._last_exit_trigger = MagicMock()
        xe.submit_exit.side_effect = RuntimeError("chain error")

        # Should NOT raise
        orch.refresh_exit_status()

        assert orch.lifecycle == LifecycleState.EXIT_FAILED
        assert orch._exit_retry_count == 1


# ═══════════════════════════════════════════════════════════════════════
# 7. OptionExitExecutor.allow_resubmit
# ═══════════════════════════════════════════════════════════════════════


class TestAllowResubmit:
    def test_allow_resubmit_clears_protection(self):
        xe = OptionExitExecutor(ib=MagicMock())
        xe._submitted_entry_ids.add(42)
        xe.allow_resubmit(42)
        assert 42 not in xe._submitted_entry_ids

    def test_allow_resubmit_idempotent(self):
        xe = OptionExitExecutor(ib=MagicMock())
        xe.allow_resubmit(42)  # not present — no error
        assert 42 not in xe._submitted_entry_ids
