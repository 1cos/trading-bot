"""Tests for OptionExitExecutor — SELL MARKET after underlying trigger.

All tests use mock IB. No real broker connection.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from trading_lab.live.underlying_exit_monitor import ExitState, ExitTriggerResult
from trading_lab.live.option_exit_executor import (
    ExitSubmissionResult,
    OptionExitExecutor,
)


# ── Mock helpers ─────────────────────────────────────────────────────────────

def _mock_contract():
    return SimpleNamespace(
        symbol="QQQ", conId=123456, secType="OPT",
        lastTradeDateOrContractMonth="20260811",
        strike=585.0, right="C", exchange="SMART",
        multiplier="100",
    )


def _mock_ib():
    ib = MagicMock()
    mock_order = SimpleNamespace(orderId=55, permId=888)
    mock_status = SimpleNamespace(status="PendingSubmit")
    mock_trade = SimpleNamespace(
        order=mock_order, orderStatus=mock_status,
        contract=_mock_contract(), fills=[], log=[],
    )
    ib.placeOrder.return_value = mock_trade
    return ib


def _stop_trigger(stop=584.70, target=586.20, bar_time=1786455060000):
    return ExitTriggerResult(
        state=ExitState.STOP_TRIGGERED,
        direction="LONG",
        stop_price=stop,
        target_price=target,
        trigger_bar_time_ms=bar_time,
        trigger_bar_open=585.0,
        trigger_bar_high=585.1,
        trigger_bar_low=584.5,
        trigger_bar_close=584.6,
        same_bar_ambiguity=False,
    )


def _target_trigger(stop=584.70, target=586.20, bar_time=1786455120000):
    return ExitTriggerResult(
        state=ExitState.TARGET_TRIGGERED,
        direction="LONG",
        stop_price=stop,
        target_price=target,
        trigger_bar_time_ms=bar_time,
        trigger_bar_open=585.8,
        trigger_bar_high=586.5,
        trigger_bar_low=585.6,
        trigger_bar_close=586.3,
        same_bar_ambiguity=False,
    )


def _hold_trigger():
    return ExitTriggerResult(
        state=ExitState.HOLD,
        direction="LONG",
        stop_price=584.70,
        target_price=586.20,
    )


# ── Test 1: STOP → SELL MarketOrder ──────────────────────────────────────────

class TestStopSell:
    def test_sell_market(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        ex.submit_exit(
            _mock_contract(), _stop_trigger(), entry_order_id=42,
            con_id=123456, right="C", expiration="20260811", strike=585.0,
        )
        order = ib.placeOrder.call_args[0][1]
        assert order.action == "SELL"
        assert order.orderType == "MKT"


# ── Test 2: TARGET → SELL MarketOrder ────────────────────────────────────────

class TestTargetSell:
    def test_sell_market(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        ex.submit_exit(
            _mock_contract(), _target_trigger(), entry_order_id=42,
            con_id=123456, right="C", expiration="20260811", strike=585.0,
        )
        order = ib.placeOrder.call_args[0][1]
        assert order.action == "SELL"
        assert order.orderType == "MKT"


# ── Test 3: Quantity = 1 ────────────────────────────────────────────────────

class TestQuantity:
    def test_one(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        order = ib.placeOrder.call_args[0][1]
        assert order.totalQuantity == 1


# ── Test 4: Exact qualified contract reused ──────────────────────────────────

class TestContractReuse:
    def test_exact_contract(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        contract = _mock_contract()
        ex.submit_exit(contract, _stop_trigger(), entry_order_id=42)
        passed = ib.placeOrder.call_args[0][0]
        assert passed is contract


# ── Test 5: conId preserved ──────────────────────────────────────────────────

class TestConId:
    def test_preserved(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        r = ex.submit_exit(
            _mock_contract(), _stop_trigger(), entry_order_id=42,
            con_id=123456,
        )
        assert r.con_id == 123456


# ── Test 6: placeOrder called once ───────────────────────────────────────────

class TestSingleCall:
    def test_once(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        assert ib.placeOrder.call_count == 1


# ── Test 7: No option re-selection ───────────────────────────────────────────

class TestNoReselection:
    def test_no_chain(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        ib.reqSecDefOptParams.assert_not_called()


# ── Test 8: No option-chain request ──────────────────────────────────────────
# (covered by test 7)


# ── Test 9: No market-data request ───────────────────────────────────────────

class TestNoMarketData:
    def test_no_mkt_data(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        ib.reqMktData.assert_not_called()


# ── Test 10: Exit reason STOP preserved ──────────────────────────────────────

class TestStopReason:
    def test_reason(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        r = ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        assert r.exit_reason == "STOP"


# ── Test 11: Exit reason TARGET preserved ────────────────────────────────────

class TestTargetReason:
    def test_reason(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        r = ex.submit_exit(_mock_contract(), _target_trigger(), entry_order_id=42)
        assert r.exit_reason == "TARGET"


# ── Test 12: Trigger timestamp preserved ─────────────────────────────────────

class TestTriggerTimestamp:
    def test_preserved(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        r = ex.submit_exit(_mock_contract(), _stop_trigger(bar_time=999), entry_order_id=42)
        assert r.trigger_bar_time_ms == 999


# ── Test 13: Underlying levels preserved ─────────────────────────────────────

class TestLevelsPreserved:
    def test_levels(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        r = ex.submit_exit(
            _mock_contract(), _stop_trigger(stop=584.70, target=586.20),
            entry_order_id=42,
        )
        assert r.underlying_stop_price == 584.70
        assert r.underlying_target_price == 586.20


# ── Test 14: Submission ≠ filled ─────────────────────────────────────────────

class TestNotFilled:
    def test_status_not_filled(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        r = ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        assert r.status != "Filled"
        assert r.status == "PendingSubmit"


# ── Test 15: HOLD trigger rejected ───────────────────────────────────────────

class TestHoldRejected:
    def test_hold(self):
        """A non-terminal trigger must never reach the broker.

        The assertion used to pin the exact wording "STOP_TRIGGERED or
        TARGET_TRIGGERED". That wording became wrong when
        SESSION_END_TRIGGERED joined the valid set, while the property
        being guarded — HOLD is refused and no order is placed — did
        not change. Now the property is asserted directly.
        """
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        with pytest.raises(ValueError, match="HOLD"):
            ex.submit_exit(_mock_contract(), _hold_trigger(), entry_order_id=42)
        ib.placeOrder.assert_not_called()

    def test_session_end_is_accepted(self):
        """The counterpart: the new terminal state IS valid."""
        from trading_lab.live.option_exit_executor import _TRIGGER_TO_REASON
        from trading_lab.live.underlying_exit_monitor import ExitState
        assert _TRIGGER_TO_REASON[ExitState.SESSION_END_TRIGGERED] == "SESSION_END"


# ── Test 16: Missing contract rejected ───────────────────────────────────────

class TestMissingContract:
    def test_none(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        with pytest.raises(ValueError, match="qualified_contract"):
            ex.submit_exit(None, _stop_trigger(), entry_order_id=42)
        ib.placeOrder.assert_not_called()


# ── Test 17: Invalid quantity rejected ───────────────────────────────────────

class TestInvalidQuantity:
    def test_two(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        with pytest.raises(ValueError, match="quantity=1"):
            ex.submit_exit(
                _mock_contract(), _stop_trigger(), entry_order_id=42,
                quantity=2,
            )
        ib.placeOrder.assert_not_called()


# ── Test 18: Broker exception propagated ─────────────────────────────────────

class TestBrokerException:
    def test_propagates(self):
        ib = _mock_ib()
        ib.placeOrder.side_effect = RuntimeError("TWS error")
        ex = OptionExitExecutor(ib)
        with pytest.raises(RuntimeError, match="TWS error"):
            ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)


# ── Test 19: No automatic retry ──────────────────────────────────────────────

class TestNoRetry:
    def test_single_attempt(self):
        ib = _mock_ib()
        ib.placeOrder.side_effect = RuntimeError("fail")
        ex = OptionExitExecutor(ib)
        with pytest.raises(RuntimeError):
            ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        assert ib.placeOrder.call_count == 1


# ── Test 20: Duplicate exit submission prevented ─────────────────────────────

class TestDuplicatePrevention:
    def test_second_submit_rejected(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        with pytest.raises(ValueError, match="already submitted"):
            ex.submit_exit(_mock_contract(), _target_trigger(), entry_order_id=42)
        assert ib.placeOrder.call_count == 1

    def test_different_entry_id_allowed(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=43)
        assert ib.placeOrder.call_count == 2


# ── Test 21: No DailyTradeManager mutation ───────────────────────────────────

class TestNoTradeManager:
    def test_no_import(self):
        import inspect
        import trading_lab.live.option_exit_executor as mod
        source = inspect.getsource(mod)
        assert "from trading_lab.live.trade_manager" not in source
        assert "import DailyTradeManager" not in source
        assert "record_trade_result" not in source


# ── Test 22: No WIN/LOSS classification ──────────────────────────────────────

class TestNoWinLoss:
    def test_no_win_loss(self):
        import inspect
        import trading_lab.live.option_exit_executor as mod
        source = inspect.getsource(mod)
        # WIN/LOSS as trade result classification
        assert "TradeResult" not in source


# ── Test 23: No bracket ─────────────────────────────────────────────────────

class TestNoBracket:
    def test_single_order(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        assert ib.placeOrder.call_count == 1
        order = ib.placeOrder.call_args[0][1]
        assert not hasattr(order, "parentId") or getattr(order, "parentId", 0) == 0


# ── Test 24: No option-premium stop/target ───────────────────────────────────

class TestNoPremiumLevels:
    def test_no_premium_fields(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        r = ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        assert not hasattr(r, "option_stop")
        assert not hasattr(r, "option_target")
        assert not hasattr(r, "premium_stop")


# ── Test: Order IDs captured ─────────────────────────────────────────────────

class TestOrderIds:
    def test_ids(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        r = ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        assert r.order_id == 55
        assert r.perm_id == 888

    def test_entry_order_id_preserved(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        r = ex.submit_exit(_mock_contract(), _stop_trigger(), entry_order_id=42)
        assert r.entry_order_id == 42


# ── Test: Option identity preserved ──────────────────────────────────────────

class TestOptionIdentity:
    def test_fields(self):
        ib = _mock_ib()
        ex = OptionExitExecutor(ib)
        r = ex.submit_exit(
            _mock_contract(), _stop_trigger(), entry_order_id=42,
            con_id=123456, right="C", expiration="20260811", strike=585.0,
        )
        assert r.right == "C"
        assert r.expiration == "20260811"
        assert r.strike == 585.0
