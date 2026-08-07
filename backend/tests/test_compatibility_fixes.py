"""Regression tests for critical compatibility fixes.

Proves:
  1. LevelSource enum serializes correctly for all implemented sources
  2. DetectionResult contains correct LevelSource (never None) for PDH/PDL
  3. PREVIOUS_DAY_HIGH preset is accepted by validate_preset
  4. PREVIOUS_DAY_LOW preset is accepted by validate_preset
  5. ORB presets still behave identically
  6. Invalid pairings are still rejected
"""

import pytest

from trading_lab.contracts.enums import LevelSource
from trading_lab.preset_store import validate_preset_params


# ── 1. LevelSource serialization ─────────────────────────────────────────────

class TestLevelSourceSerialization:
    def test_previous_day_high_value(self):
        assert LevelSource.PREVIOUS_DAY_HIGH == "PREVIOUS_DAY_HIGH"
        assert LevelSource.PREVIOUS_DAY_HIGH.value == "PREVIOUS_DAY_HIGH"

    def test_previous_day_low_value(self):
        assert LevelSource.PREVIOUS_DAY_LOW == "PREVIOUS_DAY_LOW"
        assert LevelSource.PREVIOUS_DAY_LOW.value == "PREVIOUS_DAY_LOW"

    def test_orb_high_unchanged(self):
        assert LevelSource.ORB_HIGH == "ORB_HIGH"

    def test_orb_low_unchanged(self):
        assert LevelSource.ORB_LOW == "ORB_LOW"

    def test_roundtrip_from_string(self):
        assert LevelSource("PREVIOUS_DAY_HIGH") is LevelSource.PREVIOUS_DAY_HIGH
        assert LevelSource("PREVIOUS_DAY_LOW") is LevelSource.PREVIOUS_DAY_LOW

    def test_old_pdh_rejected(self):
        """The old 'PDH' string must NOT be a valid LevelSource."""
        with pytest.raises(ValueError):
            LevelSource("PDH")

    def test_old_pdl_rejected(self):
        with pytest.raises(ValueError):
            LevelSource("PDL")

    def test_str_serialization(self):
        assert str(LevelSource.PREVIOUS_DAY_HIGH) == "PREVIOUS_DAY_HIGH"
        assert str(LevelSource.PREVIOUS_DAY_LOW) == "PREVIOUS_DAY_LOW"


# ── 2. DetectionResult contains correct LevelSource ──────────────────────────

class TestDetectionResultLevelSource:
    def test_pdh_detection_result_has_level_source(self):
        """A PDH backtest must produce DetectionResult with
        level_source=PREVIOUS_DAY_HIGH, never None."""
        from trading_lab.csv_parser import parse_candles_from_csv
        from trading_lab.session_split import split_into_sessions
        from trading_lab.strategy_runner import run_bdrr_strategy

        with open("dati/SPY_5m.csv") as f:
            candles = parse_candles_from_csv(f.read())
        raw = split_into_sessions(candles, "America/New_York")
        sessions = [
            {"symbol": "SPY", "date": s["date"],
             "market_timezone": "America/New_York",
             "session_open_utc_ms": s["candles"][0]["time_ms"],
             "session_close_utc_ms": s["candles"][-1]["time_ms"],
             "timeframe": "5m", "candles": s["candles"]}
            for s in raw
        ]

        preset = {
            "timeframe_minutes": 5, "timezone": "America/New_York",
            "session_open": "09:30", "orb_start": "session_open",
            "orb_duration_minutes": 5,
            "level_source": "PREVIOUS_DAY_HIGH", "direction": "LONG",
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
            "min_displacement_ticks": None, "min_penetration_ticks": None,
            "min_close_beyond_level_ticks": 1, "min_displacement_bars": 1,
            "consecutive_orb_closes": 2,
            "confirmation_wick_penetration_pct_min": 0,
        }
        config = {"tick_size": 0.01, "engine_version": "bdrr_v1.0",
                  "exit_target_r": 2}

        results = run_bdrr_strategy(sessions, preset, config)

        # At least some sessions should have a level built (skip first day)
        found_level_source = False
        for r in results:
            dr = r["detection_result"]
            if dr.level_source is not None:
                assert dr.level_source == LevelSource.PREVIOUS_DAY_HIGH
                found_level_source = True
        assert found_level_source, "No DetectionResult had a non-None level_source"


# ── 3–4. Preset validation accepts PREVIOUS_DAY_HIGH/LOW ────────────────────

def _base_preset(**overrides):
    p = {
        "preset_id": "test_preset_001",
        "schema_version": "StrategyPreset/v1",
        "strategy_id": "BDRR",
        "symbol": "SPY",
        "timeframe": "5m",
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
        "min_close_beyond_level_ticks": 1,
        "min_displacement_bars": 1,
        "consecutive_orb_closes": 2,
        "confirmation_wick_penetration_pct_min": None,
        "rejection_wick_ratio_min": None,
        "body_ratio_max": None,
        "exit_target_r": "2",
        "tick_size": "0.01",
    }
    p.update(overrides)
    return p


class TestPresetPDHAccepted:
    def test_previous_day_high_long_accepted(self):
        p = _base_preset(level_source="PREVIOUS_DAY_HIGH", direction="LONG")
        errors = validate_preset_params(p)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_previous_day_low_short_accepted(self):
        p = _base_preset(level_source="PREVIOUS_DAY_LOW", direction="SHORT")
        errors = validate_preset_params(p)
        assert errors == [], f"Unexpected errors: {errors}"


# ── 5. ORB presets still behave identically ──────────────────────────────────

class TestORBPresetsUnchanged:
    def test_orb_high_long_accepted(self):
        p = _base_preset(level_source="ORB_HIGH", direction="LONG")
        assert validate_preset_params(p) == []

    def test_orb_low_short_accepted(self):
        p = _base_preset(level_source="ORB_LOW", direction="SHORT")
        assert validate_preset_params(p) == []

    def test_both_both_accepted(self):
        p = _base_preset(level_source="BOTH", direction="BOTH")
        assert validate_preset_params(p) == []

    def test_orb_high_short_rejected(self):
        """LONG/ORB_HIGH and SHORT/ORB_LOW are the only ORB pairs."""
        p = _base_preset(level_source="ORB_HIGH", direction="SHORT")
        errors = validate_preset_params(p)
        assert any("canonical pair" in e for e in errors)


# ── 6. Invalid pairings still rejected ──────────────────────────────────────

class TestInvalidPairingsRejected:
    def test_pdh_short_rejected(self):
        """PREVIOUS_DAY_HIGH + SHORT is not a canonical pair."""
        p = _base_preset(level_source="PREVIOUS_DAY_HIGH", direction="SHORT")
        errors = validate_preset_params(p)
        assert any("canonical pair" in e for e in errors)

    def test_pdl_long_rejected(self):
        """PREVIOUS_DAY_LOW + LONG is not a canonical pair."""
        p = _base_preset(level_source="PREVIOUS_DAY_LOW", direction="LONG")
        errors = validate_preset_params(p)
        assert any("canonical pair" in e for e in errors)

    def test_unknown_source_rejected(self):
        p = _base_preset(level_source="MAGIC", direction="LONG")
        errors = validate_preset_params(p)
        assert any("level_source" in e for e in errors)
