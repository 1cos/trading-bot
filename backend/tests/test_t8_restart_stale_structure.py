"""T8 (2026-08-21 audit) — restart with an old, no-longer-valid structure.

Principle under test: at restart, state must be DERIVED from the
available data, never recovered/dragged from whatever the previous
process last reported.

Scenario:

    break SHORT (idx5)
    -> 3 valid displacement bars (idx6-8), no retest contact yet
       => at this intermediate point, the detector is genuinely in
          RETEST_NOT_FOUND ("WAITING FOR RETEST" per _STAGE_LABELS) —
          a live, correct state, since more bars could still complete
          the sequence.
    -> a retest contact arrives (idx9) and closes back inside the ORB
       band, followed by a second consecutive inside close (idx10)
       => validate_sequence() invalidates the sequence.

A brand-new LiveSignalDetector instance (simulating a freshly
restarted process, no incremental state) then evaluates the ENTIRE
available history in a single call. It must NOT report the stale
RETEST_NOT_FOUND state anchored on the old break — it must derive
whatever is genuinely true of the data right now.

This is a test of existing, already-correct behavior (the stateless,
recompute-from-scratch architecture plus the existing
SEQUENCE_INVALIDATED skip-loop) — it is not hunting for a new bug.
"""

from __future__ import annotations

from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus


MS_0930 = 1786455000000


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _c(t, o, h, l, cl):
    return {"time_ms": t, "open": o, "high": h, "low": l, "close": cl, "volume": 1000}


def _orb_bars():
    """5 ORB bars defining ORB high=101.00, low=99.00."""
    return [_c(_ms(i), 100.0, 101.0, 99.0, 100.0) for i in range(5)]


def _build_session(bars):
    sb = LiveSessionBuilder("QQQ")
    for b in bars:
        sb.add_bar(b)
    return sb.current_session()


def _make_short_detector():
    return LiveSignalDetector(symbol="QQQ", direction="SHORT", tick_size=0.01)


def _bars_before_invalidation():
    """Break + 3 valid displacement bars, no retest contact yet."""
    bars = _orb_bars()
    bars.append(_c(_ms(5), 99.0, 99.2, 97.5, 97.8))   # idx5 break SHORT
    bars.append(_c(_ms(6), 97.8, 97.9, 97.3, 97.5))   # idx6 disp 1/3 (no contact, high<99)
    bars.append(_c(_ms(7), 97.5, 97.6, 97.0, 97.2))   # idx7 disp 2/3
    bars.append(_c(_ms(8), 97.2, 97.3, 96.8, 97.0))   # idx8 disp 3/3
    return bars


def _bars_with_invalidation():
    """The above, plus a retest contact that closes back inside the ORB
    twice in a row (2 consecutive) — genuine SEQUENCE_INVALIDATED."""
    bars = _bars_before_invalidation()
    bars.append(_c(_ms(9), 97.0, 99.05, 96.9, 99.05))   # idx9 contact, closes inside (1st)
    bars.append(_c(_ms(10), 99.05, 99.5, 98.9, 99.3))   # idx10 2nd consecutive inside close
    return bars


def test_t8_restart_does_not_resurrect_stale_waiting_for_retest():
    old_break_ts = _ms(5)

    # ── Step 1: on the truncated history, confirm the setup was
    # genuinely alive and WAITING FOR RETEST (RETEST_NOT_FOUND) —
    # not yet dead, not yet invalidated. ────────────────────────────────
    mid_bars = _bars_before_invalidation()
    detector_mid = _make_short_detector()
    result_mid = detector_mid.evaluate(_build_session(mid_bars))

    assert result_mid.status == SignalStatus.NO_SETUP
    assert result_mid.failed_stage == "RETEST_NOT_FOUND"
    mid_ctx = result_mid.stage_context or {}
    assert mid_ctx.get("break_bar_index") == 5
    assert mid_ctx.get("displacement_bars") == 3

    # ── Step 2: add the bars that genuinely invalidate the sequence,
    # then create a BRAND-NEW detector instance (simulating a fresh
    # restart — no incremental state) and evaluate the full available
    # history in one call. ──────────────────────────────────────────────
    full_bars = _bars_with_invalidation()
    fresh_detector = _make_short_detector()
    result_after_restart = fresh_detector.evaluate(_build_session(full_bars))

    # The decisive assertions: the stale RETEST_NOT_FOUND / "WAITING FOR
    # RETEST" state anchored on the old break must NOT survive.
    assert result_after_restart.failed_stage != "RETEST_NOT_FOUND", (
        "restart resurrected the stale WAITING_FOR_RETEST state instead "
        "of deriving the current, already-invalidated reality"
    )
    restart_ctx = result_after_restart.stage_context or {}
    assert restart_ctx.get("break_bar_index") != 5, (
        "the old, now-invalidated break (idx5) was returned as if still "
        "alive after restart"
    )
    # No setup_key belonging to the old break is returned as live.
    assert result_after_restart.setup_key != f"SHORT:{old_break_ts}"

    # The result must be coherent with either the invalidation itself
    # (SEQUENCE_INVALIDATED) or whatever genuine state the data resolves
    # to once the dead break is skipped (here: no further valid break
    # exists in the available candles at all, so BREAK_NOT_FOUND is the
    # correct, fully-derived outcome — evaluate()'s own skip-loop
    # already walks past SEQUENCE_INVALIDATED and the subsequent
    # structurally-dead candidate breaks on its own).
    assert result_after_restart.status == SignalStatus.NO_SETUP
    assert result_after_restart.failed_stage in (
        "SEQUENCE_INVALIDATED", "BREAK_NOT_FOUND",
    ), (
        f"unexpected failed_stage after restart: "
        f"{result_after_restart.failed_stage!r}"
    )
