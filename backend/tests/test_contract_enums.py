"""Tests for canonical enum/literal vocabularies."""

import pytest

from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    EntryPatternType,
    FailedStage,
    LevelSource,
    Operator,
    Stage,
    ValueType,
    ZoneStatus,
    ZoneType,
)


class TestDetectionStatus:
    def test_values(self):
        assert DetectionStatus.VALID == "VALID"
        assert DetectionStatus.INVALID == "INVALID"

    def test_member_count(self):
        assert len(DetectionStatus) == 2

    def test_string_comparison(self):
        assert DetectionStatus.VALID == "VALID"
        assert DetectionStatus.INVALID == "INVALID"

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            DetectionStatus("UNKNOWN")


class TestFailedStage:
    def test_all_values(self):
        expected = {
            "LEVEL_NOT_FOUND",
            "BREAK_NOT_FOUND",
            "DISPLACEMENT_MINIMUM_NOT_MET",
            "RETEST_BEFORE_DISPLACEMENT",
            "RETEST_NOT_FOUND",
            "NO_QUALIFYING_REJECTION_CANDLE",
            "SEQUENCE_INVALIDATED",
        }
        assert {m.value for m in FailedStage} == expected

    def test_member_count(self):
        assert len(FailedStage) == 7

    def test_string_comparison(self):
        assert FailedStage.RETEST_BEFORE_DISPLACEMENT == "RETEST_BEFORE_DISPLACEMENT"

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            FailedStage("NOT_A_STAGE")


class TestLevelSource:
    def test_all_values(self):
        expected = {"ORB_HIGH", "ORB_LOW", "PREVIOUS_DAY_HIGH", "PREVIOUS_DAY_LOW", "PMH", "PML", "OB", "SR"}
        assert {m.value for m in LevelSource} == expected

    def test_member_count(self):
        assert len(LevelSource) == 8

    def test_string_comparison(self):
        assert LevelSource.ORB_HIGH == "ORB_HIGH"

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            LevelSource("VWAP")


class TestDirection:
    def test_values(self):
        assert Direction.LONG == "LONG"
        assert Direction.SHORT == "SHORT"

    def test_member_count(self):
        assert len(Direction) == 2

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            Direction("FLAT")


class TestStage:
    def test_all_values(self):
        expected = {"LEVEL", "BREAK", "DISPLACEMENT", "RETEST", "REJECTION_CANDLE"}
        assert {m.value for m in Stage} == expected

    def test_member_count(self):
        assert len(Stage) == 5

    def test_string_comparison(self):
        assert Stage.REJECTION_CANDLE == "REJECTION_CANDLE"


class TestValueType:
    def test_all_values(self):
        expected = {"DECIMAL", "INTEGER", "BOOLEAN", "ENUM", "MISSING"}
        assert {m.value for m in ValueType} == expected

    def test_member_count(self):
        assert len(ValueType) == 5


class TestOperator:
    def test_all_values(self):
        expected = {"GT", "GTE", "LT", "LTE", "EQ", "NEQ"}
        assert {m.value for m in Operator} == expected

    def test_member_count(self):
        assert len(Operator) == 6

    def test_string_comparison(self):
        assert Operator.GTE == "GTE"

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            Operator("BETWEEN")


class TestEntryPatternType:
    def test_all_values(self):
        expected = {
            "SINGLE_CANDLE_REJECTION",
            "TWO_CANDLE_ENGULFING_RECOVERY",
            "RETEST_STRUCTURE",
        }
        assert {m.value for m in EntryPatternType} == expected

    def test_member_count(self):
        assert len(EntryPatternType) == 3

    def test_string_comparison(self):
        assert EntryPatternType.TWO_CANDLE_ENGULFING_RECOVERY == "TWO_CANDLE_ENGULFING_RECOVERY"

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            EntryPatternType("UNKNOWN_PATTERN")


class TestZoneType:
    def test_all_values(self):
        expected = {
            "VALIDATED_PIVOT_ZONE",
            "COMPOSITE_CONFLUENCE_ZONE",
        }
        assert {m.value for m in ZoneType} == expected

    def test_member_count(self):
        assert len(ZoneType) == 2

    def test_string_comparison(self):
        assert ZoneType.VALIDATED_PIVOT_ZONE == "VALIDATED_PIVOT_ZONE"

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            ZoneType("NOT_A_ZONE")


class TestZoneStatus:
    def test_all_values(self):
        expected = {"ACTIVE", "SECONDARY", "STORED"}
        assert {m.value for m in ZoneStatus} == expected

    def test_member_count(self):
        assert len(ZoneStatus) == 3

    def test_stored_and_secondary_distinct(self):
        assert ZoneStatus.STORED != ZoneStatus.SECONDARY
        assert ZoneStatus.STORED.value != ZoneStatus.SECONDARY.value

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            ZoneStatus("EXPIRED")


class TestPackageExports:
    def test_all_enums_importable(self):
        from trading_lab.contracts import (
            DetectionStatus,
            Direction,
            EntryPatternType,
            FailedStage,
            LevelSource,
            Operator,
            Stage,
            ValueType,
            ZoneStatus,
            ZoneType,
        )
        assert DetectionStatus.VALID == "VALID"
        assert Direction.LONG == "LONG"
        assert FailedStage.BREAK_NOT_FOUND == "BREAK_NOT_FOUND"
        assert LevelSource.ORB_HIGH == "ORB_HIGH"
        assert Operator.GT == "GT"
        assert Stage.LEVEL == "LEVEL"
        assert ValueType.DECIMAL == "DECIMAL"
        assert EntryPatternType.SINGLE_CANDLE_REJECTION == "SINGLE_CANDLE_REJECTION"
        assert ZoneType.VALIDATED_PIVOT_ZONE == "VALIDATED_PIVOT_ZONE"
        assert ZoneStatus.ACTIVE == "ACTIVE"

    def test_new_contracts_importable(self):
        from trading_lab.contracts import (
            CompositeZone,
            EntryPatternResult,
            ZoneComponent,
        )
        assert CompositeZone is not None
        assert EntryPatternResult is not None
        assert ZoneComponent is not None
