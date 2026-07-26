"""Tests for canonical DetectionResult/v1 contract type.

Covers:
  - Complete valid VALID result
  - Complete valid INVALID result
  - Exact preservation of all fields
  - Exact serialization key order and shape
  - Serialization of every nested contract type
  - Empty required arrays
  - Nullable fields
  - Immutability
  - Deterministic equality
  - Wrong schema_version
  - Missing required values
  - Invalid enum values
  - Invalid primitive types
  - Boolean rejection for integer fields
  - Invalid nested contract types
  - Invalid collection types
  - Invalid collection members
  - Package export
"""

import pytest

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.distances import (
    AbsoluteTickDistance,
    DirectionalTickDistance,
)
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
    LevelSource,
)
from trading_lab.contracts.primitives import PriceTicks, Rational
from trading_lab.contracts.rule_failure import RejectionAttempt, RuleFailure
from trading_lab.contracts.session_metadata import SessionMetadata
from trading_lab.contracts.enums import Stage, ValueType


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK_SIZE = "0.01"
BAR_MS = 1748264400000  # 2026-05-26 09:30 ET


def _pt(ticks: int) -> PriceTicks:
    return PriceTicks(ticks=ticks, tick_size=TICK_SIZE)


def _bar(ms: int = BAR_MS) -> Bar:
    return Bar(
        bar_utc_ms=ms,
        open=_pt(52500), high=_pt(52550),
        low=_pt(52480), close=_pt(52530),
    )


def _session() -> SessionMetadata:
    return SessionMetadata(
        symbol="SPY",
        date="2026-05-26",
        market_timezone="America/New_York",
        session_open_utc_ms=1748264400000,
        session_close_utc_ms=1748287800000,
        timeframe_seconds=300,
    )


def _rule_failure() -> RuleFailure:
    return RuleFailure(
        rule_id="REJECTION_WICK_RATIO_TOO_LOW",
        stage=Stage.REJECTION_CANDLE,
        value_type=ValueType.BOOLEAN,
        actual_value=None, operator=None,
        required_value=None, unit=None,
        message="REJECTION_WICK_RATIO_TOO_LOW",
    )


def _rejection_attempt() -> RejectionAttempt:
    return RejectionAttempt(bar=_bar(), failed_rules=(_rule_failure(),))


def _valid_kwargs() -> dict:
    """All fields for a minimal VALID DetectionResult."""
    return dict(
        schema_version="DetectionResult/v1",
        result_id="a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        produced_at="2026-05-26T14:05:00.000Z",
        session=_session(),
        preset_id="bdrr_spy_v1",
        engine_version="1.0.0",
        status=DetectionStatus.VALID,
        failed_stage=None,
        failed_rules=(),
        level_price=_pt(75044),
        level_source=LevelSource.ORB_HIGH,
        level_bar=_bar(),
        direction=Direction.LONG,
        break_bar=_bar(BAR_MS + 300000),
        directional_break_distance=DirectionalTickDistance(
            ticks=19, tick_size=TICK_SIZE
        ),
        displacement_window=(_bar(BAR_MS + 600000),),
        displacement_bar_count=1,
        displacement_pts=AbsoluteTickDistance(ticks=68, tick_size=TICK_SIZE),
        displacement_pct=Rational(numerator=68, denominator=75044),
        rejection_side_clearance_by_bar=(
            DirectionalTickDistance(ticks=20, tick_size=TICK_SIZE),
        ),
        minimum_rejection_side_clearance=DirectionalTickDistance(
            ticks=20, tick_size=TICK_SIZE
        ),
        average_rejection_side_clearance="0.20",
        retest_window=(_bar(BAR_MS + 900000),),
        retest_bar_count=1,
        failed_retest_count=1,
        failed_retests=(_rejection_attempt(),),
        bars_break_to_first_retest=2,
        bars_break_to_confirmation=15,
        retest_closest_approach=AbsoluteTickDistance(
            ticks=0, tick_size=TICK_SIZE
        ),
        retest_penetration_through_level=AbsoluteTickDistance(
            ticks=86, tick_size=TICK_SIZE
        ),
        retest_displacement_retracement_pct=Rational(
            numerator=86, denominator=68
        ),
        confirmation_bar=_bar(BAR_MS + 1200000),
        confirmation_rej_wick=Rational(numerator=670000, denominator=1000000),
        confirmation_body=Rational(numerator=200000, denominator=1000000),
        confirmation_opp_wick=Rational(numerator=130000, denominator=1000000),
        confirmation_favorable_close_location=Rational(
            numerator=870000, denominator=1000000
        ),
        confirmation_penetration=AbsoluteTickDistance(
            ticks=7, tick_size=TICK_SIZE
        ),
        confirmation_close_beyond_level=DirectionalTickDistance(
            ticks=45, tick_size=TICK_SIZE
        ),
    )


def _invalid_kwargs() -> dict:
    """All fields for a minimal INVALID DetectionResult."""
    kw = _valid_kwargs()
    kw.update(
        status=DetectionStatus.INVALID,
        failed_stage=FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
        failed_rules=(),
        # Nullable fields set to None for INVALID
        confirmation_bar=None,
        confirmation_rej_wick=None,
        confirmation_body=None,
        confirmation_opp_wick=None,
        confirmation_favorable_close_location=None,
        confirmation_penetration=None,
        confirmation_close_beyond_level=None,
        bars_break_to_confirmation=None,
    )
    return kw


def make_dr(**overrides) -> DetectionResult:
    kw = _valid_kwargs()
    kw.update(overrides)
    return DetectionResult(**kw)


# ═══════════════════════════════════════════════════════════════════════════════
# Valid construction
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidConstruction:
    def test_valid_result(self):
        dr = make_dr()
        assert dr.schema_version == "DetectionResult/v1"
        assert dr.status == DetectionStatus.VALID
        assert dr.failed_stage is None
        assert dr.failed_rules == ()

    def test_invalid_result(self):
        dr = DetectionResult(**_invalid_kwargs())
        assert dr.status == DetectionStatus.INVALID
        assert dr.failed_stage == FailedStage.NO_QUALIFYING_REJECTION_CANDLE
        assert dr.confirmation_bar is None

    def test_minimal_valid_all_nulls(self):
        """VALID with all nullable fields as None and empty arrays."""
        dr = make_dr(
            level_price=None, level_source=None, level_bar=None,
            direction=None,
            break_bar=None, directional_break_distance=None,
            displacement_window=(), displacement_bar_count=None,
            displacement_pts=None, displacement_pct=None,
            rejection_side_clearance_by_bar=None,
            minimum_rejection_side_clearance=None,
            average_rejection_side_clearance=None,
            retest_window=(), retest_bar_count=None,
            failed_retest_count=None, failed_retests=(),
            bars_break_to_first_retest=None,
            bars_break_to_confirmation=None,
            retest_closest_approach=None,
            retest_penetration_through_level=None,
            retest_displacement_retracement_pct=None,
            confirmation_bar=None, confirmation_rej_wick=None,
            confirmation_body=None, confirmation_opp_wick=None,
            confirmation_favorable_close_location=None,
            confirmation_penetration=None,
            confirmation_close_beyond_level=None,
        )
        assert dr.status == DetectionStatus.VALID
        assert dr.displacement_window == ()
        assert dr.retest_window == ()


# ═══════════════════════════════════════════════════════════════════════════════
# Field preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestFieldPreservation:
    def test_identity_fields(self):
        dr = make_dr()
        assert dr.result_id == "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
        assert dr.produced_at == "2026-05-26T14:05:00.000Z"
        assert dr.preset_id == "bdrr_spy_v1"
        assert dr.engine_version == "1.0.0"

    def test_session_preserved(self):
        session = _session()
        dr = make_dr(session=session)
        assert dr.session is session

    def test_level_fields(self):
        dr = make_dr()
        assert dr.level_price == _pt(75044)
        assert dr.level_source == LevelSource.ORB_HIGH
        assert dr.direction == Direction.LONG

    def test_displacement_bar_count(self):
        dr = make_dr(displacement_bar_count=3)
        assert dr.displacement_bar_count == 3

    def test_retest_counts(self):
        dr = make_dr(retest_bar_count=5, failed_retest_count=2)
        assert dr.retest_bar_count == 5
        assert dr.failed_retest_count == 2

    def test_bars_counts(self):
        dr = make_dr(
            bars_break_to_first_retest=3,
            bars_break_to_confirmation=10,
        )
        assert dr.bars_break_to_first_retest == 3
        assert dr.bars_break_to_confirmation == 10

    def test_average_rejection_side_clearance(self):
        dr = make_dr(average_rejection_side_clearance="0.15")
        assert dr.average_rejection_side_clearance == "0.15"


# ═══════════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_cannot_set_status(self):
        dr = make_dr()
        with pytest.raises(AttributeError):
            dr.status = DetectionStatus.INVALID  # type: ignore[misc]

    def test_cannot_set_schema_version(self):
        dr = make_dr()
        with pytest.raises(AttributeError):
            dr.schema_version = "X"  # type: ignore[misc]

    def test_cannot_set_level_price(self):
        dr = make_dr()
        with pytest.raises(AttributeError):
            dr.level_price = None  # type: ignore[misc]

    def test_cannot_set_displacement_window(self):
        dr = make_dr()
        with pytest.raises(AttributeError):
            dr.displacement_window = ()  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic equality
# ═══════════════════════════════════════════════════════════════════════════════


class TestEquality:
    def test_equal(self):
        a = make_dr()
        b = make_dr()
        assert a == b

    def test_not_equal_status(self):
        a = make_dr()
        b = DetectionResult(**_invalid_kwargs())
        assert a != b

    def test_hash_equal(self):
        a = make_dr()
        b = make_dr()
        assert hash(a) == hash(b)


# ═══════════════════════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialization:
    def test_all_keys_present(self):
        dr = make_dr()
        d = dr.to_dict()
        expected_keys = {
            "schema_version", "result_id", "produced_at", "session",
            "preset_id", "engine_version",
            "status", "failed_stage", "failed_rules",
            "level_price", "level_source", "level_bar", "direction",
            "break_bar", "directional_break_distance",
            "displacement_window", "displacement_bar_count",
            "displacement_pts", "displacement_pct",
            "rejection_side_clearance_by_bar",
            "minimum_rejection_side_clearance",
            "average_rejection_side_clearance",
            "retest_window", "retest_bar_count",
            "failed_retest_count", "failed_retests",
            "bars_break_to_first_retest", "bars_break_to_confirmation",
            "retest_closest_approach",
            "retest_penetration_through_level",
            "retest_displacement_retracement_pct",
            "confirmation_bar", "confirmation_rej_wick",
            "confirmation_body", "confirmation_opp_wick",
            "confirmation_favorable_close_location",
            "confirmation_penetration",
            "confirmation_close_beyond_level",
        }
        assert set(d.keys()) == expected_keys

    def test_key_count(self):
        """38 fields in DetectionResult/v1."""
        dr = make_dr()
        assert len(dr.to_dict()) == 38

    def test_schema_version_serialized(self):
        dr = make_dr()
        assert dr.to_dict()["schema_version"] == "DetectionResult/v1"

    def test_status_serialized_as_string(self):
        dr = make_dr()
        assert dr.to_dict()["status"] == "VALID"
        assert isinstance(dr.to_dict()["status"], str)

    def test_failed_stage_null_serialized(self):
        dr = make_dr()
        assert dr.to_dict()["failed_stage"] is None

    def test_failed_stage_enum_serialized(self):
        dr = DetectionResult(**_invalid_kwargs())
        assert dr.to_dict()["failed_stage"] == "NO_QUALIFYING_REJECTION_CANDLE"

    def test_failed_rules_empty_array(self):
        dr = make_dr()
        assert dr.to_dict()["failed_rules"] == []

    def test_level_source_serialized(self):
        dr = make_dr()
        assert dr.to_dict()["level_source"] == "ORB_HIGH"

    def test_direction_serialized(self):
        dr = make_dr()
        assert dr.to_dict()["direction"] == "LONG"

    def test_session_nested_dict(self):
        dr = make_dr()
        s = dr.to_dict()["session"]
        assert isinstance(s, dict)
        assert s["symbol"] == "SPY"
        assert s["timeframe_seconds"] == 300

    def test_level_price_nested_dict(self):
        dr = make_dr()
        lp = dr.to_dict()["level_price"]
        assert isinstance(lp, dict)
        assert lp["ticks"] == 75044
        assert lp["tick_size"] == TICK_SIZE

    def test_break_bar_nested(self):
        dr = make_dr()
        bb = dr.to_dict()["break_bar"]
        assert isinstance(bb, dict)
        assert "bar_utc_ms" in bb
        assert "open" in bb

    def test_directional_break_distance_nested(self):
        dr = make_dr()
        dbd = dr.to_dict()["directional_break_distance"]
        assert dbd == {"ticks": 19, "tick_size": TICK_SIZE}

    def test_displacement_window_array(self):
        dr = make_dr()
        dw = dr.to_dict()["displacement_window"]
        assert isinstance(dw, list)
        assert len(dw) == 1
        assert isinstance(dw[0], dict)

    def test_displacement_pts_nested(self):
        dr = make_dr()
        dp = dr.to_dict()["displacement_pts"]
        assert dp == {"ticks": 68, "tick_size": TICK_SIZE}

    def test_displacement_pct_nested(self):
        dr = make_dr()
        dp = dr.to_dict()["displacement_pct"]
        assert dp == {"numerator": 68, "denominator": 75044}

    def test_rejection_side_clearance_array(self):
        dr = make_dr()
        rsc = dr.to_dict()["rejection_side_clearance_by_bar"]
        assert isinstance(rsc, list)
        assert rsc[0] == {"ticks": 20, "tick_size": TICK_SIZE}

    def test_rejection_side_clearance_null(self):
        dr = make_dr(rejection_side_clearance_by_bar=None)
        assert dr.to_dict()["rejection_side_clearance_by_bar"] is None

    def test_failed_retests_nested(self):
        dr = make_dr()
        fr = dr.to_dict()["failed_retests"]
        assert isinstance(fr, list)
        assert len(fr) == 1
        assert "bar" in fr[0]
        assert "failed_rules" in fr[0]

    def test_confirmation_rationals_nested(self):
        dr = make_dr()
        d = dr.to_dict()
        assert d["confirmation_rej_wick"] == {
            "numerator": 670000, "denominator": 1000000
        }
        assert d["confirmation_body"] == {
            "numerator": 200000, "denominator": 1000000
        }

    def test_confirmation_penetration_nested(self):
        dr = make_dr()
        cp = dr.to_dict()["confirmation_penetration"]
        assert cp == {"ticks": 7, "tick_size": TICK_SIZE}

    def test_confirmation_close_beyond_level_nested(self):
        dr = make_dr()
        ccbl = dr.to_dict()["confirmation_close_beyond_level"]
        assert ccbl == {"ticks": 45, "tick_size": TICK_SIZE}

    def test_null_fields_present_in_output(self):
        dr = make_dr(
            level_price=None, break_bar=None,
            confirmation_bar=None, confirmation_rej_wick=None,
        )
        d = dr.to_dict()
        assert "level_price" in d and d["level_price"] is None
        assert "break_bar" in d and d["break_bar"] is None
        assert "confirmation_bar" in d and d["confirmation_bar"] is None
        assert "confirmation_rej_wick" in d and d["confirmation_rej_wick"] is None

    def test_empty_arrays_serialized(self):
        dr = make_dr(
            displacement_window=(), retest_window=(),
            failed_retests=(), failed_rules=(),
        )
        d = dr.to_dict()
        assert d["displacement_window"] == []
        assert d["retest_window"] == []
        assert d["failed_retests"] == []
        assert d["failed_rules"] == []

    def test_average_rejection_side_clearance_string(self):
        dr = make_dr(average_rejection_side_clearance="0.20")
        assert dr.to_dict()["average_rejection_side_clearance"] == "0.20"

    def test_integer_fields_serialized(self):
        dr = make_dr()
        d = dr.to_dict()
        assert isinstance(d["displacement_bar_count"], int)
        assert isinstance(d["bars_break_to_first_retest"], int)


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid schema_version
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidSchemaVersion:
    def test_wrong_version(self):
        with pytest.raises(ValueError, match="DetectionResult/v1"):
            make_dr(schema_version="DetectionResult/v2")

    def test_empty_version(self):
        with pytest.raises(ValueError, match="DetectionResult/v1"):
            make_dr(schema_version="")

    def test_none_version(self):
        with pytest.raises(ValueError, match="DetectionResult/v1"):
            make_dr(schema_version=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid identity fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidIdentityFields:
    def test_result_id_none(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_dr(result_id=None)

    def test_result_id_empty(self):
        with pytest.raises(ValueError, match="non-empty"):
            make_dr(result_id="")

    def test_result_id_int(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_dr(result_id=123)

    def test_produced_at_none(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_dr(produced_at=None)

    def test_produced_at_empty(self):
        with pytest.raises(ValueError, match="non-empty"):
            make_dr(produced_at="")

    def test_preset_id_none(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_dr(preset_id=None)

    def test_engine_version_empty(self):
        with pytest.raises(ValueError, match="non-empty"):
            make_dr(engine_version="")


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid enum values
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidEnums:
    def test_status_string(self):
        with pytest.raises(TypeError, match="DetectionStatus"):
            make_dr(status="VALID")

    def test_status_none(self):
        with pytest.raises(TypeError, match="DetectionStatus"):
            make_dr(status=None)

    def test_failed_stage_string(self):
        with pytest.raises(TypeError, match="FailedStage"):
            make_dr(failed_stage="BREAK_NOT_FOUND")

    def test_level_source_string(self):
        with pytest.raises(TypeError, match="LevelSource"):
            make_dr(level_source="ORB_HIGH")

    def test_direction_string(self):
        with pytest.raises(TypeError, match="Direction"):
            make_dr(direction="LONG")


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid session
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidSession:
    def test_session_none(self):
        with pytest.raises(TypeError, match="SessionMetadata"):
            make_dr(session=None)

    def test_session_dict(self):
        with pytest.raises(TypeError, match="SessionMetadata"):
            make_dr(session={"symbol": "SPY"})


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid nested types
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidNestedTypes:
    def test_level_price_dict(self):
        with pytest.raises(TypeError, match="PriceTicks"):
            make_dr(level_price={"ticks": 100, "tick_size": "0.01"})

    def test_level_bar_dict(self):
        with pytest.raises(TypeError, match="Bar"):
            make_dr(level_bar={"bar_utc_ms": 0})

    def test_break_bar_int(self):
        with pytest.raises(TypeError, match="Bar"):
            make_dr(break_bar=42)

    def test_directional_break_distance_dict(self):
        with pytest.raises(TypeError, match="DirectionalTickDistance"):
            make_dr(directional_break_distance={"ticks": 19})

    def test_displacement_pts_dict(self):
        with pytest.raises(TypeError, match="AbsoluteTickDistance"):
            make_dr(displacement_pts={"ticks": 68})

    def test_displacement_pct_dict(self):
        with pytest.raises(TypeError, match="Rational"):
            make_dr(displacement_pct={"numerator": 1, "denominator": 2})

    def test_minimum_rejection_side_clearance_int(self):
        with pytest.raises(TypeError, match="DirectionalTickDistance"):
            make_dr(minimum_rejection_side_clearance=20)

    def test_confirmation_bar_dict(self):
        with pytest.raises(TypeError, match="Bar"):
            make_dr(confirmation_bar={"bar_utc_ms": 0})

    def test_confirmation_rej_wick_float(self):
        with pytest.raises(TypeError, match="Rational"):
            make_dr(confirmation_rej_wick=0.67)

    def test_confirmation_penetration_dict(self):
        with pytest.raises(TypeError, match="AbsoluteTickDistance"):
            make_dr(confirmation_penetration={"ticks": 7})

    def test_retest_closest_approach_dict(self):
        with pytest.raises(TypeError, match="AbsoluteTickDistance"):
            make_dr(retest_closest_approach={"ticks": 0})


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid integer fields (boolean rejection)
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidIntegers:
    def test_displacement_bar_count_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_dr(displacement_bar_count=True)

    def test_retest_bar_count_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_dr(retest_bar_count=False)

    def test_failed_retest_count_float(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_dr(failed_retest_count=1.5)

    def test_bars_break_to_first_retest_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_dr(bars_break_to_first_retest=True)

    def test_bars_break_to_confirmation_str(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_dr(bars_break_to_confirmation="10")


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid collections
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidCollections:
    def test_failed_rules_list(self):
        with pytest.raises(TypeError, match="must be a tuple"):
            make_dr(failed_rules=[])

    def test_failed_rules_contains_non_rule(self):
        with pytest.raises(TypeError, match="RuleFailure"):
            make_dr(failed_rules=("not_a_rule",))

    def test_displacement_window_list(self):
        with pytest.raises(TypeError, match="must be a tuple"):
            make_dr(displacement_window=[_bar()])

    def test_displacement_window_contains_non_bar(self):
        with pytest.raises(TypeError, match="Bar"):
            make_dr(displacement_window=({"bar_utc_ms": 0},))

    def test_retest_window_list(self):
        with pytest.raises(TypeError, match="must be a tuple"):
            make_dr(retest_window=[])

    def test_failed_retests_list(self):
        with pytest.raises(TypeError, match="must be a tuple"):
            make_dr(failed_retests=[])

    def test_failed_retests_contains_non_attempt(self):
        with pytest.raises(TypeError, match="RejectionAttempt"):
            make_dr(failed_retests=("not_an_attempt",))

    def test_rejection_side_clearance_list(self):
        with pytest.raises(TypeError, match="must be a tuple"):
            make_dr(rejection_side_clearance_by_bar=[
                DirectionalTickDistance(ticks=1, tick_size=TICK_SIZE)
            ])

    def test_rejection_side_clearance_contains_non_dtd(self):
        with pytest.raises(TypeError, match="DirectionalTickDistance"):
            make_dr(rejection_side_clearance_by_bar=(42,))


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid Decimal string field
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidDecimalField:
    def test_average_rejection_side_clearance_float(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_dr(average_rejection_side_clearance=0.20)

    def test_average_rejection_side_clearance_int(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_dr(average_rejection_side_clearance=0)


# ═══════════════════════════════════════════════════════════════════════════════
# Package export
# ═══════════════════════════════════════════════════════════════════════════════


class TestPackageExport:
    def test_import(self):
        from trading_lab.contracts import DetectionResult as DR
        assert DR is DetectionResult
