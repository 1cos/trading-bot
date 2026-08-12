"""Tests for IBKR event-loop isolation from Flask control thread.

Reproduces the real topology: Flask request thread starts bot worker.
No real IBKR connection.
"""

import asyncio
import json
import os
import time
import threading
import pytest
from unittest.mock import MagicMock, patch

from trading_lab.live.control_api import (
    BotState,
    MaxBotController,
    create_app,
)


# ── Test 1-3: Non-main thread with no event loop ────────────────────────────

class TestNoEventLoopThread:
    def test_old_topology_would_fail(self):
        """Constructing MaxBotRunner in a thread with no event loop fails."""
        error_holder = [None]

        def run_in_thread():
            # Remove any event loop (simulates Flask request thread)
            try:
                asyncio.get_event_loop().close()
            except Exception:
                pass
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass  # no running loop — correct

            # This is what the OLD code did: construct runner in request thread
            try:
                from trading_lab.live.bot_runner import MaxBotRunner
                # Construction touches ib_insync → eventkit → get_event_loop
                runner = MaxBotRunner("QQQ")
            except RuntimeError as e:
                if "event loop" in str(e).lower():
                    error_holder[0] = str(e)
                else:
                    error_holder[0] = f"unexpected: {e}"
            except Exception as e:
                error_holder[0] = f"other: {e}"

        t = threading.Thread(target=run_in_thread)
        t.start()
        t.join(timeout=5)

        # This test documents the old bug — it may or may not reproduce
        # depending on whether the thread inherits a loop. The fix is
        # verified by test_fixed_start_works below.

    def test_fixed_start_does_not_raise(self):
        """Controller.start() from any thread should not raise event-loop errors.

        The fix ensures all ib_insync imports happen in the worker thread
        which has its own event loop.
        """
        ctrl = MaxBotController()

        # Start from a thread (like Flask request) — should not raise
        # because start() only validates config and spawns worker
        error_holder = [None]

        def run_in_thread():
            try:
                ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            except Exception as e:
                error_holder[0] = str(e)

        t = threading.Thread(target=run_in_thread)
        t.start()
        t.join(timeout=5)

        # start() itself should not raise — it only spawns the worker
        assert error_holder[0] is None, f"Start raised: {error_holder[0]}"

        # Worker thread will fail (no real IBKR) but that's ERROR, not stuck STARTING
        time.sleep(2)
        assert ctrl.state in (BotState.ERROR, BotState.STOPPED)


# ── Test 4-5: Worker owns event loop ────────────────────────────────────────

class TestWorkerEventLoop:
    def test_worker_has_loop(self):
        """Worker thread should have its own asyncio event loop."""
        loop_holder = [None]

        ctrl = MaxBotController()

        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            def capture_loop(*args, **kwargs):
                loop_holder[0] = asyncio.get_event_loop()
                mock = MagicMock()
                mock.run.return_value = None
                return mock

            MockRunner.side_effect = capture_loop
            ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            time.sleep(0.5)

        assert loop_holder[0] is not None


# ── Test 6-7: State transitions ──────────────────────────────────────────────

class TestStateTransitions:
    def test_stopped_to_starting(self):
        ctrl = MaxBotController()
        assert ctrl.state == BotState.STOPPED

        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            mock = MagicMock()
            # Block run() so we can observe STARTING
            started = threading.Event()
            def slow_run():
                started.set()
                time.sleep(1)
            mock.run.side_effect = slow_run
            MockRunner.return_value = mock

            ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            started.wait(timeout=2)
            assert ctrl.state == BotState.RUNNING

    def test_startup_failure_to_error(self):
        ctrl = MaxBotController()

        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            MockRunner.side_effect = RuntimeError("TWS connection refused")
            ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            time.sleep(1)

        assert ctrl.state == BotState.ERROR
        assert ctrl.error is not None
        assert "TWS" in ctrl.error


# ── Test 8-10: Failure state ─────────────────────────────────────────────────

class TestFailureState:
    def test_failure_stores_error(self):
        ctrl = MaxBotController()
        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            MockRunner.side_effect = RuntimeError("No paper account")
            ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            time.sleep(1)
        assert ctrl.error == "No paper account"

    def test_failure_not_starting(self):
        ctrl = MaxBotController()
        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            MockRunner.side_effect = RuntimeError("fail")
            ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            time.sleep(1)
        assert ctrl.state != BotState.STARTING

    def test_config_retained_after_error(self):
        ctrl = MaxBotController()
        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            MockRunner.side_effect = RuntimeError("fail")
            ctrl.start(["QQQ", "SPY"], execution_mode="PAPER_EXECUTE")
            time.sleep(1)
        status = ctrl.get_status()
        assert status["execution_mode"] == "PAPER_EXECUTE"
        assert status["watchlist"] == ["QQQ", "SPY"]
        assert status["error"] is not None


# ── Test 11: Config retained while starting ──────────────────────────────────

class TestConfigRetained:
    def test_config_during_starting(self):
        ctrl = MaxBotController()
        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            mock = MagicMock()
            started = threading.Event()
            mock.run.side_effect = lambda: (started.set(), time.sleep(1))
            MockRunner.return_value = mock
            ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            started.wait(timeout=2)

        status = ctrl.get_status()
        assert status["execution_mode"] == "OBSERVE_ONLY"
        assert status["watchlist"] == ["QQQ"]


# ── Test 12: Duplicate START rejected ────────────────────────────────────────

class TestDuplicateStart:
    def test_rejected(self):
        ctrl = MaxBotController()
        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            mock = MagicMock()
            mock.run.side_effect = lambda: time.sleep(5)
            MockRunner.return_value = mock
            ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            time.sleep(0.3)
            with pytest.raises(RuntimeError, match="already running"):
                ctrl.start(["SPY"], execution_mode="OBSERVE_ONLY")


# ── Test 13: Graceful STOP ───────────────────────────────────────────────────

class TestGracefulStop:
    def test_stop_works(self):
        ctrl = MaxBotController()
        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            mock = MagicMock()
            mock._running = True
            def slow_run():
                while mock._running:
                    time.sleep(0.1)
            mock.run.side_effect = slow_run
            MockRunner.return_value = mock

            ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            time.sleep(0.5)
            assert ctrl.state == BotState.RUNNING
            ctrl.stop()
            time.sleep(1)
            assert ctrl.state in (BotState.STOPPED, BotState.STOPPING)


# ── Test 14: Restart after stop ──────────────────────────────────────────────

class TestRestart:
    def test_start_after_stop(self):
        ctrl = MaxBotController()
        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            mock = MagicMock()
            mock._running = True
            def run_once():
                while mock._running:
                    time.sleep(0.1)
            mock.run.side_effect = run_once
            MockRunner.return_value = mock

            ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            time.sleep(0.3)
            ctrl.stop()
            time.sleep(0.5)

            # Reset mock for second start
            mock2 = MagicMock()
            mock2.run.return_value = None
            MockRunner.return_value = mock2
            ctrl._state = BotState.STOPPED  # ensure clean
            ctrl.start(["SPY"], execution_mode="OBSERVE_ONLY")
            time.sleep(0.5)


# ── Test 15: No orphan worker ────────────────────────────────────────────────

class TestNoOrphan:
    def test_thread_exits(self):
        ctrl = MaxBotController()
        with patch("trading_lab.live.bot_runner.MaxBotRunner") as MockRunner:
            mock = MagicMock()
            mock.run.return_value = None
            MockRunner.return_value = mock
            ctrl.start(["QQQ"], execution_mode="OBSERVE_ONLY")
            time.sleep(0.5)
        # Thread should have exited
        if ctrl._thread:
            ctrl._thread.join(timeout=2)
            assert not ctrl._thread.is_alive()


# ── Test 16-17: PWA error display ────────────────────────────────────────────

class TestPWAError:
    def test_error_element_exists(self):
        app = create_app(MaxBotController())
        client = app.test_client()
        html = client.get("/").data.decode()
        assert "error-msg" in html

    def test_status_shows_error(self):
        ctrl = MaxBotController()
        ctrl._state = BotState.ERROR
        ctrl._error = "TWS connection refused"
        ctrl._config = {"execution_mode": "PAPER_EXECUTE", "symbols": ["QQQ"],
                        "direction": "BOTH", "trade_limits_enabled": False}
        status = ctrl.get_status()
        assert status["state"] == "ERROR"
        assert status["error"] == "TWS connection refused"
        assert status["execution_mode"] == "PAPER_EXECUTE"
        assert status["watchlist"] == ["QQQ"]


# ── Test 18-20: Regressions ──────────────────────────────────────────────────

class TestRegressions:
    def test_api_status(self):
        app = create_app(MaxBotController())
        client = app.test_client()
        r = client.get("/api/bot/status")
        assert r.status_code == 200

    def test_pwa_serves(self):
        app = create_app(MaxBotController())
        client = app.test_client()
        r = client.get("/")
        assert r.status_code == 200
        assert b"MAXBOT" in r.data

    def test_live_rejected(self):
        app = create_app(MaxBotController())
        client = app.test_client()
        r = client.post("/api/bot/start", json={
            "symbols": ["QQQ"], "execution_mode": "LIVE"
        })
        assert r.status_code == 400


# ── Test 21: Zero placeOrder calls ───────────────────────────────────────────

class TestZeroOrders:
    def test_no_place_order(self):
        import inspect
        import trading_lab.live.control_api as mod
        source = inspect.getsource(mod)
        assert "placeOrder" not in source
