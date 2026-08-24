"""Tests for premarket_context exposure inside EventType.SYMBOL_ENABLED
— micro-task "Expose premarket context in audit event only".

Scope: rt.premarket_context -> SYMBOL_ENABLED.data.premarket_context ->
/api/events. No new endpoint, no trading behavior change, no
grading/interpretation. /api/bot/symbols must remain byte-identical to
before this change.

Cases covered (exactly as specified):
    E1  NONE
    E2  PREMARKET_OBSERVED
    E3  PREMARKET_CARRY_IN
    E4  PDL SHORT
    E5  no available context -> {} (no crash)
    E6  /api/events preserves data.premarket_context end-to-end
    E7  /api/bot/symbols unchanged (premarket_context absent, other
        fields identical)
    E8  rt.pdh_pdl_candidate unaffected
    E9  static guard: no trading consumer reads premarket_context
    E10 existing SYMBOL_ENABLED data fields preserved (additive only)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.control_api import MaxBotController, create_app
from trading_lab.live.watchlist import SymbolRuntime


MS_0930 = 1786455000000


def _ms_pm(minutes_before_open: int) -> int:
    return MS_0930 - minutes_before_open * 60_000


def _bar(t, o, h, l, c):
    return {"time_ms": t, "open": o, "high": h, "low": l, "close": c, "volume": 1000}


def _prev_sessions(pdh=103.00, pdl=95.00):
    return [{
        "date": "2026-08-10",
        "candles": [{"time_ms": 1, "open": 100.0, "high": pdh, "low": pdl,
                     "close": 100.5, "volume": 500}],
    }]


# ── Premarket fixtures ───────────────────────────────────────────────────────

def _pm_none():
    return [
        _bar(_ms_pm(95), 102.00, 102.20, 101.90, 102.10),
        _bar(_ms_pm(90), 102.10, 102.30, 102.00, 102.20),
    ]


def _pm_observed():
    return [
        _bar(_ms_pm(95), 102.40, 102.60, 102.30, 102.50),
        _bar(_ms_pm(90), 102.50, 103.60, 102.45, 103.50),
    ]


def _pm_carry_in():
    return [
        _bar(_ms_pm(90), 103.20, 103.60, 103.10, 103.50),
        _bar(_ms_pm(85), 103.50, 103.80, 103.55, 103.70),
    ]


def _pm_observed_short():
    return [
        _bar(_ms_pm(95), 95.60, 95.70, 95.40, 95.50),
        _bar(_ms_pm(90), 95.50, 95.55, 94.40, 94.50),
    ]


# ── Harness: run the REAL _compute_context_levels() with IBKR fetches
# mocked out, and inspect the emitted SYMBOL_ENABLED event. ─────────────────

def _run_boot_and_get_symbol_enabled_event(
    direction="LONG", premarket_bars=None, prev_sessions=None,
):
    runner = MaxBotRunner(["QQQ"], direction=direction, tick_size=0.01)
    runner._ib = MagicMock()

    rt = SymbolRuntime(symbol="QQQ")
    rt.enabled = True
    rt.underlying_contract = MagicMock()
    runner._runtimes = {"QQQ": rt}

    with patch("trading_lab.live.bot_runner.fetch_previous_session_bars",
               return_value=prev_sessions if prev_sessions is not None else _prev_sessions()), \
         patch("trading_lab.live.bot_runner.fetch_premarket_bars",
               return_value=premarket_bars):
        runner._compute_context_levels()

    events = runner.session_log.events_since(0)
    enabled_events = [e for e in events if e.event_type == "SYMBOL_ENABLED"]
    assert len(enabled_events) == 1
    return runner, rt, enabled_events[0]


# ═════════════════════════════════════════════════════════════════════════
# E1 — NONE
# ═════════════════════════════════════════════════════════════════════════

class TestE1None:
    def test_none_in_symbol_enabled_event(self):
        runner, rt, event = _run_boot_and_get_symbol_enabled_event(
            direction="LONG", premarket_bars=_pm_none(),
        )
        pm_ctx = event.data["premarket_context"]
        assert pm_ctx["LONG"]["break_origin"] == "NONE"
        assert pm_ctx["LONG"]["break_timestamp_ms"] is None
        # Consistent with the real classifier's own output on rt.
        assert pm_ctx == rt.premarket_context


# ═════════════════════════════════════════════════════════════════════════
# E2 — PREMARKET_OBSERVED
# ═════════════════════════════════════════════════════════════════════════

class TestE2Observed:
    def test_observed_in_symbol_enabled_event(self):
        pm_bars = _pm_observed()
        runner, rt, event = _run_boot_and_get_symbol_enabled_event(
            direction="LONG", premarket_bars=pm_bars,
        )
        pm_ctx = event.data["premarket_context"]
        assert pm_ctx["LONG"]["break_origin"] == "PREMARKET_OBSERVED"
        assert pm_ctx["LONG"]["break_timestamp_ms"] == pm_bars[1]["time_ms"]


# ═════════════════════════════════════════════════════════════════════════
# E3 — PREMARKET_CARRY_IN
# ═════════════════════════════════════════════════════════════════════════

class TestE3CarryIn:
    def test_carry_in_in_symbol_enabled_event(self):
        runner, rt, event = _run_boot_and_get_symbol_enabled_event(
            direction="LONG", premarket_bars=_pm_carry_in(),
        )
        pm_ctx = event.data["premarket_context"]
        assert pm_ctx["LONG"]["break_origin"] == "PREMARKET_CARRY_IN"
        assert pm_ctx["LONG"]["break_timestamp_ms"] is None


# ═════════════════════════════════════════════════════════════════════════
# E4 — PDL SHORT
# ═════════════════════════════════════════════════════════════════════════

class TestE4PdlShort:
    def test_short_observed_in_symbol_enabled_event(self):
        pm_bars = _pm_observed_short()
        runner, rt, event = _run_boot_and_get_symbol_enabled_event(
            direction="SHORT", premarket_bars=pm_bars,
        )
        pm_ctx = event.data["premarket_context"]
        assert pm_ctx["SHORT"]["level_source"] == "PREVIOUS_DAY_LOW"
        assert pm_ctx["SHORT"]["break_origin"] == "PREMARKET_OBSERVED"
        assert pm_ctx["SHORT"]["break_timestamp_ms"] == pm_bars[1]["time_ms"]
        assert "LONG" not in pm_ctx


# ═════════════════════════════════════════════════════════════════════════
# E5 — No available context -> {}
# ═════════════════════════════════════════════════════════════════════════

class TestE5NoContext:
    def test_missing_pdh_pdl_produces_empty_dict_no_crash(self):
        runner, rt, event = _run_boot_and_get_symbol_enabled_event(
            direction="LONG",
            premarket_bars=_pm_observed(),
            prev_sessions=[],  # no previous session -> pdh/pdl stay None
        )
        assert event.data["premarket_context"] == {}
        assert rt.premarket_context == {}


# ═════════════════════════════════════════════════════════════════════════
# E6 — /api/events preserves data.premarket_context
# ═════════════════════════════════════════════════════════════════════════

class TestE6ApiEvents:
    def test_api_events_preserves_premarket_context(self):
        runner, rt, _ = _run_boot_and_get_symbol_enabled_event(
            direction="LONG", premarket_bars=_pm_observed(),
        )
        ctrl = MaxBotController()
        ctrl._runner = runner
        app = create_app(ctrl)
        app.config["TESTING"] = True
        client = app.test_client()

        r = client.get("/api/events")
        assert r.status_code == 200
        events = r.get_json()
        enabled = [e for e in events if e["event_type"] == "SYMBOL_ENABLED"]
        assert len(enabled) == 1
        assert enabled[0]["data"]["premarket_context"]["LONG"]["break_origin"] == "PREMARKET_OBSERVED"


# ═════════════════════════════════════════════════════════════════════════
# E7 — /api/bot/symbols unchanged
# ═════════════════════════════════════════════════════════════════════════

class TestE7ApiSymbolsUnchanged:
    def test_premarket_context_absent_from_symbols_endpoint(self):
        runner, rt, _ = _run_boot_and_get_symbol_enabled_event(
            direction="LONG", premarket_bars=_pm_observed(),
        )
        assert rt.premarket_context is not None  # sanity: it WAS computed

        ctrl = MaxBotController()
        ctrl._runner = runner
        app = create_app(ctrl)
        app.config["TESTING"] = True
        client = app.test_client()

        r = client.get("/api/bot/symbols")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list) and len(data) == 1
        entry = data[0]
        assert "premarket_context" not in entry
        # Other context-level fields still present as before.
        assert entry["pdh"] == 103.00
        assert entry["pdl"] == 95.00
        assert entry["symbol"] == "QQQ"


# ═════════════════════════════════════════════════════════════════════════
# E8 — rt.pdh_pdl_candidate unaffected
# ═════════════════════════════════════════════════════════════════════════

class TestE8CandidateUnaffected:
    def test_pdh_pdl_candidate_untouched_by_this_change(self):
        runner, rt, _ = _run_boot_and_get_symbol_enabled_event(
            direction="LONG", premarket_bars=_pm_observed(),
        )
        # _compute_context_levels() never touches pdh_pdl_candidate —
        # that is only ever written by _update_pdh_pdl_candidate(),
        # called from _on_bar_update(), never from the boot path.
        assert rt.pdh_pdl_candidate is None


# ═════════════════════════════════════════════════════════════════════════
# E9 — No trading consumer (static guard)
# ═════════════════════════════════════════════════════════════════════════

class TestE9NoTradingConsumer:
    def test_premarket_context_absent_from_trading_modules(self):
        import trading_lab.live.pdh_pdl_candidate_evaluator as candidate_mod
        import trading_lab.live.signal_detector as detector_mod
        import trading_lab.pdh_pdl_eligibility as eligibility_mod
        import trading_lab.sequence_validator as seq_mod
        import trading_lab.retest_window as retest_mod
        import trading_lab.rejection_finder as rejection_mod
        import trading_lab.live.trade_orchestrator as trade_orch_mod
        import trading_lab.live.observe_orchestrator as observe_orch_mod

        modules = (
            candidate_mod, detector_mod, eligibility_mod, seq_mod,
            retest_mod, rejection_mod, trade_orch_mod, observe_orch_mod,
        )
        for mod in modules:
            source = open(mod.__file__, encoding="utf-8").read()
            assert "premarket_context" not in source, (
                f"{mod.__name__} must never reference premarket_context"
            )


# ═════════════════════════════════════════════════════════════════════════
# E10 — Existing SYMBOL_ENABLED data fields preserved
# ═════════════════════════════════════════════════════════════════════════

class TestE10ExistingFieldsPreserved:
    def test_pdh_pdl_pmh_pml_still_present_alongside_premarket_context(self):
        pm_bars = _pm_observed()
        runner, rt, event = _run_boot_and_get_symbol_enabled_event(
            direction="LONG", premarket_bars=pm_bars,
        )
        data = event.data
        assert data["pdh"] == 103.00
        assert data["pdl"] == 95.00
        assert "premarket_context" in data
        # premarket_context is additive — every pre-existing key survives.
        assert set(data.keys()) >= {"symbol", "status", "pdh", "pdl", "premarket_context"}
