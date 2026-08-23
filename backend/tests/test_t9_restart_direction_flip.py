"""T9 (2026-08-21 audit) — restart + opposite-direction break.

Scenario:

    pre-restart:  a SHORT structure forms and is later invalidated
                  (2 consecutive closes back inside the ORB band)
    restart:      a brand-new DualSignalDetector instance evaluates the
                  FULL history (pre- and post-restart bars) in a single
                  call — no incremental state carried over, exactly as
                  a freshly-started process would after bootstrapping
                  today's historical bars
    post-restart: a genuinely new, fully valid LONG break forms and
                  builds all the way to a real SIGNAL

This is a behavioral test of the current architecture (LONG and SHORT
are two independent LiveSignalDetector instances wrapped by
DualSignalDetector, with no directional lock) — it is not hunting for
a new bug. Uses the real LiveSignalDetector / DualSignalDetector /
LiveSessionBuilder pipeline; nothing is mocked.
"""

from __future__ import annotations

from trading_lab.live.dual_signal_detector import DualSignalDetector
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.session_builder_live import LiveSessionBuilder


MS_0930 = 1786455000000


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _c(t, o, h, l, cl):
    return {"time_ms": t, "open": o, "high": h, "low": l, "close": cl, "volume": 1000}


def _orb_bars():
    """5 ORB bars defining ORB high=101.00, low=99.00."""
    return [_c(_ms(i), 100.0, 101.0, 99.0, 100.0) for i in range(5)]


def _build_session(bars):
    sb = LiveSessionBuilder("TSLA")
    for b in bars:
        sb.add_bar(b)
    return sb.current_session()


def _make_dual_detector():
    long_sd = LiveSignalDetector(symbol="TSLA", direction="LONG", tick_size=0.01)
    short_sd = LiveSignalDetector(symbol="TSLA", direction="SHORT", tick_size=0.01)
    return DualSignalDetector(long_sd, short_sd)


def _build_bars():
    bars = _orb_bars()

    # ── Pre-restart: SHORT structure that later invalidates ─────────────
    bars.append(_c(_ms(5), 99.0, 99.2, 97.5, 97.8))        # idx5 break SHORT
    bars.append(_c(_ms(6), 97.8, 97.9, 97.3, 97.5))        # idx6 disp 1/3
    bars.append(_c(_ms(7), 97.5, 97.6, 97.0, 97.2))        # idx7 disp 2/3
    bars.append(_c(_ms(8), 97.2, 97.3, 96.8, 97.0))        # idx8 disp 3/3
    bars.append(_c(_ms(9), 98.90, 99.15, 98.80, 99.05))    # idx9 weak retest fail, 1st inside close
    bars.append(_c(_ms(10), 99.05, 99.6, 98.95, 99.4))     # idx10 2nd inside close -> SHORT INVALIDATED

    # ── Post-restart: genuinely new, fully valid LONG break -> SIGNAL ───
    # (canonical LONG fixture, same ORB high=101.00 already in effect)
    bars.append(_c(_ms(11), 100.80, 101.60, 100.70, 101.50))  # idx11 break LONG
    bars.append(_c(_ms(12), 101.55, 101.80, 101.20, 101.60))  # idx12 disp 1/3
    bars.append(_c(_ms(13), 101.60, 101.90, 101.30, 101.70))  # idx13 disp 2/3
    bars.append(_c(_ms(14), 101.70, 101.85, 101.10, 101.40))  # idx14 disp 3/3
    bars.append(_c(_ms(15), 101.10, 101.30, 100.80, 101.20))  # idx15 rejection -> SIGNAL LONG
    return bars


def test_t9_restart_finds_new_long_after_invalidated_short():
    bars = _build_bars()

    old_short_break_ts = _ms(5)
    new_long_break_ts = _ms(11)
    expected_setup_key = f"LONG:{new_long_break_ts}"

    # A fresh DualSignalDetector instance = simulating a brand-new,
    # post-restart process (no incremental state), evaluating the full
    # bootstrapped history in one call.
    fresh_dual = _make_dual_detector()
    result = fresh_dual.evaluate(_build_session(bars))

    # 1+2: sanity — the pre-restart SHORT structure genuinely existed
    # and is genuinely gone by the time we evaluate (confirmed on the
    # SHORT side alone: no lingering, no residual break at all).
    short_only = LiveSignalDetector(symbol="TSLA", direction="SHORT", tick_size=0.01)
    short_result = short_only.evaluate(_build_session(bars))
    assert short_result.status == SignalStatus.NO_SETUP
    assert short_result.failed_stage == "BREAK_NOT_FOUND"
    assert (short_result.stage_context or {}).get("break_bar_index") != old_short_break_ts

    # 3+4: the combined (real) detector reaches the new LONG setup, as
    # a genuine SIGNAL, with the setup_key belonging to the NEW LONG
    # break — not the old SHORT one.
    assert result.status == SignalStatus.SIGNAL, (
        f"expected SIGNAL on the new LONG break, got {result.status} "
        f"(direction={result.direction}, failed_stage={result.failed_stage!r}) "
        f"— a residual SHORT structure may be blocking the LONG side"
    )
    assert result.direction == "LONG"
    assert result.setup_key == expected_setup_key

    # 5: the old SHORT setup_key must never be what's returned.
    old_short_setup_key = f"SHORT:{old_short_break_ts}"
    assert result.setup_key != old_short_setup_key
