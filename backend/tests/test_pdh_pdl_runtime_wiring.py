"""Tests for MaxBotRunner._update_pdh_pdl_candidate() — micro-task 15.

Observational-only wiring: after the ORB orchestrator processes a bar,
the runner separately runs evaluate_pdh_pdl_candidate() and stores the
result on rt.pdh_pdl_candidate, purely for future PWA/audit display.

    - Never touches TradeOrchestrator/ObserveOrchestrator.
    - Never creates a pending order.
    - Never talks to IBKR.
    - The ORB detector/orchestrator path is completely unaffected.

Cases covered (exactly as specified):
    R1 no previous_sessions       -> no crash
    R2 previous_sessions present  -> candidate evaluator called
    R3 PDH SIGNAL                 -> visible in runtime, NOT pending execution
    R4 ORB path unchanged         -> identical result before/after wiring
    R5 two different symbols      -> no cross-contamination
"""

from __future__ import annotations

from unittest.mock import MagicMock

from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.watchlist import SymbolRuntime


MS_0930 = 1786455000000


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _c(offset_min, o, h, l, cl):
    return {"time_ms": _ms(offset_min), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


def _prev_sessions(pdh=None, pdl=None):
    return [{
        "date": "2026-08-10",
        "candles": [{
            "time_ms": 1, "open": 100.0,
            "high": pdh if pdh is not None else 105.0,
            "low": pdl if pdl is not None else 95.0,
            "close": 100.5, "volume": 500,
        }],
    }]


def _orb_bars():
    """5 ORB bars (idx0-4): ORB high=101.00, low=99.00."""
    return [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]


def _orb_long_eligible_bars():
    """ORB + LONG break + 3 valid displacement bars + ORB retest
    contact -> ORB displacement complete -> eligible (given PDH > 101),
    but no PDH-specific structure yet."""
    bars = _orb_bars()
    bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))   # ORB break
    bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))   # ORB disp 1/3
    bars.append(_c(7, 101.60, 101.90, 101.30, 101.70))   # ORB disp 2/3
    bars.append(_c(8, 101.70, 101.85, 101.10, 101.40))   # ORB disp 3/3
    bars.append(_c(9, 101.20, 101.40, 100.90, 101.10))   # ORB contact
    return bars


def _pdh_full_signal_bars(pdh_level=103.00):
    """Full session: ORB structure (eligible) + independent PDH BDRR
    sequence, built by shifting the already-validated ORB break/
    displacement/rejection geometry — same technique validated in
    test_pdh_pdl_candidate_evaluator.py."""
    shift = pdh_level - 101.00
    bars = _orb_long_eligible_bars()
    bars.append(_c(10, 100.80 + shift, 101.60 + shift, 100.70 + shift, 101.50 + shift))
    bars.append(_c(11, 101.55 + shift, 101.80 + shift, 101.20 + shift, 101.60 + shift))
    bars.append(_c(12, 101.60 + shift, 101.90 + shift, 101.30 + shift, 101.70 + shift))
    bars.append(_c(13, 101.70 + shift, 101.85 + shift, 101.10 + shift, 101.40 + shift))
    bars.append(_c(14, 101.10 + shift, 101.30 + shift, 100.80 + shift, 101.20 + shift))
    return bars


def _make_runner(symbols, direction="LONG"):
    runner = MaxBotRunner(symbols, direction=direction, tick_size=0.01)
    runner._ib = MagicMock()
    return runner


def _rt_with_bars(symbol, bars, previous_sessions=None):
    rt = SymbolRuntime(symbol=symbol)
    rt.session_builder = LiveSessionBuilder(symbol)
    for b in bars:
        rt.session_builder.add_bar(b)
    rt.previous_sessions = previous_sessions
    return rt


# ═════════════════════════════════════════════════════════════════════════
# R1 — no previous_sessions -> no crash
# ═════════════════════════════════════════════════════════════════════════

class TestR1NoPreviousSessionsNoCrash:
    def test_no_crash_without_previous_sessions(self):
        runner = _make_runner(["QQQ"], direction="LONG")
        rt = _rt_with_bars("QQQ", _orb_long_eligible_bars(), previous_sessions=None)

        # Must not raise.
        runner._update_pdh_pdl_candidate(rt)

        assert rt.pdh_pdl_candidate is not None
        assert "LONG" in rt.pdh_pdl_candidate
        assert rt.pdh_pdl_candidate["LONG"]["eligible"] is False
        assert rt.pdh_pdl_candidate["LONG"]["eligibility"]["reason"] == "NO_PREVIOUS_SESSIONS"
        assert rt.pdh_pdl_candidate["LONG"]["signal_result"] is None

    def test_no_crash_with_no_session_yet(self):
        """No bars fed at all — session_builder.current_session() is
        None. Must return cleanly without touching rt.pdh_pdl_candidate."""
        runner = _make_runner(["QQQ"], direction="LONG")
        rt = SymbolRuntime(symbol="QQQ")
        rt.session_builder = LiveSessionBuilder("QQQ")
        rt.previous_sessions = None

        runner._update_pdh_pdl_candidate(rt)  # must not raise
        assert rt.pdh_pdl_candidate is None  # untouched — nothing to evaluate yet


# ═════════════════════════════════════════════════════════════════════════
# R2 — previous_sessions present -> candidate evaluator called
# ═════════════════════════════════════════════════════════════════════════

class TestR2PreviousSessionsPresentEvaluatorCalled:
    def test_candidate_evaluator_invoked_and_result_stored(self):
        runner = _make_runner(["QQQ"], direction="LONG")
        rt = _rt_with_bars(
            "QQQ", _orb_long_eligible_bars(), previous_sessions=_prev_sessions(pdh=105.00),
        )

        runner._update_pdh_pdl_candidate(rt)

        assert rt.pdh_pdl_candidate is not None
        entry = rt.pdh_pdl_candidate["LONG"]
        assert entry["direction"] == "LONG"
        assert entry["level_source"] == "PREVIOUS_DAY_HIGH"
        # Eligible=True proves the real eligibility + detector pipeline
        # actually ran (not skipped), even though no PDH break exists
        # yet in these candles (signal_result is a real NO_SETUP).
        assert entry["eligible"] is True
        assert entry["signal_result"] is not None
        assert entry["signal_result"].status == SignalStatus.NO_SETUP


# ═════════════════════════════════════════════════════════════════════════
# R3 — PDH SIGNAL visible in runtime but NOT pending execution
# ═════════════════════════════════════════════════════════════════════════

class TestR3PdhSignalVisibleNotExecuted:
    def test_pdh_signal_visible_but_orchestrator_untouched(self):
        runner = _make_runner(["QQQ"], direction="LONG")
        rt = _rt_with_bars(
            "QQQ", _pdh_full_signal_bars(pdh_level=103.00),
            previous_sessions=_prev_sessions(pdh=103.00),
        )
        # A mock orchestrator standing in for the real one — proves
        # _update_pdh_pdl_candidate never reads or writes it.
        rt.orchestrator = MagicMock()
        rt.orchestrator.has_pending_signal = False

        runner._update_pdh_pdl_candidate(rt)

        entry = rt.pdh_pdl_candidate["LONG"]
        assert entry["eligible"] is True
        assert entry["signal_result"] is not None
        assert entry["signal_result"].status == SignalStatus.SIGNAL
        assert entry["signal_result"].direction == "LONG"
        assert entry["signal_result"].setup_key.startswith("LONG:PREVIOUS_DAY_HIGH:")

        # Decisive assertion: the mock orchestrator was never touched —
        # no attribute was read/written by _update_pdh_pdl_candidate,
        # and pending-signal state is exactly what it was before.
        rt.orchestrator.on_bar.assert_not_called()
        assert rt.orchestrator.has_pending_signal is False
        assert rt.orchestrator.method_calls == []


# ═════════════════════════════════════════════════════════════════════════
# R4 — ORB path unchanged
# ═════════════════════════════════════════════════════════════════════════

class TestR4OrbPathUnchanged:
    def test_orb_result_identical_before_and_after_wiring(self):
        bars = _orb_long_eligible_bars()

        orb_detector_before = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
        )
        session_a = _rt_with_bars("QQQ", bars).session_builder.current_session()
        result_before = orb_detector_before.evaluate(session_a)

        runner = _make_runner(["QQQ"], direction="LONG")
        rt = _rt_with_bars("QQQ", bars, previous_sessions=_prev_sessions(pdh=105.00))
        runner._update_pdh_pdl_candidate(rt)

        orb_detector_after = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
        )
        result_after = orb_detector_after.evaluate(rt.session_builder.current_session())

        assert result_before.status == result_after.status
        assert result_before.failed_stage == result_after.failed_stage
        assert result_before.setup_key == result_after.setup_key
        assert result_before.pipeline_stage == result_after.pipeline_stage


# ═════════════════════════════════════════════════════════════════════════
# R5 — two different symbols -> no cross-contamination
# ═════════════════════════════════════════════════════════════════════════

class TestR5NoCrossContamination:
    def test_two_symbols_independent_candidates(self):
        runner = _make_runner(["QQQ", "NVDA"], direction="LONG")

        rt_qqq = _rt_with_bars(
            "QQQ", _pdh_full_signal_bars(pdh_level=103.00),
            previous_sessions=_prev_sessions(pdh=103.00),
        )
        rt_nvda = _rt_with_bars(
            "NVDA", _orb_long_eligible_bars(),
            previous_sessions=_prev_sessions(pdh=999.00),  # deliberately different
        )

        runner._update_pdh_pdl_candidate(rt_qqq)
        runner._update_pdh_pdl_candidate(rt_nvda)

        qqq_entry = rt_qqq.pdh_pdl_candidate["LONG"]
        nvda_entry = rt_nvda.pdh_pdl_candidate["LONG"]

        # QQQ reached a real SIGNAL; NVDA (different candles, different
        # previous_sessions) did not — and must not have been
        # influenced by QQQ's previous_sessions or result in any way.
        assert qqq_entry["signal_result"].status == SignalStatus.SIGNAL
        assert nvda_entry["signal_result"].status == SignalStatus.NO_SETUP

        assert rt_qqq.previous_sessions != rt_nvda.previous_sessions
        assert rt_qqq.pdh_pdl_candidate is not rt_nvda.pdh_pdl_candidate
        assert qqq_entry is not nvda_entry
