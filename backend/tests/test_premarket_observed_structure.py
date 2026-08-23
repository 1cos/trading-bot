"""Tests for evaluate_observed_premarket_structure() — micro-task 25.

Uses the REAL break candle already located by
premarket_break_classifier.classify_premarket_context() (via its real
break_timestamp_ms) to build a genuine BDRR structure on premarket
bars, calling find_displacement() and validate_sequence() directly and
unmodified. No synthetic break, timestamp, or displacement.

Cases covered (exactly as specified):
    O1  PDH LONG positive              -> retest_ready=True
    O2  PDH displacement incomplete    -> False
    O3  PDL SHORT positive             -> True
    O4  PDL displacement incomplete    -> False
    O5  LONG invalidated pre-market    -> False
    O6  SHORT invalidated pre-market   -> False
    O7  timestamp integrity            -> output timestamp == input, real bar
    O8  unknown break timestamp        -> clean fail, no crash
    O9  premarket retest already seen  -> explicit True field
    O10 stateless                      -> no memory between calls
"""

from __future__ import annotations

from trading_lab.premarket_observed_structure import evaluate_observed_premarket_structure


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
# O1 — PDH LONG positive: real break + 3 valid displacement bars + real
# contact, no invalidation.
# ═════════════════════════════════════════════════════════════════════════

def _pdh_positive_bars():
    return [
        _bar(1, close=104.00),                                    # anchor (before break)
        _bar(2, close=105.50),                                    # BREAK: close > PDH
        _bar(3, close=105.60, low=105.20, high=105.80),           # disp 1/3
        _bar(4, close=105.70, low=105.30, high=105.90),           # disp 2/3
        _bar(5, close=105.40, low=105.10, high=105.85),           # disp 3/3
        _bar(6, close=105.10, low=104.80, high=105.30),           # contact: low <= PDH
    ]


class TestO1PdhLongPositive:
    def test_retest_ready_true(self):
        bars = _pdh_positive_bars()
        out = evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is True
        assert out["reason"] == "READY"
        assert out["displacement_bar_count"] == 3
        assert out["break_origin"] == "PREMARKET_OBSERVED"
        assert out["break_timestamp_ms"] == 2


# ═════════════════════════════════════════════════════════════════════════
# O2 — PDH displacement incomplete: real break + only 1 displacement
# bar + early contact.
# ═════════════════════════════════════════════════════════════════════════

class TestO2PdhDisplacementIncomplete:
    def test_retest_ready_false(self):
        bars = [
            _bar(1, close=104.00),                          # anchor
            _bar(2, close=105.50),                          # BREAK
            _bar(3, close=105.60, low=105.20, high=105.80),  # 1 disp bar
            _bar(4, close=105.10, low=104.90, high=105.30),  # contact (too early)
        ]
        out = evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is False
        assert out["reason"] == "DISPLACEMENT_INCOMPLETE"
        assert out["break_timestamp_ms"] == 2


# ═════════════════════════════════════════════════════════════════════════
# O3 — PDL SHORT positive: symmetric.
# ═════════════════════════════════════════════════════════════════════════

def _pdl_positive_bars():
    return [
        _bar(1, close=96.00),                                     # anchor
        _bar(2, close=94.50),                                     # BREAK: close < PDL
        _bar(3, close=94.60, low=94.20, high=94.80),              # disp 1/3
        _bar(4, close=94.30, low=94.10, high=94.70),              # disp 2/3
        _bar(5, close=94.60, low=94.30, high=94.90),              # disp 3/3
        _bar(6, close=95.10, low=94.80, high=95.20),              # contact: high >= PDL
    ]


class TestO3PdlShortPositive:
    def test_retest_ready_true(self):
        bars = _pdl_positive_bars()
        out = evaluate_observed_premarket_structure(
            bars, "SHORT", PDL, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is True
        assert out["reason"] == "READY"
        assert out["displacement_bar_count"] == 3
        assert out["break_origin"] == "PREMARKET_OBSERVED"


# ═════════════════════════════════════════════════════════════════════════
# O4 — PDL displacement incomplete
# ═════════════════════════════════════════════════════════════════════════

class TestO4PdlDisplacementIncomplete:
    def test_retest_ready_false(self):
        bars = [
            _bar(1, close=96.00),                          # anchor
            _bar(2, close=94.50),                          # BREAK
            _bar(3, close=94.60, low=94.20, high=94.80),   # 1 disp bar
            _bar(4, close=95.10, low=94.90, high=95.30),   # contact (too early)
        ]
        out = evaluate_observed_premarket_structure(
            bars, "SHORT", PDL, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is False
        assert out["reason"] == "DISPLACEMENT_INCOMPLETE"


# ═════════════════════════════════════════════════════════════════════════
# Displacement complete, no retest yet — the exact scenario reported:
# real break + 3+ real displacement bars + price stays beyond the level
# for the entire visible premarket window (no contact at all). This is
# NOT displacement-incomplete: it is a validly built structure simply
# still awaiting its first touch (which may happen in RTH).
# ═════════════════════════════════════════════════════════════════════════

class TestDisplacementCompleteNoRetestYet:
    def test_pdh_long_ready_with_no_contact_observed(self):
        bars = [
            _bar(1, close=104.00),                                  # anchor
            _bar(2, close=105.50),                                  # BREAK: close > PDH
            _bar(3, close=105.60, low=105.20, high=105.80),          # disp 1
            _bar(4, close=105.70, low=105.30, high=105.90),          # disp 2
            _bar(5, close=105.80, low=105.40, high=106.00),          # disp 3
            _bar(6, close=105.90, low=105.50, high=106.10),          # disp 4 — still no contact
            # price stays above PDH the whole visible window; no bar's
            # low ever touches back down to 105.00.
        ]
        out = evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is True
        assert out["reason"] == "DISPLACEMENT_COMPLETE_AWAITING_RETEST"
        assert out["premarket_retest_already_seen"] is False
        assert out["displacement_bar_count"] == 4  # all 4 bars after break, none is a contact
        assert out["break_timestamp_ms"] == 2

    def test_pdl_short_ready_with_no_contact_observed(self):
        bars = [
            _bar(1, close=96.00),                                    # anchor
            _bar(2, close=94.50),                                    # BREAK: close < PDL
            _bar(3, close=94.40, low=94.00, high=94.80),             # disp 1
            _bar(4, close=94.30, low=93.90, high=94.70),             # disp 2
            _bar(5, close=94.20, low=93.80, high=94.60),             # disp 3
            # price stays below PDL the whole visible window.
        ]
        out = evaluate_observed_premarket_structure(
            bars, "SHORT", PDL, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is True
        assert out["reason"] == "DISPLACEMENT_COMPLETE_AWAITING_RETEST"
        assert out["premarket_retest_already_seen"] is False
        assert out["displacement_bar_count"] == 3

    def test_still_incomplete_when_too_few_bars_and_no_contact(self):
        """No contact yet, but also fewer than min_displacement_bars
        bars available — must NOT be reported as ready."""
        bars = [
            _bar(1, close=104.00),                                  # anchor
            _bar(2, close=105.50),                                  # BREAK
            _bar(3, close=105.60, low=105.20, high=105.80),          # only 1 bar so far
        ]
        out = evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is False
        assert out["reason"] == "DISPLACEMENT_INCOMPLETE"
        assert out["premarket_retest_already_seen"] is False


# ═════════════════════════════════════════════════════════════════════════
# O5 — LONG invalidated pre-market: positive structure, then 2
# consecutive wrong-side closes after the contact.
# ═════════════════════════════════════════════════════════════════════════

class TestO5LongInvalidated:
    def test_retest_ready_false_after_invalidation(self):
        bars = _pdh_positive_bars() + [
            _bar(7, close=104.50),   # wrong side 1 (close < PDH)
            _bar(8, close=104.00),   # wrong side 2 -> INVALIDATED
        ]
        out = evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is False
        assert out["reason"] == "STRUCTURE_INVALIDATED"
        assert out["break_timestamp_ms"] == 2


# ═════════════════════════════════════════════════════════════════════════
# O6 — SHORT invalidated pre-market: symmetric.
# ═════════════════════════════════════════════════════════════════════════

class TestO6ShortInvalidated:
    def test_retest_ready_false_after_invalidation(self):
        bars = _pdl_positive_bars() + [
            _bar(7, close=95.50),   # wrong side 1 (close > PDL)
            _bar(8, close=96.00),   # wrong side 2 -> INVALIDATED
        ]
        out = evaluate_observed_premarket_structure(
            bars, "SHORT", PDL, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is False
        assert out["reason"] == "STRUCTURE_INVALIDATED"


# ═════════════════════════════════════════════════════════════════════════
# O7 — timestamp integrity: output timestamp is exactly the input,
# and it corresponds to a real bar.
# ═════════════════════════════════════════════════════════════════════════

class TestO7TimestampIntegrity:
    def test_output_timestamp_matches_input_and_real_bar(self):
        bars = _pdh_positive_bars()
        out = evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["break_timestamp_ms"] == 2
        real_bar_timestamps = {b["time_ms"] for b in bars}
        assert out["break_timestamp_ms"] in real_bar_timestamps


# ═════════════════════════════════════════════════════════════════════════
# O8 — unknown break timestamp: not present in premarket_bars.
# ═════════════════════════════════════════════════════════════════════════

class TestO8UnknownBreakTimestamp:
    def test_clean_fail_no_crash(self):
        bars = _pdh_positive_bars()
        out = evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=999999,
        )
        assert out["retest_ready"] is False
        assert out["reason"] == "BREAK_TIMESTAMP_NOT_FOUND"


# ═════════════════════════════════════════════════════════════════════════
# O9 — premarket retest already seen: explicit field, True.
# ═════════════════════════════════════════════════════════════════════════

class TestO9PremarketRetestAlreadySeen:
    def test_field_present_and_true(self):
        bars = _pdh_positive_bars()
        out = evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert "premarket_retest_already_seen" in out
        assert out["premarket_retest_already_seen"] is True


# ═════════════════════════════════════════════════════════════════════════
# O10 — stateless: no memory between calls.
# ═════════════════════════════════════════════════════════════════════════

class TestO10Stateless:
    def test_no_memory_between_calls(self):
        valid_bars = _pdh_positive_bars()
        invalidated_bars = valid_bars + [
            _bar(7, close=104.50),
            _bar(8, close=104.00),
        ]

        result_before = evaluate_observed_premarket_structure(
            valid_bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert result_before["retest_ready"] is True

        result_invalidated = evaluate_observed_premarket_structure(
            invalidated_bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert result_invalidated["retest_ready"] is False
        assert result_invalidated["reason"] == "STRUCTURE_INVALIDATED"

        result_reevaluated = evaluate_observed_premarket_structure(
            valid_bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert result_reevaluated["retest_ready"] is True
        assert result_reevaluated == result_before


# ═════════════════════════════════════════════════════════════════════════
# Misc guards
# ═════════════════════════════════════════════════════════════════════════

class TestMiscGuards:
    def test_no_premarket_bars(self):
        out = evaluate_observed_premarket_structure(
            None, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is False
        assert out["reason"] == "NO_PREMARKET_DATA"

    def test_unsupported_direction(self):
        bars = _pdh_positive_bars()
        out = evaluate_observed_premarket_structure(
            bars, "SIDEWAYS", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert out["retest_ready"] is False
        assert out["reason"] == "UNSUPPORTED_DIRECTION"

    def test_break_as_first_bar_has_no_anchor(self):
        """If the located break candle is index 0, there is no real
        preceding candle to use as the compatibility anchor — must
        fail cleanly rather than fabricate one."""
        bars = [
            _bar(1, close=105.50),                          # "break" at index 0
            _bar(2, close=105.60, low=105.20, high=105.80),
        ]
        out = evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=1,
        )
        assert out["retest_ready"] is False
        assert out["reason"] == "NO_ANCHOR_CANDLE"

    def test_never_mutates_input_list(self):
        bars = [
            _bar(6, close=105.10, low=104.80, high=105.30),
            _bar(1, close=104.00),
            _bar(2, close=105.50),
            _bar(3, close=105.60, low=105.20, high=105.80),
            _bar(4, close=105.70, low=105.30, high=105.90),
            _bar(5, close=105.40, low=105.10, high=105.85),
        ]
        original_order = list(bars)
        evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        assert bars == original_order

    def test_default_level_source_derived_from_direction(self):
        bars = _pdh_positive_bars()
        out = evaluate_observed_premarket_structure(
            bars, "LONG", PDH, tick_size=0.01, break_timestamp_ms=2,
        )
        # Reaching a real READY/INVALIDATED outcome (not
        # UNSUPPORTED_CONFIGURATION) proves level_source was
        # correctly auto-derived as PREVIOUS_DAY_HIGH.
        assert out["retest_ready"] is True
