"""Tests for canonical buildORB port.

Parity vectors verified against bdrr_engine.js buildORB via Node.js
on dati/SPY_5m.csv sessions.
"""

import copy

import pytest

from trading_lab.orb_builder import build_orb
from trading_lab.session_context import build_session_context


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

# 2026-07-01 EDT: 09:30 ET = 13:30 UTC
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


def make_session_and_orb(candles, config=CONFIG):
    sc = build_session_context(candles, config)
    orb = build_orb(sc["candles"], sc, config)
    return sc, orb


# ═══════════════════════════════════════════════════════════════════════════════
# Valid ORB construction
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidORB:
    def test_status_ok(self):
        candles = [
            candle(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5),
            candle(MS_0935, open_=100.5, high=100.8, low=100.2, close=100.6),
        ]
        _, orb = make_session_and_orb(candles)
        assert orb["status"] == "OK"

    def test_date(self):
        _, orb = make_session_and_orb([candle(MS_0930)])
        assert orb["date"] == "2026-07-01"

    def test_orb_candle_index(self):
        _, orb = make_session_and_orb([candle(MS_0930), candle(MS_0935)])
        assert orb["orb_candle_index"] == 0

    def test_orb_high(self):
        _, orb = make_session_and_orb([candle(MS_0930, high=101.0)])
        assert orb["orb_high"] == 101.0

    def test_orb_low(self):
        _, orb = make_session_and_orb([candle(MS_0930, low=99.0)])
        assert orb["orb_low"] == 99.0

    def test_orb_low_active_false(self):
        _, orb = make_session_and_orb([candle(MS_0930)])
        assert orb["orb_low_active"] is False

    def test_level_source(self):
        _, orb = make_session_and_orb([candle(MS_0930)])
        assert orb["level_source"] == "ORB_HIGH"

    def test_level_price_equals_orb_high(self):
        _, orb = make_session_and_orb([candle(MS_0930, high=101.0)])
        assert orb["level_price"] == 101.0

    def test_level_price_ticks(self):
        _, orb = make_session_and_orb([candle(MS_0930, high=101.0)])
        assert orb["level_price_ticks"] == 10100

    def test_direction_from_config(self):
        _, orb = make_session_and_orb([candle(MS_0930)])
        assert orb["direction"] == "LONG"

    def test_orb_candle_identity(self):
        """The orb_candle is the same dict object from session_context."""
        c = candle(MS_0930)
        sc = build_session_context([c], CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        assert orb["orb_candle"] is sc["candles"][0]


class TestOutputFields:
    def test_ok_fields(self):
        _, orb = make_session_and_orb([candle(MS_0930)])
        assert set(orb.keys()) == {
            "status", "date", "orb_candle_index", "orb_candle",
            "orb_high", "orb_low", "orb_low_active", "level_source",
            "level_price", "level_price_ticks", "direction",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ORB candle at non-first index
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonFirstIndex:
    def test_orb_at_index_2(self):
        """ORB candle not at index 0 if earlier candles exist."""
        # 09:20, 09:25, 09:30 — only 09:30 matches session_open
        ms_0920 = MS_0930 - 600000
        ms_0925 = MS_0930 - 300000
        candles = [candle(ms_0920), candle(ms_0925), candle(MS_0930)]
        _, orb = make_session_and_orb(candles)
        assert orb["orb_candle_index"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Tick conversion
# ═══════════════════════════════════════════════════════════════════════════════


class TestTickConversion:
    def test_spy_level(self):
        """750.44 / 0.01 → 75044 ticks."""
        _, orb = make_session_and_orb(
            [candle(MS_0930, high=750.44)]
        )
        assert orb["level_price_ticks"] == 75044

    def test_quarter_tick(self):
        cfg = {**CONFIG, "tick_size": 0.25}
        _, orb = make_session_and_orb(
            [candle(MS_0930, high=100.25)], cfg
        )
        assert orb["level_price_ticks"] == 401

    def test_rounding(self):
        """Half-tick: 100.005 / 0.01 → 10001 (round half toward +∞)."""
        _, orb = make_session_and_orb(
            [candle(MS_0930, high=100.005)]
        )
        assert orb["level_price_ticks"] == 10001


# ═══════════════════════════════════════════════════════════════════════════════
# Missing ORB candle
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingORB:
    def test_no_candle_at_session_open(self):
        """Only a 09:35 candle — no 09:30 → FAILED."""
        candles = [candle(MS_0935)]
        _, orb = make_session_and_orb(candles)
        assert orb["status"] == "FAILED"
        assert orb["failed_stage"] == "LEVEL_NOT_FOUND"
        assert "ORB candle not found" in orb["reason"]


# ═══════════════════════════════════════════════════════════════════════════════
# FAILED session context
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailedContext:
    def test_failed_context(self):
        failed_sc = {"status": "FAILED", "failed_stage": "X", "reason": "bad"}
        orb = build_orb([], failed_sc, CONFIG)
        assert orb["status"] == "FAILED"
        assert orb["failed_stage"] == "LEVEL_NOT_FOUND"
        assert "sessionContext" in orb["reason"]

    def test_none_context(self):
        orb = build_orb([], None, CONFIG)
        assert orb["status"] == "FAILED"
        assert orb["failed_stage"] == "LEVEL_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
# Unsupported configuration
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnsupportedConfig:
    def test_orb_start_not_session_open(self):
        cfg = {**CONFIG, "orb_start": "custom"}
        sc = build_session_context([candle(MS_0930)], cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "FAILED"
        assert orb["failed_stage"] == "UNSUPPORTED_CONFIGURATION"
        assert "orb_start" in orb["reason"]

    def test_multi_candle_orb(self):
        cfg = {**CONFIG, "orb_duration_minutes": 15}
        sc = build_session_context([candle(MS_0930)], cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "FAILED"
        assert orb["failed_stage"] == "UNSUPPORTED_CONFIGURATION"
        assert "multi-candle" in orb["reason"]

    def test_level_source_not_orb_high(self):
        cfg = {**CONFIG, "level_source": "ORB_LOW"}
        sc = build_session_context([candle(MS_0930)], cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "FAILED"
        assert orb["failed_stage"] == "UNSUPPORTED_CONFIGURATION"
        assert "level_source" in orb["reason"]


# ═══════════════════════════════════════════════════════════════════════════════
# Defensive cross-check
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossCheck:
    def test_mismatched_candles(self):
        sc = build_session_context([candle(MS_0930), candle(MS_0935)], CONFIG)
        different = [candle(MS_0940)]
        orb = build_orb(different, sc, CONFIG)
        assert orb["status"] == "FAILED"
        assert orb["failed_stage"] == "INVALID_INPUT"

    def test_same_candles_ok(self):
        sc = build_session_context([candle(MS_0930)], CONFIG)
        orb = build_orb(sc["candles"], sc, CONFIG)
        assert orb["status"] == "OK"


# ═══════════════════════════════════════════════════════════════════════════════
# Config validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigValidation:
    def test_missing_tick_size(self):
        cfg = {k: v for k, v in CONFIG.items() if k != "tick_size"}
        with pytest.raises(TypeError, match="tick_size"):
            build_orb([], {}, cfg)

    def test_config_none(self):
        with pytest.raises(TypeError, match="must be a dict"):
            build_orb([], {}, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Input not mutated
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutation:
    def test_candles_not_mutated(self):
        candles = [candle(MS_0930), candle(MS_0935)]
        original = copy.deepcopy(candles)
        sc = build_session_context(candles, CONFIG)
        build_orb(sc["candles"], sc, CONFIG)
        assert candles == original

    def test_config_not_mutated(self):
        cfg = {**CONFIG}
        original = copy.deepcopy(cfg)
        sc = build_session_context([candle(MS_0930)], cfg)
        build_orb(sc["candles"], sc, cfg)
        assert cfg == original


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministic:
    def test_repeated(self):
        candles = [candle(MS_0930, high=101.0)]
        results = []
        for _ in range(10):
            sc = build_session_context(candles, CONFIG)
            orb = build_orb(sc["candles"], sc, CONFIG)
            results.append(orb["level_price_ticks"])
        assert all(r == 10100 for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# No detection fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoDetection:
    def test_no_break_fields(self):
        _, orb = make_session_and_orb([candle(MS_0930)])
        assert "break_candle" not in orb
        assert "break_candle_index" not in orb
        assert "displacement" not in orb


# ═══════════════════════════════════════════════════════════════════════════════
# EST/EDT
# ═══════════════════════════════════════════════════════════════════════════════


class TestDST:
    def test_edt_session(self):
        """EDT: 2026-07-01 09:30 ET = 13:30 UTC."""
        _, orb = make_session_and_orb([candle(MS_0930)])
        assert orb["date"] == "2026-07-01"

    def test_est_session(self):
        """EST: 2026-01-05 09:30 ET = 14:30 UTC."""
        ms = 1767623400000  # 2026-01-05T14:30:00Z
        _, orb = make_session_and_orb([candle(ms)])
        assert orb["date"] == "2026-01-05"


# ═══════════════════════════════════════════════════════════════════════════════
# Real CSV parity
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealCSVParity:
    """Parity against JS buildORB on SPY_5m.csv 2026-04-24.

    JS results:
      status: OK, date: 2026-04-24, orb_candle_index: 0,
      orb_high: 711.1599731445312, orb_low: 709.760009765625,
      level_price: 711.1599731445312, level_price_ticks: 71116,
      direction: LONG, orb_candle time_ms: 1777037400000
    """

    @pytest.fixture()
    def spy_orb(self):
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
        sc = build_session_context(sessions[0]["candles"], CONFIG)
        return build_orb(sc["candles"], sc, CONFIG)

    def test_status(self, spy_orb):
        assert spy_orb["status"] == "OK"

    def test_date(self, spy_orb):
        assert spy_orb["date"] == "2026-04-24"

    def test_orb_candle_index(self, spy_orb):
        assert spy_orb["orb_candle_index"] == 0

    def test_orb_high(self, spy_orb):
        assert spy_orb["orb_high"] == 711.1599731445312

    def test_orb_low(self, spy_orb):
        assert spy_orb["orb_low"] == 709.760009765625

    def test_level_price(self, spy_orb):
        assert spy_orb["level_price"] == 711.1599731445312

    def test_level_price_ticks(self, spy_orb):
        assert spy_orb["level_price_ticks"] == 71116

    def test_direction(self, spy_orb):
        assert spy_orb["direction"] == "LONG"

    def test_orb_candle_timestamp(self, spy_orb):
        assert spy_orb["orb_candle"]["time_ms"] == 1777037400000
