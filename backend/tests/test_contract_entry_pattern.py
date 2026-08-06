"""Tests for EntryPatternResult contract.

Covers acceptance criteria from B1 preflight:
- Construction, immutability, validation, serialization.
- Metadata immutability via MappingProxyType.
- Bool rejection in numeric fields.
- NaN/Inf rejection.
- candle_indices invariants.
"""

import math
from types import MappingProxyType

import pytest

from trading_lab.contracts.entry_pattern import EntryPatternResult
from trading_lab.contracts.enums import Direction, EntryPatternType


def _make(**overrides):
    """Build a valid EntryPatternResult with defaults."""
    defaults = dict(
        pattern_type=EntryPatternType.SINGLE_CANDLE_REJECTION,
        direction=Direction.LONG,
        entry_bar_index=10,
        entry_price=222.45,
        stop_price=221.80,
        candle_indices=(10,),
        metadata={},
    )
    defaults.update(overrides)
    return EntryPatternResult(**defaults)


# ── Valid construction ────────────────────────────────────────────────────────


class TestValidConstruction:
    def test_single_candle_long(self):
        r = _make()
        assert r.pattern_type == EntryPatternType.SINGLE_CANDLE_REJECTION
        assert r.direction == Direction.LONG
        assert r.entry_bar_index == 10
        assert r.entry_price == 222.45
        assert r.stop_price == 221.80
        assert r.candle_indices == (10,)
        assert dict(r.metadata) == {}

    def test_two_candle_short(self):
        r = _make(
            pattern_type=EntryPatternType.TWO_CANDLE_ENGULFING_RECOVERY,
            direction=Direction.SHORT,
            entry_bar_index=42,
            entry_price=770.50,
            stop_price=771.20,
            candle_indices=(41, 42),
            metadata={"engulfing_body_ratio": 1.35},
        )
        assert r.pattern_type == EntryPatternType.TWO_CANDLE_ENGULFING_RECOVERY
        assert r.direction == Direction.SHORT
        assert r.candle_indices == (41, 42)
        assert r.metadata["engulfing_body_ratio"] == 1.35

    def test_retest_structure(self):
        r = _make(
            pattern_type=EntryPatternType.RETEST_STRUCTURE,
            candle_indices=(5, 6, 7, 8, 9, 10),
        )
        assert r.pattern_type == EntryPatternType.RETEST_STRUCTURE
        assert len(r.candle_indices) == 6

    def test_int_prices_normalized_to_float(self):
        r = _make(entry_price=222, stop_price=221)
        assert isinstance(r.entry_price, float)
        assert isinstance(r.stop_price, float)
        assert r.entry_price == 222.0
        assert r.stop_price == 221.0


# ── Immutability ──────────────────────────────────────────────────────────────


class TestImmutability:
    def test_frozen(self):
        r = _make()
        with pytest.raises(AttributeError):
            r.entry_price = 999.0

    def test_metadata_is_mapping_proxy(self):
        r = _make(metadata={"key": "val"})
        assert isinstance(r.metadata, MappingProxyType)

    def test_metadata_mutation_rejected(self):
        r = _make(metadata={"key": "val"})
        with pytest.raises(TypeError):
            r.metadata["new_key"] = "new_val"

    def test_original_dict_mutation_does_not_affect_contract(self):
        """AC#27: modifying the original dict after construction has no effect."""
        original = {"key": "original"}
        r = _make(metadata=original)
        original["key"] = "mutated"
        original["new"] = "added"
        assert r.metadata["key"] == "original"
        assert "new" not in r.metadata


# ── Serialization ─────────────────────────────────────────────────────────────


class TestSerialization:
    def test_to_dict(self):
        r = _make(
            pattern_type=EntryPatternType.TWO_CANDLE_ENGULFING_RECOVERY,
            direction=Direction.LONG,
            entry_bar_index=42,
            entry_price=222.45,
            stop_price=221.80,
            candle_indices=(41, 42),
            metadata={"engulfing_body_ratio": 1.35},
        )
        d = r.to_dict()
        assert d == {
            "pattern_type": "TWO_CANDLE_ENGULFING_RECOVERY",
            "direction": "LONG",
            "entry_bar_index": 42,
            "entry_price": 222.45,
            "stop_price": 221.80,
            "candle_indices": [41, 42],
            "metadata": {"engulfing_body_ratio": 1.35},
        }
        # candle_indices serialized as list, not tuple
        assert isinstance(d["candle_indices"], list)
        # metadata serialized as dict, not MappingProxyType
        assert isinstance(d["metadata"], dict)

    def test_to_dict_empty_metadata(self):
        d = _make().to_dict()
        assert d["metadata"] == {}

    def test_enum_values_are_strings(self):
        d = _make().to_dict()
        assert isinstance(d["pattern_type"], str)
        assert isinstance(d["direction"], str)


# ── Type validation ───────────────────────────────────────────────────────────


class TestTypeValidation:
    def test_bad_pattern_type(self):
        with pytest.raises(TypeError, match="EntryPatternType"):
            _make(pattern_type="SINGLE_CANDLE_REJECTION")

    def test_bad_direction(self):
        with pytest.raises(TypeError, match="Direction"):
            _make(direction="LONG")

    def test_bad_entry_bar_index_type(self):
        with pytest.raises(TypeError):
            _make(entry_bar_index=10.5)

    def test_bad_entry_price_type(self):
        with pytest.raises(TypeError):
            _make(entry_price="222.45")

    def test_bad_stop_price_type(self):
        with pytest.raises(TypeError):
            _make(stop_price="221.80")

    def test_bad_candle_indices_type(self):
        with pytest.raises(TypeError, match="tuple"):
            _make(candle_indices=[10])

    def test_bad_metadata_type(self):
        with pytest.raises(TypeError, match="dict"):
            _make(metadata="not a dict")


# ── Bool rejection in numeric fields (AC#24) ──────────────────────────────────


class TestBoolRejection:
    def test_entry_bar_index_rejects_bool(self):
        with pytest.raises(TypeError, match="bool"):
            _make(entry_bar_index=True)

    def test_entry_price_rejects_bool(self):
        with pytest.raises(TypeError, match="bool"):
            _make(entry_price=True)

    def test_stop_price_rejects_bool(self):
        with pytest.raises(TypeError, match="bool"):
            _make(stop_price=False)

    def test_candle_index_element_rejects_bool(self):
        with pytest.raises(TypeError, match="bool"):
            _make(candle_indices=(True,), entry_bar_index=1)


# ── NaN / Infinity rejection (AC#26) ─────────────────────────────────────────


class TestNanInfRejection:
    def test_entry_price_nan(self):
        with pytest.raises(ValueError, match="finite"):
            _make(entry_price=float("nan"))

    def test_stop_price_inf(self):
        with pytest.raises(ValueError, match="finite"):
            _make(stop_price=float("inf"))

    def test_stop_price_neg_inf(self):
        with pytest.raises(ValueError, match="finite"):
            _make(stop_price=float("-inf"))

    def test_metadata_float_nan(self):
        with pytest.raises(ValueError, match="finite"):
            _make(metadata={"ratio": float("nan")})

    def test_metadata_float_inf(self):
        with pytest.raises(ValueError, match="finite"):
            _make(metadata={"ratio": float("inf")})


# ── Value validation ──────────────────────────────────────────────────────────


class TestValueValidation:
    def test_negative_entry_bar_index(self):
        with pytest.raises(ValueError, match=">= 0"):
            _make(entry_bar_index=-1, candle_indices=(0,))

    def test_empty_candle_indices(self):
        with pytest.raises(ValueError, match="non-empty"):
            _make(candle_indices=())

    def test_negative_candle_index(self):
        with pytest.raises(ValueError, match=">= 0"):
            _make(candle_indices=(-1, 10))

    def test_entry_bar_index_not_in_candle_indices(self):
        with pytest.raises(ValueError, match="present in candle_indices"):
            _make(entry_bar_index=10, candle_indices=(5, 6))

    def test_entry_bar_index_in_candle_indices(self):
        r = _make(entry_bar_index=6, candle_indices=(5, 6))
        assert r.entry_bar_index == 6


# ── Metadata validation (AC#25) ──────────────────────────────────────────────


class TestMetadataValidation:
    def test_non_string_key_rejected(self):
        with pytest.raises(TypeError, match="keys must be str"):
            _make(metadata={42: "value"})

    def test_invalid_value_type(self):
        with pytest.raises(TypeError, match="str|int|float|bool|None"):
            _make(metadata={"key": [1, 2, 3]})

    def test_allowed_value_types(self):
        r = _make(metadata={
            "s": "hello",
            "i": 42,
            "f": 3.14,
            "b": True,
            "n": None,
        })
        assert r.metadata["s"] == "hello"
        assert r.metadata["i"] == 42
        assert r.metadata["f"] == 3.14
        assert r.metadata["b"] is True
        assert r.metadata["n"] is None
