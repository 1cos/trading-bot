"""T10 (2026-08-21 audit) — consumed semantics after restart.

Close to T12, but isolates the internal consumed-set bookkeeping
specifically: a historical SIGNAL A whose entry candle predates
_live_boundary_ms (simulating a restart) must be:

    1. recognized as stale (never executed);
    2. marked consumed (setup_key -> _consumed_setups,
       signal_key -> _consumed_signals) so it cannot monopolize later
       scanning (Fix B);
    3. exactly-once: a later evaluation of the SAME SIGNAL A must not
       re-attempt it or duplicate anything.

Unlike T9/T12, this test does not need a genuinely new setup B — that
end-to-end "detector reaches B" mechanism is already covered by T12.
Here signal_detector.evaluate() is mocked with a scripted, deterministic
sequence (the same established pattern used in test_edge_triggered_signal.py
and test_t19c_setup_reentry.py) because what's under test is the
orchestrator's own bookkeeping given a known SIGNAL result, not whether
the real pipeline can scan past a dead/consumed break.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from trading_lab.live.signal_detector import SignalResult, SignalStatus
from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator


def _mock_orchestrator(signal_results):
    sb = MagicMock()
    sb.current_session.return_value = {
        "date": "2026-01-15",
        "candles": [{"time_ms": 5000, "open": 100, "high": 101,
                     "low": 99, "close": 100.5, "volume": 1000}],
    }
    sd = MagicMock()
    sd.evaluate = MagicMock(side_effect=signal_results)
    sd.last_result = None
    tm = MagicMock()
    tm.can_trade = True
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="NVDA", direction="SHORT", tick_size=0.01,
        session_builder=sb, signal_detector=sd, trade_manager=tm,
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(),
    )
    return orch


def test_t10_stale_signal_marked_consumed_and_not_reattempted():
    setup_key_a = "SHORT:1000"
    signal_key_a = "SHORT:1000:5000"

    sig_a = SignalResult(
        status=SignalStatus.SIGNAL, direction="SHORT",
        setup_key=setup_key_a, signal_key=signal_key_a,
        entry_timestamp_ms=5000,
        pipeline_stage="SIGNAL", trade_plan=MagicMock(),
        detection_result=MagicMock(),
    )
    # Same SIGNAL A found again on the next bar (as the real, stateless
    # detector would keep re-deriving it if not yet marked consumed).
    sig_a_again = SignalResult(
        status=SignalStatus.SIGNAL, direction="SHORT",
        setup_key=setup_key_a, signal_key=signal_key_a,
        entry_timestamp_ms=5000,
        pipeline_stage="SIGNAL", trade_plan=MagicMock(),
        detection_result=MagicMock(),
    )

    orch = _mock_orchestrator([sig_a, sig_a_again])
    orch._live_boundary_ms = 6000  # simulates restart: A's entry (5000) predates it

    bar = {"time_ms": 5000, "open": 100, "high": 101,
           "low": 99, "close": 100.5, "volume": 1000}

    # ── First evaluation: SIGNAL A found, but is stale ───────────────────
    orch.on_bar(bar)

    # 1. No pending trade/entry was created for SIGNAL A.
    assert not orch.has_pending_signal

    # 2. setup_key A is now in _consumed_setups.
    assert setup_key_a in orch._consumed_setups

    # 3. signal_key A is now in _consumed_signals (Fix B marks both).
    assert signal_key_a in orch._consumed_signals

    # ── Second evaluation (next bar): the same SIGNAL A resurfaces ──────
    bar2 = {"time_ms": 5500, "open": 100, "high": 101,
            "low": 99, "close": 100.5, "volume": 1000}
    orch.on_bar(bar2)

    # 4+5. A is not re-attempted — still no pending signal, and the
    # consumed sets still contain exactly A's keys (no duplication,
    # exactly-once behavior preserved).
    assert not orch.has_pending_signal
    assert orch._consumed_setups == {setup_key_a}
    assert orch._consumed_signals == {signal_key_a}
