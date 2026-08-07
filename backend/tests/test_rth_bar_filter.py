"""Tests for bar-level RTH filtering of IBKR equity sessions.

Verifies that strip_non_rth_bars correctly removes pre-market and
after-hours bars so only RTH (09:30–15:59 ET) enter the strategy
pipeline.

Invariants:
  1. No bar before 09:30 ET or >= 16:00 ET in the strategy array.
  2. ORB 5m occupies index 0 for a complete session.
  3. Complete sessions: 390 (1m), 195 (2m), 78 (5m) bars.
  4. Warmup = last 14 RTH bars of previous session (not after-hours).
  5. session_close_utc_ms = last RTH bar, not after-hours.
  6. No trade can be entered or exited on an after-hours bar.
  7. Futures path is not affected.
"""

import os
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.timeframe_aggregation import (
    load_candles_for_timeframe,
    strip_non_rth_bars,
    get_warmup_candles,
)
from trading_lab.strategy_runner import run_bdrr_strategy

ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")
DATI = os.path.join(os.path.dirname(__file__), "..", "..", "dati")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ms(year, month, day, hour, minute, tz):
    """Build epoch ms from local time."""
    dt = datetime(year, month, day, hour, minute, tzinfo=tz)
    return int(dt.timestamp() * 1000)


def _candle(time_ms, o=100.0, h=101.0, l=99.0, c=100.5):
    return {"time_ms": time_ms, "open": o, "high": h, "low": l,
            "close": c, "volume": 1000}


FROZEN_PRESET = {
    "preset_id": "bdrr_v1_initial", "timeframe_minutes": 5,
    "timezone": "America/New_York", "session_open": "09:30",
    "orb_start": "session_open", "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH", "direction": "LONG",
    "entry_model": "CONFIRMATION_CLOSE", "entry_buffer_ticks": 0,
    "stop_buffer_ticks": 0, "min_displacement_ticks": None,
    "min_penetration_ticks": None, "min_close_beyond_level_ticks": None,
    "consecutive_orb_closes": 2,
}
BASE_CONFIG = {"tick_size": 0.01, "exit_target_r": 2,
               "engine_version": "bdrr_v1.0"}


# ── Unit tests: strip_non_rth_bars ───────────────────────────────────────────


class TestStripNonRthBars:
    """Unit tests for the bar-level filter."""

    def test_excludes_0929(self):
        """A 09:29 ET bar must be excluded."""
        cbd = {"2026-06-11": [
            _candle(_ms(2026, 6, 11, 9, 29, ET)),
            _candle(_ms(2026, 6, 11, 9, 30, ET)),
        ]}
        result = strip_non_rth_bars(cbd)
        bars = result["2026-06-11"]
        times = [
            datetime.fromtimestamp(c["time_ms"] / 1000, tz=timezone.utc)
            .astimezone(ET).strftime("%H:%M")
            for c in bars
        ]
        assert "09:29" not in times
        assert "09:30" in times

    def test_includes_0930(self):
        """A 09:30 ET bar must be included."""
        cbd = {"2026-06-11": [_candle(_ms(2026, 6, 11, 9, 30, ET))]}
        result = strip_non_rth_bars(cbd)
        assert len(result["2026-06-11"]) == 1

    def test_includes_1559(self):
        """A 15:59 ET bar (last 1m RTH) must be included."""
        cbd = {"2026-06-11": [_candle(_ms(2026, 6, 11, 15, 59, ET))]}
        result = strip_non_rth_bars(cbd)
        assert len(result["2026-06-11"]) == 1

    def test_excludes_1600(self):
        """A 16:00 ET bar must be excluded."""
        cbd = {"2026-06-11": [
            _candle(_ms(2026, 6, 11, 15, 59, ET)),
            _candle(_ms(2026, 6, 11, 16, 0, ET)),
        ]}
        result = strip_non_rth_bars(cbd)
        assert len(result["2026-06-11"]) == 1

    def test_drops_empty_dates(self):
        """A date with only non-RTH bars must be dropped entirely."""
        cbd = {"2026-06-11": [
            _candle(_ms(2026, 6, 11, 4, 0, ET)),
            _candle(_ms(2026, 6, 11, 19, 0, ET)),
        ]}
        result = strip_non_rth_bars(cbd)
        assert "2026-06-11" not in result

    def test_custom_session_window(self):
        """Supports custom session_open/close (e.g. futures)."""
        cbd = {"2026-06-11": [
            _candle(_ms(2026, 6, 11, 8, 29, CT)),
            _candle(_ms(2026, 6, 11, 8, 30, CT)),
            _candle(_ms(2026, 6, 11, 14, 55, CT)),
            _candle(_ms(2026, 6, 11, 15, 0, CT)),
        ]}
        result = strip_non_rth_bars(
            cbd,
            timezone_str="America/Chicago",
            session_open="08:30",
            session_close="15:00",
        )
        assert len(result["2026-06-11"]) == 2  # 08:30 and 14:55


# ── Integration: real IBKR data ──────────────────────────────────────────────


class TestRealIbkrEquity:
    """Tests on real IBKR equity data."""

    def test_1m_390_bars(self):
        """1m complete session has 390 bars."""
        result = load_candles_for_timeframe(DATI, "SPY", 1)
        # Check a mid-dataset session
        dates = result["dates"]
        mid = dates[len(dates) // 2]
        candles = result["candles_by_date"][mid]
        assert len(candles) == 390

    def test_2m_195_bars(self):
        """2m complete session has 195 bars."""
        result = load_candles_for_timeframe(DATI, "SPY", 2)
        dates = result["dates"]
        mid = dates[len(dates) // 2]
        candles = result["candles_by_date"][mid]
        assert len(candles) == 195

    def test_5m_78_bars(self):
        """5m complete session has 78 bars."""
        result = load_candles_for_timeframe(DATI, "SPY", 5)
        dates = result["dates"]
        mid = dates[len(dates) // 2]
        candles = result["candles_by_date"][mid]
        assert len(candles) == 78

    def test_first_bar_is_0930(self):
        """First bar of every equity session is 09:30 ET."""
        result = load_candles_for_timeframe(DATI, "QQQ", 5)
        for date, candles in result["candles_by_date"].items():
            dt = datetime.fromtimestamp(
                candles[0]["time_ms"] / 1000, tz=timezone.utc
            ).astimezone(ET)
            assert dt.strftime("%H:%M") == "09:30", (
                f"{date}: first bar at {dt.strftime('%H:%M')}, expected 09:30"
            )

    def test_last_bar_before_1600(self):
        """Last bar of every equity session is before 16:00 ET."""
        result = load_candles_for_timeframe(DATI, "QQQ", 5)
        for date, candles in result["candles_by_date"].items():
            dt = datetime.fromtimestamp(
                candles[-1]["time_ms"] / 1000, tz=timezone.utc
            ).astimezone(ET)
            minutes = dt.hour * 60 + dt.minute
            assert minutes < 960, (
                f"{date}: last bar at {dt.strftime('%H:%M')}, expected < 16:00"
            )

    def test_orb_index_0_at_5m(self):
        """ORB candle is at index 0 for 5m equity sessions."""
        from trading_lab.session_context import build_session_context
        from trading_lab.orb_builder import build_orb

        result = load_candles_for_timeframe(DATI, "SPY", 5)
        date = result["dates"][5]  # arbitrary non-first session
        candles = result["candles_by_date"][date]
        engine_cfg = {
            "timeframe_minutes": 5, "timezone": "America/New_York",
            "session_open": "09:30", "orb_start": "session_open",
            "orb_duration_minutes": 5, "level_source": "ORB_HIGH",
            "direction": "LONG", "tick_size": 0.01,
        }
        sc = build_session_context(candles, engine_cfg)
        orb = build_orb(candles, sc, engine_cfg)
        assert orb["orb_candle_index"] == 0


# ── Warmup source ────────────────────────────────────────────────────────────


class TestWarmupIsRth:
    """Warmup candles must come from RTH, not after-hours."""

    def test_warmup_all_rth(self):
        """All 14 warmup candles are within 09:30–15:59 ET."""
        result = load_candles_for_timeframe(DATI, "QQQ", 5)
        cbd = result["candles_by_date"]
        dates = result["dates"]
        if len(dates) < 2:
            pytest.skip("Need ≥2 sessions")
        warmup, _ = get_warmup_candles(cbd, dates[5], 14)
        assert len(warmup) == 14
        for c in warmup:
            dt = datetime.fromtimestamp(
                c["time_ms"] / 1000, tz=timezone.utc
            ).astimezone(ET)
            minutes = dt.hour * 60 + dt.minute
            assert 570 <= minutes < 960, (
                f"Warmup bar at {dt.strftime('%H:%M')} is outside RTH"
            )


# ── After-hours contamination elimination ────────────────────────────────────


class TestNoAfterHoursContamination:
    """Confirm that contamination cases from the audit are eliminated."""

    def test_spy_20260512_no_afterhours_trade(self):
        """SPY 2026-05-12: after-hours trade (confirmation 16:10) must vanish."""
        result = load_candles_for_timeframe(DATI, "SPY", 5)
        cbd = result["candles_by_date"]
        candles = cbd["2026-05-12"]
        warmup, wpc = get_warmup_candles(cbd, "2026-05-12", 14)
        sess = {
            "symbol": "SPY", "date": "2026-05-12",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": candles[0]["time_ms"],
            "session_close_utc_ms": candles[-1]["time_ms"],
            "timeframe": "5m", "candles": candles,
        }
        if warmup:
            sess["warmup_candles"] = warmup
            sess["warmup_previous_close"] = wpc
        r = run_bdrr_strategy([sess], FROZEN_PRESET, BASE_CONFIG)[0]
        assert str(r["outcome"]) == "NO_VALID_SETUP"

    def test_spy_20260717_no_afterhours_stop(self):
        """SPY 2026-07-17: stop at 16:10 must become SESSION_CLOSE at 15:55."""
        result = load_candles_for_timeframe(DATI, "SPY", 5)
        cbd = result["candles_by_date"]
        candles = cbd["2026-07-17"]
        warmup, wpc = get_warmup_candles(cbd, "2026-07-17", 14)
        sess = {
            "symbol": "SPY", "date": "2026-07-17",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": candles[0]["time_ms"],
            "session_close_utc_ms": candles[-1]["time_ms"],
            "timeframe": "5m", "candles": candles,
        }
        if warmup:
            sess["warmup_candles"] = warmup
            sess["warmup_previous_close"] = wpc
        r = run_bdrr_strategy([sess], FROZEN_PRESET, BASE_CONFIG)[0]
        assert str(r["outcome"]) == "SESSION_CLOSE"
        # Exit must be on last RTH bar
        to = r.get("trade_outcome")
        if to and hasattr(to, "exit_bar_utc_ms") and to.exit_bar_utc_ms:
            dt = datetime.fromtimestamp(
                to.exit_bar_utc_ms / 1000, tz=timezone.utc
            ).astimezone(ET)
            assert dt.hour * 60 + dt.minute < 960, (
                f"Exit at {dt.strftime('%H:%M')} is after-hours"
            )

    def test_session_close_utc_ms_is_rth(self):
        """session_close_utc_ms must point to last RTH bar, not after-hours."""
        result = load_candles_for_timeframe(DATI, "SPY", 5)
        for date, candles in result["candles_by_date"].items():
            last_ms = candles[-1]["time_ms"]
            dt = datetime.fromtimestamp(
                last_ms / 1000, tz=timezone.utc
            ).astimezone(ET)
            assert dt.hour * 60 + dt.minute < 960, (
                f"{date}: last bar at {dt.strftime('%H:%M')}, must be RTH"
            )


# ── Futures path untouched ───────────────────────────────────────────────────


class TestFuturesUnchanged:
    """The futures path must not be affected by the equity filter."""

    def test_mes_uses_chicago_timezone(self):
        """MES data, if available, still uses America/Chicago boundaries."""
        staging = os.path.join(DATI, "staging", "MES")
        if not os.path.isdir(staging):
            pytest.skip("No MES staging data")
        # The equity filter is in load_candles_for_timeframe which checks
        # is_ibkr_equity.  MES is NOT an IBKR equity symbol, so the
        # strip_non_rth_bars call should never fire for it.
        from trading_lab.timeframe_aggregation import is_ibkr_equity
        assert not is_ibkr_equity("MES", DATI)
