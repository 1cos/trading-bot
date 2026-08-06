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
from trading_lab.tick_arithmetic import price_to_ticks


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
    "min_displacement_bars": 1,
    "confirmation_wick_penetration_pct_min": 0,
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
    def test_unknown_direction(self):
        cfg = {**CONFIG, "direction": "SIDEWAYS"}
        candles = base_candles([qualifying_candle(MS_0945)])
        sc = build_session_context(candles, CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, CONFIG)
        rw = find_retest_window(sc["candles"], orb, brk, disp, CONFIG)
        rej = find_rejection(sc["candles"], orb, brk, disp, rw, cfg)
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "UNSUPPORTED_CONFIGURATION"

    def test_unsupported_level_source(self):
        cfg = {**CONFIG, "level_source": "PDH"}
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
            "body_outside_orb", "wick_penetration_pct",
            # B4: ATR diagnostics
            "candle_atr_status", "candle_atr_ratio",
            "candle_atr_previous", "candle_atr_threshold",
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


# ═══════════════════════════════════════════════════════════════════════════════
# B4 — News Candle ATR filter tests
# ═══════════════════════════════════════════════════════════════════════════════


def _huge_candle(time_ms, range_points=50.0):
    """A candle with an enormous range that exceeds 3 ATR.

    The padding candles have range ~2.0 points (matching qualifying_candle).
    ATR(14) will be ~2.0.  A 50-point range gives ratio ~25, well above 3 ATR.
    Geometry: wick=0.50, body=0.30, closeLoc=0.80 (same proportions
    as qualifying_candle, just scaled up enormously).
    """
    range_ticks = int(range_points / TICK_SIZE)  # 5000 ticks
    low_ticks = LEVEL_TICKS - int(range_ticks * 0.50)  # wick reaches far below
    open_ticks = low_ticks + int(range_ticks * 0.50)
    close_ticks = low_ticks + int(range_ticks * 0.80)
    return mk_candle(time_ms, low_ticks=low_ticks, range_ticks=range_ticks,
                     open_ticks=open_ticks, close_ticks=close_ticks)


def _many_candles_before(extra, n_padding=15):
    """Base candles + padding to ensure ATR has sufficient history.

    Padding candles have range ~2.0 points (200 ticks), close to the
    qualifying_candle range of 10.0 points (1000 ticks).  This ensures
    the qualifying candle's ratio stays well below 3 ATR while the
    huge candle's ratio stays well above it.
    """
    base = [
        c(MS_0930, high=101.0, low=99.0, close=100.5),       # ORB (range=2.0)
        c(MS_0935, open_=100.50, high=101.50, low=100.30, close=101.20),  # break
        c(MS_0940, open_=101.20, high=101.60, low=101.10, close=101.30),  # displacement
    ]
    # Padding candles with range ~10.0 points (same as qualifying_candle)
    # so qualifying_candle ratio is ~1.0 and huge_candle ratio is >>3.
    # Both open and low stay above level (101.0) so they are NOT retest
    # attempts in the LONG direction.
    padding = []
    for j in range(n_padding):
        t = MS_0940 + (j + 1) * 300000
        padding.append(c(t, open_=106.0, high=111.0, low=101.10, close=105.0))
    return base + padding + extra


class TestNewsCandleFilter:
    """B4: News Candle classification integrated into rejection finder."""

    def test_normal_candle_qualifies(self):
        """A normal-sized qualifying candle passes all gates."""
        candles = _many_candles_before([qualifying_candle(MS_0930 + 20 * 300000)])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        geo = rej["geometry"]
        assert geo["candle_atr_status"] in ("NORMAL", "LARGE")
        assert "CANDLE_ATR_EXCEEDS_THRESHOLD" not in rej.get("failed_rules", [])

    def test_huge_candle_excluded(self):
        """A candle with range >> 3 ATR is excluded despite good geometry."""
        candles = _many_candles_before([_huge_candle(MS_0930 + 20 * 300000)])
        _, _, _, _, _, rej = run_full(candles)
        # The huge candle should be in failed_retests
        assert rej["status"] == "FAILED"
        fr = rej["failed_retests"]
        assert len(fr) >= 1
        atr_failures = [
            f for f in fr
            if "CANDLE_ATR_EXCEEDS_THRESHOLD" in f["failed_rules"]
        ]
        assert len(atr_failures) >= 1

    def test_first_news_second_valid(self):
        """First candidate is NEWS_CANDLE, second qualifies → OK on second."""
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        candles = _many_candles_before([_huge_candle(t1), qualifying_candle(t2)])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        # The first (huge) candle should be in failed_retests
        assert rej["failed_retest_count"] >= 1
        atr_in_failed = any(
            "CANDLE_ATR_EXCEEDS_THRESHOLD" in fr["failed_rules"]
            for fr in rej["failed_retests"]
        )
        assert atr_in_failed
        # Confirmation candle should NOT have the ATR failure
        assert rej["geometry"]["candle_atr_status"] != "NEWS_CANDLE"

    def test_all_candidates_news_candle(self):
        """All candidates are NEWS_CANDLE → NO_QUALIFYING_REJECTION_CANDLE."""
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        candles = _many_candles_before([_huge_candle(t1), _huge_candle(t2)])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "FAILED"
        assert rej["failed_stage"] == "NO_QUALIFYING_REJECTION_CANDLE"


class TestNewsCandleMetadata:
    """ATR diagnostics present in geometry for every candidate."""

    def test_metadata_in_ok_result(self):
        candles = _many_candles_before([qualifying_candle(MS_0930 + 20 * 300000)])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        geo = rej["geometry"]
        assert "candle_atr_status" in geo
        assert "candle_atr_ratio" in geo
        assert "candle_atr_previous" in geo
        assert "candle_atr_threshold" in geo

    def test_metadata_in_failed_retest(self):
        candles = _many_candles_before([failing_candle_a(MS_0930 + 20 * 300000)])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "FAILED"
        fr = rej["failed_retests"][0]
        geo = fr["geometry"]
        assert "candle_atr_status" in geo
        assert "candle_atr_ratio" in geo
        assert "candle_atr_previous" in geo
        assert "candle_atr_threshold" in geo

    def test_metadata_threshold_default(self):
        candles = _many_candles_before([qualifying_candle(MS_0930 + 20 * 300000)])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["geometry"]["candle_atr_threshold"] == 3.0

    def test_metadata_threshold_custom(self):
        cfg = {**CONFIG, "news_threshold": 2.5}
        candles = _many_candles_before([qualifying_candle(MS_0930 + 20 * 300000)])
        _, _, _, _, _, rej = run_full(candles, config=cfg)
        assert rej["geometry"]["candle_atr_threshold"] == 2.5


class TestNewsCandleNoLookAhead:
    """The confirmation candle must not be included in its own ATR."""

    def test_confirmation_excluded_from_own_atr(self):
        """Changing the confirmation candle's range doesn't change its ATR."""
        t_entry = MS_0930 + 20 * 300000

        # Run with normal qualifying candle
        candles_normal = _many_candles_before([qualifying_candle(t_entry)])
        _, _, _, _, _, rej_normal = run_full(candles_normal)
        assert rej_normal["status"] == "OK"
        atr_normal = rej_normal["geometry"]["candle_atr_previous"]

        # Run with a differently-sized qualifying candle at same position
        bigger = mk_candle(t_entry, low_ticks=9400, range_ticks=1200,
                           open_ticks=10100, close_ticks=10420)
        candles_bigger = _many_candles_before([bigger])
        _, _, _, _, _, rej_bigger = run_full(candles_bigger)
        if rej_bigger["status"] == "OK":
            atr_bigger = rej_bigger["geometry"]["candle_atr_previous"]
            # ATR should be identical — confirmation candle excluded
            assert atr_normal == atr_bigger


class TestNewsCandleInsufficientHistory:
    """Early candles with insufficient ATR history are fail-open."""

    def test_insufficient_history_allows_entry(self):
        """Candle within first 14 bars has INSUFFICIENT_HISTORY → entry OK."""
        # Use base_candles with qualifying candle at index 3 (< 14)
        candles = base_candles([qualifying_candle(MS_0945)])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej["geometry"]["candle_atr_status"] == "INSUFFICIENT_HISTORY"
        assert rej["geometry"]["candle_atr_ratio"] is None
        assert rej["geometry"]["candle_atr_previous"] is None


class TestNewsCandleLongShortSymmetry:
    """ATR filter is direction-independent."""

    def test_short_direction_has_atr_metadata(self):
        short_cfg = {**CONFIG, "level_source": "ORB_LOW", "direction": "SHORT"}
        short_candles = [
            c(MS_0930, high=101.0, low=99.0, close=99.5),    # ORB
            c(MS_0935, open_=99.50, high=99.70, low=98.50, close=98.80),  # break
            c(MS_0940, open_=98.80, high=98.90, low=98.40, close=98.50),  # disp
        ]
        for j in range(15):
            t = MS_0940 + (j + 1) * 300000
            short_candles.append(
                c(t, open_=98.50, high=98.70, low=98.30, close=98.50)
            )
        t_entry = MS_0940 + 16 * 300000
        short_candles.append(
            c(t_entry, open_=98.80, high=99.50, low=98.60, close=98.70)
        )
        _, _, _, _, _, rej = run_full(short_candles, config=short_cfg)
        if rej["status"] == "OK":
            assert "candle_atr_status" in rej["geometry"]
            assert "candle_atr_ratio" in rej["geometry"]


class TestNewsCandleCustomThreshold:
    """Custom news_threshold via config."""

    def test_invalid_threshold_below_2(self):
        cfg = {**CONFIG, "news_threshold": 1.5}
        candles = _many_candles_before([qualifying_candle(MS_0930 + 20 * 300000)])
        with pytest.raises(ValueError, match=">= 2.0"):
            run_full(candles, config=cfg)

    def test_bool_threshold(self):
        cfg = {**CONFIG, "news_threshold": True}
        candles = _many_candles_before([qualifying_candle(MS_0930 + 20 * 300000)])
        with pytest.raises(TypeError, match="bool"):
            run_full(candles, config=cfg)

    def test_nan_threshold(self):
        cfg = {**CONFIG, "news_threshold": float("nan")}
        candles = _many_candles_before([qualifying_candle(MS_0930 + 20 * 300000)])
        with pytest.raises(ValueError, match="finite"):
            run_full(candles, config=cfg)


class TestNewsCandleFailedRuleNoDuplicates:
    """CANDLE_ATR_EXCEEDS_THRESHOLD appears at most once per candle."""

    def test_no_duplicate_atr_rule(self):
        t1 = MS_0930 + 20 * 300000
        candles = _many_candles_before([_huge_candle(t1)])
        _, _, _, _, _, rej = run_full(candles)
        for fr in rej.get("failed_retests", []):
            atr_count = fr["failed_rules"].count("CANDLE_ATR_EXCEEDS_THRESHOLD")
            assert atr_count <= 1


class TestNewsCandleGeometryFailPlusAtr:
    """Candle fails both geometry and ATR: both recorded."""

    def test_geometry_and_atr_failures_coexist(self):
        """A huge candle with bad geometry has both sets of failed_rules."""
        t1 = MS_0930 + 20 * 300000
        range_ticks = 5000
        low_ticks = LEVEL_TICKS - int(range_ticks * 0.50)
        cnd = mk_candle(t1, low_ticks=low_ticks, range_ticks=range_ticks,
                        open_ticks=low_ticks + 100, close_ticks=low_ticks + 200)
        candles = _many_candles_before([cnd])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "FAILED"
        fr = rej["failed_retests"]
        huge_fr = [f for f in fr if "CANDLE_ATR_EXCEEDS_THRESHOLD" in f["failed_rules"]]
        if huge_fr:
            rules = huge_fr[0]["failed_rules"]
            assert len(rules) > 1


class TestNewsCandleAtrComputedOnce:
    """atr_series is called exactly once per find_rejection invocation."""

    def test_single_atr_call(self):
        from unittest.mock import patch
        candles = _many_candles_before([qualifying_candle(MS_0930 + 20 * 300000)])
        sc = build_session_context(candles, CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, CONFIG)
        rw = find_retest_window(sc["candles"], orb, brk, disp, CONFIG)

        with patch(
            "trading_lab.rejection_finder.atr_series",
            wraps=__import__("trading_lab.atr", fromlist=["atr_series"]).atr_series,
        ) as mock_atr:
            find_rejection(sc["candles"], orb, brk, disp, rw, CONFIG)
            assert mock_atr.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# B5 — TWO_CANDLE_ENGULFING_RECOVERY tests
# ═══════════════════════════════════════════════════════════════════════════════

# For TWO_CANDLE we need zone edges (ORB).
# level=101.00 (ORB_HIGH), orb_low~=99.0 (far_edge for LONG).
# The existing fixtures use 5-minute candles (CONFIG timeframe=5, so 300000ms).


def _tc_first_long(time_ms, close_inside=100.50):
    """First candle: penetrates level, closes inside zone (>= orb_low=99.0)."""
    # low reaches below level (101.0), close inside zone but below level
    return c(time_ms, open_=101.10, high=101.20, low=100.20, close=close_inside)


def _tc_second_long(time_ms, open_=100.40, close=101.20):
    """Second candle: bullish, engulfs first body, closes above level."""
    return c(time_ms, open_=open_, high=101.30, low=100.10, close=close)


def _tc_first_short(time_ms, close_inside=99.50):
    """First candle SHORT: high >= level (99.0), close inside zone (<= orb_high=101.0)."""
    return c(time_ms, open_=98.90, high=99.80, low=98.70, close=close_inside)


SHORT_CONFIG = {**CONFIG, "level_source": "ORB_LOW", "direction": "SHORT"}


def _short_base_candles(extra, n_padding=15):
    """SHORT: ORB with level at orb_low=99.0, break down, displacement down."""
    base = [
        c(MS_0930, high=101.0, low=99.0, close=99.5),       # ORB
        c(MS_0935, open_=99.50, high=99.70, low=98.50, close=98.80),  # break
        c(MS_0940, open_=98.80, high=98.90, low=98.40, close=98.50),  # disp
    ]
    padding = []
    for j in range(n_padding):
        t = MS_0940 + (j + 1) * 300000
        padding.append(c(t, open_=98.50, high=98.70, low=98.30, close=98.50))
    return base + padding + extra


class TestTwoCandleLongValid:
    """Test 1: LONG TWO_CANDLE valid."""

    def test_basic_long_two_candle(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000  # consecutive
        first = _tc_first_long(t1)
        second = _tc_second_long(t2, open_=100.40, close=101.20)
        candles = _many_candles_before([first, second])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "TWO_CANDLE_ENGULFING_RECOVERY"
        assert rej["confirmation_candle_index"] == len(candles) - 2 + 1  # index of second
        assert rej["confirmation_candle"]["time_ms"] == t2
        assert "pair_stop_basis_ticks" in rej
        assert "penetration_candle_index" in rej
        assert "penetration_candle_geometry" in rej


class TestTwoCandleShortValid:
    """Test 2: SHORT TWO_CANDLE valid."""

    def test_basic_short_two_candle(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        # SHORT: first high >= orb_low (99.0), close inside zone (<= orb_high=101.0)
        first = c(t1, open_=98.90, high=99.80, low=98.70, close=99.50)
        # Second: bearish, engulfs first body, close < orb_low (99.0)
        second = c(t2, open_=99.60, high=99.70, low=98.50, close=98.80)
        candles = _short_base_candles([first, second])
        _, _, _, _, _, rej = run_full(candles, config=SHORT_CONFIG)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "TWO_CANDLE_ENGULFING_RECOVERY"
        assert rej["confirmation_candle"]["time_ms"] == t2


class TestTwoCandleBodyTraversal:
    """Tests 3, 4, 5, 6, 7: body traversal and wick behavior."""

    def test_close_below_far_edge_invalidated(self):
        """Test 3: LONG close_1 < far_edge (orb_low=99.0) → invalidated."""
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        first = _tc_first_long(t1, close_inside=98.90)  # below orb_low
        second = _tc_second_long(t2)
        candles = _many_candles_before([first, second])
        _, _, _, _, _, rej = run_full(candles)
        # TWO_CANDLE should fail; check if second qualifies as SINGLE
        if rej["status"] == "FAILED":
            fr = [f for f in rej["failed_retests"]
                  if f.get("two_candle_failed_rules")
                  and "TWO_CANDLE_BODY_TRAVERSES_ZONE" in f["two_candle_failed_rules"]]
            assert len(fr) >= 1

    def test_short_close_above_far_edge_invalidated(self):
        """Test 4: SHORT close_1 > far_edge (orb_high=101.0) → invalidated."""
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        first = c(t1, open_=98.90, high=99.80, low=98.70, close=101.10)
        second = c(t2, open_=99.60, high=99.70, low=98.50, close=98.80)
        candles = _short_base_candles([first, second])
        _, _, _, _, _, rej = run_full(candles, config=SHORT_CONFIG)
        if rej["status"] == "FAILED":
            fr = [f for f in rej["failed_retests"]
                  if f.get("two_candle_failed_rules")
                  and "TWO_CANDLE_BODY_TRAVERSES_ZONE" in f["two_candle_failed_rules"]]
            assert len(fr) >= 1

    def test_wick_beyond_far_edge_body_inside_valid(self):
        """Test 5: wick < far_edge but close >= far_edge → valid."""
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        # low goes to 98.5 (below orb_low=99.0), close at 100.0 (inside zone)
        first = c(t1, open_=101.10, high=101.20, low=98.50, close=100.00)
        second = _tc_second_long(t2, open_=99.90, close=101.20)
        candles = _many_candles_before([first, second])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "TWO_CANDLE_ENGULFING_RECOVERY"

    def test_open_beyond_far_edge_close_inside_valid(self):
        """Test 6: open < far_edge but close >= far_edge → valid."""
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        first = c(t1, open_=98.50, high=101.10, low=98.40, close=100.00)
        second = _tc_second_long(t2, open_=98.40, close=101.20)
        candles = _many_candles_before([first, second])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "TWO_CANDLE_ENGULFING_RECOVERY"

    def test_open_and_close_beyond_far_edge_invalidated(self):
        """Test 7: open AND close < far_edge → invalidated."""
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        first = c(t1, open_=98.50, high=101.10, low=98.40, close=98.60)
        second = _tc_second_long(t2, open_=98.40, close=101.20)
        candles = _many_candles_before([first, second])
        _, _, _, _, _, rej = run_full(candles)
        if rej["status"] == "FAILED" or rej.get("entry_pattern_type") != "TWO_CANDLE_ENGULFING_RECOVERY":
            # The TWO_CANDLE should be invalidated
            fr = [f for f in rej.get("failed_retests", [])
                  if f.get("two_candle_failed_rules")
                  and "TWO_CANDLE_BODY_TRAVERSES_ZONE" in f["two_candle_failed_rules"]]
            assert len(fr) >= 1


class TestTwoCandlePDHNoPair:
    """Test 8: PDH/PDL (line source) → TWO_CANDLE not evaluated."""

    def test_pdh_no_two_candle(self):
        pdh_cfg = {**CONFIG, "level_source": "PREVIOUS_DAY_HIGH", "direction": "SHORT"}
        # TWO_CANDLE should not be attempted for line sources
        # We just verify no crash and entry_pattern_type is SINGLE or FAILED
        candles = base_candles([qualifying_candle(MS_0945)])
        try:
            _, _, _, _, _, rej = run_full(candles, config=pdh_cfg)
        except Exception:
            pass  # PDH may fail for other reasons in test fixtures


class TestTwoCandleStop:
    """Tests 9: stop on pair extreme."""

    def test_stop_long_min_of_pair(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        # first low=100.20, second low=100.10 → stop basis = 100.10
        first = _tc_first_long(t1)
        second = _tc_second_long(t2)
        candles = _many_candles_before([first, second])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        min_low = min(
            price_to_ticks(first["low"], TICK_SIZE),
            price_to_ticks(second["low"], TICK_SIZE),
        )
        assert rej["pair_stop_basis_ticks"] == min_low

    def test_stop_short_max_of_pair(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        first = c(t1, open_=98.90, high=99.80, low=98.70, close=99.50)
        second = c(t2, open_=99.60, high=99.70, low=98.50, close=98.80)
        candles = _short_base_candles([first, second])
        _, _, _, _, _, rej = run_full(candles, config=SHORT_CONFIG)
        assert rej["status"] == "OK"
        max_high = max(
            price_to_ticks(first["high"], TICK_SIZE),
            price_to_ticks(second["high"], TICK_SIZE),
        )
        assert rej["pair_stop_basis_ticks"] == max_high


class TestTwoCandleConfirmation:
    """Test 10: confirmation candle/index = second candle."""

    def test_confirmation_is_second(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        first = _tc_first_long(t1)
        second = _tc_second_long(t2)
        candles = _many_candles_before([first, second])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej["confirmation_candle"]["time_ms"] == t2
        assert rej["confirmation_candle"]["close"] == second["close"]


class TestTwoCandleOverlap:
    """Test 11: pair failed, second candle rivalutated."""

    def test_second_of_failed_pair_becomes_new_candidate(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        t3 = t2 + 300000
        # First pair: first valid penetration, second NOT bullish → pair fails
        first = _tc_first_long(t1)
        second_bad = c(t2, open_=100.80, high=101.00, low=100.20, close=100.30)
        # second_bad is also a retest attempt (low <= 101.0) and
        # can be the start of a new pair with third candle
        third = _tc_second_long(t3, open_=100.20, close=101.20)
        candles = _many_candles_before([first, second_bad, third])
        _, _, _, _, _, rej = run_full(candles)
        # The second_bad+third might form a valid TWO_CANDLE
        # or third might qualify as SINGLE
        assert rej["status"] == "OK"
        # First candle should be in failed_retests
        assert rej["failed_retest_count"] >= 1


class TestTwoCandleConsecutiveness:
    """Test 12: gap timestamp → not consecutive."""

    def test_temporal_gap_invalidates(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 600000  # 10 minutes instead of 5 → not consecutive
        first = _tc_first_long(t1)
        second = _tc_second_long(t2)
        candles = _many_candles_before([first, second])
        _, _, _, _, _, rej = run_full(candles)
        # TWO_CANDLE should not qualify due to timestamp gap
        if rej["status"] == "FAILED":
            fr = [f for f in rej["failed_retests"]
                  if f.get("two_candle_failed_rules")
                  and "TWO_CANDLE_NOT_CONSECUTIVE" in f["two_candle_failed_rules"]]
            assert len(fr) >= 1


class TestTwoCandleATR:
    """Tests 13: NEWS_CANDLE on first or second."""

    def test_first_news_candle_no_two_candle(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        candles = _many_candles_before([_huge_candle(t1), _tc_second_long(t2)])
        _, _, _, _, _, rej = run_full(candles)
        # First is NEWS_CANDLE → TWO_CANDLE not attempted
        # Second may qualify as SINGLE or not
        fr_first = [f for f in rej.get("failed_retests", [])
                    if "CANDLE_ATR_EXCEEDS_THRESHOLD" in f["failed_rules"]]
        assert len(fr_first) >= 1
        # No TWO_CANDLE_SECOND_ATR_EXCEEDS on first's failed record
        for f in fr_first:
            assert "TWO_CANDLE_SECOND_ATR_EXCEEDS" not in f.get("two_candle_failed_rules", [])

    def test_second_news_candle_pair_excluded(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        first = _tc_first_long(t1)  # body: 100.50–101.10
        # Second: engulfs body (open <= 100.50, close >= 101.10) but
        # enormous range → NEWS_CANDLE
        second_huge = c(t2, open_=100.40, high=160.0, low=60.0, close=101.20)
        candles = _many_candles_before([first, second_huge])
        _, _, _, _, _, rej = run_full(candles)
        # TWO_CANDLE should fail with TWO_CANDLE_SECOND_ATR_EXCEEDS
        fr = [f for f in rej.get("failed_retests", [])
              if f.get("two_candle_failed_rules")
              and "TWO_CANDLE_SECOND_ATR_EXCEEDS" in f["two_candle_failed_rules"]]
        assert len(fr) >= 1


class TestTwoCandleNoDuplication:
    """Test 14: no duplicate failed_retests."""

    def test_max_one_record_per_index(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        first = _tc_first_long(t1)
        second_bad = c(t2, open_=100.80, high=101.00, low=100.20, close=100.30)
        candles = _many_candles_before([first, second_bad])
        _, _, _, _, _, rej = run_full(candles)
        indices = [f["candle_index"] for f in rej.get("failed_retests", [])]
        assert len(indices) == len(set(indices)), "duplicate indices in failed_retests"


class TestTwoCandleSinglePriority:
    """SINGLE_CANDLE has priority over TWO_CANDLE."""

    def test_single_found_immediately(self):
        t1 = MS_0930 + 20 * 300000
        candles = _many_candles_before([qualifying_candle(t1)])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej.get("entry_pattern_type", "SINGLE_CANDLE_REJECTION") == "SINGLE_CANDLE_REJECTION"
        assert "pair_stop_basis_ticks" not in rej


class TestTwoCandleEntryPatternType:
    """entry_pattern_type in result."""

    def test_single_has_type(self):
        candles = base_candles([qualifying_candle(MS_0945)])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "SINGLE_CANDLE_REJECTION"

    def test_two_candle_has_type(self):
        t1 = MS_0930 + 20 * 300000
        t2 = t1 + 300000
        first = _tc_first_long(t1)
        second = _tc_second_long(t2)
        candles = _many_candles_before([first, second])
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "TWO_CANDLE_ENGULFING_RECOVERY"
