"""Tests for exactly-once signal execution (signal_key).

Root cause: the stateless detector finds the SAME complete BDRR
sequence on every bar evaluation. setup_key (break identity) was
consumed after execution, but the SAME signal (same entry candle)
was re-emitted on subsequent bars because setup_key consumption
only prevented re-entry on the same BREAK, not re-emission of
the same SIGNAL.

Fix: signal_key = setup_key:entry_candle_time_ms. Consumed after
first execution attempt (success or cancel). Same signal cannot
re-fire. A new entry candle produces a new signal_key.
"""

from unittest.mock import MagicMock, patch
import pytest

from trading_lab.live.signal_detector import SignalResult, SignalStatus
from trading_lab.live.trade_orchestrator import (
    MaxBotTradeOrchestrator,
    LifecycleState,
)


def _make_orch(signal_results):
    sb = MagicMock()
    sb.current_session.return_value = {
        "date": "2026-01-15",
        "candles": [{"time_ms": 1000, "open": 100, "high": 101,
                     "low": 99, "close": 100.5, "volume": 1000}],
    }
    sd = MagicMock()
    sd.evaluate = MagicMock(side_effect=signal_results)
    sd.last_result = None
    tm = MagicMock()
    tm.can_trade = True
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="QQQ", direction="SHORT",
        tick_size=0.01, session_builder=sb,
        signal_detector=sd, trade_manager=tm,
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(),
    )
    return orch, sd


def _sig(setup_key="SHORT:1000", entry_ts=5000):
    return SignalResult(
        status=SignalStatus.SIGNAL, direction="SHORT",
        pipeline_stage="SIGNAL", failed_stage=None,
        setup_key=setup_key,
        signal_key=f"{setup_key}:{entry_ts}",
        stage_context={"break_bar_index": 5},
        trade_plan=MagicMock(), detection_result=MagicMock(),
    )


def _no():
    return SignalResult(
        status=SignalStatus.NO_SETUP, direction="SHORT",
        failed_stage="BREAK_NOT_FOUND",
    )


def _bar(t=1000):
    return {"time_ms": t, "open": 100, "high": 101, "low": 99,
            "close": 100.5, "volume": 1000}


class TestSignalKeyExactlyOnce:
    def test_first_signal_accepted(self):
        sig = _sig()
        orch, sd = _make_orch([sig])
        orch.on_bar(_bar())
        assert orch.has_pending_signal

    @patch("trading_lab.live.trade_orchestrator.build_option_execution_intent")
    def test_same_signal_blocked_after_execution(self, mock_intent):
        from decimal import Decimal
        triggers = MagicMock()
        triggers.entry_price = Decimal("100")
        triggers.stop_price = Decimal("101")
        triggers.target_price = Decimal("98")
        mock_intent.return_value = MagicMock(underlying_triggers=triggers)

        sig1 = _sig("SHORT:1000", 5000)
        sig2 = _sig("SHORT:1000", 5000)  # SAME signal_key
        orch, sd = _make_orch([sig1, sig2])

        sel = MagicMock(right="P", expiration="20260115", strike=100.0,
                        con_id=123, exchange="SMART", multiplier="100",
                        bid=1.5, ask=1.6, spread=0.1)
        orch._option_selector.select.return_value = sel
        orch._entry_executor.submit_entry.return_value = MagicMock(
            order_id=42, perm_id=99, status="Submitted", con_id=123)

        # First bar: signal → execute
        orch.on_bar(_bar(1000))
        orch.execute_pending_signal()

        # Simulate cancel → back to WAITING_FOR_SIGNAL
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        # Second bar: same signal_key → BLOCKED
        orch.on_bar(_bar(2000))
        assert not orch.has_pending_signal

    def test_same_signal_blocked_even_without_execution(self):
        """Signal consumed at _check_for_signal level, before execute."""
        sig1 = _sig("SHORT:1000", 5000)
        sig2 = _sig("SHORT:1000", 5000)
        orch, sd = _make_orch([sig1, sig2])

        # First bar: signal pending
        orch.on_bar(_bar(1000))
        assert orch.has_pending_signal

        # Manually consume (simulating what execute_pending_signal does)
        orch._consumed_signals.add("SHORT:1000:5000")
        orch._consumed_setups.add("SHORT:1000")
        orch._pending_signal = None
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        # Second bar: same signal_key → blocked
        orch.on_bar(_bar(2000))
        assert not orch.has_pending_signal

    def test_different_entry_candle_blocked_by_setup(self):
        """New entry candle on SAME setup → blocked (one setup = one trade)."""
        sig1 = _sig("SHORT:1000", 5000)
        sig2 = _sig("SHORT:1000", 7000)  # different entry candle, SAME setup
        orch, sd = _make_orch([sig1, sig2])

        orch.on_bar(_bar(1000))
        # setup_key consumed immediately at acceptance
        assert "SHORT:1000" in orch._consumed_setups
        orch._pending_signal = None
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        # New entry candle on same setup → BLOCKED
        orch.on_bar(_bar(2000))
        assert not orch.has_pending_signal

    def test_different_setup_allowed(self):
        """Different break → different setup_key → allowed."""
        sig1 = _sig("SHORT:1000", 5000)
        sig2 = _sig("SHORT:3000", 8000)  # different break
        orch, sd = _make_orch([sig1, sig2])

        orch.on_bar(_bar(1000))
        orch._consumed_signals.add("SHORT:1000:5000")
        orch._consumed_setups.add("SHORT:1000")
        orch._pending_signal = None
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        orch.on_bar(_bar(2000))
        assert orch.has_pending_signal


class TestQQQRegression:
    """Reproduce the QQQ 10:21→10:25→10:28 repeated signal bug."""

    def test_same_signal_three_bars_only_one_enqueue(self):
        """Same signal evaluated on 3 successive bars → exactly 1 pending."""
        sig = _sig("SHORT:1000", 5000)
        orch, sd = _make_orch([sig, sig, sig])

        # Bar 1: signal → pending
        orch.on_bar(_bar(1000))
        assert orch.has_pending_signal

        # Consume it
        orch._consumed_signals.add("SHORT:1000:5000")
        orch._consumed_setups.add("SHORT:1000")
        orch._pending_signal = None
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        # Bar 2: same signal → blocked
        orch.on_bar(_bar(2000))
        assert not orch.has_pending_signal

        # Bar 3: same signal → still blocked
        orch.on_bar(_bar(3000))
        assert not orch.has_pending_signal


class TestSignalKeyOnSignalResult:
    def test_signal_key_format(self):
        r = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", signal_key="SHORT:1000:5000",
        )
        assert r.signal_key == "SHORT:1000:5000"

    def test_no_setup_has_no_signal_key(self):
        r = SignalResult(status=SignalStatus.NO_SETUP)
        assert r.signal_key is None
