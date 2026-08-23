"""Tests for evaluate_single_candle_rejection_geometry() — micro-task 30.

This is a surgical extraction of the geometry logic that previously
lived only as the nested evaluate_geometry() closure inside
find_rejection(). No rule, threshold, or tick-handling behavior was
changed — this file exists to prove that, and to make the utility
independently callable and testable.

Cases covered (exactly as specified):
    G1 LONG canonical PASS
    G2 SHORT canonical PASS
    G3 LONG canonical FAIL
    G4 SHORT canonical FAIL
    G5 Equivalence: find_rejection()'s reported geometry (which now
       calls the extracted utility internally) matches calling the
       utility directly on the same candle/level/config
    G6 Existing rejection_finder.py suite remains green (run
       separately as backend/tests/test_rejection_finder.py — not
       duplicated here, see report)
"""

from __future__ import annotations

import pytest

from trading_lab.rejection_finder import (
    BODY_RATIO_MAX,
    FAVORABLE_CLOSE_LOCATION_MIN,
    REJECTION_WICK_RATIO_MIN,
    evaluate_single_candle_rejection_geometry,
)


TICK_SIZE = 0.01


def _bar(time_ms, open_, high, low, close):
    return {"time_ms": time_ms, "open": open_, "high": high, "low": low, "close": close}


# ═════════════════════════════════════════════════════════════════════════
# G1 — LONG canonical PASS
# (the exact, already-validated fixture used throughout prior PDH/PDL
# tasks: test_signal_detector.py's _rejection_bar(), level=101.00)
# ═════════════════════════════════════════════════════════════════════════

class TestG1LongCanonicalPass:
    def test_qualifies_true(self):
        candle = _bar(9, 101.10, 101.30, 100.80, 101.20)
        result = evaluate_single_candle_rejection_geometry(
            candle, "LONG", level_price=101.00, tick_size=TICK_SIZE,
        )
        assert result["qualifies"] is True
        assert result["failed_rules"] == []
        assert result["geometry"]["rejection_wick_ratio"] == pytest.approx(0.6)
        assert result["geometry"]["body_ratio"] == pytest.approx(0.2)
        assert result["geometry"]["favorable_close_location"] == pytest.approx(0.8)


# ═════════════════════════════════════════════════════════════════════════
# G2 — SHORT canonical PASS
# (mirrored version of the same fixture, level=99.00 — same technique
# already validated across prior PDH/PDL tasks)
# ═════════════════════════════════════════════════════════════════════════

class TestG2ShortCanonicalPass:
    def test_qualifies_true(self):
        candle = _bar(9, 98.90, 99.20, 98.70, 98.80)
        result = evaluate_single_candle_rejection_geometry(
            candle, "SHORT", level_price=99.00, tick_size=TICK_SIZE,
        )
        assert result["qualifies"] is True
        assert result["failed_rules"] == []
        assert result["geometry"]["rejection_wick_ratio"] == pytest.approx(0.6)
        assert result["geometry"]["body_ratio"] == pytest.approx(0.2)
        assert result["geometry"]["favorable_close_location"] == pytest.approx(0.8)


# ═════════════════════════════════════════════════════════════════════════
# G3 — LONG canonical FAIL: big body, wick inside the level zone.
# ═════════════════════════════════════════════════════════════════════════

class TestG3LongCanonicalFail:
    def test_qualifies_false(self):
        candle = _bar(9, 100.50, 101.60, 100.40, 101.50)
        result = evaluate_single_candle_rejection_geometry(
            candle, "LONG", level_price=101.00, tick_size=TICK_SIZE,
        )
        assert result["qualifies"] is False
        assert "REJECTION_WICK_RATIO_TOO_LOW" in result["failed_rules"]
        assert "BODY_RATIO_TOO_HIGH" in result["failed_rules"]


# ═════════════════════════════════════════════════════════════════════════
# G4 — SHORT canonical FAIL: symmetric.
# ═════════════════════════════════════════════════════════════════════════

class TestG4ShortCanonicalFail:
    def test_qualifies_false(self):
        candle = _bar(9, 99.50, 99.60, 97.90, 98.00)
        result = evaluate_single_candle_rejection_geometry(
            candle, "SHORT", level_price=99.00, tick_size=TICK_SIZE,
        )
        assert result["qualifies"] is False
        assert "REJECTION_WICK_RATIO_TOO_LOW" in result["failed_rules"]
        assert "BODY_RATIO_TOO_HIGH" in result["failed_rules"]


# ═════════════════════════════════════════════════════════════════════════
# G5 — Equivalence: find_rejection()'s reported geometry (which now
# calls the extracted utility internally) matches calling the utility
# directly on the same real confirmation candle, level, and thresholds.
# ═════════════════════════════════════════════════════════════════════════

MS_0945 = 1782913500000


class TestG5EquivalenceLong:
    def test_reported_geometry_matches_direct_utility_call(self):
        from trading_lab.orb_builder import build_orb as _build_orb
        from trading_lab.session_context import build_session_context as _bsc
        from test_rejection_finder import (
            CONFIG as RF_CONFIG,
            base_candles,
            qualifying_candle,
            run_full,
        )

        candles = base_candles([qualifying_candle(MS_0945)])
        sc = _bsc(candles, RF_CONFIG)
        orb = _build_orb(sc["candles"], sc, RF_CONFIG)
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"

        confirmation_candle = rej["confirmation_candle"]
        direct = evaluate_single_candle_rejection_geometry(
            confirmation_candle, "LONG",
            level_price=orb["level_price"], tick_size=TICK_SIZE,
            min_close_beyond_level_ticks=RF_CONFIG["min_close_beyond_level_ticks"],
            confirmation_wick_penetration_pct_min=RF_CONFIG["confirmation_wick_penetration_pct_min"],
        )

        assert direct["qualifies"] is True
        assert direct["failed_rules"] == []
        # rej["geometry"] has 4 additional ATR keys injected AFTER the
        # geometry call (by find_rejection()'s own _inject_atr()) — ATR
        # is a separate concern, not part of this extracted function.
        # Every core geometry field must still match exactly.
        for key, value in direct["geometry"].items():
            assert rej["geometry"][key] == value, key


class TestG5EquivalenceFailedRetests:
    def test_failed_retest_geometry_matches_direct_utility_call(self):
        """Also check a FAILED retest candle's geometry (not just the
        final confirmation candle) — find_rejection() records these in
        failed_retests, each carrying the same geometry shape."""
        from trading_lab.orb_builder import build_orb as _build_orb
        from trading_lab.session_context import build_session_context as _bsc
        from test_rejection_finder import (
            CONFIG as RF_CONFIG,
            base_candles,
            failing_candle_a,
            qualifying_candle,
            run_full,
        )

        candles = base_candles([
            failing_candle_a(MS_0945),
            qualifying_candle(MS_0945 + 300000),
        ])
        sc = _bsc(candles, RF_CONFIG)
        orb = _build_orb(sc["candles"], sc, RF_CONFIG)
        _, _, _, _, _, rej = run_full(candles)
        assert rej["status"] == "OK"
        assert rej["failed_retest_count"] >= 1

        failed_entry = rej["failed_retests"][0]
        direct = evaluate_single_candle_rejection_geometry(
            failed_entry["candle"], "LONG",
            level_price=orb["level_price"], tick_size=TICK_SIZE,
            min_close_beyond_level_ticks=RF_CONFIG["min_close_beyond_level_ticks"],
            confirmation_wick_penetration_pct_min=RF_CONFIG["confirmation_wick_penetration_pct_min"],
        )
        assert direct["failed_rules"] == failed_entry["failed_rules"]
        # Same ATR-separation rationale as the LONG equivalence test above.
        for key, value in direct["geometry"].items():
            assert failed_entry["geometry"][key] == value, key


# ═════════════════════════════════════════════════════════════════════════
# Misc: confirm no new parameter changes default behavior, thresholds
# match the same frozen module constants.
# ═════════════════════════════════════════════════════════════════════════

class TestDefaultsMatchModuleConstants:
    def test_defaults_are_the_same_frozen_constants(self):
        import inspect
        sig = inspect.signature(evaluate_single_candle_rejection_geometry)
        assert sig.parameters["rejection_wick_ratio_min"].default == REJECTION_WICK_RATIO_MIN
        assert sig.parameters["body_ratio_max"].default == BODY_RATIO_MAX
        assert sig.parameters["favorable_close_location_min"].default == FAVORABLE_CLOSE_LOCATION_MIN
        assert sig.parameters["min_close_beyond_level_ticks"].default is None
        assert sig.parameters["confirmation_wick_penetration_pct_min"].default == 0.20

