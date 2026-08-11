"""Tests for option entry order builder — BUY LMT spec for MaxBot v0.1.

Uses real OptionSelectionResult objects.
"""

import math
import pytest

from trading_lab.live.option_selector import OptionSelectionResult
from trading_lab.live.option_order_builder import (
    OptionEntryOrderSpec,
    build_option_entry_order,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _call_selection(**overrides) -> OptionSelectionResult:
    defaults = dict(
        underlying_symbol="QQQ",
        underlying_price=585.20,
        right="C",
        expiration="20260811",
        strike=585.0,
        exchange="SMART",
        trading_class="QQQ",
        multiplier="100",
        quantity=1,
        con_id=123456789,
        qualified_contract=object(),  # sentinel
        bid=2.50,
        ask=2.70,
        spread=0.20,
    )
    defaults.update(overrides)
    return OptionSelectionResult(**defaults)


def _put_selection(**overrides) -> OptionSelectionResult:
    defaults = dict(
        underlying_symbol="QQQ",
        underlying_price=585.20,
        right="P",
        expiration="20260811",
        strike=586.0,
        exchange="SMART",
        trading_class="QQQ",
        multiplier="100",
        quantity=1,
        con_id=987654321,
        qualified_contract=object(),
        bid=1.80,
        ask=2.00,
        spread=0.20,
    )
    defaults.update(overrides)
    return OptionSelectionResult(**defaults)


# ── Test 1: CALL → BUY LMT ──────────────────────────────────────────────────

class TestCallBuyLmt:
    def test_action_buy(self):
        spec = build_option_entry_order(_call_selection())
        assert spec.action == "BUY"

    def test_order_type_lmt(self):
        spec = build_option_entry_order(_call_selection())
        assert spec.order_type == "LMT"


# ── Test 2: PUT → BUY LMT ───────────────────────────────────────────────────

class TestPutBuyLmt:
    def test_action_buy(self):
        spec = build_option_entry_order(_put_selection())
        assert spec.action == "BUY"

    def test_order_type_lmt(self):
        spec = build_option_entry_order(_put_selection())
        assert spec.order_type == "LMT"


# ── Test 3: Quantity = 1 ────────────────────────────────────────────────────

class TestQuantity:
    def test_always_one(self):
        spec = build_option_entry_order(_call_selection())
        assert spec.quantity == 1


# ── Test 4: Limit price = ask ────────────────────────────────────────────────

class TestLimitPrice:
    def test_equals_ask(self):
        spec = build_option_entry_order(_call_selection(ask=2.70))
        assert spec.limit_price == 2.70

    def test_different_ask(self):
        spec = build_option_entry_order(_call_selection(bid=3.00, ask=3.20))
        assert spec.limit_price == 3.20


# ── Test 5: Bid preserved ───────────────────────────────────────────────────

class TestBidPreserved:
    def test_bid(self):
        spec = build_option_entry_order(_call_selection(bid=2.50))
        assert spec.bid == 2.50


# ── Test 6: Ask preserved ───────────────────────────────────────────────────

class TestAskPreserved:
    def test_ask(self):
        spec = build_option_entry_order(_call_selection(ask=2.70))
        assert spec.ask == 2.70


# ── Test 7: Spread calculated ────────────────────────────────────────────────

class TestSpread:
    def test_spread(self):
        spec = build_option_entry_order(_call_selection(bid=2.50, ask=2.70))
        assert spec.spread == pytest.approx(0.20)

    def test_tight_spread(self):
        spec = build_option_entry_order(_call_selection(bid=2.50, ask=2.55))
        assert spec.spread == pytest.approx(0.05)


# ── Test 8: Spread percentage ────────────────────────────────────────────────

class TestSpreadPct:
    def test_spread_pct(self):
        spec = build_option_entry_order(_call_selection(bid=2.50, ask=2.70))
        expected = round((2.70 - 2.50) / 2.70, 6)
        assert spec.spread_pct == pytest.approx(expected)

    def test_zero_spread(self):
        spec = build_option_entry_order(_call_selection(bid=2.50, ask=2.50))
        assert spec.spread_pct == 0.0


# ── Test 9: No spread threshold ──────────────────────────────────────────────

class TestNoThreshold:
    def test_wide_spread_accepted(self):
        """Even a very wide spread is accepted — no threshold in T8."""
        spec = build_option_entry_order(_call_selection(bid=1.00, ask=5.00))
        assert spec.limit_price == 5.00
        assert spec.spread == pytest.approx(4.00)


# ── Test 10: Missing ask rejected ────────────────────────────────────────────

class TestMissingAsk:
    def test_none_ask(self):
        with pytest.raises(ValueError, match="ask.*not available"):
            build_option_entry_order(_call_selection(ask=None))


# ── Test 11: Zero ask rejected ───────────────────────────────────────────────

class TestZeroAsk:
    def test_zero(self):
        with pytest.raises(ValueError, match="ask.*must be > 0"):
            build_option_entry_order(_call_selection(ask=0.0))


# ── Test 12: Negative ask rejected ───────────────────────────────────────────

class TestNegativeAsk:
    def test_negative(self):
        with pytest.raises(ValueError, match="ask.*must be > 0"):
            build_option_entry_order(_call_selection(ask=-1.0))


# ── Test 13: NaN/inf ask rejected ────────────────────────────────────────────

class TestNonFiniteAsk:
    def test_nan(self):
        with pytest.raises(ValueError, match="ask.*finite"):
            build_option_entry_order(_call_selection(ask=float("nan")))

    def test_inf(self):
        with pytest.raises(ValueError, match="ask.*finite"):
            build_option_entry_order(_call_selection(ask=float("inf")))


# ── Test 14: Invalid bid/ask geometry ────────────────────────────────────────

class TestInvalidGeometry:
    def test_ask_below_bid(self):
        with pytest.raises(ValueError, match="ask.*< bid"):
            build_option_entry_order(_call_selection(bid=3.00, ask=2.50))

    def test_missing_bid(self):
        with pytest.raises(ValueError, match="bid.*not available"):
            build_option_entry_order(_call_selection(bid=None))

    def test_zero_bid(self):
        with pytest.raises(ValueError, match="bid.*must be > 0"):
            build_option_entry_order(_call_selection(bid=0.0))


# ── Test 15: Contract identity preserved ─────────────────────────────────────

class TestContractIdentity:
    def test_all_fields(self):
        sel = _call_selection()
        spec = build_option_entry_order(sel)
        assert spec.underlying_symbol == "QQQ"
        assert spec.right == "C"
        assert spec.expiration == "20260811"
        assert spec.strike == 585.0
        assert spec.exchange == "SMART"
        assert spec.multiplier == "100"


# ── Test 16: conId preserved ─────────────────────────────────────────────────

class TestConId:
    def test_preserved(self):
        sel = _call_selection(con_id=123456789)
        spec = build_option_entry_order(sel)
        assert spec.con_id == 123456789

    def test_none_preserved(self):
        sel = _call_selection(con_id=None)
        spec = build_option_entry_order(sel)
        assert spec.con_id is None


# ── Test 17: No option stop ──────────────────────────────────────────────────

class TestNoOptionStop:
    def test_no_stop(self):
        spec = build_option_entry_order(_call_selection())
        assert not hasattr(spec, "stop_price")
        assert not hasattr(spec, "option_stop")
        assert not hasattr(spec, "premium_stop")


# ── Test 18: No option target ────────────────────────────────────────────────

class TestNoOptionTarget:
    def test_no_target(self):
        spec = build_option_entry_order(_call_selection())
        assert not hasattr(spec, "target_price")
        assert not hasattr(spec, "option_target")
        assert not hasattr(spec, "premium_target")


# ── Test 19: No bracket ─────────────────────────────────────────────────────

class TestNoBracket:
    def test_no_bracket_fields(self):
        spec = build_option_entry_order(_call_selection())
        assert not hasattr(spec, "take_profit")
        assert not hasattr(spec, "stop_loss")
        assert not hasattr(spec, "children")


# ── Test 20: No placeOrder ───────────────────────────────────────────────────

class TestNoPlaceOrder:
    def test_no_broker_call(self):
        import inspect
        import trading_lab.live.option_order_builder as mod
        source = inspect.getsource(mod)
        assert "placeOrder" not in source
        assert "from ib_insync" not in source
        assert "import ib_insync" not in source


# ── Test 21: No MARKET order ────────────────────────────────────────────────

class TestNoMarketOrder:
    def test_never_market(self):
        spec = build_option_entry_order(_call_selection())
        assert spec.order_type == "LMT"
        assert spec.order_type != "MKT"


# ── Test 22: Deterministic ──────────────────────────────────────────────────

class TestDeterminism:
    def test_identical_input_identical_output(self):
        sel = _call_selection()
        s1 = build_option_entry_order(sel)
        s2 = build_option_entry_order(sel)
        assert s1.limit_price == s2.limit_price
        assert s1.bid == s2.bid
        assert s1.ask == s2.ask
        assert s1.spread == s2.spread
        assert s1.spread_pct == s2.spread_pct
        assert s1.action == s2.action
        assert s1.order_type == s2.order_type
        assert s1.quantity == s2.quantity
        assert s1.strike == s2.strike
        assert s1.expiration == s2.expiration
        assert s1.right == s2.right


# ── Test: Qualified contract preserved ───────────────────────────────────────

class TestQualifiedContract:
    def test_preserved(self):
        sentinel = object()
        sel = _call_selection(qualified_contract=sentinel)
        spec = build_option_entry_order(sel)
        assert spec.qualified_contract is sentinel
