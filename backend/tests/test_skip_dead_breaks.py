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

    def test_retest_too_early_is_skipped_even_without_orb_return(self):
        """RETEST_BEFORE_DISPLACEMENT is structurally terminal for that
        break_idx (first_retest_contact_index is a fixed historical fact
        once found) — Fix A (2026-08-21 audit, Gap A) skips it
        unconditionally, without waiting for an ORB reentry. This
        supersedes the pre-fix expectation (break #1 used to be kept
        "active" indefinitely here purely because price never closed
        back inside the ORB, even though stage 3 had already proven it
        dead). Each dead candidate is skipped in turn until a break is
        reached whose displacement genuinely still has room to build
        (RETEST_NOT_FOUND, not yet disproven) — here that lands on the
        break at index 7.
        """
        bars = _orb_bars_short()
        # Break #1 at index 5
        bars.append({"time_ms": _ms(5), "open": 99.0, "high": 99.3,
                      "low": 98.0, "close": 98.5, "volume": 1500})
        # Retest too early — touches 99 immediately → dead, regardless of ORB
        bars.append({"time_ms": _ms(6), "open": 98.5, "high": 99.3,
                      "low": 98.0, "close": 98.8, "volume": 1200})
        # Still outside ORB (close < 99)
        bars.append({"time_ms": _ms(7), "open": 98.8, "high": 99.0,
                      "low": 98.2, "close": 98.4, "volume": 1000})

        sd = _make_short_detector()
        result = sd.evaluate(_build_session(bars))
        ctx = result.stage_context or {}
        # Break #1 (and the immediately-retested break #2 at index 6) are
        # both abandoned; the detector lands on the break at index 7,
        # which is genuinely still building (no data yet to prove or
        # disprove its displacement).
        assert ctx.get("break_bar_index") == 7
        assert result.failed_stage == "RETEST_NOT_FOUND"


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

        # First: evaluate with just break #1 + its immediate (too-early)
        # retest bar, and nothing else. Under Fix A (2026-08-21 audit,
        # Gap A) RETEST_BEFORE_DISPLACEMENT is abandoned unconditionally
        # — break #1 is already structurally dead here, with no later
        # break yet available in this truncated slice, so this now
        # correctly reports BREAK_NOT_FOUND rather than staying "active"
        # on a break stage 3 has already disproven.
        r1 = sd.evaluate(_build_session(bars[:7]))
        assert r1.status == SignalStatus.NO_SETUP
        assert r1.failed_stage == "BREAK_NOT_FOUND"
        key1_break_idx = (r1.stage_context or {}).get("break_bar_index")

        # Second: evaluate with all bars — break #2 is found directly.
        r2 = sd.evaluate(_build_session(bars))
        key2_break_idx = (r2.stage_context or {}).get("break_bar_index")

        assert key1_break_idx is None  # break #1 already abandoned
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


# ═══════════════════════════════════════════════════════════════════════
# T11 (2026-08-21 audit, Gap A) — direct, definitive proof.
#
# Break A ends in DISPLACEMENT_TOO_SHORT (structurally, permanently dead
# for that exact break_idx — first_retest_contact_index is a fixed
# historical fact once found) WITHOUT price ever closing back inside the
# ORB twice in a row (the independent, stricter condition _is_break_dead()
# checks). A genuinely new, fully valid Break B appears afterward and
# builds a complete BDRR sequence all the way to SIGNAL.
#
# Fix A (signal_detector.py::evaluate()) must abandon Break A
# unconditionally on DISPLACEMENT_TOO_SHORT/RETEST_BEFORE_DISPLACEMENT,
# without requiring _is_break_dead(). Pre-fix, the old code required
# BOTH conditions, so with no ORB reentry present here, find_break() was
# never given a reason to advance past Break A, and Break B could never
# be reached — not even to observe it "building", let alone as SIGNAL.
# ═══════════════════════════════════════════════════════════════════════


_LONG_LEVEL_T11 = 101.00
_SHORT_LEVEL_T11 = 99.00


def _mirror_t11(bar_long, t):
    """Mirror a LONG bar (from test_signal_detector.py's canonical,
    already-validated fixture) into a SHORT bar around orb_low=99, so
    the exact same rejection geometry (body_ratio, wick_ratio,
    favorable_close_location, wick penetration) applies unchanged."""
    def m(x):
        return round(_SHORT_LEVEL_T11 - (x - _LONG_LEVEL_T11), 4)
    return {
        "time_ms": t,
        "open": m(bar_long["open"]),
        "high": m(bar_long["low"]),   # low reflects to high
        "low": m(bar_long["high"]),   # high reflects to low
        "close": m(bar_long["close"]),
        "volume": 1000,
    }


# Canonical LONG break/displacement/rejection fixture (test_signal_detector.py),
# reused here mirrored around orb_low=99 for Break B.
_T11_LONG_BREAK = {"open": 100.80, "high": 101.60, "low": 100.70, "close": 101.50}
_T11_LONG_DISP = [
    {"open": 101.55, "high": 101.80, "low": 101.20, "close": 101.60},
    {"open": 101.60, "high": 101.90, "low": 101.30, "close": 101.70},
    {"open": 101.70, "high": 101.85, "low": 101.10, "close": 101.40},
]
_T11_LONG_REJ = {"open": 101.10, "high": 101.30, "low": 100.80, "close": 101.20}


def _t11_build_bars():
    bars = _orb_bars_short()  # ORB high=101.00, low=99.00 (5 bars)
    # Break A at idx5 — structurally dead: only 1 displacement bar (idx6)
    # before the retest contact (idx7), yet idx7 closes back at 98.95,
    # i.e. STILL below orb_low=99 — no ORB reentry occurs at all.
    bars.append({"time_ms": _ms(5), "open": 99.0, "high": 99.3,
                 "low": 98.0, "close": 98.5, "volume": 1000})       # idx5 break A
    bars.append({"time_ms": _ms(6), "open": 98.5, "high": 98.8,
                 "low": 98.0, "close": 98.3, "volume": 1000})       # idx6 disp 1/3 (too few)
    bars.append({"time_ms": _ms(7), "open": 98.3, "high": 99.05,
                 "low": 98.6, "close": 98.95, "volume": 1000})      # idx7 contact -> DISPLACEMENT_TOO_SHORT

    # Break B at idx8 — a genuinely new, fully valid BDRR sequence,
    # reusing the canonical mirrored fixture verbatim.
    bars.append(_mirror_t11(_T11_LONG_BREAK, _ms(8)))               # idx8 break B
    bars += [_mirror_t11(b, _ms(9 + i)) for i, b in enumerate(_T11_LONG_DISP)]  # idx9-11 disp B
    bars.append(_mirror_t11(_T11_LONG_REJ, _ms(12)))                # idx12 rejection B -> SIGNAL B
    return bars


class TestT11GapADirectProof:
    def test_dead_break_a_does_not_monopolize_scanning_and_signal_b_is_found(self):
        bars = _t11_build_bars()
        sd = _make_short_detector()
        result = sd.evaluate(_build_session(bars))

        break_a_ts = _ms(5)
        break_b_ts = _ms(8)
        expected_setup_key_b = f"SHORT:ORB_LOW:{break_b_ts}"

        # The decisive assertion: the detector must reach a genuine
        # SIGNAL, and it must belong to Break B (idx8), not be stuck
        # reporting NO_SETUP/DISPLACEMENT_TOO_SHORT anchored on the dead
        # Break A (idx5) forever.
        assert result.status == SignalStatus.SIGNAL, (
            f"expected a SIGNAL on Break B, got {result.status} "
            f"(failed_stage={result.failed_stage!r}) — the detector is "
            f"still monopolized by the dead Break A"
        )
        assert result.setup_key == expected_setup_key_b, (
            f"expected setup_key for Break B ({expected_setup_key_b!r}), "
            f"got {result.setup_key!r} instead"
        )
        ctx = result.stage_context or {}
        assert ctx.get("break_bar_index") == 8
        assert ctx.get("break_bar_index") != 5

