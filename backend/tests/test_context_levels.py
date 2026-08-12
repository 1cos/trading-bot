"""Tests for live PDH/PDL context levels.

No real IBKR connection. Uses synthetic session data.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

from trading_lab.live.context_levels import (
    ContextLevels,
    compute_live_context_levels,
    _build_sessions_list,
)
from trading_lab.live.watchlist import SymbolRuntime
from trading_lab.live.bot_runner import MaxBotRunner


ET = ZoneInfo("America/New_York")


# ── Session fixtures ────────────────────────────────────────────────────────

def _session(date, bars):
    """Build a session dict from (high, low) pairs."""
    candles = [
        {"time_ms": i, "open": h, "high": h, "low": l, "close": l, "volume": 1000}
        for i, (h, l) in enumerate(bars)
    ]
    return {"date": date, "candles": candles}


FRIDAY = _session("2026-08-07", [(550.0, 545.0), (552.0, 548.0), (551.0, 546.0)])
MONDAY_SESSIONS = [FRIDAY]  # Monday uses Friday as previous


# ── Test 1: Previous RTH session high ────────────────────────────────────────

class TestPDH:
    def test_correct(self):
        sessions = [_session("2026-08-10", [(100.0, 95.0), (102.0, 97.0)])]
        ctx = compute_live_context_levels("QQQ", "2026-08-11", sessions)
        assert ctx.pdh == 102.0

    def test_single_bar(self):
        sessions = [_session("2026-08-10", [(585.50, 583.20)])]
        ctx = compute_live_context_levels("QQQ", "2026-08-11", sessions)
        assert ctx.pdh == 585.50


# ── Test 2: Previous RTH session low ────────────────────────────────────────

class TestPDL:
    def test_correct(self):
        sessions = [_session("2026-08-10", [(100.0, 95.0), (102.0, 97.0)])]
        ctx = compute_live_context_levels("QQQ", "2026-08-11", sessions)
        assert ctx.pdl == 95.0

    def test_single_bar(self):
        sessions = [_session("2026-08-10", [(585.50, 583.20)])]
        ctx = compute_live_context_levels("QQQ", "2026-08-11", sessions)
        assert ctx.pdl == 583.20


# ── Test 3: Weekend uses Friday ──────────────────────────────────────────────

class TestWeekend:
    def test_friday_for_monday(self):
        ctx = compute_live_context_levels("SPY", "2026-08-10", MONDAY_SESSIONS)
        assert ctx.pdh == 552.0  # Friday max high
        assert ctx.pdl == 545.0  # Friday min low
        assert ctx.prev_date == "2026-08-07"


# ── Test 4: Holiday uses most recent session ────────────────────────────────

class TestHoliday:
    def test_gap(self):
        sessions = [
            _session("2026-08-06", [(100.0, 95.0)]),
            # 2026-08-07 missing (holiday)
        ]
        ctx = compute_live_context_levels("QQQ", "2026-08-08", sessions)
        assert ctx.pdh == 100.0
        assert ctx.prev_date == "2026-08-06"


# ── Test 5: Each symbol gets independent PDH/PDL ────────────────────────────

class TestIndependent:
    def test_different_symbols(self):
        qqq_sessions = [_session("2026-08-10", [(585.0, 580.0)])]
        spy_sessions = [_session("2026-08-10", [(540.0, 535.0)])]

        qqq = compute_live_context_levels("QQQ", "2026-08-11", qqq_sessions)
        spy = compute_live_context_levels("SPY", "2026-08-11", spy_sessions)

        assert qqq.pdh == 585.0
        assert spy.pdh == 540.0
        assert qqq.pdl == 580.0
        assert spy.pdl == 535.0


# ── Test 6: PDH/PDL exposed in status API ───────────────────────────────────

class TestStatusAPI:
    def test_pdh_pdl_in_symbols(self):
        from trading_lab.live.control_api import MaxBotController, create_app
        ctrl = MaxBotController()
        runner = MagicMock()
        runner._execution_mode = SimpleNamespace(__eq__=lambda s, o: True)

        rt = SymbolRuntime(symbol="QQQ")
        rt.enabled = True
        rt.orchestrator = MagicMock()
        rt.orchestrator.lifecycle = "WAITING_FOR_SIGNAL"
        rt.context_levels = ContextLevels(
            symbol="QQQ", pdh=585.50, pdl=583.20, prev_date="2026-08-10",
        )
        runner._runtimes = {"QQQ": rt}
        ctrl._runner = runner
        ctrl._state = "RUNNING"

        symbols = ctrl.get_symbols()
        assert len(symbols) == 1
        assert symbols[0]["pdh"] == 585.50
        assert symbols[0]["pdl"] == 583.20
        assert symbols[0]["pdh_pdl_date"] == "2026-08-10"


# ── Test 7: PDH/PDL visible in PWA payload ──────────────────────────────────

class TestPWAPayload:
    def test_pwa_renders_pdh(self):
        from trading_lab.live.control_api import create_app
        app = create_app()
        client = app.test_client()
        html = client.get("/").data.decode()
        assert "PDH" in html
        assert "PDL" in html


# ── Test 8: PDH/PDL in session telemetry ────────────────────────────────────

class TestTelemetry:
    def test_context_in_event(self):
        from trading_lab.live.event_stream import EventFactory, EventType
        f = EventFactory("OBSERVE_ONLY")
        ev = f.create(EventType.SYMBOL_ENABLED, symbol="QQQ",
                       data={"pdh": 585.50, "pdl": 583.20, "prev_date": "2026-08-10"})
        assert ev.data["pdh"] == 585.50
        assert ev.data["pdl"] == 583.20


# ── Test 9: ORB-only signal behavior unchanged ──────────────────────────────

class TestORBUnchanged:
    def test_signal_detector_uses_orb(self):
        from trading_lab.live.signal_detector import LiveSignalDetector
        sd = LiveSignalDetector("QQQ", "LONG", 0.01)
        assert sd._engine_config["level_source"] == "ORB_HIGH"

    def test_short_detector_uses_orb_low(self):
        from trading_lab.live.signal_detector import LiveSignalDetector
        sd = LiveSignalDetector("QQQ", "SHORT", 0.01)
        assert sd._engine_config["level_source"] == "ORB_LOW"


# ── Test 10: No signal generated from PDH/PDL alone ─────────────────────────

class TestNoSignalFromPDH:
    def test_no_pdh_detector(self):
        import inspect
        import trading_lab.live.context_levels as mod
        source = inspect.getsource(mod)
        assert "LiveSignalDetector" not in source
        assert "find_break" not in source
        assert "find_displacement" not in source

    def test_no_pdh_level_source(self):
        from trading_lab.live.signal_detector import LiveSignalDetector
        sd = LiveSignalDetector("QQQ", "LONG", 0.01)
        assert "PREVIOUS_DAY" not in sd._engine_config["level_source"]


# ── Test 11: No previous session → status reports unavailable ────────────────

class TestNoPreviousSession:
    def test_no_sessions(self):
        ctx = compute_live_context_levels("QQQ", "2026-08-11", [])
        assert ctx.status == "NO_PREVIOUS_SESSION"
        assert ctx.pdh is None
        assert ctx.pdl is None


# ── Test 12: ContextLevels is frozen ─────────────────────────────────────────

class TestFrozen:
    def test_immutable(self):
        ctx = ContextLevels(symbol="QQQ", pdh=585.0, pdl=583.0)
        with pytest.raises(AttributeError):
            ctx.pdh = 999.0


# ── Test 13: to_dict serialization ───────────────────────────────────────────

class TestSerialization:
    def test_to_dict(self):
        ctx = ContextLevels(symbol="QQQ", pdh=585.50, pdl=583.20,
                            prev_date="2026-08-10")
        d = ctx.to_dict()
        assert d["symbol"] == "QQQ"
        assert d["pdh"] == 585.50
        assert d["pdl"] == 583.20
        assert d["prev_date"] == "2026-08-10"

    def test_unavailable_to_dict(self):
        ctx = ContextLevels(symbol="QQQ", status="NO_PREVIOUS_SESSION")
        d = ctx.to_dict()
        assert "pdh" not in d
        assert d["status"] == "NO_PREVIOUS_SESSION"


# ── Test 14: _build_sessions_list ────────────────────────────────────────────

class TestBuildSessionsList:
    def test_sorted(self):
        by_date = {
            "2026-08-11": [{"high": 100, "low": 95}],
            "2026-08-10": [{"high": 102, "low": 97}],
        }
        sessions = _build_sessions_list(by_date)
        assert sessions[0]["date"] == "2026-08-10"
        assert sessions[1]["date"] == "2026-08-11"


# ── Test 15: No IBKR in context_levels module ────────────────────────────────

class TestNoIBKRInCompute:
    def test_no_ib_in_compute(self):
        """compute_live_context_levels uses pdh_pdl_provider, not IBKR."""
        import inspect
        # Only fetch_previous_session_bars uses ib — compute doesn't
        source = inspect.getsource(compute_live_context_levels)
        assert "ib_insync" not in source
        assert "reqHistorical" not in source


# ── Test: Runner wires context levels ────────────────────────────────────────

class TestRunnerWiring:
    def test_runtime_has_field(self):
        rt = SymbolRuntime(symbol="QQQ")
        assert rt.context_levels is None  # default
        rt.context_levels = ContextLevels(symbol="QQQ", pdh=585.0, pdl=583.0)
        assert rt.context_levels.pdh == 585.0
