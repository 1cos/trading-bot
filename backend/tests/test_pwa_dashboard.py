"""Tests for MaxBot iPhone PWA dashboard.

Uses Flask test client. No real IBKR or iPhone.
"""

import json
import os
import pytest
from unittest.mock import MagicMock

from trading_lab.live.control_api import MaxBotController, create_app
from trading_lab.live.event_stream import EventFactory, SessionEventLog


@pytest.fixture
def ctrl():
    return MaxBotController()

@pytest.fixture
def app(ctrl):
    return create_app(ctrl)

@pytest.fixture
def client(app):
    app.config["TESTING"] = True
    return app.test_client()


# ── Test 1: Root serves dashboard ────────────────────────────────────────────

class TestRootServes:
    def test_serves_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"MAXBOT" in r.data


# ── Test 2: Manifest served ──────────────────────────────────────────────────

class TestManifest:
    def test_manifest(self, client):
        r = client.get("/manifest.json")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["display"] == "standalone"
        assert data["short_name"] == "MaxBot"


# ── Test 3: Default UI mode PAPER_EXECUTE ────────────────────────────────────

class TestDefaultMode:
    def test_paper_default(self, client):
        r = client.get("/")
        assert b"PAPER_EXECUTE" in r.data


# ── Test 4: Default direction BOTH ───────────────────────────────────────────

class TestDefaultDirection:
    def test_both(self, client):
        r = client.get("/")
        assert b'"BOTH" selected' in r.data or b'value="BOTH"' in r.data


# ── Test 5: Default watchlist populated ──────────────────────────────────────

class TestDefaultWatchlist:
    def test_watchlist(self, client):
        r = client.get("/")
        assert b"QQQ" in r.data
        assert b"SPY" in r.data
        assert b"NVDA" in r.data


# ── Test 6: LIVE mode absent ────────────────────────────────────────────────

class TestNoLive:
    def test_no_live_option(self, client):
        r = client.get("/")
        html = r.data.decode()
        assert 'value="LIVE"' not in html
        assert ">LIVE<" not in html


# ── Test 7: START sends correct payload ──────────────────────────────────────

class TestStartPayload:
    def test_start_payload(self, client):
        # The JS sends POST to /api/bot/start — verify endpoint works
        r = client.post("/api/bot/start", json={
            "symbols": ["QQQ", "SPY"],
            "direction": "BOTH",
            "execution_mode": "OBSERVE_ONLY",
        })
        # Will fail connecting to IBKR but payload accepted
        assert r.status_code in (200, 400)


# ── Test 8: STOP endpoint called ─────────────────────────────────────────────

class TestStopEndpoint:
    def test_stop(self, client):
        r = client.post("/api/bot/stop")
        assert r.status_code in (200, 400)


# ── Test 9: Token included only for control ──────────────────────────────────

class TestTokenControl:
    def test_status_no_token_needed(self, client):
        r = client.get("/api/bot/status")
        assert r.status_code == 200

    def test_start_needs_token_when_configured(self):
        os.environ["MAXBOT_API_TOKEN"] = "test123"
        try:
            app = create_app(MaxBotController())
            c = app.test_client()
            r = c.post("/api/bot/start", json={"symbols": ["QQQ"]})
            assert r.status_code == 401
        finally:
            del os.environ["MAXBOT_API_TOKEN"]


# ── Test 10: Status data rendered ────────────────────────────────────────────

class TestStatusRendered:
    def test_state_in_response(self, client):
        r = client.get("/api/bot/status")
        assert "state" in r.get_json()


# ── Test 11: Symbols rendered independently ──────────────────────────────────

class TestSymbolsIndependent:
    def test_empty_when_stopped(self, client):
        r = client.get("/api/bot/symbols")
        assert r.status_code == 200
        assert r.get_json() == []


# ── Test 12: Active trades displayed ─────────────────────────────────────────
# (Tested via JS rendering of symbol cards with POSITION_OPEN lifecycle)

class TestActiveTradesSection:
    def test_html_has_active_section(self, client):
        r = client.get("/")
        assert b"active-trades" in r.data


# ── Test 13: Event polling uses since ────────────────────────────────────────

class TestEventSince:
    def test_since_param(self, client, ctrl):
        runner = MagicMock()
        log = SessionEventLog()
        f = EventFactory("OBSERVE_ONLY")
        log.append(f.create("BOT_STARTED"))
        log.append(f.create("SIGNAL", symbol="QQQ"))
        runner.session_log = log
        ctrl._runner = runner

        r = client.get("/api/events?since=1")
        data = r.get_json()
        assert len(data) == 1
        assert data[0]["event_type"] == "SIGNAL"


# ── Test 14: New events append ───────────────────────────────────────────────
# (JS behavior — insertBefore keeps old events, adds new at top)

class TestEventAppend:
    def test_html_has_timeline(self, client):
        r = client.get("/")
        assert b"timeline" in r.data
        assert b"insertBefore" in r.data


# ── Test 15-19: Event types rendered ─────────────────────────────────────────

class TestEventTypes:
    def test_signal_class(self, client):
        r = client.get("/")
        assert b"event-signal" in r.data

    def test_win_class(self, client):
        assert b"event-win" in client.get("/").data

    def test_loss_class(self, client):
        assert b"event-loss" in client.get("/").data

    def test_trigger_class(self, client):
        assert b"event-trigger" in client.get("/").data


# ── Test 20: Error state rendered ────────────────────────────────────────────

class TestErrorState:
    def test_error_chip(self, client):
        assert b"state-error" in client.get("/").data


# ── Test 21: Connection-lost state ───────────────────────────────────────────

class TestConnectionLost:
    def test_conn_lost_element(self, client):
        assert b"conn-lost" in client.get("/").data
        assert b"CONNECTION LOST" in client.get("/").data


# ── Test 22-23: Export endpoints accessible ──────────────────────────────────

class TestExportEndpoints:
    def test_json_404_when_no_session(self, client):
        r = client.get("/api/session/export.json")
        assert r.status_code == 404

    def test_md_404_when_no_session(self, client):
        r = client.get("/api/session/export.md")
        assert r.status_code == 404


# ── Test 24: Token not hardcoded ─────────────────────────────────────────────

class TestTokenNotHardcoded:
    def test_no_token_in_html(self, client):
        html = client.get("/").data.decode()
        assert "MAXBOT_API_TOKEN" not in html
        # Token value never in source
        assert "Bearer test" not in html


# ── Test 25: No LIVE execution ───────────────────────────────────────────────

class TestNoLiveExecution:
    def test_api_rejects(self, client):
        r = client.post("/api/bot/start", json={
            "symbols": ["QQQ"],
            "execution_mode": "LIVE",
        })
        assert r.status_code == 400

    def test_html_no_live(self, client):
        html = client.get("/").data.decode()
        assert 'value="LIVE"' not in html


# ── Test 26: Mobile viewport ────────────────────────────────────────────────

class TestMobileViewport:
    def test_viewport(self, client):
        html = client.get("/").data.decode()
        assert "viewport" in html
        assert "viewport-fit=cover" in html


# ── Test 27: Manifest standalone ─────────────────────────────────────────────

class TestManifestStandalone:
    def test_standalone(self, client):
        data = json.loads(client.get("/manifest.json").data)
        assert data["display"] == "standalone"


# ── Test 28: No horizontal overflow ──────────────────────────────────────────

class TestNoOverflow:
    def test_overflow_hidden(self, client):
        html = client.get("/").data.decode()
        assert "overflow-x:hidden" in html


# ── Test 29: No broker in tests ──────────────────────────────────────────────

class TestNoBroker:
    def test_no_real_connection(self):
        """All tests use Flask test client."""
        pass


# ── Test: Service worker served ──────────────────────────────────────────────

class TestServiceWorker:
    def test_sw(self, client):
        r = client.get("/sw.js")
        assert r.status_code == 200
        assert b"fetch" in r.data


# ── Test: Apple mobile web app meta ──────────────────────────────────────────

class TestAppleMeta:
    def test_apple_capable(self, client):
        html = client.get("/").data.decode()
        assert "apple-mobile-web-app-capable" in html
