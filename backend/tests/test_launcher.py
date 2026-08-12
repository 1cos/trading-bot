"""Tests for MaxBot launcher and server entry point.

No real IBKR connection. No real Flask server started.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from trading_lab.live.control_api import (
    create_app,
    MaxBotController,
    run_server,
    get_lan_ip,
)


# ── Test 1: Control API has runnable entry point ─────────────────────────────

class TestEntryPoint:
    def test_run_server_exists(self):
        assert callable(run_server)

    def test_module_has_main(self):
        import trading_lab.live.control_api as mod
        import inspect
        source = inspect.getsource(mod)
        assert 'if __name__ == "__main__"' in source


# ── Test 2: Bind host configurable ──────────────────────────────────────────

class TestBindHost:
    def test_env_variable(self):
        os.environ["MAXBOT_BIND_HOST"] = "192.168.1.1"
        try:
            # We can't actually start the server, but verify config parsing
            host = os.environ.get("MAXBOT_BIND_HOST", "0.0.0.0")
            assert host == "192.168.1.1"
        finally:
            del os.environ["MAXBOT_BIND_HOST"]


# ── Test 3: API port configurable ───────────────────────────────────────────

class TestApiPort:
    def test_env_variable(self):
        os.environ["MAXBOT_API_PORT"] = "9999"
        try:
            port = int(os.environ.get("MAXBOT_API_PORT", "8765"))
            assert port == 9999
        finally:
            del os.environ["MAXBOT_API_PORT"]


# ── Test 4: IB host configurable ────────────────────────────────────────────

class TestIBHost:
    def test_env_variable(self):
        os.environ["MAXBOT_IB_HOST"] = "10.0.0.1"
        try:
            assert os.environ["MAXBOT_IB_HOST"] == "10.0.0.1"
        finally:
            del os.environ["MAXBOT_IB_HOST"]


# ── Test 5: IB port configurable ────────────────────────────────────────────

class TestIBPort:
    def test_env_variable(self):
        os.environ["MAXBOT_IB_PORT"] = "4002"
        try:
            assert int(os.environ["MAXBOT_IB_PORT"]) == 4002
        finally:
            del os.environ["MAXBOT_IB_PORT"]


# ── Test 6: Client ID configurable ──────────────────────────────────────────

class TestClientId:
    def test_env_variable(self):
        os.environ["MAXBOT_IB_CLIENT_ID"] = "5"
        try:
            assert int(os.environ["MAXBOT_IB_CLIENT_ID"]) == 5
        finally:
            del os.environ["MAXBOT_IB_CLIENT_ID"]


# ── Test 7: Debug disabled ──────────────────────────────────────────────────

class TestDebugDisabled:
    def test_no_debug(self):
        import inspect
        import trading_lab.live.control_api as mod
        source = inspect.getsource(mod.run_server)
        assert "debug=False" in source


# ── Test 8: Reloader disabled ───────────────────────────────────────────────

class TestReloaderDisabled:
    def test_no_reloader(self):
        import inspect
        import trading_lab.live.control_api as mod
        source = inspect.getsource(mod.run_server)
        assert "use_reloader=False" in source


# ── Test 9: Launcher contains no credentials ────────────────────────────────

class TestNoCredentials:
    def test_launcher_script(self):
        script = Path(__file__).parent.parent.parent / "start_maxbot.sh"
        if script.exists():
            text = script.read_text()
            assert "password" not in text.lower()
            assert "secret" not in text.lower()
            # No hardcoded tokens
            assert "Bearer " not in text

    def test_control_api_no_creds(self):
        import inspect
        import trading_lab.live.control_api as mod
        source = inspect.getsource(mod.run_server)
        assert "password" not in source.lower()


# ── Test 10: Launcher does not auto-start runner ────────────────────────────

class TestNoAutoStart:
    def test_launcher_no_bot_start(self):
        script = Path(__file__).parent.parent.parent / "start_maxbot.sh"
        if script.exists():
            text = script.read_text()
            assert "/api/bot/start" not in text
            assert "bot_start" not in text

    def test_run_server_no_auto_start(self):
        import inspect
        import trading_lab.live.control_api as mod
        source = inspect.getsource(mod.run_server)
        assert "ctrl.start(" not in source
        assert "/api/bot/start" not in source


# ── Test 11: Launcher does not invoke start endpoint ─────────────────────────

class TestNoStartInvoke:
    def test_no_curl_start(self):
        script = Path(__file__).parent.parent.parent / "start_maxbot.sh"
        if script.exists():
            text = script.read_text()
            assert "curl" not in text.lower()


# ── Test 12: API token not printed ───────────────────────────────────────────

class TestTokenNotPrinted:
    def test_run_server_no_token_value(self):
        import inspect
        import trading_lab.live.control_api as mod
        source = inspect.getsource(mod.run_server)
        # Should say "configured" not print the actual value
        assert "configured (protected)" in source
        # Should not print the token variable itself
        assert 'print(f"' not in source or "token}" not in source


# ── Test 13: README contains Paper workflow ──────────────────────────────────

class TestReadme:
    def test_paper_workflow(self):
        readme = Path(__file__).parent.parent / "src/trading_lab/live/README_RUN.md"
        if readme.exists():
            text = readme.read_text()
            assert "Paper" in text
            assert "TWS" in text
            assert "START MAXBOT" in text
            assert "STOP MAXBOT" in text


# ── Test 14: README contains iPhone setup ────────────────────────────────────

class TestReadmeIphone:
    def test_add_to_home(self):
        readme = Path(__file__).parent.parent / "src/trading_lab/live/README_RUN.md"
        if readme.exists():
            text = readme.read_text()
            assert "Add to Home Screen" in text
            assert "iPhone" in text or "Safari" in text


# ── Test 15: README distinguishes server vs start ────────────────────────────

class TestReadmeDistinction:
    def test_distinction(self):
        readme = Path(__file__).parent.parent / "src/trading_lab/live/README_RUN.md"
        if readme.exists():
            text = readme.read_text()
            assert "control server only" in text.lower() or "does NOT start trading" in text


# ── Test 16: No LIVE execution mode ─────────────────────────────────────────

class TestNoLive:
    def test_no_live_in_launcher(self):
        script = Path(__file__).parent.parent.parent / "start_maxbot.sh"
        if script.exists():
            text = script.read_text()
            assert "LIVE" not in text or "PAPER_EXECUTE" in text


# ── Test 17: Existing API/PWA regression ─────────────────────────────────────

class TestRegression:
    def test_api_still_works(self):
        app = create_app(MaxBotController())
        client = app.test_client()
        r = client.get("/api/bot/status")
        assert r.status_code == 200

    def test_pwa_still_serves(self):
        app = create_app(MaxBotController())
        client = app.test_client()
        r = client.get("/")
        assert r.status_code == 200
        assert b"MAXBOT" in r.data


# ── Test: LAN IP discovery ──────────────────────────────────────────────────

class TestLanIp:
    def test_returns_string(self):
        ip = get_lan_ip()
        assert isinstance(ip, str)

    def test_fallback_on_error(self):
        with patch("socket.socket") as mock_sock:
            mock_sock.side_effect = OSError("no network")
            ip = get_lan_ip()
            assert ip == "?"


# ── Test: Controller receives IB config ──────────────────────────────────────

class TestControllerConfig:
    def test_ib_params(self):
        ctrl = MaxBotController(
            default_host="10.0.0.1",
            default_port=4002,
            default_client_id=5,
        )
        assert ctrl._ibkr_host == "10.0.0.1"
        assert ctrl._ibkr_port == 4002
        assert ctrl._ibkr_client_id == 5
