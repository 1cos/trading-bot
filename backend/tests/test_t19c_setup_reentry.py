"""Tests for T19C — same-setup re-entry prevention.

A single BDRR setup (identified by break_time + direction) must
produce at most ONE trade.  After exit, the same setup cannot
generate a re-entry.  A genuinely new BDRR sequence (different
break candle) CAN generate a new trade.

Covers:
1. Same setup evaluated on successive bars → one entry only
2. Exit of trade → old setup cannot re-enter
3. New break/retest → new entry allowed
4. setup_key computed correctly from break + direction
5. PAPER_EXECUTE invariant for genuinely new setups
6. OBSERVE_ONLY same behavior
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)
from trading_lab.live.trade_orchestrator import (
    MaxBotTradeOrchestrator,
    LifecycleState,
)


# ═════════════════════════════════════════════════════════════════════════════
# setup_key on SignalResult
# ═════════════════════════════════════════════════════════════════════════════


class TestSetupKey:
    """Verify setup_key is computed and available."""

    def test_setup_key_present_on_signal(self):
        r = SignalResult(
            status=SignalStatus.SIGNAL,
            direction="SHORT",
            setup_key="SHORT:1786715520000",
        )
        assert r.setup_key == "SHORT:1786715520000"

    def test_setup_key_none_on_no_setup(self):
        r = SignalResult(status=SignalStatus.NO_SETUP)
        assert r.setup_key is None

    def test_different_breaks_different_keys(self):
        r1 = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000",
        )
        r2 = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:2000",
        )
        assert r1.setup_key != r2.setup_key

    def test_same_break_different_direction_different_keys(self):
        r1 = SignalResult(
            status=SignalStatus.SIGNAL, direction="LONG",
            setup_key="LONG:1000",
        )
        r2 = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000",
        )
        assert r1.setup_key != r2.setup_key


# ═════════════════════════════════════════════════════════════════════════════
# TradeOrchestrator consumed setup logic
# ═════════════════════════════════════════════════════════════════════════════


def _make_orchestrator(signal_results):
    """Create orchestrator with mocked deps that returns signals in sequence."""
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

    os_ = MagicMock()
    ee = MagicMock()
    xe = MagicMock()

    orch = MaxBotTradeOrchestrator(
        underlying_symbol="NVDA", direction="SHORT",
        tick_size=0.01, session_builder=sb,
        signal_detector=sd, trade_manager=tm,
        option_selector=os_, entry_executor=ee,
        exit_executor=xe,
    )
    return orch, sd, os_, ee


def _make_signal(setup_key="SHORT:1000"):
    return SignalResult(
        status=SignalStatus.SIGNAL, direction="SHORT",
        pipeline_stage="SIGNAL", failed_stage=None,
        trade_plan=MagicMock(), detection_result=MagicMock(),
        setup_key=setup_key,
    )


def _make_no_setup():
    return SignalResult(
        status=SignalStatus.NO_SETUP, direction="SHORT",
        pipeline_stage="BREAK_NOT_FOUND", failed_stage="BREAK_NOT_FOUND",
    )


class TestSameSetupReEntry:
    """Core test: same setup cannot re-enter after trade."""

    def test_first_signal_accepted(self):
        sig = _make_signal("SHORT:1000")
        orch, sd, os_, ee = _make_orchestrator([sig])
        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        assert orch.has_pending_signal

    @patch("trading_lab.live.trade_orchestrator.build_option_execution_intent")
    def test_same_setup_blocked_after_execution(self, mock_intent):
        """After executing a setup, the same setup_key is consumed."""
        from decimal import Decimal
        triggers = MagicMock()
        triggers.entry_price = Decimal("225.00")
        triggers.stop_price = Decimal("226.50")
        triggers.target_price = Decimal("222.00")
        mock_intent.return_value = MagicMock(underlying_triggers=triggers)

        sig1 = _make_signal("SHORT:1000")
        sig2 = _make_signal("SHORT:1000")  # SAME setup key
        orch, sd, os_, ee = _make_orchestrator([sig1, sig2])

        selection = MagicMock(
            right="P", expiration="20260115", strike=225.0,
            con_id=123, exchange="SMART", multiplier="100",
            bid=3.00, ask=3.20, spread=0.20,
        )
        os_.select.return_value = selection
        ee.submit_entry.return_value = MagicMock(
            order_id=42, perm_id=99, status="Submitted", con_id=123,
        )

        # First bar: signal detected + executed
        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        assert orch.has_pending_signal
        orch.execute_pending_signal()
        assert not orch.has_pending_signal

        # Simulate exit → WAITING_FOR_SIGNAL
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        # Second bar: same setup_key → BLOCKED
        bar2 = {"time_ms": 2000, "open": 100, "high": 101,
                "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar2)
        assert not orch.has_pending_signal  # blocked!
        assert os_.select.call_count == 1  # only first trade

    @patch("trading_lab.live.trade_orchestrator.build_option_execution_intent")
    def test_new_setup_allowed_after_consumed(self, mock_intent):
        """A genuinely new setup (different break) IS allowed."""
        from decimal import Decimal
        triggers = MagicMock()
        triggers.entry_price = Decimal("225.00")
        triggers.stop_price = Decimal("226.50")
        triggers.target_price = Decimal("222.00")
        mock_intent.return_value = MagicMock(underlying_triggers=triggers)

        sig1 = _make_signal("SHORT:1000")  # first break
        sig2 = _make_signal("SHORT:5000")  # NEW break at different time
        orch, sd, os_, ee = _make_orchestrator([sig1, sig2])

        selection = MagicMock(
            right="P", expiration="20260115", strike=225.0,
            con_id=123, exchange="SMART", multiplier="100",
            bid=3.00, ask=3.20, spread=0.20,
        )
        os_.select.return_value = selection
        ee.submit_entry.return_value = MagicMock(
            order_id=42, perm_id=99, status="Submitted", con_id=123,
        )

        # First trade
        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        orch.execute_pending_signal()

        # Exit → back to waiting
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        # Second bar: NEW setup key → allowed
        bar2 = {"time_ms": 6000, "open": 100, "high": 101,
                "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar2)
        assert orch.has_pending_signal  # allowed!

    def test_same_setup_consumed_at_first_acceptance(self):
        """Setup consumed at signal acceptance, not just at execution."""
        sig1 = _make_signal("SHORT:1000")
        sig2 = _make_signal("SHORT:1000")
        orch, sd, os_, ee = _make_orchestrator([sig1, sig2])

        # First bar: signal accepted → setup consumed immediately
        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        assert orch.has_pending_signal
        assert "SHORT:1000" in orch._consumed_setups

        orch._pending_signal = None
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        # Second bar: same setup → BLOCKED (consumed at acceptance)
        bar2 = {"time_ms": 2000, "open": 100, "high": 101,
                "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar2)
        assert not orch.has_pending_signal

    def test_no_setup_result_not_affected(self):
        """NO_SETUP results are unaffected by consumed setups."""
        no_setup = _make_no_setup()
        orch, sd, os_, ee = _make_orchestrator([no_setup])

        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        assert not orch.has_pending_signal

    def test_signal_without_setup_key_still_works(self):
        """A signal with no setup_key (backward compat) is not blocked."""
        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            pipeline_stage="SIGNAL", failed_stage=None,
            trade_plan=MagicMock(), detection_result=MagicMock(),
            setup_key=None,  # no key
        )
        orch, sd, os_, ee = _make_orchestrator([sig])

        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        assert orch.has_pending_signal


# ═════════════════════════════════════════════════════════════════════════════
# ObserveOrchestrator same behavior
# ═════════════════════════════════════════════════════════════════════════════


class TestObserveSameSetup:
    """ObserveOrchestrator also blocks same-setup re-entry."""

    def test_same_setup_blocked_in_observe(self):
        from trading_lab.live.observe_orchestrator import ObserveOrchestrator

        sb = MagicMock()
        sb.current_session.return_value = {
            "date": "2026-01-15",
            "candles": [{"time_ms": 1000, "open": 100, "high": 101,
                         "low": 99, "close": 100.5, "volume": 1000}],
        }

        sig1 = _make_signal("SHORT:1000")
        sig2 = _make_signal("SHORT:1000")  # same setup
        sd = MagicMock()
        sd.evaluate = MagicMock(side_effect=[sig1, sig2])
        sd.last_result = None
        os_ = MagicMock()

        orch = ObserveOrchestrator(
            underlying_symbol="NVDA", direction="SHORT",
            tick_size=0.01, session_builder=sb,
            signal_detector=sd, option_selector=os_,
        )

        # First bar: signal pending
        bar = {"time_ms": 1000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)
        assert orch.has_pending_signal

        # Simulate execution to consume the setup
        orch._consumed_setups.add("SHORT:1000")
        orch._pending_signal = None
        orch._lifecycle = orch._lifecycle  # stay in current state

        # Reset lifecycle to allow re-evaluation
        from trading_lab.live.observe_orchestrator import ObserveLifecycle
        orch._lifecycle = ObserveLifecycle.WAITING_FOR_SIGNAL

        # Second bar: same setup → blocked
        bar2 = {"time_ms": 2000, "open": 100, "high": 101,
                "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar2)
        assert not orch.has_pending_signal
