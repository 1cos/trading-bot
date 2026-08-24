"""Tests for collect_multi_source_signals() — pure ORB + PDH/PDL
multi-source signal collector.

Covers M1-M15 exactly as specified. Every scenario except M12 (see
below) drives REAL LiveSignalDetector / evaluate_pdh_pdl_candidate()
output through synthetic-but-realistic 1m bars — not hand-built
SignalResult stand-ins — so these tests prove the collector's actual
behavior against the real BDRR pipeline, not just its own internal
wiring logic.

M12 (opposite-direction, same current bar) is the one exception. A
structural fact emerged while building these fixtures and is worth
recording here: under the standard CONFIRMATION_CLOSE entry model
used throughout this codebase, a LONG entry candle's close must be
strictly ABOVE its level (ORB_HIGH or PDH) and a SHORT entry candle's
close must be strictly BELOW its level (ORB_LOW or PDL). Since
PDH > ORB_HIGH > ORB_LOW > PDL is always true, a single candle's
close cannot simultaneously satisfy both inequalities — so a genuine
same-candle LONG-and-SHORT double-completion cannot occur through the
real pipeline at all, for any pair of ORB/PDH/PDL-anchored levels.
M12 therefore uses hand-built SignalResult stand-ins (same technique
already established by test_signal_dedup.py's own D5/C4 tests) to
prove collect_actionable_signals()'s "never merge" guarantee still
holds should this ever change (e.g. a future non-close entry model),
while still routing them through the real, unmodified
collect_multi_source_signals() -> collect_actionable_signals() path.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.live.multi_source_signal_collector import collect_multi_source_signals
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_dedup import (
    DedupedSignalCandidate,
    SignalObservation,
    collect_actionable_signals,
)
from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)
from trading_lab.pdh_pdl_eligibility import check_orb_to_level_eligibility


# ── Bar/session helpers ──────────────────────────────────────────────────────

MS_0930 = 1786455000000


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _c(offset_min, o, h, l, cl):
    return {"time_ms": _ms(offset_min), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


def _build_session(bars, symbol="QQQ"):
    sb = LiveSessionBuilder(symbol)
    for b in bars:
        sb.add_bar(b)
    return sb.current_session()


def _orb_bars_5m():
    """Standard 5-bar ORB window: high=101.00, low=99.00."""
    return [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]


def _prev_sessions(pdh=None, pdl=None):
    """previous_sessions in all_sessions format. pdh/pdl control the
    single previous-session candle's high/low (hence PDH/PDL)."""
    return [{
        "date": "2026-08-10",
        "candles": [{
            "time_ms": 1, "open": 100.0,
            "high": pdh if pdh is not None else 103.00,
            "low": pdl if pdl is not None else 95.00,
            "close": 100.0, "volume": 500,
        }],
    }]


def _orb_long_detector():
    return LiveSignalDetector(symbol="QQQ", direction="LONG", tick_size=0.01,
                               market_timezone="America/New_York", session_open="09:30")


def _orb_short_detector():
    return LiveSignalDetector(symbol="QQQ", direction="SHORT", tick_size=0.01,
                               market_timezone="America/New_York", session_open="09:30")


# ── M1/M2/M4: LONG fixture where ORB (101.00) and PDH (101.01) share the
# same final rejection candle — ORB alone completes structurally earlier
# than PDH would with a farther level, but this shared-candle fixture is
# built so BOTH complete on the identical bar (idx9), matching M4's own
# purpose. M1 (ORB only) and M2 (PDH only) just omit one side's directions.
def _long_shared_candle_bars():
    bars = _orb_bars_5m()
    bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))   # break (clears 101.00 and 101.01)
    bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))   # disp1
    bars.append(_c(7, 101.60, 101.90, 101.30, 101.70))   # disp2
    bars.append(_c(8, 101.70, 101.85, 101.10, 101.40))   # disp3
    bars.append(_c(9, 101.10, 101.30, 100.80, 101.20))   # shared retest/rejection
    return bars


def _short_shared_candle_bars():
    bars = _orb_bars_5m()
    bars.append(_c(5, 99.20, 99.30, 98.40, 98.50))    # break (clears 99.00 and 98.99)
    bars.append(_c(6, 98.45, 98.80, 98.20, 98.30))    # disp1
    bars.append(_c(7, 98.30, 98.70, 97.90, 98.00))    # disp2
    bars.append(_c(8, 98.00, 98.20, 97.60, 97.70))    # disp3
    bars.append(_c(9, 97.70, 97.90, 96.80, 96.90))    # PDL break (level=97.00)
    bars.append(_c(10, 96.90, 96.80, 96.60, 96.70))   # PDL disp1
    bars.append(_c(11, 96.70, 96.65, 96.50, 96.60))   # PDL disp2
    bars.append(_c(12, 96.60, 96.55, 96.45, 96.55))   # PDL disp3
    bars.append(_c(13, 96.60, 100.00, 96.40, 96.60))  # shared retest/rejection (ORB_LOW + PDL)
    return bars


# ═════════════════════════════════════════════════════════════════════════
# M1 — ORB only current
# ═════════════════════════════════════════════════════════════════════════

class TestM1OrbOnlyCurrent:
    def test_orb_only_one_candidate(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]
        # previous-day high BELOW orb_high -> PDH ineligible (WRONG_GEOMETRY)
        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdh=100.50),
            orb_detector=_orb_long_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
        )
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("ORB_HIGH",)


# ═════════════════════════════════════════════════════════════════════════
# M2 — PDH only current
# ═════════════════════════════════════════════════════════════════════════

class TestM2PdhOnlyCurrent:
    def test_pdh_only_one_candidate(self):
        # ORB breaks 101 (idx5), displaces 3 clean bars (idx6-8), then a
        # "touch" bar (idx9) dips to low<=101 -- satisfying
        # check_orb_to_level_eligibility()'s own displacement-complete
        # requirement (find_displacement() needs a first-contact index
        # to even measure displacement_bar_count) -- WITHOUT closing
        # above 101, so it fails ORB's own rejection-candle geometry
        # outright (close must be > orb_high). Price then independently
        # breaks/displaces/retests PDH's own, much higher level (103)
        # afterward. ORB's own detector never finds a qualifying
        # rejection candle anywhere in the session.
        bars = _orb_bars_5m()
        bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))   # ORB break (101 only)
        bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))   # clean disp1
        bars.append(_c(7, 101.60, 101.90, 101.30, 101.70))   # clean disp2
        bars.append(_c(8, 101.70, 101.85, 101.10, 101.40))   # clean disp3
        bars.append(_c(9, 100.95, 101.05, 100.85, 100.90))   # touch, but close<101: not a rejection
        bars.append(_c(10, 100.95, 102.00, 100.90, 101.90))  # recover, still below PDH(103)
        bars.append(_c(11, 101.90, 103.60, 101.85, 103.50))  # fresh break of PDH(103)
        bars.append(_c(12, 103.55, 103.80, 103.20, 103.60))  # PDH disp1
        bars.append(_c(13, 103.60, 103.90, 103.30, 103.70))  # PDH disp2
        bars.append(_c(14, 103.70, 103.85, 103.10, 103.40))  # PDH disp3
        bars.append(_c(15, 103.10, 103.30, 102.80, 103.20))  # PDH retest/rejection -> SIGNAL
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]

        orb_detector = _orb_long_detector()
        probe = orb_detector.evaluate(session)
        assert probe.status == SignalStatus.NO_SETUP  # sanity: ORB never completes here
        assert probe.failed_stage == "NO_QUALIFYING_REJECTION_CANDLE"

        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdh=103.00),
            orb_detector=orb_detector, current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
        )
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("PREVIOUS_DAY_HIGH",)


# ═════════════════════════════════════════════════════════════════════════
# M3 — PDL only current (SHORT mirror of M2)
# ═════════════════════════════════════════════════════════════════════════

class TestM3PdlOnlyCurrent:
    def test_pdl_only_one_candidate(self):
        # SHORT mirror of M2.
        bars = _orb_bars_5m()
        bars.append(_c(5, 99.20, 99.30, 98.40, 98.50))    # ORB break (99 only)
        bars.append(_c(6, 98.45, 98.80, 98.20, 98.30))    # clean disp1
        bars.append(_c(7, 98.30, 98.70, 97.90, 98.00))    # clean disp2
        bars.append(_c(8, 98.00, 98.20, 97.60, 97.70))    # clean disp3
        bars.append(_c(9, 99.05, 99.15, 98.95, 99.10))    # touch, but close>99: not a rejection
        bars.append(_c(10, 99.05, 99.10, 98.00, 98.10))   # recover down, still above PDL(97)
        bars.append(_c(11, 98.10, 98.15, 96.40, 96.50))   # fresh break of PDL(97)
        bars.append(_c(12, 96.45, 96.80, 96.20, 96.30))   # PDL disp1
        bars.append(_c(13, 96.30, 96.70, 95.90, 96.00))   # PDL disp2
        bars.append(_c(14, 96.00, 96.20, 95.60, 95.70))   # PDL disp3
        bars.append(_c(15, 96.90, 97.20, 96.70, 96.80))   # PDL retest/rejection -> SIGNAL
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]

        orb_detector = _orb_short_detector()
        probe = orb_detector.evaluate(session)
        assert probe.status == SignalStatus.NO_SETUP  # sanity: ORB_LOW never completes here
        assert probe.failed_stage == "NO_QUALIFYING_REJECTION_CANDLE"

        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdl=97.00),
            orb_detector=orb_detector, current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("SHORT",),
        )
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("PREVIOUS_DAY_LOW",)


# ═════════════════════════════════════════════════════════════════════════
# M4 — ORB + PDH same current Max Entry Candle (the strategically
# important case)
# ═════════════════════════════════════════════════════════════════════════

class TestM4OrbPlusPdhSameCandle:
    def test_one_candidate_both_sources(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]
        # PDH = 101.01, just above orb_high=101.00 -> eligible, and close
        # enough that the identical rejection candle qualifies for both.
        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdh=101.01),
            orb_detector=_orb_long_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
        )
        assert len(out) == 1
        assert set(out[0].contributing_level_sources) == {"ORB_HIGH", "PREVIOUS_DAY_HIGH"}
        assert out[0].signal.entry_timestamp_ms == current_bar_time_ms


# ═════════════════════════════════════════════════════════════════════════
# M5 — ORB + PDL same current SHORT candle (mirror of M4)
# ═════════════════════════════════════════════════════════════════════════

class TestM5OrbPlusPdlSameCandle:
    def test_one_candidate_both_sources(self):
        bars = _short_shared_candle_bars()
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]
        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdl=98.99),
            orb_detector=_orb_short_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("SHORT",),
        )
        assert len(out) == 1
        assert set(out[0].contributing_level_sources) == {"ORB_LOW", "PREVIOUS_DAY_LOW"}


# ═════════════════════════════════════════════════════════════════════════
# M6 — ORB old + PDH current
# ═════════════════════════════════════════════════════════════════════════

class TestM6OrbOldPdhCurrent:
    def test_pdh_only_survives(self):
        bars = _orb_bars_5m()
        bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))   # ORB break
        bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))   # ORB disp1
        bars.append(_c(7, 101.60, 101.90, 101.30, 101.70))   # ORB disp2
        bars.append(_c(8, 101.70, 101.85, 101.10, 101.40))   # ORB disp3
        bars.append(_c(9, 101.10, 101.30, 100.80, 101.20))   # ORB SIGNAL @ T1 (old)
        bars.append(_c(10, 101.20, 102.50, 101.15, 102.40))  # push up
        bars.append(_c(11, 102.40, 103.60, 102.30, 103.50))  # break PDH (103)
        bars.append(_c(12, 103.55, 103.80, 103.20, 103.60))  # PDH disp1
        bars.append(_c(13, 103.60, 103.90, 103.30, 103.70))  # PDH disp2
        bars.append(_c(14, 103.70, 103.85, 103.10, 103.40))  # PDH disp3
        bars.append(_c(15, 103.10, 103.30, 102.80, 103.20))  # PDH SIGNAL @ T3 (current)
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]

        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdh=103.00),
            orb_detector=_orb_long_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
        )
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("PREVIOUS_DAY_HIGH",)
        assert out[0].signal.entry_timestamp_ms == current_bar_time_ms


# ═════════════════════════════════════════════════════════════════════════
# M7 — PDH old + ORB current (inverse of M6)
# ═════════════════════════════════════════════════════════════════════════

class TestM7PdhOldOrbCurrent:
    def test_orb_only_survives(self):
        bars = _orb_bars_5m()
        bars.append(_c(5, 100.80, 103.60, 100.70, 103.50))   # break clears BOTH 101 and 103
        bars.append(_c(6, 103.55, 103.90, 103.20, 103.60))   # disp1
        bars.append(_c(7, 103.60, 104.00, 103.30, 103.70))   # disp2
        bars.append(_c(8, 103.70, 104.10, 103.10, 103.40))   # disp3
        bars.append(_c(9, 103.10, 103.30, 102.80, 103.20))   # PDH SIGNAL @ T1 (old, shallow retest)
        bars.append(_c(10, 103.25, 103.60, 103.10, 103.30))  # consolidation, close still > PDH(103)
        bars.append(_c(11, 103.30, 103.50, 103.20, 103.40))  # consolidation
        bars.append(_c(12, 103.40, 103.55, 103.25, 103.35))  # consolidation
        # Single deep-wick candle: ORB's own retest/rejection at level=101.
        # Only ONE bar closes below PDH's level (103) here -- below the
        # 2-consecutive-closes line-invalidation threshold -- so PDH's
        # earlier SIGNAL remains a genuine, non-invalidated historical
        # result at this snapshot, not merely NO_SETUP.
        bars.append(_c(13, 101.10, 101.30, 100.80, 101.20))  # ORB SIGNAL @ T2 (current)
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]

        # Sanity: confirm PDH really is a genuine historical SIGNAL here,
        # not an invalidated/absent one -- this is what makes M7 a true
        # "old SIGNAL, filtered as non-current" case, same shape as M6.
        prev_sessions = _prev_sessions(pdh=103.00)
        pdh_probe = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
            level_source="PREVIOUS_DAY_HIGH",
        )
        pdh_probe.set_previous_sessions(prev_sessions)
        pdh_result = pdh_probe.evaluate(session)
        assert pdh_result.status == SignalStatus.SIGNAL
        assert pdh_result.entry_timestamp_ms == _ms(9)
        assert pdh_result.entry_timestamp_ms != current_bar_time_ms

        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=prev_sessions,
            orb_detector=_orb_long_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
        )
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("ORB_HIGH",)
        assert out[0].signal.entry_timestamp_ms == current_bar_time_ms


# ═════════════════════════════════════════════════════════════════════════
# M8 — both historical
# ═════════════════════════════════════════════════════════════════════════

class TestM8BothHistorical:
    def test_zero_executable_candidates(self):
        bars = _long_shared_candle_bars()
        # One quiet filler bar after the shared SIGNAL bar -- current
        # bar advances past it, so both ORB and PDH become historical.
        bars.append(_c(10, 101.20, 101.35, 101.05, 101.15))
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]

        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdh=101.01),
            orb_detector=_orb_long_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
        )
        assert out == []


# ═════════════════════════════════════════════════════════════════════════
# M9 — stale current signal after restart boundary
# ═════════════════════════════════════════════════════════════════════════

class TestM9StaleAfterRestartBoundary:
    def test_zero_candidates_even_though_current_bar_condition_holds(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]

        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdh=101.01),
            orb_detector=_orb_long_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
            live_boundary_ms=current_bar_time_ms + 1,
        )
        assert out == []


# ═════════════════════════════════════════════════════════════════════════
# M10 — consumed ORB setup
# ═════════════════════════════════════════════════════════════════════════

class TestM10ConsumedOrbSetup:
    def test_orb_excluded_pdh_survives(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]

        orb_detector = _orb_long_detector()
        probe = orb_detector.evaluate(session)
        assert probe.status == SignalStatus.SIGNAL

        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdh=101.01),
            orb_detector=orb_detector, current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
            consumed_setup_keys={probe.setup_key},
        )
        # ORB's own setup_key is consumed, but the SAME rejection candle's
        # PDH observation carries a DIFFERENT setup_key (source-specific
        # break timestamp), so it is not excluded by this consumed key.
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("PREVIOUS_DAY_HIGH",)


# ═════════════════════════════════════════════════════════════════════════
# M11 — consumed PDH signal
# ═════════════════════════════════════════════════════════════════════════

class TestM11ConsumedPdhSignal:
    def test_pdh_excluded_orb_survives(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]

        from trading_lab.live.pdh_pdl_candidate_evaluator import evaluate_pdh_pdl_candidate
        probe = evaluate_pdh_pdl_candidate(
            session, _prev_sessions(pdh=101.01), symbol="QQQ", direction="LONG",
            tick_size=0.01, market_timezone="America/New_York", session_open="09:30",
        )
        pdh_signal_key = probe["pdh_pdl_result"].signal_key
        assert pdh_signal_key is not None

        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdh=101.01),
            orb_detector=_orb_long_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
            consumed_signal_keys={pdh_signal_key},
        )
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("ORB_HIGH",)


# ═════════════════════════════════════════════════════════════════════════
# M12 — opposite direction, same current bar (hand-built; see module
# docstring for why a genuine same-candle LONG+SHORT completion cannot
# occur through the real pipeline under the CONFIRMATION_CLOSE entry
# model)
# ═════════════════════════════════════════════════════════════════════════

def _bar_stub(ts, o, h, l, c):
    return Bar(
        bar_utc_ms=ts,
        open=PriceTicks(ticks=int(round(o * 100)), tick_size="0.01"),
        high=PriceTicks(ticks=int(round(h * 100)), tick_size="0.01"),
        low=PriceTicks(ticks=int(round(l * 100)), tick_size="0.01"),
        close=PriceTicks(ticks=int(round(c * 100)), tick_size="0.01"),
        volume=1000,
    )


def _signal_stub(*, direction, entry_ts, level_source, confirmation_bar):
    dr = SimpleNamespace(confirmation_bar=confirmation_bar)
    return SignalResult(
        status=SignalStatus.SIGNAL,
        direction=direction,
        entry_price=Decimal("101.00") if direction == "LONG" else Decimal("99.00"),
        stop_price=Decimal("100.00") if direction == "LONG" else Decimal("100.00"),
        target_price=Decimal("103.00") if direction == "LONG" else Decimal("97.00"),
        entry_timestamp_ms=entry_ts,
        detection_result=dr,
        trade_plan=None,
        setup_key=f"{direction}:{level_source}:{entry_ts - 500}",
        signal_key=f"{direction}:{level_source}:{entry_ts - 500}:{entry_ts}",
        pipeline_stage="SIGNAL",
        stage_context={"level_source": level_source},
    )


class TestM12OppositeDirectionSameCurrentBar:
    def test_two_candidates_never_merged(self):
        bar = _bar_stub(1_000, 100.5, 101.5, 100.0, 101.2)
        s_long = _signal_stub(direction="LONG", entry_ts=1_000, level_source="ORB_HIGH",
                               confirmation_bar=bar)
        s_short = _signal_stub(direction="SHORT", entry_ts=1_000, level_source="PREVIOUS_DAY_LOW",
                                confirmation_bar=bar)
        # Routed through the SAME collect_actionable_signals() the real
        # collector uses internally -- not a separate/duplicated check.
        out = collect_actionable_signals(
            [
                SignalObservation(symbol="QQQ", signal=s_long),
                SignalObservation(symbol="QQQ", signal=s_short),
            ],
            current_bar_time_ms=1_000,
        )
        assert len(out) == 2
        assert {c.signal.direction for c in out} == {"LONG", "SHORT"}


# ═════════════════════════════════════════════════════════════════════════
# M13 — PDH eligibility false (proves the real eligibility gate is used,
# not bypassed)
# ═════════════════════════════════════════════════════════════════════════

class TestM13PdhEligibilityFalse:
    def test_no_pdh_candidate_when_ineligible(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]
        # PDH = 100.95, BELOW orb_high=101.00 -> WRONG_GEOMETRY, ineligible.
        prev_sessions = _prev_sessions(pdh=100.95)

        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=prev_sessions,
            orb_detector=_orb_long_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
        )
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("ORB_HIGH",)  # PDH never appears

        # Prove this isn't just "geometry happened not to produce a
        # signal" -- confirm eligibility itself explicitly says False,
        # AND that a raw detector bypassing eligibility WOULD have found
        # a structure with the identical candles/level (i.e. the gate is
        # doing real suppression work, not a no-op).
        from trading_lab.session_context import build_session_context
        eligibility_config = {
            "timeframe_minutes": 1, "timezone": "America/New_York",
            "session_open": "09:30", "orb_start": "session_open",
            "orb_duration_minutes": 5, "level_source": "ORB_HIGH",
            "direction": "LONG", "tick_size": 0.01,
            "consecutive_orb_closes": 2,
        }
        session_context = build_session_context(session["candles"], eligibility_config)
        assert session_context["status"] == "OK"
        eligibility = check_orb_to_level_eligibility(
            session_context["candles"], session_context, eligibility_config,
            candidate_level_price=100.95,
        )
        assert eligibility["eligible"] is False
        assert eligibility["reason"] == "WRONG_GEOMETRY"

        raw_bypass_detector = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
            level_source="PREVIOUS_DAY_HIGH",
        )
        raw_bypass_detector.set_previous_sessions(prev_sessions)
        raw_result = raw_bypass_detector.evaluate(session)
        assert raw_result.status == SignalStatus.SIGNAL  # would have "worked" if bypassed


# ═════════════════════════════════════════════════════════════════════════
# M14 — no previous-session level
# ═════════════════════════════════════════════════════════════════════════

class TestM14NoPreviousSessionLevel:
    def test_no_crash_orb_only(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]

        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=None,
            orb_detector=_orb_long_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
        )
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("ORB_HIGH",)


# ═════════════════════════════════════════════════════════════════════════
# M15 — determinism / purity
# ═════════════════════════════════════════════════════════════════════════

class TestM15DeterminismAndPurity:
    def test_same_input_same_output_and_no_mutation(self):
        """Determinism here means every TRADING-relevant field is
        identical across repeated calls: status, direction, entry/stop/
        target price, entry_timestamp_ms, setup_key, signal_key, and
        contributing_level_sources. It does NOT mean byte-for-byte
        SignalResult equality -- DetectionResult (nested inside
        SignalResult.detection_result) carries its own fresh
        result_id (UUID) and produced_at (timestamp) provenance stamps
        on every call, by design (see contracts/detection_result.py),
        completely independent of this collector. That is expected and
        is not a purity violation of collect_multi_source_signals()
        itself -- it is the underlying detector's own documented
        provenance behavior, which this function neither reads nor
        relies on for any of its own logic."""
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]
        prev_sessions = _prev_sessions(pdh=101.01)
        consumed_setups = {"SOME:OTHER:KEY"}
        consumed_setups_snapshot = set(consumed_setups)
        consumed_signals = {"SOME:OTHER:SIGNAL"}
        consumed_signals_snapshot = set(consumed_signals)

        # A single ORB detector instance is reused across both calls to
        # confirm evaluate() is a pure function of its own arguments,
        # aside from its own documented _last_result cache attribute.
        orb_detector = _orb_long_detector()

        out1 = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=prev_sessions,
            orb_detector=orb_detector, current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
            consumed_setup_keys=consumed_setups, consumed_signal_keys=consumed_signals,
        )
        out2 = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=prev_sessions,
            orb_detector=orb_detector, current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
            consumed_setup_keys=consumed_setups, consumed_signal_keys=consumed_signals,
        )

        def _trading_relevant(candidates):
            return [
                (c.signal.status, c.signal.direction, c.signal.entry_price,
                 c.signal.stop_price, c.signal.target_price,
                 c.signal.entry_timestamp_ms, c.signal.setup_key,
                 c.signal.signal_key, c.contributing_level_sources)
                for c in candidates
            ]

        assert len(out1) == len(out2) == 1
        assert _trading_relevant(out1) == _trading_relevant(out2)
        assert consumed_setups == consumed_setups_snapshot
        assert consumed_signals == consumed_signals_snapshot
        # session dict itself untouched (candle list identity/content same)
        assert session["candles"] == _build_session(bars)["candles"]

    def test_orb_detector_evaluate_is_stateless_aside_from_documented_cache(self):
        """Confirms (does not just assume) LiveSignalDetector.evaluate()
        is a pure function of (session, consumed_setup_keys) in every
        trading-relevant respect: calling it directly, repeatedly, with
        the same arguments, returns the same status/direction/prices/
        entry_timestamp_ms/setup_key/signal_key regardless of how many
        times it has been called before. Its only side effects are (a)
        refreshing its own `last_result` cache property, which this
        collector never reads, and (b) DetectionResult's own fresh
        result_id/produced_at provenance stamps each call (see the note
        on the test above) -- neither affects this collector's return
        value in any way that matters to a caller."""
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        orb_detector = _orb_long_detector()

        r1 = orb_detector.evaluate(session)
        r2 = orb_detector.evaluate(session)
        r3 = orb_detector.evaluate(session)

        def _key(r):
            return (r.status, r.direction, r.entry_price, r.stop_price,
                    r.target_price, r.entry_timestamp_ms, r.setup_key, r.signal_key)

        assert _key(r1) == _key(r2) == _key(r3)
        assert orb_detector.last_result == r3


# ═════════════════════════════════════════════════════════════════════════
# Rule 1 guardrail — PDH/PDL always eligibility-gated (static + behavioral)
# ═════════════════════════════════════════════════════════════════════════

class TestRule1Guardrail:
    def test_module_imports_the_real_eligibility_gated_evaluator(self):
        import ast
        import inspect
        from trading_lab.live import multi_source_signal_collector as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "evaluate_pdh_pdl_candidate" in imported_names
        # Never imports a shortcut around the eligibility gate.
        assert "check_orb_to_level_eligibility" not in imported_names

    def test_ineligible_pdh_never_produces_a_candidate(self):
        # Same behavioral proof as M13, referenced here explicitly as
        # the Rule 1 guardrail's behavioral half.
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        current_bar_time_ms = bars[-1]["time_ms"]
        out = collect_multi_source_signals(
            symbol="QQQ", session=session, previous_sessions=_prev_sessions(pdh=100.50),
            orb_detector=_orb_long_detector(), current_bar_time_ms=current_bar_time_ms,
            tick_size=0.01, pdh_pdl_directions=("LONG",),
        )
        assert all(
            "PREVIOUS_DAY_HIGH" not in c.contributing_level_sources for c in out
        )


# ═════════════════════════════════════════════════════════════════════════
# No premarket continuation — static guardrail
# ═════════════════════════════════════════════════════════════════════════

class TestNoPremarketContinuation:
    def test_module_does_not_import_premarket_machinery(self):
        # AST-based, not a raw substring search: this module's own
        # docstring legitimately names these symbols in prose (to
        # document what it deliberately does NOT do), so a substring
        # check on full source would false-positive on its own
        # documentation. This checks actual import statements only.
        import ast
        import inspect
        from trading_lab.live import multi_source_signal_collector as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        for forbidden in (
            "premarket_observed_structure", "carry_in_separation",
            "evaluate_seeded", "premarket_context",
        ):
            assert forbidden not in imported_names


# ═════════════════════════════════════════════════════════════════════════
# No runtime wiring — static guardrail
# ═════════════════════════════════════════════════════════════════════════

class TestNoRuntimeWiring:
    def test_not_imported_by_bot_runner_or_orchestrator(self):
        import inspect
        from trading_lab.live import bot_runner, trade_orchestrator
        for mod in (bot_runner, trade_orchestrator):
            src = inspect.getsource(mod)
            assert "multi_source_signal_collector" not in src
            assert "collect_multi_source_signals" not in src
