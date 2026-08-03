"""Tests for canonical TradeOutcome/v2 contract type.

Covers:
    - Valid construction with integer and fractional R/R values
    - Invalid values (zero, negative, wrong type)
    - Label validation (must match Rational value)
    - Serialization round-trip without precision loss
    - v1 compatibility (v1 unchanged, rejects 2.5; v2 accepts 2.5)
"""

import pytest

from trading_lab.contracts.enums import Direction
from trading_lab.contracts.primitives import Rational
from trading_lab.contracts.trade_outcome import TradeOutcome, TradeOutcomeStatus
from trading_lab.contracts.trade_outcome_v2 import (
    TradeOutcomeV2,
    rational_to_label,
)
from trading_lab.contracts.trade_plan import EntryModel


# ── Constants ────────────────────────────────────────────────────────────────

TICK_SIZE = "0.01"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _base_kwargs(
    selected_r: Rational,
    *,
    outcome: TradeOutcomeStatus = TradeOutcomeStatus.TARGET_HIT,
    exit_target_r: Rational | None = None,
    highest_target_r: Rational | None = None,
    realized_r: Rational | None = None,
) -> dict:
    """Build a minimal valid v2 kwargs dict with the given R target."""
    label = rational_to_label(selected_r)
    return dict(
        schema_version="TradeOutcome/v2",
        direction=Direction.LONG,
        entry_model=EntryModel.CONFIRMATION_CLOSE,
        entry_price_ticks=10120,
        stop_price_ticks=10000,
        tick_size=TICK_SIZE,
        selected_exit_target_r=selected_r,
        selected_exit_target_label=label,
        entry_triggered=True,
        entry_bar_utc_ms=1748264400000,
        bosb_entry_bar_index=None,
        first_eval_bar_index=0,
        first_eval_bar_utc_ms=1748264700000,
        outcome=outcome,
        exit_bar_index=3,
        exit_bar_utc_ms=1748265600000,
        exit_price_ticks=10360,
        exit_target_label=label if outcome == TradeOutcomeStatus.TARGET_HIT else None,
        exit_target_r=exit_target_r if exit_target_r is not None else (
            selected_r if outcome == TradeOutcomeStatus.TARGET_HIT else None
        ),
        highest_target_achieved=label if outcome == TradeOutcomeStatus.TARGET_HIT else None,
        highest_target_r=highest_target_r if highest_target_r is not None else (
            selected_r if outcome == TradeOutcomeStatus.TARGET_HIT else None
        ),
        realized_r=realized_r if realized_r is not None else (
            selected_r if outcome == TradeOutcomeStatus.TARGET_HIT else None
        ),
        r2_price_ticks=10360,
        r3_price_ticks=10480,
        r4_price_ticks=10600,
    )


def _v1_target_hit_kwargs() -> dict:
    """Build a valid v1 kwargs dict for comparison tests."""
    return dict(
        schema_version="TradeOutcome/v1",
        direction=Direction.LONG,
        entry_model=EntryModel.CONFIRMATION_CLOSE,
        entry_price_ticks=10120,
        stop_price_ticks=10000,
        tick_size=TICK_SIZE,
        selected_exit_target_r=2,
        selected_exit_target_label="2R",
        entry_triggered=True,
        entry_bar_utc_ms=1748264400000,
        bosb_entry_bar_index=None,
        first_eval_bar_index=0,
        first_eval_bar_utc_ms=1748264700000,
        outcome=TradeOutcomeStatus.TARGET_HIT,
        exit_bar_index=3,
        exit_bar_utc_ms=1748265600000,
        exit_price_ticks=10360,
        exit_target_label="2R",
        exit_target_r=2,
        highest_target_achieved="2R",
        highest_target_r=2,
        realized_r=2,
        r2_price_ticks=10360,
        r3_price_ticks=10480,
        r4_price_ticks=10600,
    )


# ── rational_to_label ────────────────────────────────────────────────────────


class TestRationalToLabel:
    def test_integer_2(self):
        assert rational_to_label(Rational(2, 1)) == "2R"

    def test_integer_3(self):
        assert rational_to_label(Rational(3, 1)) == "3R"

    def test_integer_4(self):
        assert rational_to_label(Rational(4, 1)) == "4R"

    def test_decimal_2_1(self):
        assert rational_to_label(Rational(21, 10)) == "2.1R"

    def test_decimal_2_25(self):
        assert rational_to_label(Rational(9, 4)) == "2.25R"

    def test_decimal_2_5(self):
        assert rational_to_label(Rational(5, 2)) == "2.5R"

    def test_decimal_3_75(self):
        assert rational_to_label(Rational(15, 4)) == "3.75R"

    def test_no_trailing_zeros(self):
        # 10/5 = 2.0 should display as "2R" not "2.0R"
        assert rational_to_label(Rational(10, 5)) == "2R"

    def test_precise_fraction(self):
        # 7/3 = 2.333... — Decimal division is exact for rationals
        label = rational_to_label(Rational(7, 3))
        assert label.endswith("R")
        assert "7" in label or "2.3" in label  # representation is valid


# ── Valid construction ───────────────────────────────────────────────────────


class TestValidConstruction:
    def test_integer_r2(self):
        kw = _base_kwargs(Rational(2, 1))
        o = TradeOutcomeV2(**kw)
        assert o.schema_version == "TradeOutcome/v2"
        assert o.selected_exit_target_r == Rational(2, 1)
        assert o.selected_exit_target_label == "2R"

    def test_decimal_2_1(self):
        r = Rational(21, 10)
        o = TradeOutcomeV2(**_base_kwargs(r))
        assert o.selected_exit_target_r == r
        assert o.selected_exit_target_label == "2.1R"

    def test_decimal_2_25(self):
        r = Rational(9, 4)
        o = TradeOutcomeV2(**_base_kwargs(r))
        assert o.selected_exit_target_r == r
        assert o.selected_exit_target_label == "2.25R"

    def test_decimal_2_5(self):
        r = Rational(5, 2)
        o = TradeOutcomeV2(**_base_kwargs(r))
        assert o.selected_exit_target_label == "2.5R"

    def test_decimal_3_75(self):
        r = Rational(15, 4)
        o = TradeOutcomeV2(**_base_kwargs(r))
        assert o.selected_exit_target_label == "3.75R"

    def test_stopped_outcome(self):
        r = Rational(5, 2)
        kw = _base_kwargs(
            r,
            outcome=TradeOutcomeStatus.STOPPED,
            realized_r=Rational(-1, 1),
        )
        kw["exit_price_ticks"] = 10000
        o = TradeOutcomeV2(**kw)
        assert o.outcome == TradeOutcomeStatus.STOPPED
        assert o.realized_r == Rational(-1, 1)

    def test_open_outcome_with_none_rationals(self):
        r = Rational(9, 4)
        kw = _base_kwargs(r, outcome=TradeOutcomeStatus.OPEN)
        kw.update(
            exit_bar_index=None,
            exit_bar_utc_ms=None,
            exit_price_ticks=None,
        )
        o = TradeOutcomeV2(**kw)
        assert o.exit_target_r is None
        assert o.highest_target_r is None
        assert o.realized_r is None

    def test_short_direction(self):
        r = Rational(5, 2)
        kw = _base_kwargs(r)
        kw["direction"] = Direction.SHORT
        o = TradeOutcomeV2(**kw)
        assert o.direction == Direction.SHORT

    def test_immutable(self):
        o = TradeOutcomeV2(**_base_kwargs(Rational(2, 1)))
        with pytest.raises(AttributeError):
            o.selected_exit_target_r = Rational(3, 1)


# ── Invalid construction ────────────────────────────────────────────────────


class TestInvalidConstruction:
    def test_zero_r_rejected(self):
        r = Rational(0, 1)
        # Rational(0, 1) is valid as a Rational but not positive
        with pytest.raises(ValueError, match="strictly positive"):
            TradeOutcomeV2(**_base_kwargs(r))

    def test_negative_r_rejected(self):
        r = Rational(-2, 1)
        with pytest.raises(ValueError, match="strictly positive"):
            TradeOutcomeV2(**_base_kwargs(r))

    def test_int_type_rejected(self):
        """selected_exit_target_r must be Rational, not int."""
        kw = _base_kwargs(Rational(2, 1))
        kw["selected_exit_target_r"] = 2
        with pytest.raises(TypeError, match="Rational"):
            TradeOutcomeV2(**kw)

    def test_float_type_rejected(self):
        """selected_exit_target_r must be Rational, not float."""
        kw = _base_kwargs(Rational(2, 1))
        kw["selected_exit_target_r"] = 2.5
        with pytest.raises(TypeError, match="Rational"):
            TradeOutcomeV2(**kw)

    def test_wrong_schema_version(self):
        kw = _base_kwargs(Rational(2, 1))
        kw["schema_version"] = "TradeOutcome/v1"
        with pytest.raises(ValueError, match="TradeOutcome/v2"):
            TradeOutcomeV2(**kw)

    def test_label_mismatch_rejected(self):
        kw = _base_kwargs(Rational(5, 2))
        kw["selected_exit_target_label"] = "3R"  # should be "2.5R"
        with pytest.raises(ValueError, match="2.5R"):
            TradeOutcomeV2(**kw)

    def test_exit_target_r_float_rejected(self):
        kw = _base_kwargs(Rational(2, 1))
        kw["exit_target_r"] = 2.0
        with pytest.raises(TypeError, match="Rational"):
            TradeOutcomeV2(**kw)

    def test_realized_r_float_rejected(self):
        kw = _base_kwargs(Rational(2, 1))
        kw["realized_r"] = 2.0
        with pytest.raises(TypeError, match="Rational"):
            TradeOutcomeV2(**kw)

    def test_highest_target_r_float_rejected(self):
        kw = _base_kwargs(Rational(2, 1))
        kw["highest_target_r"] = 2.0
        with pytest.raises(TypeError, match="Rational"):
            TradeOutcomeV2(**kw)


# ── Serialization ────────────────────────────────────────────────────────────


class TestSerialization:
    def test_integer_round_trip(self):
        r = Rational(2, 1)
        o = TradeOutcomeV2(**_base_kwargs(r))
        d = o.to_dict()
        assert d["selected_exit_target_r"] == {"numerator": 2, "denominator": 1}
        assert d["selected_exit_target_label"] == "2R"
        assert d["schema_version"] == "TradeOutcome/v2"
        # Reconstruct Rational from dict
        rd = d["selected_exit_target_r"]
        r2 = Rational(rd["numerator"], rd["denominator"])
        assert r2 == r

    def test_fractional_round_trip(self):
        r = Rational(9, 4)  # 2.25
        o = TradeOutcomeV2(**_base_kwargs(r))
        d = o.to_dict()
        assert d["selected_exit_target_r"] == {"numerator": 9, "denominator": 4}
        assert d["selected_exit_target_label"] == "2.25R"
        # Verify exit_target_r and realized_r also serialize correctly
        assert d["exit_target_r"] == {"numerator": 9, "denominator": 4}
        assert d["realized_r"] == {"numerator": 9, "denominator": 4}
        # Round trip
        rd = d["selected_exit_target_r"]
        r2 = Rational(rd["numerator"], rd["denominator"])
        assert r2 == r
        assert rational_to_label(r2) == "2.25R"

    def test_none_rationals_serialize(self):
        r = Rational(5, 2)
        kw = _base_kwargs(r, outcome=TradeOutcomeStatus.OPEN)
        kw.update(
            exit_bar_index=None,
            exit_bar_utc_ms=None,
            exit_price_ticks=None,
        )
        o = TradeOutcomeV2(**kw)
        d = o.to_dict()
        assert d["exit_target_r"] is None
        assert d["highest_target_r"] is None
        assert d["realized_r"] is None

    def test_3_75_precision(self):
        """Verify 3.75 survives serialize → deserialize without loss."""
        r = Rational(15, 4)
        o = TradeOutcomeV2(**_base_kwargs(r))
        d = o.to_dict()
        rd = d["selected_exit_target_r"]
        reconstructed = Rational(rd["numerator"], rd["denominator"])
        assert reconstructed.as_decimal() == r.as_decimal()
        assert rational_to_label(reconstructed) == "3.75R"


# ── v1 / v2 compatibility ───────────────────────────────────────────────────


class TestV1V2Compatibility:
    def test_v1_still_works(self):
        """v1 must continue to accept integer R values."""
        o = TradeOutcome(**_v1_target_hit_kwargs())
        assert o.selected_exit_target_r == 2
        assert o.selected_exit_target_label == "2R"

    def test_v1_rejects_decimal(self):
        """v1 must continue to reject non-{2,3,4} values."""
        kw = _v1_target_hit_kwargs()
        kw["selected_exit_target_r"] = 5
        with pytest.raises(ValueError, match="2, 3, or 4"):
            TradeOutcome(**kw)

    def test_v1_rejects_2_5_as_float(self):
        """v1 must reject float values entirely."""
        kw = _v1_target_hit_kwargs()
        kw["selected_exit_target_r"] = 2.5
        with pytest.raises(TypeError, match="int"):
            TradeOutcome(**kw)

    def test_v2_accepts_2_5(self):
        """v2 must accept 2.5 as Rational(5, 2)."""
        r = Rational(5, 2)
        o = TradeOutcomeV2(**_base_kwargs(r))
        assert o.selected_exit_target_r == r
        assert o.selected_exit_target_label == "2.5R"

    def test_v2_accepts_classic_integers(self):
        """v2 accepts 2, 3, 4 as Rational(n, 1)."""
        for n in (2, 3, 4):
            r = Rational(n, 1)
            o = TradeOutcomeV2(**_base_kwargs(r))
            assert o.selected_exit_target_label == f"{n}R"

    def test_v1_and_v2_coexist(self):
        """Both v1 and v2 can be constructed in the same process."""
        v1 = TradeOutcome(**_v1_target_hit_kwargs())
        v2 = TradeOutcomeV2(**_base_kwargs(Rational(5, 2)))
        assert v1.schema_version == "TradeOutcome/v1"
        assert v2.schema_version == "TradeOutcome/v2"
        assert v1.selected_exit_target_r == 2
        assert v2.selected_exit_target_r == Rational(5, 2)
