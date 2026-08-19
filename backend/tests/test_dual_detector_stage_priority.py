"""Tests for DualSignalDetector's explicit semantic stage-priority ranking.

Replaces the old ``len(stage_context)`` proxy (audited as semantically
fragile: dict size reflects incidental field accumulation, not real
pipeline progress) with an explicit priority table built from the real
failed_stage values LiveSignalDetector actually produces (see
_STAGE_LABELS in signal_detector.py), plus a break_time_ms tie-break
when both sides sit at the same stage.

SIGNAL priority (LONG first, then SHORT) is unchanged and not
re-tested here beyond what test_both_direction.py / test_skip_dead_
breaks.py already cover.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from trading_lab.live.dual_signal_detector import DualSignalDetector
from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)
from trading_lab.live.session_builder_live import LiveSessionBuilder


# ── Helpers ──────────────────────────────────────────────────────────────────


def _no_setup(failed_stage, direction, break_time_ms=None, extra_keys=0):
    """Build a NO_SETUP SignalResult with a given failed_stage and an
    optional pile of extra stage_context keys (to prove ranking never
    depends on dict size)."""
    ctx = {"direction": direction}
    if break_time_ms is not None:
        ctx["break_time_ms"] = break_time_ms
        ctx["break_bar_index"] = 1
        ctx["break_close"] = 100.0
        ctx["break_level"] = 100.0
    for i in range(extra_keys):
        ctx[f"noise_field_{i}"] = i
    return SignalResult(status=SignalStatus.NO_SETUP, failed_stage=failed_stage,
                         stage_context=ctx)


def _dual_with_mocked_results(long_result, short_result):
    long_sd = MagicMock(spec=LiveSignalDetector)
    long_sd.evaluate.return_value = long_result
    short_sd = MagicMock(spec=LiveSignalDetector)
    short_sd.evaluate.return_value = short_result
    return DualSignalDetector(long_sd, short_sd)


# ═════════════════════════════════════════════════════════════════════════
# 1. Old entry-waiting (more advanced) beats a brand new break (less
#    advanced) — proves this is NOT simply "newest break always wins".
# ═════════════════════════════════════════════════════════════════════════


class TestAdvancedOldSetupBeatsFreshBreak:
    def test_old_short_waiting_for_entry_beats_new_long_displacement_building(self):
        old_short = _no_setup("NO_QUALIFYING_REJECTION_CANDLE", "SHORT", break_time_ms=1_000)
        new_long = _no_setup("DISPLACEMENT_TOO_SHORT", "LONG", break_time_ms=50_000)

        dual = _dual_with_mocked_results(new_long, old_short)
        result = dual.evaluate({"candles": []})

        assert result.failed_stage == "NO_QUALIFYING_REJECTION_CANDLE"
        assert result.stage_context.get("direction") == "SHORT"


# ═════════════════════════════════════════════════════════════════════════
# 2. Same semantic stage → newest break wins (both directions)
# ═════════════════════════════════════════════════════════════════════════


class TestSameStageNewestBreakWins:
    def test_long_newer_break_wins(self):
        long_r = _no_setup("RETEST_NOT_FOUND", "LONG", break_time_ms=5_000)
        short_r = _no_setup("RETEST_NOT_FOUND", "SHORT", break_time_ms=3_000)

        dual = _dual_with_mocked_results(long_r, short_r)
        result = dual.evaluate({"candles": []})

        assert result.stage_context.get("direction") == "LONG"

    def test_short_newer_break_wins_when_timestamps_inverted(self):
        long_r = _no_setup("RETEST_NOT_FOUND", "LONG", break_time_ms=3_000)
        short_r = _no_setup("RETEST_NOT_FOUND", "SHORT", break_time_ms=5_000)

        dual = _dual_with_mocked_results(long_r, short_r)
        result = dual.evaluate({"candles": []})

        assert result.stage_context.get("direction") == "SHORT"


# ═════════════════════════════════════════════════════════════════════════
# 3. One side has no break at all
# ═════════════════════════════════════════════════════════════════════════


class TestNoBreakSideLoses:
    def test_short_with_real_break_beats_long_break_not_found(self):
        long_r = _no_setup("BREAK_NOT_FOUND", "LONG")  # no break_time_ms
        short_r = _no_setup("DISPLACEMENT_TOO_SHORT", "SHORT", break_time_ms=1_000)

        dual = _dual_with_mocked_results(long_r, short_r)
        result = dual.evaluate({"candles": []})

        assert result.stage_context.get("direction") == "SHORT"
        assert result.failed_stage == "DISPLACEMENT_TOO_SHORT"


# ═════════════════════════════════════════════════════════════════════════
# 4. Noisy stage_context (many extra keys) must never affect ranking
# ═════════════════════════════════════════════════════════════════════════


class TestKeyCountNeverMatters:
    def test_smaller_context_wins_on_recency_despite_fewer_keys(self):
        """Both at the SAME stage; the side with far fewer stage_context
        keys must still win purely because its break is more recent —
        proving the ranking never falls back to len(stage_context)."""
        noisy_old = _no_setup("NO_QUALIFYING_REJECTION_CANDLE", "SHORT",
                               break_time_ms=1_000, extra_keys=20)
        lean_new = _no_setup("NO_QUALIFYING_REJECTION_CANDLE", "LONG",
                              break_time_ms=99_000, extra_keys=0)

        assert len(noisy_old.stage_context) > len(lean_new.stage_context)

        dual = _dual_with_mocked_results(lean_new, noisy_old)
        result = dual.evaluate({"candles": []})

        assert result.stage_context.get("direction") == "LONG"

    def test_advanced_stage_wins_despite_far_fewer_keys(self):
        """A higher-tier stage with a tiny context must still beat a
        lower-tier stage padded with many extra keys."""
        lean_advanced = _no_setup("NO_QUALIFYING_REJECTION_CANDLE", "SHORT",
                                   break_time_ms=1_000, extra_keys=0)
        noisy_early = _no_setup("DISPLACEMENT_TOO_SHORT", "LONG",
                                 break_time_ms=99_000, extra_keys=20)

        assert len(noisy_early.stage_context) > len(lean_advanced.stage_context)

        dual = _dual_with_mocked_results(noisy_early, lean_advanced)
        result = dual.evaluate({"candles": []})

        assert result.stage_context.get("direction") == "SHORT"


# ═════════════════════════════════════════════════════════════════════════
# 5. Regression: TSLA — old invalidated SHORT must not mask new LONG
# ═════════════════════════════════════════════════════════════════════════


MS_0930 = 1786455000000


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _c(t, o, h, l, cl):
    return {"time_ms": t, "open": o, "high": h, "low": l, "close": cl, "volume": 1000}


def _orb_bars():
    return [_c(_ms(i), 100.0, 101.0, 99.0, 100.0) for i in range(5)]


def _build_session(bars):
    sb = LiveSessionBuilder("TSLA")
    for b in bars:
        sb.add_bar(b)
    return sb.current_session()


class TestTslaRegressionInvalidatedShortDoesNotMaskLong:
    def test_dual_detector_shows_new_long_not_dead_short(self):
        """End-to-end, real pipeline (no mocking): old SHORT break ->
        displacement -> weak retest -> invalidated exactly at the
        first_retest boundary (the T20 fix scenario) -> new LONG break
        just formed. DualSignalDetector must show the live LONG, not
        the dead SHORT."""
        bars = _orb_bars()
        bars.append(_c(_ms(5), 99.0, 99.2, 97.5, 97.8))       # break SHORT
        bars.append(_c(_ms(6), 97.8, 97.9, 97.3, 97.5))       # disp 1/3
        bars.append(_c(_ms(7), 97.5, 97.6, 97.0, 97.2))       # disp 2/3
        bars.append(_c(_ms(8), 97.2, 97.3, 96.8, 97.0))       # disp 3/3
        bars.append(_c(_ms(9), 98.90, 99.15, 98.80, 99.05))   # weak retest attempt
        bars.append(_c(_ms(10), 99.05, 99.6, 98.95, 99.4))    # invalidation trigger
        bars.append(_c(_ms(11), 99.4, 101.8, 99.3, 101.5))    # new LONG break

        dual = DualSignalDetector(
            LiveSignalDetector(symbol="TSLA", direction="LONG", tick_size=0.01),
            LiveSignalDetector(symbol="TSLA", direction="SHORT", tick_size=0.01),
        )
        result = dual.evaluate(_build_session(bars))

        assert result.stage_context.get("direction") == "LONG"
        assert result.stage_context.get("break_bar_index") == 11
