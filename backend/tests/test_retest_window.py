"""Tests for canonical findRetestWindow port.

Parity vectors verified against bdrr_engine.js findRetestWindow via
Node.js on dati/SPY_5m.csv sessions.
"""

import copy

import pytest

from trading_lab.retest_window import find_retest_window
from trading_lab.session_context import build_session_context
from trading_lab.orb_builder import build_orb
from trading_lab.break_finder import find_break
from trading_lab.displacement_finder import find_displacement


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
}

# 2026-07-01 EDT
MS_0930 = 1782912600000
MS_0935 = 1782912900000
MS_0940 = 1782913200000
MS_0945 = 1782913500000
MS_0950 = 1782913800000
MS_0955 = 1782914100000
MS_1000 = 1782914400000


def c(time_ms, open_=100.0, high=100.5, low=99.5, close=100.0):
    return {"time_ms": time_ms, "open": open_, "high": high, "low": low, "close": close}


def run_full(candles_list, config=CONFIG):
    sc = build_session_context(candles_list, config)
    orb = build_orb(sc["candles"], sc, config)
    brk = find_break(sc["candles"], orb, config)
    disp = find_displacement(sc["candles"], orb, brk, config)
    rw = find_retest_window(sc["candles"], orb, brk, disp, config)
    return sc, orb, brk, disp, rw


def standard_candles():
    """ORB→break→1 disp bar→retest contact→2 more bars."""
    return [
        c(MS_0930, high=101.0, low=99.0, close=100.5),    # ORB, level=101
        c(MS_0935, close=101.50),                           # break
        c(MS_0940, high=102.0, low=101.10, close=101.80),  # displacement
        c(MS_0945, high=101.50, low=100.90, close=101.20), # retest contact (low<=101)
        c(MS_0950, high=101.80, low=101.20, close=101.60), # above level
        c(MS_0955, high=101.30, low=100.80, close=101.10), # another contact
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Valid retest window
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidRetestWindow:
    def test_status_ok(self):
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["status"] == "OK"

    def test_date(self):
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["date"] == "2026-07-01"

    def test_level_price(self):
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["level_price"] == 101.0

    def test_retest_start_index(self):
        _, _, _, _, rw = run_full(standard_candles())
        # displacement contact at index 3
        assert rw["retest_start_index"] == 3

    def test_retest_start_timestamp(self):
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["retest_start_timestamp"] == MS_0945

    def test_window_start_equals_retest_start(self):
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["retest_window_start_index"] == rw["retest_start_index"]

    def test_window_end_is_last_candle(self):
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["retest_window_end_index"] == 5  # 6 candles, last index=5

    def test_window_length(self):
        _, _, _, _, rw = run_full(standard_candles())
        # indices 3,4,5 → 3 bars
        assert len(rw["retest_window"]) == 3

    def test_contact_count(self):
        _, _, _, _, rw = run_full(standard_candles())
        # candle 3: low=100.90<=101 ✓, candle 4: low=101.20>101 ✗, candle 5: low=100.80<=101 ✓
        assert rw["retest_contact_count"] == 2

    def test_window_includes_non_contacts(self):
        """All candles in range are in window, even if low > level."""
        _, _, _, _, rw = run_full(standard_candles())
        assert len(rw["retest_window"]) == 3
        assert rw["retest_contact_count"] == 2


class TestOutputFields:
    def test_ok_fields(self):
        _, _, _, _, rw = run_full(standard_candles())
        expected = {
            "status", "date", "level_price",
            "retest_start_index", "retest_start_timestamp",
            "retest_window_start_index", "retest_window_end_index",
            "retest_window", "retest_contacts", "retest_contact_count",
        }
        assert set(rw.keys()) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# Contact metrics
# ═══════════════════════════════════════════════════════════════════════════════


class TestContactMetrics:
    def test_first_contact_fields(self):
        _, _, _, _, rw = run_full(standard_candles())
        rc = rw["retest_contacts"][0]
        expected = {
            "candle_index", "candle", "timestamp",
            "closest_directional_position_ticks",
            "penetration_through_level_ticks",
            "penetration_through_level_points",
            "displacement_retracement_pct",
        }
        assert set(rc.keys()) == expected

    def test_first_contact_index(self):
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["retest_contacts"][0]["candle_index"] == 3

    def test_first_contact_timestamp(self):
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["retest_contacts"][0]["timestamp"] == MS_0945

    def test_penetration_ticks(self):
        """low=100.90, level=101.0 → lowTicks=10090, levelTicks=10100.
        pen = max(0, 10100-10090) = 10."""
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["retest_contacts"][0]["penetration_through_level_ticks"] == 10

    def test_penetration_points(self):
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["retest_contacts"][0]["penetration_through_level_points"] == 0.10

    def test_closest_directional_position(self):
        """cdp = lowTicks - levelTicks = 10090 - 10100 = -10."""
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["retest_contacts"][0]["closest_directional_position_ticks"] == -10

    def test_retracement_pct(self):
        """pen=10, displacement_distance.ticks=100 → 10/100 = 0.1."""
        _, _, _, _, rw = run_full(standard_candles())
        rc = rw["retest_contacts"][0]
        assert abs(rc["displacement_retracement_pct"] - 0.1) < 1e-10

    def test_second_contact(self):
        _, _, _, _, rw = run_full(standard_candles())
        assert rw["retest_contacts"][1]["candle_index"] == 5
        # low=100.80 → lowTicks=10080, pen=max(0,10100-10080)=20
        assert rw["retest_contacts"][1]["penetration_through_level_ticks"] == 20

    def test_contact_candle_identity(self):
        candles = standard_candles()
        sc, _, _, _, rw = run_full(candles)
        assert rw["retest_contacts"][0]["candle"] is sc["candles"][3]


# ═══════════════════════════════════════════════════════════════════════════════
# Boundary: low exactly at level
# ═══════════════════════════════════════════════════════════════════════════════


class TestBoundary:
    def test_low_exactly_at_level(self):
        """low == level → contact with pen=0."""
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, high=102.0, low=101.10),
            c(MS_0945, low=101.0),  # exactly at level
        ]
        _, _, _, _, rw = run_full(candles)
        assert rw["retest_contact_count"] == 1
        assert rw["retest_contacts"][0]["penetration_through_level_ticks"] == 0
        assert rw["retest_contacts"][0]["closest_directional_position_ticks"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Displacement distance zero → retracement null
# ═══════════════════════════════════════════════════════════════════════════════


class TestZeroDisplacement:
    def test_retracement_null_when_disp_zero(self):
        """If displacement_distance.ticks==0, retracement_pct is null."""
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            # displacement bar with high=101.0 → dist_ticks = 10100-10100 = 0
            c(MS_0940, high=101.0, low=101.0 + 0.01),
            c(MS_0945, low=100.50),
        ]
        _, _, _, disp, rw = run_full(candles)
        if disp["status"] == "OK" and disp["displacement_distance"]["ticks"] == 0:
            for rc in rw["retest_contacts"]:
                assert rc["displacement_retracement_pct"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# All contacts or no contacts
# ═══════════════════════════════════════════════════════════════════════════════


class TestAllOrNone:
    def test_all_bars_are_contacts(self):
        """Every post-displacement bar has low <= level."""
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, high=102.0, low=101.10),
            c(MS_0945, low=100.90),
            c(MS_0950, low=100.80),
            c(MS_0955, low=100.70),
        ]
        _, _, _, _, rw = run_full(candles)
        assert rw["retest_contact_count"] == 3
        assert len(rw["retest_window"]) == 3

    def test_first_contact_only(self):
        """Only the first retest-contact bar has low <= level."""
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, high=102.0, low=101.10),
            c(MS_0945, low=100.90),   # contact
            c(MS_0950, low=101.10),   # above → not contact
        ]
        _, _, _, _, rw = run_full(candles)
        assert rw["retest_contact_count"] == 1
        assert len(rw["retest_window"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Window identity
# ═══════════════════════════════════════════════════════════════════════════════


class TestWindowIdentity:
    def test_window_contains_originals(self):
        candles = standard_candles()
        sc, _, _, _, rw = run_full(candles)
        for i, wc in enumerate(rw["retest_window"]):
            src_idx = rw["retest_window_start_index"] + i
            assert wc is sc["candles"][src_idx]


# ═══════════════════════════════════════════════════════════════════════════════
# Failed upstream
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailedUpstream:
    def test_failed_orb(self):
        rw = find_retest_window([], None, {"status": "OK"}, {"status": "OK"}, CONFIG)
        assert rw["failed_stage"] == "LEVEL_NOT_FOUND"

    def test_failed_break(self):
        rw = find_retest_window([], {"status": "OK"}, None, {"status": "OK"}, CONFIG)
        assert rw["failed_stage"] == "BREAK_NOT_FOUND"

    def test_failed_displacement(self):
        rw = find_retest_window([], {"status": "OK"}, {"status": "OK"}, None, CONFIG)
        assert rw["failed_stage"] == "RETEST_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
# Unsupported config
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnsupportedConfig:
    def test_unknown_direction(self):
        cfg = {**CONFIG, "direction": "SIDEWAYS"}
        fake = {"status": "OK", "orb_candle_index": 0, "orb_candle": c(MS_0930),
                "level_price": 101.0, "level_price_ticks": 10100, "date": "X"}
        rw = find_retest_window([c(MS_0930)], fake, fake, fake, cfg)
        assert rw["failed_stage"] == "UNSUPPORTED_CONFIGURATION"

    def test_unsupported_level_source(self):
        cfg = {**CONFIG, "level_source": "PDH"}
        fake = {"status": "OK", "orb_candle_index": 0, "orb_candle": c(MS_0930),
                "level_price": 101.0, "level_price_ticks": 10100, "date": "X"}
        rw = find_retest_window([c(MS_0930)], fake, fake, fake, cfg)
        assert rw["failed_stage"] == "UNSUPPORTED_CONFIGURATION"


# ═══════════════════════════════════════════════════════════════════════════════
# Defensive cross-check
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossCheck:
    def test_mismatched_displacement_contact(self):
        candles = standard_candles()
        sc, orb, brk, disp, _ = run_full(candles)
        alt = list(sc["candles"])
        alt[3] = c(MS_1000)  # different timestamp at contact index
        rw = find_retest_window(alt, orb, brk, disp, CONFIG)
        assert rw["failed_stage"] == "INVALID_INPUT"


# ═══════════════════════════════════════════════════════════════════════════════
# Input not mutated
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutation:
    def test_candles_not_mutated(self):
        candles = standard_candles()
        original = copy.deepcopy(candles)
        run_full(candles)
        assert candles == original


# ═══════════════════════════════════════════════════════════════════════════════
# Candles type
# ═══════════════════════════════════════════════════════════════════════════════


class TestCandlesType:
    def test_tuple_rejected(self):
        fake = {"status": "OK", "orb_candle_index": 0, "orb_candle": c(MS_0930),
                "level_price": 101.0, "level_price_ticks": 10100, "date": "X",
                "break_candle_index": 1, "break_candle": c(MS_0935),
                "first_retest_contact_index": 2, "first_retest_contact_candle": c(MS_0940),
                "displacement_distance": {"ticks": 10}}
        with pytest.raises(TypeError, match="must be a list"):
            find_retest_window((), fake, fake, fake, CONFIG)


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministic:
    def test_repeated(self):
        candles = standard_candles()
        results = [run_full(candles)[4]["retest_contact_count"] for _ in range(10)]
        assert all(r == 2 for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# Real CSV parity
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealCSVParity:
    """JS parity on SPY 2026-04-29:
      status: OK, date: 2026-04-29, level: 711.1900024414062,
      retest_start_index: 18, start_timestamp_ms: 1777474800000,
      window_start: 18, window_end: 77, window_length: 60,
      contact_count: 60,
      c0: index=18, cdp=-24, pen=24, pen_pts=0.24, retrace=0.2608...,
      cL: index=77, pen=68.
    """

    @pytest.fixture()
    def spy_apr29(self):
        import os
        from trading_lab.csv_parser import parse_candles_from_csv
        from trading_lab.session_split import split_into_sessions

        csv_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "dati", "SPY_5m.csv"
        )
        if not os.path.exists(csv_path):
            pytest.skip("SPY_5m.csv not available")
        with open(csv_path) as f:
            all_candles = parse_candles_from_csv(f.read())
        sessions = split_into_sessions(all_candles, "America/New_York")
        s = next(s for s in sessions if s["date"] == "2026-04-29")
        _, _, _, _, rw = run_full(s["candles"])
        return rw

    def test_status(self, spy_apr29):
        assert spy_apr29["status"] == "OK"

    def test_date(self, spy_apr29):
        assert spy_apr29["date"] == "2026-04-29"

    def test_level_price(self, spy_apr29):
        assert spy_apr29["level_price"] == 711.1900024414062

    def test_retest_start_index(self, spy_apr29):
        assert spy_apr29["retest_start_index"] == 18

    def test_start_timestamp(self, spy_apr29):
        assert spy_apr29["retest_start_timestamp"] == 1777474800000

    def test_window_range(self, spy_apr29):
        assert spy_apr29["retest_window_start_index"] == 18
        assert spy_apr29["retest_window_end_index"] == 77

    def test_window_length(self, spy_apr29):
        assert len(spy_apr29["retest_window"]) == 60

    def test_contact_count(self, spy_apr29):
        assert spy_apr29["retest_contact_count"] == 60

    def test_first_contact(self, spy_apr29):
        rc0 = spy_apr29["retest_contacts"][0]
        assert rc0["candle_index"] == 18
        assert rc0["timestamp"] == 1777474800000
        assert rc0["closest_directional_position_ticks"] == -24
        assert rc0["penetration_through_level_ticks"] == 24
        assert rc0["penetration_through_level_points"] == 0.24
        assert abs(rc0["displacement_retracement_pct"] - 24 / 92) < 1e-10

    def test_last_contact(self, spy_apr29):
        rcL = spy_apr29["retest_contacts"][-1]
        assert rcL["candle_index"] == 77
        assert rcL["penetration_through_level_ticks"] == 68
