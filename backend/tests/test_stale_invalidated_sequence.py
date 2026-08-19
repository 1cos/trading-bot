"""Tests for the stale-invalidated-sequence fix.

Root cause (forensic audit on live TSLA/TSLL 2026-08-19 data):
``validate_sequence()`` correctly detects when a sequence should die —
including the case where invalidation happens AFTER the retest has
already begun (``max_valid_index >= first_retest_contact_index``, the
common case once price has touched the level at least once). But the
old code in ``_evaluate_inner()`` only surfaced this as
``SEQUENCE_INVALIDATED`` when invalidation happened BEFORE the retest
began. Once retest had begun, it silently froze the retest window at
``max_valid_index`` and kept re-evaluating the SAME break forever,
producing the same ``NO_QUALIFYING_REJECTION_CANDLE`` ("RETEST — NO
ENTRY") result on every subsequent bar — even after price closed
decisively on the opposite side of the ORB (a clear new breakout in
the other direction).

``evaluate()``'s outer dead-break-skip loop only advances past a break
when ``failed_stage`` is ``RETEST_BEFORE_DISPLACEMENT`` or
``DISPLACEMENT_TOO_SHORT`` — never for the frozen-retest case, so the
same break kept being re-selected by ``find_break`` indefinitely.

Fix: ``_evaluate_inner()`` now always returns ``SEQUENCE_INVALIDATED``
when ``validate_sequence()`` reports INVALIDATED, regardless of
whether the retest had already begun. ``evaluate()``'s outer loop now
also treats ``SEQUENCE_INVALIDATED`` as a dead break to skip past,
advancing ``skip_before`` and searching for the next break — exactly
as it already did for the two earlier-stage cases.

No changes to break rules, displacement, rejection geometry,
TWO_CANDLE, consumed-setup behavior, or PWA rendering.
"""

from __future__ import annotations

from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.session_builder_live import LiveSessionBuilder


# ── Fixture helpers ──────────────────────────────────────────────────────────

# 2026-08-11 EDT: 09:30 ET = 13:30 UTC (same anchor as test_skip_dead_breaks.py)
MS_0930 = 1786455000000


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _c(t, o, h, l, cl):
    return {"time_ms": t, "open": o, "high": h, "low": l, "close": cl, "volume": 1000}


def _orb_bars():
    """5 ORB bars defining ORB high=101.00, low=99.00 (SHORT/LONG mirror)."""
    return [_c(_ms(i), 100.0, 101.0, 99.0, 100.0) for i in range(5)]


def _build_session(bars):
    sb = LiveSessionBuilder("TSLA")
    for b in bars:
        sb.add_bar(b)
    return sb.current_session()


def _short_detector():
    return LiveSignalDetector(symbol="TSLA", direction="SHORT", tick_size=0.01)


def _long_detector():
    return LiveSignalDetector(symbol="TSLA", direction="LONG", tick_size=0.01)


# ═══════════════════════════════════════════════════════════════════════════
# 1. SHORT stale invalidated → new LONG break found (TSLA reproduction)
# ═══════════════════════════════════════════════════════════════════════════


def _tsla_style_bars():
    """Reproduces the TSLA shape: break SHORT -> displacement -> weak
    retest (no qualifying entry) -> 2 consecutive closes that invalidate
    the sequence (the second one decisively above ORB high, mirroring
    the real opposite-side breakout) -> a genuine new LONG break."""
    bars = _orb_bars()
    bars.append(_c(_ms(5), 99.0, 99.2, 97.5, 97.8))      # idx5 break SHORT (close < 99)
    bars.append(_c(_ms(6), 97.8, 97.9, 97.3, 97.5))      # idx6 displacement 1/3
    bars.append(_c(_ms(7), 97.5, 97.6, 97.0, 97.2))      # idx7 displacement 2/3
    bars.append(_c(_ms(8), 97.2, 97.3, 96.8, 97.0))      # idx8 displacement 3/3
    # idx9: weak retest attempt — touches the level (high >= 99) but
    # geometry fails SINGLE_CANDLE (wick_ratio 0.10/0.35 = 0.286 < 0.47).
    bars.append(_c(_ms(9), 98.90, 99.15, 98.80, 99.05))
    # idx10: close back >= orb_low -> 2nd consecutive "inside" close
    # (idx9's close 99.05 was already >= 99, so idx10 completes the
    # consecutive_orb_closes=2 threshold) -> INVALIDATED, with
    # max_valid_index == first_retest_contact_index (the exact
    # boundary case the old code silently absorbed into a frozen window).
    bars.append(_c(_ms(10), 99.05, 99.6, 98.95, 99.4))
    # idx11: genuine new LONG break — closes decisively above ORB high,
    # mirroring TSLA's real opposite-side breakout.
    bars.append(_c(_ms(11), 99.4, 101.8, 99.3, 101.5))
    return bars


class TestStaleShortInvalidatedNewLongBreakFound:
    def test_short_detector_stops_reporting_old_break(self):
        """1. The SHORT detector must NOT keep returning the old break."""
        bars = _tsla_style_bars()
        sd = _short_detector()
        result = sd.evaluate(_build_session(bars))

        # Must not still be anchored on the dead break at index 5.
        ctx = result.stage_context or {}
        assert ctx.get("break_bar_index") != 5
        # With no other SHORT break available after skipping the dead
        # one, the SHORT detector correctly reports BREAK_NOT_FOUND —
        # not a repeated NO_QUALIFYING_REJECTION_CANDLE on the old break.
        assert result.failed_stage == "BREAK_NOT_FOUND"

    def test_old_setup_marked_invalidated_not_frozen(self):
        """2. The dead break is recognized as invalidated (not silently
        frozen into an endlessly-repeating retest window). We verify
        this indirectly: the SHORT detector no longer references
        break index 5 at all once evaluate() has skipped past it."""
        bars = _tsla_style_bars()
        sd = _short_detector()
        result = sd.evaluate(_build_session(bars))
        ctx = result.stage_context or {}
        assert ctx.get("break_bar_index") != 5
        assert "invalidation_index" not in ctx  # not stuck returning the frozen SEQUENCE_INVALIDATED itself

    def test_long_detector_finds_new_break(self):
        """3. The LONG detector independently finds the new breakout."""
        bars = _tsla_style_bars()
        ld = _long_detector()
        result = ld.evaluate(_build_session(bars))

        ctx = result.stage_context or {}
        assert ctx.get("break_bar_index") == 11
        assert ctx.get("direction") == "LONG"
        assert ctx.get("break_close") == 101.5


# ═══════════════════════════════════════════════════════════════════════════
# 2. Same-direction later break: old SHORT invalidated -> new SHORT found
# ═══════════════════════════════════════════════════════════════════════════


class TestSameDirectionLaterBreakFound:
    def test_short_detector_advances_to_second_short_break(self):
        bars = _tsla_style_bars()[:-1]  # drop the LONG break at idx11
        # idx11: consolidation back inside the ORB band (not a break —
        # close stays between orb_low and orb_high).
        bars.append(_c(_ms(11), 99.4, 100.3, 99.3, 100.0))
        # idx12: a genuinely NEW SHORT break, well after the first one.
        bars.append(_c(_ms(12), 100.0, 100.2, 97.0, 97.3))
        # idx13-15: full displacement for break #2.
        bars.append(_c(_ms(13), 97.3, 97.4, 96.8, 97.0))
        bars.append(_c(_ms(14), 97.0, 97.1, 96.5, 96.7))
        bars.append(_c(_ms(15), 96.7, 96.8, 96.2, 96.4))

        sd = _short_detector()
        result = sd.evaluate(_build_session(bars))
        ctx = result.stage_context or {}

        # The detector advanced past break #1 (index 5) and found
        # break #2 (index 12), with displacement fully built.
        assert ctx.get("break_bar_index") == 12
        assert ctx.get("break_close") == 97.3
        assert ctx.get("displacement_bars") == 3


# ═══════════════════════════════════════════════════════════════════════════
# 3. Regression: a still-valid RETEST — NO ENTRY sequence stays eligible
# ═══════════════════════════════════════════════════════════════════════════


class TestStillValidRetestNoEntryUnaffected:
    def test_non_invalidated_sequence_keeps_tracking_same_break(self):
        """A break whose retest attempts fail geometry but which never
        accumulates 2 consecutive closes back inside/beyond the ORB
        must NOT be treated as dead — it must keep reporting the SAME
        break, exactly as before this fix."""
        bars = _orb_bars()
        bars.append(_c(_ms(5), 99.0, 99.2, 97.5, 97.8))       # break SHORT
        bars.append(_c(_ms(6), 97.8, 97.9, 97.3, 97.5))       # disp 1/3
        bars.append(_c(_ms(7), 97.5, 97.6, 97.0, 97.2))       # disp 2/3
        bars.append(_c(_ms(8), 97.2, 97.3, 96.8, 97.0))       # disp 3/3
        # Retest attempts that touch the level but close back OUTSIDE
        # the ORB each time (close < orb_low) — never 2 consecutive
        # "inside" closes, so validate_sequence never invalidates.
        bars.append(_c(_ms(9), 98.3, 99.05, 98.2, 98.9))      # weak, closes 98.9 < 99
        bars.append(_c(_ms(10), 98.9, 98.95, 98.6, 98.75))    # weak, closes 98.75 < 99

        sd = _short_detector()
        result = sd.evaluate(_build_session(bars))
        ctx = result.stage_context or {}

        # Still anchored on the original break — this setup is genuinely
        # alive (RETEST — NO ENTRY), not invalidated, so it must not be
        # skipped.
        assert result.status == SignalStatus.NO_SETUP
        assert result.failed_stage == "NO_QUALIFYING_REJECTION_CANDLE"
        assert ctx.get("break_bar_index") == 5
        assert ctx.get("displacement_bars") == 3
        assert ctx.get("retest_start_index") == 9
