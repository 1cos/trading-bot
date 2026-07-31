"""Tests for canonical DetectorAuditRecord/v1 contract type.

Covers:
  - Minimal valid VALID record
  - Minimal valid REJECTED record
  - Exact schema version
  - Invalid UUID (empty / wrong type)
  - Blank symbol
  - Invalid date
  - Invalid timeframe
  - Invalid direction
  - Invalid candidate_status
  - VALID with failed_stage
  - VALID with failed_rules
  - REJECTED with neither failed_stage nor failed_rules
  - Each invalid progression-flag combination
  - ORB high without ORB low
  - ORB low without ORB high
  - orb_high <= orb_low
  - Invalid timestamp values
  - VALID audit record with INVALID DetectionResult
  - REJECTED audit record with VALID DetectionResult
  - Immutability
  - Deterministic serialization / equality
  - Package export
"""

import pytest

from trading_lab.contracts.detector_audit_record import (
    CandidateStatus,
    DetectorAuditRecord,
)
from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
    Stage,
    ValueType,
)
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.contracts.rule_failure import RuleFailure

# Reuse existing detection result fixtures
from test_contract_detection_result import (
    _valid_kwargs as _dr_valid_kwargs,
    _invalid_kwargs as _dr_invalid_kwargs,
    _pt,
    _rule_failure,
)


TICK_SIZE = "0.01"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _valid_detection_result() -> DetectionResult:
    return DetectionResult(**_dr_valid_kwargs())


def _invalid_detection_result() -> DetectionResult:
    return DetectionResult(**_dr_invalid_kwargs())


def _valid_audit_kwargs() -> dict:
    """All fields for a minimal VALID DetectorAuditRecord."""
    return dict(
        schema_version="DetectorAuditRecord/v1",
        audit_id="a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        symbol="SPY",
        session_date="2026-07-24",
        timeframe="5m",
        direction=Direction.LONG,
        candidate_status=CandidateStatus.VALID,
        failed_stage=None,
        failed_rules=(),
        reached_orb=True,
        reached_break=True,
        reached_displacement=True,
        reached_retest=True,
        reached_rejection_scan=True,
        orb_high=PriceTicks(ticks=55000, tick_size=TICK_SIZE),
        orb_low=PriceTicks(ticks=54900, tick_size=TICK_SIZE),
        orb_candle_time_ms=1753365000000,
        break_candle_time_ms=1753365300000,
        last_relevant_time_ms=1753369200000,
        detection_result=_valid_detection_result(),
    )


def _rejected_audit_kwargs() -> dict:
    """All fields for a minimal REJECTED DetectorAuditRecord."""
    return dict(
        schema_version="DetectorAuditRecord/v1",
        audit_id="b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
        symbol="QQQ",
        session_date="2026-07-25",
        timeframe="3m",
        direction=Direction.SHORT,
        candidate_status=CandidateStatus.REJECTED,
        failed_stage=FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
        failed_rules=(),
        reached_orb=True,
        reached_break=True,
        reached_displacement=True,
        reached_retest=True,
        reached_rejection_scan=True,
        orb_high=PriceTicks(ticks=48500, tick_size=TICK_SIZE),
        orb_low=PriceTicks(ticks=48400, tick_size=TICK_SIZE),
        orb_candle_time_ms=1753451400000,
        break_candle_time_ms=1753451700000,
        last_relevant_time_ms=1753455600000,
        detection_result=_invalid_detection_result(),
    )


def make_valid(**overrides) -> DetectorAuditRecord:
    kw = _valid_audit_kwargs()
    kw.update(overrides)
    return DetectorAuditRecord(**kw)


def make_rejected(**overrides) -> DetectorAuditRecord:
    kw = _rejected_audit_kwargs()
    kw.update(overrides)
    return DetectorAuditRecord(**kw)


# ═══════════════════════════════════════════════════════════════════════════════
# Valid construction
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidConstruction:
    def test_valid_record(self):
        r = make_valid()
        assert r.schema_version == "DetectorAuditRecord/v1"
        assert r.audit_id == "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
        assert r.symbol == "SPY"
        assert r.session_date == "2026-07-24"
        assert r.timeframe == "5m"
        assert r.direction == Direction.LONG
        assert r.candidate_status == CandidateStatus.VALID
        assert r.failed_stage is None
        assert r.failed_rules == ()
        assert r.reached_orb is True
        assert r.reached_break is True
        assert r.reached_displacement is True
        assert r.reached_retest is True
        assert r.reached_rejection_scan is True
        assert isinstance(r.orb_high, PriceTicks)
        assert isinstance(r.orb_low, PriceTicks)
        assert r.orb_candle_time_ms == 1753365000000
        assert r.break_candle_time_ms == 1753365300000
        assert r.last_relevant_time_ms == 1753369200000
        assert isinstance(r.detection_result, DetectionResult)
        assert str(r.detection_result.status) == "VALID"

    def test_rejected_record(self):
        r = make_rejected()
        assert r.schema_version == "DetectorAuditRecord/v1"
        assert r.candidate_status == CandidateStatus.REJECTED
        assert r.failed_stage == FailedStage.NO_QUALIFYING_REJECTION_CANDLE
        assert r.direction == Direction.SHORT
        assert str(r.detection_result.status) == "INVALID"

    def test_rejected_with_failed_rules_only(self):
        """REJECTED record with no failed_stage but non-empty failed_rules."""
        rf = _rule_failure()
        r = make_rejected(failed_stage=None, failed_rules=(rf,))
        assert r.candidate_status == CandidateStatus.REJECTED
        assert r.failed_stage is None
        assert len(r.failed_rules) == 1

    def test_rejected_with_both_indicators(self):
        """REJECTED with both failed_stage and failed_rules."""
        rf = _rule_failure()
        r = make_rejected(
            failed_stage=FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
            failed_rules=(rf,),
        )
        assert r.failed_stage is not None
        assert len(r.failed_rules) == 1

    def test_null_timestamps(self):
        """All timestamps can be None."""
        r = make_valid(
            orb_candle_time_ms=None,
            break_candle_time_ms=None,
            last_relevant_time_ms=None,
        )
        assert r.orb_candle_time_ms is None
        assert r.break_candle_time_ms is None
        assert r.last_relevant_time_ms is None

    def test_null_orb_prices(self):
        """Both ORB prices can be None (ORB not reached)."""
        r = make_rejected(
            orb_high=None,
            orb_low=None,
            reached_orb=False,
            reached_break=False,
            reached_displacement=False,
            reached_retest=False,
            reached_rejection_scan=False,
        )
        assert r.orb_high is None
        assert r.orb_low is None

    def test_minimal_progression_flags(self):
        """All progression flags False is valid (for REJECTED)."""
        r = make_rejected(
            orb_high=None,
            orb_low=None,
            orb_candle_time_ms=None,
            break_candle_time_ms=None,
            reached_orb=False,
            reached_break=False,
            reached_displacement=False,
            reached_retest=False,
            reached_rejection_scan=False,
        )
        assert r.reached_orb is False


# ═══════════════════════════════════════════════════════════════════════════════
# Schema version
# ═══════════════════════════════════════════════════════════════════════════════


class TestSchemaVersion:
    def test_wrong_version(self):
        with pytest.raises(ValueError, match="DetectorAuditRecord/v1"):
            make_valid(schema_version="DetectorAuditRecord/v2")

    def test_empty_version(self):
        with pytest.raises(ValueError, match="DetectorAuditRecord/v1"):
            make_valid(schema_version="")

    def test_wrong_type(self):
        with pytest.raises(TypeError, match="schema_version must be a str"):
            make_valid(schema_version=1)


# ═══════════════════════════════════════════════════════════════════════════════
# Identity fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuditId:
    def test_empty_audit_id(self):
        with pytest.raises(ValueError, match="audit_id.*non-empty"):
            make_valid(audit_id="")

    def test_wrong_type(self):
        with pytest.raises(TypeError, match="audit_id must be a str"):
            make_valid(audit_id=42)


class TestSymbol:
    def test_blank_symbol(self):
        with pytest.raises(ValueError, match="symbol.*non-empty"):
            make_valid(symbol="")

    def test_wrong_type(self):
        with pytest.raises(TypeError, match="symbol must be a str"):
            make_valid(symbol=123)


class TestSessionDate:
    def test_invalid_format(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            make_valid(session_date="07-24-2026")

    def test_empty_date(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            make_valid(session_date="")

    def test_wrong_type(self):
        with pytest.raises(TypeError, match="session_date must be a str"):
            make_valid(session_date=20260724)


class TestTimeframe:
    def test_empty_timeframe(self):
        with pytest.raises(ValueError, match="timeframe.*non-empty"):
            make_valid(timeframe="")

    def test_wrong_type(self):
        with pytest.raises(TypeError, match="timeframe must be a str"):
            make_valid(timeframe=5)


class TestDirection:
    def test_invalid_direction(self):
        with pytest.raises(TypeError, match="Direction enum member"):
            make_valid(direction="LONG")

    def test_none_direction(self):
        with pytest.raises(TypeError, match="Direction enum member"):
            make_valid(direction=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Candidate status
# ═══════════════════════════════════════════════════════════════════════════════


class TestCandidateStatus:
    def test_invalid_status_string(self):
        with pytest.raises(TypeError, match="CandidateStatus enum member"):
            make_valid(candidate_status="VALID")

    def test_none_status(self):
        with pytest.raises(TypeError, match="CandidateStatus enum member"):
            make_valid(candidate_status=None)


# ═══════════════════════════════════════════════════════════════════════════════
# INV-A-08: VALID constraints
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidConstraints:
    def test_valid_with_failed_stage(self):
        with pytest.raises(ValueError, match="VALID.*failed_stage=None"):
            make_valid(
                failed_stage=FailedStage.BREAK_NOT_FOUND,
            )

    def test_valid_with_failed_rules(self):
        rf = _rule_failure()
        with pytest.raises(ValueError, match="VALID.*empty failed_rules"):
            make_valid(failed_rules=(rf,))


# ═══════════════════════════════════════════════════════════════════════════════
# INV-A-09: REJECTED constraints
# ═══════════════════════════════════════════════════════════════════════════════


class TestRejectedConstraints:
    def test_rejected_without_any_indicator(self):
        with pytest.raises(ValueError, match="REJECTED.*non-null"):
            make_rejected(failed_stage=None, failed_rules=())


# ═══════════════════════════════════════════════════════════════════════════════
# INV-A-10: Progression monotonicity
# ═══════════════════════════════════════════════════════════════════════════════


class TestProgressionMonotonicity:
    def test_rejection_scan_without_retest(self):
        with pytest.raises(
            ValueError, match="reached_rejection_scan implies reached_retest"
        ):
            make_rejected(
                reached_rejection_scan=True,
                reached_retest=False,
            )

    def test_retest_without_displacement(self):
        with pytest.raises(
            ValueError, match="reached_retest implies reached_displacement"
        ):
            make_rejected(
                reached_rejection_scan=False,
                reached_retest=True,
                reached_displacement=False,
            )

    def test_displacement_without_break(self):
        with pytest.raises(
            ValueError, match="reached_displacement implies reached_break"
        ):
            make_rejected(
                reached_rejection_scan=False,
                reached_retest=False,
                reached_displacement=True,
                reached_break=False,
            )

    def test_break_without_orb(self):
        with pytest.raises(
            ValueError, match="reached_break implies reached_orb"
        ):
            make_rejected(
                reached_rejection_scan=False,
                reached_retest=False,
                reached_displacement=False,
                reached_break=True,
                reached_orb=False,
            )

    def test_valid_partial_progression(self):
        """ORB + break only is valid (rejected at displacement)."""
        r = make_rejected(
            failed_stage=FailedStage.RETEST_BEFORE_DISPLACEMENT,
            reached_orb=True,
            reached_break=True,
            reached_displacement=False,
            reached_retest=False,
            reached_rejection_scan=False,
        )
        assert r.reached_break is True
        assert r.reached_displacement is False

    def test_non_bool_progression_flag(self):
        with pytest.raises(TypeError, match="reached_orb must be a bool"):
            make_valid(reached_orb=1)


# ═══════════════════════════════════════════════════════════════════════════════
# INV-A-11: ORB consistency
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrbConsistency:
    def test_orb_high_without_orb_low(self):
        with pytest.raises(ValueError, match="both present or both None"):
            make_valid(
                orb_high=PriceTicks(ticks=55000, tick_size=TICK_SIZE),
                orb_low=None,
            )

    def test_orb_low_without_orb_high(self):
        with pytest.raises(ValueError, match="both present or both None"):
            make_valid(
                orb_high=None,
                orb_low=PriceTicks(ticks=54900, tick_size=TICK_SIZE),
            )

    def test_orb_high_equal_to_orb_low(self):
        with pytest.raises(ValueError, match="must be greater than"):
            make_valid(
                orb_high=PriceTicks(ticks=55000, tick_size=TICK_SIZE),
                orb_low=PriceTicks(ticks=55000, tick_size=TICK_SIZE),
            )

    def test_orb_high_less_than_orb_low(self):
        with pytest.raises(ValueError, match="must be greater than"):
            make_valid(
                orb_high=PriceTicks(ticks=54800, tick_size=TICK_SIZE),
                orb_low=PriceTicks(ticks=55000, tick_size=TICK_SIZE),
            )

    def test_wrong_type_orb_high(self):
        with pytest.raises(TypeError, match="PriceTicks"):
            make_valid(orb_high=55000)


# ═══════════════════════════════════════════════════════════════════════════════
# INV-A-12: Timestamps
# ═══════════════════════════════════════════════════════════════════════════════


class TestTimestamps:
    def test_bool_timestamp_rejected(self):
        with pytest.raises(TypeError, match="orb_candle_time_ms.*int"):
            make_valid(orb_candle_time_ms=True)

    def test_float_timestamp_rejected(self):
        with pytest.raises(TypeError, match="break_candle_time_ms.*int"):
            make_valid(break_candle_time_ms=1.5)

    def test_string_timestamp_rejected(self):
        with pytest.raises(TypeError, match="last_relevant_time_ms.*int"):
            make_valid(last_relevant_time_ms="1753365000000")


# ═══════════════════════════════════════════════════════════════════════════════
# INV-A-13: DetectionResult status consistency
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectionResultConsistency:
    def test_valid_audit_with_invalid_detection_result(self):
        with pytest.raises(
            ValueError, match="VALID audit record must embed a VALID"
        ):
            make_valid(detection_result=_invalid_detection_result())

    def test_rejected_audit_with_valid_detection_result(self):
        with pytest.raises(
            ValueError, match="REJECTED audit record must embed an INVALID"
        ):
            make_rejected(detection_result=_valid_detection_result())

    def test_detection_result_wrong_type(self):
        with pytest.raises(
            TypeError, match="detection_result must be a DetectionResult"
        ):
            make_valid(detection_result={"status": "VALID"})

    def test_detection_result_none(self):
        with pytest.raises(
            TypeError, match="detection_result must be a DetectionResult"
        ):
            make_valid(detection_result=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Failed stage / failed rules type validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailedStageType:
    def test_string_failed_stage_rejected(self):
        with pytest.raises(TypeError, match="FailedStage enum member"):
            make_rejected(failed_stage="NO_QUALIFYING_REJECTION_CANDLE")

    def test_wrong_failed_rules_type(self):
        with pytest.raises(TypeError, match="failed_rules must be a tuple"):
            make_rejected(failed_rules=["not", "a", "tuple"])

    def test_wrong_failed_rules_element(self):
        with pytest.raises(TypeError, match="failed_rules.*RuleFailure"):
            make_rejected(failed_rules=("not_a_rule_failure",))


# ═══════════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_frozen(self):
        r = make_valid()
        with pytest.raises(AttributeError):
            r.symbol = "QQQ"  # type: ignore[misc]

    def test_frozen_candidate_status(self):
        r = make_valid()
        with pytest.raises(AttributeError):
            r.candidate_status = CandidateStatus.REJECTED  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialization:
    def test_to_dict_valid(self):
        r = make_valid()
        d = r.to_dict()
        assert d["schema_version"] == "DetectorAuditRecord/v1"
        assert d["audit_id"] == "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
        assert d["symbol"] == "SPY"
        assert d["session_date"] == "2026-07-24"
        assert d["timeframe"] == "5m"
        assert d["direction"] == "LONG"
        assert d["candidate_status"] == "VALID"
        assert d["failed_stage"] is None
        assert d["failed_rules"] == []
        assert d["reached_orb"] is True
        assert d["reached_break"] is True
        assert d["reached_displacement"] is True
        assert d["reached_retest"] is True
        assert d["reached_rejection_scan"] is True
        assert isinstance(d["orb_high"], dict)
        assert d["orb_high"]["ticks"] == 55000
        assert isinstance(d["orb_low"], dict)
        assert d["orb_low"]["ticks"] == 54900
        assert d["orb_candle_time_ms"] == 1753365000000
        assert d["break_candle_time_ms"] == 1753365300000
        assert d["last_relevant_time_ms"] == 1753369200000
        assert isinstance(d["detection_result"], dict)
        assert d["detection_result"]["status"] == "VALID"

    def test_to_dict_rejected(self):
        r = make_rejected()
        d = r.to_dict()
        assert d["candidate_status"] == "REJECTED"
        assert d["failed_stage"] == "NO_QUALIFYING_REJECTION_CANDLE"
        assert d["direction"] == "SHORT"
        assert d["detection_result"]["status"] == "INVALID"

    def test_to_dict_null_optional_fields(self):
        r = make_valid(
            orb_candle_time_ms=None,
            break_candle_time_ms=None,
            last_relevant_time_ms=None,
        )
        d = r.to_dict()
        assert d["orb_candle_time_ms"] is None
        assert d["break_candle_time_ms"] is None
        assert d["last_relevant_time_ms"] is None

    def test_to_dict_null_orb(self):
        r = make_rejected(
            orb_high=None,
            orb_low=None,
            reached_orb=False,
            reached_break=False,
            reached_displacement=False,
            reached_retest=False,
            reached_rejection_scan=False,
        )
        d = r.to_dict()
        assert d["orb_high"] is None
        assert d["orb_low"] is None

    def test_to_dict_all_keys_present(self):
        """Every field must appear in the dict, even when None."""
        d = make_valid().to_dict()
        expected_keys = {
            "schema_version", "audit_id", "symbol", "session_date",
            "timeframe", "direction", "candidate_status",
            "failed_stage", "failed_rules",
            "reached_orb", "reached_break", "reached_displacement",
            "reached_retest", "reached_rejection_scan",
            "orb_high", "orb_low",
            "orb_candle_time_ms", "break_candle_time_ms",
            "last_relevant_time_ms",
            "detection_result",
        }
        assert set(d.keys()) == expected_keys


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic equality
# ═══════════════════════════════════════════════════════════════════════════════


class TestEquality:
    def test_identical_records_are_equal(self):
        r1 = make_valid()
        r2 = make_valid()
        assert r1 == r2

    def test_different_audit_id_not_equal(self):
        r1 = make_valid()
        r2 = make_valid(audit_id="99999999-8888-4777-a666-555555555555")
        assert r1 != r2

    def test_to_dict_deterministic(self):
        """Same inputs produce identical dicts."""
        d1 = make_valid().to_dict()
        d2 = make_valid().to_dict()
        assert d1 == d2


# ═══════════════════════════════════════════════════════════════════════════════
# Package export
# ═══════════════════════════════════════════════════════════════════════════════


class TestPackageExport:
    def test_importable_from_contracts_package(self):
        from trading_lab.contracts import DetectorAuditRecord as Dar
        assert Dar is DetectorAuditRecord

    def test_candidate_status_importable(self):
        from trading_lab.contracts import CandidateStatus as Cs
        assert Cs is CandidateStatus
