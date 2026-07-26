"""Tests for canonical primitive contract types.

Covers:
  - Valid construction
  - Immutability (frozen dataclass)
  - Exact serialization shape (.to_dict())
  - Invalid integer inputs
  - Boolean rejection
  - Invalid Rational denominators
  - Deterministic equality
  - Computed methods (to_price, as_decimal)
"""

from decimal import Decimal

import pytest

from trading_lab.contracts.primitives import PriceTicks, Rational


# ═══════════════════════════════════════════════════════════════════════════════
# PriceTicks
# ═══════════════════════════════════════════════════════════════════════════════


class TestPriceTicksConstruction:
    """Valid construction."""

    def test_positive_ticks(self):
        pt = PriceTicks(ticks=100, tick_size="0.01")
        assert pt.ticks == 100
        assert pt.tick_size == "0.01"

    def test_zero_ticks(self):
        pt = PriceTicks(ticks=0, tick_size="0.25")
        assert pt.ticks == 0

    def test_negative_ticks(self):
        pt = PriceTicks(ticks=-50, tick_size="0.01")
        assert pt.ticks == -50

    def test_large_ticks(self):
        pt = PriceTicks(ticks=2**40, tick_size="0.0001")
        assert pt.ticks == 2**40


class TestPriceTicksImmutability:
    """Frozen dataclass must reject mutation."""

    def test_cannot_set_ticks(self):
        pt = PriceTicks(ticks=100, tick_size="0.01")
        with pytest.raises(AttributeError):
            pt.ticks = 200  # type: ignore[misc]

    def test_cannot_set_tick_size(self):
        pt = PriceTicks(ticks=100, tick_size="0.01")
        with pytest.raises(AttributeError):
            pt.tick_size = "0.05"  # type: ignore[misc]


class TestPriceTicksSerialization:
    """to_dict() must match canonical JSON shape."""

    def test_shape(self):
        pt = PriceTicks(ticks=42, tick_size="0.25")
        d = pt.to_dict()
        assert d == {"ticks": 42, "tick_size": "0.25"}

    def test_keys_exact(self):
        pt = PriceTicks(ticks=1, tick_size="0.01")
        assert set(pt.to_dict().keys()) == {"ticks", "tick_size"}

    def test_ticks_is_int(self):
        pt = PriceTicks(ticks=10, tick_size="0.01")
        assert isinstance(pt.to_dict()["ticks"], int)

    def test_tick_size_is_str(self):
        pt = PriceTicks(ticks=10, tick_size="0.01")
        assert isinstance(pt.to_dict()["tick_size"], str)

    def test_negative_ticks_serialization(self):
        pt = PriceTicks(ticks=-7, tick_size="0.01")
        assert pt.to_dict() == {"ticks": -7, "tick_size": "0.01"}


class TestPriceTicksToPrice:
    """to_price() must return exact Decimal."""

    def test_basic(self):
        pt = PriceTicks(ticks=100, tick_size="0.01")
        assert pt.to_price() == Decimal("1.00")

    def test_quarter_tick(self):
        pt = PriceTicks(ticks=4, tick_size="0.25")
        assert pt.to_price() == Decimal("1.00")

    def test_zero_ticks_price(self):
        pt = PriceTicks(ticks=0, tick_size="0.01")
        assert pt.to_price() == Decimal("0.00")

    def test_negative_ticks_price(self):
        pt = PriceTicks(ticks=-3, tick_size="0.25")
        assert pt.to_price() == Decimal("-0.75")


class TestPriceTicksInvalidInputs:
    """Reject non-integer ticks and invalid tick_size."""

    def test_float_ticks(self):
        with pytest.raises(TypeError, match="must be an int"):
            PriceTicks(ticks=1.5, tick_size="0.01")  # type: ignore[arg-type]

    def test_string_ticks(self):
        with pytest.raises(TypeError, match="must be an int"):
            PriceTicks(ticks="100", tick_size="0.01")  # type: ignore[arg-type]

    def test_none_ticks(self):
        with pytest.raises(TypeError, match="must be an int"):
            PriceTicks(ticks=None, tick_size="0.01")  # type: ignore[arg-type]

    def test_bool_ticks_rejected(self):
        with pytest.raises(TypeError, match="got bool"):
            PriceTicks(ticks=True, tick_size="0.01")  # type: ignore[arg-type]

    def test_bool_false_ticks_rejected(self):
        with pytest.raises(TypeError, match="got bool"):
            PriceTicks(ticks=False, tick_size="0.01")  # type: ignore[arg-type]

    def test_numeric_tick_size_rejected(self):
        with pytest.raises(TypeError, match="must be a str"):
            PriceTicks(ticks=1, tick_size=0.01)  # type: ignore[arg-type]

    def test_empty_tick_size(self):
        with pytest.raises(ValueError, match="non-empty"):
            PriceTicks(ticks=1, tick_size="")

    def test_non_numeric_tick_size(self):
        with pytest.raises(ValueError, match="not a valid decimal"):
            PriceTicks(ticks=1, tick_size="abc")

    def test_zero_tick_size(self):
        with pytest.raises(ValueError, match="must be positive"):
            PriceTicks(ticks=1, tick_size="0")

    def test_negative_tick_size(self):
        with pytest.raises(ValueError, match="must be positive"):
            PriceTicks(ticks=1, tick_size="-0.01")


class TestPriceTicksEquality:
    """Deterministic equality from frozen dataclass."""

    def test_equal(self):
        a = PriceTicks(ticks=100, tick_size="0.01")
        b = PriceTicks(ticks=100, tick_size="0.01")
        assert a == b

    def test_not_equal_ticks(self):
        a = PriceTicks(ticks=100, tick_size="0.01")
        b = PriceTicks(ticks=101, tick_size="0.01")
        assert a != b

    def test_not_equal_tick_size(self):
        a = PriceTicks(ticks=100, tick_size="0.01")
        b = PriceTicks(ticks=100, tick_size="0.25")
        assert a != b

    def test_hash_equal(self):
        a = PriceTicks(ticks=100, tick_size="0.01")
        b = PriceTicks(ticks=100, tick_size="0.01")
        assert hash(a) == hash(b)

    def test_hash_usable_in_set(self):
        a = PriceTicks(ticks=100, tick_size="0.01")
        b = PriceTicks(ticks=100, tick_size="0.01")
        assert len({a, b}) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Rational
# ═══════════════════════════════════════════════════════════════════════════════


class TestRationalConstruction:
    """Valid construction."""

    def test_positive(self):
        r = Rational(numerator=3, denominator=4)
        assert r.numerator == 3
        assert r.denominator == 4

    def test_zero_numerator(self):
        r = Rational(numerator=0, denominator=1)
        assert r.numerator == 0

    def test_negative_numerator(self):
        r = Rational(numerator=-5, denominator=10)
        assert r.numerator == -5

    def test_large_denominator(self):
        r = Rational(numerator=470000, denominator=1000000)
        assert r.denominator == 1000000


class TestRationalImmutability:
    """Frozen dataclass must reject mutation."""

    def test_cannot_set_numerator(self):
        r = Rational(numerator=1, denominator=2)
        with pytest.raises(AttributeError):
            r.numerator = 3  # type: ignore[misc]

    def test_cannot_set_denominator(self):
        r = Rational(numerator=1, denominator=2)
        with pytest.raises(AttributeError):
            r.denominator = 4  # type: ignore[misc]


class TestRationalSerialization:
    """to_dict() must match canonical JSON shape."""

    def test_shape(self):
        r = Rational(numerator=47, denominator=100)
        d = r.to_dict()
        assert d == {"numerator": 47, "denominator": 100}

    def test_keys_exact(self):
        r = Rational(numerator=1, denominator=2)
        assert set(r.to_dict().keys()) == {"numerator", "denominator"}

    def test_values_are_int(self):
        r = Rational(numerator=3, denominator=7)
        d = r.to_dict()
        assert isinstance(d["numerator"], int)
        assert isinstance(d["denominator"], int)


class TestRationalAsDecimal:
    """as_decimal() must return exact Decimal."""

    def test_half(self):
        r = Rational(numerator=1, denominator=2)
        assert r.as_decimal() == Decimal("0.5")

    def test_zero(self):
        r = Rational(numerator=0, denominator=100)
        assert r.as_decimal() == Decimal("0")

    def test_negative(self):
        r = Rational(numerator=-3, denominator=4)
        assert r.as_decimal() == Decimal("-0.75")

    def test_engine_ratio_precision(self):
        """Match JS floatToRational(0.47) → { numerator: 470000, denominator: 1000000 }."""
        r = Rational(numerator=470000, denominator=1000000)
        assert r.as_decimal() == Decimal("0.47")


class TestRationalInvalidInputs:
    """Reject invalid types and denominators."""

    def test_float_numerator(self):
        with pytest.raises(TypeError, match="must be an int"):
            Rational(numerator=1.5, denominator=2)  # type: ignore[arg-type]

    def test_float_denominator(self):
        with pytest.raises(TypeError, match="must be an int"):
            Rational(numerator=1, denominator=2.0)  # type: ignore[arg-type]

    def test_string_numerator(self):
        with pytest.raises(TypeError, match="must be an int"):
            Rational(numerator="1", denominator=2)  # type: ignore[arg-type]

    def test_none_denominator(self):
        with pytest.raises(TypeError, match="must be an int"):
            Rational(numerator=1, denominator=None)  # type: ignore[arg-type]

    def test_bool_numerator_rejected(self):
        with pytest.raises(TypeError, match="got bool"):
            Rational(numerator=True, denominator=2)  # type: ignore[arg-type]

    def test_bool_denominator_rejected(self):
        with pytest.raises(TypeError, match="got bool"):
            Rational(numerator=1, denominator=True)  # type: ignore[arg-type]

    def test_zero_denominator(self):
        with pytest.raises(ValueError, match="must be > 0"):
            Rational(numerator=1, denominator=0)

    def test_negative_denominator(self):
        with pytest.raises(ValueError, match="must be > 0"):
            Rational(numerator=1, denominator=-1)


class TestRationalEquality:
    """Deterministic equality from frozen dataclass."""

    def test_equal(self):
        a = Rational(numerator=3, denominator=4)
        b = Rational(numerator=3, denominator=4)
        assert a == b

    def test_not_equal_numerator(self):
        a = Rational(numerator=3, denominator=4)
        b = Rational(numerator=5, denominator=4)
        assert a != b

    def test_not_equal_denominator(self):
        a = Rational(numerator=3, denominator=4)
        b = Rational(numerator=3, denominator=8)
        assert a != b

    def test_not_mathematically_reduced(self):
        """2/4 and 1/2 are NOT equal — no reduction per contract."""
        a = Rational(numerator=2, denominator=4)
        b = Rational(numerator=1, denominator=2)
        assert a != b

    def test_hash_equal(self):
        a = Rational(numerator=3, denominator=4)
        b = Rational(numerator=3, denominator=4)
        assert hash(a) == hash(b)

    def test_hash_usable_in_set(self):
        a = Rational(numerator=3, denominator=4)
        b = Rational(numerator=3, denominator=4)
        assert len({a, b}) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Package-level imports
# ═══════════════════════════════════════════════════════════════════════════════


class TestContractsPackageExports:
    """Verify re-exports from trading_lab.contracts."""

    def test_import_price_ticks(self):
        from trading_lab.contracts import PriceTicks as PT
        assert PT is PriceTicks

    def test_import_rational(self):
        from trading_lab.contracts import Rational as R
        assert R is Rational
