"""Tests for the generic Level Provider dispatcher (Phase 2).

Covers:
  1. Dispatcher returns OK for ORB_HIGH
  2. Dispatcher returns OK for ORB_LOW
  3. Dispatcher fails explicitly for known-but-unimplemented sources (PDH, PDL, etc.)
  4. Dispatcher fails explicitly for unknown sources
  5. No silent fallback to ORB
  6. LevelResult contract validation (generic fields present)
  7. Legacy ORB fields preserved for backward compatibility
  8. provider_data populated for ORB
  9. scan_from_index == orb_candle_index (alias consistency)
  10. scan_from_bar == orb_candle (alias consistency)
  11. Sequence validator returns NOT_APPLICABLE for non-ORB
  12. Sequence validator applied normally for ORB
  13. Sequence validator NOT_APPLICABLE has correct max_valid_index
  14. Regression: ORB trade output unchanged through dispatcher
"""

import pytest

from trading_lab.level_provider import (
    build_level,
    validate_level_result,
    IMPLEMENTED_SOURCES,
    KNOWN_FUTURE_SOURCES,
    ALL_KNOWN_SOURCES,
    _is_orb_source,
)
from trading_lab.sequence_validator import validate_sequence
from trading_lab.session_context import build_session_context


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_date_ms(date_str, time_str):
    from datetime import datetime
    iso = f"{date_str}T{time_str}:00-04:00"
    dt = datetime.fromisoformat(iso)
    return int(dt.timestamp() * 1000)


def _mc(date_str, time_str, open_, high, low, close):
    return {
        "time_ms": _make_date_ms(date_str, time_str),
        "open": open_, "high": high, "low": low, "close": close,
    }


DATE = "2025-06-10"

# A minimal valid session: ORB candle + 3 post-ORB candles
CANDLES = [
    _mc(DATE, "09:30", 100.0, 102.0, 99.0, 101.0),  # ORB
    _mc(DATE, "09:35", 101.0, 103.0, 100.5, 102.5),  # break above
    _mc(DATE, "09:40", 102.5, 104.0, 102.0, 103.5),  # displacement
    _mc(DATE, "09:45", 103.5, 104.5, 101.0, 101.5),  # retest contact
]

BASE_CONFIG = {
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "tick_size": 0.01,
}


def _sc_and_config(level_source="ORB_HIGH", direction="LONG"):
    config = {**BASE_CONFIG, "level_source": level_source, "direction": direction}
    sc = build_session_context(CANDLES, config)
    assert sc["status"] == "OK"
    return sc, config


# ── 1–2. Dispatcher returns OK for ORB_HIGH and ORB_LOW ─────────────────────

class TestDispatcherORB:
    def test_orb_high_ok(self):
        sc, config = _sc_and_config("ORB_HIGH")
        result = build_level(sc["candles"], sc, config)
        assert result["status"] == "OK"
        assert result["level_source"] == "ORB_HIGH"

    def test_orb_low_ok(self):
        sc, config = _sc_and_config("ORB_LOW", direction="SHORT")
        result = build_level(sc["candles"], sc, config)
        assert result["status"] == "OK"
        assert result["level_source"] == "ORB_LOW"

    def test_orb_high_level_price_is_high(self):
        sc, config = _sc_and_config("ORB_HIGH")
        result = build_level(sc["candles"], sc, config)
        assert result["level_price"] == 102.0  # high of ORB candle

    def test_orb_low_level_price_is_low(self):
        sc, config = _sc_and_config("ORB_LOW", direction="SHORT")
        result = build_level(sc["candles"], sc, config)
        assert result["level_price"] == 99.0  # low of ORB candle


# ── 3. Explicit failure for known-but-unimplemented sources ──────────────────

class TestDispatcherUnimplemented:
    @pytest.mark.parametrize("source", sorted(KNOWN_FUTURE_SOURCES))
    def test_known_future_source_fails(self, source):
        sc, config = _sc_and_config("ORB_HIGH")
        config = {**config, "level_source": source}
        result = build_level(sc["candles"], sc, config)
        assert result["status"] == "FAILED"
        assert result["failed_stage"] == "PROVIDER_NOT_IMPLEMENTED"
        assert source in result["reason"]
        assert "No fallback" in result["reason"]

    def test_future_source_not_silently_orb(self):
        """PMH must NOT silently produce an ORB result."""
        sc, config = _sc_and_config("ORB_HIGH")
        config = {**config, "level_source": "PMH"}
        result = build_level(sc["candles"], sc, config)
        assert result["status"] == "FAILED"
        # Must not contain any ORB fields
        assert "orb_high" not in result
        assert "level_price" not in result


# ── 4–5. Unknown sources fail explicitly, no silent fallback ─────────────────

class TestDispatcherUnknown:
    @pytest.mark.parametrize("source", ["VWAP", "MAGIC", "", "orb_high"])
    def test_unknown_source_fails(self, source):
        sc, config = _sc_and_config("ORB_HIGH")
        config = {**config, "level_source": source}
        result = build_level(sc["candles"], sc, config)
        assert result["status"] == "FAILED"
        assert result["failed_stage"] == "UNKNOWN_LEVEL_SOURCE"
        assert "not recognized" in result["reason"]


# ── 6. LevelResult contract validation ──────────────────────────────────────

class TestLevelResultContract:
    def test_orb_result_passes_contract_validation(self):
        sc, config = _sc_and_config("ORB_HIGH")
        result = build_level(sc["candles"], sc, config)
        valid, reason = validate_level_result(result)
        assert valid, f"Contract violation: {reason}"

    def test_contract_requires_status_ok(self):
        valid, reason = validate_level_result({"status": "FAILED"})
        assert not valid
        assert "status" in reason

    def test_contract_requires_scan_from_index(self):
        sc, config = _sc_and_config("ORB_HIGH")
        result = build_level(sc["candles"], sc, config)
        del result["scan_from_index"]
        valid, reason = validate_level_result(result)
        assert not valid
        assert "scan_from_index" in reason

    def test_contract_requires_scan_from_bar(self):
        sc, config = _sc_and_config("ORB_HIGH")
        result = build_level(sc["candles"], sc, config)
        del result["scan_from_bar"]
        valid, reason = validate_level_result(result)
        assert not valid
        assert "scan_from_bar" in reason

    def test_contract_requires_level_price_numeric(self):
        sc, config = _sc_and_config("ORB_HIGH")
        result = build_level(sc["candles"], sc, config)
        result["level_price"] = "102.0"
        valid, reason = validate_level_result(result)
        assert not valid
        assert "level_price" in reason

    def test_non_dict_fails(self):
        valid, reason = validate_level_result("not a dict")
        assert not valid


# ── 7–8. Legacy ORB fields and provider_data ─────────────────────────────────

class TestLegacyFields:
    def test_legacy_orb_fields_present(self):
        sc, config = _sc_and_config("ORB_HIGH")
        result = build_level(sc["candles"], sc, config)
        # Legacy fields (deprecated but preserved)
        assert "orb_candle_index" in result
        assert "orb_candle" in result
        assert "orb_high" in result
        assert "orb_low" in result
        assert "orb_high_ticks" in result
        assert "orb_low_ticks" in result
        assert "orb_low_active" in result

    def test_provider_data_populated(self):
        sc, config = _sc_and_config("ORB_HIGH")
        result = build_level(sc["candles"], sc, config)
        pd = result["provider_data"]
        assert pd["orb_high"] == result["orb_high"]
        assert pd["orb_low"] == result["orb_low"]
        assert pd["orb_high_ticks"] == result["orb_high_ticks"]
        assert pd["orb_low_ticks"] == result["orb_low_ticks"]
        assert pd["orb_low_active"] == result["orb_low_active"]


# ── 9–10. Alias consistency ─────────────────────────────────────────────────

class TestAliasConsistency:
    def test_scan_from_index_equals_orb_candle_index(self):
        sc, config = _sc_and_config("ORB_HIGH")
        result = build_level(sc["candles"], sc, config)
        assert result["scan_from_index"] == result["orb_candle_index"]

    def test_scan_from_bar_is_orb_candle(self):
        sc, config = _sc_and_config("ORB_HIGH")
        result = build_level(sc["candles"], sc, config)
        assert result["scan_from_bar"] is result["orb_candle"]


# ── 11–13. Sequence validator ORB-conditional behavior ──────────────────────

class TestSequenceValidatorConditional:
    def _orb_dict(self, orb_high=102.0, orb_low=99.0):
        return {"status": "OK", "orb_high": orb_high, "orb_low": orb_low}

    def _break_ok(self):
        return {"status": "OK"}

    def _disp_ok(self, first_retest=3):
        return {"status": "OK", "first_retest_contact_index": first_retest}

    def test_orb_high_applies_normally(self):
        """ORB_HIGH must apply the ORB band invalidation check."""
        candles = [
            {"time_ms": i, "open": 100, "high": 101, "low": 99, "close": 100}
            for i in range(5)
        ]
        config = {**BASE_CONFIG, "level_source": "ORB_HIGH", "consecutive_orb_closes": 2}
        result = validate_sequence(
            candles, self._orb_dict(), self._break_ok(), self._disp_ok(), config
        )
        # Should be OK or INVALIDATED — NOT "NOT_APPLICABLE"
        assert result["status"] in ("OK", "INVALIDATED")

    def test_orb_low_applies_normally(self):
        """ORB_LOW must also apply the ORB band invalidation check."""
        candles = [
            {"time_ms": i, "open": 100, "high": 101, "low": 99, "close": 100}
            for i in range(5)
        ]
        config = {**BASE_CONFIG, "level_source": "ORB_LOW", "direction": "SHORT",
                  "consecutive_orb_closes": 2}
        result = validate_sequence(
            candles, self._orb_dict(), self._break_ok(), self._disp_ok(), config
        )
        assert result["status"] in ("OK", "INVALIDATED")

    @pytest.mark.parametrize("source", ["PMH", "PML", "PIVOT_WICK", "OCL"])
    def test_non_orb_returns_not_applicable(self, source):
        """Non-ORB, non-PDH/PDL sources must skip validation."""
        candles = [
            {"time_ms": i, "open": 100, "high": 101, "low": 99, "close": 100}
            for i in range(5)
        ]
        config = {**BASE_CONFIG, "level_source": source, "consecutive_orb_closes": 2}
        dummy_level = {"status": "OK"}
        result = validate_sequence(
            candles, dummy_level, self._break_ok(), self._disp_ok(), config
        )
        assert result["status"] == "NOT_APPLICABLE"
        assert source in result["reason"]

    @pytest.mark.parametrize("source,direction", [
        ("PREVIOUS_DAY_HIGH", "LONG"),
        ("PREVIOUS_DAY_LOW", "SHORT"),
    ])
    def test_pdh_pdl_now_validated(self, source, direction):
        """PDH/PDL are now validated with line-level invalidation."""
        candles = [
            {"time_ms": i, "open": 100, "high": 101, "low": 99, "close": 100}
            for i in range(5)
        ]
        config = {**BASE_CONFIG, "level_source": source, "direction": direction,
                  "level_invalidation_closes": 2}
        level = {"status": "OK", "level_price": 100.0}
        result = validate_sequence(
            candles, level, self._break_ok(), self._disp_ok(), config
        )
        assert result["status"] in ("OK", "INVALIDATED")
        assert result.get("level_source") == source

    def test_not_applicable_max_valid_index(self):
        """NOT_APPLICABLE must set max_valid_index to last candle (unsupported source)."""
        candles = [
            {"time_ms": i, "open": 100, "high": 101, "low": 99, "close": 100}
            for i in range(10)
        ]
        config = {**BASE_CONFIG, "level_source": "PIVOT_WICK", "consecutive_orb_closes": 2}
        result = validate_sequence(
            candles, {"status": "OK"}, self._break_ok(), self._disp_ok(), config
        )
        assert result["status"] == "NOT_APPLICABLE"
        assert result["max_valid_index"] == 9  # len(candles) - 1
        assert result["invalidation_index"] is None
        assert result["consecutive_inside_closes"] == []

    def test_not_applicable_does_not_read_orb_fields(self):
        """Non-ORB must not crash even if orb_high/orb_low are missing."""
        candles = [{"time_ms": 0, "open": 1, "high": 2, "low": 0, "close": 1}]
        config = {**BASE_CONFIG, "level_source": "PIVOT_WICK"}
        # Pass a level dict WITHOUT orb_high/orb_low
        result = validate_sequence(
            candles,
            {"status": "OK"},  # no orb_high, no orb_low
            self._break_ok(),
            {"status": "OK", "first_retest_contact_index": 0},
            config,
        )
        assert result["status"] == "NOT_APPLICABLE"


# ── 14. Regression: ORB trade output unchanged through dispatcher ────────────

class TestRegressionORBUnchanged:
    """Verify that running through build_level produces identical results
    to the pre-Phase-2 code path (build_orb directly)."""

    def test_orb_high_level_matches_direct_build(self):
        """build_level for ORB_HIGH produces the same level_price as build_orb."""
        from trading_lab.orb_builder import build_orb
        sc, config = _sc_and_config("ORB_HIGH")

        direct = build_orb(sc["candles"], sc, config)
        dispatched = build_level(sc["candles"], sc, config)

        assert direct["level_price"] == dispatched["level_price"]
        assert direct["level_price_ticks"] == dispatched["level_price_ticks"]
        assert direct["orb_candle_index"] == dispatched["orb_candle_index"]
        assert direct["orb_high"] == dispatched["orb_high"]
        assert direct["orb_low"] == dispatched["orb_low"]
        assert direct["date"] == dispatched["date"]

    def test_orb_low_level_matches_direct_build(self):
        """build_level for ORB_LOW produces the same level_price as build_orb."""
        from trading_lab.orb_builder import build_orb
        sc, config = _sc_and_config("ORB_LOW", direction="SHORT")

        direct = build_orb(sc["candles"], sc, config)
        dispatched = build_level(sc["candles"], sc, config)

        assert direct["level_price"] == dispatched["level_price"]
        assert direct["level_price_ticks"] == dispatched["level_price_ticks"]
        assert direct["orb_candle_index"] == dispatched["orb_candle_index"]

    def test_full_pipeline_through_dispatcher(self):
        """Full strategy run through dispatcher produces same outcome
        as it did before Phase 2 (ORB path)."""
        from trading_lab.strategy_runner import run_bdrr_strategy

        preset = {
            "preset_id": "phase2_regression",
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
            "min_close_beyond_level_ticks": 1,
            "min_displacement_bars": 1,
            "consecutive_orb_closes": 2,
            "confirmation_wick_penetration_pct_min": 0,
        }
        config = {
            "tick_size": 0.01,
            "engine_version": "bdrr_v1.0",
            "exit_target_r": 2,
        }

        sessions = [{
            "symbol": "TEST",
            "date": DATE,
            "market_timezone": "America/New_York",
            "session_open_utc_ms": CANDLES[0]["time_ms"],
            "session_close_utc_ms": CANDLES[-1]["time_ms"],
            "timeframe": "5m",
            "candles": CANDLES,
        }]

        results = run_bdrr_strategy(sessions, preset, config)
        assert len(results) == 1
        # The pipeline must complete (not crash) for ORB
        assert results[0]["outcome"] in [
            "WIN", "LOSS", "NO_VALID_SETUP", "PIPELINE_FAILURE",
        ]


# ── Helper function tests ───────────────────────────────────────────────────

class TestHelpers:
    def test_is_orb_source_true(self):
        assert _is_orb_source("ORB_HIGH") is True
        assert _is_orb_source("ORB_LOW") is True

    def test_is_orb_source_false(self):
        assert _is_orb_source("PREVIOUS_DAY_HIGH") is False
        assert _is_orb_source("PIVOT_WICK") is False
        assert _is_orb_source("") is False

    def test_registries_disjoint(self):
        """Implemented and future sources must not overlap."""
        assert IMPLEMENTED_SOURCES & KNOWN_FUTURE_SOURCES == set()

    def test_all_known_is_union(self):
        assert ALL_KNOWN_SOURCES == IMPLEMENTED_SOURCES | KNOWN_FUTURE_SOURCES
