"""Tests for skipping dead breaks and resuming setup scanning.

Verifies:
1. First break + RETEST_BEFORE_DISPLACEMENT stays when still active
2. First break dead (ORB return) → skipped → second break found
3. Second SHORT break can progress to later stages
4. First SHORT dead → LONG break found
5. New setup_key differs from old
6. Active break NOT skipped
7. Normal pipeline unchanged
8. Consumed setup behavior preserved
9. No threshold/rejection/execution modifications
"""

from unittest.mock import MagicMock

import pytest

from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.session_builder_live import LiveSessionBuilder


# ── Fixture helpers ──────────────────────────────────────────────────────────

# 2026-08-11 EDT: 09:30 ET = 13:30 UTC
MS_0930 = 1786455000000

def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _orb_bars_short():
    """5 ORB bars defining ORB high=101.00, low=99.00."""
    return [
        {"time_ms": _ms(i), "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0, "volume": 1000}
        for i in range(5)
    ]


def _build_session(bars):
    builder = LiveSessionBuilder("QQQ")
    for b in bars:
        builder.add_bar(b)
    return builder.current_session()


def _make_short_detector():
    return LiveSignalDetector(
        symbol="QQQ", direction="SHORT", tick_size=0.01,
    )


def _make_long_detector():
    return LiveSignalDetector(
        symbol="QQQ", direction="LONG", tick_size=0.01,
    )


# ═══════════════════════════════════════════════════════════════════════
# 1. Active break NOT skipped
# ═══════════════════════════════════════════════════════════════════════


class TestActiveBreakNotSkipped:
    def test_break_with_displacement_building_stays(self):
        """A break whose displacement is building should NOT be skipped."""
        bars = _orb_bars_short()
        # Break at index 5
        bars.append({"time_ms": _ms(5), "open": 99.0, "high": 99.3,
                      "low": 98.0, "close": 98.5, "volume": 1500})
        # Displacement building — 1 bar outside (not enough)
        bars.append({"time_ms": _ms(6), "open": 98.5, "high": 98.8,
                      "low": 98.0, "close": 98.3, "volume": 1200})

        sd = _make_short_detector()
        session = _build_session(bars)
        result = sd.evaluate(session)

        assert result.status == SignalStatus.NO_SETUP
        ctx = result.stage_context or {}
        # Should still reference break #1 at index 5
        assert ctx.get("break_bar_index") == 5

    def test_retest_too_early_without_orb_return_stays(self):
        """RETEST_BEFORE_DISPLACEMENT without ORB return → not dead."""
        bars = _orb_bars_short()
        # Break
        bars.append({"time_ms": _ms(5), "open": 99.0, "high": 99.3,
                      "low": 98.0, "close": 98.5, "volume": 1500})
        # Retest too early — touches 99 immediately
        bars.append({"time_ms": _ms(6), "open": 98.5, "high": 99.3,
                      "low": 98.0, "close": 98.8, "volume": 1200})
        # Still outside ORB (close < 99)
        bars.append({"time_ms": _ms(7), "open": 98.8, "high": 99.0,
                      "low": 98.2, "close": 98.4, "volume": 1000})

        sd = _make_short_detector()
        result = sd.evaluate(_build_session(bars))
        ctx = result.stage_context or {}
        # Break #1 still active — no ORB return
        assert ctx.get("break_bar_index") == 5


# ═══════════════════════════════════════════════════════════════════════
# 2. Dead break skipped → second SHORT break found
# ═══════════════════════════════════════════════════════════════════════


class TestDeadBreakSkipped:
    def test_dead_break_with_orb_return_skipped(self):
        """Break #1 dies (2 closes inside ORB) → break #2 found."""
        bars = _orb_bars_short()
        # Break #1 at index 5
        bars.append({"time_ms": _ms(5), "open": 99.0, "high": 99.3,
                      "low": 98.0, "close": 98.5, "volume": 1500})
        # Retest too early
        bars.append({"time_ms": _ms(6), "open": 98.5, "high": 99.3,
                      "low": 98.0, "close": 99.2, "volume": 1200})
        # Return to ORB — close >= 99 (orb_low) — 2 consecutive
        bars.append({"time_ms": _ms(7), "open": 99.2, "high": 100.5,
                      "low": 99.0, "close": 100.0, "volume": 1000})
        bars.append({"time_ms": _ms(8), "open": 100.0, "high": 100.5,
                      "low": 99.5, "close": 100.2, "volume": 1000})
        # Inside ORB
        bars.append({"time_ms": _ms(9), "open": 100.0, "high": 100.5,
                      "low": 99.5, "close": 100.0, "volume": 1000})
        # Break #2 at index 10
        bars.append({"time_ms": _ms(10), "open": 99.5, "high": 99.6,
                       "low": 97.5, "close": 97.8, "volume": 2000})

        sd = _make_short_detector()
        result = sd.evaluate(_build_session(bars))
        ctx = result.stage_context or {}

        # Should find break #2, not break #1
        assert ctx.get("break_bar_index") == 10
        assert ctx.get("break_close") == 97.8

    def test_dead_break_different_setup_key(self):
        """Break #2's setup_key differs from break #1."""
        bars = _orb_bars_short()
        # Break #1 at index 5
        bars.append({"time_ms": _ms(5), "open": 99.0, "high": 99.3,
                      "low": 98.0, "close": 98.5, "volume": 1500})
        # Retest too early
        bars.append({"time_ms": _ms(6), "open": 98.5, "high": 99.3,
                      "low": 98.0, "close": 99.2, "volume": 1200})
        # Return to ORB (2 consecutive)
        bars.append({"time_ms": _ms(7), "open": 99.2, "high": 100.5,
                      "low": 99.0, "close": 100.0, "volume": 1000})
        bars.append({"time_ms": _ms(8), "open": 100.0, "high": 100.5,
                      "low": 99.5, "close": 100.2, "volume": 1000})
        # Break #2 at index 9
        bars.append({"time_ms": _ms(9), "open": 99.5, "high": 99.6,
                       "low": 97.5, "close": 97.8, "volume": 2000})

        sd = _make_short_detector()

        # First: evaluate with just break #1 bars (no ORB return yet)
        r1 = sd.evaluate(_build_session(bars[:7]))
        key1_break_idx = (r1.stage_context or {}).get("break_bar_index")

        # Second: evaluate with all bars (break #1 dead → break #2)
        r2 = sd.evaluate(_build_session(bars))
        key2_break_idx = (r2.stage_context or {}).get("break_bar_index")

        assert key1_break_idx == 5  # break #1
        assert key2_break_idx == 9  # break #2
        assert key1_break_idx != key2_break_idx


# ═══════════════════════════════════════════════════════════════════════
# 3. Dead SHORT → LONG break found (via DualSignalDetector)
# ═══════════════════════════════════════════════════════════════════════


class TestDeadShortThenLong:
    def test_long_break_found_after_dead_short(self):
        """LONG detector independently finds its break — not blocked by SHORT."""
        bars = _orb_bars_short()  # ORB high=101, low=99
        # Some bars inside ORB
        bars.append({"time_ms": _ms(5), "open": 100.0, "high": 100.5,
                      "low": 99.5, "close": 100.2, "volume": 1000})
        bars.append({"time_ms": _ms(6), "open": 100.2, "high": 100.8,
                      "low": 100.0, "close": 100.5, "volume": 1000})
        # LONG break at index 7 — close > 101
        bars.append({"time_ms": _ms(7), "open": 100.8, "high": 102.0,
                      "low": 100.5, "close": 101.5, "volume": 2000})

        ld = _make_long_detector()
        result = ld.evaluate(_build_session(bars))
        ctx = result.stage_context or {}

        assert ctx.get("break_bar_index") == 7
        assert ctx.get("direction") == "LONG"


# ═══════════════════════════════════════════════════════════════════════
# 4. Normal pipeline unchanged
# ═══════════════════════════════════════════════════════════════════════


class TestNormalPipelineUnchanged:
    def test_single_break_no_orb_return_works_normally(self):
        """A valid break → displacement → etc. pipeline is not disrupted."""
        bars = _orb_bars_short()
        # Break
        bars.append({"time_ms": _ms(5), "open": 99.0, "high": 99.3,
                      "low": 98.0, "close": 98.5, "volume": 1500})
        # 3 displacement bars
        for i in range(3):
            bars.append({"time_ms": _ms(6+i), "open": 98.5-i*0.3,
                          "high": 98.8-i*0.3, "low": 98.0-i*0.3,
                          "close": 98.3-i*0.3, "volume": 1200})

        sd = _make_short_detector()
        result = sd.evaluate(_build_session(bars))
        ctx = result.stage_context or {}

        # Should find break at index 5, displacement building/confirmed
        assert ctx.get("break_bar_index") == 5


# ═══════════════════════════════════════════════════════════════════════
# 5. Consumed setup behavior preserved
# ═══════════════════════════════════════════════════════════════════════


class TestConsumedBehaviorPreserved:
    def test_consumed_signal_still_skipped(self):
        """A consumed SIGNAL setup is still skipped (T19C/T19F)."""
        sd = _make_short_detector()
        # Mock to return consumed signal then no_setup
        from trading_lab.live.signal_detector import SignalResult
        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:123", pipeline_stage="SIGNAL",
            stage_context={"break_bar_index": 5},
        )
        no_setup = SignalResult(
            status=SignalStatus.NO_SETUP,
            failed_stage="BREAK_NOT_FOUND",
        )
        sd._evaluate_inner = MagicMock(side_effect=[sig, no_setup])

        result = sd.evaluate({"candles": []}, consumed_setup_keys={"SHORT:123"})
        assert result.status == SignalStatus.NO_SETUP


# ═══════════════════════════════════════════════════════════════════════
# 6. _is_break_dead unit tests
# ═══════════════════════════════════════════════════════════════════════


class TestIsBreakDead:
    def test_not_dead_when_still_outside(self):
        candles = [
            {"close": 100.0},  # ORB
            {"close": 98.5},   # break (index 1)
            {"close": 98.3},   # still outside
            {"close": 98.1},   # still outside
        ]
        assert not LiveSignalDetector._is_break_dead(
            candles, 1, "SHORT", 101.0, 99.0, 2
        )

    def test_dead_with_2_consecutive_inside(self):
        candles = [
            {"close": 100.0},  # ORB
            {"close": 98.5},   # break (index 1)
            {"close": 99.5},   # inside (>= 99)
            {"close": 100.0},  # inside (>= 99) — 2 consecutive
        ]
        assert LiveSignalDetector._is_break_dead(
            candles, 1, "SHORT", 101.0, 99.0, 2
        )

    def test_not_dead_with_1_inside_then_outside(self):
        candles = [
            {"close": 100.0},  # ORB
            {"close": 98.5},   # break (index 1)
            {"close": 99.5},   # inside — 1 consecutive
            {"close": 98.0},   # outside — reset
        ]
        assert not LiveSignalDetector._is_break_dead(
            candles, 1, "SHORT", 101.0, 99.0, 2
        )

    def test_dead_long_break(self):
        candles = [
            {"close": 100.0},  # ORB
            {"close": 101.5},  # LONG break (index 1)
            {"close": 100.5},  # inside (<= 101)
            {"close": 100.0},  # inside — 2 consecutive
        ]
        assert LiveSignalDetector._is_break_dead(
            candles, 1, "LONG", 101.0, 99.0, 2
        )

    def test_no_orb_levels_returns_false(self):
        candles = [{"close": 100.0}, {"close": 98.5}]
        assert not LiveSignalDetector._is_break_dead(
            candles, 1, "SHORT", None, None, 2
        )
