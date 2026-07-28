"""Tests for canonical findBreak port.

Parity vectors verified against bdrr_engine.js findBreak via Node.js
on dati/SPY_5m.csv sessions.
"""

import copy

import pytest

from trading_lab.break_finder import find_break
from trading_lab.session_context import build_session_context
from trading_lab.orb_builder import build_orb


# ── Config fixture ────────────────────────────────────────────────────────────

CONFIG = {
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "tick_size": 0.01,
}

# 2026-07-01 EDT
MS_0930 = 1782912600000
MS_0935 = 1782912900000
MS_0940 = 1782913200000
MS_0945 = 1782913500000


def candle(time_ms, open_=100.0, high=101.0, low=99.0, close=100.5):
    return {
        "time_ms": time_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def run_pipeline(candles_list, config=CONFIG):
    sc = build_session_context(candles_list, config)
    orb = build_orb(sc["candles"], sc, config)
    brk = find_break(sc["candles"], orb, config)
    return sc, orb, brk


# ═══════════════════════════════════════════════════════════════════════════════
# Valid LONG break
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidBreak:
    def test_first_candle_after_orb_breaks(self):
        """Close 101.50 > level 101.00 → break at index 1."""
        candles = [
            candle(MS_0930, high=101.0, low=99.0, close=100.5),
            candle(MS_0935, close=101.50),
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["status"] == "OK"
        assert brk["break_candle_index"] == 1
        assert brk["break_candle"]["close"] == 101.50

    def test_date_from_orb(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.50),
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["date"] == "2026-07-01"

    def test_break_timestamp(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.50),
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["break_timestamp"] == MS_0935

    def test_break_candle_identity(self):
        """break_candle is the same dict object."""
        c_orb = candle(MS_0930, high=101.0, close=100.5)
        c_brk = candle(MS_0935, close=101.50)
        sc = build_session_context([c_orb, c_brk], CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        brk = find_break(sc["candles"], orb, CONFIG)
        assert brk["break_candle"] is sc["candles"][1]

    def test_distance_ticks(self):
        """level=101.0 (10100 ticks), close=101.50 (10150 ticks) → 50 ticks."""
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.50),
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["directional_break_distance"]["ticks"] == 50

    def test_distance_points(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.50),
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["directional_break_distance"]["points"] == 0.50


class TestOutputFields:
    def test_ok_fields(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.50),
        ]
        _, _, brk = run_pipeline(candles)
        assert set(brk.keys()) == {
            "status", "date", "break_candle_index", "break_candle",
            "break_timestamp", "directional_break_distance",
        }

    def test_distance_fields(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.50),
        ]
        _, _, brk = run_pipeline(candles)
        assert set(brk["directional_break_distance"].keys()) == {
            "points", "ticks",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Strict comparison — equality does NOT qualify
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrictComparison:
    def test_close_equals_level_no_break(self):
        """close == level → NOT a break (strict >)."""
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.0),  # exactly at level
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "BREAK_NOT_FOUND"

    def test_close_just_above_level(self):
        """close = 101.01 > level 101.0 → break."""
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.01),
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["status"] == "OK"

    def test_close_just_below_level(self):
        """close = 100.99 < level 101.0 → no break."""
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=100.99),
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["status"] == "FAILED"


# ═══════════════════════════════════════════════════════════════════════════════
# Wick crossing without close doesn't qualify
# ═══════════════════════════════════════════════════════════════════════════════


class TestWickOnly:
    def test_wick_above_but_close_below(self):
        """High crosses level but close doesn't → no break."""
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, high=102.0, close=100.50),  # wick above, close at level
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["status"] == "FAILED"


# ═══════════════════════════════════════════════════════════════════════════════
# ORB candle not eligible
# ═══════════════════════════════════════════════════════════════════════════════


class TestORBExcluded:
    def test_orb_candle_close_above_level_not_break(self):
        """ORB candle itself closes above its own high → doesn't count."""
        # This can happen if open/close > high conceptually, but realistically
        # the ORB candle's close could match or exceed high.  The function
        # starts scanning at orb_candle_index + 1.
        candles = [
            candle(MS_0930, high=101.0, close=101.50),  # close > high (weird but allowed)
            candle(MS_0935, close=100.50),  # below level
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "BREAK_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
# First qualifying candle selected
# ═══════════════════════════════════════════════════════════════════════════════


class TestFirstQualifying:
    def test_second_candle_qualifies_not_third(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.50),  # break
            candle(MS_0940, close=102.00),  # also qualifies but not returned
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["break_candle_index"] == 1
        assert brk["break_candle"]["close"] == 101.50

    def test_third_candle_is_first_break(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=100.80),  # below
            candle(MS_0940, close=101.20),  # first break
            candle(MS_0945, close=101.50),  # also qualifies
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["break_candle_index"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# No break
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoBreak:
    def test_all_below_level(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=100.80),
            candle(MS_0940, close=100.90),
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "BREAK_NOT_FOUND"

    def test_only_orb_candle(self):
        """Single candle is the ORB — no post-ORB candles exist."""
        candles = [candle(MS_0930, high=101.0, close=100.5)]
        _, _, brk = run_pipeline(candles)
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "BREAK_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
# Break on final candle
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakOnLastCandle:
    def test_last_candle_breaks(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=100.80),
            candle(MS_0940, close=101.50),  # break on last
        ]
        _, _, brk = run_pipeline(candles)
        assert brk["status"] == "OK"
        assert brk["break_candle_index"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Zero distance (raw float > level but same tick)
# ═══════════════════════════════════════════════════════════════════════════════


class TestZeroDistance:
    def test_zero_tick_distance(self):
        """close slightly above level in float but same tick → 0 distance.
        JS parity: SPY 2026-04-24 close=711.1649... > level=711.1599...
        Both round to 71116 ticks → distance = 0."""
        candles = [
            candle(MS_0930, high=711.1599731445312, close=710.0),
            candle(MS_0935, close=711.1649780273438),  # barely above
        ]
        cfg = {**CONFIG}
        _, _, brk = run_pipeline(candles, cfg)
        assert brk["status"] == "OK"
        assert brk["directional_break_distance"]["ticks"] == 0
        assert brk["directional_break_distance"]["points"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Failed / missing ORB
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailedORB:
    def test_failed_orb(self):
        failed_orb = {"status": "FAILED", "failed_stage": "LEVEL_NOT_FOUND", "reason": "bad"}
        brk = find_break([], failed_orb, CONFIG)
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "LEVEL_NOT_FOUND"

    def test_none_orb(self):
        brk = find_break([], None, CONFIG)
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "LEVEL_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
# Unsupported direction
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnsupportedDirection:
    def test_unknown(self):
        cfg = {**CONFIG, "direction": "SIDEWAYS"}
        candles = [candle(MS_0930)]
        fake_orb = {
            "status": "OK", "date": "2026-07-01",
            "orb_candle_index": 0, "orb_candle": candles[0],
            "level_price": 101.0, "level_price_ticks": 10100,
        }
        brk = find_break(candles, fake_orb, cfg)
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "UNSUPPORTED_CONFIGURATION"
        assert "SIDEWAYS" in brk["reason"]


# ═══════════════════════════════════════════════════════════════════════════════
# Defensive cross-check
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossCheck:
    def test_mismatched_candles(self):
        c_orb = candle(MS_0930, high=101.0, close=100.5)
        sc = build_session_context([c_orb, candle(MS_0935)], CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        different = [candle(MS_0940)]  # doesn't match
        brk = find_break(different, orb, CONFIG)
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "INVALID_INPUT"


# ═══════════════════════════════════════════════════════════════════════════════
# Config validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigValidation:
    def test_missing_direction(self):
        cfg = {k: v for k, v in CONFIG.items() if k != "direction"}
        with pytest.raises(TypeError, match="direction"):
            find_break([], {}, cfg)

    def test_config_none(self):
        with pytest.raises(TypeError, match="must be a dict"):
            find_break([], {}, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Input not mutated
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutation:
    def test_candles_not_mutated(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.50),
        ]
        original = copy.deepcopy(candles)
        run_pipeline(candles)
        assert candles == original


# ═══════════════════════════════════════════════════════════════════════════════
# Candles not a list
# ═══════════════════════════════════════════════════════════════════════════════


class TestCandlesType:
    def test_tuple(self):
        fake_orb = {"status": "OK", "orb_candle_index": 0,
                     "orb_candle": candle(MS_0930),
                     "level_price": 101.0, "level_price_ticks": 10100,
                     "date": "2026-07-01"}
        with pytest.raises(TypeError, match="must be a list"):
            find_break((), fake_orb, CONFIG)


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministic:
    def test_repeated(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.50),
        ]
        results = []
        for _ in range(10):
            _, _, brk = run_pipeline(candles)
            results.append(brk["break_candle_index"])
        assert all(r == 1 for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# No displacement fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoDownstreamFields:
    def test_no_displacement_fields(self):
        candles = [
            candle(MS_0930, high=101.0, close=100.5),
            candle(MS_0935, close=101.50),
        ]
        _, _, brk = run_pipeline(candles)
        assert "displacement" not in brk
        assert "retest" not in brk
        assert "rejection" not in brk


# ═══════════════════════════════════════════════════════════════════════════════
# Real CSV parity
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealCSVParity:
    """JS parity on SPY 2026-04-24:
      break_candle_index: 15
      break_timestamp_ms: 1777041900000
      break_close: 711.1649780273438
      distance_ticks: 0, distance_points: 0
    """

    @pytest.fixture()
    def spy_break(self):
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
        _, _, brk = run_pipeline(sessions[0]["candles"])
        return brk

    def test_status(self, spy_break):
        assert spy_break["status"] == "OK"

    def test_index(self, spy_break):
        assert spy_break["break_candle_index"] == 15

    def test_timestamp(self, spy_break):
        assert spy_break["break_timestamp"] == 1777041900000

    def test_close(self, spy_break):
        assert spy_break["break_candle"]["close"] == 711.1649780273438

    def test_distance_ticks(self, spy_break):
        assert spy_break["directional_break_distance"]["ticks"] == 0

    def test_distance_points(self, spy_break):
        assert spy_break["directional_break_distance"]["points"] == 0.0

    def test_no_break_session(self):
        """SPY 2026-05-27 has no break in JS."""
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
        # Find 2026-05-27
        s = next(s for s in sessions if s["date"] == "2026-05-27")
        _, _, brk = run_pipeline(s["candles"])
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "BREAK_NOT_FOUND"
