"""Tests for PDH/PDL wired through the level dispatcher.

Covers:
  1. Dispatcher PREVIOUS_DAY_HIGH returns OK
  2. Dispatcher PREVIOUS_DAY_LOW returns OK
  3. level_price_ticks correct
  4. No previous session → FAILED
  5. Sequence validator NOT_APPLICABLE
  6. ORB pipeline unchanged
"""

import pytest

from trading_lab.level_provider import (
    build_level,
    validate_level_result,
    _is_pdh_pdl_source,
)
from trading_lab.sequence_validator import validate_sequence
from trading_lab.session_context import build_session_context
from trading_lab.tick_arithmetic import price_to_ticks


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_ms(date_str, time_str):
    from datetime import datetime
    return int(datetime.fromisoformat(
        f"{date_str}T{time_str}:00-04:00"
    ).timestamp() * 1000)


def _mc(date_str, time_str, o, h, l, c):
    return {"time_ms": _make_ms(date_str, time_str),
            "open": o, "high": h, "low": l, "close": c}


# Two-day fixture: Monday + Tuesday
MONDAY = "2025-06-09"
TUESDAY = "2025-06-10"

MONDAY_CANDLES = [
    _mc(MONDAY, "09:30", 100.0, 105.0, 98.0, 102.0),  # ORB
    _mc(MONDAY, "09:35", 102.0, 106.0, 101.0, 104.0),
    _mc(MONDAY, "09:40", 104.0, 107.0, 103.0, 106.0),  # session high 107
    _mc(MONDAY, "09:45", 106.0, 106.5, 97.0, 99.0),    # session low 97
]

TUESDAY_CANDLES = [
    _mc(TUESDAY, "09:30", 103.0, 108.0, 102.0, 106.0),  # ORB
    _mc(TUESDAY, "09:35", 106.0, 109.0, 105.0, 108.0),
    _mc(TUESDAY, "09:40", 108.0, 110.0, 107.0, 109.0),
    _mc(TUESDAY, "09:45", 109.0, 109.5, 104.0, 105.0),
]

ALL_SESSIONS = [
    {"date": MONDAY, "candles": MONDAY_CANDLES},
    {"date": TUESDAY, "candles": TUESDAY_CANDLES},
]

BASE_CONFIG = {
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "PREVIOUS_DAY_HIGH",
    "direction": "LONG",
    "tick_size": 0.01,
}


def _build_tuesday(level_source, direction="LONG"):
    config = {**BASE_CONFIG, "level_source": level_source, "direction": direction}
    sc = build_session_context(TUESDAY_CANDLES, config)
    assert sc["status"] == "OK"
    result = build_level(
        sc["candles"], sc, config,
        all_sessions=ALL_SESSIONS,
    )
    return result, config


# ── 1. Dispatcher PREVIOUS_DAY_HIGH ─────────────────────────────────────────

class TestDispatcherPDH:
    def test_returns_ok(self):
        result, _ = _build_tuesday("PREVIOUS_DAY_HIGH")
        assert result["status"] == "OK"

    def test_level_source_correct(self):
        result, _ = _build_tuesday("PREVIOUS_DAY_HIGH")
        assert result["level_source"] == "PREVIOUS_DAY_HIGH"

    def test_level_price_is_monday_high(self):
        result, _ = _build_tuesday("PREVIOUS_DAY_HIGH")
        # Monday's max high = 107.0
        assert result["level_price"] == 107.0

    def test_passes_contract_validation(self):
        result, _ = _build_tuesday("PREVIOUS_DAY_HIGH")
        valid, reason = validate_level_result(result)
        assert valid, reason

    def test_provider_data_has_both(self):
        result, _ = _build_tuesday("PREVIOUS_DAY_HIGH")
        pd = result["provider_data"]
        assert pd["pdh"] == 107.0
        assert pd["pdl"] == 97.0
        assert pd["prev_date"] == MONDAY


# ── 2. Dispatcher PREVIOUS_DAY_LOW ──────────────────────────────────────────

class TestDispatcherPDL:
    def test_returns_ok(self):
        result, _ = _build_tuesday("PREVIOUS_DAY_LOW", direction="SHORT")
        assert result["status"] == "OK"

    def test_level_price_is_monday_low(self):
        result, _ = _build_tuesday("PREVIOUS_DAY_LOW", direction="SHORT")
        # Monday's min low = 97.0
        assert result["level_price"] == 97.0

    def test_level_source_correct(self):
        result, _ = _build_tuesday("PREVIOUS_DAY_LOW", direction="SHORT")
        assert result["level_source"] == "PREVIOUS_DAY_LOW"


# ── 3. Tick price correct ───────────────────────────────────────────────────

class TestTickPrice:
    def test_pdh_ticks(self):
        result, _ = _build_tuesday("PREVIOUS_DAY_HIGH")
        expected = price_to_ticks(107.0, 0.01)
        assert result["level_price_ticks"] == expected

    def test_pdl_ticks(self):
        result, _ = _build_tuesday("PREVIOUS_DAY_LOW", direction="SHORT")
        expected = price_to_ticks(97.0, 0.01)
        assert result["level_price_ticks"] == expected


# ── 4. No previous session ──────────────────────────────────────────────────

class TestNoPreviousSession:
    def test_first_day_fails(self):
        """Monday has no prior session → FAILED."""
        config = {**BASE_CONFIG, "level_source": "PREVIOUS_DAY_HIGH"}
        sc = build_session_context(MONDAY_CANDLES, config)
        single = [{"date": MONDAY, "candles": MONDAY_CANDLES}]
        result = build_level(
            sc["candles"], sc, config,
            all_sessions=single,
        )
        assert result["status"] == "FAILED"
        assert result["failed_stage"] == "NO_PREVIOUS_SESSION"

    def test_missing_all_sessions_fails(self):
        """all_sessions=None → FAILED."""
        config = {**BASE_CONFIG, "level_source": "PREVIOUS_DAY_HIGH"}
        sc = build_session_context(TUESDAY_CANDLES, config)
        result = build_level(sc["candles"], sc, config)
        assert result["status"] == "FAILED"
        assert result["failed_stage"] == "MISSING_SESSIONS_DATA"


# ── 5. Sequence validator for PDH/PDL ────────────────────────────────────────

class TestSequenceValidator:
    def test_pdh_validated(self):
        """PDH is now validated with line-level invalidation, not NOT_APPLICABLE."""
        config = {**BASE_CONFIG, "level_source": "PREVIOUS_DAY_HIGH",
                  "direction": "LONG", "level_invalidation_closes": 2}
        result = validate_sequence(
            TUESDAY_CANDLES,
            {"status": "OK", "level_price": 103.0},
            {"status": "OK"},
            {"status": "OK", "first_retest_contact_index": 2},
            config,
        )
        # With real candles, result is OK or INVALIDATED — no longer NOT_APPLICABLE
        assert result["status"] in ("OK", "INVALIDATED")
        assert result.get("level_source") == "PREVIOUS_DAY_HIGH"

    def test_pdl_validated(self):
        """PDL is now validated with line-level invalidation, not NOT_APPLICABLE."""
        config = {**BASE_CONFIG, "level_source": "PREVIOUS_DAY_LOW",
                  "direction": "SHORT", "level_invalidation_closes": 2}
        result = validate_sequence(
            TUESDAY_CANDLES,
            {"status": "OK", "level_price": 97.0},
            {"status": "OK"},
            {"status": "OK", "first_retest_contact_index": 2},
            config,
        )
        assert result["status"] in ("OK", "INVALIDATED")
        assert result.get("level_source") == "PREVIOUS_DAY_LOW"


# ── 6. ORB unchanged ────────────────────────────────────────────────────────

class TestORBUnchanged:
    def test_orb_still_works_without_all_sessions(self):
        config = {**BASE_CONFIG, "level_source": "ORB_HIGH", "direction": "LONG"}
        sc = build_session_context(TUESDAY_CANDLES, config)
        result = build_level(sc["candles"], sc, config)
        assert result["status"] == "OK"
        assert result["level_source"] == "ORB_HIGH"
        assert result["level_price"] == 108.0  # Tuesday ORB high

    def test_orb_with_all_sessions_also_works(self):
        config = {**BASE_CONFIG, "level_source": "ORB_HIGH", "direction": "LONG"}
        sc = build_session_context(TUESDAY_CANDLES, config)
        result = build_level(
            sc["candles"], sc, config,
            all_sessions=ALL_SESSIONS,
        )
        assert result["status"] == "OK"
        assert result["level_price"] == 108.0


# ── Scan-from-index ─────────────────────────────────────────────────────────

class TestScanFromIndex:
    def test_scan_from_index_is_post_orb(self):
        """PDH/PDL scan_from_index must match ORB's orb_candle_index."""
        result, _ = _build_tuesday("PREVIOUS_DAY_HIGH")
        # With 5min ORB and 5min candles, ORB is candle 0
        assert result["scan_from_index"] == 0
        assert result["orb_candle_index"] == result["scan_from_index"]
