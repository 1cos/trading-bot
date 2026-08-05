"""Tests for the PDH/PDL level provider.

Covers:
  1. PDH correct (max high of previous session)
  2. PDL correct (min low of previous session)
  3. No previous session → NO_PREVIOUS_SESSION
  4. Weekend / missing day → uses last available session
  5. No look-ahead (future sessions ignored)
  6. ORB pipeline unchanged (regression)
"""

import pytest

from trading_lab.pdh_pdl_provider import compute_pdh_pdl


# ── Helpers ──────────────────────────────────────────────────────────────────

def _candle(high, low, time_ms=0):
    return {"time_ms": time_ms, "open": low, "high": high, "low": low, "close": high}


def _session(date, candles):
    return {"date": date, "candles": candles}


# ── Fixtures ─────────────────────────────────────────────────────────────────

MONDAY = "2025-06-09"
TUESDAY = "2025-06-10"
WEDNESDAY = "2025-06-11"
FRIDAY = "2025-06-13"
NEXT_MONDAY = "2025-06-16"

SESSIONS = [
    _session(MONDAY, [
        _candle(102.0, 98.0),
        _candle(105.0, 99.0),   # highest high
        _candle(103.0, 97.0),   # lowest low
    ]),
    _session(TUESDAY, [
        _candle(110.0, 104.0),
        _candle(112.0, 106.0),
    ]),
    _session(WEDNESDAY, [
        _candle(108.0, 101.0),
    ]),
    _session(FRIDAY, [
        _candle(115.0, 109.0),
        _candle(113.0, 107.0),
    ]),
    _session(NEXT_MONDAY, [
        _candle(120.0, 114.0),
    ]),
]


# ── 1. PDH correct ──────────────────────────────────────────────────────────

class TestPDHCorrect:
    def test_pdh_is_max_high_of_previous_session(self):
        result = compute_pdh_pdl(TUESDAY, SESSIONS)
        assert result["status"] == "OK"
        # Monday's max high = 105.0
        assert result["pdh"] == 105.0

    def test_pdh_wednesday_uses_tuesday(self):
        result = compute_pdh_pdl(WEDNESDAY, SESSIONS)
        assert result["status"] == "OK"
        assert result["pdh"] == 112.0  # Tuesday's max high

    def test_prev_date_reported(self):
        result = compute_pdh_pdl(TUESDAY, SESSIONS)
        assert result["prev_date"] == MONDAY


# ── 2. PDL correct ──────────────────────────────────────────────────────────

class TestPDLCorrect:
    def test_pdl_is_min_low_of_previous_session(self):
        result = compute_pdh_pdl(TUESDAY, SESSIONS)
        assert result["status"] == "OK"
        # Monday's min low = 97.0
        assert result["pdl"] == 97.0

    def test_pdl_wednesday_uses_tuesday(self):
        result = compute_pdh_pdl(WEDNESDAY, SESSIONS)
        assert result["status"] == "OK"
        assert result["pdl"] == 104.0  # Tuesday's min low


# ── 3. No previous session ──────────────────────────────────────────────────

class TestNoPreviousSession:
    def test_first_session_has_no_previous(self):
        result = compute_pdh_pdl(MONDAY, SESSIONS)
        assert result["status"] == "NO_PREVIOUS_SESSION"
        assert "No session" in result["reason"]

    def test_single_session_list(self):
        single = [_session("2025-06-10", [_candle(100, 95)])]
        result = compute_pdh_pdl("2025-06-10", single)
        assert result["status"] == "NO_PREVIOUS_SESSION"

    def test_empty_sessions_list(self):
        result = compute_pdh_pdl("2025-06-10", [])
        assert result["status"] == "NO_PREVIOUS_SESSION"

    def test_previous_session_with_empty_candles(self):
        """A prior date with zero candles is not a valid previous session."""
        sessions = [
            _session("2025-06-09", []),  # exists but no candles
            _session("2025-06-10", [_candle(100, 95)]),
        ]
        result = compute_pdh_pdl("2025-06-10", sessions)
        assert result["status"] == "NO_PREVIOUS_SESSION"


# ── 4. Weekend / missing day ────────────────────────────────────────────────

class TestWeekendAndGaps:
    def test_monday_uses_friday(self):
        """Weekend gap: Monday's previous session is Friday."""
        result = compute_pdh_pdl(NEXT_MONDAY, SESSIONS)
        assert result["status"] == "OK"
        assert result["prev_date"] == FRIDAY
        assert result["pdh"] == 115.0  # Friday's max high
        assert result["pdl"] == 107.0  # Friday's min low

    def test_gap_skips_missing_date(self):
        """Thursday is missing (no session). Friday uses Wednesday."""
        # SESSIONS has no Thursday — Friday follows Wednesday
        result = compute_pdh_pdl(FRIDAY, SESSIONS)
        assert result["status"] == "OK"
        assert result["prev_date"] == WEDNESDAY
        assert result["pdh"] == 108.0
        assert result["pdl"] == 101.0


# ── 5. No look-ahead ────────────────────────────────────────────────────────

class TestNoLookAhead:
    def test_future_sessions_not_used(self):
        """PDH/PDL for Tuesday must not see Wednesday+ data."""
        result = compute_pdh_pdl(TUESDAY, SESSIONS)
        assert result["prev_date"] == MONDAY
        # Must be Monday's values, not Wednesday's or later
        assert result["pdh"] == 105.0
        assert result["pdl"] == 97.0

    def test_same_date_not_used_as_previous(self):
        """The current session's own candles must not be used."""
        sessions = [
            _session("2025-06-10", [_candle(200, 50)]),
        ]
        result = compute_pdh_pdl("2025-06-10", sessions)
        assert result["status"] == "NO_PREVIOUS_SESSION"

    def test_only_strictly_before(self):
        """Only sessions with date < current_date are candidates."""
        sessions = [
            _session("2025-06-09", [_candle(100, 90)]),
            _session("2025-06-10", [_candle(200, 50)]),
            _session("2025-06-11", [_candle(300, 10)]),
        ]
        result = compute_pdh_pdl("2025-06-10", sessions)
        assert result["pdh"] == 100.0  # June 9, not June 10 or 11
        assert result["pdl"] == 90.0


# ── 6. ORB regression ───────────────────────────────────────────────────────

class TestORBUnchanged:
    def test_orb_pipeline_unaffected(self):
        """PDH/PDL provider is standalone — ORB pipeline must still work."""
        from trading_lab.level_provider import build_level
        from trading_lab.session_context import build_session_context

        def _mc(date_str, time_str, o, h, l, c):
            from datetime import datetime
            iso = f"{date_str}T{time_str}:00-04:00"
            dt = datetime.fromisoformat(iso)
            return {"time_ms": int(dt.timestamp() * 1000),
                    "open": o, "high": h, "low": l, "close": c}

        candles = [
            _mc("2025-06-10", "09:30", 100, 102, 99, 101),
            _mc("2025-06-10", "09:35", 101, 103, 100, 102),
        ]
        config = {
            "timeframe_minutes": 5,
            "timezone": "America/New_York",
            "session_open": "09:30",
            "orb_start": "session_open",
            "orb_duration_minutes": 5,
            "level_source": "ORB_HIGH",
            "direction": "LONG",
            "tick_size": 0.01,
        }
        sc = build_session_context(candles, config)
        result = build_level(sc["candles"], sc, config)
        assert result["status"] == "OK"
        assert result["level_source"] == "ORB_HIGH"
        assert result["level_price"] == 102.0
