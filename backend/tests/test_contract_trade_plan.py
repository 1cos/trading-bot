"""Tests for canonical TradePlan/v1 contract type.

Covers:
  - Complete valid construction
  - Exact preservation of every field
  - Exact schema version
  - Exact enum values (EntryModel)
  - Required fields
  - No nullable fields (all required per schema)
  - Invalid primitive types
  - Boolean rejection for integer fields
  - Invalid enum values
  - Invalid nested canonical types
  - Immutability
  - Deterministic equality
  - Exact to_dict() shape
  - Nested canonical serialization
  - Unexpected constructor fields rejected by dataclass
  - Buffer non-negativity
"""

import pytest

from trading_lab.contracts.distances import AbsoluteTickDistance
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.contracts.trade_plan import EntryModel, TradePlan


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK_SIZE = "0.01"


def _pt(ticks: int) -> PriceTicks:
    return PriceTicks(ticks=ticks, tick_size=TICK_SIZE)


def _valid_kwargs() -> dict:
    """LONG CONFIRMATION_CLOSE zero-buffer plan.

    close=101.20 → entry=10120; low=100.00 → stop=10000; risk=120
    r2=10120+240=10360; r3=10120+360=10480; r4=10120+480=10600
    """
    return dict(
        schema_version="TradePlan/v1",
        entry_model=EntryModel.CONFIRMATION_CLOSE,
        entry_buffer_ticks=0,
        stop_buffer_ticks=0,
        tick_size=TICK_SIZE,
        entry_price=_pt(10120),
        stop_price=_pt(10000),
        risk=AbsoluteTickDistance(ticks=120, tick_size=TICK_SIZE),
        r2_price=_pt(10360),
        r3_price=_pt(10480),
        r4_price=_pt(10600),
    )


def make_tp(**overrides) -> TradePlan:
    kw = _valid_kwargs()
    kw.update(overrides)
    return TradePlan(**kw)


# ═══════════════════════════════════════════════════════════════════════════════
# Valid construction
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidConstruction:
    def test_confirmation_close(self):
        tp = make_tp()
        assert tp.schema_version == "TradePlan/v1"
        assert tp.entry_model == EntryModel.CONFIRMATION_CLOSE
        assert tp.entry_buffer_ticks == 0
        assert tp.stop_buffer_ticks == 0
        assert tp.tick_size == TICK_SIZE
        assert tp.entry_price == _pt(10120)
        assert tp.stop_price == _pt(10000)
        assert tp.risk == AbsoluteTickDistance(ticks=120, tick_size=TICK_SIZE)
        assert tp.r2_price == _pt(10360)
        assert tp.r3_price == _pt(10480)
        assert tp.r4_price == _pt(10600)

    def test_break_of_signal_bar(self):
        tp = make_tp(entry_model=EntryModel.BREAK_OF_SIGNAL_BAR)
        assert tp.entry_model == EntryModel.BREAK_OF_SIGNAL_BAR

    def test_non_zero_buffers(self):
        tp = make_tp(entry_buffer_ticks=2, stop_buffer_ticks=3)
        assert tp.entry_buffer_ticks == 2
        assert tp.stop_buffer_ticks == 3

    def test_quarter_tick_size(self):
        ts = "0.25"
        tp = make_tp(
            tick_size=ts,
            entry_price=PriceTicks(ticks=400, tick_size=ts),
            stop_price=PriceTicks(ticks=396, tick_size=ts),
            risk=AbsoluteTickDistance(ticks=4, tick_size=ts),
            r2_price=PriceTicks(ticks=408, tick_size=ts),
            r3_price=PriceTicks(ticks=412, tick_size=ts),
            r4_price=PriceTicks(ticks=416, tick_size=ts),
        )
        assert tp.tick_size == "0.25"


# ═══════════════════════════════════════════════════════════════════════════════
# Field preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestFieldPreservation:
    def test_all_fields_exact(self):
        tp = make_tp()
        assert tp.entry_price.ticks == 10120
        assert tp.stop_price.ticks == 10000
        assert tp.risk.ticks == 120
        assert tp.r2_price.ticks == 10360
        assert tp.r3_price.ticks == 10480
        assert tp.r4_price.ticks == 10600

    def test_identity_preserved(self):
        ep = _pt(10120)
        tp = make_tp(entry_price=ep)
        assert tp.entry_price is ep


# ═══════════════════════════════════════════════════════════════════════════════
# EntryModel enum
# ═══════════════════════════════════════════════════════════════════════════════


class TestEntryModel:
    def test_values(self):
        assert EntryModel.CONFIRMATION_CLOSE == "CONFIRMATION_CLOSE"
        assert EntryModel.BREAK_OF_SIGNAL_BAR == "BREAK_OF_SIGNAL_BAR"

    def test_member_count(self):
        assert len(EntryModel) == 2

    def test_string_comparison(self):
        assert EntryModel.CONFIRMATION_CLOSE == "CONFIRMATION_CLOSE"

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            EntryModel("MARKET_ORDER")


# ═══════════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_cannot_set_schema_version(self):
        tp = make_tp()
        with pytest.raises(AttributeError):
            tp.schema_version = "X"  # type: ignore[misc]

    def test_cannot_set_entry_model(self):
        tp = make_tp()
        with pytest.raises(AttributeError):
            tp.entry_model = EntryModel.BREAK_OF_SIGNAL_BAR  # type: ignore[misc]

    def test_cannot_set_entry_price(self):
        tp = make_tp()
        with pytest.raises(AttributeError):
            tp.entry_price = _pt(0)  # type: ignore[misc]

    def test_cannot_set_risk(self):
        tp = make_tp()
        with pytest.raises(AttributeError):
            tp.risk = AbsoluteTickDistance(ticks=1, tick_size=TICK_SIZE)  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic equality
# ═══════════════════════════════════════════════════════════════════════════════


class TestEquality:
    def test_equal(self):
        a = make_tp()
        b = make_tp()
        assert a == b

    def test_not_equal_entry(self):
        a = make_tp(entry_price=_pt(10120))
        b = make_tp(entry_price=_pt(10121))
        assert a != b

    def test_hash_equal(self):
        a = make_tp()
        b = make_tp()
        assert hash(a) == hash(b)

    def test_hash_usable_in_set(self):
        a = make_tp()
        b = make_tp()
        assert len({a, b}) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialization:
    def test_all_keys_present(self):
        tp = make_tp()
        d = tp.to_dict()
        expected = {
            "schema_version", "entry_model", "entry_buffer_ticks",
            "stop_buffer_ticks", "tick_size", "entry_price", "stop_price",
            "risk", "r2_price", "r3_price", "r4_price",
        }
        assert set(d.keys()) == expected

    def test_key_count(self):
        tp = make_tp()
        assert len(tp.to_dict()) == 11

    def test_schema_version_serialized(self):
        tp = make_tp()
        assert tp.to_dict()["schema_version"] == "TradePlan/v1"

    def test_entry_model_serialized_as_string(self):
        tp = make_tp()
        d = tp.to_dict()
        assert d["entry_model"] == "CONFIRMATION_CLOSE"
        assert isinstance(d["entry_model"], str)

    def test_buffers_serialized_as_int(self):
        tp = make_tp(entry_buffer_ticks=2, stop_buffer_ticks=3)
        d = tp.to_dict()
        assert d["entry_buffer_ticks"] == 2
        assert isinstance(d["entry_buffer_ticks"], int)
        assert d["stop_buffer_ticks"] == 3

    def test_tick_size_serialized_as_string(self):
        tp = make_tp()
        assert tp.to_dict()["tick_size"] == TICK_SIZE
        assert isinstance(tp.to_dict()["tick_size"], str)

    def test_entry_price_nested(self):
        tp = make_tp()
        assert tp.to_dict()["entry_price"] == {
            "ticks": 10120, "tick_size": TICK_SIZE
        }

    def test_stop_price_nested(self):
        tp = make_tp()
        assert tp.to_dict()["stop_price"] == {
            "ticks": 10000, "tick_size": TICK_SIZE
        }

    def test_risk_nested(self):
        tp = make_tp()
        assert tp.to_dict()["risk"] == {
            "ticks": 120, "tick_size": TICK_SIZE
        }

    def test_r2_r3_r4_nested(self):
        tp = make_tp()
        d = tp.to_dict()
        assert d["r2_price"] == {"ticks": 10360, "tick_size": TICK_SIZE}
        assert d["r3_price"] == {"ticks": 10480, "tick_size": TICK_SIZE}
        assert d["r4_price"] == {"ticks": 10600, "tick_size": TICK_SIZE}

    def test_full_shape(self):
        tp = make_tp()
        d = tp.to_dict()
        assert d == {
            "schema_version": "TradePlan/v1",
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_buffer_ticks": 0,
            "stop_buffer_ticks": 0,
            "tick_size": TICK_SIZE,
            "entry_price": {"ticks": 10120, "tick_size": TICK_SIZE},
            "stop_price": {"ticks": 10000, "tick_size": TICK_SIZE},
            "risk": {"ticks": 120, "tick_size": TICK_SIZE},
            "r2_price": {"ticks": 10360, "tick_size": TICK_SIZE},
            "r3_price": {"ticks": 10480, "tick_size": TICK_SIZE},
            "r4_price": {"ticks": 10600, "tick_size": TICK_SIZE},
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid schema_version
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidSchemaVersion:
    def test_wrong_version(self):
        with pytest.raises(ValueError, match="TradePlan/v1"):
            make_tp(schema_version="TradePlan/v2")

    def test_empty(self):
        with pytest.raises(ValueError, match="TradePlan/v1"):
            make_tp(schema_version="")

    def test_none(self):
        with pytest.raises(ValueError, match="TradePlan/v1"):
            make_tp(schema_version=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid entry_model
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidEntryModel:
    def test_string(self):
        with pytest.raises(TypeError, match="EntryModel"):
            make_tp(entry_model="CONFIRMATION_CLOSE")

    def test_none(self):
        with pytest.raises(TypeError, match="EntryModel"):
            make_tp(entry_model=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid buffers
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidBuffers:
    def test_entry_buffer_negative(self):
        with pytest.raises(ValueError, match="must be >= 0"):
            make_tp(entry_buffer_ticks=-1)

    def test_stop_buffer_negative(self):
        with pytest.raises(ValueError, match="must be >= 0"):
            make_tp(stop_buffer_ticks=-1)

    def test_entry_buffer_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_tp(entry_buffer_ticks=True)

    def test_stop_buffer_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_tp(stop_buffer_ticks=False)

    def test_entry_buffer_float(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_tp(entry_buffer_ticks=1.0)

    def test_stop_buffer_string(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_tp(stop_buffer_ticks="0")


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid tick_size
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidTickSize:
    def test_numeric(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_tp(tick_size=0.01)

    def test_none(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_tp(tick_size=None)

    def test_empty(self):
        with pytest.raises(ValueError, match="non-empty"):
            make_tp(tick_size="")


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid nested types
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidNestedTypes:
    def test_entry_price_dict(self):
        with pytest.raises(TypeError, match="PriceTicks"):
            make_tp(entry_price={"ticks": 100, "tick_size": "0.01"})

    def test_entry_price_none(self):
        with pytest.raises(TypeError, match="PriceTicks"):
            make_tp(entry_price=None)

    def test_stop_price_int(self):
        with pytest.raises(TypeError, match="PriceTicks"):
            make_tp(stop_price=10000)

    def test_risk_price_ticks(self):
        """risk must be AbsoluteTickDistance, not PriceTicks."""
        with pytest.raises(TypeError, match="AbsoluteTickDistance"):
            make_tp(risk=_pt(120))

    def test_risk_dict(self):
        with pytest.raises(TypeError, match="AbsoluteTickDistance"):
            make_tp(risk={"ticks": 120, "tick_size": "0.01"})

    def test_risk_none(self):
        with pytest.raises(TypeError, match="AbsoluteTickDistance"):
            make_tp(risk=None)

    def test_r2_price_none(self):
        with pytest.raises(TypeError, match="PriceTicks"):
            make_tp(r2_price=None)

    def test_r3_price_dict(self):
        with pytest.raises(TypeError, match="PriceTicks"):
            make_tp(r3_price={"ticks": 10480})

    def test_r4_price_int(self):
        with pytest.raises(TypeError, match="PriceTicks"):
            make_tp(r4_price=10600)


# ═══════════════════════════════════════════════════════════════════════════════
# Unexpected constructor fields (dataclass rejects)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnexpectedFields:
    def test_extra_field_rejected(self):
        kw = _valid_kwargs()
        kw["direction"] = "LONG"
        with pytest.raises(TypeError):
            TradePlan(**kw)


# ═══════════════════════════════════════════════════════════════════════════════
# Package export
# ═══════════════════════════════════════════════════════════════════════════════


class TestPackageExport:
    def test_import_trade_plan(self):
        from trading_lab.contracts import TradePlan as TP
        assert TP is TradePlan

    def test_import_entry_model(self):
        from trading_lab.contracts import EntryModel as EM
        assert EM is EntryModel
