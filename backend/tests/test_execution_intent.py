"""Tests for OptionExecutionIntent — options execution architecture.

Proves:
  1.  LONG signal → CALL
  2.  SHORT signal → PUT
  3.  execution_type is OPTION
  4.  underlying symbol preserved
  5.  underlying entry price preserved
  6.  underlying stop price preserved
  7.  underlying target price preserved
  8.  prices are explicitly underlying trigger levels
  9.  no option strike selected
  10. no expiration selected
  11. no option premium invented
  12. no IBKR connection
  13. no equity order generated from option intent
  14. deterministic output
"""

import pytest
from decimal import Decimal

from trading_lab.contracts.distances import AbsoluteTickDistance
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.contracts.trade_plan import EntryModel, TradePlan
from trading_lab.live.execution_intent import (
    ExecutionInstrumentType,
    OptionAction,
    OptionExecutionIntent,
    OptionRight,
    UnderlyingTriggerLevels,
    build_option_execution_intent,
)


# ── TradePlan fixtures ───────────────────────────────────────────────────────

TS = "0.01"


def _long_plan() -> TradePlan:
    """LONG: entry=585.20, stop=584.70, 2R=586.20."""
    return TradePlan(
        schema_version="TradePlan/v1",
        entry_model=EntryModel.CONFIRMATION_CLOSE,
        entry_buffer_ticks=0,
        stop_buffer_ticks=0,
        tick_size=TS,
        entry_price=PriceTicks(ticks=58520, tick_size=TS),
        stop_price=PriceTicks(ticks=58470, tick_size=TS),
        risk=AbsoluteTickDistance(ticks=50, tick_size=TS),
        r2_price=PriceTicks(ticks=58620, tick_size=TS),
        r3_price=PriceTicks(ticks=58670, tick_size=TS),
        r4_price=PriceTicks(ticks=58720, tick_size=TS),
    )


def _short_plan() -> TradePlan:
    """SHORT: entry=584.80, stop=585.30, 2R=583.80."""
    return TradePlan(
        schema_version="TradePlan/v1",
        entry_model=EntryModel.CONFIRMATION_CLOSE,
        entry_buffer_ticks=0,
        stop_buffer_ticks=0,
        tick_size=TS,
        entry_price=PriceTicks(ticks=58480, tick_size=TS),
        stop_price=PriceTicks(ticks=58530, tick_size=TS),
        risk=AbsoluteTickDistance(ticks=50, tick_size=TS),
        r2_price=PriceTicks(ticks=58380, tick_size=TS),
        r3_price=PriceTicks(ticks=58330, tick_size=TS),
        r4_price=PriceTicks(ticks=58280, tick_size=TS),
    )


# ── Test 1: LONG → CALL ─────────────────────────────────────────────────────

class TestLongCall:
    def test_option_right_is_call(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert intent.option_right == OptionRight.CALL

    def test_action_is_buy(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert intent.option_action == OptionAction.BUY


# ── Test 2: SHORT → PUT ─────────────────────────────────────────────────────

class TestShortPut:
    def test_option_right_is_put(self):
        intent = build_option_execution_intent(_short_plan(), "QQQ", "SHORT")
        assert intent.option_right == OptionRight.PUT

    def test_action_is_buy(self):
        intent = build_option_execution_intent(_short_plan(), "QQQ", "SHORT")
        assert intent.option_action == OptionAction.BUY


# ── Test 3: Execution type is OPTION ─────────────────────────────────────────

class TestExecutionType:
    def test_is_option(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert intent.execution_type == ExecutionInstrumentType.OPTION


# ── Test 4: Underlying symbol preserved ──────────────────────────────────────

class TestUnderlyingSymbol:
    def test_qqq(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert intent.underlying_symbol == "QQQ"

    def test_spy(self):
        intent = build_option_execution_intent(_long_plan(), "SPY", "LONG")
        assert intent.underlying_symbol == "SPY"

    def test_nvda(self):
        intent = build_option_execution_intent(_long_plan(), "NVDA", "LONG")
        assert intent.underlying_symbol == "NVDA"


# ── Test 5–7: Underlying prices preserved ────────────────────────────────────

class TestUnderlyingPrices:
    def test_entry_price(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert intent.underlying_triggers.entry_price == Decimal("585.20")

    def test_stop_price(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert intent.underlying_triggers.stop_price == Decimal("584.70")

    def test_target_price(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert intent.underlying_triggers.target_price == Decimal("586.20")

    def test_short_prices(self):
        intent = build_option_execution_intent(_short_plan(), "QQQ", "SHORT")
        assert intent.underlying_triggers.entry_price == Decimal("584.80")
        assert intent.underlying_triggers.stop_price == Decimal("585.30")
        assert intent.underlying_triggers.target_price == Decimal("583.80")

    def test_r3_target(self):
        intent = build_option_execution_intent(
            _long_plan(), "QQQ", "LONG", exit_target_r=3
        )
        assert intent.underlying_triggers.target_price == Decimal("586.70")


# ── Test 8: Prices are trigger levels, not premiums ──────────────────────────

class TestTriggerLevels:
    def test_triggers_are_underlying_type(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert isinstance(intent.underlying_triggers, UnderlyingTriggerLevels)

    def test_trade_plan_preserved(self):
        plan = _long_plan()
        intent = build_option_execution_intent(plan, "QQQ", "LONG")
        assert intent.trade_plan is plan


# ── Test 9: No strike selected ───────────────────────────────────────────────

class TestNoStrike:
    def test_no_strike_field(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert not hasattr(intent, "strike")
        assert not hasattr(intent, "strike_price")


# ── Test 10: No expiration selected ──────────────────────────────────────────

class TestNoExpiration:
    def test_no_expiration_field(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert not hasattr(intent, "expiration")
        assert not hasattr(intent, "expiry")


# ── Test 11: No premium invented ─────────────────────────────────────────────

class TestNoPremium:
    def test_no_premium_field(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert not hasattr(intent, "premium")
        assert not hasattr(intent, "option_price")


# ── Test 12: No IBKR connection ──────────────────────────────────────────────

class TestNoBroker:
    def test_no_ib_import(self):
        import inspect
        import trading_lab.live.execution_intent as mod
        source = inspect.getsource(mod)
        assert "ib_insync" not in source


# ── Test 13: No equity order from option intent ──────────────────────────────

class TestNoEquityOrder:
    def test_intent_does_not_contain_bracket_order(self):
        """OptionExecutionIntent has no BracketOrderSpec."""
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert not hasattr(intent, "entry")
        assert not hasattr(intent, "take_profit")
        assert not hasattr(intent, "stop_loss")

    def test_execution_type_not_equity(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert intent.execution_type != "EQUITY"
        assert intent.execution_type == ExecutionInstrumentType.OPTION


# ── Test 14: Deterministic ───────────────────────────────────────────────────

class TestDeterminism:
    def test_identical_output(self):
        i1 = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        i2 = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert i1.underlying_triggers == i2.underlying_triggers
        assert i1.option_right == i2.option_right
        assert i1.direction == i2.direction
        assert i1.underlying_symbol == i2.underlying_symbol


# ── Test: Invalid direction ──────────────────────────────────────────────────

class TestInvalidDirection:
    def test_bad_direction(self):
        with pytest.raises(ValueError, match="direction"):
            build_option_execution_intent(_long_plan(), "QQQ", "BOTH")


# ── Test: Detection result preserved ─────────────────────────────────────────

class TestDetectionResult:
    def test_none_by_default(self):
        intent = build_option_execution_intent(_long_plan(), "QQQ", "LONG")
        assert intent.detection_result is None

    def test_preserved_when_provided(self):
        sentinel = {"status": "VALID", "test": True}
        intent = build_option_execution_intent(
            _long_plan(), "QQQ", "LONG", detection_result=sentinel
        )
        assert intent.detection_result is sentinel
