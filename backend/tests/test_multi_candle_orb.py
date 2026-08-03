"""Tests for multi-candle ORB construction.

Covers: bar count, high/low aggregation, pre-market exclusion,
invalid durations, missing bars, 5m/5m compatibility, real 1m backtest.
"""

import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.orb_builder import build_orb
from trading_lab.session_context import build_session_context


# ── Fixtures ──────────────────────────────────────────────────────────────────

ET = ZoneInfo("America/New_York")
# 2026-07-01 09:30 ET = 13:30 UTC
MS_0930 = int(datetime(2026, 7, 1, 9, 30, tzinfo=ET).timestamp() * 1000)


def _c(ms, o=100.0, h=101.0, l=99.0, cl=100.5, vol=1000):
    return {"time_ms": ms, "open": o, "high": h, "low": l, "close": cl, "volume": vol}


def _cfg(tf=5, orb_dur=5, **kw):
    base = {
        "timeframe_minutes": tf, "timezone": "America/New_York",
        "session_open": "09:30", "orb_start": "session_open",
        "orb_duration_minutes": orb_dur, "level_source": "ORB_HIGH",
        "direction": "LONG", "tick_size": 0.01,
    }
    base.update(kw)
    return base


def _1m_candles(count, start_ms=MS_0930):
    """Generate count 1m candles starting at start_ms with varying prices."""
    candles = []
    for i in range(count):
        ms = start_ms + i * 60000
        h = 100.0 + (i % 5) * 0.5
        l = 99.0 - (i % 3) * 0.2
        candles.append(_c(ms, o=99.5+i*0.1, h=h, l=l, cl=99.8+i*0.1))
    return candles


def _5m_candles(count, start_ms=MS_0930):
    """Generate count 5m candles."""
    candles = []
    for i in range(count):
        ms = start_ms + i * 300000
        h = 100.0 + i * 0.5
        l = 99.0 - i * 0.2
        candles.append(_c(ms, o=99.5, h=h, l=l, cl=100.0))
    return candles


# ── Bar count ────────────────────────────────────────────────────────────────


class TestBarCount:
    def test_1m_orb_1m(self):
        """1m TF + 1m ORB = 1 bar."""
        candles = _1m_candles(20)
        cfg = _cfg(tf=1, orb_dur=1)
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "OK"
        # orb_candle_index = 0 (single bar)
        assert orb["orb_candle_index"] == 0

    def test_1m_orb_5m(self):
        """1m TF + 5m ORB = 5 bars."""
        candles = _1m_candles(30)
        cfg = _cfg(tf=1, orb_dur=5)
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "OK"
        # Last ORB bar is at index 4 (0-based, 5 bars: 0,1,2,3,4)
        assert orb["orb_candle_index"] == 4

    def test_1m_orb_15m(self):
        """1m TF + 15m ORB = 15 bars."""
        candles = _1m_candles(50)
        cfg = _cfg(tf=1, orb_dur=15)
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "OK"
        assert orb["orb_candle_index"] == 14

    def test_5m_orb_15m(self):
        """5m TF + 15m ORB = 3 bars."""
        candles = _5m_candles(20)
        cfg = _cfg(tf=5, orb_dur=15)
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "OK"
        assert orb["orb_candle_index"] == 2


# ── High/Low aggregation ────────────────────────────────────────────────────


class TestHighLowAggregation:
    def test_orb_high_is_max(self):
        """ORB high must be the max high across all ORB bars."""
        candles = [
            _c(MS_0930, h=100.0, l=99.0),
            _c(MS_0930 + 60000, h=101.5, l=98.5),   # highest
            _c(MS_0930 + 120000, h=100.5, l=99.5),
            _c(MS_0930 + 180000, h=100.2, l=99.2),
            _c(MS_0930 + 240000, h=100.8, l=99.8),
        ] + _1m_candles(20, MS_0930 + 300000)
        cfg = _cfg(tf=1, orb_dur=5)
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "OK"
        assert orb["orb_high"] == 101.5

    def test_orb_low_is_min(self):
        """ORB low must be the min low across all ORB bars."""
        candles = [
            _c(MS_0930, h=100.0, l=99.0),
            _c(MS_0930 + 60000, h=101.0, l=97.5),    # lowest
            _c(MS_0930 + 120000, h=100.5, l=99.5),
            _c(MS_0930 + 180000, h=100.2, l=99.2),
            _c(MS_0930 + 240000, h=100.8, l=99.8),
        ] + _1m_candles(20, MS_0930 + 300000)
        cfg = _cfg(tf=1, orb_dur=5)
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "OK"
        assert orb["orb_low"] == 97.5


# ── Pre-market exclusion ────────────────────────────────────────────────────


class TestPreMarket:
    def test_premarket_not_in_orb(self):
        """Candles before session_open must not affect ORB."""
        pre = _c(MS_0930 - 60000, h=200.0, l=50.0)  # extreme pre-market
        regular = _1m_candles(30, MS_0930)
        candles = [pre] + regular
        cfg = _cfg(tf=1, orb_dur=5)
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "OK"
        # ORB high should NOT be 200 (pre-market)
        assert orb["orb_high"] < 200.0
        assert orb["orb_low"] > 50.0


# ── Invalid durations ───────────────────────────────────────────────────────


class TestInvalidDuration:
    def test_non_multiple_rejected(self):
        """5m TF + 7m ORB must be rejected."""
        candles = _5m_candles(20)
        cfg = _cfg(tf=5, orb_dur=7)
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "FAILED"
        assert "multiple" in orb["reason"]

    def test_zero_duration_rejected(self):
        cfg = _cfg(tf=5, orb_dur=0)
        sc = build_session_context(_5m_candles(10), cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "FAILED"


# ── Missing bars ─────────────────────────────────────────────────────────────


class TestMissingBars:
    def test_insufficient_candles(self):
        """Need 5 bars for 1m/5m ORB, but only 3 available."""
        candles = _1m_candles(3)
        cfg = _cfg(tf=1, orb_dur=5)
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "FAILED"
        assert "insufficient" in orb["reason"]


# ── 5m/5m compatibility ─────────────────────────────────────────────────────


class TestCompatibility5m5m:
    def test_single_bar_orb_unchanged(self):
        """5m TF + 5m ORB = 1 bar, same as before."""
        candles = _5m_candles(10)
        cfg = _cfg(tf=5, orb_dur=5)
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "OK"
        assert orb["orb_candle_index"] == 0
        # High/low from single candle
        assert orb["orb_high"] == candles[0]["high"]
        assert orb["orb_low"] == candles[0]["low"]


# ── End-to-end 1m backtest ───────────────────────────────────────────────────


class TestBacktest1m:
    @pytest.fixture
    def client(self):
        from trading_lab.backtest_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_spy_1m_orb_5m_no_exception(self, client):
        """SPY 1m with 5m ORB must complete without error."""
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "1m",
            "preset": {"direction": "LONG", "orb_duration_minutes": 5},
            "config": {"exit_target_r": "2"},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" not in data
        assert "metrics" in data

    def test_spy_1m_orb_15m_no_exception(self, client):
        """SPY 1m with 15m ORB must complete without error."""
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "1m",
            "preset": {"direction": "BOTH", "level_source": "BOTH",
                       "orb_duration_minutes": 15},
            "config": {"exit_target_r": "2"},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" not in data
