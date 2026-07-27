"""Tests for canonical buildDetectionResult port.

Mirrors estrategie/test_bdrr_detection_result.js (161 checks).
"""

import copy
import math
import re
import uuid

import pytest

from trading_lab.detection_result_builder import (
    build_detection_result,
    _float_to_rational,
)
from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.enums import (
    DetectionStatus, FailedStage, Direction, LevelSource, Stage, ValueType,
)
from trading_lab.contracts.primitives import PriceTicks, Rational
from trading_lab.contracts.bar import Bar
from trading_lab.contracts.distances import (
    AbsoluteTickDistance, DirectionalTickDistance,
)
from trading_lab.contracts.rule_failure import RejectionAttempt, RuleFailure
from trading_lab.contracts.session_metadata import SessionMetadata


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK_SIZE = 0.01

def _session_meta():
    return {
        "symbol": "SPY",
        "date": "2026-07-01",
        "market_timezone": "America/New_York",
        "session_open_utc_ms": 1782912600000,
        "session_close_utc_ms": 1782935700000,
        "timeframe_seconds": 300,
    }

def _metadata(**overrides):
    m = {
        "tick_size": TICK_SIZE,
        "session": _session_meta(),
        "preset_id": "default",
        "engine_version": "1.0.0",
    }
    m.update(overrides)
    return m

def _candle(time_ms=1000, open_=100.0, high=101.0, low=99.0, close=100.5):
    return {"time_ms": time_ms, "open": open_, "high": high,
            "low": low, "close": close}

def _orb_ok(**kw):
    d = {
        "status": "OK", "date": "2026-07-01",
        "orb_candle_index": 0, "orb_candle": _candle(),
        "orb_high": 101.0, "orb_low": 99.0,
        "orb_low_active": False,
        "level_source": "ORB_HIGH", "level_price": 101.0,
        "level_price_ticks": 10100, "direction": "LONG",
    }
    d.update(kw)
    return d

def _break_ok(**kw):
    d = {
        "status": "OK", "date": "2026-07-01",
        "break_candle_index": 1, "break_candle": _candle(time_ms=2000, close=101.5),
        "break_timestamp": 2000,
        "directional_break_distance": {"ticks": 50, "points": 0.5},
    }
    d.update(kw)
    return d

def _disp_ok(**kw):
    d = {
        "status": "OK", "date": "2026-07-01",
        "level_price": 101.0, "break_candle_index": 1,
        "displacement_start_index": 2, "displacement_end_index": 2,
        "displacement_bar_count": 1,
        "displacement_window": [_candle(time_ms=3000, high=102.0, low=101.1)],
        "max_favorable_high": 102.0,
        "displacement_distance": {"ticks": 100, "points": 1.0},
        "first_retest_contact_index": 3,
        "first_retest_contact_candle": _candle(time_ms=4000, low=100.9),
        "first_retest_contact_timestamp": 4000,
    }
    d.update(kw)
    return d

def _retest_ok(**kw):
    d = {
        "status": "OK", "date": "2026-07-01",
        "level_price": 101.0,
        "retest_start_index": 3, "retest_start_timestamp": 4000,
        "retest_window_start_index": 3, "retest_window_end_index": 4,
        "retest_window": [
            _candle(time_ms=4000, low=100.9),
            _candle(time_ms=5000, low=100.8, close=101.5),
        ],
        "retest_contacts": [
            {"candle_index": 3, "candle": _candle(time_ms=4000, low=100.9),
             "timestamp": 4000},
            {"candle_index": 4, "candle": _candle(time_ms=5000, low=100.8, close=101.5),
             "timestamp": 5000},
        ],
        "retest_contact_count": 2,
    }
    d.update(kw)
    return d

def _rej_ok(**kw):
    d = {
        "status": "OK", "date": "2026-07-01",
        "level_price": 101.0,
        "confirmation_candle_index": 4,
        "confirmation_candle": _candle(time_ms=5000, open_=100.5, high=102.0,
                                       low=100.8, close=101.5),
        "confirmation_timestamp": 5000,
        "geometry": {
            "range_ticks": 120,
            "body_ticks": 100,
            "rejection_wick_ticks": 70,
            "opposite_wick_ticks": 50,
            "rejection_wick_ratio": 70 / 120,
            "body_ratio": 100 / 120,
            "favorable_close_location": 70 / 120,
            "opposite_wick_ratio": 50 / 120,
            "penetration_through_level_ticks": 20,
            "penetration_through_level_points": 0.20,
            "close_beyond_level_ticks": 50,
            "close_beyond_level_points": 0.50,
        },
        "failed_retests": [],
        "failed_retest_count": 0,
    }
    d.update(kw)
    return d

def _rej_failed(**kw):
    d = {
        "status": "FAILED",
        "failed_stage": "NO_QUALIFYING_REJECTION_CANDLE",
        "reason": "no qualifying candle",
        "failed_retests": [
            {
                "candle_index": 3,
                "candle": _candle(time_ms=4000, low=100.9),
                "timestamp": 4000,
                "geometry": {
                    "range_ticks": 200,
                    "rejection_wick_ratio": 0.10,
                    "body_ratio": 0.80,
                    "favorable_close_location": 0.15,
                },
                "failed_rules": ["REJECTION_WICK_RATIO_TOO_LOW",
                                 "BODY_RATIO_TOO_HIGH",
                                 "FAVORABLE_CLOSE_LOCATION_TOO_LOW"],
            },
        ],
        "failed_retest_count": 1,
    }
    d.update(kw)
    return d

def _full_stage_outputs(rej=None, **kw):
    so = {
        "orb": _orb_ok(),
        "break_result": _break_ok(),
        "disp_result": _disp_ok(),
        "retest_result": _retest_ok(),
        "rej_result": rej or _rej_ok(),
    }
    so.update(kw)
    return so


# ═══════════════════════════════════════════════════════════════════════════════
# Wrapper shape
# ═══════════════════════════════════════════════════════════════════════════════


class TestWrapperShape:
    def test_success_wrapper(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert r["status"] == "OK"
        assert "detection_result" in r
        assert isinstance(r["detection_result"], DetectionResult)

    def test_failure_wrapper(self):
        r = build_detection_result(None, _metadata())
        assert r["status"] == "FAILED"
        assert "failure_code" in r
        assert "reason" in r


# ═══════════════════════════════════════════════════════════════════════════════
# Schema version and identity fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdentityFields:
    def test_schema_version(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dr = r["detection_result"]
        assert dr.schema_version == "DetectionResult/v1"

    def test_result_id_is_uuid4(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dr = r["detection_result"]
        parsed = uuid.UUID(dr.result_id)
        assert parsed.version == 4

    def test_result_id_unique(self):
        r1 = build_detection_result(_full_stage_outputs(), _metadata())
        r2 = build_detection_result(_full_stage_outputs(), _metadata())
        assert r1["detection_result"].result_id != r2["detection_result"].result_id

    def test_produced_at_iso8601(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dr = r["detection_result"]
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", dr.produced_at)

    def test_preset_id(self):
        r = build_detection_result(_full_stage_outputs(), _metadata(preset_id="my_preset"))
        assert r["detection_result"].preset_id == "my_preset"

    def test_engine_version(self):
        r = build_detection_result(_full_stage_outputs(), _metadata(engine_version="2.0"))
        assert r["detection_result"].engine_version == "2.0"


# ═══════════════════════════════════════════════════════════════════════════════
# Status mapping
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatusMapping:
    def test_ok_maps_to_valid(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert r["detection_result"].status == DetectionStatus.VALID

    def test_failed_maps_to_invalid(self):
        so = _full_stage_outputs(rej=_rej_failed())
        r = build_detection_result(so, _metadata())
        assert r["detection_result"].status == DetectionStatus.INVALID

    def test_valid_has_null_failed_stage(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert r["detection_result"].failed_stage is None

    def test_invalid_has_failed_stage(self):
        so = _full_stage_outputs(rej=_rej_failed())
        r = build_detection_result(so, _metadata())
        assert r["detection_result"].failed_stage == FailedStage.NO_QUALIFYING_REJECTION_CANDLE

    def test_valid_has_empty_failed_rules(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert r["detection_result"].failed_rules == ()


# ═══════════════════════════════════════════════════════════════════════════════
# Level fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestLevelFields:
    def test_level_price(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dr = r["detection_result"]
        assert isinstance(dr.level_price, PriceTicks)
        assert dr.level_price.ticks == 10100

    def test_level_source(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert r["detection_result"].level_source == LevelSource.ORB_HIGH

    def test_level_bar(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert isinstance(r["detection_result"].level_bar, Bar)

    def test_direction(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert r["detection_result"].direction == Direction.LONG

    def test_no_orb_level_null(self):
        so = _full_stage_outputs(rej=_rej_failed())
        so["orb"] = None
        r = build_detection_result(so, _metadata())
        dr = r["detection_result"]
        assert dr.level_price is None
        assert dr.level_source is None


# ═══════════════════════════════════════════════════════════════════════════════
# Break fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakFields:
    def test_break_bar(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert isinstance(r["detection_result"].break_bar, Bar)

    def test_directional_break_distance(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dbd = r["detection_result"].directional_break_distance
        assert isinstance(dbd, DirectionalTickDistance)
        assert dbd.ticks == 50


# ═══════════════════════════════════════════════════════════════════════════════
# Displacement fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestDisplacementFields:
    def test_displacement_window_is_tuple(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dw = r["detection_result"].displacement_window
        assert isinstance(dw, tuple)
        assert len(dw) == 1
        assert isinstance(dw[0], Bar)

    def test_displacement_bar_count(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert r["detection_result"].displacement_bar_count == 1

    def test_displacement_pts(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dp = r["detection_result"].displacement_pts
        assert isinstance(dp, AbsoluteTickDistance)
        assert dp.ticks == 100

    def test_displacement_pct(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dp = r["detection_result"].displacement_pct
        assert isinstance(dp, Rational)
        assert dp.numerator == 100
        assert dp.denominator == 10100

    def test_rejection_side_clearance(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        rsc = r["detection_result"].rejection_side_clearance_by_bar
        assert isinstance(rsc, tuple)
        assert len(rsc) == 1
        assert isinstance(rsc[0], DirectionalTickDistance)

    def test_minimum_rejection_side_clearance(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert isinstance(r["detection_result"].minimum_rejection_side_clearance,
                          DirectionalTickDistance)

    def test_average_rejection_side_clearance_is_string(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        avg = r["detection_result"].average_rejection_side_clearance
        assert isinstance(avg, str)

    def test_no_disp_empty_window(self):
        so = _full_stage_outputs(rej=_rej_failed())
        so["disp_result"] = None
        r = build_detection_result(so, _metadata())
        assert r["detection_result"].displacement_window == ()


# ═══════════════════════════════════════════════════════════════════════════════
# Retest fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetestFields:
    def test_retest_window_is_tuple(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        rw = r["detection_result"].retest_window
        assert isinstance(rw, tuple)

    def test_valid_retest_bounded_to_confirmation(self):
        """VALID: retest window bounded to confirmation candle (inclusive)."""
        r = build_detection_result(_full_stage_outputs(), _metadata())
        rw = r["detection_result"].retest_window
        # confirmation candle is at time_ms=5000, retest window has 2 candles
        # (4000, 5000), both should be included
        assert len(rw) == 2

    def test_invalid_retest_uses_full_window(self):
        so = _full_stage_outputs(rej=_rej_failed())
        r = build_detection_result(so, _metadata())
        rw = r["detection_result"].retest_window
        assert len(rw) == 2  # full window

    def test_retest_bar_count(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert r["detection_result"].retest_bar_count == 2

    def test_bars_break_to_first_retest(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        # retest_start=3, break=1 → 2
        assert r["detection_result"].bars_break_to_first_retest == 2

    def test_bars_break_to_confirmation(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        # confirmation=4, break=1 → 3
        assert r["detection_result"].bars_break_to_confirmation == 3

    def test_retest_closest_approach(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        rca = r["detection_result"].retest_closest_approach
        assert isinstance(rca, AbsoluteTickDistance)
        assert rca.ticks >= 0

    def test_retest_penetration(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        rp = r["detection_result"].retest_penetration_through_level
        assert isinstance(rp, AbsoluteTickDistance)
        assert rp.ticks >= 0

    def test_no_retest_empty_window(self):
        so = _full_stage_outputs(rej=_rej_failed())
        so["retest_result"] = None
        r = build_detection_result(so, _metadata())
        assert r["detection_result"].retest_window == ()


# ═══════════════════════════════════════════════════════════════════════════════
# Failed retests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailedRetests:
    def test_failed_retests_tuple(self):
        so = _full_stage_outputs(rej=_rej_failed())
        r = build_detection_result(so, _metadata())
        fr = r["detection_result"].failed_retests
        assert isinstance(fr, tuple)
        assert len(fr) == 1
        assert isinstance(fr[0], RejectionAttempt)

    def test_failed_retest_bar(self):
        so = _full_stage_outputs(rej=_rej_failed())
        r = build_detection_result(so, _metadata())
        fr = r["detection_result"].failed_retests[0]
        assert isinstance(fr.bar, Bar)

    def test_failed_retest_rules(self):
        so = _full_stage_outputs(rej=_rej_failed())
        r = build_detection_result(so, _metadata())
        fr = r["detection_result"].failed_retests[0]
        assert len(fr.failed_rules) == 3
        assert isinstance(fr.failed_rules[0], RuleFailure)
        assert fr.failed_rules[0].rule_id == "REJECTION_WICK_RATIO_TOO_LOW"
        assert fr.failed_rules[0].stage == Stage.REJECTION_CANDLE
        assert fr.failed_rules[0].value_type == ValueType.BOOLEAN

    def test_failed_retest_count(self):
        so = _full_stage_outputs(rej=_rej_failed())
        r = build_detection_result(so, _metadata())
        assert r["detection_result"].failed_retest_count == 1

    def test_empty_failed_retests(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert r["detection_result"].failed_retests == ()
        assert r["detection_result"].failed_retest_count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Confirmation candle fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfirmationFields:
    def test_confirmation_bar(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        cb = r["detection_result"].confirmation_bar
        assert isinstance(cb, Bar)

    def test_confirmation_rej_wick(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert isinstance(r["detection_result"].confirmation_rej_wick, Rational)

    def test_confirmation_body(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        assert isinstance(r["detection_result"].confirmation_body, Rational)

    def test_confirmation_penetration(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        cp = r["detection_result"].confirmation_penetration
        assert isinstance(cp, AbsoluteTickDistance)
        assert cp.ticks == 20

    def test_confirmation_close_beyond_level(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        cbl = r["detection_result"].confirmation_close_beyond_level
        assert isinstance(cbl, DirectionalTickDistance)
        assert cbl.ticks == 50

    def test_invalid_has_null_confirmation(self):
        so = _full_stage_outputs(rej=_rej_failed())
        r = build_detection_result(so, _metadata())
        dr = r["detection_result"]
        assert dr.confirmation_bar is None
        assert dr.confirmation_rej_wick is None
        assert dr.confirmation_penetration is None


# ═══════════════════════════════════════════════════════════════════════════════
# Session metadata
# ═══════════════════════════════════════════════════════════════════════════════


class TestSessionMetadata:
    def test_session_preserved(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        s = r["detection_result"].session
        assert isinstance(s, SessionMetadata)
        assert s.symbol == "SPY"
        assert s.date == "2026-07-01"
        assert s.market_timezone == "America/New_York"
        assert s.timeframe_seconds == 300


# ═══════════════════════════════════════════════════════════════════════════════
# floatToRational
# ═══════════════════════════════════════════════════════════════════════════════


class TestFloatToRational:
    def test_normal(self):
        r = _float_to_rational(0.47)
        assert r == Rational(numerator=470000, denominator=1000000)

    def test_zero(self):
        r = _float_to_rational(0.0)
        assert r == Rational(numerator=0, denominator=1000000)

    def test_negative(self):
        r = _float_to_rational(-0.5)
        assert r == Rational(numerator=-500000, denominator=1000000)

    def test_none(self):
        assert _float_to_rational(None) is None

    def test_nan(self):
        assert _float_to_rational(float("nan")) is None

    def test_inf(self):
        assert _float_to_rational(float("inf")) is None

    def test_js_math_round_half(self):
        """JS Math.round(0.5) = 1 → floor(0.5 + 0.5) = 1."""
        # 0.0000005 * 1000000 = 0.5 → should round to 1
        r = _float_to_rational(0.0000005)
        assert r.numerator == 1

    def test_js_math_round_negative_half(self):
        """JS Math.round(-0.5) = 0 → floor(-0.5 + 0.5) = 0."""
        r = _float_to_rational(-0.0000005)
        assert r.numerator == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Metadata validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetadataValidation:
    def test_null_metadata(self):
        r = build_detection_result(_full_stage_outputs(), None)
        assert r["failure_code"] == "INVALID_METADATA"

    def test_missing_tick_size(self):
        md = _metadata()
        del md["tick_size"]
        r = build_detection_result(_full_stage_outputs(), md)
        assert r["failure_code"] == "INVALID_METADATA"
        assert "tick_size" in r["reason"]

    def test_non_positive_tick_size(self):
        r = build_detection_result(_full_stage_outputs(), _metadata(tick_size=-0.01))
        assert r["failure_code"] == "INVALID_METADATA"

    def test_missing_preset_id(self):
        md = _metadata()
        del md["preset_id"]
        r = build_detection_result(_full_stage_outputs(), md)
        assert r["failure_code"] == "INVALID_METADATA"
        assert "preset_id" in r["reason"]

    def test_missing_engine_version(self):
        md = _metadata()
        del md["engine_version"]
        r = build_detection_result(_full_stage_outputs(), md)
        assert r["failure_code"] == "INVALID_METADATA"

    def test_missing_session(self):
        md = _metadata()
        del md["session"]
        r = build_detection_result(_full_stage_outputs(), md)
        assert r["failure_code"] == "INVALID_METADATA"
        assert "session" in r["reason"]

    def test_missing_session_symbol(self):
        md = _metadata()
        del md["session"]["symbol"]
        r = build_detection_result(_full_stage_outputs(), md)
        assert r["failure_code"] == "INVALID_METADATA"
        assert "symbol" in r["reason"]

    def test_invalid_session_date(self):
        md = _metadata()
        md["session"]["date"] = "not-a-date"
        r = build_detection_result(_full_stage_outputs(), md)
        assert r["failure_code"] == "INVALID_METADATA"
        assert "date" in r["reason"]

    def test_missing_timeframe_seconds(self):
        md = _metadata()
        del md["session"]["timeframe_seconds"]
        r = build_detection_result(_full_stage_outputs(), md)
        assert r["failure_code"] == "INVALID_METADATA"
        assert "timeframe_seconds" in r["reason"]


# ═══════════════════════════════════════════════════════════════════════════════
# Stage outputs validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestStageOutputsValidation:
    def test_null_stage_outputs(self):
        r = build_detection_result(None, _metadata())
        assert r["failure_code"] == "INVALID_STAGE_OUTPUTS"

    def test_missing_rej_result(self):
        so = {"orb": _orb_ok()}
        r = build_detection_result(so, _metadata())
        assert r["failure_code"] == "INVALID_STAGE_OUTPUTS"
        assert "rejResult" in r["reason"]

    def test_invalid_rej_status(self):
        so = {"rej_result": {"status": "UNKNOWN"}}
        r = build_detection_result(so, _metadata())
        assert r["failure_code"] == "INVALID_STAGE_OUTPUTS"

    def test_validation_order_so_before_md(self):
        """Stage outputs validated before metadata."""
        r = build_detection_result(None, None)
        assert r["failure_code"] == "INVALID_STAGE_OUTPUTS"


# ═══════════════════════════════════════════════════════════════════════════════
# No mutation
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutation:
    def test_inputs_not_mutated(self):
        so = _full_stage_outputs()
        md = _metadata()
        so_copy = copy.deepcopy(so)
        md_copy = copy.deepcopy(md)
        build_detection_result(so, md)
        assert so == so_copy
        assert md == md_copy


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic (except UUID/timestamp)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministic:
    def test_same_structure(self):
        r1 = build_detection_result(_full_stage_outputs(), _metadata())
        r2 = build_detection_result(_full_stage_outputs(), _metadata())
        dr1 = r1["detection_result"]
        dr2 = r2["detection_result"]
        # All fields same except result_id and produced_at
        assert dr1.status == dr2.status
        assert dr1.level_price == dr2.level_price
        assert dr1.displacement_bar_count == dr2.displacement_bar_count


# ═══════════════════════════════════════════════════════════════════════════════
# Tick-size consistency
# ═══════════════════════════════════════════════════════════════════════════════


class TestTickSizeConsistency:
    def test_all_price_fields_same_tick_size(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dr = r["detection_result"]
        ts = str(TICK_SIZE)
        if dr.level_price:
            assert dr.level_price.tick_size == ts
        if dr.level_bar:
            assert dr.level_bar.open.tick_size == ts
        if dr.break_bar:
            assert dr.break_bar.open.tick_size == ts


# ═══════════════════════════════════════════════════════════════════════════════
# No extra fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoExtraFields:
    def test_38_fields(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dr = r["detection_result"]
        assert len(dr.__dataclass_fields__) == 38

    def test_no_scorer_fields(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        d = r["detection_result"].to_dict()
        assert "score" not in d
        assert "grade" not in d
        assert "trade_plan" not in d
        assert "outcome" not in d


# ═══════════════════════════════════════════════════════════════════════════════
# VALID with no upstream stages (only rej_result)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMinimalInputs:
    def test_valid_with_only_rej(self):
        so = {"rej_result": _rej_ok()}
        r = build_detection_result(so, _metadata())
        assert r["status"] == "OK"
        dr = r["detection_result"]
        assert dr.status == DetectionStatus.VALID
        assert dr.displacement_window == ()
        assert dr.retest_window == ()

    def test_invalid_with_only_rej(self):
        rej = _rej_failed()
        so = {"rej_result": rej}
        r = build_detection_result(so, _metadata())
        assert r["status"] == "OK"
        dr = r["detection_result"]
        assert dr.status == DetectionStatus.INVALID
        assert dr.level_price is None
        assert dr.level_source is None


# ═══════════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_detection_result_frozen(self):
        r = build_detection_result(_full_stage_outputs(), _metadata())
        dr = r["detection_result"]
        with pytest.raises(AttributeError):
            dr.status = DetectionStatus.INVALID
