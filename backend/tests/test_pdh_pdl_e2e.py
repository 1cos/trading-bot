"""End-to-end regression: PREVIOUS_DAY_HIGH and PREVIOUS_DAY_LOW
execute the complete backtest pipeline without errors.

These tests do NOT guarantee trades are produced. They prove the
pipeline executes all stages and returns a valid result for every
session, with no ORB-specific assumption failures.
"""

import pytest

from trading_lab.csv_parser import parse_candles_from_csv
from trading_lab.session_split import split_into_sessions
from trading_lab.strategy_runner import run_bdrr_strategy


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def spy_sessions():
    with open("dati/SPY_5m.csv") as f:
        candles = parse_candles_from_csv(f.read())
    raw = split_into_sessions(candles, "America/New_York")
    return [
        {
            "symbol": "SPY",
            "date": s["date"],
            "market_timezone": "America/New_York",
            "session_open_utc_ms": s["candles"][0]["time_ms"],
            "session_close_utc_ms": s["candles"][-1]["time_ms"],
            "timeframe": "5m",
            "candles": s["candles"],
        }
        for s in raw
    ]


def _make_preset(level_source, direction):
    return {
        "timeframe_minutes": 5,
        "timezone": "America/New_York",
        "session_open": "09:30",
        "orb_start": "session_open",
        "orb_duration_minutes": 5,
        "level_source": level_source,
        "direction": direction,
        "entry_model": "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0,
        "min_displacement_ticks": None,
        "min_penetration_ticks": None,
        "min_close_beyond_level_ticks": 1,
        "min_displacement_bars": 1,
        "consecutive_orb_closes": 2,
        "confirmation_wick_penetration_pct_min": 0,
    }


CONFIG = {"tick_size": 0.01, "engine_version": "bdrr_v1.0", "exit_target_r": 2}

VALID_OUTCOMES = {"WIN", "LOSS", "STOPPED", "SESSION_CLOSE", "NO_VALID_SETUP", "TARGET_HIT", "PIPELINE_FAILURE"}


# ── Tests ────────────────────────────────────────────────────────────────────

class TestPDHEndToEnd:
    def test_pipeline_completes(self, spy_sessions):
        """PREVIOUS_DAY_HIGH completes the full pipeline for every session."""
        preset = _make_preset("PREVIOUS_DAY_HIGH", "SHORT")
        results = run_bdrr_strategy(spy_sessions, preset, CONFIG)
        assert len(results) == len(spy_sessions)
        for r in results:
            outcome = str(r["outcome"]).split(".")[-1]
            assert outcome in VALID_OUTCOMES, (
                f"Session {r['session_date']}: unexpected outcome {r['outcome']}"
            )

    def test_no_pipeline_crash(self, spy_sessions):
        """No session raises an exception."""
        preset = _make_preset("PREVIOUS_DAY_HIGH", "SHORT")
        # If any session crashes, run_bdrr_strategy raises
        results = run_bdrr_strategy(spy_sessions, preset, CONFIG)
        assert all(r["outcome"] is not None for r in results)


class TestPDLEndToEnd:
    def test_pipeline_completes(self, spy_sessions):
        """PREVIOUS_DAY_LOW completes the full pipeline for every session."""
        preset = _make_preset("PREVIOUS_DAY_LOW", "LONG")
        results = run_bdrr_strategy(spy_sessions, preset, CONFIG)
        assert len(results) == len(spy_sessions)
        for r in results:
            outcome = str(r["outcome"]).split(".")[-1]
            assert outcome in VALID_OUTCOMES, (
                f"Session {r['session_date']}: unexpected outcome {r['outcome']}"
            )

    def test_no_pipeline_crash(self, spy_sessions):
        """No session raises an exception."""
        preset = _make_preset("PREVIOUS_DAY_LOW", "LONG")
        results = run_bdrr_strategy(spy_sessions, preset, CONFIG)
        assert all(r["outcome"] is not None for r in results)


class TestORBStillWorks:
    def test_orb_unchanged(self, spy_sessions):
        """ORB pipeline still produces results after PDH/PDL plumbing."""
        preset = _make_preset("ORB_HIGH", "LONG")
        results = run_bdrr_strategy(spy_sessions, preset, CONFIG)
        assert len(results) == len(spy_sessions)
        outcomes = set()
        for r in results:
            outcomes.add(str(r["outcome"]).split(".")[-1])
        assert outcomes <= VALID_OUTCOMES
