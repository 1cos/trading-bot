"""Tests for audit_record_builder — build_detector_audit_record().

Covers:
  1.  VALID audit record from a real-shaped VALID runner result
  2.  REJECTED records from: break failure, displacement failure,
      retest-before-displacement, retest failure (no qualifying rejection)
  3.  Deterministic audit_id (same input same UUID, different input different)
  4.  candidate_status mapping
  5.  failed_stage mapping
  6.  failed_rules mapping and ordering
  7.  Progression flags for each failure depth
  8.  PriceTicks preserved exactly
  9.  DetectionResult preserved by identity
  10. Timestamps mapped only when available
  11. Malformed input rejected
  12. Missing metadata rejected
  13. Status mismatch rejected
  14. No mutation of runner_result
  15. No mutation of DetectionResult
"""

import copy
import pytest

from trading_lab.audit_record_builder import build_detector_audit_record
from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.detector_audit_record import (
    CandidateStatus,
    DetectorAuditRecord,
)
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
)
from trading_lab.contracts.primitives import PriceTicks

# Reuse existing detection result fixtures
from test_contract_detection_result import (
    _valid_kwargs as _dr_valid_kwargs,
    _invalid_kwargs as _dr_invalid_kwargs,
    _pt,
    _bar,
    BAR_MS,
)


TICK_SIZE = "0.01"


# ── Runner result fixtures ───────────────────────────────────────────────────


def _valid_dr() -> DetectionResult:
    return DetectionResult(**_dr_valid_kwargs())


def _invalid_dr(**overrides) -> DetectionResult:
    kw = _dr_invalid_kwargs()
    kw.update(overrides)
    return DetectionResult(**kw)


def _valid_runner_result() -> dict:
    """A runner result for a VALID detection."""
    dr = _valid_dr()
    return {
        "run_record_id": "aaaa-bbbb-cccc-dddd",
        "symbol": "SPY",
        "session_date": "2026-05-26",
        "preset_id": "bdrr_spy_v1",
        "exit_target_r": 2,
        "detection_status": "VALID",
        "failure_stage": None,
        "failed_rules": (),
        "detection_result_id": dr.result_id,
        "candidate_id": "some-candidate-id",
        "confirmation_timestamp": None,
        "entry_timestamp": None,
        "first_evaluation_timestamp": None,
        "entry_price_ticks": 52530,
        "stop_price_ticks": 52480,
        "r2_price_ticks": 52630,
        "r3_price_ticks": 52680,
        "r4_price_ticks": 52730,
        "outcome": "TARGET_HIT",
        "realized_r": 2.0,
        "highest_target_achieved": 2,
        "exit_timestamp": None,
        "exit_price_ticks": 52630,
        "detection_result": dr,
        "trade_plan": None,
        "trade_outcome": None,
    }


def _rejected_runner_result(
    failed_stage: FailedStage | None = FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
    **dr_overrides,
) -> dict:
    """A runner result for an INVALID detection."""
    dr = _invalid_dr(
        failed_stage=failed_stage,
        **dr_overrides,
    )
    return {
        "run_record_id": "eeee-ffff-0000-1111",
        "symbol": "QQQ",
        "session_date": "2026-05-26",
        "preset_id": "bdrr_qqq_v1",
        "exit_target_r": 2,
        "detection_status": "INVALID",
        "failure_stage": failed_stage,
        "failed_rules": (),
        "detection_result_id": dr.result_id,
        "candidate_id": None,
        "confirmation_timestamp": None,
        "entry_timestamp": None,
        "first_evaluation_timestamp": None,
        "entry_price_ticks": None,
        "stop_price_ticks": None,
        "r2_price_ticks": None,
        "r3_price_ticks": None,
        "r4_price_ticks": None,
        "outcome": "NO_VALID_SETUP",
        "realized_r": None,
        "highest_target_achieved": None,
        "exit_timestamp": None,
        "exit_price_ticks": None,
        "detection_result": dr,
        "trade_plan": None,
        "trade_outcome": None,
    }


def _break_failure_dr() -> DetectionResult:
    """DR that failed at break stage."""
    kw = _dr_invalid_kwargs()
    kw.update(
        failed_stage=FailedStage.BREAK_NOT_FOUND,
        # ORB data present, but no break or later data
        break_bar=None,
        directional_break_distance=None,
        displacement_window=(),
        displacement_bar_count=None,
        displacement_pts=None,
        displacement_pct=None,
        rejection_side_clearance_by_bar=None,
        minimum_rejection_side_clearance=None,
        average_rejection_side_clearance=None,
        retest_window=(),
        retest_bar_count=None,
        failed_retest_count=None,
        failed_retests=(),
        bars_break_to_first_retest=None,
        retest_closest_approach=None,
        retest_penetration_through_level=None,
        retest_displacement_retracement_pct=None,
    )
    return DetectionResult(**kw)


def _displacement_failure_dr() -> DetectionResult:
    """DR that failed at displacement stage (RETEST_BEFORE_DISPLACEMENT)."""
    kw = _dr_invalid_kwargs()
    kw.update(
        failed_stage=FailedStage.RETEST_BEFORE_DISPLACEMENT,
        # ORB + break present, no displacement or later
        displacement_window=(),
        displacement_bar_count=None,
        displacement_pts=None,
        displacement_pct=None,
        rejection_side_clearance_by_bar=None,
        minimum_rejection_side_clearance=None,
        average_rejection_side_clearance=None,
        retest_window=(),
        retest_bar_count=None,
        failed_retest_count=None,
        failed_retests=(),
        bars_break_to_first_retest=None,
        retest_closest_approach=None,
        retest_penetration_through_level=None,
        retest_displacement_retracement_pct=None,
    )
    return DetectionResult(**kw)


def _retest_not_found_dr() -> DetectionResult:
    """DR that failed at retest/displacement boundary."""
    kw = _dr_invalid_kwargs()
    kw.update(
        failed_stage=FailedStage.RETEST_NOT_FOUND,
        # ORB + break present, no displacement
        displacement_window=(),
        displacement_bar_count=None,
        displacement_pts=None,
        displacement_pct=None,
        rejection_side_clearance_by_bar=None,
        minimum_rejection_side_clearance=None,
        average_rejection_side_clearance=None,
        retest_window=(),
        retest_bar_count=None,
        failed_retest_count=None,
        failed_retests=(),
        bars_break_to_first_retest=None,
        retest_closest_approach=None,
        retest_penetration_through_level=None,
        retest_displacement_retracement_pct=None,
    )
    return DetectionResult(**kw)


def _level_not_found_dr() -> DetectionResult:
    """DR that failed at ORB/level stage."""
    kw = _dr_invalid_kwargs()
    kw.update(
        failed_stage=FailedStage.LEVEL_NOT_FOUND,
        # Nothing present
        level_price=None,
        level_source=None,
        level_bar=None,
        direction=Direction.LONG,  # direction still needed
        break_bar=None,
        directional_break_distance=None,
        displacement_window=(),
        displacement_bar_count=None,
        displacement_pts=None,
        displacement_pct=None,
        rejection_side_clearance_by_bar=None,
        minimum_rejection_side_clearance=None,
        average_rejection_side_clearance=None,
        retest_window=(),
        retest_bar_count=None,
        failed_retest_count=None,
        failed_retests=(),
        bars_break_to_first_retest=None,
        retest_closest_approach=None,
        retest_penetration_through_level=None,
        retest_displacement_retracement_pct=None,
    )
    return DetectionResult(**kw)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. VALID audit record from VALID runner result
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidRecord:
    def test_basic_valid(self):
        rr = _valid_runner_result()
        ar = build_detector_audit_record(rr)
        assert isinstance(ar, DetectorAuditRecord)
        assert ar.schema_version == "DetectorAuditRecord/v1"
        assert ar.symbol == "SPY"
        assert ar.session_date == "2026-05-26"
        assert ar.timeframe == "5m"
        assert ar.direction == Direction.LONG
        assert ar.candidate_status == CandidateStatus.VALID
        assert ar.failed_stage is None
        assert ar.failed_rules == ()

    def test_valid_progression_all_true(self):
        rr = _valid_runner_result()
        ar = build_detector_audit_record(rr)
        assert ar.reached_orb is True
        assert ar.reached_break is True
        assert ar.reached_displacement is True
        assert ar.reached_retest is True
        assert ar.reached_rejection_scan is True

    def test_valid_detection_result_embedded(self):
        rr = _valid_runner_result()
        ar = build_detector_audit_record(rr)
        assert ar.detection_result is rr["detection_result"]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. REJECTED records at various failure depths
# ═══════════════════════════════════════════════════════════════════════════════


class TestRejectedRecords:
    def test_rejection_failure(self):
        """Failure at rejection stage (NO_QUALIFYING_REJECTION_CANDLE)."""
        rr = _rejected_runner_result()
        ar = build_detector_audit_record(rr)
        assert ar.candidate_status == CandidateStatus.REJECTED
        assert ar.failed_stage == FailedStage.NO_QUALIFYING_REJECTION_CANDLE

    def test_break_failure(self):
        dr = _break_failure_dr()
        rr = _rejected_runner_result(
            failed_stage=FailedStage.BREAK_NOT_FOUND,
        )
        rr["detection_result"] = dr
        ar = build_detector_audit_record(rr)
        assert ar.candidate_status == CandidateStatus.REJECTED
        assert ar.failed_stage == FailedStage.BREAK_NOT_FOUND
        assert ar.reached_orb is True
        assert ar.reached_break is False
        assert ar.reached_displacement is False
        assert ar.reached_retest is False
        assert ar.reached_rejection_scan is False

    def test_displacement_failure(self):
        dr = _displacement_failure_dr()
        rr = _rejected_runner_result(
            failed_stage=FailedStage.RETEST_BEFORE_DISPLACEMENT,
        )
        rr["detection_result"] = dr
        ar = build_detector_audit_record(rr)
        assert ar.failed_stage == FailedStage.RETEST_BEFORE_DISPLACEMENT
        assert ar.reached_orb is True
        assert ar.reached_break is True
        assert ar.reached_displacement is False
        assert ar.reached_retest is False
        assert ar.reached_rejection_scan is False

    def test_retest_not_found_failure(self):
        dr = _retest_not_found_dr()
        rr = _rejected_runner_result(
            failed_stage=FailedStage.RETEST_NOT_FOUND,
        )
        rr["detection_result"] = dr
        ar = build_detector_audit_record(rr)
        assert ar.failed_stage == FailedStage.RETEST_NOT_FOUND
        assert ar.reached_break is True
        assert ar.reached_displacement is False

    def test_level_not_found_failure(self):
        dr = _level_not_found_dr()
        rr = _rejected_runner_result(
            failed_stage=FailedStage.LEVEL_NOT_FOUND,
        )
        rr["detection_result"] = dr
        ar = build_detector_audit_record(rr)
        assert ar.failed_stage == FailedStage.LEVEL_NOT_FOUND
        assert ar.reached_orb is False
        assert ar.reached_break is False
        assert ar.reached_displacement is False
        assert ar.reached_retest is False
        assert ar.reached_rejection_scan is False
        assert ar.orb_high is None
        assert ar.orb_low is None
        assert ar.orb_candle_time_ms is None

    def test_sequence_invalidated_raises(self):
        """SEQUENCE_INVALIDATED has failed_stage=None in DR (enum gap)."""
        dr = _break_failure_dr()
        # Simulate SEQUENCE_INVALIDATED: INVALID with failed_stage=None
        kw = _dr_invalid_kwargs()
        kw.update(
            failed_stage=None,
            failed_rules=(),
            displacement_window=(),
            displacement_bar_count=None,
        )
        dr = DetectionResult(**kw)
        rr = _rejected_runner_result()
        rr["detection_result"] = dr
        rr["failure_stage"] = None
        with pytest.raises(ValueError, match="failed_stage=None"):
            build_detector_audit_record(rr)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Deterministic audit_id
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministicId:
    def test_same_input_same_id(self):
        rr = _valid_runner_result()
        ar1 = build_detector_audit_record(rr)
        ar2 = build_detector_audit_record(rr)
        assert ar1.audit_id == ar2.audit_id

    def test_different_symbol_different_id(self):
        rr1 = _valid_runner_result()
        rr2 = _valid_runner_result()
        rr2["symbol"] = "QQQ"
        ar1 = build_detector_audit_record(rr1)
        ar2 = build_detector_audit_record(rr2)
        assert ar1.audit_id != ar2.audit_id

    def test_different_date_different_id(self):
        rr1 = _valid_runner_result()
        rr2 = _valid_runner_result()
        # Change both runner result and DR session date
        from trading_lab.contracts.session_metadata import SessionMetadata
        dr2 = _valid_dr()
        new_session = SessionMetadata(
            symbol="SPY",
            date="2026-05-27",
            market_timezone="America/New_York",
            session_open_utc_ms=dr2.session.session_open_utc_ms,
            session_close_utc_ms=dr2.session.session_close_utc_ms,
            timeframe_seconds=300,
        )
        kw = _dr_valid_kwargs()
        kw["session"] = new_session
        kw["result_id"] = "different-result-id-for-new-date"
        rr2["detection_result"] = DetectionResult(**kw)
        rr2["session_date"] = "2026-05-27"
        ar1 = build_detector_audit_record(rr1)
        ar2 = build_detector_audit_record(rr2)
        assert ar1.audit_id != ar2.audit_id

    def test_uuid_format(self):
        import re
        rr = _valid_runner_result()
        ar = build_detector_audit_record(rr)
        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            ar.audit_id,
        ), f"Expected UUID v5, got {ar.audit_id}"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. candidate_status mapping
# ═══════════════════════════════════════════════════════════════════════════════


class TestCandidateStatusMapping:
    def test_valid_maps_to_valid(self):
        ar = build_detector_audit_record(_valid_runner_result())
        assert ar.candidate_status == CandidateStatus.VALID

    def test_invalid_maps_to_rejected(self):
        ar = build_detector_audit_record(_rejected_runner_result())
        assert ar.candidate_status == CandidateStatus.REJECTED


# ═══════════════════════════════════════════════════════════════════════════════
# 5. failed_stage mapping
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailedStageMapping:
    def test_valid_has_null_failed_stage(self):
        ar = build_detector_audit_record(_valid_runner_result())
        assert ar.failed_stage is None

    def test_rejected_preserves_failed_stage(self):
        ar = build_detector_audit_record(_rejected_runner_result())
        assert ar.failed_stage == FailedStage.NO_QUALIFYING_REJECTION_CANDLE

    def test_each_failed_stage_value(self):
        for fs in [FailedStage.BREAK_NOT_FOUND,
                    FailedStage.RETEST_BEFORE_DISPLACEMENT,
                    FailedStage.RETEST_NOT_FOUND]:
            rr = _rejected_runner_result(failed_stage=fs)
            ar = build_detector_audit_record(rr)
            assert ar.failed_stage == fs


# ═══════════════════════════════════════════════════════════════════════════════
# 6. failed_rules mapping
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailedRulesMapping:
    def test_valid_has_empty_failed_rules(self):
        ar = build_detector_audit_record(_valid_runner_result())
        assert ar.failed_rules == ()

    def test_rejected_preserves_failed_rules_tuple(self):
        ar = build_detector_audit_record(_rejected_runner_result())
        assert isinstance(ar.failed_rules, tuple)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Progression flags at each depth
# ═══════════════════════════════════════════════════════════════════════════════


class TestProgressionFlags:
    def test_full_progression_valid(self):
        ar = build_detector_audit_record(_valid_runner_result())
        assert ar.reached_orb is True
        assert ar.reached_break is True
        assert ar.reached_displacement is True
        assert ar.reached_retest is True
        assert ar.reached_rejection_scan is True

    def test_rejection_scan_reached_on_failure(self):
        """NO_QUALIFYING_REJECTION_CANDLE means scan was done."""
        ar = build_detector_audit_record(_rejected_runner_result())
        # The default _invalid_kwargs has retest and rejection data
        assert ar.reached_rejection_scan is True

    def test_level_failure_nothing_reached(self):
        dr = _level_not_found_dr()
        rr = _rejected_runner_result(
            failed_stage=FailedStage.LEVEL_NOT_FOUND)
        rr["detection_result"] = dr
        ar = build_detector_audit_record(rr)
        assert ar.reached_orb is False
        assert ar.reached_break is False
        assert ar.reached_displacement is False
        assert ar.reached_retest is False
        assert ar.reached_rejection_scan is False

    def test_break_failure_only_orb_reached(self):
        dr = _break_failure_dr()
        rr = _rejected_runner_result(
            failed_stage=FailedStage.BREAK_NOT_FOUND)
        rr["detection_result"] = dr
        ar = build_detector_audit_record(rr)
        assert ar.reached_orb is True
        assert ar.reached_break is False
        assert ar.reached_displacement is False


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PriceTicks preserved exactly
# ═══════════════════════════════════════════════════════════════════════════════


class TestPriceTicksPreservation:
    def test_orb_prices_are_price_ticks(self):
        ar = build_detector_audit_record(_valid_runner_result())
        assert isinstance(ar.orb_high, PriceTicks)
        assert isinstance(ar.orb_low, PriceTicks)

    def test_orb_prices_from_level_bar(self):
        rr = _valid_runner_result()
        dr = rr["detection_result"]
        ar = build_detector_audit_record(rr)
        assert ar.orb_high.ticks == dr.level_bar.high.ticks
        assert ar.orb_low.ticks == dr.level_bar.low.ticks
        assert ar.orb_high.tick_size == dr.level_bar.high.tick_size

    def test_no_level_bar_null_orb_prices(self):
        dr = _level_not_found_dr()
        rr = _rejected_runner_result(
            failed_stage=FailedStage.LEVEL_NOT_FOUND)
        rr["detection_result"] = dr
        ar = build_detector_audit_record(rr)
        assert ar.orb_high is None
        assert ar.orb_low is None


# ═══════════════════════════════════════════════════════════════════════════════
# 9. DetectionResult preserved by identity
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectionResultPreservation:
    def test_same_object_reference(self):
        rr = _valid_runner_result()
        ar = build_detector_audit_record(rr)
        assert ar.detection_result is rr["detection_result"]

    def test_rejected_same_object(self):
        rr = _rejected_runner_result()
        ar = build_detector_audit_record(rr)
        assert ar.detection_result is rr["detection_result"]


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Timestamps
# ═══════════════════════════════════════════════════════════════════════════════


class TestTimestamps:
    def test_orb_timestamp_from_level_bar(self):
        rr = _valid_runner_result()
        dr = rr["detection_result"]
        ar = build_detector_audit_record(rr)
        assert ar.orb_candle_time_ms == dr.level_bar.bar_utc_ms

    def test_break_timestamp_from_break_bar(self):
        rr = _valid_runner_result()
        dr = rr["detection_result"]
        ar = build_detector_audit_record(rr)
        assert ar.break_candle_time_ms == dr.break_bar.bar_utc_ms

    def test_last_relevant_from_confirmation_bar_for_valid(self):
        rr = _valid_runner_result()
        dr = rr["detection_result"]
        ar = build_detector_audit_record(rr)
        assert ar.last_relevant_time_ms == dr.confirmation_bar.bar_utc_ms

    def test_last_relevant_fallback_to_retest_window(self):
        """REJECTED with retest data uses last retest bar."""
        rr = _rejected_runner_result()
        dr = rr["detection_result"]
        ar = build_detector_audit_record(rr)
        assert ar.last_relevant_time_ms == dr.retest_window[-1].bar_utc_ms

    def test_null_timestamps_when_no_data(self):
        dr = _level_not_found_dr()
        rr = _rejected_runner_result(
            failed_stage=FailedStage.LEVEL_NOT_FOUND)
        rr["detection_result"] = dr
        ar = build_detector_audit_record(rr)
        assert ar.orb_candle_time_ms is None
        assert ar.break_candle_time_ms is None
        assert ar.last_relevant_time_ms is None

    def test_break_failure_last_relevant_from_level_bar(self):
        """Break failure: last relevant is the ORB candle."""
        dr = _break_failure_dr()
        rr = _rejected_runner_result(
            failed_stage=FailedStage.BREAK_NOT_FOUND)
        rr["detection_result"] = dr
        ar = build_detector_audit_record(rr)
        assert ar.last_relevant_time_ms == dr.level_bar.bar_utc_ms
        assert ar.break_candle_time_ms is None


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Malformed input rejected
# ═══════════════════════════════════════════════════════════════════════════════


class TestMalformedInput:
    def test_non_dict_input(self):
        with pytest.raises(TypeError, match="runner_result must be a dict"):
            build_detector_audit_record("not a dict")

    def test_list_input(self):
        with pytest.raises(TypeError, match="runner_result must be a dict"):
            build_detector_audit_record([])

    def test_none_input(self):
        with pytest.raises(TypeError, match="runner_result must be a dict"):
            build_detector_audit_record(None)

    def test_detection_result_is_dict(self):
        rr = _valid_runner_result()
        rr["detection_result"] = {"status": "VALID"}
        with pytest.raises(TypeError, match="DetectionResult instance"):
            build_detector_audit_record(rr)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Missing metadata rejected
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingMetadata:
    def test_missing_detection_result(self):
        rr = _valid_runner_result()
        rr["detection_result"] = None
        with pytest.raises(ValueError, match="non-null detection_result"):
            build_detector_audit_record(rr)

    def test_missing_symbol(self):
        rr = _valid_runner_result()
        rr["symbol"] = None
        with pytest.raises(ValueError, match="symbol"):
            build_detector_audit_record(rr)

    def test_empty_symbol(self):
        rr = _valid_runner_result()
        rr["symbol"] = ""
        with pytest.raises(ValueError, match="symbol"):
            build_detector_audit_record(rr)

    def test_missing_session_date(self):
        rr = _valid_runner_result()
        rr["session_date"] = None
        with pytest.raises(ValueError, match="session_date"):
            build_detector_audit_record(rr)

    def test_missing_direction_in_dr(self):
        """DR with direction=None should be rejected."""
        kw = _dr_valid_kwargs()
        kw["direction"] = None
        # This will create a DR with direction=None (the DR contract allows it)
        dr = DetectionResult(**kw)
        rr = _valid_runner_result()
        rr["detection_result"] = dr
        with pytest.raises(ValueError, match="direction"):
            build_detector_audit_record(rr)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Status mismatch rejected
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatusMismatch:
    def test_valid_runner_with_invalid_dr(self):
        rr = _valid_runner_result()
        rr["detection_result"] = _invalid_dr()
        # detection_status says VALID but DR is INVALID
        with pytest.raises(ValueError, match="inconsistent"):
            build_detector_audit_record(rr)

    def test_invalid_runner_with_valid_dr(self):
        rr = _rejected_runner_result()
        rr["detection_result"] = _valid_dr()
        # detection_status says INVALID but DR is VALID
        with pytest.raises(ValueError, match="inconsistent"):
            build_detector_audit_record(rr)


# ═══════════════════════════════════════════════════════════════════════════════
# 14. No mutation of runner_result
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutation:
    def test_runner_result_not_mutated(self):
        rr = _valid_runner_result()
        rr_copy = copy.copy(rr)
        build_detector_audit_record(rr)
        assert rr == rr_copy

    def test_runner_result_keys_unchanged(self):
        rr = _valid_runner_result()
        original_keys = set(rr.keys())
        build_detector_audit_record(rr)
        assert set(rr.keys()) == original_keys


# ═══════════════════════════════════════════════════════════════════════════════
# 15. No mutation of DetectionResult
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectionResultNotMutated:
    def test_frozen_dr_preserved(self):
        rr = _valid_runner_result()
        dr = rr["detection_result"]
        ar = build_detector_audit_record(rr)
        # DetectionResult is frozen, so any mutation would raise
        with pytest.raises(AttributeError):
            ar.detection_result.status = DetectionStatus.INVALID  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
# Timeframe derivation
# ═══════════════════════════════════════════════════════════════════════════════


class TestTimeframeDerivation:
    def test_300s_to_5m(self):
        ar = build_detector_audit_record(_valid_runner_result())
        assert ar.timeframe == "5m"

    def test_60s_to_1m(self):
        """Build a 1m-timeframe runner result."""
        from trading_lab.contracts.session_metadata import SessionMetadata
        kw = _dr_valid_kwargs()
        kw["session"] = SessionMetadata(
            symbol="SPY",
            date="2026-05-26",
            market_timezone="America/New_York",
            session_open_utc_ms=1748264400000,
            session_close_utc_ms=1748287800000,
            timeframe_seconds=60,
        )
        dr = DetectionResult(**kw)
        rr = _valid_runner_result()
        rr["detection_result"] = dr
        ar = build_detector_audit_record(rr)
        assert ar.timeframe == "1m"
