"""Tests for canonical tick distance contract types."""

from decimal import Decimal

import pytest

from trading_lab.contracts.distances import (
    AbsoluteTickDistance,
    DirectionalTickDistance,
)


TICK_SIZE = "0.01"


# ═══════════════════════════════════════════════════════════════════════════════
# DirectionalTickDistance
# ═══════════════════════════════════════════════════════════════════════════════


class TestDirectionalConstruction:
    def test_positive(self):
        d = DirectionalTickDistance(ticks=10, tick_size=TICK_SIZE)
        assert d.ticks == 10

    def test_zero(self):
        d = DirectionalTickDistance(ticks=0, tick_size=TICK_SIZE)
        assert d.ticks == 0

    def test_negative(self):
        d = DirectionalTickDistance(ticks=-5, tick_size=TICK_SIZE)
        assert d.ticks == -5


class TestDirectionalImmutability:
    def test_cannot_set_ticks(self):
        d = DirectionalTickDistance(ticks=10, tick_size=TICK_SIZE)
        with pytest.raises(AttributeError):
            d.ticks = 20  # type: ignore[misc]


class TestDirectionalSerialization:
    def test_shape(self):
        d = DirectionalTickDistance(ticks=-3, tick_size="0.25")
        assert d.to_dict() == {"ticks": -3, "tick_size": "0.25"}

    def test_keys_exact(self):
        d = DirectionalTickDistance(ticks=1, tick_size=TICK_SIZE)
        assert set(d.to_dict().keys()) == {"ticks", "tick_size"}


class TestDirectionalToPrice:
    def test_positive(self):
        d = DirectionalTickDistance(ticks=4, tick_size="0.25")
        assert d.to_price() == Decimal("1.00")

    def test_negative(self):
        d = DirectionalTickDistance(ticks=-2, tick_size="0.01")
        assert d.to_price() == Decimal("-0.02")


class TestDirectionalEquality:
    def test_equal(self):
        a = DirectionalTickDistance(ticks=5, tick_size=TICK_SIZE)
        b = DirectionalTickDistance(ticks=5, tick_size=TICK_SIZE)
        assert a == b

    def test_not_equal(self):
        a = DirectionalTickDistance(ticks=5, tick_size=TICK_SIZE)
        b = DirectionalTickDistance(ticks=-5, tick_size=TICK_SIZE)
        assert a != b

    def test_hash_equal(self):
        a = DirectionalTickDistance(ticks=5, tick_size=TICK_SIZE)
        b = DirectionalTickDistance(ticks=5, tick_size=TICK_SIZE)
        assert hash(a) == hash(b)


class TestDirectionalInvalid:
    def test_bool_ticks(self):
        with pytest.raises(TypeError, match="got bool"):
            DirectionalTickDistance(ticks=True, tick_size=TICK_SIZE)

    def test_float_ticks(self):
        with pytest.raises(TypeError, match="must be an int"):
            DirectionalTickDistance(ticks=1.5, tick_size=TICK_SIZE)

    def test_numeric_tick_size(self):
        with pytest.raises(TypeError, match="must be a str"):
            DirectionalTickDistance(ticks=1, tick_size=0.01)  # type: ignore[arg-type]

    def test_negative_tick_size(self):
        with pytest.raises(ValueError, match="must be positive"):
            DirectionalTickDistance(ticks=1, tick_size="-0.01")


# ═══════════════════════════════════════════════════════════════════════════════
# AbsoluteTickDistance
# ═══════════════════════════════════════════════════════════════════════════════


class TestAbsoluteConstruction:
    def test_positive(self):
        a = AbsoluteTickDistance(ticks=10, tick_size=TICK_SIZE)
        assert a.ticks == 10

    def test_zero(self):
        a = AbsoluteTickDistance(ticks=0, tick_size=TICK_SIZE)
        assert a.ticks == 0


class TestAbsoluteNonNegativity:
    def test_negative_rejected(self):
        with pytest.raises(ValueError, match="must be >= 0"):
            AbsoluteTickDistance(ticks=-1, tick_size=TICK_SIZE)

    def test_large_negative_rejected(self):
        with pytest.raises(ValueError, match="must be >= 0"):
            AbsoluteTickDistance(ticks=-999, tick_size=TICK_SIZE)


class TestAbsoluteImmutability:
    def test_cannot_set_ticks(self):
        a = AbsoluteTickDistance(ticks=10, tick_size=TICK_SIZE)
        with pytest.raises(AttributeError):
            a.ticks = 20  # type: ignore[misc]


class TestAbsoluteSerialization:
    def test_shape(self):
        a = AbsoluteTickDistance(ticks=7, tick_size="0.25")
        assert a.to_dict() == {"ticks": 7, "tick_size": "0.25"}

    def test_keys_exact(self):
        a = AbsoluteTickDistance(ticks=1, tick_size=TICK_SIZE)
        assert set(a.to_dict().keys()) == {"ticks", "tick_size"}


class TestAbsoluteToPrice:
    def test_basic(self):
        a = AbsoluteTickDistance(ticks=4, tick_size="0.25")
        assert a.to_price() == Decimal("1.00")

    def test_zero(self):
        a = AbsoluteTickDistance(ticks=0, tick_size="0.01")
        assert a.to_price() == Decimal("0.00")


class TestAbsoluteEquality:
    def test_equal(self):
        a = AbsoluteTickDistance(ticks=5, tick_size=TICK_SIZE)
        b = AbsoluteTickDistance(ticks=5, tick_size=TICK_SIZE)
        assert a == b

    def test_hash_equal(self):
        a = AbsoluteTickDistance(ticks=5, tick_size=TICK_SIZE)
        b = AbsoluteTickDistance(ticks=5, tick_size=TICK_SIZE)
        assert hash(a) == hash(b)


class TestAbsoluteInvalid:
    def test_bool_ticks(self):
        with pytest.raises(TypeError, match="got bool"):
            AbsoluteTickDistance(ticks=True, tick_size=TICK_SIZE)

    def test_float_ticks(self):
        with pytest.raises(TypeError, match="must be an int"):
            AbsoluteTickDistance(ticks=1.5, tick_size=TICK_SIZE)

    def test_numeric_tick_size(self):
        with pytest.raises(TypeError, match="must be a str"):
            AbsoluteTickDistance(ticks=1, tick_size=0.01)  # type: ignore[arg-type]


class TestDistancePackageExports:
    def test_import_directional(self):
        from trading_lab.contracts import DirectionalTickDistance as D
        assert D is DirectionalTickDistance

    def test_import_absolute(self):
        from trading_lab.contracts import AbsoluteTickDistance as A
        assert A is AbsoluteTickDistance
