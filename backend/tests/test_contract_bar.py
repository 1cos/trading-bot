"""Tests for the canonical Bar contract type.

Covers:
  - Valid construction (with and without volume)
  - Exact field preservation
  - Immutability (frozen dataclass)
  - Deterministic equality
  - Exact serialization shape (.to_dict())
  - Nested PriceTicks serialization
  - Every required field
  - Nullable versus non-nullable fields
  - Invalid primitive types
  - Boolean rejection for integer fields
  - Invalid nested values
  - Timestamp validation
  - OHLC non-validation (matching buildBar — consumer validates, not Bar)
"""

import pytest

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.primitives import PriceTicks


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK_SIZE = "0.01"

# Reusable PriceTicks for a bar at ~525.00
PT_OPEN = PriceTicks(ticks=52500, tick_size=TICK_SIZE)
PT_HIGH = PriceTicks(ticks=52550, tick_size=TICK_SIZE)
PT_LOW = PriceTicks(ticks=52480, tick_size=TICK_SIZE)
PT_CLOSE = PriceTicks(ticks=52530, tick_size=TICK_SIZE)

# Epoch ms for 2026-05-26 09:30:00 ET (13:30 UTC)
BAR_MS = 1748264400000


def make_bar(**overrides):
    """Build a valid Bar, overriding any fields."""
    defaults = dict(
        bar_utc_ms=BAR_MS,
        open=PT_OPEN,
        high=PT_HIGH,
        low=PT_LOW,
        close=PT_CLOSE,
        volume=None,
    )
    defaults.update(overrides)
    return Bar(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# Valid construction
# ═══════════════════════════════════════════════════════════════════════════════


class TestBarConstruction:
    def test_minimal_no_volume(self):
        bar = make_bar()
        assert bar.bar_utc_ms == BAR_MS
        assert bar.open is PT_OPEN
        assert bar.high is PT_HIGH
        assert bar.low is PT_LOW
        assert bar.close is PT_CLOSE
        assert bar.volume is None

    def test_with_volume(self):
        bar = make_bar(volume=123456)
        assert bar.volume == 123456

    def test_with_zero_volume(self):
        bar = make_bar(volume=0)
        assert bar.volume == 0

    def test_with_negative_volume(self):
        """Schema says int64 — no non-negativity rule in the contract."""
        bar = make_bar(volume=-1)
        assert bar.volume == -1

    def test_zero_timestamp(self):
        bar = make_bar(bar_utc_ms=0)
        assert bar.bar_utc_ms == 0

    def test_negative_timestamp(self):
        """Pre-epoch timestamps are valid int64."""
        bar = make_bar(bar_utc_ms=-1000)
        assert bar.bar_utc_ms == -1000


# ═══════════════════════════════════════════════════════════════════════════════
# Field preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestBarFieldPreservation:
    def test_bar_utc_ms_exact(self):
        bar = make_bar(bar_utc_ms=1748264400123)
        assert bar.bar_utc_ms == 1748264400123

    def test_ohlc_identity(self):
        """PriceTicks instances are preserved by identity."""
        bar = make_bar()
        assert bar.open is PT_OPEN
        assert bar.high is PT_HIGH
        assert bar.low is PT_LOW
        assert bar.close is PT_CLOSE

    def test_volume_exact(self):
        bar = make_bar(volume=999999)
        assert bar.volume == 999999


# ═══════════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════════


class TestBarImmutability:
    def test_cannot_set_bar_utc_ms(self):
        bar = make_bar()
        with pytest.raises(AttributeError):
            bar.bar_utc_ms = 0  # type: ignore[misc]

    def test_cannot_set_open(self):
        bar = make_bar()
        with pytest.raises(AttributeError):
            bar.open = PT_CLOSE  # type: ignore[misc]

    def test_cannot_set_high(self):
        bar = make_bar()
        with pytest.raises(AttributeError):
            bar.high = PT_LOW  # type: ignore[misc]

    def test_cannot_set_low(self):
        bar = make_bar()
        with pytest.raises(AttributeError):
            bar.low = PT_HIGH  # type: ignore[misc]

    def test_cannot_set_close(self):
        bar = make_bar()
        with pytest.raises(AttributeError):
            bar.close = PT_OPEN  # type: ignore[misc]

    def test_cannot_set_volume(self):
        bar = make_bar(volume=100)
        with pytest.raises(AttributeError):
            bar.volume = 200  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic equality
# ═══════════════════════════════════════════════════════════════════════════════


class TestBarEquality:
    def test_equal(self):
        a = make_bar()
        b = make_bar()
        assert a == b

    def test_not_equal_timestamp(self):
        a = make_bar(bar_utc_ms=1000)
        b = make_bar(bar_utc_ms=2000)
        assert a != b

    def test_not_equal_open(self):
        a = make_bar(open=PriceTicks(ticks=1, tick_size=TICK_SIZE))
        b = make_bar(open=PriceTicks(ticks=2, tick_size=TICK_SIZE))
        assert a != b

    def test_not_equal_volume(self):
        a = make_bar(volume=100)
        b = make_bar(volume=200)
        assert a != b

    def test_not_equal_volume_null_vs_int(self):
        a = make_bar(volume=None)
        b = make_bar(volume=0)
        assert a != b

    def test_hash_equal(self):
        a = make_bar()
        b = make_bar()
        assert hash(a) == hash(b)

    def test_hash_usable_in_set(self):
        a = make_bar()
        b = make_bar()
        assert len({a, b}) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════════════════════


class TestBarSerialization:
    def test_shape_no_volume(self):
        bar = make_bar()
        d = bar.to_dict()
        assert d == {
            "bar_utc_ms": BAR_MS,
            "open": {"ticks": 52500, "tick_size": TICK_SIZE},
            "high": {"ticks": 52550, "tick_size": TICK_SIZE},
            "low": {"ticks": 52480, "tick_size": TICK_SIZE},
            "close": {"ticks": 52530, "tick_size": TICK_SIZE},
            "volume": None,
        }

    def test_shape_with_volume(self):
        bar = make_bar(volume=42)
        d = bar.to_dict()
        assert d["volume"] == 42

    def test_keys_exact(self):
        bar = make_bar()
        assert set(bar.to_dict().keys()) == {
            "bar_utc_ms", "open", "high", "low", "close", "volume",
        }

    def test_bar_utc_ms_is_int(self):
        bar = make_bar()
        assert isinstance(bar.to_dict()["bar_utc_ms"], int)

    def test_ohlc_are_dicts(self):
        bar = make_bar()
        d = bar.to_dict()
        for field in ("open", "high", "low", "close"):
            assert isinstance(d[field], dict)

    def test_nested_price_ticks_shape(self):
        """Nested PriceTicks must serialize via their own to_dict()."""
        bar = make_bar()
        d = bar.to_dict()
        for field in ("open", "high", "low", "close"):
            assert set(d[field].keys()) == {"ticks", "tick_size"}
            assert isinstance(d[field]["ticks"], int)
            assert isinstance(d[field]["tick_size"], str)

    def test_volume_none_serialized(self):
        bar = make_bar(volume=None)
        assert bar.to_dict()["volume"] is None

    def test_volume_int_serialized(self):
        bar = make_bar(volume=77)
        assert bar.to_dict()["volume"] == 77
        assert isinstance(bar.to_dict()["volume"], int)


# ═══════════════════════════════════════════════════════════════════════════════
# Required fields — invalid types
# ═══════════════════════════════════════════════════════════════════════════════


class TestBarInvalidBarUtcMs:
    def test_none(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_bar(bar_utc_ms=None)

    def test_float(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_bar(bar_utc_ms=1.5)

    def test_string(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_bar(bar_utc_ms="1748264400000")

    def test_bool_true(self):
        with pytest.raises(TypeError, match="got bool"):
            make_bar(bar_utc_ms=True)

    def test_bool_false(self):
        with pytest.raises(TypeError, match="got bool"):
            make_bar(bar_utc_ms=False)


class TestBarInvalidOHLC:
    def test_open_none(self):
        with pytest.raises(TypeError, match="open must be a PriceTicks"):
            make_bar(open=None)

    def test_open_dict(self):
        with pytest.raises(TypeError, match="open must be a PriceTicks"):
            make_bar(open={"ticks": 100, "tick_size": "0.01"})

    def test_open_int(self):
        with pytest.raises(TypeError, match="open must be a PriceTicks"):
            make_bar(open=52500)

    def test_high_none(self):
        with pytest.raises(TypeError, match="high must be a PriceTicks"):
            make_bar(high=None)

    def test_low_none(self):
        with pytest.raises(TypeError, match="low must be a PriceTicks"):
            make_bar(low=None)

    def test_close_none(self):
        with pytest.raises(TypeError, match="close must be a PriceTicks"):
            make_bar(close=None)

    def test_close_string(self):
        with pytest.raises(TypeError, match="close must be a PriceTicks"):
            make_bar(close="52530")


class TestBarInvalidVolume:
    def test_float(self):
        with pytest.raises(TypeError, match="must be an int or None"):
            make_bar(volume=1.5)

    def test_string(self):
        with pytest.raises(TypeError, match="must be an int or None"):
            make_bar(volume="100")

    def test_bool_true(self):
        with pytest.raises(TypeError, match="got bool"):
            make_bar(volume=True)

    def test_bool_false(self):
        with pytest.raises(TypeError, match="got bool"):
            make_bar(volume=False)


# ═══════════════════════════════════════════════════════════════════════════════
# OHLC non-validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestBarOHLCNonValidation:
    """Bar constructor does NOT enforce high >= low.

    The JS buildBar function does not validate OHLC relationships.
    validateBars in trade_outcome.js enforces high.ticks >= low.ticks
    at the consumer level.  The Python Bar type matches buildBar behavior.
    """

    def test_high_below_low_accepted(self):
        """This is structurally invalid but accepted by the constructor."""
        bar = make_bar(
            high=PriceTicks(ticks=100, tick_size=TICK_SIZE),
            low=PriceTicks(ticks=200, tick_size=TICK_SIZE),
        )
        assert bar.high.ticks < bar.low.ticks

    def test_equal_ohlc_accepted(self):
        pt = PriceTicks(ticks=500, tick_size=TICK_SIZE)
        bar = make_bar(open=pt, high=pt, low=pt, close=pt)
        assert bar.open == bar.high == bar.low == bar.close


# ═══════════════════════════════════════════════════════════════════════════════
# Package-level export
# ═══════════════════════════════════════════════════════════════════════════════


class TestBarPackageExport:
    def test_import_from_contracts(self):
        from trading_lab.contracts import Bar as BarImport
        assert BarImport is Bar
