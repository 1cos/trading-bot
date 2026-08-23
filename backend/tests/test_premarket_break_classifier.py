"""Tests for classify_premarket_context() — micro-task 20.

Pure predicate: classifies, per level, the premarket relationship
between price and PDH/PDL as NONE / PREMARKET_OBSERVED /
PREMARKET_CARRY_IN. No retest, no displacement, no synthetic break,
no setup_key, no wiring to eligibility/candidate evaluator/execution.

Cases covered (exactly as specified):
    PMB1 PDH observed             -> PREMARKET_OBSERVED + real timestamp
    PMB2 PDL observed             -> PREMARKET_OBSERVED
    PMB3 PDH carry-in             -> PREMARKET_CARRY_IN, timestamp None
    PMB4 PDL carry-in             -> PREMARKET_CARRY_IN
    PMB5 no break PDH             -> NONE
    PMB6 no break PDL             -> NONE
    PMB7 equality guard           -> first bar exactly at level is NOT carry-in
    PMB8 observed takes precedence -> not carry-in, later real crossing -> OBSERVED
"""

from __future__ import annotations

from trading_lab.premarket_break_classifier import classify_premarket_context


def _bar(time_ms, close, open_=None, high=None, low=None, volume=100):
    return {
        "time_ms": time_ms,
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": volume,
    }


PDH = 105.00
PDL = 95.00


# ═════════════════════════════════════════════════════════════════════════
# PMB1 — PDH observed: close <= PDH, then close > PDH
# ═════════════════════════════════════════════════════════════════════════

class TestPMB1PdhObserved:
    def test_real_crossing_detected_with_timestamp(self):
        bars = [
            _bar(1, 104.00),   # <= PDH
            _bar(2, 104.50),   # <= PDH
            _bar(3, 105.50),   # > PDH -> the break candle
            _bar(4, 106.00),   # still above, irrelevant
        ]
        out = classify_premarket_context(bars, PDH, "LONG", "PREVIOUS_DAY_HIGH")
        assert out["break_origin"] == "PREMARKET_OBSERVED"
        assert out["break_timestamp_ms"] == 3
        assert out["level_source"] == "PREVIOUS_DAY_HIGH"
        assert out["direction"] == "LONG"
        assert out["level_price"] == PDH


# ═════════════════════════════════════════════════════════════════════════
# PMB2 — PDL observed: close >= PDL, then close < PDL
# ═════════════════════════════════════════════════════════════════════════

class TestPMB2PdlObserved:
    def test_real_crossing_detected_with_timestamp(self):
        bars = [
            _bar(1, 96.00),    # >= PDL
            _bar(2, 95.50),    # >= PDL
            _bar(3, 94.50),    # < PDL -> the break candle
            _bar(4, 94.00),
        ]
        out = classify_premarket_context(bars, PDL, "SHORT", "PREVIOUS_DAY_LOW")
        assert out["break_origin"] == "PREMARKET_OBSERVED"
        assert out["break_timestamp_ms"] == 3
        assert out["level_source"] == "PREVIOUS_DAY_LOW"
        assert out["direction"] == "SHORT"


# ═════════════════════════════════════════════════════════════════════════
# PMB3 — PDH carry-in: first bar already above PDH
# ═════════════════════════════════════════════════════════════════════════

class TestPMB3PdhCarryIn:
    def test_first_bar_already_broken_is_carry_in(self):
        bars = [
            _bar(1, 106.00),   # already > PDH at the very first bar
            _bar(2, 106.50),
        ]
        out = classify_premarket_context(bars, PDH, "LONG", "PREVIOUS_DAY_HIGH")
        assert out["break_origin"] == "PREMARKET_CARRY_IN"
        assert out["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# PMB4 — PDL carry-in: first bar already below PDL
# ═════════════════════════════════════════════════════════════════════════

class TestPMB4PdlCarryIn:
    def test_first_bar_already_broken_is_carry_in(self):
        bars = [
            _bar(1, 94.00),    # already < PDL at the very first bar
            _bar(2, 93.50),
        ]
        out = classify_premarket_context(bars, PDL, "SHORT", "PREVIOUS_DAY_LOW")
        assert out["break_origin"] == "PREMARKET_CARRY_IN"
        assert out["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# PMB5 — no break PDH: always below/at PDH
# ═════════════════════════════════════════════════════════════════════════

class TestPMB5NoBreakPdh:
    def test_never_crosses_is_none(self):
        bars = [
            _bar(1, 100.00),
            _bar(2, 102.00),
            _bar(3, 104.99),
            _bar(4, 105.00),   # exactly at level — still not broken
        ]
        out = classify_premarket_context(bars, PDH, "LONG", "PREVIOUS_DAY_HIGH")
        assert out["break_origin"] == "NONE"
        assert out["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# PMB6 — no break PDL: always above/at PDL
# ═════════════════════════════════════════════════════════════════════════

class TestPMB6NoBreakPdl:
    def test_never_crosses_is_none(self):
        bars = [
            _bar(1, 100.00),
            _bar(2, 97.00),
            _bar(3, 95.01),
            _bar(4, 95.00),    # exactly at level — still not broken
        ]
        out = classify_premarket_context(bars, PDL, "SHORT", "PREVIOUS_DAY_LOW")
        assert out["break_origin"] == "NONE"
        assert out["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# PMB7 — equality guard: first bar exactly at the level is NOT carry-in
# ═════════════════════════════════════════════════════════════════════════

class TestPMB7EqualityGuard:
    def test_pdh_first_bar_exactly_at_level_not_carry_in(self):
        bars = [
            _bar(1, 105.00),   # exactly == PDH — NOT broken
            _bar(2, 104.50),   # stays at/below afterward, no real crossing
        ]
        out = classify_premarket_context(bars, PDH, "LONG", "PREVIOUS_DAY_HIGH")
        assert out["break_origin"] != "PREMARKET_CARRY_IN"
        assert out["break_origin"] == "NONE"
        assert out["break_timestamp_ms"] is None

    def test_pdl_first_bar_exactly_at_level_not_carry_in(self):
        bars = [
            _bar(1, 95.00),    # exactly == PDL — NOT broken
            _bar(2, 95.50),    # stays at/above afterward, no real crossing
        ]
        out = classify_premarket_context(bars, PDL, "SHORT", "PREVIOUS_DAY_LOW")
        assert out["break_origin"] != "PREMARKET_CARRY_IN"
        assert out["break_origin"] == "NONE"
        assert out["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# PMB8 — observed takes precedence: first bar not carry-in, later a real
# crossing appears -> PREMARKET_OBSERVED, never carry-in.
# ═════════════════════════════════════════════════════════════════════════

class TestPMB8ObservedTakesPrecedence:
    def test_pdh_not_carry_in_then_real_crossing_is_observed(self):
        bars = [
            _bar(1, 105.00),   # exactly at level — not broken (not carry-in)
            _bar(2, 104.80),   # still not broken
            _bar(3, 105.60),   # real crossing -> the break candle
        ]
        out = classify_premarket_context(bars, PDH, "LONG", "PREVIOUS_DAY_HIGH")
        assert out["break_origin"] == "PREMARKET_OBSERVED"
        assert out["break_timestamp_ms"] == 3

    def test_pdl_not_carry_in_then_real_crossing_is_observed(self):
        bars = [
            _bar(1, 95.00),    # exactly at level — not broken (not carry-in)
            _bar(2, 95.20),    # still not broken
            _bar(3, 94.40),    # real crossing -> the break candle
        ]
        out = classify_premarket_context(bars, PDL, "SHORT", "PREVIOUS_DAY_LOW")
        assert out["break_origin"] == "PREMARKET_OBSERVED"
        assert out["break_timestamp_ms"] == 3


# ═════════════════════════════════════════════════════════════════════════
# Misc guards
# ═════════════════════════════════════════════════════════════════════════

class TestMiscGuards:
    def test_no_premarket_bars_is_none(self):
        out = classify_premarket_context(None, PDH, "LONG", "PREVIOUS_DAY_HIGH")
        assert out["break_origin"] == "NONE"
        assert out["break_timestamp_ms"] is None

    def test_empty_premarket_bars_is_none(self):
        out = classify_premarket_context([], PDH, "LONG", "PREVIOUS_DAY_HIGH")
        assert out["break_origin"] == "NONE"
        assert out["break_timestamp_ms"] is None

    def test_unsupported_direction_is_none(self):
        out = classify_premarket_context(
            [_bar(1, 200.00)], PDH, "SIDEWAYS", "PREVIOUS_DAY_HIGH",
        )
        assert out["break_origin"] == "NONE"
        assert out["break_timestamp_ms"] is None

    def test_never_mutates_input_list(self):
        bars = [_bar(3, 106.00), _bar(1, 100.00), _bar(2, 102.00)]
        original_order = list(bars)
        classify_premarket_context(bars, PDH, "LONG", "PREVIOUS_DAY_HIGH")
        assert bars == original_order  # unsorted input untouched
