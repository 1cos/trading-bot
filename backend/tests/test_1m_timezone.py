"""Tests for 1m CSV timezone handling with America/New_York.

Covers EST, EDT, DST transitions, session open, pre-market exclusion.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading_lab.timeframe_aggregation import parse_csv_candles

ET = ZoneInfo("America/New_York")


def _write_csv(tmp_dir, rows):
    """Write a 1m CSV with header and given data rows."""
    path = tmp_dir / "TEST_1m.csv"
    lines = ["time_et,open,high,low,close,volume\n"]
    for r in rows:
        lines.append(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}\n")
    path.write_text("".join(lines))
    return path


# ── EDT (summer) ─────────────────────────────────────────────────────────────


class TestEDT:
    def test_summer_offset(self, tmp_path):
        """2026-07-24 09:30 ET during EDT = UTC-4 → 13:30 UTC."""
        path = _write_csv(tmp_path, [
            ("2026-07-24 09:30:00", 100, 101, 99, 100.5, 1000),
        ])
        candles = parse_csv_candles(path)
        assert len(candles) == 1

        # Verify: 09:30 EDT = 13:30 UTC
        dt = datetime.fromtimestamp(candles[0]["time_ms"] / 1000, tz=timezone.utc)
        assert dt.hour == 13
        assert dt.minute == 30

    def test_summer_local_time(self, tmp_path):
        """Verify the local ET time is 09:30."""
        path = _write_csv(tmp_path, [
            ("2026-07-24 09:30:00", 100, 101, 99, 100.5, 1000),
        ])
        candles = parse_csv_candles(path)
        dt_et = datetime.fromtimestamp(candles[0]["time_ms"] / 1000, tz=ET)
        assert dt_et.hour == 9
        assert dt_et.minute == 30


# ── EST (winter) ─────────────────────────────────────────────────────────────


class TestEST:
    def test_winter_offset(self, tmp_path):
        """2026-01-15 09:30 ET during EST = UTC-5 → 14:30 UTC."""
        path = _write_csv(tmp_path, [
            ("2026-01-15 09:30:00", 100, 101, 99, 100.5, 1000),
        ])
        candles = parse_csv_candles(path)
        assert len(candles) == 1

        dt = datetime.fromtimestamp(candles[0]["time_ms"] / 1000, tz=timezone.utc)
        assert dt.hour == 14  # 09:30 EST = 14:30 UTC
        assert dt.minute == 30

    def test_winter_local_time(self, tmp_path):
        """Local ET time must still be 09:30."""
        path = _write_csv(tmp_path, [
            ("2026-01-15 09:30:00", 100, 101, 99, 100.5, 1000),
        ])
        candles = parse_csv_candles(path)
        dt_et = datetime.fromtimestamp(candles[0]["time_ms"] / 1000, tz=ET)
        assert dt_et.hour == 9
        assert dt_et.minute == 30


# ── DST transitions ──────────────────────────────────────────────────────────


class TestDSTTransitions:
    def test_spring_forward_after(self, tmp_path):
        """2026-03-08 is spring forward day. 2026-03-09 09:30 should be EDT."""
        path = _write_csv(tmp_path, [
            ("2026-03-09 09:30:00", 100, 101, 99, 100.5, 1000),
        ])
        candles = parse_csv_candles(path)
        # After spring forward → EDT → UTC-4 → 13:30 UTC
        dt = datetime.fromtimestamp(candles[0]["time_ms"] / 1000, tz=timezone.utc)
        assert dt.hour == 13

    def test_spring_forward_before(self, tmp_path):
        """2026-03-07 09:30 should still be EST."""
        path = _write_csv(tmp_path, [
            ("2026-03-07 09:30:00", 100, 101, 99, 100.5, 1000),
        ])
        candles = parse_csv_candles(path)
        # Before spring forward → EST → UTC-5 → 14:30 UTC
        dt = datetime.fromtimestamp(candles[0]["time_ms"] / 1000, tz=timezone.utc)
        assert dt.hour == 14

    def test_fall_back_after(self, tmp_path):
        """2026-11-01 is fall back day. 2026-11-02 09:30 should be EST."""
        path = _write_csv(tmp_path, [
            ("2026-11-02 09:30:00", 100, 101, 99, 100.5, 1000),
        ])
        candles = parse_csv_candles(path)
        # After fall back → EST → UTC-5 → 14:30 UTC
        dt = datetime.fromtimestamp(candles[0]["time_ms"] / 1000, tz=timezone.utc)
        assert dt.hour == 14

    def test_summer_and_winter_differ(self, tmp_path):
        """Same local time in summer vs winter → different UTC timestamps."""
        path = _write_csv(tmp_path, [
            ("2026-07-15 09:30:00", 100, 101, 99, 100.5, 1000),
            ("2026-01-15 09:30:00", 100, 101, 99, 100.5, 1000),
        ])
        candles = parse_csv_candles(path)
        # Different UTC hours
        dt_summer = datetime.fromtimestamp(candles[0]["time_ms"] / 1000, tz=timezone.utc)
        dt_winter = datetime.fromtimestamp(candles[1]["time_ms"] / 1000, tz=timezone.utc)
        assert dt_summer.hour == 13  # EDT
        assert dt_winter.hour == 14  # EST


# ── 5m compatibility ─────────────────────────────────────────────────────────


class TestFiveMinuteCompatibility:
    def test_5m_csv_unchanged(self, tmp_path):
        """TradingView 5m CSVs with tz-aware timestamps parse the same."""
        path = tmp_path / "TEST_5m.csv"
        path.write_text(
            "Price,Close,High,Low,Open,Volume\n"
            "Ticker,TEST,TEST,TEST,TEST,TEST\n"
            "Datetime,,,,,\n"
            "2026-07-24 09:30:00-04:00,100.5,101.0,99.0,100.0,1000\n"
        )
        candles = parse_csv_candles(path)
        assert len(candles) == 1
        dt = datetime.fromtimestamp(candles[0]["time_ms"] / 1000, tz=timezone.utc)
        assert dt.hour == 13  # 09:30 EDT = 13:30 UTC


# ── End-to-end backtest ──────────────────────────────────────────────────────


class TestBacktest1mTimezone:
    @pytest.fixture
    def client(self):
        from trading_lab.backtest_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_spy_1m_orb_at_0930(self, client):
        """Verify ORB starts at 09:30 ET in real 1m data."""
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "1m",
            "preset": {"direction": "LONG", "orb_duration_minutes": 5},
            "config": {"exit_target_r": "2"},
        })
        data = resp.get_json()
        assert "error" not in data

        # Check chart events — ORB candle time should be 09:34 ET
        # (5-bar ORB: 09:30, 09:31, 09:32, 09:33, 09:34 → last = orb_candle)
        for ev in data.get("chart_events", []):
            ann = ev.get("annotations", {})
            # The first post-ORB bar (break candidate) should be after 09:34
            if ann.get("break_candle_time_ms"):
                dt_et = datetime.fromtimestamp(
                    ann["break_candle_time_ms"] / 1000, tz=ET
                )
                # Break must be after ORB window (09:34)
                assert dt_et.hour == 9
                assert dt_et.minute >= 35

    def test_no_premarket_in_orb(self, client):
        """Pre-market candles must not appear in ORB."""
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "1m",
            "preset": {"direction": "LONG", "orb_duration_minutes": 1},
            "config": {"exit_target_r": "2"},
        })
        data = resp.get_json()
        assert "error" not in data
        # All chart events should have session dates, not pre-market
        for ev in data.get("chart_events", []):
            ann = ev.get("annotations", {})
            if ann.get("entry_price_ticks"):
                # Entry should be during regular hours
                entry_ms = ann.get("exit_candle_time_ms") or ann.get("break_candle_time_ms")
                if entry_ms:
                    dt = datetime.fromtimestamp(entry_ms / 1000, tz=ET)
                    assert dt.hour >= 9

    def test_backtest_completes(self, client):
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "1m",
            "preset": {"direction": "BOTH", "level_source": "BOTH",
                       "orb_duration_minutes": 5},
            "config": {"exit_target_r": "2"},
        })
        assert resp.status_code == 200
        assert "error" not in resp.get_json()
