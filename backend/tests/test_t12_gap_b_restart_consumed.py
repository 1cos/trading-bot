"""T12 (2026-08-21 audit, Gap B) — single, definitive, non-mocked test.

Scenario (exactly as specified for T12):

    restart
    -> old SIGNAL A predates _live_boundary_ms
    -> A must NOT be executed
    -> a genuinely new setup B exists afterward
    -> the detector/orchestrator must be able to reach B

This test deliberately does NOT mock LiveSignalDetector.evaluate(). A
previous (informal, non-repo) verification of this same claim used a
MagicMock with a scripted side_effect list for signal_detector.evaluate,
which trivially returns whatever result is next in the list regardless
of what consumed_setup_keys is passed in — it cannot distinguish
pre-fix from post-fix behavior, because the REAL mechanism under test
(whether LiveSignalDetector.evaluate()'s own internal scan-skip loop
advances past setup A) never runs. That produced a misleading picture.

This test uses the REAL LiveSignalDetector + real LiveSessionBuilder,
wired into a real MaxBotTradeOrchestrator (only the broker-facing
adapters — option selector / entry / exit executors — are mocked,
since no order is expected to be placed here). This is the only way to
honestly exercise the mechanism Fix B touches.

Bar layout (SHORT, ORB high=101.00 / low=99.00 — a tick-for-tick mirror
of the canonical fixture in test_signal_detector.py, reflected around
the level so the exact same, already-validated rejection geometry
applies):

    idx0-4   ORB
    idx5     break A
    idx6-8   3 valid displacement bars for A
    idx9     retest + qualifying rejection candle -> SIGNAL A
    idx10    a single bounce back inside the ORB (close >= 99) — only
             ONE such candle, never two consecutive, so A's sequence is
             never naturally invalidated by validate_sequence(). This
             isolates Gap B from the (already-fixed, unrelated)
             SEQUENCE_INVALIDATED skip mechanism.
    idx11    break B (fresh close < 99)
    idx12-14 3 valid displacement bars for B
    idx15    retest + qualifying rejection candle -> SIGNAL B

Bars 0-10 are fed directly to the session builder (simulating a
bootstrap of historical bars at restart, as MaxBotRunner._bootstrap_symbol
does — added to the session but not run through on_bar/_check_for_signal).
_live_boundary_ms is then set to bar 11's own time_ms (the moment the
"live" bars start arriving after restart). Bars 11-15 are then fed one
at a time via orch.on_bar(), exactly as the runner's main loop would.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator


MS_0930 = 1786455000000


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


# ── SHORT fixture: exact mirror of the canonical LONG fixture in
# test_signal_detector.py, reflected around price so the same,
# already-validated rejection geometry (body_ratio, wick_ratio,
# favorable_close_location, wick penetration) applies unchanged. ─────────────

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


_LONG_ORB = [
    {"open": 100.00, "high": 101.00, "low": 99.00, "close": 100.50},
    {"open": 100.50, "high": 100.80, "low": 100.00, "close": 100.30},
    {"open": 100.30, "high": 100.70, "low": 99.80, "close": 100.40},
    {"open": 100.40, "high": 100.90, "low": 100.10, "close": 100.60},
    {"open": 100.60, "high": 100.95, "low": 100.20, "close": 100.70},
]
_LONG_BREAK = {"open": 100.80, "high": 101.60, "low": 100.70, "close": 101.50}
_LONG_DISP = [
    {"open": 101.55, "high": 101.80, "low": 101.20, "close": 101.60},
    {"open": 101.60, "high": 101.90, "low": 101.30, "close": 101.70},
    {"open": 101.70, "high": 101.85, "low": 101.10, "close": 101.40},
]
_LONG_REJ = {"open": 101.10, "high": 101.30, "low": 100.80, "close": 101.20}


def _build_bars() -> list[dict]:
    bars = [_mirror(b, _ms(i)) for i, b in enumerate(_LONG_ORB)]
    bars.append(_mirror(_LONG_BREAK, _ms(5)))                    # idx5  break A
    bars += [_mirror(b, _ms(6 + i)) for i, b in enumerate(_LONG_DISP)]  # idx6-8 disp A
    bars.append(_mirror(_LONG_REJ, _ms(9)))                      # idx9  SIGNAL A

    # idx10: single bounce back inside the ORB (close >= 99) — NOT two
    # consecutive, so validate_sequence() never invalidates A.
    bars.append({"time_ms": _ms(10), "open": 98.8, "high": 99.6,
                 "low": 98.7, "close": 99.3, "volume": 1000})

    bars.append(_mirror(_LONG_BREAK, _ms(11)))                   # idx11 break B
    bars += [_mirror(b, _ms(12 + i)) for i, b in enumerate(_LONG_DISP)]  # idx12-14 disp B
    bars.append(_mirror(_LONG_REJ, _ms(15)))                     # idx15 SIGNAL B
    return bars


def _make_orchestrator() -> MaxBotTradeOrchestrator:
    sb = LiveSessionBuilder("SPY")
    sd = LiveSignalDetector(
        symbol="SPY", direction="SHORT", tick_size=0.01,
        market_timezone="America/New_York", session_open="09:30",
    )
    tm = DailyTradeManager()
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="SPY", direction="SHORT", tick_size=0.01,
        session_builder=sb, signal_detector=sd, trade_manager=tm,
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(),
    )
    return orch, sb


def test_t12_old_signal_before_boundary_does_not_block_new_setup():
    """T12 — restart -> stale SIGNAL A before live boundary -> A not
    executed -> a later, genuinely new SIGNAL B must still be found.

    This is the single required T12 test. It must be run twice by the
    surrounding process (not by pytest itself): once against the
    current (Fix B applied) code, and once with ONLY the Fix B change
    in trade_orchestrator.py reverted (Fix A and everything else
    unchanged), to compare pre/post-fix behavior on this exact,
    unmodified test.
    """
    bars = _build_bars()
    orch, sb = _make_orchestrator()

    # Bootstrap: bars 0-10 (through the single ORB bounce-back) are fed
    # directly to the session builder only — simulating MaxBotRunner's
    # historical-bar bootstrap at restart, which populates context
    # without running _check_for_signal.
    for bar in bars[:11]:
        sb.add_bar(bar)

    # Live boundary = the moment bar 11 (the first genuinely "live"
    # bar after restart) begins. A's own entry candle (idx9) is
    # unambiguously before this.
    orch._live_boundary_ms = bars[11]["time_ms"]
    assert bars[9]["time_ms"] < orch._live_boundary_ms  # sanity: A predates the boundary

    # Feed the "live" bars one at a time, exactly as the runner would.
    for bar in bars[11:]:
        orch.on_bar(bar)

    old_setup_key = f"SHORT:ORB_LOW:{bars[5]['time_ms']}"
    new_setup_key = f"SHORT:ORB_LOW:{bars[11]['time_ms']}"

    # The decisive assertion: a genuinely new setup (B) must have been
    # reached and accepted as a pending signal once all of its bars
    # have arrived.
    assert orch.has_pending_signal, (
        "detector never reached the new setup B — it is stuck "
        "re-deriving the old, pre-boundary setup A on every bar"
    )
    assert orch._pending_signal.setup_key == new_setup_key, (
        f"expected pending signal for the NEW setup {new_setup_key!r}, "
        f"got {orch._pending_signal.setup_key!r} instead"
    )
    # A's own setup_key must never have been accepted for execution.
    assert old_setup_key != orch._pending_signal.setup_key
