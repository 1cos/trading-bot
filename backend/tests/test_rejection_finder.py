"""Tests for canonical findRejection port (Stage 5 — Rejection Qualification).

Mirrors estrategie/test_bdrr_stage5.js with Python fixtures.
Uses small, hand-built synthetic candle fixtures.

Run: pytest tests/test_rejection_finder.py -v
"""

import copy
import math

import pytest

from trading_lab.rejection_finder import find_rejection
from trading_lab.session_context import build_session_context
from trading_lab.orb_builder import build_orb
from trading_lab.break_finder import find_break
from trading_lab.displacement_finder import find_displacement
from trading_lab.retest_window import find_retest_window


# ── Fixtures ──────────────────────────────────────────────────────────────────

CONFIG = {
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "tick_size": 0.01,
    "min_displacement_ticks": None,
    "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": 1,
}

TICK_SIZE = 0.01
LEVEL = 101.00
LEVEL_TICKS = round(LEVEL / TICK_SIZE)  # 10100

# 2026-07-01 EDT (UTC-4)
MS_0930 = 1782912600000
MS_0935 = 1782912900000
MS_0940 = 1782913200000
MS_0945 = 1782913500000
MS_0950 = 1782913800000
MS_0955 = 1782914100000
MS_1000 = 1782914400000
MS_1005 = 1782914700000
MS_1010 = 1782915000000
MS_1015 = 1782915300000


def c(time_ms, open_=100.0, high=100.5, low=99.5, close=100.0):
    return {"time_ms": time_ms, "open": open_, "high": high, "low": low, "close": close}


def mk_candle(time_ms, *, low_ticks, range_ticks, open_ticks, close_ticks):
    """Build a candle from absolute tick offsets."""
    return {
        "time_ms": time_ms,
        "open": open_ticks / 100,
        "high": (low_ticks + range_ticks) / 100,
        "low": low_ticks / 100,
        "close": close_ticks / 100,
    }


def local_geometry(cnd):
    """Independent re-implementation for fixture self-checks."""
    h = round(cnd["high"] / TICK_SIZE)
    lo = round(cnd["low"] / TICK_SIZE)
    o = round(cnd["open"] / TICK_SIZE)
    cl = round(cnd["close"] / TICK_SIZE)
    rng = h - lo
    if rng == 0:
        return {"range_ticks": 0}
    return {
        "range_ticks": rng,
        "wick": (min(o, cl) - lo) / rng,
        "body": abs(cl - o) / rng,
        "close_loc": (cl - lo) / rng,
        "opp_wick": (h - max(o, cl)) / rng,
        "penetration_ticks": max(0, LEVEL_TICKS - lo),
        "close_beyond_ticks": cl - LEVEL_TICKS,
    }


def qualifying_candle(time_ms):
    """wick=0.50, body=0.30, closeLoc=0.80, low well below level."""
    return mk_candle(time_ms, low_ticks=9600, range_ticks=1000,
                     open_ticks=10100, close_ticks=10400)


def failing_candle_a(time_ms):
    """wick=0.10, body=0.05, closeLoc=0.15."""
    return mk_candle(time_ms, low_ticks=9600, range_ticks=1000,
                     open_ticks=9700, close_ticks=9750)


def failing_candle_b(time_ms):
    """wick=0.20, body=0.10, closeLoc=0.30."""
    return mk_candle(time_ms, low_ticks=9600, range_ticks=1000,
                     open_ticks=9800, close_ticks=9900)


def base_candles(extra):
    """ORB level 101.00, break 09:35, displacement 09:40."""
    return [
        c(MS_0930, high=101.0, low=99.0, close=100.5),       # ORB
        c(MS_0935, open_=100.50, high=101.50, low=100.30, close=101.20),  # break
        c(MS_0940, open_=101.20, high=101.60, low=101.10, close=101.30),  # displacement
    ] + extra


def run_full(candles_list, config=CONFIG):
    sc = build_session_context(candles_list, config)
    orb = build_orb(sc["candles"], sc, config)
    brk = find_break(sc["candles"], orb, config)
    disp = find_displacement(sc["candles"], orb, brk, config)
    rw = find_retest_window(sc["candles"], orb, brk, disp, config)
    rej = find_rejection(sc["candles"], orb, brk, disp, rw, config)
    return sc, orb, brk, disp, rw, rej


# ── Fixture self-checks ──────────────────────────────────────────────────────


class TestFixtureSelfChecks:
    def test_qualifying_candle_geometry(self):
        g = local_geometry(qualifying_candle(MS_0945))
        assert abs(g["wick"] - 0.50) < 1e-9
        assert abs(g["body"] - 0.30) < 1e-9
        assert abs(g["close_loc"] - 0.80) < 1e-9

    def test_failing_candle_a_geometry(self):
        g = local_geometry(failing_candle_a(MS_0945))
        assert abs(g["wick"] - 0.10) < 1e-9
        assert abs(g["body"] - 0.05) < 1e-9
        assert abs(g["close_loc"] - 0.15) < 1e-9

    def test_failing_candle_b_geometry(self):
        g = local_geometry(failing_candle_b(MS_0945))
        assert abs(g["wick"] - 0.20) < 1e-9
        assert abs(g["body"] - 0.10) < 1e-9
        assert abs(g["close_loc"] - 0.30) < 1e-9


# ── Test 1: qualifying rejection on the first retest-contact candle ──────────


class TestFirstContactQualifies:
    def test_status_ok(self):
        _, _, _, _, _, rej = run_full(base_candles([qualifying_candle(MS_0945)]))
        assert rej["status"] == "OK"

    def test_zero_failed_retests(self):
        _, _, _, _, _, rej = run_full(base_candles([qualifying_candle(MS_0945)]))
        assert rej["failed_retest_count"] == 0

    def test_wick_ratio_satisfies_threshold(self):
        _, _, _, _, _, rej = run_full(base_candles([qualifying_candle(MS_0945)]))
        assert rej["geometry"]["rejection_wick_ratio"] >= 0.47

    def test_body_ratio_satisfies_threshold(self):
        _, _, _, _, _, rej = run_full(base_candles([qualifying_candle(MS_0945)]))
        assert rej["geometry"]["body_ratio"] <= 0.40

    def test_close_location_satisfies_threshold(self):
        _, _, _, _, _, rej = run_full(base_candles([qualifying_candle(MS_0945)]))
        assert rej["geometry"]["favorable_close_location"] >= 0.80

    def test_confirmation_candle_identity(self):
        _, _, _, _, _, rej = run_full(base_candles([qualifying_candle(MS_0945)]))
        assert rej["confirmation_timestamp"] == MS_0945


# ── Test 2: failed retests then qualify ──────────────────────────────────────


class TestFailedRetestsThenQualify:
    def test_status_ok(self):
        candles = base_candles([
            failing_candle_a(MS_0945),
            failing_candle_b(MS_0950),
            qualifying_candle(MS_0955),
        ])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"

    def test_two_failed_retests(self):
        candles = base_candles([
            failing_candle_a(MS_0945),
            failing_candle_b(MS_0950),
            qualifying_candle(MS_0955),
        ])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["failed_retest_count"] == 2

    def test_confirmation_is_third_candle(self):
        candles = base_candles([
            failing_candle_a(MS_0945),
            failing_candle_b(MS_0950),
            qualifying_candle(MS_0955),
        ])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["confirmation_timestamp"] == MS_0955


# ── Test 3: scan stops at first qualifier ────────────────────────────────────


class TestScanStopsAtFirstQualifier:
    def test_confirmation_is_first_qualifier(self):
        candles = base_candles([
            qualifying_candle(MS_0945),
            qualifying_candle(MS_0950),
        ])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej["confirmation_timestamp"] == MS_0945


# ── Test 4: non-contact candles cannot qualify ───────────────────────────────


class TestNonContactCannotQualify:
    def test_non_contact_skipped(self):
        candles = base_candles([
            # low 101.20 > level 101.00: NOT a contact
            c(MS_0945, open_=101.55, high=101.90, low=101.20, close=101.80),
            qualifying_candle(MS_0950),
        ])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej["confirmation_timestamp"] == MS_0950
        assert rej["failed_retest_count"] == 0


# ── Test 5: wick ratio exactly 0.47 passes ──────────────────────────────────


class TestWickExactly047:
    def test_passes(self):
        # wick=0.47, body=0.35, closeLoc=0.82
        cnd = mk_candle(MS_0945, low_ticks=9600, range_ticks=1000,
                        open_ticks=10070, close_ticks=10420)
        g = local_geometry(cnd)
        assert abs(g["wick"] - 0.47) < 1e-9
        _, _, _, _, _, rej = run_full(base_candles([cnd]))
        assert rej["status"] == "OK"
        assert abs(rej["geometry"]["rejection_wick_ratio"] - 0.47) < 1e-9


# ── Test 6: body ratio exactly 0.40 passes ──────────────────────────────────


class TestBodyExactly040:
    def test_passes(self):
        # wick=0.55, body=0.40, closeLoc=0.95
        cnd = mk_candle(MS_0945, low_ticks=9600, range_ticks=1000,
                        open_ticks=10150, close_ticks=10550)
        g = local_geometry(cnd)
        assert abs(g["body"] - 0.40) < 1e-9
        _, _, _, _, _, rej = run_full(base_candles([cnd]))
        assert rej["status"] == "OK"
        assert abs(rej["geometry"]["body_ratio"] - 0.40) < 1e-9


# ── Test 7: favorable close location exactly 0.80 passes ────────────────────


class TestCloseLocExactly080:
    def test_passes(self):
        cnd = qualifying_candle(MS_0945)  # wick=0.50, body=0.30, closeLoc=0.80
        _, _, _, _, _, rej = run_full(base_candles([cnd]))
        assert rej["status"] == "OK"
        assert abs(rej["geometry"]["favorable_close_location"] - 0.80) < 1e-9


# ── Test 8: single failing threshold produces correct rule ───────────────────


class TestSingleFailingThreshold:
    def test_wick_too_low(self):
        # wick=0.45 (fails), body=0.40, closeLoc=0.85
        cnd = mk_candle(MS_0945, low_ticks=9600, range_ticks=1000,
                        open_ticks=10050, close_ticks=10450)
        g = local_geometry(cnd)
        assert abs(g["wick"] - 0.45) < 1e-9
        _, _, _, _, _, rej = run_full(base_candles([cnd]))
        assert rej["status"] == "FAILED"
        assert rej["failed_retest_count"] == 1
        assert rej["failed_retests"][0]["failed_rules"] == [
            "REJECTION_WICK_RATIO_TOO_LOW"
        ]


# ── Test 9: multiple failing thresholds all reported ─────────────────────────


class TestMultipleFailingThresholds:
    def test_all_three_reported(self):
        # wick=0.10, body=0.50, closeLoc=0.60
        cnd = mk_candle(MS_0945, low_ticks=9600, range_ticks=1000,
                        open_ticks=9700, close_ticks=10200)
        _, _, _, _, _, rej = run_full(base_candles([cnd]))
        assert rej["status"] == "FAILED"
        fr = rej["failed_retests"][0]["failed_rules"]
        assert "REJECTION_WICK_RATIO_TOO_LOW" in fr
        assert "BODY_RATIO_TOO_HIGH" in fr
        assert "FAVORABLE_CLOSE_LOCATION_TOO_LOW" in fr
        assert len(fr) == 3


# ── Test 10: zero-range candle ───────────────────────────────────────────────


class TestZeroRangeCandle:
    def test_zero_range_fails(self):
        candles = base_candles([
            c(MS_0945, open_=100.90, high=100.90, low=100.90, close=100.90),
        ])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "FAILED"
        assert rej["failed_retest_count"] == 1
        fr = rej["failed_retests"][0]
        assert fr["geometry"]["rejection_wick_ratio"] is None
        assert fr["geometry"]["body_ratio"] is None
        assert fr["geometry"]["favorable_close_location"] is None
        assert fr["geometry"]["opposite_wick_ratio"] is None
        assert fr["failed_rules"] == ["ZERO_RANGE_CANDLE"]


# ── Test 11: low exactly equal to level may qualify ──────────────────────────


class TestLowExactlyEqualsLevel:
    def test_qualifies(self):
        # low == LEVEL_TICKS, wick=0.50, body=0.30, closeLoc=0.80
        cnd = mk_candle(MS_1000, low_ticks=LEVEL_TICKS, range_ticks=1000,
                        open_ticks=LEVEL_TICKS + 500, close_ticks=LEVEL_TICKS + 800)
        _, _, _, _, _, rej = run_full(base_candles([cnd]))
        assert rej["status"] == "OK"
        assert rej["confirmation_candle"]["low"] == LEVEL
        assert rej["geometry"]["penetration_through_level_ticks"] == 0


# ── Test 12: close below level rejected by close-beyond-level gate ───────────


class TestCloseBelowLevelRejected:
    def test_fails(self):
        low_ticks = LEVEL_TICKS - 1000
        range_ticks = 900
        open_ticks = low_ticks + 450   # wick = 0.50
        close_ticks = low_ticks + 765  # body = 0.35, closeLoc = 0.85
        cnd = mk_candle(MS_1000, low_ticks=low_ticks, range_ticks=range_ticks,
                        open_ticks=open_ticks, close_ticks=close_ticks)
        g = local_geometry(cnd)
        assert abs(g["wick"] - 0.50) < 1e-9
        _, _, _, _, _, rej = run_full(base_candles([cnd]))
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "NO_QUALIFYING_REJECTION_CANDLE"
        assert rej["failed_retest_count"] == 1
        assert "CLOSE_BEYOND_LEVEL_TOO_LOW" in rej["failed_retests"][0]["failed_rules"]


# ── Test 12b: close exactly at level rejected ────────────────────────────────


class TestCloseExactlyAtLevelRejected:
    def test_fails(self):
        # wick=0.50, body=0.30, closeLoc=0.80, close exactly at level
        cnd = mk_candle(MS_1010, low_ticks=LEVEL_TICKS - 400, range_ticks=500,
                        open_ticks=LEVEL_TICKS - 150, close_ticks=LEVEL_TICKS)
        g = local_geometry(cnd)
        assert abs(g["wick"] - 0.50) < 1e-9
        assert abs(g["body"] - 0.30) < 1e-9
        assert abs(g["close_loc"] - 0.80) < 1e-9
        _, _, _, _, _, rej = run_full(base_candles([cnd]))
        assert rej["status"] == "FAILED"


# ── Test 12c: close 1 tick above level passes ────────────────────────────────


class TestCloseOneTickAboveLevelPasses:
    def test_passes(self):
        cnd = mk_candle(MS_1015, low_ticks=LEVEL_TICKS - 500, range_ticks=625,
                        open_ticks=LEVEL_TICKS - 200, close_ticks=LEVEL_TICKS + 1)
        g = local_geometry(cnd)
        assert g["wick"] >= 0.47
        assert g["body"] <= 0.40
        assert g["close_loc"] >= 0.80
        assert g["close_beyond_ticks"] == 1
        _, _, _, _, _, rej = run_full(base_candles([cnd]))
        assert rej["status"] == "OK"
        assert rej["geometry"]["close_beyond_level_ticks"] == 1


# ── Test 13: no qualifying candle ────────────────────────────────────────────


class TestNoQualifyingCandle:
    def test_fails(self):
        candles = base_candles([
            failing_candle_a(MS_0945),
            failing_candle_b(MS_0950),
        ])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "NO_QUALIFYING_REJECTION_CANDLE"
        assert rej["failed_retest_count"] == 2


# ── Test 14: determinism ────────────────────────────────────────────────────


class TestDeterminism:
    def test_identical_across_runs(self):
        def fresh():
            return base_candles([failing_candle_a(MS_0945), qualifying_candle(MS_0950)])
        _, _, _, _, _, r1 = run_full(fresh())
        _, _, _, _, _, r2 = run_full(fresh())
        assert r1 == r2


# ── Test 15: no mutation ────────────────────────────────────────────────────


class TestNoMutation:
    def test_candles_not_mutated(self):
        def fresh():
            return base_candles([failing_candle_a(MS_0945), qualifying_candle(MS_0950)])
        original = fresh()
        reference = copy.deepcopy(original)
        run_full(original)
        assert original == reference


# ── Test 16: failed upstream propagation ─────────────────────────────────────


class TestFailedUpstreamPropagation:
    def test_failed_retest_result(self):
        candles = base_candles([qualifying_candle(MS_0945)])
        sc = build_session_context(candles, CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, CONFIG)
        fake_failed = {
            "status": "FAILED",
            "failed_stage": "RETEST_NOT_FOUND",
            "reason": "synthetic failure for test",
        }
        rej = find_rejection(sc["candles"], orb, brk, disp, fake_failed, CONFIG)
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "RETEST_NOT_FOUND"

    def test_failed_displacement_result(self):
        candles = base_candles([qualifying_candle(MS_0945)])
        sc = build_session_context(candles, CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        fake_disp = {
            "status": "FAILED",
            "failed_stage": "RETEST_BEFORE_DISPLACEMENT",
            "reason": "synthetic",
        }
        fake_retest = {
            "status": "FAILED",
            "failed_stage": "RETEST_NOT_FOUND",
            "reason": "synthetic",
        }
        rej = find_rejection(sc["candles"], orb, brk, fake_disp, fake_retest, CONFIG)
        assert rej["status"] == "FAILED"

    def test_failed_orb(self):
        rej = find_rejection([], {"status": "FAILED", "reason": "no ORB"}, {}, {}, {}, CONFIG)
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "LEVEL_NOT_FOUND"

    def test_failed_break(self):
        rej = find_rejection(
            [], {"status": "OK"}, {"status": "FAILED", "reason": "no break"}, {}, {}, CONFIG
        )
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "BREAK_NOT_FOUND"


# ── Test: unsupported config ─────────────────────────────────────────────────


class TestUnsupportedConfig:
    def test_short_direction(self):
        cfg = {**CONFIG, "direction": "SHORT"}
        candles = base_candles([qualifying_candle(MS_0945)])
        sc = build_session_context(candles, CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, CONFIG)
        rw = find_retest_window(sc["candles"], orb, brk, disp, CONFIG)
        rej = find_rejection(sc["candles"], orb, brk, disp, rw, cfg)
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "UNSUPPORTED_CONFIGURATION"

    def test_orb_low_level_source(self):
        cfg = {**CONFIG, "level_source": "ORB_LOW"}
        candles = base_candles([qualifying_candle(MS_0945)])
        sc = build_session_context(candles, CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, CONFIG)
        rw = find_retest_window(sc["candles"], orb, brk, disp, CONFIG)
        rej = find_rejection(sc["candles"], orb, brk, disp, rw, cfg)
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "UNSUPPORTED_CONFIGURATION"

    def test_min_penetration_ticks_set(self):
        cfg = {**CONFIG, "min_penetration_ticks": 5}
        candles = base_candles([qualifying_candle(MS_0945)])
        sc = build_session_context(candles, CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, CONFIG)
        rw = find_retest_window(sc["candles"], orb, brk, disp, CONFIG)
        rej = find_rejection(sc["candles"], orb, brk, disp, rw, cfg)
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "UNSUPPORTED_CONFIGURATION"

    def test_invalid_min_close_beyond(self):
        cfg = {**CONFIG, "min_close_beyond_level_ticks": -1}
        candles = base_candles([qualifying_candle(MS_0945)])
        sc = build_session_context(candles, CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, CONFIG)
        rw = find_retest_window(sc["candles"], orb, brk, disp, CONFIG)
        rej = find_rejection(sc["candles"], orb, brk, disp, rw, cfg)
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "UNSUPPORTED_CONFIGURATION"


# ── Test: invalid inputs ─────────────────────────────────────────────────────


class TestInvalidInputs:
    def test_candles_not_list(self):
        with pytest.raises(TypeError, match="candles must be a list"):
            find_rejection("not a list", {"status": "OK"}, {"status": "OK"},
                           {"status": "OK"}, {"status": "OK"}, CONFIG)

    def test_config_missing_key(self):
        bad_cfg = {"tick_size": 0.01}
        with pytest.raises(TypeError, match="config.timeframe_minutes is required"):
            find_rejection([], {"status": "OK"}, {"status": "OK"},
                           {"status": "OK"}, {"status": "OK"}, bad_cfg)


# ── Test: geometry fields completeness ───────────────────────────────────────


class TestGeometryFields:
    def test_all_geometry_fields_present(self):
        _, _, _, _, _, rej = run_full(base_candles([qualifying_candle(MS_0945)]))
        assert rej["status"] == "OK"
        g = rej["geometry"]
        expected_keys = {
            "range_ticks", "body_ticks", "rejection_wick_ticks",
            "opposite_wick_ticks", "rejection_wick_ratio", "body_ratio",
            "favorable_close_location", "opposite_wick_ratio",
            "penetration_through_level_ticks", "penetration_through_level_points",
            "close_beyond_level_ticks", "close_beyond_level_points",
        }
        assert set(g.keys()) == expected_keys

    def test_penetration_computed(self):
        _, _, _, _, _, rej = run_full(base_candles([qualifying_candle(MS_0945)]))
        g = rej["geometry"]
        # qualifying candle low=96.00, level=101.00 → 500 ticks penetration
        assert g["penetration_through_level_ticks"] == 500

    def test_close_beyond_computed(self):
        _, _, _, _, _, rej = run_full(base_candles([qualifying_candle(MS_0945)]))
        g = rej["geometry"]
        # qualifying candle close=104.00, level=101.00 → 300 ticks beyond
        assert g["close_beyond_level_ticks"] == 300

    def test_failed_retest_geometry_present(self):
        candles = base_candles([
            failing_candle_a(MS_0945),
            qualifying_candle(MS_0950),
        ])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["failed_retest_count"] == 1
        fr = rej["failed_retests"][0]
        assert "geometry" in fr
        assert "failed_rules" in fr
        assert fr["geometry"]["range_ticks"] == 1000


# ── Test: close-beyond-level gate disabled ───────────────────────────────────


class TestCloseBeyondDisabled:
    def test_close_below_level_passes_when_gate_disabled(self):
        cfg = {**CONFIG, "min_close_beyond_level_ticks": None}
        # Close below level but all 3 geometry rules pass
        low_ticks = LEVEL_TICKS - 1000
        range_ticks = 900
        open_ticks = low_ticks + 450
        close_ticks = low_ticks + 765
        cnd = mk_candle(MS_1000, low_ticks=low_ticks, range_ticks=range_ticks,
                        open_ticks=open_ticks, close_ticks=close_ticks)
        _, _, _, _, _, rej = run_full(base_candles([cnd]), config=cfg)
        assert rej["status"] == "OK"


# ── Test: output shape for FAILED ────────────────────────────────────────────


class TestFailedOutputShape:
    def test_has_required_fields(self):
        candles = base_candles([failing_candle_a(MS_0945)])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "FAILED"
        assert "failed_stage" in rej
        assert "reason" in rej
        assert "failed_retests" in rej
        assert "failed_retest_count" in rej

    def test_failed_retest_shape(self):
        candles = base_candles([failing_candle_a(MS_0945)])
        _, _, _, _, _, rej = run_full(candles)
        fr = rej["failed_retests"][0]
        assert "candle_index" in fr
        assert "candle" in fr
        assert "timestamp" in fr
        assert "geometry" in fr
        assert "failed_rules" in fr
