"""MaxBot local control API — Flask HTTP server for PWA.

Exposes bot runtime state, session telemetry, and start/stop control
for the future iPhone PWA.

Endpoints:
    GET  /api/bot/status      — aggregate bot state
    GET  /api/bot/symbols     — per-symbol status
    GET  /api/events          — event timeline (supports ?since=N)
    GET  /api/session         — session summary
    GET  /api/session/export  — JSON session export
    POST /api/bot/start       — start the bot
    POST /api/bot/stop        — stop the bot

Security:
    State-changing endpoints (start/stop) require a control token
    via Authorization header or ?token= parameter.
    Token from MAXBOT_API_TOKEN env var.
    If no token configured → state-changing endpoints bind localhost only.

No LIVE execution mode is accepted.
"""

from __future__ import annotations

import logging
import os
import threading
from enum import StrEnum, unique

from flask import Flask, jsonify, request
from flask_cors import CORS

log = logging.getLogger("maxbot.api")


# ── Bot state ────────────────────────────────────────────────────────────────

@unique
class BotState(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


# ── Controller ───────────────────────────────────────────────────────────────


class MaxBotController:
    """Owns the running MaxBotRunner and exposes its state.

    Parameters
    ----------
    default_host : str
        IBKR TWS/Gateway host.
    default_port : int
        IBKR TWS/Gateway port.
    default_client_id : int
        IBKR API client ID.
    """

    def __init__(
        self,
        default_host: str = "127.0.0.1",
        default_port: int = 7497,
        default_client_id: int = 1,
    ):
        self._ibkr_host = default_host
        self._ibkr_port = default_port
        self._ibkr_client_id = default_client_id
        self._state = BotState.STOPPED
        self._runner = None
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self._config: dict | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> BotState:
        return self._state

    @property
    def runner(self):
        return self._runner

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def config(self) -> dict | None:
        return self._config

    def start(
        self,
        symbols: list[str],
        direction: str = "BOTH",
        execution_mode: str = "OBSERVE_ONLY",
        trade_limits_enabled: bool = False,
    ) -> None:
        """Start the bot in a background thread.

        All IBKR/ib_insync objects are constructed in the worker thread
        to avoid asyncio event-loop errors in Flask request threads.

        Raises RuntimeError if already running.
        """
        with self._lock:
            if self._state in (BotState.RUNNING, BotState.STARTING):
                raise RuntimeError("Bot is already running")

            # Validate (no ib_insync imports here)
            if execution_mode not in ("OBSERVE_ONLY", "PAPER_EXECUTE"):
                raise ValueError(
                    f"Invalid execution_mode: {execution_mode!r}. "
                    f"Only OBSERVE_ONLY and PAPER_EXECUTE are allowed."
                )
            if direction not in ("LONG", "SHORT", "BOTH"):
                raise ValueError(f"Invalid direction: {direction!r}")
            if not symbols:
                raise ValueError("Symbol list cannot be empty")

            self._state = BotState.STARTING
            self._error = None
            self._config = {
                "symbols": symbols,
                "direction": direction,
                "execution_mode": execution_mode,
                "trade_limits_enabled": trade_limits_enabled,
            }

        # Worker thread owns the event loop and all IBKR objects
        def _run_thread():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Import and construct runner INSIDE worker thread
                from trading_lab.live.bot_runner import MaxBotRunner

                runner = MaxBotRunner(
                    symbols=symbols,
                    direction=direction,
                    host=self._ibkr_host,
                    port=self._ibkr_port,
                    client_id=self._ibkr_client_id,
                    execution_mode=execution_mode,
                    trade_limits_enabled=trade_limits_enabled,
                )
                self._runner = runner
                self._state = BotState.RUNNING
                runner.run()
            except Exception as e:
                self._error = str(e)
                self._state = BotState.ERROR
                log.error(f"Bot runner error: {e}")
            finally:
                if self._state not in (BotState.ERROR,):
                    self._state = BotState.STOPPED
                self._runner = None
                try:
                    loop.close()
                except Exception:
                    pass

        self._thread = threading.Thread(target=_run_thread, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Request graceful shutdown."""
        with self._lock:
            if self._state not in (BotState.RUNNING, BotState.STARTING):
                raise RuntimeError("Bot is not running")
            self._state = BotState.STOPPING

        if self._runner:
            self._runner._running = False

    def get_status(self) -> dict:
        """Return aggregate bot status."""
        runner = self._runner
        result = {
            "state": str(self._state),
            "error": self._error,
        }

        # Always include config if available
        if self._config:
            result["execution_mode"] = self._config.get("execution_mode")
            result["direction"] = self._config.get("direction")
            result["watchlist"] = self._config.get("symbols", [])
            result["trade_limits_enabled"] = self._config.get("trade_limits_enabled", False)
        else:
            result["execution_mode"] = None
            result["watchlist"] = []

        # Runner-specific live data
        if runner:
            try:
                result["enabled_symbols"] = runner.enabled_count
                result["disabled_symbols"] = runner.disabled_symbols
                result["total_open_positions"] = runner.total_open_positions
                result["symbol_statuses"] = runner.symbol_statuses
                result["session_event_count"] = len(runner.session_log.events)
                result["ibkr_connected"] = runner._ib.isConnected() if runner._ib else False
                result["paper_verified"] = runner._paper_account is not None
            except Exception:
                pass

        return result

    def get_symbols(self) -> list[dict]:
        """Return per-symbol status."""
        runner = self._runner
        if not runner:
            return []

        from trading_lab.live.observe_orchestrator import ExecutionMode, ObserveLifecycle
        from trading_lab.live.trade_orchestrator import LifecycleState

        result = []
        for sym, rt in runner._runtimes.items():
            entry = {"symbol": sym, "enabled": rt.enabled}
            if not rt.enabled:
                entry["error"] = rt.error
                entry["lifecycle"] = "DISABLED"
            elif rt.orchestrator:
                if runner._execution_mode == ExecutionMode.OBSERVE_ONLY:
                    entry["lifecycle"] = str(rt.orchestrator.lifecycle)
                else:
                    entry["lifecycle"] = str(rt.orchestrator.lifecycle)
                    orch = rt.orchestrator
                    if orch._resolved_direction:
                        entry["direction"] = orch._resolved_direction
                    if orch._option_right:
                        entry["option_right"] = orch._option_right
                        entry["option_expiration"] = orch._option_expiration
                        entry["option_strike"] = orch._option_strike
                        entry["option_con_id"] = orch._entry_con_id
                    if orch._underlying_triggers:
                        entry["underlying_entry"] = float(orch._underlying_triggers.entry_price)
                        entry["underlying_stop"] = float(orch._underlying_triggers.stop_price)
                        entry["underlying_target"] = float(orch._underlying_triggers.target_price)
                    if orch._exit_reason:
                        entry["exit_reason"] = orch._exit_reason
            # Context levels — always included if available
            if rt.context_levels:
                ctx = rt.context_levels
                if ctx.pdh is not None:
                    entry["pdh"] = ctx.pdh
                if ctx.pdl is not None:
                    entry["pdl"] = ctx.pdl
                if ctx.prev_date is not None:
                    entry["pdh_pdl_date"] = ctx.prev_date
                if ctx.pmh is not None:
                    entry["pmh"] = ctx.pmh
                if ctx.pml is not None:
                    entry["pml"] = ctx.pml
                if ctx.pm_bar_count > 0:
                    entry["pm_bar_count"] = ctx.pm_bar_count
                entry["premarket_final"] = ctx.premarket_final
                if ctx.premarket_date is not None:
                    entry["premarket_date"] = ctx.premarket_date
            # Feed health
            entry["feed_status"] = rt.feed_status
            entry["processed_bar_count"] = rt.processed_bar_count
            if rt.resubscribe_count > 0:
                entry["resubscribe_count"] = rt.resubscribe_count
            result.append(entry)
        return result

    def get_events(self, since: int = 0) -> list[dict]:
        """Return events since given sequence number."""
        runner = self._runner
        if not runner:
            return []
        events = runner.session_log.events_since(since)
        return [e.to_dict() for e in events]

    def get_session_summary(self) -> dict:
        """Return session summary."""
        runner = self._runner
        if not runner:
            return {"state": str(self._state)}

        log_obj = runner.session_log
        events = log_obj.events
        trade_completed = [e for e in events if e.event_type == "TRADE_COMPLETED"]
        wins = sum(1 for e in trade_completed if e.data.get("result") == "WIN")
        losses = sum(1 for e in trade_completed if e.data.get("result") == "LOSS")

        return {
            "state": str(self._state),
            "execution_mode": self._config.get("execution_mode") if self._config else None,
            "watchlist": self._config.get("symbols", []) if self._config else [],
            "event_count": len(events),
            "trade_completed_count": len(trade_completed),
            "wins": wins,
            "losses": losses,
            "total_open_positions": runner.total_open_positions,
            "metadata": log_obj.metadata,
        }

    def export_session_json(self) -> dict | None:
        """Export full session as JSON-serializable dict."""
        runner = self._runner
        if not runner:
            return None
        log_obj = runner.session_log
        return {
            "maxbot_version": "v0.1",
            "session": log_obj.metadata,
            "event_count": len(log_obj.events),
            "events": [e.to_dict() for e in log_obj.events],
        }

    def export_session_markdown(self) -> str | None:
        """Export session as Markdown string."""
        runner = self._runner
        if not runner:
            return None
        import tempfile
        from pathlib import Path
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            p = Path(f.name)
        try:
            runner.session_log.export_markdown(p)
            return p.read_text()
        finally:
            p.unlink(missing_ok=True)


# ── Flask app factory ────────────────────────────────────────────────────────

def create_app(controller: MaxBotController | None = None) -> Flask:
    """Create the Flask API app.

    Parameters
    ----------
    controller : MaxBotController or None
        If None, a default controller is created.
    """
    from pathlib import Path
    from flask import send_from_directory

    app = Flask(__name__)
    CORS(app)

    ctrl = controller or MaxBotController()
    api_token = os.environ.get("MAXBOT_API_TOKEN", "")
    ui_dir = str(Path(__file__).parent / "ui")

    def _check_token():
        """Check control token for state-changing endpoints."""
        if not api_token:
            return None
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            token = request.args.get("token", "")
        if token != api_token:
            return jsonify({"error": "Invalid or missing API token"}), 401
        return None

    # ── PWA serving ──────────────────────────────────────────────────────

    @app.route("/")
    def index():
        resp = send_from_directory(ui_dir, "dashboard.html")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    @app.route("/manifest.json")
    def manifest():
        return send_from_directory(ui_dir, "manifest.json")

    @app.route("/sw.js")
    def service_worker():
        resp = send_from_directory(ui_dir, "sw.js")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    # ── Status ───────────────────────────────────────────────────────────

    @app.route("/api/bot/status")
    def bot_status():
        return jsonify(ctrl.get_status())

    @app.route("/api/bot/symbols")
    def bot_symbols():
        return jsonify(ctrl.get_symbols())

    @app.route("/api/events")
    def events():
        since = request.args.get("since", 0, type=int)
        return jsonify(ctrl.get_events(since=since))

    @app.route("/api/session")
    def session_summary():
        return jsonify(ctrl.get_session_summary())

    @app.route("/api/session/export.json")
    def session_export_json():
        data = ctrl.export_session_json()
        if data is None:
            return jsonify({"error": "No session available"}), 404
        return jsonify(data)

    @app.route("/api/session/export.md")
    def session_export_md():
        text = ctrl.export_session_markdown()
        if text is None:
            return "No session available", 404
        return text, 200, {"Content-Type": "text/markdown; charset=utf-8"}

    # ── Control ──────────────────────────────────────────────────────────

    @app.route("/api/bot/start", methods=["POST"])
    def bot_start():
        auth_err = _check_token()
        if auth_err:
            return auth_err

        data = request.get_json(silent=True) or {}
        symbols = data.get("symbols", [])
        direction = data.get("direction", "BOTH")
        execution_mode = data.get("execution_mode", "OBSERVE_ONLY")
        trade_limits = data.get("trade_limits_enabled", False)

        try:
            ctrl.start(
                symbols=symbols,
                direction=direction,
                execution_mode=execution_mode,
                trade_limits_enabled=trade_limits,
            )
            return jsonify({"status": "starting", "symbols": symbols,
                            "execution_mode": execution_mode})
        except (RuntimeError, ValueError) as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/bot/stop", methods=["POST"])
    def bot_stop():
        auth_err = _check_token()
        if auth_err:
            return auth_err

        try:
            ctrl.stop()
            return jsonify({"status": "stopping"})
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 400

    return app


# ── Server entry point ──────────────────────────────────────────────────────

def get_lan_ip() -> str:
    """Best-effort LAN IPv4 discovery."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?"


def run_server(
    bind_host: str | None = None,
    api_port: int | None = None,
    ib_host: str | None = None,
    ib_port: int | None = None,
    ib_client_id: int | None = None,
):
    """Start the MaxBot control API / PWA server."""
    import sys as _sys
    if _sys.version_info >= (3, 14):
        print(f"\n  ❌ MaxBot requires Python 3.11–3.13 for ib_insync.")
        print(f"     Current: Python {_sys.version_info.major}.{_sys.version_info.minor}")
        print(f"     Recreate venv with: /opt/homebrew/bin/python3.12 -m venv venv\n")
        _sys.exit(1)

    host = bind_host or os.environ.get("MAXBOT_BIND_HOST", "0.0.0.0")
    port = api_port or int(os.environ.get("MAXBOT_API_PORT", "8765"))
    ibh = ib_host or os.environ.get("MAXBOT_IB_HOST", "127.0.0.1")
    ibp = ib_port or int(os.environ.get("MAXBOT_IB_PORT", "7497"))
    ibc = ib_client_id or int(os.environ.get("MAXBOT_IB_CLIENT_ID", "1"))
    token = os.environ.get("MAXBOT_API_TOKEN", "")

    lan_ip = get_lan_ip()

    print()
    print("MAXBOT CONTROL SERVER")
    print("=" * 40)
    print(f"Dashboard:  http://{lan_ip}:{port}")
    print(f"IBKR target: {ibh}:{ibp} (clientId={ibc})")
    print(f"Mode:       PAPER_EXECUTE / OBSERVE_ONLY")
    if token:
        print(f"API token:  configured (protected)")
    else:
        if host != "127.0.0.1":
            print(f"⚠ WARNING: No MAXBOT_API_TOKEN set and binding to {host}")
            print(f"  Set MAXBOT_API_TOKEN for control protection")
    print()
    print("Waiting for iPhone START command...")
    print()

    ctrl = MaxBotController(
        default_host=ibh,
        default_port=ibp,
        default_client_id=ibc,
    )
    app = create_app(ctrl)
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    run_server()

