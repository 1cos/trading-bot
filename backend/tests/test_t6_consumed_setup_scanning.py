"""T6 (2026-08-21 audit) — Setup A consumed, Setup B still detectable.

Existing coverage checked first:
  - test_t19f_reentry.py::TestDetectorSkipsConsumed::test_consumed_first_skips_to_second
  - test_skip_dead_breaks.py::TestConsumedBehaviorPreserved::test_consumed_signal_still_skipped
  - test_state_machine_audit.py::TestNewBreakAfterInvalidation::test_invalidated_setup_consumed_allows_new

All three mock LiveSignalDetector._evaluate_inner (scripted side_effect
results, or a None session). They correctly verify the outer scan-skip
loop's own bookkeeping (skip_before advances, the right number of
calls happens), but none of them exercise the REAL break/displacement/
retest/rejection pipeline on real bars — so none of them can prove
"the break returned actually belongs to B" (task requirement #4), only
that the loop returns whatever scripted SignalResult was next. This
test closes that gap with a real, non-mocked LiveSignalDetector
building two genuine, complete BDRR sequences from real candles.

Scenario:
    ORB (high=101.00, low=99.00)
    -> Break A (idx5) + 3 valid displacement bars + qualifying entry
       candle -> SIGNAL A (idx9)
    -> a single bounce back inside the ORB (idx10) — not two
       consecutive, so A's own sequence is never naturally invalidated
       by validate_sequence(); the ONLY reason evaluate() has to move
       past A here is consumed_setup_keys
    -> Break B (idx11) + 3 valid displacement bars + qualifying entry
       candle -> SIGNAL B (idx15)

Evaluating the FULL session with consumed_setup_keys={setup_key_A}
must yield SIGNAL B.
"""

from __future__ import annotations

from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus


MS_0930 = 1786455000000


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


# ── SHORT fixture: exact mirror of the canonical LONG fixture in
# test_signal_detector.py, reflected around the level so the same,
# already-validated rejection geometry applies unchanged. ─────────────

_LONG_LEVEL = 101.00
_SHORT_LEVEL = 99.00


def _mirror(bar: dict, t: int) -> dict:
    def m(x: float) -> float:
        return round(_SHORT_LEVEL - (x - _LONG_LEVEL), 4)

    return {
        "time_ms": t,
        "open": m(bar["open"]),
        "high": m(bar["low"]),   # low reflects to high
        "low": m(bar["high"]),   # high reflects to low
        "close": m(bar["close"]),
        "volume": bar.get("volume", 1000),
    }


_LONG_BREAK = {"open": 100.80, "high": 101.60, "low": 100.70, "close": 101.50}
_LONG_DISP = [
    {"open": 101.55, "high": 101.80, "low": 101.20, "close": 101.60},
    {"open": 101.60, "high": 101.90, "low": 101.30, "close": 101.70},
    {"open": 101.70, "high": 101.85, "low": 101.10, "close": 101.40},
]
_LONG_REJ = {"open": 101.10, "high": 101.30, "low": 100.80, "close": 101.20}


def _orb_bars():
    return [{"time_ms": _ms(i), "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "volume": 1000} for i in range(5)]


def _build_bars() -> list[dict]:
    bars = _orb_bars()
    bars.append(_mirror(_LONG_BREAK, _ms(5)))                    # idx5  break A
    bars += [_mirror(b, _ms(6 + i)) for i, b in enumerate(_LONG_DISP)]  # idx6-8 disp A
    bars.append(_mirror(_LONG_REJ, _ms(9)))                      # idx9  SIGNAL A

    # Single bounce back inside the ORB — NOT two consecutive, so A's
    # sequence is never naturally invalidated by validate_sequence().
    bars.append({"time_ms": _ms(10), "open": 98.8, "high": 99.6,
                 "low": 98.7, "close": 99.3, "volume": 1000})

    bars.append(_mirror(_LONG_BREAK, _ms(11)))                   # idx11 break B
    bars += [_mirror(b, _ms(12 + i)) for i, b in enumerate(_LONG_DISP)]  # idx12-14 disp B
    bars.append(_mirror(_LONG_REJ, _ms(15)))                     # idx15 SIGNAL B
    return bars


def _build_session(bars):
    sb = LiveSessionBuilder("SPY")
    for b in bars:
        sb.add_bar(b)
    return sb.current_session()


def test_t6_consumed_setup_a_does_not_block_new_setup_b():
    bars = _build_bars()

    # First, establish SIGNAL A on its own (bars through idx9 only),
    # exactly as it would have been found and consumed earlier.
    detector_a = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
    result_a = detector_a.evaluate(_build_session(bars[:10]))
    assert result_a.status == SignalStatus.SIGNAL
    setup_key_a = result_a.setup_key
    assert setup_key_a == f"SHORT:ORB_LOW:{_ms(5)}"

    # Now evaluate the FULL session (A + bounce + B) with A marked
    # consumed — a fresh detector instance, matching how the real
    # scanning mechanism is used (consumed keys are supplied externally,
    # not remembered by the detector itself).
    detector_full = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
    result = detector_full.evaluate(_build_session(bars), consumed_setup_keys={setup_key_a})

    setup_key_b = f"SHORT:ORB_LOW:{_ms(11)}"

    # 1. Final result is SIGNAL.
    assert result.status == SignalStatus.SIGNAL

    # 2 & 3. setup_key belongs to B, not A.
    assert result.setup_key == setup_key_b
    assert result.setup_key != setup_key_a

    # 4. The break actually returned belongs to B (idx11), not A (idx5).
    ctx = result.stage_context or {}
    assert ctx.get("break_bar_index") == 11
    assert ctx.get("break_bar_index") != 5

    # 5. A is skipped exclusively because it is consumed: without
    # consumed_setup_keys, the SAME full session still resolves to A
    # (A's own sequence was never naturally invalidated — confirmed by
    # the single, non-consecutive bounce-back bar at idx10). This
    # isolates consumed_setup_keys as the only mechanism responsible
    # for skipping A here.
    detector_no_consumed = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
    result_no_consumed = detector_no_consumed.evaluate(_build_session(bars))
    assert result_no_consumed.status == SignalStatus.SIGNAL
    assert result_no_consumed.setup_key == setup_key_a

    # 6. B is not erroneously filtered out — its own entry timestamp
    # (idx15) is returned, matching a genuinely complete sequence.
    assert result.entry_timestamp_ms == _ms(15)
