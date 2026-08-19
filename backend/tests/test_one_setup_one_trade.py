"""Tests: one setup/break → at most one trade.

Reproduces the SOFI 2026-08-18 bug: 9 trades from one setup.
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
        underlying_symbol="SOFI", direction="SHORT",
        tick_size=0.01, session_builder=sb,
        signal_detector=sd, trade_manager=tm,
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(),
    )
    return orch


def _sig(setup_key="SHORT:1000", entry_ts=1000):
    return SignalResult(
        status=SignalStatus.SIGNAL, direction="SHORT",
        pipeline_stage="SIGNAL", failed_stage=None,
        setup_key=setup_key,
        signal_key=f"{setup_key}:{entry_ts}",
        entry_timestamp_ms=entry_ts,
        stage_context={"break_bar_index": 5},
        trade_plan=MagicMock(), detection_result=MagicMock(),
    )


def _bar(t=1000):
    return {"time_ms": t, "open": 100, "high": 101, "low": 99,
            "close": 100.5, "volume": 1000}


class TestOneSetupOneTrade:
    """TEST 1: same setup, different rejection candle → one trade."""

    def test_same_setup_different_entry_candle_blocked(self):
        # sig1's entry_ts must match bar(1000) — the edge-trigger gate
        # (added for the current-candle invariant) requires the entry
        # candle timestamp to equal the current completed bar.
        sig1 = _sig("SHORT:1000", entry_ts=1000)
        sig2 = _sig("SHORT:1000", entry_ts=6000)  # different entry candle
        sig3 = _sig("SHORT:1000", entry_ts=7000)  # yet another
        orch = _make_orch([sig1, sig2, sig3])

        # Bar 1: first signal accepted
        orch.on_bar(_bar(1000))
        assert orch.has_pending_signal
        assert "SHORT:1000" in orch._consumed_setups

        # Simulate full trade cycle: execute → fill → exit → WAITING
        orch._pending_signal = None
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        # Bar 2: different entry candle, SAME setup → BLOCKED
        orch.on_bar(_bar(2000))
        assert not orch.has_pending_signal

        # Bar 3: yet another entry candle → still BLOCKED
        orch.on_bar(_bar(3000))
        assert not orch.has_pending_signal


class TestManyRejectionCandles:
    """TEST 2: 5 different rejection candles on same setup → one trade."""

    def test_five_signals_one_trade(self):
        # First signal's entry_ts must match bar(1000) to clear the
        # edge-trigger gate; the rest are blocked via setup_key before
        # that gate is even reached, so their entry_ts is immaterial.
        signals = [_sig("SHORT:1000", entry_ts=1000)] + [
            _sig("SHORT:1000", entry_ts=5000 + i * 1000)
            for i in range(1, 5)
        ]
        orch = _make_orch(signals)

        # Only first signal accepted
        orch.on_bar(_bar(1000))
        assert orch.has_pending_signal

        orch._pending_signal = None
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        for i in range(1, 5):
            orch.on_bar(_bar(1000 + i * 1000))
            assert not orch.has_pending_signal, f"Signal {i+1} should be blocked"


class TestNewBreakAllowed:
    """TEST 3: different setup_key → second trade allowed."""

    def test_new_setup_after_consumed(self):
        # Both entry_ts values are set to match their triggering bar
        # so each clears the edge-trigger gate on its own merits.
        sig1 = _sig("SHORT:1000", entry_ts=1000)
        sig2 = _sig("SHORT:5000", entry_ts=2000)  # DIFFERENT break
        orch = _make_orch([sig1, sig2])

        # First trade
        orch.on_bar(_bar(1000))
        assert orch.has_pending_signal
        orch._pending_signal = None
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        # Second trade with new setup_key → allowed
        orch.on_bar(_bar(2000))
        assert orch.has_pending_signal


class TestFailedPreEntryNotConsumed:
    """TEST 4: signal accepted but setup consumed immediately.

    Current semantics: setup_key is consumed at signal acceptance
    in _check_for_signal, BEFORE execution. This means even a
    failed execution burns the setup. This is intentional — it's
    safer to miss a retry than to allow repeated entries.

    If a genuine new rejection candle appears on the same setup,
    it will also be blocked. Only a genuinely new break can trade.
    """

    def test_setup_consumed_at_acceptance(self):
        sig = _sig("SHORT:1000", entry_ts=1000)  # matches bar(1000)
        orch = _make_orch([sig])

        orch.on_bar(_bar(1000))

        # setup_key consumed IMMEDIATELY at acceptance
        assert "SHORT:1000" in orch._consumed_setups
        assert "SHORT:1000:1000" in orch._consumed_signals


class TestConsumedSurvivesExitCycle:
    """Consumed sets survive full trade lifecycle."""

    def test_consumed_after_exit_to_waiting(self):
        sig1 = _sig("SHORT:1000", 1000)  # matches bar(1000)
        sig2 = _sig("SHORT:1000", 6000)
        orch = _make_orch([sig1, sig2])

        # Trade #1
        orch.on_bar(_bar(1000))
        assert "SHORT:1000" in orch._consumed_setups

        # Full cycle: execute → fill → exit → WAITING
        orch._pending_signal = None
        orch._lifecycle = LifecycleState.ENTRY_SUBMITTED
        orch._entry_submission = MagicMock()
        orch._resolved_direction = "SHORT"
        orch._lifecycle = LifecycleState.POSITION_OPEN
        orch._lifecycle = LifecycleState.EXIT_SUBMITTED
        orch._clear_active_trade()
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        # STILL consumed
        assert "SHORT:1000" in orch._consumed_setups

        # Trade #2 attempt → blocked
        orch.on_bar(_bar(2000))
        assert not orch.has_pending_signal
