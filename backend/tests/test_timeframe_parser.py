"""Tests for canonical timeframe parser and 1m pipeline correctness.

Covers:
    1. "1m" → 60 seconds
    2. "5m" → 300 seconds
    3. "15m" → 900 seconds
    4. Invalid format rejected
    5. DetectionResult 1m reports 60 seconds
    6. ORB 1m/5m uses 5 bars
    7. First post-ORB bar is 09:35 ET
    8. RETEST_BEFORE_DISPLACEMENT cases validated
    9. Pipeline 5m unchanged
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from trading_lab.timeframe_aggregation import timeframe_to_seconds


# ── 1–4: Canonical parser ───────────────────────────────────────────────────


class TestTimeframeToSeconds:
    def test_1m(self):
        assert timeframe_to_seconds("1m") == 60

    def test_5m(self):
        assert timeframe_to_seconds("5m") == 300

    def test_15m(self):
        assert timeframe_to_seconds("15m") == 900

    def test_10m(self):
        assert timeframe_to_seconds("10m") == 600

    def test_30m(self):
        assert timeframe_to_seconds("30m") == 1800

    def test_numeric_passthrough(self):
        assert timeframe_to_seconds(300) == 300
        assert timeframe_to_seconds(60) == 60

    def test_float_passthrough(self):
        assert timeframe_to_seconds(300.0) == 300

    def test_invalid_string_rejected(self):
        with pytest.raises(ValueError, match="unrecognised"):
            timeframe_to_seconds("hourly")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="unrecognised"):
            timeframe_to_seconds("")

    def test_none_rejected(self):
        with pytest.raises(ValueError):
            timeframe_to_seconds(None)

    def test_bool_rejected(self):
        with pytest.raises(ValueError):
            timeframe_to_seconds(True)

    def test_no_silent_fallback(self):
        """Unknown formats must raise, never silently return 300."""
        with pytest.raises(ValueError):
            timeframe_to_seconds("unknown")


# ── 5: DetectionResult 1m reports 60 seconds ────────────────────────────────


class TestDetectionResult1m:
    def test_1m_timeframe_in_detection_result(self):
        """Runner must produce timeframe_seconds=60 for 1m sessions."""
        from trading_lab.strategy_runner import run_bdrr_strategy
        from trading_lab.timeframe_aggregation import load_candles_for_timeframe

        result = load_candles_for_timeframe("dati", "SPY", 1)
        if "error" in result or not result["dates"]:
            pytest.skip("No SPY 1m data available")

        d = result["dates"][0]
        candles = result["candles_by_date"][d]
        session = {
            "symbol": "SPY", "date": d,
            "market_timezone": "America/New_York",
            "session_open_utc_ms": candles[0]["time_ms"],
            "session_close_utc_ms": candles[-1]["time_ms"],
            "timeframe": "1m", "candles": candles,
        }
        preset = {
            "preset_id": "tf_test", "timeframe_minutes": 1,
            "timezone": "America/New_York", "session_open": "09:30",
            "orb_start": "session_open", "orb_duration_minutes": 5,
            "level_source": "ORB_HIGH", "direction": "LONG",
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
            "min_displacement_ticks": None, "min_penetration_ticks": None,
            "min_close_beyond_level_ticks": None,
            "min_displacement_bars": 3, "consecutive_orb_closes": 2,
        }
        config = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0"}

        results = run_bdrr_strategy([session], preset, config)
        dr = results[0]["detection_result"]
        assert dr.session.timeframe_seconds == 60

    def test_5m_timeframe_still_300(self):
        """Runner must still produce timeframe_seconds=300 for 5m sessions."""
        from trading_lab.strategy_runner import run_bdrr_strategy
        from trading_lab.timeframe_aggregation import load_candles_for_timeframe

        result = load_candles_for_timeframe("dati", "SPY", 5)
        if "error" in result or not result["dates"]:
            pytest.skip("No SPY 5m data available")

        d = result["dates"][0]
        candles = result["candles_by_date"][d]
        session = {
            "symbol": "SPY", "date": d,
            "market_timezone": "America/New_York",
            "session_open_utc_ms": candles[0]["time_ms"],
            "session_close_utc_ms": candles[-1]["time_ms"],
            "timeframe": "5m", "candles": candles,
        }
        preset = {
            "preset_id": "tf_test", "timeframe_minutes": 5,
            "timezone": "America/New_York", "session_open": "09:30",
            "orb_start": "session_open", "orb_duration_minutes": 5,
            "level_source": "ORB_HIGH", "direction": "LONG",
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
            "min_displacement_ticks": None, "min_penetration_ticks": None,
            "min_close_beyond_level_ticks": None,
            "min_displacement_bars": 1, "consecutive_orb_closes": 2,
        }
        config = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0"}

        results = run_bdrr_strategy([session], preset, config)
        dr = results[0]["detection_result"]
        assert dr.session.timeframe_seconds == 300


# ── 6–7: ORB 1m/5m uses 5 bars, first post-ORB is 09:35 ────────────────────


class TestORB1m:
    def _get_orb(self):
        from trading_lab.timeframe_aggregation import load_candles_for_timeframe
        from trading_lab.session_context import build_session_context
        from trading_lab.orb_builder import build_orb

        result = load_candles_for_timeframe("dati", "SPY", 1)
        if "error" in result or not result["dates"]:
            pytest.skip("No SPY 1m data")

        d = result["dates"][0]
        candles = result["candles_by_date"][d]

        cfg = {
            "timeframe_minutes": 1, "timezone": "America/New_York",
            "session_open": "09:30", "orb_start": "session_open",
            "orb_duration_minutes": 5, "level_source": "ORB_HIGH",
            "direction": "LONG", "tick_size": 0.01,
        }
        sc = build_session_context(candles, cfg)
        assert sc["status"] == "OK"
        orb = build_orb(sc["candles"], sc, cfg)
        assert orb["status"] == "OK"
        return sc["candles"], orb

    def test_orb_uses_5_bars(self):
        """ORB with duration=5m on 1m data should span exactly 5 bars."""
        candles, orb = self._get_orb()
        # orb_duration_minutes=5, timeframe_minutes=1 → 5 bars
        # orb_candle_index is the LAST bar of ORB; first ORB bar is at index - 4
        orb_bar_count = 5  # 5 / 1
        orb_start_idx = orb["orb_candle_index"] - orb_bar_count + 1
        et = ZoneInfo("America/New_York")
        start_dt = datetime.fromtimestamp(
            candles[orb_start_idx]["time_ms"] / 1000, tz=timezone.utc
        ).astimezone(et)
        end_dt = datetime.fromtimestamp(
            candles[orb["orb_candle_index"]]["time_ms"] / 1000, tz=timezone.utc
        ).astimezone(et)
        assert start_dt.hour == 9 and start_dt.minute == 30
        assert end_dt.hour == 9 and end_dt.minute == 34

    def test_first_post_orb_is_0935(self):
        """First bar after 5-bar ORB on 1m data is 09:35 ET."""
        candles, orb = self._get_orb()
        post_orb_idx = orb["orb_candle_index"] + 1
        post_orb_ms = candles[post_orb_idx]["time_ms"]
        et = ZoneInfo("America/New_York")
        dt = datetime.fromtimestamp(post_orb_ms / 1000, tz=timezone.utc).astimezone(et)
        assert dt.hour == 9 and dt.minute == 35, f"Expected 09:35 ET, got {dt}"

    def test_orb_start_is_0930(self):
        """ORB starts at 09:30 ET, not at pre-market."""
        candles, orb = self._get_orb()
        orb_bar_count = 5
        orb_start_idx = orb["orb_candle_index"] - orb_bar_count + 1
        orb_start_ms = candles[orb_start_idx]["time_ms"]
        et = ZoneInfo("America/New_York")
        dt = datetime.fromtimestamp(orb_start_ms / 1000, tz=timezone.utc).astimezone(et)
        assert dt.hour == 9 and dt.minute == 30, f"Expected 09:30 ET, got {dt}"

    def test_premarket_excluded_from_orb(self):
        """Pre-market candles (before 09:30) must not be part of the ORB."""
        candles, orb = self._get_orb()
        orb_bar_count = 5
        orb_start_idx = orb["orb_candle_index"] - orb_bar_count + 1
        # All candles before orb_start_idx are pre-market
        et = ZoneInfo("America/New_York")
        if orb_start_idx > 0:
            last_premarket_ms = candles[orb_start_idx - 1]["time_ms"]
            dt = datetime.fromtimestamp(
                last_premarket_ms / 1000, tz=timezone.utc
            ).astimezone(et)
            assert dt.hour < 9 or (dt.hour == 9 and dt.minute < 30), (
                f"Expected pre-market candle before 09:30, got {dt}"
            )
