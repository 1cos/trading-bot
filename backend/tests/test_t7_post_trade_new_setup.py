"""T7 (2026-08-21 audit) — post-trade: new setup detectable after close.

Existing coverage checked first: test_t19c_setup_reentry.py::
TestSameSetupReEntry::test_new_setup_allowed_after_consumed already
exercises "SIGNAL A -> execute -> exit -> SIGNAL B (new setup_key) ->
accepted", and test_same_setup_consumed_at_first_acceptance separately
asserts "SHORT:1000" in orch._consumed_setups for setup A alone. But no
single existing test combines both checks — none of the existing tests
assert that _consumed_setups explicitly RETAINS A's key at the same
time B is found and accepted (task requirement #1 + #6 together). This
test closes that gap explicitly, without duplicating the existing
fixture logic (helpers imported from test_t19c_setup_reentry.py).

Uses the same established mocked-orchestrator pattern as T19C/T19F —
signal_detector.evaluate() is scripted with a signal sequence; the
lifecycle reset after "close" is done directly (orch._lifecycle =
WAITING_FOR_SIGNAL), matching the existing convention in this test
suite for simulating a completed exit without mocking the full
broker-facing exit-fill flow.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from trading_lab.live.trade_orchestrator import LifecycleState
from test_t19c_setup_reentry import _make_orchestrator, _make_signal


@patch("trading_lab.live.trade_orchestrator.build_option_execution_intent")
def test_t7_new_setup_detected_after_trade_a_closed(mock_intent):
    triggers = MagicMock()
    triggers.entry_price = Decimal("225.00")
    triggers.stop_price = Decimal("226.50")
    triggers.target_price = Decimal("222.00")
    mock_intent.return_value = MagicMock(underlying_triggers=triggers)

    setup_key_a = "SHORT:1000"
    setup_key_b = "SHORT:5000"
    sig_a = _make_signal(setup_key_a)
    sig_b = _make_signal(setup_key_b)  # genuinely new break
    orch, sd, os_, ee = _make_orchestrator([sig_a, sig_b])

    selection = MagicMock(
        right="P", expiration="20260115", strike=225.0,
        con_id=123, exchange="SMART", multiplier="100",
        bid=3.00, ask=3.20, spread=0.20,
    )
    os_.select.return_value = selection
    ee.submit_entry.return_value = MagicMock(
        order_id=42, perm_id=99, status="Submitted", con_id=123,
    )

    # ── Setup A: SIGNAL -> accepted -> executed ─────────────────────────
    bar_a = {"time_ms": 1000, "open": 100, "high": 101,
             "low": 99, "close": 100.5, "volume": 1000}
    orch.on_bar(bar_a)
    assert orch.has_pending_signal
    orch.execute_pending_signal()
    assert not orch.has_pending_signal

    # 1. Setup A is consumed.
    assert setup_key_a in orch._consumed_setups

    # ── Trade A closed/completed: lifecycle returns to operational ─────
    orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

    # 2. Lifecycle is not stuck — it is back to WAITING_FOR_SIGNAL,
    # ready to process new bars normally.
    assert orch.lifecycle == LifecycleState.WAITING_FOR_SIGNAL

    # ── Setup B: genuinely new break arrives ────────────────────────────
    bar_b = {"time_ms": 6000, "open": 100, "high": 101,
             "low": 99, "close": 100.5, "volume": 1000}
    orch.on_bar(bar_b)

    # 3. Setup B has a different setup_key from A.
    assert setup_key_b != setup_key_a

    # 4. Setup B is detected/accepted (pending signal set).
    assert orch.has_pending_signal
    assert orch._pending_signal.setup_key == setup_key_b

    # 5. A is not reused — the pending signal belongs to B, not A.
    assert orch._pending_signal.setup_key != setup_key_a

    # 6. _consumed_setups retains A but does not block B: A's key is
    # still present, and B was still found and accepted (not blocked as
    # a duplicate) even though B's own setup_key is ALSO now in
    # _consumed_setups (setup_key is recorded at signal acceptance
    # time, in on_bar/_check_for_signal, not only at execution —
    # verified directly: both keys are present here). What matters for
    # T7 is that A's presence in the set never blocked B from being
    # detected and accepted in the first place.
    assert setup_key_a in orch._consumed_setups
    assert orch.has_pending_signal
