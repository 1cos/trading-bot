"""Tests for IBKROptionExecutor — option entry submission with mock IB.

All tests use a fake IB object. No real TWS/Gateway connection.
"""

import math
import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from trading_lab.live.option_order_builder import OptionEntryOrderSpec
from trading_lab.live.ibkr_option_executor import (
    IBKROptionExecutor,
    EntrySubmissionResult,
    _validate_spec,
)


# ── Mock helpers ─────────────────────────────────────────────────────────────

def _mock_contract():
    """A fake qualified option contract."""
    c = SimpleNamespace(
        symbol="QQQ", conId=123456, secType="OPT",
        lastTradeDateOrContractMonth="20260811",
        strike=585.0, right="C", exchange="SMART",
        multiplier="100", currency="USD", tradingClass="QQQ",
    )
    return c


def _valid_spec(**overrides) -> OptionEntryOrderSpec:
    defaults = dict(
        action="BUY",
        order_type="LMT",
        quantity=1,
        limit_price=2.70,
        bid=2.50,
        ask=2.70,
        spread=0.20,
        spread_pct=0.074074,
        underlying_symbol="QQQ",
        right="C",
        expiration="20260811",
        strike=585.0,
        exchange="SMART",
        multiplier="100",
        con_id=123456,
        qualified_contract=_mock_contract(),
    )
    defaults.update(overrides)
    return OptionEntryOrderSpec(**defaults)


def _mock_ib():
    """Create a mock IB that returns a plausible Trade on placeOrder."""
    ib = MagicMock()
    mock_order = SimpleNamespace(orderId=42, permId=999)
    mock_status = SimpleNamespace(status="PendingSubmit")
    mock_trade = SimpleNamespace(
        order=mock_order,
        orderStatus=mock_status,
        contract=_mock_contract(),
        fills=[],
        log=[],
    )
    ib.placeOrder.return_value = mock_trade
    return ib


# ── Test 1: Valid entry builds BUY LimitOrder ────────────────────────────────

class TestBuildOrder:
    def test_place_order_called_with_limit(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        spec = _valid_spec()
        executor.submit_entry(spec)

        ib.placeOrder.assert_called_once()
        args = ib.placeOrder.call_args
        contract = args[0][0]
        order = args[0][1]
        assert order.action == "BUY"
        assert order.orderType == "LMT"


# ── Test 2: Quantity = 1 ────────────────────────────────────────────────────

class TestQuantity:
    def test_total_quantity_one(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        executor.submit_entry(_valid_spec())
        order = ib.placeOrder.call_args[0][1]
        assert order.totalQuantity == 1


# ── Test 3: Exact limit price preserved ──────────────────────────────────────

class TestLimitPrice:
    def test_exact_price(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        executor.submit_entry(_valid_spec(limit_price=3.45))
        order = ib.placeOrder.call_args[0][1]
        assert order.lmtPrice == 3.45


# ── Test 4: Qualified contract passed to placeOrder ──────────────────────────

class TestContract:
    def test_exact_contract(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        contract = _mock_contract()
        spec = _valid_spec(qualified_contract=contract)
        executor.submit_entry(spec)
        passed_contract = ib.placeOrder.call_args[0][0]
        assert passed_contract is contract


# ── Test 5: placeOrder called exactly once ───────────────────────────────────

class TestSingleCall:
    def test_one_call(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        executor.submit_entry(_valid_spec())
        assert ib.placeOrder.call_count == 1


# ── Test 6: No bracket/children ─────────────────────────────────────────────

class TestNoBracket:
    def test_no_child_orders(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        executor.submit_entry(_valid_spec())
        # Only one placeOrder call, no additional child orders
        assert ib.placeOrder.call_count == 1
        order = ib.placeOrder.call_args[0][1]
        assert not hasattr(order, "parentId") or getattr(order, "parentId", 0) == 0


# ── Test 7: Result preserves option identity ─────────────────────────────────

class TestResultIdentity:
    def test_fields(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        result = executor.submit_entry(_valid_spec())
        assert result.underlying_symbol == "QQQ"
        assert result.con_id == 123456
        assert result.right == "C"
        assert result.expiration == "20260811"
        assert result.strike == 585.0
        assert result.quantity == 1
        assert result.limit_price == 2.70


# ── Test 8: Broker order ID captured ─────────────────────────────────────────

class TestOrderId:
    def test_order_id(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        result = executor.submit_entry(_valid_spec())
        assert result.order_id == 42


# ── Test 9: Broker status captured ───────────────────────────────────────────

class TestBrokerStatus:
    def test_status(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        result = executor.submit_entry(_valid_spec())
        assert result.status == "PendingSubmit"


# ── Test 10: Submission ≠ fill ───────────────────────────────────────────────

class TestNotFill:
    def test_status_not_filled(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        result = executor.submit_entry(_valid_spec())
        assert result.status != "Filled"

    def test_trade_object_available(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        result = executor.submit_entry(_valid_spec())
        assert result.trade is not None


# ── Test 11: Invalid action rejected ─────────────────────────────────────────

class TestInvalidAction:
    def test_sell(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        with pytest.raises(ValueError, match="Only BUY"):
            executor.submit_entry(_valid_spec(action="SELL"))
        ib.placeOrder.assert_not_called()


# ── Test 12: Invalid order type rejected ─────────────────────────────────────

class TestInvalidOrderType:
    def test_market(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        with pytest.raises(ValueError, match="Only LMT"):
            executor.submit_entry(_valid_spec(order_type="MKT"))
        ib.placeOrder.assert_not_called()


# ── Test 13: Invalid quantity rejected ───────────────────────────────────────

class TestInvalidQuantity:
    def test_two(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        with pytest.raises(ValueError, match="quantity=1"):
            executor.submit_entry(_valid_spec(quantity=2))
        ib.placeOrder.assert_not_called()


# ── Test 14: Invalid limit price rejected ────────────────────────────────────

class TestInvalidLimitPrice:
    def test_zero(self):
        with pytest.raises(ValueError, match="limit_price.*> 0"):
            _validate_spec(_valid_spec(limit_price=0.0))

    def test_negative(self):
        with pytest.raises(ValueError, match="limit_price.*> 0"):
            _validate_spec(_valid_spec(limit_price=-1.0))

    def test_nan(self):
        with pytest.raises(ValueError, match="limit_price.*finite"):
            _validate_spec(_valid_spec(limit_price=float("nan")))

    def test_inf(self):
        with pytest.raises(ValueError, match="limit_price.*finite"):
            _validate_spec(_valid_spec(limit_price=float("inf")))


# ── Test 15: Missing qualified contract rejected ─────────────────────────────

class TestMissingContract:
    def test_none(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        with pytest.raises(ValueError, match="qualified_contract"):
            executor.submit_entry(_valid_spec(qualified_contract=None))
        ib.placeOrder.assert_not_called()


# ── Test 16: Broker exception propagated ─────────────────────────────────────

class TestBrokerException:
    def test_propagates(self):
        ib = _mock_ib()
        ib.placeOrder.side_effect = RuntimeError("TWS disconnected")
        executor = IBKROptionExecutor(ib)
        with pytest.raises(RuntimeError, match="TWS disconnected"):
            executor.submit_entry(_valid_spec())


# ── Test 17: No automatic retry ─────────────────────────────────────────────

class TestNoRetry:
    def test_single_attempt(self):
        ib = _mock_ib()
        ib.placeOrder.side_effect = RuntimeError("fail")
        executor = IBKROptionExecutor(ib)
        with pytest.raises(RuntimeError):
            executor.submit_entry(_valid_spec())
        assert ib.placeOrder.call_count == 1


# ── Test 18: No connection logic ─────────────────────────────────────────────

class TestNoConnection:
    def test_no_connect_call(self):
        import inspect
        import trading_lab.live.ibkr_option_executor as mod
        source = inspect.getsource(mod)
        assert ".connect(" not in source

    def test_no_host_port(self):
        import inspect
        import trading_lab.live.ibkr_option_executor as mod
        source = inspect.getsource(mod)
        # No connection parameters in code
        assert "host=" not in source
        assert "port=" not in source
        assert "clientId=" not in source


# ── Test 19: No option-chain request ─────────────────────────────────────────

class TestNoChainRequest:
    def test_no_reqSecDef(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        executor.submit_entry(_valid_spec())
        ib.reqSecDefOptParams.assert_not_called()


# ── Test 20: No requalification ──────────────────────────────────────────────

class TestNoRequalification:
    def test_no_qualifyContracts(self):
        ib = _mock_ib()
        executor = IBKROptionExecutor(ib)
        executor.submit_entry(_valid_spec())
        ib.qualifyContracts.assert_not_called()


# ── Test 21: No DailyTradeManager mutation ───────────────────────────────────

class TestNoTradeManager:
    def test_no_import(self):
        import inspect
        import trading_lab.live.ibkr_option_executor as mod
        source = inspect.getsource(mod)
        assert "trade_manager" not in source
        assert "DailyTradeManager" not in source
        assert "record_trade" not in source


# ── Test 22: Deterministic translation ───────────────────────────────────────

class TestDeterministic:
    def test_same_spec_same_order(self):
        ib1 = _mock_ib()
        ib2 = _mock_ib()
        spec = _valid_spec()
        IBKROptionExecutor(ib1).submit_entry(spec)
        IBKROptionExecutor(ib2).submit_entry(spec)
        o1 = ib1.placeOrder.call_args[0][1]
        o2 = ib2.placeOrder.call_args[0][1]
        assert o1.action == o2.action
        assert o1.totalQuantity == o2.totalQuantity
        assert o1.lmtPrice == o2.lmtPrice
        assert o1.orderType == o2.orderType
