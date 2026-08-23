"""T1 (2026-08-21 audit) — dead break reset (general case).

Existing coverage checked first:
  TestDeadBreakSkipped::test_dead_break_with_orb_return_skipped (this
  file) is close, but its Break A fails via RETEST_BEFORE_DISPLACEMENT
  — a failed_stage Fix A now skips unconditionally regardless of ORB
  reentry. With Fix A present, that test no longer isolates "genuine
  ORB reentry" as the operative mechanism (Fix A alone already
  explains the skip), and it does not assert on result.failed_stage or
  the overall final result at all — only on the new break_bar_index.
  It does not satisfy T1's explicit constraint: this task must NOT
  re-prove Fix A directly (that is T11's exclusive job).

This test instead uses a break whose displacement genuinely COMPLETES
(3 valid bars, contact found, structurally never touches Fix A's
DISPLACEMENT_TOO_SHORT / RETEST_BEFORE_DISPLACEMENT branch at all) and
is invalidated afterward via the general, independent
SEQUENCE_INVALIDATED mechanism (2 consecutive closes back inside the
ORB band) — proving the general principle: failed/invalidated
structure -> reset, not the specific Fix A pathway.
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


def test_t1_dead_break_reset_general_case():
    old_break_ts = _ms(5)

    bars = _orb_bars()
    bars.append(_c(_ms(5), 99.0, 99.2, 97.5, 97.8))    # idx5 break A SHORT
    bars.append(_c(_ms(6), 97.8, 97.9, 97.3, 97.5))    # idx6 disp 1/3 (no contact)
    bars.append(_c(_ms(7), 97.5, 97.6, 97.0, 97.2))    # idx7 disp 2/3
    bars.append(_c(_ms(8), 97.2, 97.3, 96.8, 97.0))    # idx8 disp 3/3 — displacement is
    # genuinely complete (3 valid bars, never touches Fix A's territory)
    bars.append(_c(_ms(9), 97.0, 99.05, 96.9, 99.05))  # idx9 contact + 1st close inside ORB
    bars.append(_c(_ms(10), 99.05, 99.5, 98.9, 99.4))  # idx10 2nd consecutive inside close
    # -> genuine SEQUENCE_INVALIDATED (real market re-entry into the ORB)

    # Price genuinely stays inside the ORB afterward — no new break at all.
    bars.append(_c(_ms(11), 99.4, 99.8, 99.2, 99.6))
    bars.append(_c(_ms(12), 99.6, 99.9, 99.3, 99.7))

    sd = LiveSignalDetector(symbol="QQQ", direction="SHORT", tick_size=0.01)
    result = sd.evaluate(_build_session(bars))

    # 1. Break A is not returned as a live setup.
    ctx = result.stage_context or {}
    assert ctx.get("break_bar_index") != 5
    assert result.setup_key != f"SHORT:{old_break_ts}"

    # 2. The final result is not anchored on DISPLACEMENT_TOO_SHORT for
    # the old Break A (its displacement genuinely completed — this test
    # deliberately never exercises Fix A's specific branch at all).
    assert result.failed_stage != "DISPLACEMENT_TOO_SHORT"

    # 3. break_bar_index does not remain the old Break A.
    assert ctx.get("break_bar_index") != 5

    # 4. The detector correctly resets to a semantically valid state —
    # here, no further break exists in the data at all, so
    # BREAK_NOT_FOUND ("waiting for a break") is the fully-derived,
    # correct outcome once the invalidated Break A is skipped.
    assert result.status == SignalStatus.NO_SETUP
    assert result.failed_stage == "BREAK_NOT_FOUND"
