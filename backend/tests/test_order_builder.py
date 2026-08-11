"""Tests for bracket order builder — broker-agnostic order spec for MaxBot v0.1.

Uses real TradePlan instances from the existing contract.
"""

import pytest
from decimal import Decimal

from trading_lab.contracts.distances import AbsoluteTickDistance
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.contracts.trade_plan import EntryModel, TradePlan
from trading_lab.live.order_builder import (
    Action,
    BracketOrderSpec,
    LegRole,
    OrderLegSpec,
    OrderType,
    build_bracket_order,
)


# ── TradePlan fixtures using real contracts ──────────────────────────────────

TS = "0.01"  # SPY tick size


def _long_plan() -> TradePlan:
    """LONG plan: entry=101.20, stop=100.80, risk=40 ticks, 2R=102.00."""
    return TradePlan(
        schema_version="TradePlan/v1",
        entry_model=EntryModel.CONFIRMATION_CLOSE,
        entry_buffer_ticks=0,
        stop_buffer_ticks=0,
        tick_size=TS,
        entry_price=PriceTicks(ticks=10120, tick_size=TS),
        stop_price=PriceTicks(ticks=10080, tick_size=TS),
        risk=AbsoluteTickDistance(ticks=40, tick_size=TS),
        r2_price=PriceTicks(ticks=10200, tick_size=TS),
        r3_price=PriceTicks(ticks=10240, tick_size=TS),
        r4_price=PriceTicks(ticks=10280, tick_size=TS),
    )


def _short_plan() -> TradePlan:
    """SHORT plan: entry=99.80, stop=100.20, risk=40 ticks, 2R=99.00."""
    return TradePlan(
        schema_version="TradePlan/v1",
        entry_model=EntryModel.CONFIRMATION_CLOSE,
        entry_buffer_ticks=0,
        stop_buffer_ticks=0,
        tick_size=TS,
        entry_price=PriceTicks(ticks=9980, tick_size=TS),
        stop_price=PriceTicks(ticks=10020, tick_size=TS),
        risk=AbsoluteTickDistance(ticks=40, tick_size=TS),
        r2_price=PriceTicks(ticks=9900, tick_size=TS),
        r3_price=PriceTicks(ticks=9860, tick_size=TS),
        r4_price=PriceTicks(ticks=9820, tick_size=TS),
    )


# ── Test 1: Valid LONG ───────────────────────────────────────────────────────

class TestLongBracket:
    def test_builds_successfully(self):
        spec = build_bracket_order(_long_plan(), "SPY", 100, "LONG")
        assert isinstance(spec, BracketOrderSpec)
        assert spec.direction == "LONG"
        assert spec.symbol == "SPY"
        assert spec.quantity == 100


# ── Test 2: Valid SHORT ──────────────────────────────────────────────────────

class TestShortBracket:
    def test_builds_successfully(self):
        spec = build_bracket_order(_short_plan(), "SPY", 100, "SHORT")
        assert isinstance(spec, BracketOrderSpec)
        assert spec.direction == "SHORT"


# ── Test 3: Correct entry action ─────────────────────────────────────────────

class TestEntryAction:
    def test_long_entry_is_buy(self):
        spec = build_bracket_order(_long_plan(), "SPY", 1, "LONG")
        assert spec.entry.action == Action.BUY

    def test_short_entry_is_sell(self):
        spec = build_bracket_order(_short_plan(), "SPY", 1, "SHORT")
        assert spec.entry.action == Action.SELL


# ── Test 4: Correct child actions ────────────────────────────────────────────

class TestChildActions:
    def test_long_children_sell(self):
        spec = build_bracket_order(_long_plan(), "SPY", 1, "LONG")
        assert spec.take_profit.action == Action.SELL
        assert spec.stop_loss.action == Action.SELL

    def test_short_children_buy(self):
        spec = build_bracket_order(_short_plan(), "SPY", 1, "SHORT")
        assert spec.take_profit.action == Action.BUY
        assert spec.stop_loss.action == Action.BUY


# ── Test 5: Correct order types ──────────────────────────────────────────────

class TestOrderTypes:
    def test_entry_is_limit(self):
        spec = build_bracket_order(_long_plan(), "SPY", 1, "LONG")
        assert spec.entry.order_type == OrderType.LMT

    def test_take_profit_is_limit(self):
        spec = build_bracket_order(_long_plan(), "SPY", 1, "LONG")
        assert spec.take_profit.order_type == OrderType.LMT

    def test_stop_loss_is_stop(self):
        spec = build_bracket_order(_long_plan(), "SPY", 1, "LONG")
        assert spec.stop_loss.order_type == OrderType.STP


# ── Test 6: Exact strategy prices preserved ──────────────────────────────────

class TestPricePreservation:
    def test_long_prices(self):
        spec = build_bracket_order(_long_plan(), "SPY", 1, "LONG")
        assert spec.entry.price == Decimal("101.20")
        assert spec.stop_loss.price == Decimal("100.80")
        assert spec.take_profit.price == Decimal("102.00")

    def test_short_prices(self):
        spec = build_bracket_order(_short_plan(), "SPY", 1, "SHORT")
        assert spec.entry.price == Decimal("99.80")
        assert spec.stop_loss.price == Decimal("100.20")
        assert spec.take_profit.price == Decimal("99.00")

    def test_r3_target(self):
        spec = build_bracket_order(_long_plan(), "SPY", 1, "LONG", exit_target_r=3)
        assert spec.take_profit.price == Decimal("102.40")

    def test_r4_target(self):
        spec = build_bracket_order(_long_plan(), "SPY", 1, "LONG", exit_target_r=4)
        assert spec.take_profit.price == Decimal("102.80")


# ── Test 7: Quantity preserved ───────────────────────────────────────────────

class TestQuantity:
    def test_all_legs_same_quantity(self):
        spec = build_bracket_order(_long_plan(), "SPY", 50, "LONG")
        assert spec.entry.quantity == 50
        assert spec.take_profit.quantity == 50
        assert spec.stop_loss.quantity == 50
        assert spec.quantity == 50


# ── Test 8: Invalid LONG geometry rejected ───────────────────────────────────

class TestInvalidLongGeometry:
    def test_stop_above_entry(self):
        plan = TradePlan(
            schema_version="TradePlan/v1",
            entry_model=EntryModel.CONFIRMATION_CLOSE,
            entry_buffer_ticks=0,
            stop_buffer_ticks=0,
            tick_size=TS,
            entry_price=PriceTicks(ticks=10000, tick_size=TS),
            stop_price=PriceTicks(ticks=10100, tick_size=TS),  # stop > entry
            risk=AbsoluteTickDistance(ticks=100, tick_size=TS),
            r2_price=PriceTicks(ticks=10200, tick_size=TS),
            r3_price=PriceTicks(ticks=10300, tick_size=TS),
            r4_price=PriceTicks(ticks=10400, tick_size=TS),
        )
        with pytest.raises(ValueError, match="LONG bracket requires"):
            build_bracket_order(plan, "SPY", 1, "LONG")


# ── Test 9: Invalid SHORT geometry rejected ──────────────────────────────────

class TestInvalidShortGeometry:
    def test_stop_below_entry(self):
        plan = TradePlan(
            schema_version="TradePlan/v1",
            entry_model=EntryModel.CONFIRMATION_CLOSE,
            entry_buffer_ticks=0,
            stop_buffer_ticks=0,
            tick_size=TS,
            entry_price=PriceTicks(ticks=10000, tick_size=TS),
            stop_price=PriceTicks(ticks=9900, tick_size=TS),  # stop < entry for SHORT is wrong
            risk=AbsoluteTickDistance(ticks=100, tick_size=TS),
            r2_price=PriceTicks(ticks=9800, tick_size=TS),
            r3_price=PriceTicks(ticks=9700, tick_size=TS),
            r4_price=PriceTicks(ticks=9600, tick_size=TS),
        )
        with pytest.raises(ValueError, match="SHORT bracket requires"):
            build_bracket_order(plan, "SPY", 1, "SHORT")


# ── Test 10: Zero/negative quantity ──────────────────────────────────────────

class TestInvalidQuantity:
    def test_zero(self):
        with pytest.raises(ValueError, match="quantity"):
            build_bracket_order(_long_plan(), "SPY", 0, "LONG")

    def test_negative(self):
        with pytest.raises(ValueError, match="quantity"):
            build_bracket_order(_long_plan(), "SPY", -1, "LONG")

    def test_bool_rejected(self):
        with pytest.raises(TypeError, match="quantity"):
            build_bracket_order(_long_plan(), "SPY", True, "LONG")


# ── Test 11: Non-finite price rejected ───────────────────────────────────────

class TestNonFinitePrice:
    def test_nan_entry(self):
        """TradePlan with NaN ticks would be caught upstream, but verify
        the order builder also guards via Decimal conversion."""
        # PriceTicks requires int ticks, so NaN can't sneak through the
        # normal path. This test confirms the builder's own validation
        # would catch it if somehow a non-finite Decimal appeared.
        from unittest.mock import MagicMock
        mock_plan = MagicMock()
        mock_plan.entry_price.to_price.return_value = Decimal("NaN")
        mock_plan.stop_price.to_price.return_value = Decimal("100.00")
        mock_plan.r2_price.to_price.return_value = Decimal("102.00")
        with pytest.raises(ValueError, match="finite"):
            build_bracket_order(mock_plan, "SPY", 1, "LONG")


# ── Test 12: Deterministic ───────────────────────────────────────────────────

class TestDeterminism:
    def test_identical_input_identical_output(self):
        s1 = build_bracket_order(_long_plan(), "SPY", 10, "LONG")
        s2 = build_bracket_order(_long_plan(), "SPY", 10, "LONG")
        assert s1 == s2


# ── Test 13: No IBKR connection ──────────────────────────────────────────────

class TestNoBroker:
    def test_no_ib_import(self):
        import inspect
        import trading_lab.live.order_builder as mod
        source = inspect.getsource(mod)
        assert "ib_insync" not in source
        assert "from ib_insync" not in source


# ── Test: Transmit flags ─────────────────────────────────────────────────────

class TestTransmitFlags:
    def test_bracket_transmit_sequence(self):
        spec = build_bracket_order(_long_plan(), "SPY", 1, "LONG")
        assert spec.entry.transmit is False
        assert spec.take_profit.transmit is False
        assert spec.stop_loss.transmit is True


# ── Test: Leg roles ──────────────────────────────────────────────────────────

class TestLegRoles:
    def test_roles(self):
        spec = build_bracket_order(_long_plan(), "SPY", 1, "LONG")
        assert spec.entry.role == LegRole.ENTRY
        assert spec.take_profit.role == LegRole.TAKE_PROFIT
        assert spec.stop_loss.role == LegRole.STOP_LOSS


# ── Test: Invalid direction ──────────────────────────────────────────────────

class TestInvalidDirection:
    def test_bad_direction(self):
        with pytest.raises(ValueError, match="direction"):
            build_bracket_order(_long_plan(), "SPY", 1, "BOTH")

    def test_invalid_exit_target_r(self):
        with pytest.raises(ValueError, match="exit_target_r"):
            build_bracket_order(_long_plan(), "SPY", 1, "LONG", exit_target_r=5)
