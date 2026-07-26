"""Tests for canonical tick-arithmetic utilities.

Parity vectors are derived from the authoritative JavaScript functions:
  - priceToTicks   (bdrr_engine.js:99-104)
  - ticksToPoints  (bdrr_engine.js:112-116)
  - decimalsOf     (bdrr_engine.js:106-110)

Verified by running the JavaScript functions directly via Node.js and
recording input/output pairs.  See test docstrings for exact source
references.
"""

import math

import pytest

from trading_lab.tick_arithmetic import (
    decimals_of,
    price_to_ticks,
    ticks_to_points,
)


# ═══════════════════════════════════════════════════════════════════════════════
# price_to_ticks — JS parity vectors
# ═══════════════════════════════════════════════════════════════════════════════


class TestPriceToTicksNormal:
    """Normal cases verified against JS priceToTicks in bdrr_engine.js."""

    def test_spy_level(self):
        """priceToTicks(750.44, 0.01) = 75044 (oracle SPY level price)."""
        assert price_to_ticks(750.44, 0.01) == 75044

    def test_integer_price(self):
        """priceToTicks(101.00, 0.01) = 10100 (test_bdrr_stage1_stage2.js:209)."""
        assert price_to_ticks(101.00, 0.01) == 10100

    def test_round_hundred(self):
        """priceToTicks(100.00, 0.01) = 10000 (test_bdrr_stage1_stage2.js:210)."""
        assert price_to_ticks(100.00, 0.01) == 10000

    def test_quarter_tick(self):
        assert price_to_ticks(100.00, 0.25) == 400

    def test_whole_tick(self):
        assert price_to_ticks(5.0, 1.0) == 5


class TestPriceToTicksZero:
    def test_zero_price(self):
        """priceToTicks(0, 0.01) = 0."""
        assert price_to_ticks(0, 0.01) == 0

    def test_zero_float(self):
        assert price_to_ticks(0.0, 0.01) == 0


class TestPriceToTicksNegative:
    def test_negative_price(self):
        """priceToTicks(-5.50, 0.25) = -22."""
        assert price_to_ticks(-5.50, 0.25) == -22

    def test_negative_small(self):
        assert price_to_ticks(-0.01, 0.01) == -1


class TestPriceToTicksRounding:
    """JS Math.round rounds half toward +∞ (not banker's rounding).

    math.floor(x + 0.5) reproduces this exactly.
    """

    def test_half_up_positive(self):
        """100.005 / 0.01 = 10000.5 in IEEE 754 → rounds to 10001."""
        assert price_to_ticks(100.005, 0.01) == 10001

    def test_half_up_positive_2(self):
        """100.015 / 0.01 = 10001.5 → 10002."""
        assert price_to_ticks(100.015, 0.01) == 10002

    def test_half_up_positive_3(self):
        """100.025 / 0.01 = 10002.5 → 10003."""
        assert price_to_ticks(100.025, 0.01) == 10003

    def test_exact_boundary(self):
        """Exactly on a tick boundary."""
        assert price_to_ticks(100.50, 0.01) == 10050

    def test_just_below_boundary(self):
        """Just below a tick boundary rounds down."""
        # 100.004 / 0.01 ≈ 10000.4 → 10000
        assert price_to_ticks(100.004, 0.01) == 10000

    def test_just_above_boundary(self):
        """Just above a tick boundary rounds up."""
        # 100.006 / 0.01 ≈ 10000.6 → 10001
        assert price_to_ticks(100.006, 0.01) == 10001


class TestPriceToTicksJsMathRoundSemantics:
    """Verify exact JS Math.round behavior at ±0.5 boundaries.

    JS Math.round(0.5)=1, Math.round(-0.5)=0, Math.round(-1.5)=-1.
    """

    def test_positive_half(self):
        # 0.005 / 0.01 = 0.5 → 1
        assert price_to_ticks(0.005, 0.01) == 1

    def test_negative_half(self):
        # -0.005 / 0.01 = -0.5 → 0 (toward +∞)
        assert price_to_ticks(-0.005, 0.01) == 0

    def test_negative_one_and_half(self):
        # -0.015 / 0.01 = -1.5 → -1 (toward +∞)
        assert price_to_ticks(-0.015, 0.01) == -1


class TestPriceToTicksIntInput:
    """price can be int (JS Number includes integers)."""

    def test_int_price(self):
        assert price_to_ticks(100, 0.01) == 10000

    def test_int_zero(self):
        assert price_to_ticks(0, 0.01) == 0

    def test_int_tick_size(self):
        assert price_to_ticks(100.0, 1) == 100


class TestPriceToTicksLargeValues:
    def test_large_spy_price(self):
        """SPY at ~525.00."""
        assert price_to_ticks(525.00, 0.01) == 52500

    def test_large_amzn(self):
        """AMZN at ~3500."""
        assert price_to_ticks(3500.00, 0.01) == 350000


class TestPriceToTicksInvalid:
    def test_nan(self):
        with pytest.raises(ValueError, match="finite"):
            price_to_ticks(float("nan"), 0.01)

    def test_inf(self):
        with pytest.raises(ValueError, match="finite"):
            price_to_ticks(float("inf"), 0.01)

    def test_neg_inf(self):
        with pytest.raises(ValueError, match="finite"):
            price_to_ticks(float("-inf"), 0.01)

    def test_string_price(self):
        with pytest.raises(TypeError, match="finite number"):
            price_to_ticks("100.00", 0.01)

    def test_none_price(self):
        with pytest.raises(TypeError, match="finite number"):
            price_to_ticks(None, 0.01)

    def test_bool_price(self):
        with pytest.raises(TypeError, match="got bool"):
            price_to_ticks(True, 0.01)

    def test_zero_tick_size(self):
        with pytest.raises(ValueError, match="positive"):
            price_to_ticks(100.0, 0)

    def test_negative_tick_size(self):
        with pytest.raises(ValueError, match="positive"):
            price_to_ticks(100.0, -0.01)

    def test_nan_tick_size(self):
        with pytest.raises(ValueError, match="positive"):
            price_to_ticks(100.0, float("nan"))

    def test_string_tick_size(self):
        with pytest.raises(TypeError, match="positive finite number"):
            price_to_ticks(100.0, "0.01")

    def test_bool_tick_size(self):
        with pytest.raises(TypeError, match="got bool"):
            price_to_ticks(100.0, True)


class TestPriceToTicksDeterministic:
    def test_repeated_calls(self):
        results = [price_to_ticks(750.44, 0.01) for _ in range(100)]
        assert all(r == 75044 for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# decimals_of — JS parity
# ═══════════════════════════════════════════════════════════════════════════════


class TestDecimalsOf:
    """Matches JS decimalsOf in bdrr_engine.js:106-110."""

    def test_two_decimals(self):
        assert decimals_of(0.01) == 2

    def test_quarter(self):
        assert decimals_of(0.25) == 2

    def test_integer(self):
        assert decimals_of(1) == 0

    def test_four_decimals(self):
        assert decimals_of(0.0001) == 4

    def test_one_decimal(self):
        assert decimals_of(0.5) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# ticks_to_points — JS parity vectors
# ═══════════════════════════════════════════════════════════════════════════════


class TestTicksToPointsNormal:
    """Verified against JS ticksToPoints in bdrr_engine.js:112-116."""

    def test_hundred_ticks(self):
        """ticksToPoints(100, 0.01) = 1.00 (test_bdrr_stage1_stage2.js:211)."""
        assert ticks_to_points(100, 0.01) == 1.0

    def test_spy_level(self):
        """ticksToPoints(75044, 0.01) = 750.44."""
        assert ticks_to_points(75044, 0.01) == 750.44

    def test_risk_ticks(self):
        """ticksToPoints(120, 0.01) = 1.2."""
        assert ticks_to_points(120, 0.01) == 1.2

    def test_break_distance(self):
        """ticksToPoints(19, 0.01) = 0.19."""
        assert ticks_to_points(19, 0.01) == 0.19

    def test_quarter_tick(self):
        """ticksToPoints(4, 0.25) = 1.0."""
        assert ticks_to_points(4, 0.25) == 1.0


class TestTicksToPointsZero:
    def test_zero(self):
        """ticksToPoints(0, 0.01) = 0.0."""
        assert ticks_to_points(0, 0.01) == 0.0


class TestTicksToPointsNegative:
    def test_negative(self):
        """ticksToPoints(-3, 0.25) = -0.75."""
        assert ticks_to_points(-3, 0.25) == -0.75

    def test_negative_small(self):
        assert ticks_to_points(-1, 0.01) == -0.01


class TestTicksToPointsInvalid:
    def test_bool_ticks(self):
        with pytest.raises(TypeError, match="got bool"):
            ticks_to_points(True, 0.01)

    def test_float_ticks(self):
        with pytest.raises(TypeError, match="must be an int"):
            ticks_to_points(1.5, 0.01)

    def test_string_ticks(self):
        with pytest.raises(TypeError, match="must be an int"):
            ticks_to_points("100", 0.01)

    def test_bool_tick_size(self):
        with pytest.raises(TypeError, match="got bool"):
            ticks_to_points(100, True)

    def test_string_tick_size(self):
        with pytest.raises(TypeError, match="must be a number"):
            ticks_to_points(100, "0.01")


class TestTicksToPointsDeterministic:
    def test_repeated(self):
        results = [ticks_to_points(75044, 0.01) for _ in range(100)]
        assert all(r == 750.44 for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-function parity: round-trip
# ═══════════════════════════════════════════════════════════════════════════════


class TestRoundTrip:
    """price_to_ticks then ticks_to_points should recover the price."""

    def test_round_trip_spy(self):
        ticks = price_to_ticks(750.44, 0.01)
        points = ticks_to_points(ticks, 0.01)
        assert points == 750.44

    def test_round_trip_integer(self):
        ticks = price_to_ticks(100.00, 0.01)
        points = ticks_to_points(ticks, 0.01)
        assert points == 100.0

    def test_round_trip_quarter(self):
        ticks = price_to_ticks(100.75, 0.25)
        points = ticks_to_points(ticks, 0.25)
        assert points == 100.75
