"""Tests for evaluate_carry_in_separation() — micro-task 23.

Pure predicate: given a PREMARKET_CARRY_IN level, determines whether
the visible premarket bars show enough real separation (reusing
find_displacement()'s exact bar geometry and
sequence_validator's line-level invalidation semantics) to be
RETEST_READY. No break candle, timestamp, or displacement is ever
fabricated.

Cases covered (exactly as specified):
    C1 PDH LONG positive     -> retest_ready=True
    C2 PDH insufficient      -> False
    C3 PDL SHORT positive    -> True
    C4 PDL insufficient      -> False
    C5 LONG invalidated      -> False
    C6 SHORT invalidated     -> False
    C7 timestamp honesty     -> break_timestamp_ms is None always
    C8 stateless             -> same function, no memory between calls
"""

from __future__ import annotations

from trading_lab.carry_in_separation import evaluate_carry_in_separation


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
# C1 — PDH LONG positive: first bar already above PDH + >=3 valid
# separation bars (low > PDH throughout), no contact yet.
# ═════════════════════════════════════════════════════════════════════════

class TestC1PdhLongPositive:
    def test_retest_ready_true(self):
        bars = [
            _bar(1, close=106.00, low=105.50, high=106.50),  # low > PDH
            _bar(2, close=106.50, low=105.80, high=107.00),  # low > PDH
            _bar(3, close=107.00, low=106.20, high=107.50),  # low > PDH
        ]
        out = evaluate_carry_in_separation(bars, "LONG", PDH)
        assert out["retest_ready"] is True
        assert out["visible_displacement_bar_count"] == 3
        assert out["break_origin"] == "PREMARKET_CARRY_IN"
        assert out["break_timestamp_ms"] is None
        assert out["first_observed_bar_time_ms"] == 1


# ═════════════════════════════════════════════════════════════════════════
# C2 — PDH insufficient: only 1-2 valid separation bars.
# ═════════════════════════════════════════════════════════════════════════

class TestC2PdhInsufficient:
    def test_retest_ready_false(self):
        bars = [
            _bar(1, close=106.00, low=105.50, high=106.50),  # low > PDH
            _bar(2, close=106.50, low=105.80, high=107.00),  # low > PDH
            # only 2 bars total, no contact -> visible_bar_count = 2 < 3
        ]
        out = evaluate_carry_in_separation(bars, "LONG", PDH)
        assert out["retest_ready"] is False
        assert out["reason"] == "VISIBLE_DISPLACEMENT_INCOMPLETE"
        assert out["visible_displacement_bar_count"] == 2
        assert out["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# C3 — PDL SHORT positive: first bar already below PDL + >=3 valid
# separation bars (high < PDL throughout), no contact yet.
# ═════════════════════════════════════════════════════════════════════════

class TestC3PdlShortPositive:
    def test_retest_ready_true(self):
        bars = [
            _bar(1, close=94.00, low=93.50, high=94.50),   # high < PDL
            _bar(2, close=93.50, low=93.00, high=94.20),   # high < PDL
            _bar(3, close=93.00, low=92.50, high=93.80),   # high < PDL
        ]
        out = evaluate_carry_in_separation(bars, "SHORT", PDL)
        assert out["retest_ready"] is True
        assert out["visible_displacement_bar_count"] == 3
        assert out["break_origin"] == "PREMARKET_CARRY_IN"
        assert out["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# C4 — PDL insufficient
# ═════════════════════════════════════════════════════════════════════════

class TestC4PdlInsufficient:
    def test_retest_ready_false(self):
        bars = [
            _bar(1, close=94.00, low=93.50, high=94.50),
        ]
        out = evaluate_carry_in_separation(bars, "SHORT", PDL)
        assert out["retest_ready"] is False
        assert out["reason"] == "VISIBLE_DISPLACEMENT_INCOMPLETE"
        assert out["visible_displacement_bar_count"] == 1


# ═════════════════════════════════════════════════════════════════════════
# C5 — LONG invalidated: sufficient separation, then a contact, then
# level_invalidation_closes consecutive wrong-side closes.
# ═════════════════════════════════════════════════════════════════════════

class TestC5LongInvalidated:
    def test_retest_ready_false_after_invalidation(self):
        bars = [
            _bar(1, close=106.00, low=105.50, high=106.50),  # sep 1
            _bar(2, close=106.50, low=105.80, high=107.00),  # sep 2
            _bar(3, close=107.00, low=106.20, high=107.50),  # sep 3 (>=3 ok)
            _bar(4, close=104.80, low=104.50, high=105.20),  # contact: low<=105
            _bar(5, close=104.00, low=103.50, high=104.50),  # wrong side 1 (close<105)
            _bar(6, close=103.50, low=103.00, high=104.00),  # wrong side 2 -> INVALIDATED
        ]
        out = evaluate_carry_in_separation(bars, "LONG", PDH)
        assert out["retest_ready"] is False
        assert out["reason"] == "STRUCTURE_INVALIDATED"
        assert out["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# C6 — SHORT invalidated: symmetric.
# ═════════════════════════════════════════════════════════════════════════

class TestC6ShortInvalidated:
    def test_retest_ready_false_after_invalidation(self):
        bars = [
            _bar(1, close=94.00, low=93.50, high=94.50),   # sep 1
            _bar(2, close=93.50, low=93.00, high=94.20),   # sep 2
            _bar(3, close=93.00, low=92.50, high=93.80),   # sep 3 (>=3 ok)
            _bar(4, close=95.20, low=94.80, high=95.50),   # contact: high>=95
            _bar(5, close=96.00, low=95.50, high=96.50),   # wrong side 1 (close>95)
            _bar(6, close=96.50, low=96.00, high=97.00),   # wrong side 2 -> INVALIDATED
        ]
        out = evaluate_carry_in_separation(bars, "SHORT", PDL)
        assert out["retest_ready"] is False
        assert out["reason"] == "STRUCTURE_INVALIDATED"
        assert out["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# C7 — timestamp honesty: break_timestamp_ms is None in EVERY carry-in
# outcome (ready, not-ready, invalidated).
# ═════════════════════════════════════════════════════════════════════════

class TestC7TimestampHonesty:
    def test_timestamp_none_when_ready(self):
        bars = [
            _bar(1, close=106.00, low=105.50, high=106.50),
            _bar(2, close=106.50, low=105.80, high=107.00),
            _bar(3, close=107.00, low=106.20, high=107.50),
        ]
        out = evaluate_carry_in_separation(bars, "LONG", PDH)
        assert out["retest_ready"] is True
        assert out["break_timestamp_ms"] is None
        assert out["break_origin"] == "PREMARKET_CARRY_IN"

    def test_timestamp_none_when_incomplete(self):
        bars = [_bar(1, close=106.00, low=105.50, high=106.50)]
        out = evaluate_carry_in_separation(bars, "LONG", PDH)
        assert out["retest_ready"] is False
        assert out["break_timestamp_ms"] is None

    def test_timestamp_none_when_invalidated(self):
        bars = [
            _bar(1, close=106.00, low=105.50, high=106.50),
            _bar(2, close=106.50, low=105.80, high=107.00),
            _bar(3, close=107.00, low=106.20, high=107.50),
            _bar(4, close=104.80, low=104.50, high=105.20),
            _bar(5, close=104.00, low=103.50, high=104.50),
            _bar(6, close=103.50, low=103.00, high=104.00),
        ]
        out = evaluate_carry_in_separation(bars, "LONG", PDH)
        assert out["retest_ready"] is False
        assert out["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# C8 — stateless: no memory between calls.
# ═════════════════════════════════════════════════════════════════════════

class TestC8Stateless:
    def test_no_memory_between_calls(self):
        pre_invalidation_bars = [
            _bar(1, close=106.00, low=105.50, high=106.50),
            _bar(2, close=106.50, low=105.80, high=107.00),
            _bar(3, close=107.00, low=106.20, high=107.50),
        ]
        invalidated_bars = pre_invalidation_bars + [
            _bar(4, close=104.80, low=104.50, high=105.20),  # contact
            _bar(5, close=104.00, low=103.50, high=104.50),  # wrong side 1
            _bar(6, close=103.50, low=103.00, high=104.00),  # wrong side 2 -> INVALIDATED
        ]

        result_before = evaluate_carry_in_separation(pre_invalidation_bars, "LONG", PDH)
        assert result_before["retest_ready"] is True

        result_invalidated = evaluate_carry_in_separation(invalidated_bars, "LONG", PDH)
        assert result_invalidated["retest_ready"] is False
        assert result_invalidated["reason"] == "STRUCTURE_INVALIDATED"

        # Re-evaluating the exact same pre-invalidation bars again must
        # still return True — nothing was mutated or cached by the
        # previous call.
        result_reevaluated = evaluate_carry_in_separation(pre_invalidation_bars, "LONG", PDH)
        assert result_reevaluated["retest_ready"] is True
        assert result_reevaluated == result_before


# ═════════════════════════════════════════════════════════════════════════
# Misc guards
# ═════════════════════════════════════════════════════════════════════════

class TestMiscGuards:
    def test_no_premarket_bars(self):
        out = evaluate_carry_in_separation(None, "LONG", PDH)
        assert out["retest_ready"] is False
        assert out["reason"] == "NO_PREMARKET_DATA"
        assert out["break_timestamp_ms"] is None

    def test_empty_premarket_bars(self):
        out = evaluate_carry_in_separation([], "LONG", PDH)
        assert out["retest_ready"] is False
        assert out["reason"] == "NO_PREMARKET_DATA"

    def test_unsupported_direction(self):
        out = evaluate_carry_in_separation(
            [_bar(1, close=200.00, low=199.0, high=201.0)], "SIDEWAYS", PDH,
        )
        assert out["retest_ready"] is False
        assert out["reason"] == "UNSUPPORTED_DIRECTION"

    def test_not_actually_carry_in_is_defensive_false(self):
        """If the first bar is NOT beyond the level, this predicate
        (meant only for already-classified carry-in levels) reports
        NOT_CARRY_IN rather than silently proceeding."""
        bars = [_bar(1, close=100.00, low=99.5, high=100.5)]  # below PDH
        out = evaluate_carry_in_separation(bars, "LONG", PDH)
        assert out["retest_ready"] is False
        assert out["reason"] == "NOT_CARRY_IN"

    def test_never_mutates_input_list(self):
        bars = [
            _bar(3, close=107.00, low=106.20, high=107.50),
            _bar(1, close=106.00, low=105.50, high=106.50),
            _bar(2, close=106.50, low=105.80, high=107.00),
        ]
        original_order = list(bars)
        evaluate_carry_in_separation(bars, "LONG", PDH)
        assert bars == original_order  # unsorted input untouched

    def test_first_observed_bar_never_called_break_candle(self):
        """The output must not use break-candle terminology for the
        first observed bar — only first_observed_bar_time_ms."""
        bars = [
            _bar(1, close=106.00, low=105.50, high=106.50),
            _bar(2, close=106.50, low=105.80, high=107.00),
            _bar(3, close=107.00, low=106.20, high=107.50),
        ]
        out = evaluate_carry_in_separation(bars, "LONG", PDH)
        assert "break_candle" not in out
        assert "break_candle_index" not in out
        assert "first_observed_bar_time_ms" in out
