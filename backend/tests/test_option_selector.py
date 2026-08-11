"""Tests for option contract selector — pure policy + result schema.

All tests use pure policy functions (no IBKR connection).
IBKR adapter is tested only via structural/mock assertions.
"""

import pytest

from trading_lab.live.option_selector import (
    OptionSelectionResult,
    select_expiration,
    select_strike,
    _pick_chain,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

SAMPLE_EXPIRATIONS = ["20260807", "20260808", "20260811", "20260812",
                      "20260813", "20260815", "20260818", "20260822"]

SAMPLE_STRIKES = [580.0, 581.0, 582.0, 583.0, 584.0, 585.0,
                  586.0, 587.0, 588.0, 589.0, 590.0]


# ── Test 1: LONG intent → CALL ──────────────────────────────────────────────

class TestLongCall:
    def test_call_selects_strike_below(self):
        strike = select_strike("C", 585.20, SAMPLE_STRIKES)
        assert strike == 585.0

    def test_call_right(self):
        """LONG direction uses right='C'."""
        # Policy layer uses 'C'/'P' directly; mapping from direction
        # is done in execution_intent.py (CALL→'C', PUT→'P')
        strike = select_strike("C", 585.20, SAMPLE_STRIKES)
        assert strike < 585.20


# ── Test 2: SHORT intent → PUT ──────────────────────────────────────────────

class TestShortPut:
    def test_put_selects_strike_above(self):
        strike = select_strike("P", 585.20, SAMPLE_STRIKES)
        assert strike == 586.0

    def test_put_right(self):
        strike = select_strike("P", 585.20, SAMPLE_STRIKES)
        assert strike > 585.20


# ── Test 3: 0DTE chosen when present ────────────────────────────────────────

class TestExpirationToday:
    def test_today_chosen(self):
        exp = select_expiration("20260811", SAMPLE_EXPIRATIONS)
        assert exp == "20260811"

    def test_today_preferred_over_future(self):
        exp = select_expiration("20260811",
                                ["20260811", "20260812", "20260815"])
        assert exp == "20260811"


# ── Test 4: Nearest future when today absent ─────────────────────────────────

class TestExpirationFuture:
    def test_nearest_future(self):
        exp = select_expiration("20260810", SAMPLE_EXPIRATIONS)
        assert exp == "20260811"

    def test_skips_past(self):
        exp = select_expiration("20260814",
                                ["20260811", "20260812", "20260815", "20260818"])
        assert exp == "20260815"


# ── Test 5: Expired expirations ignored ──────────────────────────────────────

class TestExpiredIgnored:
    def test_past_only_fails(self):
        with pytest.raises(ValueError, match="No valid expiration"):
            select_expiration("20260820",
                              ["20260811", "20260812", "20260815"])


# ── Test 6: No valid future expiration → explicit failure ────────────────────

class TestNoValidExpiration:
    def test_empty_list(self):
        with pytest.raises(ValueError, match="No expirations"):
            select_expiration("20260811", [])

    def test_all_past(self):
        with pytest.raises(ValueError, match="No valid expiration"):
            select_expiration("20260820", ["20260811", "20260812"])


# ── Test 7: CALL → largest strike below underlying ──────────────────────────

class TestCallStrike:
    def test_largest_below(self):
        strike = select_strike("C", 585.20, SAMPLE_STRIKES)
        assert strike == 585.0

    def test_exact_price_excluded(self):
        """Strike must be strictly below, not equal."""
        strike = select_strike("C", 585.00, SAMPLE_STRIKES)
        assert strike == 584.0

    def test_between_strikes(self):
        strike = select_strike("C", 583.50, SAMPLE_STRIKES)
        assert strike == 583.0


# ── Test 8: PUT → smallest strike above underlying ──────────────────────────

class TestPutStrike:
    def test_smallest_above(self):
        strike = select_strike("P", 585.20, SAMPLE_STRIKES)
        assert strike == 586.0

    def test_exact_price_excluded(self):
        """Strike must be strictly above, not equal."""
        strike = select_strike("P", 585.00, SAMPLE_STRIKES)
        assert strike == 586.0

    def test_between_strikes(self):
        strike = select_strike("P", 583.50, SAMPLE_STRIKES)
        assert strike == 584.0


# ── Test 9: Example 585.20 / [584,585,586,587] ──────────────────────────────

class TestSpecExample:
    def test_call_585(self):
        strike = select_strike("C", 585.20, [584, 585, 586, 587])
        assert strike == 585

    def test_put_586(self):
        strike = select_strike("P", 585.20, [584, 585, 586, 587])
        assert strike == 586


# ── Test 10: same example reversed ──────────────────────────────────────────
# (covered by test 9)


# ── Test 11: No valid ITM strike → explicit failure ─────────────────────────

class TestNoValidStrike:
    def test_call_all_above(self):
        with pytest.raises(ValueError, match="No CALL strike below"):
            select_strike("C", 580.00, [580, 581, 582])

    def test_put_all_below(self):
        with pytest.raises(ValueError, match="No PUT strike above"):
            select_strike("P", 590.00, [580, 585, 590])

    def test_empty_strikes(self):
        with pytest.raises(ValueError, match="No strikes"):
            select_strike("C", 585.0, [])


# ── Test 12: Quantity always 1 ───────────────────────────────────────────────

class TestQuantity:
    def test_result_quantity(self):
        result = OptionSelectionResult(
            underlying_symbol="QQQ",
            underlying_price=585.20,
            right="C",
            expiration="20260811",
            strike=585.0,
            exchange="SMART",
            trading_class="QQQ",
            multiplier="100",
            quantity=1,
        )
        assert result.quantity == 1


# ── Test 13: Policy uses only actual chain strikes ──────────────────────────

class TestActualChainStrikes:
    def test_non_standard_strikes(self):
        """Policy works with any strikes returned by chain."""
        strikes = [582.5, 583.5, 584.5, 585.5]
        strike = select_strike("C", 585.20, strikes)
        assert strike == 584.5

    def test_wide_strikes(self):
        strikes = [570, 575, 580, 585, 590]
        strike = select_strike("C", 583.0, strikes)
        assert strike == 580


# ── Test 14: Policy uses only actual chain expirations ───────────────────────

class TestActualChainExpirations:
    def test_weekly_only(self):
        """Works with weeklies that may not include today."""
        expirations = ["20260815", "20260822", "20260829"]
        exp = select_expiration("20260811", expirations)
        assert exp == "20260815"


# ── Test 15: Deterministic ───────────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_same_output(self):
        e1 = select_expiration("20260811", SAMPLE_EXPIRATIONS)
        e2 = select_expiration("20260811", SAMPLE_EXPIRATIONS)
        assert e1 == e2

        s1 = select_strike("C", 585.20, SAMPLE_STRIKES)
        s2 = select_strike("C", 585.20, SAMPLE_STRIKES)
        assert s1 == s2


# ── Test 16: No delta dependency ─────────────────────────────────────────────

class TestNoDelta:
    def test_no_delta_parameter(self):
        """select_strike has no delta parameter."""
        import inspect
        sig = inspect.signature(select_strike)
        assert "delta" not in sig.parameters


# ── Test 17: No premium dependency ───────────────────────────────────────────

class TestNoPremium:
    def test_no_premium_parameter(self):
        import inspect
        sig = inspect.signature(select_strike)
        assert "premium" not in sig.parameters
        sig_exp = inspect.signature(select_expiration)
        assert "premium" not in sig_exp.parameters


# ── Test 18: No option-premium stop/target ───────────────────────────────────

class TestNoPremiumStopTarget:
    def test_result_has_no_stop_target(self):
        result = OptionSelectionResult(
            underlying_symbol="QQQ",
            underlying_price=585.20,
            right="C",
            expiration="20260811",
            strike=585.0,
            exchange="SMART",
            trading_class="QQQ",
            multiplier="100",
            quantity=1,
        )
        assert not hasattr(result, "option_stop")
        assert not hasattr(result, "option_target")
        assert not hasattr(result, "premium_stop")
        assert not hasattr(result, "premium_target")


# ── Test 19: No order submitted ──────────────────────────────────────────────

class TestNoOrder:
    def test_no_placeOrder_in_module(self):
        import inspect
        import trading_lab.live.option_selector as mod
        source = inspect.getsource(mod)
        assert "placeOrder" not in source


# ── Test 20: Underlying trigger levels unchanged ─────────────────────────────

class TestUnderlyingTriggers:
    def test_result_does_not_modify_triggers(self):
        """OptionSelectionResult preserves underlying price but
        does not contain or modify entry/stop/target triggers."""
        result = OptionSelectionResult(
            underlying_symbol="QQQ",
            underlying_price=585.20,
            right="C",
            expiration="20260811",
            strike=585.0,
            exchange="SMART",
            trading_class="QQQ",
            multiplier="100",
            quantity=1,
        )
        # underlying_price is observational (used for strike selection)
        assert result.underlying_price == 585.20
        # No entry/stop/target modification
        assert not hasattr(result, "entry_price")
        assert not hasattr(result, "stop_price")
        assert not hasattr(result, "target_price")


# ── Test: Invalid right ──────────────────────────────────────────────────────

class TestInvalidRight:
    def test_bad_right(self):
        with pytest.raises(ValueError, match="right must be"):
            select_strike("X", 585.0, [584, 585, 586])


# ── Test: Chain selection ────────────────────────────────────────────────────

class TestPickChain:
    def test_prefers_smart(self):
        from types import SimpleNamespace
        chains = [
            SimpleNamespace(exchange="CBOE", tradingClass="SPY",
                            multiplier="100", expirations=["20260811"],
                            strikes=[585.0]),
            SimpleNamespace(exchange="SMART", tradingClass="SPY",
                            multiplier="100", expirations=["20260811"],
                            strikes=[585.0]),
        ]
        result = _pick_chain(chains, "SMART")
        assert result["exchange"] == "SMART"

    def test_falls_back_to_first(self):
        from types import SimpleNamespace
        chains = [
            SimpleNamespace(exchange="CBOE", tradingClass="SPY",
                            multiplier="100", expirations=["20260811"],
                            strikes=[585.0]),
        ]
        result = _pick_chain(chains, "SMART")
        assert result["exchange"] == "CBOE"

    def test_empty_chains(self):
        assert _pick_chain([]) is None


# ── Test: Bid/ask metadata ───────────────────────────────────────────────────

class TestBidAskMetadata:
    def test_defaults_to_none(self):
        result = OptionSelectionResult(
            underlying_symbol="QQQ",
            underlying_price=585.20,
            right="C",
            expiration="20260811",
            strike=585.0,
            exchange="SMART",
            trading_class="QQQ",
            multiplier="100",
            quantity=1,
        )
        assert result.bid is None
        assert result.ask is None
        assert result.spread is None

    def test_preserves_values(self):
        result = OptionSelectionResult(
            underlying_symbol="QQQ",
            underlying_price=585.20,
            right="C",
            expiration="20260811",
            strike=585.0,
            exchange="SMART",
            trading_class="QQQ",
            multiplier="100",
            quantity=1,
            bid=2.50,
            ask=2.70,
            spread=0.20,
        )
        assert result.bid == 2.50
        assert result.ask == 2.70
        assert result.spread == 0.20
