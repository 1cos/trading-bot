"""Tests for the startup existing-broker-position safety gate.

Scenario: MaxBot enters a position, the process is stopped (STOP or a
full process restart — architecturally equivalent, see the prior
STOP/RESTART audit), then restarted. The new MaxBotRunner has zero
memory of the prior trade. Without a startup check, a fresh SIGNAL on
the same symbol could submit a second, duplicate entry order while the
original option position sits open and unmanaged.

This task adds ONLY the detection + entry-block gate:
  - MaxBotRunner._reconcile_existing_positions() queries IBKR positions
    once at startup and marks matching symbols as blocked.
  - The block is enforced at two independent points:
      1. Primary: the orchestrator's lifecycle is set to
         EXISTING_BROKER_POSITION, which (via the existing, unmodified
         on_bar() dispatch) means _check_for_signal() is never called
         again for that symbol — no SIGNAL can ever become pending.
      2. Defensive: execute_pending_signal() itself refuses to run if
         _broker_position_blocked is True, regardless of how
         _pending_signal got set.

Full trade recovery (entry/stop/target/setup_key reconstruction,
automatic exit management) is explicitly NOT part of this task.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.trade_orchestrator import LifecycleState


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_option_position(symbol, quantity=1, con_id=999, local_symbol=None,
                           right="P", strike=200.0, expiry="20260821"):
    """Build a fake ib_insync Position(account, contract, position, avgCost)
    for an OPT contract, matching the attribute names this codebase
    already uses elsewhere for Option()."""
    contract = SimpleNamespace(
        secType="OPT", symbol=symbol, conId=con_id,
        localSymbol=local_symbol or f"{symbol}  260821{right}00{int(strike)}000",
        right=right, strike=strike, lastTradeDateOrContractMonth=expiry,
    )
    return SimpleNamespace(account="DU123", contract=contract,
                            position=quantity, avgCost=1.0)


def _make_stock_position(symbol, quantity=100):
    contract = SimpleNamespace(secType="STK", symbol=symbol, conId=111,
                                localSymbol=symbol)
    return SimpleNamespace(account="DU123", contract=contract,
                            position=quantity, avgCost=250.0)


def _ready_runner(symbols, positions, execution_mode="PAPER_EXECUTE"):
    """A runner past qualification, with a mocked ib.positions()."""
    runner = MaxBotRunner(symbols, execution_mode=execution_mode)
    ib = MagicMock()
    ib.managedAccounts.return_value = ["DU123"]
    ib.positions.return_value = positions
    runner._ib = ib
    runner._verify_paper()
    runner._setup_all_symbols()
    runner._qualify_all()
    return runner


# ═════════════════════════════════════════════════════════════════════════
# A. Existing option position blocks entry
# ═════════════════════════════════════════════════════════════════════════


class TestExistingOptionPositionBlocksEntry:
    def test_detector_can_still_signal_but_execution_is_blocked(self):
        runner = _ready_runner(
            ["TSLA"], [_make_option_position("TSLA", quantity=1)],
        )
        runner._reconcile_existing_positions()

        rt = runner._runtimes["TSLA"]
        assert rt.broker_position_blocked is True
        assert rt.broker_position_info["quantity"] == 1
        orch = rt.orchestrator
        assert orch._broker_position_blocked is True
        assert orch.lifecycle == LifecycleState.EXISTING_BROKER_POSITION

        # Even if a SIGNAL is somehow force-fed as pending (simulating
        # some future code path that bypasses the primary lifecycle
        # gate), the defensive check in execute_pending_signal() must
        # still refuse to call the entry executor.
        fake_signal = MagicMock()
        fake_signal.setup_key = "SHORT:123"
        orch._pending_signal = fake_signal
        orch._entry_executor = MagicMock()

        orch.execute_pending_signal()

        orch._entry_executor.assert_not_called()
        assert orch._pending_signal is None
        assert orch.lifecycle == LifecycleState.EXISTING_BROKER_POSITION

    def test_on_bar_never_reaches_check_for_signal_once_blocked(self):
        """The real, close-to-execution protection: once blocked, on_bar's
        existing dispatch (unmodified) skips signal-seeking entirely."""
        runner = _ready_runner(
            ["TSLA"], [_make_option_position("TSLA", quantity=1)],
        )
        runner._reconcile_existing_positions()
        orch = runner._runtimes["TSLA"].orchestrator
        orch._signal_detector.evaluate = MagicMock(
            side_effect=AssertionError("evaluate() must not be called while blocked")
        )

        bar = {"time_ms": 1_000_000, "open": 100, "high": 101, "low": 99, "close": 100.5}
        orch.on_bar(bar)  # must not raise

        orch._signal_detector.evaluate.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════
# B. Different symbol remains tradable — gate is per-symbol
# ═════════════════════════════════════════════════════════════════════════


class TestDifferentSymbolRemainsTradable:
    def test_nvda_unaffected_by_tsla_block(self):
        runner = _ready_runner(
            ["TSLA", "NVDA"], [_make_option_position("TSLA", quantity=1)],
        )
        runner._reconcile_existing_positions()

        assert runner._runtimes["TSLA"].broker_position_blocked is True
        assert runner._runtimes["NVDA"].broker_position_blocked is False
        assert runner._runtimes["NVDA"].orchestrator._broker_position_blocked is False
        assert runner._runtimes["NVDA"].orchestrator.lifecycle == LifecycleState.WAITING_FOR_SIGNAL


# ═════════════════════════════════════════════════════════════════════════
# C. Position zero does not block
# ═════════════════════════════════════════════════════════════════════════


class TestZeroPositionDoesNotBlock:
    def test_closed_position_ignored(self):
        runner = _ready_runner(
            ["TSLA"], [_make_option_position("TSLA", quantity=0)],
        )
        runner._reconcile_existing_positions()

        rt = runner._runtimes["TSLA"]
        assert rt.broker_position_blocked is False
        assert rt.orchestrator._broker_position_blocked is False
        assert rt.orchestrator.lifecycle == LifecycleState.WAITING_FOR_SIGNAL

    def test_no_positions_at_all(self):
        runner = _ready_runner(["TSLA"], [])
        runner._reconcile_existing_positions()

        rt = runner._runtimes["TSLA"]
        assert rt.broker_position_blocked is False
        assert rt.orchestrator.lifecycle == LifecycleState.WAITING_FOR_SIGNAL


# ═════════════════════════════════════════════════════════════════════════
# D. Non-option position must not be adopted as a block
# ═════════════════════════════════════════════════════════════════════════


class TestNonOptionPositionNotAdopted:
    def test_stock_position_does_not_block(self):
        """Documented chosen behavior: MaxBot only ever holds options, so
        a STK position in the account is not evidence of an untracked
        MaxBot trade and must not trigger the block."""
        runner = _ready_runner(
            ["TSLA"], [_make_stock_position("TSLA", quantity=100)],
        )
        runner._reconcile_existing_positions()

        rt = runner._runtimes["TSLA"]
        assert rt.broker_position_blocked is False
        assert rt.orchestrator.lifecycle == LifecycleState.WAITING_FOR_SIGNAL


# ═════════════════════════════════════════════════════════════════════════
# E. Restart reproduction: Runner A's position is still visible to
#    Runner B (a fresh runner, simulating a full restart) and gets
#    detected/blocked independently.
# ═════════════════════════════════════════════════════════════════════════


class TestRestartReproduction:
    def test_runner_b_detects_position_left_open_by_runner_a(self):
        # Runner A opens (conceptually) a position -- we don't need to
        # actually run execution here, we just need IBKR (mocked) to
        # report the resulting position, exactly as it would for a
        # real broker after Runner A is gone.
        runner_a = _ready_runner(["TSLA"], [])
        runner_a._reconcile_existing_positions()
        assert runner_a._runtimes["TSLA"].broker_position_blocked is False
        # Runner A "destroyed" -- nothing further happens with it.
        del runner_a

        # Runner B: brand new runner, brand new orchestrator, zero
        # memory of Runner A. IBKR (mocked) now reports the open
        # position that Runner A's (earlier, unrelated) trade left
        # behind.
        runner_b = _ready_runner(
            ["TSLA"], [_make_option_position("TSLA", quantity=1, con_id=555)],
        )
        runner_b._reconcile_existing_positions()

        rt_b = runner_b._runtimes["TSLA"]
        assert rt_b.broker_position_blocked is True
        assert rt_b.broker_position_info["conId"] == 555
        assert rt_b.orchestrator.lifecycle == LifecycleState.EXISTING_BROKER_POSITION

        # And a signal on Runner B for TSLA still cannot execute.
        fake_signal = MagicMock()
        fake_signal.setup_key = "LONG:999"
        rt_b.orchestrator._pending_signal = fake_signal
        rt_b.orchestrator._entry_executor = MagicMock()
        rt_b.orchestrator.execute_pending_signal()
        rt_b.orchestrator._entry_executor.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════
# Observe mode: reconciliation should not crash when the orchestrator
# is an ObserveOrchestrator (no _broker_position_blocked attribute).
# ═════════════════════════════════════════════════════════════════════════


class TestObserveModeUnaffected:
    def test_reconcile_does_not_crash_in_observe_mode(self):
        runner = _ready_runner(
            ["TSLA"], [_make_option_position("TSLA", quantity=1)],
            execution_mode="OBSERVE_ONLY",
        )
        runner._reconcile_existing_positions()  # must not raise
        rt = runner._runtimes["TSLA"]
        # Reconciliation still records the fact at the runtime level...
        assert rt.broker_position_blocked is True
        # ...but ObserveOrchestrator (never submits real orders) has no
        # _broker_position_blocked attribute and is left untouched.
        assert not hasattr(rt.orchestrator, "_broker_position_blocked")
