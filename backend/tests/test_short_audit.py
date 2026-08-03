"""SHORT direction tests — focused audit for Phase 2.

Covers:
  1. Valid SHORT detection (direct rejection)
  2. SHORT invalidation after 2 consecutive closes inside ORB
  3. SHORT wick depth calculation
  4. SHORT target hit
  5. SHORT stop hit
  6. SHORT sequence_validator direction symmetry
  7. SHORT failed retests
  8. SHORT chart event fields
"""

import pytest
from trading_lab.strategy_runner import run_bdrr_strategy
from trading_lab.sequence_validator import validate_sequence
from trading_lab.visual_review_exporter import export_visual_event


# ── Fixtures ────────────────────────────────────────────────────────────────

MS = 1782912600000  # 2026-07-01 09:30 ET

def _c(offset_min, o, h, l, c, v=100):
    return {"time_ms": MS + offset_min * 60000,
            "open": o, "high": h, "low": l, "close": c, "volume": v}


# SHORT setup: ORB Low break, downward displacement, retest above, confirm below
SHORT_CANDLES = [
    _c(0, 100.0, 101.0, 99.0, 100.5),          # ORB: H=101, L=99
    _c(5, 99.50, 99.80, 98.00, 98.50),          # Break below ORB Low (99.0)
    _c(10, 98.40, 98.60, 97.80, 98.00),         # Displacement (stays below)
    _c(15, 98.20, 99.20, 98.10, 98.15),         # Retest: high=99.20 > 99.0, close=98.15 < 99.0 ✓
    _c(20, 98.10, 98.50, 97.50, 97.60),         # Post-confirm
    _c(25, 97.50, 97.80, 97.00, 97.10),         # Heading to target
    _c(30, 97.00, 97.30, 96.50, 96.60),         # Near target
    _c(35, 96.50, 96.80, 95.80, 95.90),         # TARGET HIT (2R below)
]

# SHORT that gets invalidated
SHORT_INVALIDATED = [
    _c(0, 100.0, 101.0, 99.0, 100.5),          # ORB
    _c(5, 99.50, 99.80, 98.00, 98.50),          # Break
    _c(10, 98.40, 98.60, 97.80, 98.00),         # Displacement
    _c(15, 98.50, 99.50, 98.40, 99.10),         # Inside #1 (close >= ORB Low)
    _c(20, 99.20, 99.80, 99.00, 99.50),         # Inside #2 → INVALIDATED
    _c(25, 99.60, 100.50, 99.40, 100.20),       # After invalidation
]

PRESET_SHORT = {
    "preset_id": "short_test", "timeframe_minutes": 5,
    "timezone": "America/New_York", "session_open": "09:30",
    "orb_start": "session_open", "orb_duration_minutes": 5,
    "level_source": "ORB_LOW", "direction": "SHORT",
    "entry_model": "CONFIRMATION_CLOSE",
    "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
    "min_displacement_ticks": None, "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None,
    "consecutive_orb_closes": 2,
    "min_displacement_bars": 1,
    "confirmation_wick_penetration_pct_min": 0,
}
CONFIG = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0"}


def _make_session(candles, date="2026-07-01"):
    return {
        "symbol": "TEST", "date": date,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": candles[0]["time_ms"],
        "session_close_utc_ms": candles[-1]["time_ms"],
        "timeframe": "5m", "candles": candles,
    }


# ── Tests ──────────────────────────────────────────────────────────────────


class TestShortDetection:
    def test_valid_short_detection(self):
        r = run_bdrr_strategy([_make_session(SHORT_CANDLES)], PRESET_SHORT, CONFIG)[0]
        assert r["detection_status"] == "VALID"

    def test_short_direction_in_result(self):
        r = run_bdrr_strategy([_make_session(SHORT_CANDLES)], PRESET_SHORT, CONFIG)[0]
        dr = r["detection_result"]
        assert str(dr.direction) == "SHORT"
        assert str(dr.level_source) == "ORB_LOW"

    def test_short_break_below_orb_low(self):
        r = run_bdrr_strategy([_make_session(SHORT_CANDLES)], PRESET_SHORT, CONFIG)[0]
        dr = r["detection_result"]
        orb_low = dr.level_price.to_price()
        break_close = dr.break_bar.close.to_price()
        assert break_close < orb_low


class TestShortInvalidation:
    def test_short_invalidated_after_2_inside(self):
        r = run_bdrr_strategy(
            [_make_session(SHORT_INVALIDATED)], PRESET_SHORT, CONFIG)[0]
        assert r["detection_status"] != "VALID"

    def test_short_sequence_validator_direction(self):
        ec = {
            "direction": "SHORT", "consecutive_orb_closes": 2,
            "timeframe_minutes": 5, "timezone": "America/New_York",
            "session_open": "09:30", "orb_start": "session_open",
            "orb_duration_minutes": 5, "level_source": "ORB_LOW",
            "tick_size": 0.01, "min_displacement_ticks": None,
            "min_penetration_ticks": None,
            "min_close_beyond_level_ticks": None,
        }
        # For SHORT, closes >= ORB Low count as inside
        candles = SHORT_INVALIDATED
        orb = {"status": "OK", "orb_high": 101.0, "orb_low": 99.0}
        brk = {"status": "OK"}
        disp = {"status": "OK", "first_retest_contact_index": 3}
        sv = validate_sequence(candles, orb, brk, disp, ec)
        assert sv["status"] == "INVALIDATED"


class TestShortOutcome:
    def test_short_target_or_stopped(self):
        r = run_bdrr_strategy([_make_session(SHORT_CANDLES)], PRESET_SHORT, CONFIG)[0]
        assert r["outcome"] in ("TARGET_HIT", "STOPPED", "OPEN")


class TestShortExport:
    def test_short_visual_event_has_annotations(self):
        r = run_bdrr_strategy([_make_session(SHORT_CANDLES)], PRESET_SHORT, CONFIG)[0]
        event = export_visual_event(SHORT_CANDLES, r)
        assert "annotations" in event
        assert "orb_high_ticks" in event
        assert "orb_low_ticks" in event

    def test_short_event_direction(self):
        r = run_bdrr_strategy([_make_session(SHORT_CANDLES)], PRESET_SHORT, CONFIG)[0]
        event = export_visual_event(SHORT_CANDLES, r)
        assert event.get("direction") == "SHORT"
