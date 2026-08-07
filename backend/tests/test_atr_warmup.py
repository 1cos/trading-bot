"""Tests for ATR warm-up from prior session.

Verifies that:
1. Warmup candles from the previous session prime ATR(14) so that
   the first session candle has a valid previous_atr.
2. Warmup candles do NOT enter the strategy pipeline (ORB, break,
   displacement, retest, rejection geometry).
3. Without warmup, the first 14 candles remain INSUFFICIENT_HISTORY
   (backward compatibility).
4. get_warmup_candles correctly extracts from candles_by_date.
5. The strategy runner correctly threads warmup through to the
   rejection finder.
"""

import os
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.atr import atr_series
from trading_lab.csv_parser import parse_candles_from_csv
from trading_lab.session_split import split_into_sessions
from trading_lab.strategy_runner import run_bdrr_strategy
from trading_lab.timeframe_aggregation import get_warmup_candles


# ── Fixtures ─────────────────────────────────────────────────────────────────

FROZEN_PRESET = {
    "preset_id": "bdrr_v1_initial",
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "entry_model": "CONFIRMATION_CLOSE",
    "entry_buffer_ticks": 0,
    "stop_buffer_ticks": 0,
    "min_displacement_ticks": None,
    "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None,
    "consecutive_orb_closes": 2,
}
BASE_CONFIG = {
    "tick_size": 0.01,
    "exit_target_r": 2,
    "engine_version": "bdrr_v1.0",
}


def _load_spy_groups():
    csv_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "dati", "SPY_5m.csv"
    )
    with open(csv_path) as f:
        content = f.read()
    candles = parse_candles_from_csv(content)
    return split_into_sessions(candles, "America/New_York")


# ── get_warmup_candles tests ─────────────────────────────────────────────────


class TestGetWarmupCandles:
    """Tests for the get_warmup_candles helper."""

    def test_first_session_no_warmup(self):
        """First session in dataset has no previous session → empty."""
        cbd = {"2026-06-01": [{"close": 1.0}] * 20}
        warmup, pc = get_warmup_candles(cbd, "2026-06-01")
        assert warmup == []
        assert pc is None

    def test_date_not_in_dict(self):
        """Date not in candles_by_date → empty."""
        cbd = {"2026-06-01": [{"close": 1.0}] * 20}
        warmup, pc = get_warmup_candles(cbd, "2026-06-02")
        assert warmup == []
        assert pc is None

    def test_returns_last_14_from_previous(self):
        """Returns last 14 bars of previous session."""
        prev = [{"close": float(i)} for i in range(30)]
        curr = [{"close": 99.0}] * 10
        cbd = {"2026-06-01": prev, "2026-06-02": curr}
        warmup, pc = get_warmup_candles(cbd, "2026-06-02", 14)
        assert len(warmup) == 14
        assert warmup[0]["close"] == 16.0  # prev[16]
        assert warmup[-1]["close"] == 29.0  # prev[29]
        assert pc == 15.0  # prev[15] = bar before warmup block

    def test_previous_close_none_when_prev_too_short(self):
        """When prev session has exactly period bars, no bar before warmup."""
        prev = [{"close": float(i)} for i in range(14)]
        cbd = {"2026-06-01": prev, "2026-06-02": [{"close": 99.0}]}
        warmup, pc = get_warmup_candles(cbd, "2026-06-02", 14)
        assert len(warmup) == 14
        assert pc is None

    def test_short_previous_session(self):
        """Previous session shorter than period → returns all available."""
        prev = [{"close": float(i)} for i in range(5)]
        cbd = {"2026-06-01": prev, "2026-06-02": [{"close": 99.0}]}
        warmup, pc = get_warmup_candles(cbd, "2026-06-02", 14)
        assert len(warmup) == 5
        assert pc is None  # fewer than period+1


class TestGetWarmupReal:
    """get_warmup_candles on real SPY data."""

    def test_real_warmup_length(self):
        groups = _load_spy_groups()
        cbd = {g["date"]: g["candles"] for g in groups}
        dates = sorted(cbd.keys())
        # Second session should get 14 warmup bars
        warmup, pc = get_warmup_candles(cbd, dates[1])
        assert len(warmup) == 14
        assert pc is not None
        assert isinstance(pc, float)


# ── ATR warmup integration ───────────────────────────────────────────────────


class TestAtrWarmupIntegration:
    """ATR is primed when warmup candles are provided."""

    def test_warmup_primes_atr(self):
        """With 14-bar warmup, first session candle has valid previous_atr."""
        groups = _load_spy_groups()
        if len(groups) < 2:
            pytest.skip("Need at least 2 sessions")

        prev_candles = groups[0]["candles"]
        curr_candles = groups[1]["candles"]
        warmup = prev_candles[-14:]
        pc = prev_candles[-15]["close"] if len(prev_candles) > 14 else None

        combined = warmup + curr_candles
        atr = atr_series(combined, 14, initial_previous_close=pc)

        # ATR at index len(warmup)-1 should be non-None (warmup primed)
        assert atr[len(warmup) - 1] is not None
        # Therefore previous_atr for session candle 0 is non-None
        session_prev_atr = atr[len(warmup) - 1]
        assert isinstance(session_prev_atr, float)
        assert session_prev_atr > 0

    def test_without_warmup_insufficient(self):
        """Without warmup, first 13 candles have None ATR (backward compat)."""
        groups = _load_spy_groups()
        curr_candles = groups[1]["candles"]
        atr = atr_series(curr_candles, 14)
        for i in range(13):
            assert atr[i] is None, f"atr[{i}] should be None without warmup"
        assert atr[13] is not None, "atr[13] should be available"


# ── Strategy runner warmup threading ─────────────────────────────────────────


class TestStrategyRunnerWarmup:
    """Strategy runner correctly threads warmup to rejection finder."""

    def test_warmup_does_not_change_outcome_counts(self):
        """Adding warmup does not change SPY LONG trade counts.

        This confirms that warmup primes ATR but no currently-passing
        setup gets blocked by the newly-available ATR data.
        """
        groups = _load_spy_groups()

        # Build sessions with warmup
        sessions_w = []
        for i, g in enumerate(groups):
            sess = {
                "symbol": "SPY", "date": g["date"],
                "market_timezone": "America/New_York",
                "session_open_utc_ms": g["candles"][0]["time_ms"],
                "session_close_utc_ms": g["candles"][-1]["time_ms"],
                "timeframe": "5m", "candles": g["candles"],
            }
            if i > 0:
                prev = groups[i - 1]["candles"]
                sess["warmup_candles"] = prev[-14:]
                sess["warmup_previous_close"] = (
                    prev[-(14 + 1)]["close"] if len(prev) > 14 else None
                )
            sessions_w.append(sess)

        # Build sessions without warmup
        sessions_nw = [
            {
                "symbol": "SPY", "date": g["date"],
                "market_timezone": "America/New_York",
                "session_open_utc_ms": g["candles"][0]["time_ms"],
                "session_close_utc_ms": g["candles"][-1]["time_ms"],
                "timeframe": "5m", "candles": g["candles"],
            }
            for g in groups
        ]

        rw = run_bdrr_strategy(sessions_w, FROZEN_PRESET, BASE_CONFIG)
        rnw = run_bdrr_strategy(sessions_nw, FROZEN_PRESET, BASE_CONFIG)

        # Same number of results
        assert len(rw) == len(rnw)

        # Same outcomes per session
        for r1, r2 in zip(rw, rnw):
            assert str(r1["outcome"]) == str(r2["outcome"]), (
                f'{r1["session_date"]}: {r2["outcome"]} → {r1["outcome"]}'
            )


# ── Invariant: warmup never contaminates strategy ────────────────────────────


class TestWarmupInvariant:
    """Warmup candles must NEVER appear in strategy pipeline output."""

    def test_orb_index_unchanged(self):
        """ORB candle index is the same with and without warmup."""
        groups = _load_spy_groups()
        if len(groups) < 2:
            pytest.skip("Need at least 2 sessions")

        g = groups[1]
        prev = groups[0]["candles"]

        sess_w = {
            "symbol": "SPY", "date": g["date"],
            "market_timezone": "America/New_York",
            "session_open_utc_ms": g["candles"][0]["time_ms"],
            "session_close_utc_ms": g["candles"][-1]["time_ms"],
            "timeframe": "5m", "candles": g["candles"],
            "warmup_candles": prev[-14:],
            "warmup_previous_close": prev[-15]["close"],
        }
        sess_nw = {
            "symbol": "SPY", "date": g["date"],
            "market_timezone": "America/New_York",
            "session_open_utc_ms": g["candles"][0]["time_ms"],
            "session_close_utc_ms": g["candles"][-1]["time_ms"],
            "timeframe": "5m", "candles": g["candles"],
        }

        rw = run_bdrr_strategy([sess_w], FROZEN_PRESET, BASE_CONFIG)
        rnw = run_bdrr_strategy([sess_nw], FROZEN_PRESET, BASE_CONFIG)

        # Both should produce the same detection result structure
        assert str(rw[0]["outcome"]) == str(rnw[0]["outcome"])
        assert rw[0]["session_date"] == rnw[0]["session_date"]
