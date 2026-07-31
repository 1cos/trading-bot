"""Tests for audit_visual_exporter — export_audit_visual_event().

Covers:
   1.  VALID record exports correctly
   2.  BREAK_NOT_FOUND exports ORB but no break
   3.  DISPLACEMENT_MINIMUM_NOT_MET exports break and partial structure
   4.  RETEST_BEFORE_DISPLACEMENT exports available geometry
   5.  SEQUENCE_INVALIDATED exports available data
   6.  RETEST_NOT_FOUND exports displacement but no retest
   7.  NO_QUALIFYING_REJECTION_CANDLE exports failed retests
   8.  Enum fields serialize to strings
   9.  PriceTicks convert using existing convention (ticks int)
  10.  Candle order preserved
  11.  Annotation indices map correctly by timestamp
  12.  Missing referenced bar raises
  13.  Duplicate candle timestamps raise
  14.  Unordered candles raise
  15.  Malformed candle input raises
  16.  No mutation of record
  17.  No mutation of candles
  18.  Deterministic output
  19.  Output is JSON serializable
  20.  VALID and REJECTED outputs share a stable common shape
"""

import copy
import json
import pytest

from trading_lab.audit_visual_exporter import export_audit_visual_event
from trading_lab.contracts.bar import Bar
from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.detector_audit_record import (
    CandidateStatus,
    DetectorAuditRecord,
)
from trading_lab.contracts.distances import (
    AbsoluteTickDistance,
    DirectionalTickDistance,
)
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
    LevelSource,
    Stage,
    ValueType,
)
from trading_lab.contracts.primitives import PriceTicks, Rational
from trading_lab.contracts.rule_failure import RejectionAttempt, RuleFailure
from trading_lab.contracts.session_metadata import SessionMetadata


TICK_SIZE = "0.01"

# Base timestamps: 09:30 ET = 13:30 UTC on 2026-05-26
T0 = 1748264400000   # bar 0 (ORB)
T1 = T0 + 300000     # bar 1 (break)
T2 = T0 + 600000     # bar 2 (displacement)
T3 = T0 + 900000     # bar 3 (retest)
T4 = T0 + 1200000    # bar 4 (confirmation)
T5 = T0 + 1500000    # bar 5


def _pt(ticks: int) -> PriceTicks:
    return PriceTicks(ticks=ticks, tick_size=TICK_SIZE)


def _bar(ms: int, o=52500, h=52550, l=52480, c=52530) -> Bar:
    return Bar(
        bar_utc_ms=ms,
        open=_pt(o), high=_pt(h), low=_pt(l), close=_pt(c),
    )


def _session() -> SessionMetadata:
    return SessionMetadata(
        symbol="SPY", date="2026-05-26",
        market_timezone="America/New_York",
        session_open_utc_ms=T0, session_close_utc_ms=T0 + 23400000,
        timeframe_seconds=300,
    )


def _rule_failure(rule_id="REJECTION_WICK_RATIO_TOO_LOW") -> RuleFailure:
    return RuleFailure(
        rule_id=rule_id, stage=Stage.REJECTION_CANDLE,
        value_type=ValueType.BOOLEAN,
        actual_value=None, operator=None,
        required_value=None, unit=None,
        message=rule_id,
    )


def _candles(n=6):
    """Build n raw candle dicts with timestamps T0..T(n-1)."""
    base = [
        {"time_ms": T0, "open": 525.00, "high": 525.50, "low": 524.80, "close": 525.30, "volume": 1000},
        {"time_ms": T1, "open": 525.30, "high": 526.00, "low": 525.20, "close": 525.80, "volume": 1100},
        {"time_ms": T2, "open": 525.80, "high": 526.50, "low": 525.60, "close": 526.30, "volume": 1200},
        {"time_ms": T3, "open": 526.30, "high": 526.40, "low": 525.00, "close": 525.20, "volume": 1300},
        {"time_ms": T4, "open": 525.20, "high": 525.50, "low": 524.90, "close": 525.40, "volume": 1400},
        {"time_ms": T5, "open": 525.40, "high": 525.60, "low": 525.10, "close": 525.50, "volume": 1500},
    ]
    return base[:n]


# ── DetectionResult factories ────────────────────────────────────────────────

def _valid_dr() -> DetectionResult:
    return DetectionResult(
        schema_version="DetectionResult/v1",
        result_id="dr-valid-001",
        produced_at="2026-05-26T14:05:00.000Z",
        session=_session(), preset_id="test", engine_version="1.0.0",
        status=DetectionStatus.VALID,
        failed_stage=None, failed_rules=(),
        level_price=_pt(52550), level_source=LevelSource.ORB_HIGH,
        level_bar=_bar(T0), direction=Direction.LONG,
        break_bar=_bar(T1, o=52530, h=52600, l=52520, c=52580),
        directional_break_distance=DirectionalTickDistance(ticks=19, tick_size=TICK_SIZE),
        displacement_window=(_bar(T2),),
        displacement_bar_count=1,
        displacement_pts=AbsoluteTickDistance(ticks=68, tick_size=TICK_SIZE),
        displacement_pct=Rational(numerator=68, denominator=52550),
        rejection_side_clearance_by_bar=(DirectionalTickDistance(ticks=20, tick_size=TICK_SIZE),),
        minimum_rejection_side_clearance=DirectionalTickDistance(ticks=20, tick_size=TICK_SIZE),
        average_rejection_side_clearance="0.20",
        retest_window=(_bar(T3),),
        retest_bar_count=1,
        failed_retest_count=1,
        failed_retests=(RejectionAttempt(bar=_bar(T3), failed_rules=(_rule_failure(),)),),
        bars_break_to_first_retest=2,
        bars_break_to_confirmation=3,
        retest_closest_approach=AbsoluteTickDistance(ticks=0, tick_size=TICK_SIZE),
        retest_penetration_through_level=AbsoluteTickDistance(ticks=86, tick_size=TICK_SIZE),
        retest_displacement_retracement_pct=Rational(numerator=86, denominator=68),
        confirmation_bar=_bar(T4, o=52520, h=52550, l=52490, c=52540),
        confirmation_rej_wick=Rational(numerator=670000, denominator=1000000),
        confirmation_body=Rational(numerator=200000, denominator=1000000),
        confirmation_opp_wick=Rational(numerator=130000, denominator=1000000),
        confirmation_favorable_close_location=Rational(numerator=870000, denominator=1000000),
        confirmation_penetration=AbsoluteTickDistance(ticks=7, tick_size=TICK_SIZE),
        confirmation_close_beyond_level=DirectionalTickDistance(ticks=45, tick_size=TICK_SIZE),
    )


def _invalid_dr(
    failed_stage=FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
    has_break=True, has_disp=True, has_retest=True,
    has_failed_retests=True,
) -> DetectionResult:
    return DetectionResult(
        schema_version="DetectionResult/v1",
        result_id="dr-invalid-001",
        produced_at="2026-05-26T14:05:00.000Z",
        session=_session(), preset_id="test", engine_version="1.0.0",
        status=DetectionStatus.INVALID,
        failed_stage=failed_stage, failed_rules=(),
        level_price=_pt(52550), level_source=LevelSource.ORB_HIGH,
        level_bar=_bar(T0), direction=Direction.LONG,
        break_bar=_bar(T1) if has_break else None,
        directional_break_distance=(
            DirectionalTickDistance(ticks=19, tick_size=TICK_SIZE)
            if has_break else None
        ),
        displacement_window=(_bar(T2),) if has_disp else (),
        displacement_bar_count=1 if has_disp else None,
        displacement_pts=(
            AbsoluteTickDistance(ticks=68, tick_size=TICK_SIZE)
            if has_disp else None
        ),
        displacement_pct=(
            Rational(numerator=68, denominator=52550) if has_disp else None
        ),
        rejection_side_clearance_by_bar=(
            (DirectionalTickDistance(ticks=20, tick_size=TICK_SIZE),)
            if has_disp else None
        ),
        minimum_rejection_side_clearance=(
            DirectionalTickDistance(ticks=20, tick_size=TICK_SIZE)
            if has_disp else None
        ),
        average_rejection_side_clearance="0.20" if has_disp else None,
        retest_window=(_bar(T3),) if has_retest else (),
        retest_bar_count=1 if has_retest else None,
        failed_retest_count=1 if has_failed_retests else None,
        failed_retests=(
            (RejectionAttempt(bar=_bar(T3), failed_rules=(_rule_failure(),)),)
            if has_failed_retests else ()
        ),
        bars_break_to_first_retest=2 if has_retest else None,
        bars_break_to_confirmation=None,
        retest_closest_approach=(
            AbsoluteTickDistance(ticks=0, tick_size=TICK_SIZE)
            if has_retest else None
        ),
        retest_penetration_through_level=(
            AbsoluteTickDistance(ticks=86, tick_size=TICK_SIZE)
            if has_retest else None
        ),
        retest_displacement_retracement_pct=(
            Rational(numerator=86, denominator=68) if has_retest else None
        ),
        confirmation_bar=None,
        confirmation_rej_wick=None,
        confirmation_body=None,
        confirmation_opp_wick=None,
        confirmation_favorable_close_location=None,
        confirmation_penetration=None,
        confirmation_close_beyond_level=None,
    )


# ── Audit record factories ──────────────────────────────────────────────────

def _valid_audit(dr=None) -> DetectorAuditRecord:
    if dr is None:
        dr = _valid_dr()
    return DetectorAuditRecord(
        schema_version="DetectorAuditRecord/v1",
        audit_id="audit-valid-001",
        symbol="SPY", session_date="2026-05-26", timeframe="5m",
        direction=Direction.LONG,
        candidate_status=CandidateStatus.VALID,
        failed_stage=None, failed_rules=(),
        reached_orb=True, reached_break=True,
        reached_displacement=True, reached_retest=True,
        reached_rejection_scan=True,
        orb_high=dr.level_bar.high, orb_low=dr.level_bar.low,
        orb_candle_time_ms=T0,
        break_candle_time_ms=T1,
        last_relevant_time_ms=T4,
        detection_result=dr,
    )


def _rejected_audit(
    failed_stage=FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
    has_break=True, has_disp=True, has_retest=True,
    has_failed_retests=True,
    audit_id="audit-rejected-001",
) -> DetectorAuditRecord:
    dr = _invalid_dr(
        failed_stage=failed_stage,
        has_break=has_break, has_disp=has_disp,
        has_retest=has_retest, has_failed_retests=has_failed_retests,
    )
    return DetectorAuditRecord(
        schema_version="DetectorAuditRecord/v1",
        audit_id=audit_id,
        symbol="SPY", session_date="2026-05-26", timeframe="5m",
        direction=Direction.LONG,
        candidate_status=CandidateStatus.REJECTED,
        failed_stage=failed_stage, failed_rules=(),
        reached_orb=True,
        reached_break=has_break,
        reached_displacement=has_disp,
        reached_retest=has_retest,
        reached_rejection_scan=has_failed_retests,
        orb_high=dr.level_bar.high, orb_low=dr.level_bar.low,
        orb_candle_time_ms=T0,
        break_candle_time_ms=T1 if has_break else None,
        last_relevant_time_ms=T3 if has_retest else (T2 if has_disp else (T1 if has_break else T0)),
        detection_result=dr,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. VALID record exports correctly
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidExport:
    def test_basic_valid(self):
        ev = export_audit_visual_event(_valid_audit(), _candles())
        assert ev["schema_version"] == "DetectorAuditVisualEvent/v1"
        assert ev["audit_id"] == "audit-valid-001"
        assert ev["symbol"] == "SPY"
        assert ev["session_date"] == "2026-05-26"
        assert ev["timeframe"] == "5m"
        assert ev["direction"] == "LONG"
        assert ev["candidate_status"] == "VALID"
        assert ev["failed_stage"] is None
        assert ev["failed_rules"] == []

    def test_valid_annotations_present(self):
        ev = export_audit_visual_event(_valid_audit(), _candles())
        ann = ev["annotations"]
        assert ann["break_candle_index"] == 1
        assert ann["displacement_start_index"] == 2
        assert ann["displacement_end_index"] == 2
        assert ann["retest_start_index"] == 3
        assert ann["retest_end_index"] == 3
        assert ann["confirmation_candle_index"] == 4


# ═══════════════════════════════════════════════════════════════════════════════
# 2. BREAK_NOT_FOUND — ORB but no break
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakNotFoundExport:
    def test_orb_present_no_break(self):
        rec = _rejected_audit(
            FailedStage.BREAK_NOT_FOUND,
            has_break=False, has_disp=False,
            has_retest=False, has_failed_retests=False,
        )
        ev = export_audit_visual_event(rec, _candles())
        assert ev["orb_high_ticks"] == 52550
        assert ev["orb_low_ticks"] == 52480
        ann = ev["annotations"]
        assert ann["break_candle_index"] is None
        assert ann["displacement_start_index"] is None
        assert ann["confirmation_candle_index"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. DISPLACEMENT_MINIMUM_NOT_MET — break + partial structure
# ═══════════════════════════════════════════════════════════════════════════════


class TestDisplacementMinimumExport:
    def test_break_present_no_displacement(self):
        rec = _rejected_audit(
            FailedStage.DISPLACEMENT_MINIMUM_NOT_MET,
            has_break=True, has_disp=False,
            has_retest=False, has_failed_retests=False,
        )
        ev = export_audit_visual_event(rec, _candles())
        ann = ev["annotations"]
        assert ann["break_candle_index"] == 1
        assert ann["displacement_start_index"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RETEST_BEFORE_DISPLACEMENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetestBeforeDisplacementExport:
    def test_exports_break(self):
        rec = _rejected_audit(
            FailedStage.RETEST_BEFORE_DISPLACEMENT,
            has_break=True, has_disp=False,
            has_retest=False, has_failed_retests=False,
        )
        ev = export_audit_visual_event(rec, _candles())
        assert ev["failed_stage"] == "RETEST_BEFORE_DISPLACEMENT"
        assert ev["annotations"]["break_candle_index"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SEQUENCE_INVALIDATED
# ═══════════════════════════════════════════════════════════════════════════════


class TestSequenceInvalidatedExport:
    def test_exports_available_data(self):
        rec = _rejected_audit(
            FailedStage.SEQUENCE_INVALIDATED,
            has_break=True, has_disp=False,
            has_retest=False, has_failed_retests=False,
        )
        ev = export_audit_visual_event(rec, _candles())
        assert ev["failed_stage"] == "SEQUENCE_INVALIDATED"
        assert ev["annotations"]["break_candle_index"] == 1
        assert ev["annotations"]["displacement_start_index"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 6. RETEST_NOT_FOUND — displacement but no retest
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetestNotFoundExport:
    def test_displacement_present_no_retest(self):
        rec = _rejected_audit(
            FailedStage.RETEST_NOT_FOUND,
            has_break=True, has_disp=True,
            has_retest=False, has_failed_retests=False,
        )
        ev = export_audit_visual_event(rec, _candles())
        ann = ev["annotations"]
        assert ann["break_candle_index"] == 1
        assert ann["displacement_start_index"] == 2
        assert ann["retest_start_index"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 7. NO_QUALIFYING_REJECTION_CANDLE — failed retests exported
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoQualifyingRejectionExport:
    def test_failed_retests_exported(self):
        rec = _rejected_audit(FailedStage.NO_QUALIFYING_REJECTION_CANDLE)
        ev = export_audit_visual_event(rec, _candles())
        fr = ev["failed_retests"]
        assert len(fr) == 1
        assert fr[0]["candle_index"] == 3
        assert fr[0]["candle_time_ms"] == T3
        assert len(fr[0]["failed_rules"]) == 1
        assert fr[0]["failed_rules"][0]["rule_id"] == "REJECTION_WICK_RATIO_TOO_LOW"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Enum fields serialize to strings
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnumSerialization:
    def test_direction_is_string(self):
        ev = export_audit_visual_event(_valid_audit(), _candles())
        assert ev["direction"] == "LONG"
        assert isinstance(ev["direction"], str)

    def test_candidate_status_is_string(self):
        ev = export_audit_visual_event(_valid_audit(), _candles())
        assert ev["candidate_status"] == "VALID"

    def test_failed_stage_is_string(self):
        rec = _rejected_audit(FailedStage.BREAK_NOT_FOUND,
                              has_break=False, has_disp=False,
                              has_retest=False, has_failed_retests=False)
        ev = export_audit_visual_event(rec, _candles())
        assert ev["failed_stage"] == "BREAK_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. PriceTicks convert to integer ticks
# ═══════════════════════════════════════════════════════════════════════════════


class TestPriceTicksConversion:
    def test_orb_ticks_are_ints(self):
        ev = export_audit_visual_event(_valid_audit(), _candles())
        assert isinstance(ev["orb_high_ticks"], int)
        assert isinstance(ev["orb_low_ticks"], int)
        assert ev["orb_high_ticks"] == 52550
        assert ev["orb_low_ticks"] == 52480


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Candle order preserved
# ═══════════════════════════════════════════════════════════════════════════════


class TestCandleOrder:
    def test_indices_match_input(self):
        ev = export_audit_visual_event(_valid_audit(), _candles())
        for i, c in enumerate(ev["candles"]):
            assert c["index"] == i


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Annotation indices map correctly
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnnotationMapping:
    def test_break_maps_to_correct_index(self):
        ev = export_audit_visual_event(_valid_audit(), _candles())
        idx = ev["annotations"]["break_candle_index"]
        assert ev["candles"][idx]["time_ms"] == T1

    def test_confirmation_maps_to_correct_index(self):
        ev = export_audit_visual_event(_valid_audit(), _candles())
        idx = ev["annotations"]["confirmation_candle_index"]
        assert ev["candles"][idx]["time_ms"] == T4


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Missing referenced bar raises
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingBar:
    def test_break_bar_not_in_candles(self):
        # Remove bar at T1 from candles
        candles = [c for c in _candles() if c["time_ms"] != T1]
        with pytest.raises(ValueError, match="break.*not found"):
            export_audit_visual_event(_valid_audit(), candles)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Duplicate candle timestamps raise
# ═══════════════════════════════════════════════════════════════════════════════


class TestDuplicateTimestamps:
    def test_duplicate_raises(self):
        candles = _candles()
        candles.append(dict(candles[-1]))  # duplicate last
        with pytest.raises(ValueError, match="duplicate"):
            export_audit_visual_event(_valid_audit(), candles)


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Unordered candles raise
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnorderedCandles:
    def test_unordered_raises(self):
        candles = _candles()
        candles[1], candles[2] = candles[2], candles[1]
        with pytest.raises(ValueError, match="not strictly increasing"):
            export_audit_visual_event(_valid_audit(), candles)


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Malformed candle input raises
# ═══════════════════════════════════════════════════════════════════════════════


class TestMalformedCandles:
    def test_non_sequence(self):
        with pytest.raises(TypeError, match="sequence"):
            export_audit_visual_event(_valid_audit(), 42)

    def test_non_dict_element(self):
        with pytest.raises(TypeError, match="candles.*dict"):
            export_audit_visual_event(_valid_audit(), ["not_a_dict"])

    def test_missing_time_ms(self):
        candles = [{"open": 1, "high": 2, "low": 0, "close": 1}]
        with pytest.raises(ValueError, match="time_ms"):
            export_audit_visual_event(_valid_audit(), candles)

    def test_missing_ohlc(self):
        candles = [{"time_ms": T0, "open": 1, "high": 2, "low": 0}]
        with pytest.raises(ValueError, match="close"):
            export_audit_visual_event(_valid_audit(), candles)

    def test_non_record_input(self):
        with pytest.raises(TypeError, match="DetectorAuditRecord"):
            export_audit_visual_event({"status": "VALID"}, _candles())


# ═══════════════════════════════════════════════════════════════════════════════
# 16. No mutation of record
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutationRecord:
    def test_record_unchanged(self):
        rec = _valid_audit()
        original_id = rec.audit_id
        export_audit_visual_event(rec, _candles())
        assert rec.audit_id == original_id


# ═══════════════════════════════════════════════════════════════════════════════
# 17. No mutation of candles
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutationCandles:
    def test_candles_unchanged(self):
        candles = _candles()
        candles_copy = copy.deepcopy(candles)
        export_audit_visual_event(_valid_audit(), candles)
        assert candles == candles_copy


# ═══════════════════════════════════════════════════════════════════════════════
# 18. Deterministic output
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministicOutput:
    def test_same_input_same_output(self):
        rec = _valid_audit()
        candles = _candles()
        ev1 = export_audit_visual_event(rec, candles)
        ev2 = export_audit_visual_event(rec, candles)
        assert ev1 == ev2


# ═══════════════════════════════════════════════════════════════════════════════
# 19. JSON serializable
# ═══════════════════════════════════════════════════════════════════════════════


class TestJsonSerializable:
    def test_valid_serializes(self):
        ev = export_audit_visual_event(_valid_audit(), _candles())
        s = json.dumps(ev, sort_keys=True)
        assert isinstance(s, str)
        parsed = json.loads(s)
        assert parsed["schema_version"] == "DetectorAuditVisualEvent/v1"

    def test_rejected_serializes(self):
        rec = _rejected_audit(FailedStage.NO_QUALIFYING_REJECTION_CANDLE)
        ev = export_audit_visual_event(rec, _candles())
        s = json.dumps(ev, sort_keys=True)
        assert isinstance(s, str)


# ═══════════════════════════════════════════════════════════════════════════════
# 20. VALID and REJECTED share common shape
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommonShape:
    def test_same_top_level_keys(self):
        valid_ev = export_audit_visual_event(_valid_audit(), _candles())
        rejected_ev = export_audit_visual_event(
            _rejected_audit(FailedStage.NO_QUALIFYING_REJECTION_CANDLE),
            _candles(),
        )
        assert set(valid_ev.keys()) == set(rejected_ev.keys())

    def test_same_annotation_keys(self):
        valid_ev = export_audit_visual_event(_valid_audit(), _candles())
        rejected_ev = export_audit_visual_event(
            _rejected_audit(FailedStage.NO_QUALIFYING_REJECTION_CANDLE),
            _candles(),
        )
        assert set(valid_ev["annotations"].keys()) == \
            set(rejected_ev["annotations"].keys())
