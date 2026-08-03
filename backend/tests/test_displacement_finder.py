"""Tests for canonical findDisplacement port.

Parity vectors verified against bdrr_engine.js findDisplacement via
Node.js on dati/SPY_5m.csv sessions.
"""

import copy

import pytest

from trading_lab.displacement_finder import find_displacement
from trading_lab.session_context import build_session_context
from trading_lab.orb_builder import build_orb
from trading_lab.break_finder import find_break


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
    "min_displacement_bars": 1,
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


def run_pipeline(candles_list, config=CONFIG):
    sc = build_session_context(candles_list, config)
    orb = build_orb(sc["candles"], sc, config)
    brk = find_break(sc["candles"], orb, config)
    disp = find_displacement(sc["candles"], orb, brk, config)
    return sc, orb, brk, disp


# ═══════════════════════════════════════════════════════════════════════════════
# Valid displacement
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidDisplacement:
    def _candles(self):
        # ORB: high=101 (level), low=99
        # Break: close=101.50 > 101
        # Displacement: bar at 09:40 with low=101.10 > 101 (1 disp bar)
        # Retest contact: bar at 09:45 with low=100.90 <= 101
        return [
            c(MS_0930, high=101.0, low=99.0, close=100.5),   # ORB
            c(MS_0935, close=101.50),                          # break
            c(MS_0940, high=102.0, low=101.10, close=101.80), # displacement
            c(MS_0945, high=101.50, low=100.90, close=101.20),# retest contact
        ]

    def test_status_ok(self):
        _, _, _, disp = run_pipeline(self._candles())
        assert disp["status"] == "OK"

    def test_displacement_bar_count(self):
        _, _, _, disp = run_pipeline(self._candles())
        assert disp["displacement_bar_count"] == 1

    def test_displacement_start_index(self):
        _, _, _, disp = run_pipeline(self._candles())
        assert disp["displacement_start_index"] == 2  # break is at 1, start = 2

    def test_displacement_end_index(self):
        _, _, _, disp = run_pipeline(self._candles())
        assert disp["displacement_end_index"] == 2  # same as start (1 bar)

    def test_first_retest_contact_index(self):
        _, _, _, disp = run_pipeline(self._candles())
        assert disp["first_retest_contact_index"] == 3

    def test_first_retest_contact_candle(self):
        candles = self._candles()
        sc, _, _, disp = run_pipeline(candles)
        assert disp["first_retest_contact_candle"] is sc["candles"][3]

    def test_first_retest_contact_timestamp(self):
        _, _, _, disp = run_pipeline(self._candles())
        assert disp["first_retest_contact_timestamp"] == MS_0945

    def test_max_favorable_high(self):
        _, _, _, disp = run_pipeline(self._candles())
        assert disp["max_favorable_high"] == 102.0

    def test_displacement_distance_ticks(self):
        # level=101.0 (10100 ticks), max bar high=102.0 (10200 ticks) → 100 ticks
        _, _, _, disp = run_pipeline(self._candles())
        assert disp["displacement_distance"]["ticks"] == 100

    def test_displacement_distance_points(self):
        _, _, _, disp = run_pipeline(self._candles())
        assert disp["displacement_distance"]["points"] == 1.0

    def test_level_price(self):
        _, _, _, disp = run_pipeline(self._candles())
        assert disp["level_price"] == 101.0

    def test_displacement_window_length(self):
        _, _, _, disp = run_pipeline(self._candles())
        assert len(disp["displacement_window"]) == 1

    def test_displacement_window_identity(self):
        candles = self._candles()
        sc, _, _, disp = run_pipeline(candles)
        assert disp["displacement_window"][0] is sc["candles"][2]


class TestMultipleDisplacementBars:
    def test_three_bars(self):
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),    # ORB
            c(MS_0935, close=101.50),                           # break
            c(MS_0940, high=101.50, low=101.05, close=101.30), # disp 1
            c(MS_0945, high=102.00, low=101.10, close=101.80), # disp 2
            c(MS_0950, high=101.80, low=101.20, close=101.60), # disp 3
            c(MS_0955, high=101.30, low=100.50, close=100.80), # retest
        ]
        _, _, _, disp = run_pipeline(candles)
        assert disp["status"] == "OK"
        assert disp["displacement_bar_count"] == 3
        assert disp["displacement_start_index"] == 2
        assert disp["displacement_end_index"] == 4
        assert disp["first_retest_contact_index"] == 5
        assert disp["max_favorable_high"] == 102.00
        # max dist: bar high=102.0 → 10200 - 10100 = 100
        assert disp["displacement_distance"]["ticks"] == 100


class TestOutputFields:
    def test_ok_fields(self):
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, high=102.0, low=101.10, close=101.80),
            c(MS_0945, low=100.90),
        ]
        _, _, _, disp = run_pipeline(candles)
        expected = {
            "status", "date", "level_price", "break_candle_index",
            "displacement_start_index", "displacement_end_index",
            "displacement_bar_count", "displacement_window",
            "max_favorable_high", "displacement_distance",
            "first_retest_contact_index", "first_retest_contact_candle",
            "first_retest_contact_timestamp",
        }
        assert set(disp.keys()) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# RETEST_BEFORE_DISPLACEMENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetestBeforeDisplacement:
    def test_first_post_break_touches_level(self):
        # Break at index 1; next bar at index 2 has low <= level
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),  # ORB
            c(MS_0935, close=101.50),                         # break
            c(MS_0940, low=101.0),                            # low == level → contact
        ]
        _, _, _, disp = run_pipeline(candles)
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "RETEST_BEFORE_DISPLACEMENT"

    def test_first_post_break_below_level(self):
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, low=100.50),  # below level
        ]
        _, _, _, disp = run_pipeline(candles)
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "RETEST_BEFORE_DISPLACEMENT"

    def test_rbd_fields(self):
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, low=100.50),
        ]
        _, _, _, disp = run_pipeline(candles)
        assert "first_retest_contact_index" in disp
        assert "first_retest_contact_candle" in disp
        assert "date" in disp
        assert "break_candle_index" in disp


# ═══════════════════════════════════════════════════════════════════════════════
# RETEST_NOT_FOUND
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetestNotFound:
    def test_no_candle_touches_level(self):
        # All post-break candles have low > level
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, low=101.10),
            c(MS_0945, low=101.20),
        ]
        _, _, _, disp = run_pipeline(candles)
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "RETEST_NOT_FOUND"

    def test_rnf_fields(self):
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, low=101.10),
        ]
        _, _, _, disp = run_pipeline(candles)
        assert "displacement_bar_count" in disp
        assert "displacement_start_index" in disp
        assert disp["displacement_bar_count"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Equality at level boundary
# ═══════════════════════════════════════════════════════════════════════════════


class TestBoundary:
    def test_low_exactly_at_level_is_contact(self):
        """low == level_price counts as retest contact."""
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, low=101.10, high=102.0),  # displacement bar
            c(MS_0945, low=101.0),                 # low == level → contact
        ]
        _, _, _, disp = run_pipeline(candles)
        assert disp["status"] == "OK"
        assert disp["first_retest_contact_index"] == 3

    def test_low_just_above_level_is_displacement(self):
        """low = 101.01 > level 101.0 → displacement bar, not contact."""
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, low=101.01, high=102.0),  # > level → disp bar
            c(MS_0945, low=100.90),                # contact
        ]
        _, _, _, disp = run_pipeline(candles)
        assert disp["status"] == "OK"
        assert disp["displacement_bar_count"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Failed upstream
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailedUpstream:
    def test_failed_orb(self):
        disp = find_displacement([], None, {"status": "OK"}, CONFIG)
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "LEVEL_NOT_FOUND"

    def test_failed_break(self):
        fake_orb = {"status": "OK"}
        disp = find_displacement([], fake_orb, None, CONFIG)
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "BREAK_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
# Unsupported config
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnsupportedConfig:
    def test_unknown_direction(self):
        cfg = {**CONFIG, "direction": "SIDEWAYS"}
        fake_orb = {"status": "OK", "orb_candle_index": 0, "orb_candle": c(MS_0930),
                     "level_price": 101.0, "level_price_ticks": 10100, "date": "2026-07-01"}
        fake_brk = {"status": "OK", "break_candle_index": 1, "break_candle": c(MS_0935)}
        disp = find_displacement([c(MS_0930), c(MS_0935)], fake_orb, fake_brk, cfg)
        assert disp["failed_stage"] == "UNSUPPORTED_CONFIGURATION"

    def test_unsupported_level_source(self):
        cfg = {**CONFIG, "level_source": "PDH"}
        fake_orb = {"status": "OK", "orb_candle_index": 0, "orb_candle": c(MS_0930),
                     "level_price": 99.0, "level_price_ticks": 9900, "date": "2026-07-01"}
        fake_brk = {"status": "OK", "break_candle_index": 1, "break_candle": c(MS_0935)}
        disp = find_displacement([c(MS_0930), c(MS_0935)], fake_orb, fake_brk, cfg)
        assert disp["failed_stage"] == "UNSUPPORTED_CONFIGURATION"

    def test_min_displacement_ticks_set(self):
        cfg = {**CONFIG, "min_displacement_ticks": 10}
        fake_orb = {"status": "OK", "orb_candle_index": 0, "orb_candle": c(MS_0930),
                     "level_price": 101.0, "level_price_ticks": 10100, "date": "2026-07-01"}
        fake_brk = {"status": "OK", "break_candle_index": 1, "break_candle": c(MS_0935)}
        disp = find_displacement([c(MS_0930), c(MS_0935)], fake_orb, fake_brk, cfg)
        assert disp["failed_stage"] == "UNSUPPORTED_CONFIGURATION"
        assert "min_displacement_ticks" in disp["reason"]


# ═══════════════════════════════════════════════════════════════════════════════
# Defensive cross-check
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossCheck:
    def test_mismatched_orb_candle(self):
        candles = [c(MS_0930, high=101.0, low=99.0, close=100.5), c(MS_0935, close=101.5)]
        sc = build_session_context(candles, CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        different = [c(MS_0940)]
        disp = find_displacement(different, orb, brk, CONFIG)
        assert disp["failed_stage"] == "INVALID_INPUT"

    def test_mismatched_break_candle(self):
        candles = [c(MS_0930, high=101.0, low=99.0, close=100.5),
                   c(MS_0935, close=101.5), c(MS_0940, low=100.5)]
        sc = build_session_context(candles, CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        # Replace the break candle with a different timestamp
        alt = list(sc["candles"])
        alt[1] = c(MS_0945, close=102.0)  # different time_ms
        disp = find_displacement(alt, orb, brk, CONFIG)
        assert disp["failed_stage"] == "INVALID_INPUT"


# ═══════════════════════════════════════════════════════════════════════════════
# Input not mutated
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutation:
    def test_candles_not_mutated(self):
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, high=102.0, low=101.10, close=101.80),
            c(MS_0945, low=100.90),
        ]
        original = copy.deepcopy(candles)
        run_pipeline(candles)
        assert candles == original


# ═══════════════════════════════════════════════════════════════════════════════
# Candles type
# ═══════════════════════════════════════════════════════════════════════════════


class TestCandlesType:
    def test_tuple_rejected(self):
        fake_orb = {"status": "OK", "orb_candle_index": 0, "orb_candle": c(MS_0930),
                     "level_price": 101.0, "level_price_ticks": 10100, "date": "2026-07-01"}
        fake_brk = {"status": "OK", "break_candle_index": 1, "break_candle": c(MS_0935)}
        with pytest.raises(TypeError, match="must be a list"):
            find_displacement((), fake_orb, fake_brk, CONFIG)


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministic:
    def test_repeated(self):
        candles = [
            c(MS_0930, high=101.0, low=99.0, close=100.5),
            c(MS_0935, close=101.50),
            c(MS_0940, high=102.0, low=101.10),
            c(MS_0945, low=100.90),
        ]
        results = [run_pipeline(candles)[3]["displacement_bar_count"] for _ in range(10)]
        assert all(r == 1 for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# Real CSV parity
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealCSVParity:
    """JS parity vectors:
    SPY 2026-04-24: RETEST_BEFORE_DISPLACEMENT, contact_index=16
    SPY 2026-04-29: OK, start=11, end=17, count=7, dist_ticks=92,
                    max_high=712.1099853515625, contact_idx=18,
                    contact_ms=1777474800000, level=711.1900024414062
    Across all 60 sessions (where break found):
      OK=15, RETEST_BEFORE_DISPLACEMENT=35, RETEST_NOT_FOUND=2
    """

    @pytest.fixture()
    def spy_sessions(self):
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
        return split_into_sessions(all_candles, "America/New_York")

    def test_apr24_retest_before_displacement(self, spy_sessions):
        s = next(s for s in spy_sessions if s["date"] == "2026-04-24")
        _, _, _, disp = run_pipeline(s["candles"])
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "RETEST_BEFORE_DISPLACEMENT"
        assert disp["first_retest_contact_index"] == 16

    def test_apr29_ok(self, spy_sessions):
        s = next(s for s in spy_sessions if s["date"] == "2026-04-29")
        _, _, _, disp = run_pipeline(s["candles"])
        assert disp["status"] == "OK"
        assert disp["displacement_start_index"] == 11
        assert disp["displacement_end_index"] == 17
        assert disp["displacement_bar_count"] == 7
        assert disp["max_favorable_high"] == 712.1099853515625
        assert disp["displacement_distance"]["ticks"] == 92
        assert disp["displacement_distance"]["points"] == 0.92
        assert disp["first_retest_contact_index"] == 18
        assert disp["first_retest_contact_timestamp"] == 1777474800000
        assert disp["level_price"] == 711.1900024414062

    def test_outcome_counts(self, spy_sessions):
        counts = {"OK": 0, "RBD": 0, "RNF": 0}
        for s in spy_sessions:
            sc = build_session_context(s["candles"], CONFIG)
            orb = build_orb(sc["candles"], sc, CONFIG)
            brk = find_break(sc["candles"], orb, CONFIG)
            if brk["status"] != "OK":
                continue
            disp = find_displacement(sc["candles"], orb, brk, CONFIG)
            if disp["status"] == "OK":
                counts["OK"] += 1
            elif disp["failed_stage"] == "RETEST_BEFORE_DISPLACEMENT":
                counts["RBD"] += 1
            elif disp["failed_stage"] == "RETEST_NOT_FOUND":
                counts["RNF"] += 1
        assert counts == {"OK": 15, "RBD": 35, "RNF": 2}
