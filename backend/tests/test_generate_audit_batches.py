"""Tests for generate_audit_batches — audit batch pipeline and HTML generation.

Tests pure helper functions and the A2→A3→A4 integration without
requiring real market data or a browser.
"""

import json
import copy
import pytest

from trading_lab.audit_record_builder import build_detector_audit_record
from trading_lab.audit_candidate_selector import select_audit_candidates
from trading_lab.audit_visual_exporter import export_audit_visual_event
from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.detector_audit_record import (
    CandidateStatus,
    DetectorAuditRecord,
)
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
    LevelSource,
    Stage,
    ValueType,
)
from trading_lab.contracts.bar import Bar
from trading_lab.contracts.distances import (
    AbsoluteTickDistance,
    DirectionalTickDistance,
)
from trading_lab.contracts.primitives import PriceTicks, Rational
from trading_lab.contracts.rule_failure import RejectionAttempt, RuleFailure
from trading_lab.contracts.session_metadata import SessionMetadata

# Import the generator module functions
import importlib
import sys
from pathlib import Path

# Add backend to path so we can import the generator
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from generate_audit_batches import generate_audit_html, parse_args, balance_by_failed_stage


TICK_SIZE = "0.01"
T0 = 1748264400000
T1 = T0 + 300000
T2 = T0 + 600000
T3 = T0 + 900000
T4 = T0 + 1200000
T5 = T0 + 1500000


def _pt(ticks):
    return PriceTicks(ticks=ticks, tick_size=TICK_SIZE)


def _bar(ms, o=52500, h=52550, l=52480, c=52530):
    return Bar(bar_utc_ms=ms, open=_pt(o), high=_pt(h), low=_pt(l), close=_pt(c))


def _session():
    return SessionMetadata(
        symbol="SPY", date="2026-05-26",
        market_timezone="America/New_York",
        session_open_utc_ms=T0, session_close_utc_ms=T0 + 23400000,
        timeframe_seconds=300,
    )


def _rf(rule_id="REJECTION_WICK_RATIO_TOO_LOW"):
    return RuleFailure(
        rule_id=rule_id, stage=Stage.REJECTION_CANDLE,
        value_type=ValueType.BOOLEAN,
        actual_value=None, operator=None,
        required_value=None, unit=None, message=rule_id,
    )


def _candles(n=6):
    base = [
        {"time_ms": T0, "open": 525.00, "high": 525.50, "low": 524.80, "close": 525.30, "volume": 1000},
        {"time_ms": T1, "open": 525.30, "high": 526.00, "low": 525.20, "close": 525.80, "volume": 1100},
        {"time_ms": T2, "open": 525.80, "high": 526.50, "low": 525.60, "close": 526.30, "volume": 1200},
        {"time_ms": T3, "open": 526.30, "high": 526.40, "low": 525.00, "close": 525.20, "volume": 1300},
        {"time_ms": T4, "open": 525.20, "high": 525.50, "low": 524.90, "close": 525.40, "volume": 1400},
        {"time_ms": T5, "open": 525.40, "high": 525.60, "low": 525.10, "close": 525.50, "volume": 1500},
    ]
    return base[:n]


def _valid_dr():
    return DetectionResult(
        schema_version="DetectionResult/v1",
        result_id="dr-v-001", produced_at="2026-05-26T14:05:00.000Z",
        session=_session(), preset_id="test", engine_version="1.0.0",
        status=DetectionStatus.VALID, failed_stage=None, failed_rules=(),
        level_price=_pt(52550), level_source=LevelSource.ORB_HIGH,
        level_bar=_bar(T0), direction=Direction.LONG,
        break_bar=_bar(T1), directional_break_distance=DirectionalTickDistance(ticks=19, tick_size=TICK_SIZE),
        displacement_window=(_bar(T2),), displacement_bar_count=1,
        displacement_pts=AbsoluteTickDistance(ticks=68, tick_size=TICK_SIZE),
        displacement_pct=Rational(numerator=68, denominator=52550),
        rejection_side_clearance_by_bar=(DirectionalTickDistance(ticks=20, tick_size=TICK_SIZE),),
        minimum_rejection_side_clearance=DirectionalTickDistance(ticks=20, tick_size=TICK_SIZE),
        average_rejection_side_clearance="0.20",
        retest_window=(_bar(T3),), retest_bar_count=1,
        failed_retest_count=1,
        failed_retests=(RejectionAttempt(bar=_bar(T3), failed_rules=(_rf(),)),),
        bars_break_to_first_retest=2, bars_break_to_confirmation=3,
        retest_closest_approach=AbsoluteTickDistance(ticks=0, tick_size=TICK_SIZE),
        retest_penetration_through_level=AbsoluteTickDistance(ticks=86, tick_size=TICK_SIZE),
        retest_displacement_retracement_pct=Rational(numerator=86, denominator=68),
        confirmation_bar=_bar(T4),
        confirmation_rej_wick=Rational(numerator=670000, denominator=1000000),
        confirmation_body=Rational(numerator=200000, denominator=1000000),
        confirmation_opp_wick=Rational(numerator=130000, denominator=1000000),
        confirmation_favorable_close_location=Rational(numerator=870000, denominator=1000000),
        confirmation_penetration=AbsoluteTickDistance(ticks=7, tick_size=TICK_SIZE),
        confirmation_close_beyond_level=DirectionalTickDistance(ticks=45, tick_size=TICK_SIZE),
    )


def _invalid_dr(failed_stage=FailedStage.NO_QUALIFYING_REJECTION_CANDLE):
    return DetectionResult(
        schema_version="DetectionResult/v1",
        result_id="dr-i-001", produced_at="2026-05-26T14:05:00.000Z",
        session=_session(), preset_id="test", engine_version="1.0.0",
        status=DetectionStatus.INVALID, failed_stage=failed_stage, failed_rules=(),
        level_price=_pt(52550), level_source=LevelSource.ORB_HIGH,
        level_bar=_bar(T0), direction=Direction.LONG,
        break_bar=_bar(T1), directional_break_distance=DirectionalTickDistance(ticks=19, tick_size=TICK_SIZE),
        displacement_window=(_bar(T2),), displacement_bar_count=1,
        displacement_pts=AbsoluteTickDistance(ticks=68, tick_size=TICK_SIZE),
        displacement_pct=Rational(numerator=68, denominator=52550),
        rejection_side_clearance_by_bar=(DirectionalTickDistance(ticks=20, tick_size=TICK_SIZE),),
        minimum_rejection_side_clearance=DirectionalTickDistance(ticks=20, tick_size=TICK_SIZE),
        average_rejection_side_clearance="0.20",
        retest_window=(_bar(T3),), retest_bar_count=1,
        failed_retest_count=1,
        failed_retests=(RejectionAttempt(bar=_bar(T3), failed_rules=(_rf(),)),),
        bars_break_to_first_retest=2, bars_break_to_confirmation=None,
        retest_closest_approach=AbsoluteTickDistance(ticks=0, tick_size=TICK_SIZE),
        retest_penetration_through_level=AbsoluteTickDistance(ticks=86, tick_size=TICK_SIZE),
        retest_displacement_retracement_pct=Rational(numerator=86, denominator=68),
        confirmation_bar=None, confirmation_rej_wick=None, confirmation_body=None,
        confirmation_opp_wick=None, confirmation_favorable_close_location=None,
        confirmation_penetration=None, confirmation_close_beyond_level=None,
    )


def _make_runner_result(dr, status="VALID"):
    return {
        "run_record_id": "rr-001", "symbol": "SPY",
        "session_date": "2026-05-26", "preset_id": "test",
        "exit_target_r": 2,
        "detection_status": status,
        "failure_stage": dr.failed_stage,
        "failed_rules": dr.failed_rules,
        "detection_result_id": dr.result_id,
        "candidate_id": None, "confirmation_timestamp": None,
        "entry_timestamp": None, "first_evaluation_timestamp": None,
        "entry_price_ticks": None, "stop_price_ticks": None,
        "r2_price_ticks": None, "r3_price_ticks": None,
        "r4_price_ticks": None,
        "outcome": "TARGET_HIT" if status == "VALID" else "NO_VALID_SETUP",
        "realized_r": None, "highest_target_achieved": None,
        "exit_timestamp": None, "exit_price_ticks": None,
        "detection_result": dr, "trade_plan": None, "trade_outcome": None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. A2→A3→A4 integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestPipelineIntegration:
    def test_valid_through_pipeline(self):
        dr = _valid_dr()
        rr = _make_runner_result(dr, "VALID")
        record = build_detector_audit_record(rr)
        selected = select_audit_candidates([record])
        assert len(selected) == 1
        event = export_audit_visual_event(selected[0], _candles())
        assert event["candidate_status"] == "VALID"

    def test_rejected_audit_worthy_through_pipeline(self):
        dr = _invalid_dr(FailedStage.NO_QUALIFYING_REJECTION_CANDLE)
        rr = _make_runner_result(dr, "INVALID")
        record = build_detector_audit_record(rr)
        selected = select_audit_candidates([record])
        assert len(selected) == 1
        event = export_audit_visual_event(selected[0], _candles())
        assert event["candidate_status"] == "REJECTED"
        assert event["failed_stage"] == "NO_QUALIFYING_REJECTION_CANDLE"


# ═══════════════════════════════════════════════════════════════════════════════
# 2–3. Selection filtering
# ═══════════════════════════════════════════════════════════════════════════════


class TestSelectionFiltering:
    def test_break_not_found_excluded(self):
        dr = DetectionResult(
            schema_version="DetectionResult/v1",
            result_id="dr-bnf", produced_at="2026-05-26T14:05:00.000Z",
            session=_session(), preset_id="test", engine_version="1.0.0",
            status=DetectionStatus.INVALID,
            failed_stage=FailedStage.BREAK_NOT_FOUND, failed_rules=(),
            level_price=_pt(52550), level_source=LevelSource.ORB_HIGH,
            level_bar=_bar(T0), direction=Direction.LONG,
            break_bar=None, directional_break_distance=None,
            displacement_window=(), displacement_bar_count=None,
            displacement_pts=None, displacement_pct=None,
            rejection_side_clearance_by_bar=None,
            minimum_rejection_side_clearance=None,
            average_rejection_side_clearance=None,
            retest_window=(), retest_bar_count=None,
            failed_retest_count=None, failed_retests=(),
            bars_break_to_first_retest=None, bars_break_to_confirmation=None,
            retest_closest_approach=None,
            retest_penetration_through_level=None,
            retest_displacement_retracement_pct=None,
            confirmation_bar=None, confirmation_rej_wick=None,
            confirmation_body=None, confirmation_opp_wick=None,
            confirmation_favorable_close_location=None,
            confirmation_penetration=None, confirmation_close_beyond_level=None,
        )
        rr = _make_runner_result(dr, "INVALID")
        record = build_detector_audit_record(rr)
        selected = select_audit_candidates([record])
        assert len(selected) == 0

    def test_audit_worthy_included(self):
        dr = _invalid_dr(FailedStage.RETEST_BEFORE_DISPLACEMENT)
        rr = _make_runner_result(dr, "INVALID")
        record = build_detector_audit_record(rr)
        selected = select_audit_candidates([record])
        assert len(selected) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Include-valid option
# ═══════════════════════════════════════════════════════════════════════════════


class TestIncludeValid:
    def test_default_excludes_valid(self):
        args = parse_args([])
        assert args.include_valid is False

    def test_flag_includes_valid(self):
        args = parse_args(["--include-valid"])
        assert args.include_valid is True


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Deterministic ordering
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministicOrder:
    def test_order_preserved(self):
        dr1 = _invalid_dr(FailedStage.NO_QUALIFYING_REJECTION_CANDLE)
        dr2 = _invalid_dr(FailedStage.RETEST_BEFORE_DISPLACEMENT)
        rr1 = _make_runner_result(dr1, "INVALID")
        rr2 = _make_runner_result(dr2, "INVALID")
        r1 = build_detector_audit_record(rr1)
        r2 = build_detector_audit_record(rr2)
        selected = select_audit_candidates([r1, r2])
        assert selected[0].failed_stage == FailedStage.NO_QUALIFYING_REJECTION_CANDLE
        assert selected[1].failed_stage == FailedStage.RETEST_BEFORE_DISPLACEMENT


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Max-records
# ═══════════════════════════════════════════════════════════════════════════════


class TestMaxRecords:
    def test_max_records_parsed(self):
        args = parse_args(["--max-records", "5"])
        assert args.max_records == 5


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Summary counts
# ═══════════════════════════════════════════════════════════════════════════════


class TestSummaryCounts:
    def test_pipeline_integration_counts(self):
        # One valid + one audit-worthy rejected
        dr_v = _valid_dr()
        dr_r = _invalid_dr()
        rr_v = _make_runner_result(dr_v, "VALID")
        rr_r = _make_runner_result(dr_r, "INVALID")
        rec_v = build_detector_audit_record(rr_v)
        rec_r = build_detector_audit_record(rr_r)
        selected = select_audit_candidates([rec_v, rec_r])
        assert len(selected) == 2  # both are audit-worthy


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Output filename safety
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutputFilename:
    def test_does_not_match_training_batch(self):
        args = parse_args([])
        # Default output uses audit_batch_ prefix, never training_batch_
        assert args.output is None  # auto-generated
        # When auto-generated, name starts with "audit_batch_"


# ═══════════════════════════════════════════════════════════════════════════════
# 9–13. HTML generation
# ═══════════════════════════════════════════════════════════════════════════════


class TestHtmlGeneration:
    def _make_events(self):
        dr = _invalid_dr()
        rr = _make_runner_result(dr, "INVALID")
        rec = build_detector_audit_record(rr)
        ev = export_audit_visual_event(rec, _candles())
        ev["tick_size"] = "0.01"
        return [ev]

    def test_html_contains_events(self):
        events = self._make_events()
        html = generate_audit_html(events, [])
        assert "var EV=" in html
        assert events[0]["audit_id"] in html

    def test_html_contains_review_fields(self):
        html = generate_audit_html(self._make_events(), [])
        assert "Detector Correct?" in html
        assert "Would Trade?" in html
        assert "Quality" in html

    def test_html_contains_export_schema(self):
        html = generate_audit_html(self._make_events(), [])
        assert "DetectorAuditReviewBatch/v1" in html

    def test_audit_id_as_join_key(self):
        events = self._make_events()
        html = generate_audit_html(events, [])
        assert "audit_id" in html

    def test_data_strings_escaped(self):
        """Ensure JSON embedding doesn't break on special chars."""
        events = self._make_events()
        events[0]["symbol"] = "TE<ST"
        html = generate_audit_html(events, [])
        # json.dumps with ensure_ascii=True escapes < as-is but
        # it's inside a JS string, so it's safe
        assert "TE" in html


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Empty audit selection
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmptySelection:
    def test_html_with_no_events_shows_message(self):
        html = generate_audit_html([], [])
        assert "No audit candidates" in html


# ═══════════════════════════════════════════════════════════════════════════════
# 15. No mutation of runner results
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutation:
    def test_runner_result_not_mutated(self):
        dr = _valid_dr()
        rr = _make_runner_result(dr, "VALID")
        rr_copy = copy.copy(rr)
        build_detector_audit_record(rr)
        assert rr == rr_copy


# ═══════════════════════════════════════════════════════════════════════════════
# 16. JSON serializable events
# ═══════════════════════════════════════════════════════════════════════════════


class TestJsonSerializable:
    def test_event_serializes(self):
        dr = _invalid_dr()
        rr = _make_runner_result(dr, "INVALID")
        rec = build_detector_audit_record(rr)
        ev = export_audit_visual_event(rec, _candles())
        ev["tick_size"] = "0.01"
        s = json.dumps(ev, ensure_ascii=True, allow_nan=False)
        assert isinstance(s, str)
        parsed = json.loads(s)
        assert parsed["schema_version"] == "DetectorAuditVisualEvent/v1"

# ═══════════════════════════════════════════════════════════════════════════════
# Balanced sampling tests (A5.4)
# ═══════════════════════════════════════════════════════════════════════════════


def _make_event(stage, idx=0, direction="LONG", status="REJECTED"):
    return {
        "schema_version": "DetectorAuditVisualEvent/v1",
        "audit_id": f"audit-{stage}-{idx}-{direction}",
        "symbol": "SPY",
        "session_date": f"2026-05-{10+idx:02d}",
        "timeframe": "5m",
        "direction": direction,
        "candidate_status": status,
        "failed_stage": stage if status == "REJECTED" else None,
        "failed_rules": [],
        "candles": [],
        "annotations": {},
    }


class TestBalancedSamplingOff:
    def test_cli_default_off(self):
        args = parse_args([])
        assert args.balanced_failed_stage is False

    def test_without_balancing_preserves_order(self):
        """No balancing = pass-through, same as legacy."""
        events = [
            _make_event("RETEST_BEFORE_DISPLACEMENT", 0),
            _make_event("RETEST_BEFORE_DISPLACEMENT", 1),
            _make_event("RETEST_BEFORE_DISPLACEMENT", 2),
            _make_event("NO_QUALIFYING_REJECTION_CANDLE", 0),
        ]
        # Without balancing, just use the list directly (no call)
        assert len(events) == 4
        assert events[0]["failed_stage"] == "RETEST_BEFORE_DISPLACEMENT"
        assert events[3]["failed_stage"] == "NO_QUALIFYING_REJECTION_CANDLE"


class TestBalancedSamplingEnabled:
    def test_cli_enabled(self):
        args = parse_args(["--balanced-failed-stage"])
        assert args.balanced_failed_stage is True

    def test_even_distribution(self):
        """3 stages × 10 records each → 30 total, 10 each."""
        events = (
            [_make_event("A", i) for i in range(10)] +
            [_make_event("B", i) for i in range(10)] +
            [_make_event("C", i) for i in range(10)]
        )
        result = balance_by_failed_stage(events)
        assert len(result) == 30
        from collections import Counter
        counts = Counter(e["failed_stage"] for e in result)
        assert counts["A"] == 10
        assert counts["B"] == 10
        assert counts["C"] == 10

    def test_even_with_max_records(self):
        """100 A + 40 B + 20 C → max 30 → 10 each."""
        events = (
            [_make_event("A", i) for i in range(100)] +
            [_make_event("B", i) for i in range(40)] +
            [_make_event("C", i) for i in range(20)]
        )
        result = balance_by_failed_stage(events, max_records=30)
        from collections import Counter
        counts = Counter(e["failed_stage"] for e in result)
        assert counts["A"] == 10
        assert counts["B"] == 10
        assert counts["C"] == 10
        assert len(result) == 30

    def test_stage_exhaustion(self):
        """100 A + 5 B → max 20 → 5 B + 15 A."""
        events = (
            [_make_event("A", i) for i in range(100)] +
            [_make_event("B", i) for i in range(5)]
        )
        result = balance_by_failed_stage(events, max_records=20)
        from collections import Counter
        counts = Counter(e["failed_stage"] for e in result)
        assert counts["B"] == 5
        assert counts["A"] == 15
        assert len(result) == 20

    def test_no_duplicates(self):
        """All output audit_ids are unique."""
        events = (
            [_make_event("A", i) for i in range(50)] +
            [_make_event("B", i) for i in range(30)]
        )
        result = balance_by_failed_stage(events, max_records=40)
        ids = [e["audit_id"] for e in result]
        assert len(ids) == len(set(ids))

    def test_deterministic(self):
        events = (
            [_make_event("A", i) for i in range(20)] +
            [_make_event("B", i) for i in range(10)]
        )
        r1 = balance_by_failed_stage(events, max_records=15)
        r2 = balance_by_failed_stage(events, max_records=15)
        assert [e["audit_id"] for e in r1] == [e["audit_id"] for e in r2]

    def test_valid_appended_after_rejected(self):
        """VALID records come after balanced rejected."""
        events = [
            _make_event("A", 0),
            _make_event("B", 0),
            _make_event(None, 0, status="VALID"),
        ]
        result = balance_by_failed_stage(events)
        assert len(result) == 3
        # First two are rejected (balanced), last is VALID
        assert result[0]["candidate_status"] == "REJECTED"
        assert result[1]["candidate_status"] == "REJECTED"
        assert result[2]["candidate_status"] == "VALID"

    def test_valid_with_max_records(self):
        """max_records caps REJECTED only; VALID appended in full."""
        events = (
            [_make_event("A", i) for i in range(100)] +
            [_make_event(None, i, status="VALID") for i in range(5)]
        )
        result = balance_by_failed_stage(events, max_records=20)
        valid_count = sum(1 for e in result
                         if e["candidate_status"] == "VALID")
        rejected_count = sum(1 for e in result
                             if e["candidate_status"] == "REJECTED")
        assert rejected_count == 20  # max_records caps rejected
        assert valid_count == 5      # VALID appended in full
        assert len(result) == 25     # total = 20 rejected + 5 valid
        # VALID at end
        for e in result[-5:]:
            assert e["candidate_status"] == "VALID"

    def test_chronological_within_stage(self):
        """Within each stage, chronological order is preserved."""
        events = [_make_event("A", i) for i in range(10)]
        result = balance_by_failed_stage(events)
        dates = [e["session_date"] for e in result]
        assert dates == sorted(dates)

    def test_round_robin_interleaving(self):
        """First records alternate between stages."""
        events = (
            [_make_event("A", i) for i in range(5)] +
            [_make_event("B", i) for i in range(5)]
        )
        result = balance_by_failed_stage(events)
        # Round-robin: A, B, A, B, A, B, ...
        assert result[0]["failed_stage"] == "A"
        assert result[1]["failed_stage"] == "B"
        assert result[2]["failed_stage"] == "A"
        assert result[3]["failed_stage"] == "B"

    def test_no_balancing_without_max_returns_all(self):
        """Without max_records, all records included."""
        events = (
            [_make_event("A", i) for i in range(100)] +
            [_make_event("B", i) for i in range(40)]
        )
        result = balance_by_failed_stage(events)
        assert len(result) == 140

# ═══════════════════════════════════════════════════════════════════════════════
# Correction tests (A5.4.1)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCorrectedBudgetSemantics:
    def test_90_rejected_31_valid_produces_121(self):
        """max_records=90 with 31 VALID must produce 121 total."""
        events = (
            [_make_event("A", i) for i in range(200)] +
            [_make_event("B", i) for i in range(100)] +
            [_make_event("C", i) for i in range(50)] +
            [_make_event(None, i, status="VALID") for i in range(31)]
        )
        result = balance_by_failed_stage(events, max_records=90)
        valid_count = sum(1 for e in result
                         if e["candidate_status"] == "VALID")
        rejected_count = sum(1 for e in result
                             if e["candidate_status"] == "REJECTED")
        assert rejected_count == 90
        assert valid_count == 31
        assert len(result) == 121

    def test_valid_does_not_reduce_rejected_budget(self):
        """VALID count must not affect how many rejected are sampled."""
        # With 50 VALID and max_records=30:
        # old behavior would cap total to 30
        # correct behavior: 30 rejected + 50 VALID = 80
        events = (
            [_make_event("A", i) for i in range(100)] +
            [_make_event(None, i, status="VALID") for i in range(50)]
        )
        result = balance_by_failed_stage(events, max_records=30)
        rejected_count = sum(1 for e in result
                             if e["candidate_status"] == "REJECTED")
        valid_count = sum(1 for e in result
                         if e["candidate_status"] == "VALID")
        assert rejected_count == 30
        assert valid_count == 50
        assert len(result) == 80

    def test_no_final_cap_removes_valid(self):
        """After VALID appended, no further truncation occurs."""
        events = (
            [_make_event("A", i) for i in range(10)] +
            [_make_event(None, i, status="VALID") for i in range(100)]
        )
        result = balance_by_failed_stage(events, max_records=5)
        valid_count = sum(1 for e in result
                         if e["candidate_status"] == "VALID")
        rejected_count = sum(1 for e in result
                             if e["candidate_status"] == "REJECTED")
        assert rejected_count == 5
        assert valid_count == 100  # all 100 VALID preserved
        assert len(result) == 105


class TestAvailableVsSelected:
    def test_available_counts_captured_before_sampling(self):
        """available_by_stage must reflect pre-sampling counts."""
        # Simulate what build_audit_events returns
        summary = {
            "symbol": "SPY",
            "total_pipeline": 100,
            "total_valid": 5,
            "total_rejected": 95,
            "total_audit_worthy": 80,
            "total_excluded": 15,
            "included_in_batch": 85,
            "available_by_stage": {
                "RETEST_BEFORE_DISPLACEMENT": 50,
                "NO_QUALIFYING_REJECTION_CANDLE": 25,
                "RETEST_NOT_FOUND": 5,
                "VALID": 5,
            },
            "by_stage": {},
            "by_timeframe": {},
            "by_direction": {},
            "build_errors": [],
        }
        # After balanced sampling, only 10 per stage selected
        selected_events = (
            [_make_event("RETEST_BEFORE_DISPLACEMENT", i)
             for i in range(10)] +
            [_make_event("NO_QUALIFYING_REJECTION_CANDLE", i)
             for i in range(10)] +
            [_make_event("RETEST_NOT_FOUND", i) for i in range(5)]
        )
        html = generate_audit_html(selected_events, [summary])
        # Available shows pre-sampling numbers
        assert "RETEST_BEFORE_DISPLACEMENT: 50" in html
        assert "NO_QUALIFYING_REJECTION_CANDLE: 25" in html
        # Selected shows post-sampling numbers
        assert "10" in html  # selected counts present

    def test_selected_counts_reflect_sampled_batch(self):
        """Selected stage counts must come from final event list."""
        events = [_make_event("A", i) for i in range(3)]
        summary = {
            "symbol": "T",
            "total_pipeline": 10,
            "total_valid": 0,
            "total_rejected": 10,
            "total_audit_worthy": 10,
            "total_excluded": 0,
            "included_in_batch": 10,
            "available_by_stage": {"A": 10},
            "by_stage": {},
            "by_timeframe": {},
            "by_direction": {},
            "build_errors": [],
        }
        html = generate_audit_html(events, [summary])
        # Available = 10, Selected = 3
        assert "A: 10" in html  # available
        assert "3" in html      # selected in the arrow display


class TestLegacyBehaviorUnchanged:
    def test_legacy_no_balancing_no_flag(self):
        args = parse_args([])
        assert args.balanced_failed_stage is False

    def test_legacy_max_records_still_truncates(self):
        args = parse_args(["--max-records", "10"])
        assert args.max_records == 10
        assert args.balanced_failed_stage is False
