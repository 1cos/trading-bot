"""Tests for MaxBotRunner._update_premarket_context() — micro-task
"Add context-only PDH/PDL premarket classification to SymbolRuntime".

Observational-only: computed once at boot (when rt.premarket_bars and
rt.context_levels both become available), stores a pure history
classification on rt.premarket_context via
premarket_break_classifier.classify_premarket_context() — no
displacement, no retest, no sequence validity, no grading. Never read
by evaluate_pdh_pdl_candidate(), LiveSignalDetector, or any trading
code; can never change SIGNAL/NO_SETUP, entry, stop, target, or
setup_key.

Cases covered (exactly as specified):
    C1  NONE PDH
    C2  PREMARKET_OBSERVED PDH
    C3  PREMARKET_CARRY_IN PDH
    C4  PDL SHORT observed
    C5  missing premarket bars
    C6  missing PDH/PDL context levels
    C7  trading result unchanged
    C8  no trading consumers (static guard)
    C9  idempotence
    C10 existing runtime unchanged (see separate regression run)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.context_levels import ContextLevels
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import SignalStatus
from trading_lab.live.watchlist import SymbolRuntime


MS_0930 = 1786455000000
TICK = 0.01


def _ms_pm(minutes_before_open: int) -> int:
    return MS_0930 - minutes_before_open * 60_000


def _ms_rth(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _bar(t, o, h, l, cl):
    return {"time_ms": t, "open": o, "high": h, "low": l, "close": cl, "volume": 1000}


def _c(offset_min, o, h, l, cl):
    return _bar(_ms_rth(offset_min), o, h, l, cl)


def _make_runner(symbols, direction="BOTH"):
    runner = MaxBotRunner(symbols, direction=direction, tick_size=TICK)
    runner._ib = MagicMock()
    return runner


def _rt(symbol="QQQ", pdh=None, pdl=None, premarket_bars=None, ctx_status="OK"):
    rt = SymbolRuntime(symbol=symbol)
    if ctx_status == "OK":
        rt.context_levels = ContextLevels(symbol=symbol, pdh=pdh, pdl=pdl, status="OK")
    else:
        rt.context_levels = None
    rt.premarket_bars = premarket_bars
    return rt


# ── Premarket fixtures ───────────────────────────────────────────────────────

def _pm_none_long():
    """Never crosses PDH=103.00."""
    return [
        _bar(_ms_pm(95), 102.00, 102.20, 101.90, 102.10),
        _bar(_ms_pm(90), 102.10, 102.30, 102.00, 102.20),
        _bar(_ms_pm(85), 102.20, 102.40, 102.10, 102.30),
    ]


def _pm_observed_long():
    """Real crossing candle at t=-90 (unbroken bar before it)."""
    return [
        _bar(_ms_pm(95), 102.40, 102.60, 102.30, 102.50),
        _bar(_ms_pm(90), 102.50, 103.60, 102.45, 103.50),
        _bar(_ms_pm(85), 103.50, 103.80, 103.55, 103.70),
    ]


def _pm_carry_in_long():
    """First available bar already broken."""
    return [
        _bar(_ms_pm(90), 103.20, 103.60, 103.10, 103.50),
        _bar(_ms_pm(85), 103.50, 103.80, 103.55, 103.70),
    ]


def _pm_observed_short():
    """Real crossing below PDL=97.00."""
    return [
        _bar(_ms_pm(95), 97.60, 97.70, 97.40, 97.50),
        _bar(_ms_pm(90), 97.50, 97.55, 96.40, 96.50),
        _bar(_ms_pm(85), 96.50, 96.45, 96.20, 96.30),
    ]


# ── PDH RTH fixture that produces a real SIGNAL (for C7) ────────────────────

def _pdh_signal_bars():
    shift = 2.00
    orb_bars = [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
        _c(5, 100.80, 101.60, 100.70, 101.50),
        _c(6, 101.55, 101.80, 101.20, 101.60),
        _c(7, 101.60, 101.90, 101.30, 101.70),
        _c(8, 101.70, 101.85, 101.10, 101.40),
        _c(9, 101.20, 101.40, 100.90, 101.10),
    ]
    return orb_bars + [
        _c(10, 100.80 + shift, 101.60 + shift, 100.70 + shift, 101.50 + shift),
        _c(11, 101.55 + shift, 101.80 + shift, 101.20 + shift, 101.60 + shift),
        _c(12, 101.60 + shift, 101.90 + shift, 101.30 + shift, 101.70 + shift),
        _c(13, 101.70 + shift, 101.85 + shift, 101.10 + shift, 101.40 + shift),
        _c(14, 101.10 + shift, 101.30 + shift, 100.80 + shift, 101.20 + shift),
    ]


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


# ═════════════════════════════════════════════════════════════════════════
# C1 — NONE PDH
# ═════════════════════════════════════════════════════════════════════════

class TestC1NonePdh:
    def test_none_classification(self):
        runner = _make_runner(["QQQ"], direction="LONG")
        rt = _rt(pdh=103.00, premarket_bars=_pm_none_long())

        runner._update_premarket_context(rt)

        assert rt.premarket_context["LONG"]["break_origin"] == "NONE"
        assert rt.premarket_context["LONG"]["break_timestamp_ms"] is None
        assert rt.premarket_context["LONG"]["level_source"] == "PREVIOUS_DAY_HIGH"
        assert rt.premarket_context["LONG"]["level_price"] == 103.00


# ═════════════════════════════════════════════════════════════════════════
# C2 — PREMARKET_OBSERVED PDH
# ═════════════════════════════════════════════════════════════════════════

class TestC2ObservedPdh:
    def test_observed_classification_with_real_timestamp(self):
        runner = _make_runner(["QQQ"], direction="LONG")
        premarket_bars = _pm_observed_long()
        rt = _rt(pdh=103.00, premarket_bars=premarket_bars)

        runner._update_premarket_context(rt)

        assert rt.premarket_context["LONG"]["break_origin"] == "PREMARKET_OBSERVED"
        assert rt.premarket_context["LONG"]["break_timestamp_ms"] == premarket_bars[1]["time_ms"]


# ═════════════════════════════════════════════════════════════════════════
# C3 — PREMARKET_CARRY_IN PDH
# ═════════════════════════════════════════════════════════════════════════

class TestC3CarryInPdh:
    def test_carry_in_classification_no_timestamp(self):
        runner = _make_runner(["QQQ"], direction="LONG")
        rt = _rt(pdh=103.00, premarket_bars=_pm_carry_in_long())

        runner._update_premarket_context(rt)

        assert rt.premarket_context["LONG"]["break_origin"] == "PREMARKET_CARRY_IN"
        assert rt.premarket_context["LONG"]["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# C4 — PDL SHORT observed (mirror case)
# ═════════════════════════════════════════════════════════════════════════

class TestC4PdlShortObserved:
    def test_short_observed_classification(self):
        runner = _make_runner(["QQQ"], direction="SHORT")
        premarket_bars = _pm_observed_short()
        rt = _rt(pdl=97.00, premarket_bars=premarket_bars)

        runner._update_premarket_context(rt)

        assert rt.premarket_context["SHORT"]["break_origin"] == "PREMARKET_OBSERVED"
        assert rt.premarket_context["SHORT"]["break_timestamp_ms"] == premarket_bars[1]["time_ms"]
        assert rt.premarket_context["SHORT"]["level_source"] == "PREVIOUS_DAY_LOW"
        assert rt.premarket_context["SHORT"]["level_price"] == 97.00
        assert "LONG" not in rt.premarket_context


# ═════════════════════════════════════════════════════════════════════════
# C5 — Missing premarket bars
# ═════════════════════════════════════════════════════════════════════════

class TestC5MissingPremarketBars:
    def test_none_premarket_bars_no_crash(self):
        runner = _make_runner(["QQQ"], direction="LONG")
        rt = _rt(pdh=103.00, premarket_bars=None)

        runner._update_premarket_context(rt)  # must not raise

        assert rt.premarket_context["LONG"]["break_origin"] == "NONE"
        assert rt.premarket_context["LONG"]["break_timestamp_ms"] is None

    def test_empty_premarket_bars_no_crash(self):
        runner = _make_runner(["QQQ"], direction="LONG")
        rt = _rt(pdh=103.00, premarket_bars=[])

        runner._update_premarket_context(rt)  # must not raise

        assert rt.premarket_context["LONG"]["break_origin"] == "NONE"


# ═════════════════════════════════════════════════════════════════════════
# C6 — Missing PDH/PDL context levels
# ═════════════════════════════════════════════════════════════════════════

class TestC6MissingContextLevels:
    def test_no_context_levels_at_all(self):
        runner = _make_runner(["QQQ"], direction="LONG")
        rt = _rt(pdh=None, premarket_bars=_pm_observed_long(), ctx_status="MISSING")
        assert rt.context_levels is None

        runner._update_premarket_context(rt)  # must not raise

        assert rt.premarket_context is None  # untouched — nothing to compute yet

    def test_context_levels_present_but_pdh_none(self):
        runner = _make_runner(["QQQ"], direction="LONG")
        rt = _rt(pdh=None, premarket_bars=_pm_observed_long())  # ContextLevels(pdh=None)

        runner._update_premarket_context(rt)  # must not raise

        assert rt.premarket_context == {}  # no invented value — direction omitted

    def test_both_direction_only_available_level_populated(self):
        runner = _make_runner(["QQQ"], direction="BOTH")
        rt = _rt(pdh=103.00, pdl=None, premarket_bars=_pm_observed_long())

        runner._update_premarket_context(rt)

        assert "LONG" in rt.premarket_context
        assert "SHORT" not in rt.premarket_context


# ═════════════════════════════════════════════════════════════════════════
# C7 — Trading result unchanged
# ═════════════════════════════════════════════════════════════════════════

class TestC7TradingResultUnchanged:
    def test_pdh_candidate_identical_with_and_without_premarket(self):
        runner = _make_runner(["QQQ"], direction="LONG")

        def _make_rt(premarket_bars):
            rt = SymbolRuntime(symbol="QQQ")
            rt.session_builder = LiveSessionBuilder("QQQ")
            for b in _pdh_signal_bars():
                rt.session_builder.add_bar(b)
            rt.previous_sessions = _prev_sessions(pdh=103.00)
            rt.context_levels = ContextLevels(symbol="QQQ", pdh=103.00, pdl=95.00, status="OK")
            rt.premarket_bars = premarket_bars
            return rt

        rt_a = _make_rt([])                       # Run A: no premarket
        rt_b = _make_rt(_pm_observed_long())       # Run B: PREMARKET_OBSERVED

        runner._update_premarket_context(rt_a)
        runner._update_pdh_pdl_candidate(rt_a)

        runner._update_premarket_context(rt_b)
        runner._update_pdh_pdl_candidate(rt_b)

        # premarket_context differs as expected...
        assert rt_a.premarket_context["LONG"]["break_origin"] == "NONE"
        assert rt_b.premarket_context["LONG"]["break_origin"] == "PREMARKET_OBSERVED"

        # ...but every trading-relevant field is byte-identical.
        cand_a = rt_a.pdh_pdl_candidate["LONG"]
        cand_b = rt_b.pdh_pdl_candidate["LONG"]

        assert cand_a["eligible"] == cand_b["eligible"]
        assert cand_a["eligibility"] == cand_b["eligibility"]

        sig_a, sig_b = cand_a["signal_result"], cand_b["signal_result"]
        assert sig_a.status == sig_b.status == SignalStatus.SIGNAL
        assert sig_a.stage_context["break_bar_index"] == sig_b.stage_context["break_bar_index"]
        assert sig_a.entry_price == sig_b.entry_price
        assert sig_a.stop_price == sig_b.stop_price
        assert sig_a.target_price == sig_b.target_price
        assert sig_a.setup_key == sig_b.setup_key


# ═════════════════════════════════════════════════════════════════════════
# C8 — No trading consumers (static guard)
# ═════════════════════════════════════════════════════════════════════════

class TestC8NoTradingConsumers:
    def test_premarket_context_not_referenced_by_trading_code(self):
        import trading_lab.live.pdh_pdl_candidate_evaluator as candidate_mod
        import trading_lab.live.signal_detector as detector_mod
        import trading_lab.live.trade_orchestrator as orchestrator_mod
        import trading_lab.live.observe_orchestrator as observe_mod

        for mod in (candidate_mod, detector_mod, orchestrator_mod, observe_mod):
            source = open(mod.__file__, encoding="utf-8").read()
            assert "premarket_context" not in source, (
                f"{mod.__name__} must never reference premarket_context — "
                f"it is observational context only, not a trading input"
            )

    def test_bot_runner_only_producer(self):
        """bot_runner.py IS expected to reference it (it's the producer)
        — this test documents that as the single, intentional exception."""
        import trading_lab.live.bot_runner as runner_mod
        source = open(runner_mod.__file__, encoding="utf-8").read()
        assert "premarket_context" in source


# ═════════════════════════════════════════════════════════════════════════
# C9 — Idempotence
# ═════════════════════════════════════════════════════════════════════════

class TestC9Idempotence:
    def test_repeated_call_identical_result(self):
        runner = _make_runner(["QQQ"], direction="BOTH")
        rt = _rt(pdh=103.00, pdl=97.00, premarket_bars=_pm_observed_long())

        runner._update_premarket_context(rt)
        first = rt.premarket_context

        runner._update_premarket_context(rt)
        second = rt.premarket_context

        assert first == second
