"""Backtest Lab — Flask API server.

Serves the real Python BDRR detector and backtest pipeline.
The browser UI calls these endpoints; no strategy logic runs in JavaScript.

Start:
    cd trading-bot
    python -m backend.src.trading_lab.backtest_server

Then open http://localhost:5001
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

import flask
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from trading_lab.contracts.primitives import Rational
from trading_lab.preset_store import PresetStore, preset_to_run_config
from trading_lab.strategy_runner import run_bdrr_strategy
from trading_lab.strategy_runner import run_bdrr_strategy_v2
from trading_lab.trade_outcome_evaluator import _round_offset_ticks
from trading_lab.trade_dataset import build_trade_dataset
from trading_lab.visual_review_exporter import export_visual_event
from trading_lab.sequence_validator import validate_sequence
from trading_lab.timeframe_aggregation import (
    available_timeframes,
    load_candles_for_timeframe,
)
from trading_lab.orb_builder import build_orb
from trading_lab.break_finder import find_break
from trading_lab.displacement_finder import find_displacement
from trading_lab.retest_window import find_retest_window
from trading_lab.rejection_finder import find_rejection
from trading_lab.session_context import build_session_context


# ── RR parser ────────────────────────────────────────────────────────────────


def parse_exit_target_r(value):
    """Parse a request value into (exit_target_r, is_v2).

    v1 path: int 2, 3, or 4 → (int, False)
    v2 path: string like "2.5" → (Rational, True)

    Raises ValueError for invalid input.
    """
    # Reject non-scalar types
    if isinstance(value, (bool, list, dict)):
        raise ValueError(
            f"exit_target_r must be an integer or decimal string, "
            f"got {type(value).__name__}"
        )

    # v1: plain int 2, 3, 4
    if isinstance(value, int) and value in (2, 3, 4):
        return value, False

    # v2: string decimal
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError("exit_target_r string must not be empty")

        try:
            d = Decimal(s)
        except InvalidOperation:
            raise ValueError(
                f"exit_target_r is not a valid decimal: {value!r}"
            )

        if d.is_nan() or d.is_infinite():
            raise ValueError(
                f"exit_target_r must be a finite number, got: {value!r}"
            )
        if d <= 0:
            raise ValueError(
                f"exit_target_r must be strictly positive, got: {value!r}"
            )

        # Convert Decimal to Rational via Fraction (exact, no float)
        frac = Fraction(d)
        return Rational(frac.numerator, frac.denominator), True

    # v1 int outside {2,3,4} or float → reject
    if isinstance(value, (int, float)):
        raise ValueError(
            f"exit_target_r must be 2, 3, or 4 (v1) "
            f"or a decimal string (v2), got: {value!r}"
        )

    raise ValueError(
        f"exit_target_r: unsupported type {type(value).__name__}"
    )


def _rational_to_number(r):
    """Convert a Rational or int to a Decimal for exact arithmetic.

    Used in metrics computation to avoid float until JSON boundary.
    """
    if isinstance(r, Rational):
        return Decimal(r.numerator) / Decimal(r.denominator)
    if isinstance(r, (int, float)):
        return Decimal(str(r))
    return Decimal(str(r))


def _rational_to_json_dict(r):
    """Convert a Rational to the canonical JSON-safe dict.

    Returns {"numerator": int, "denominator": int, "decimal": str}
    where decimal is a canonical string with no trailing zeros.

    Uses Decimal arithmetic — no float.
    """
    d = Decimal(r.numerator) / Decimal(r.denominator)
    return {
        "numerator": r.numerator,
        "denominator": r.denominator,
        "decimal": str(d.normalize()),
    }


class _RationalEncoder(json.JSONEncoder):
    """JSON encoder that handles Rational and Decimal types."""

    def default(self, obj):
        if isinstance(obj, Rational):
            return _rational_to_json_dict(obj)
        if isinstance(obj, Decimal):
            # Metrics are presentation values — safe to convert
            return float(obj)
        return super().default(obj)


class _RationalJSONProvider(flask.json.provider.DefaultJSONProvider):
    """Flask 3.x JSON provider that handles Rational and Decimal."""

    def default(self, obj):
        if isinstance(obj, Rational):
            return _rational_to_json_dict(obj)
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


# ── App setup ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)
app.json_provider_class = _RationalJSONProvider
app.json = _RationalJSONProvider(app)
CORS(app)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATI_DIR = REPO_ROOT / "dati"
LAB_DIR = REPO_ROOT / "lab"
PRESETS_DIR = REPO_ROOT / "backend" / "runtime" / "presets"

_preset_store = PresetStore(PRESETS_DIR)

# ── Data loading ─────────────────────────────────────────────────────────────

_DATA_CACHE: dict[str, dict] = {}


def _load_symbol_data(symbol: str, timeframe: str = "5m") -> dict:
    """Load and cache CSV data for a symbol."""
    cache_key = f"{symbol}_{timeframe}"
    if cache_key in _DATA_CACHE:
        return _DATA_CACHE[cache_key]

    filename = f"{symbol}_{timeframe}.csv"
    filepath = DATI_DIR / filename
    if not filepath.exists():
        return {"error": f"Data file not found: {filename}", "sessions": {}}

    sessions: dict[str, list[dict]] = {}
    with open(filepath) as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i < 3:
                continue
            if not row[0].strip():
                continue
            dt = datetime.fromisoformat(row[0])
            date = row[0][:10]
            if date not in sessions:
                sessions[date] = []
            sessions[date].append({
                "time_ms": int(dt.timestamp() * 1000),
                "open": float(row[4]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[1]),
                "volume": int(float(row[5])),
            })

    dates = sorted(sessions.keys())
    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "sessions": sessions,
        "dates": dates,
        "earliest": dates[0] if dates else None,
        "latest": dates[-1] if dates else None,
        "session_count": len(dates),
    }
    _DATA_CACHE[cache_key] = result
    return result


def _available_symbols() -> list[dict]:
    """Scan dati/ and dati/1m/ for available symbol files."""
    symbols = []
    seen = set()
    # Scan both dati/ and dati/1m/
    scan_dirs = [DATI_DIR]
    subdir_1m = DATI_DIR / "1m"
    if subdir_1m.is_dir():
        scan_dirs.append(subdir_1m)
    for scan_dir in scan_dirs:
        for f in sorted(scan_dir.iterdir()):
            if f.suffix != ".csv":
                continue
            parts = f.stem.rsplit("_", 1)
            if len(parts) != 2:
                continue
            sym, tf = parts
            if tf not in ("1m", "5m"):
                continue
            if sym in seen:
                continue
            # Use 5m data for date range info (or 1m if only 1m exists)
            data = _load_symbol_data(sym, "5m")
            if not data.get("dates"):
                data = _load_symbol_data(sym, "1m")
            if not data.get("dates"):
                continue
            seen.add(sym)
            symbols.append({
                "symbol": sym,
                "timeframes": available_timeframes(DATI_DIR, sym),
                "earliest": data["earliest"],
                "latest": data["latest"],
                "session_count": data["session_count"],
            })
    return symbols


# ── Metrics ──────────────────────────────────────────────────────────────────


def _compute_metrics(results: list[dict]) -> dict:
    """Compute backtest metrics from strategy runner results."""
    valid = [r for r in results if r["detection_status"] == "VALID"]
    invalid = [r for r in results if r["detection_status"] != "VALID"]

    wins = [r for r in valid if str(r["outcome"]) == "TARGET_HIT"]
    losses = [r for r in valid if str(r["outcome"]) == "STOPPED"]
    open_trades = [r for r in valid if str(r["outcome"]) in ("OPEN", "AMBIGUOUS")]

    n_trades = len(valid)
    n_wins = len(wins)
    n_losses = len(losses)
    n_open = len(open_trades)

    win_rate = Decimal(n_wins) / Decimal(n_trades) if n_trades > 0 else Decimal(0)
    loss_rate = Decimal(n_losses) / Decimal(n_trades) if n_trades > 0 else Decimal(0)

    # R values
    r_values = []
    for r in valid:
        rr = r.get("realized_r")
        if rr is not None:
            r_values.append(_rational_to_number(rr))

    net_r = sum(r_values)
    avg_r = net_r / len(r_values) if r_values else 0

    # Expectancy = (win_rate × avg_win) - (loss_rate × avg_loss)
    win_rs = [rv for rv in r_values if rv > 0]
    loss_rs = [rv for rv in r_values if rv < 0]
    avg_win = sum(win_rs) / len(win_rs) if win_rs else 0
    avg_loss = abs(sum(loss_rs) / len(loss_rs)) if loss_rs else 0
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    # Profit factor
    gross_profit = sum(win_rs)
    gross_loss = abs(sum(loss_rs))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
        "Inf" if gross_profit > 0 else Decimal(0)
    )

    # Equity curve + drawdown
    equity_curve = []
    cumulative = Decimal(0)
    peak = Decimal(0)
    max_dd = Decimal(0)
    drawdown_curve = []
    for rv in r_values:
        cumulative += rv
        equity_curve.append(round(cumulative, 4))
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
        drawdown_curve.append(round(dd, 4))

    # Consecutive wins/losses
    max_consec_wins = 0
    max_consec_losses = 0
    cw = 0
    cl = 0
    for rv in r_values:
        if rv > 0:
            cw += 1
            cl = 0
        elif rv < 0:
            cl += 1
            cw = 0
        else:
            cw = 0
            cl = 0
        max_consec_wins = max(max_consec_wins, cw)
        max_consec_losses = max(max_consec_losses, cl)

    return {
        "total_sessions": len(results),
        "total_detected": n_trades,
        "total_invalid": len(invalid),
        "winning_trades": n_wins,
        "losing_trades": n_losses,
        "open_trades": n_open,
        "win_rate": round(win_rate, 4),
        "loss_rate": round(loss_rate, 4),
        "net_r": round(net_r, 4),
        "avg_r": round(avg_r, 4),
        "expectancy": round(expectancy, 4),
        "profit_factor": round(profit_factor, 4) if isinstance(profit_factor, Decimal) else "Inf",
        "max_drawdown": round(max_dd, 4),
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
        "r_values": r_values,
        "avg_trade_duration": "unavailable",  # would need bar-level timestamps
    }


# ── v2 target extraction ────────────────────────────────────────────────────


def _extract_v2_target_fields(r: dict) -> dict | None:
    """Extract v2 target presentation fields from a runner result.

    Returns dict with requested_target_r, effective_target_r,
    target_price_ticks, target_label — or None if not a v2 result.
    """
    to = r.get("trade_outcome")
    if to is None:
        return None

    sel_r = getattr(to, "selected_exit_target_r", None)
    if not isinstance(sel_r, Rational):
        return None

    entry = getattr(to, "entry_price_ticks", None)
    stop = getattr(to, "stop_price_ticks", None)
    if entry is None or stop is None:
        return None

    risk = abs(entry - stop)
    if risk == 0:
        return None

    offset = _round_offset_ticks(risk, sel_r)
    effective_r = Rational(offset, risk)

    direction = str(getattr(to, "direction", "LONG"))
    if direction == "SHORT":
        target_ticks = entry - offset
    else:
        target_ticks = entry + offset

    label = getattr(to, "selected_exit_target_label", None)

    return {
        "requested_target_r": sel_r,
        "effective_target_r": effective_r,
        "target_price_ticks": target_ticks,
        "target_label": label or "",
    }


# ── Trade detail builder ─────────────────────────────────────────────────────


def _build_trade_row(r: dict, idx: int, sessions_data: dict) -> dict:
    """Build a trade table row from a strategy runner result."""
    dr = r.get("detection_result")
    tp = r.get("trade_plan")
    to = r.get("trade_outcome")

    def _g(obj, attr, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    def _price(obj, attr):
        v = _g(obj, attr)
        if v is None:
            return None
        if hasattr(v, "to_price"):
            return v.to_price()
        if isinstance(v, dict):
            t = v.get("ticks")
            ts = v.get("tick_size", "0.01")
            if t is not None:
                return round(t * float(ts), 6)
        return None

    def _ms_to_time(ms):
        if ms is None:
            return None
        return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")

    entry_ticks = r.get("entry_price_ticks")
    stop_ticks = r.get("stop_price_ticks")
    r2_ticks = r.get("r2_price_ticks")
    exit_ticks = r.get("exit_price_ticks")
    tick_size = 0.01

    # v2 target fields
    v2t = _extract_v2_target_fields(r)

    return {
        "trade_number": idx + 1,
        "run_record_id": r.get("run_record_id"),
        "symbol": r.get("symbol"),
        "date": r.get("session_date"),
        "direction": r.get("_direction", str(_g(dr, "direction", "?"))),
        "level_source": r.get("_level_source", "ORB_HIGH"),
        "sequence_id": f"{r.get('_direction', 'L')[0]}-SEQ-{idx + 1:03d}",
        "break_time": _ms_to_time(_g(_g(dr, "break_bar"), "bar_utc_ms")),
        "confirmation_time": r.get("confirmation_timestamp"),
        "entry_time": r.get("entry_timestamp"),
        "exit_time": r.get("exit_timestamp"),
        "entry_price": round(entry_ticks * tick_size, 2) if entry_ticks else None,
        "stop_price": round(stop_ticks * tick_size, 2) if stop_ticks else None,
        "target_price": round((v2t["target_price_ticks"] if v2t else r2_ticks) * tick_size, 2)
            if (v2t or r2_ticks) else None,
        "exit_price": round(exit_ticks * tick_size, 2) if exit_ticks else None,
        "outcome": str(r.get("outcome", "")),
        "realized_r": float(_rational_to_number(r.get("realized_r")))
            if r.get("realized_r") is not None else None,
        "wick_depth_ticks": None,
        "failed_retest_count": 0,
        "detection_status": r.get("detection_status"),
        "preset_id": r.get("preset_id"),
        "requested_target_r": v2t["requested_target_r"] if v2t else None,
        "effective_target_r": v2t["effective_target_r"] if v2t else None,
        "target_price_ticks": v2t["target_price_ticks"] if v2t else r2_ticks,
        "target_label": v2t["target_label"] if v2t else "2R",
    }


# ── API endpoints ────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return send_from_directory(str(LAB_DIR), "index.html")


@app.route("/lab/<path:filename>")
def lab_static(filename):
    return send_from_directory(str(LAB_DIR), filename)


@app.route("/api/symbols")
def api_symbols():
    """Return available symbols with date ranges."""
    return jsonify(_available_symbols())


@app.route("/api/defaults")
def api_defaults():
    """Return default preset and config values."""
    return jsonify({
        "preset": {
            "preset_id": "frozen_default",
            "timeframe_minutes": 5,
            "timezone": "America/New_York",
            "session_open": "09:30",
            "orb_start": "session_open",
            "orb_duration_minutes": 5,
            "level_source": "ORB_HIGH",
            "direction": "LONG",
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_buffer_ticks": 0,
            "stop_buffer_ticks": 0,
            "min_displacement_ticks": None,
            "min_penetration_ticks": None,
            "min_close_beyond_level_ticks": None,
            "consecutive_orb_closes": 2,
        },
        "config": {
            "tick_size": 0.01,
            "exit_target_r": 2,
            "engine_version": "1.0.0",
        },
        "available_timeframes": [
            {"value": "5m", "label": "5 minutes", "available": True},
            {"value": "2m", "label": "2 minutes", "available": False,
             "reason": "Requires genuine 1-minute source data"},
            {"value": "1m", "label": "1 minute", "available": False,
             "reason": "No 1-minute CSV files in dati/"},
        ],
        "parameter_schema": {
            "orb": [
                {"key": "orb_duration_minutes", "label": "ORB Duration (minutes)",
                 "type": "int", "default": 5, "min": 1, "max": 30},
                {"key": "level_source", "label": "Level Source",
                 "type": "select", "default": "ORB_HIGH",
                 "options": ["ORB_HIGH", "ORB_LOW"]},
            ],
            "direction": [
                {"key": "direction", "label": "Direction",
                 "type": "select", "default": "LONG",
                 "options": ["LONG", "SHORT"]},
            ],
            "sequence": [
                {"key": "consecutive_orb_closes", "label": "Consecutive ORB Closes",
                 "type": "int", "default": 2, "min": 1, "max": 10},
            ],
            "risk": [
                {"key": "exit_target_r", "label": "Exit Target (R)",
                 "type": "select", "default": 2, "options": [2, 3, 4]},
                {"key": "entry_buffer_ticks", "label": "Entry Buffer (ticks)",
                 "type": "int", "default": 0, "min": 0, "max": 20},
                {"key": "stop_buffer_ticks", "label": "Stop Buffer (ticks)",
                 "type": "int", "default": 0, "min": 0, "max": 20},
                {"key": "tick_size", "label": "Tick Size",
                 "type": "float", "default": 0.01, "min": 0.001, "max": 1.0},
            ],
            "confirmation": [
                {"key": "entry_model", "label": "Entry Model",
                 "type": "select", "default": "CONFIRMATION_CLOSE",
                 "options": ["CONFIRMATION_CLOSE"]},
                {"key": "min_close_beyond_level_ticks",
                 "label": "Min Close Beyond Level (ticks)",
                 "type": "int_or_null", "default": None, "min": 0, "max": 100},
            ],
        },
    })


# ── Preset endpoints ────────────────────────────────────────────────────────


@app.route("/api/presets", methods=["GET"])
def api_preset_list():
    """List all saved presets."""
    try:
        return jsonify(_preset_store.list_all())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/presets", methods=["POST"])
def api_preset_create():
    """Create and save a new persistent preset."""
    try:
        body = request.get_json()
        if not body or not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        name = body.get("name")
        params = body.get("parameters")
        if not isinstance(params, dict):
            return jsonify({"error": "parameters must be a JSON object"}), 400

        preset = _preset_store.create(name, params)
        return jsonify(preset), 201

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/presets/<preset_id>")
def api_preset_get(preset_id):
    """Load a preset by ID."""
    try:
        from trading_lab.preset_store import is_safe_preset_id
        if not is_safe_preset_id(preset_id):
            return jsonify({"error": "Invalid preset_id format"}), 400
        preset = _preset_store.get(preset_id)
        if preset is None:
            return jsonify({"error": f"Preset {preset_id} not found"}), 404
        return jsonify(preset)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/run", methods=["POST"])
def api_run():
    """Execute a backtest run with the real Python detector."""
    try:
        body = request.get_json()

        # ── Config source: persistent preset or inline ───────────────
        loaded_preset = None
        config_source = "inline"

        if "preset_id" in body:
            pid = body["preset_id"]
            from trading_lab.preset_store import is_safe_preset_id
            if not is_safe_preset_id(pid):
                return jsonify({"error": "Invalid preset_id format"}), 400
            loaded_preset = _preset_store.get(pid)
            if loaded_preset is None:
                return jsonify({"error": f"Preset {pid} not found"}), 404

            # Reject strategic overrides when using preset_id
            if body.get("preset"):
                return jsonify({
                    "error": "Cannot combine preset_id with inline preset overrides"
                }), 400

            # Only start_date and end_date allowed in config for preset runs
            run_config = body.get("config", {})
            _ALLOWED_EXEC_KEYS = {"start_date", "end_date"}
            strategic_keys = set(run_config.keys()) - _ALLOWED_EXEC_KEYS
            if strategic_keys:
                return jsonify({
                    "error": f"Cannot override strategic parameters when using "
                             f"preset_id: {', '.join(sorted(strategic_keys))}"
                }), 400

            preset_overrides, config_overrides = preset_to_run_config(loaded_preset)
            p = loaded_preset["parameters"]
            symbols = [p["symbol"]]
            timeframe = p["timeframe"]
            config_source = "persistent_preset"

            # Dates from config only
            start_date = run_config.get("start_date")
            end_date = run_config.get("end_date")
        else:
            symbols = body.get("symbols", ["SPY"])
            start_date = body.get("start_date")
            end_date = body.get("end_date")
            timeframe = body.get("timeframe", "5m")
            preset_overrides = body.get("preset", {})
            config_overrides = body.get("config", {})

        if timeframe not in ("1m", "2m", "3m", "5m", "10m"):
            return jsonify({"error": f"Invalid timeframe: {timeframe}"}), 400

        tf_minutes = int(timeframe.replace("m", ""))
        direction = preset_overrides.get("direction", "LONG")

        # Canonical BDRR mapping: LONG→ORB_HIGH, SHORT→ORB_LOW
        # Level source is always derived from direction, never independent.
        if direction == "BOTH":
            directions = [("LONG", "ORB_HIGH"), ("SHORT", "ORB_LOW")]
        elif direction == "SHORT":
            directions = [("SHORT", "ORB_LOW")]
        else:
            directions = [("LONG", "ORB_HIGH")]

        level_source = "BOTH" if direction == "BOTH" else directions[0][1]

        # Build sessions from real data using timeframe aggregation
        all_sessions = []
        sessions_data = {}
        provenance = {}
        for sym in symbols:
            tf_data = load_candles_for_timeframe(DATI_DIR, sym, tf_minutes)
            if tf_data.get("error"):
                return jsonify({"error": tf_data["error"]}), 400
            sessions_data[sym] = tf_data
            provenance[sym] = {
                "source_timeframe": tf_data["source_timeframe"],
                "selected_timeframe": tf_data["selected_timeframe"],
                "aggregation_method": tf_data["aggregation_method"],
            }
            for date in tf_data["dates"]:
                if start_date and date < start_date:
                    continue
                if end_date and date > end_date:
                    continue
                candles = tf_data["candles_by_date"][date]
                all_sessions.append({
                    "symbol": sym,
                    "date": date,
                    "market_timezone": "America/New_York",
                    "session_open_utc_ms": candles[0]["time_ms"],
                    "session_close_utc_ms": candles[-1]["time_ms"],
                    "timeframe": timeframe,
                    "candles": candles,
                })

        if not all_sessions:
            return jsonify({"error": "No sessions found for the selected date range"}), 400

        # Build preset from defaults + overrides
        base_preset = {
            "preset_id": preset_overrides.get("preset_id", "frozen_default"),
            "timeframe_minutes": tf_minutes,
            "timezone": "America/New_York",
            "session_open": "09:30",
            "orb_start": "session_open",
            "orb_duration_minutes": preset_overrides.get("orb_duration_minutes", 5),
            "entry_model": preset_overrides.get("entry_model", "CONFIRMATION_CLOSE"),
            "entry_buffer_ticks": preset_overrides.get("entry_buffer_ticks", 0),
            "stop_buffer_ticks": preset_overrides.get("stop_buffer_ticks", 0),
            "min_displacement_ticks": preset_overrides.get("min_displacement_ticks"),
            "min_penetration_ticks": preset_overrides.get("min_penetration_ticks"),
            "min_close_beyond_level_ticks": preset_overrides.get("min_close_beyond_level_ticks"),
            "min_displacement_bars": preset_overrides.get("min_displacement_bars", 3),
            "consecutive_orb_closes": preset_overrides.get("consecutive_orb_closes", 2),
        }

        # Parse optional wick/body ratio overrides
        for key, default in [
            ("rejection_wick_ratio_min", None),
            ("body_ratio_max", None),
        ]:
            raw = preset_overrides.get(key)
            if raw is not None:
                if isinstance(raw, bool):
                    return jsonify({"error": f"{key} must be a number"}), 400
                try:
                    val = float(raw)
                except (TypeError, ValueError):
                    return jsonify({"error": f"{key} must be a number"}), 400
                if val < 0 or val > 1:
                    return jsonify({"error": f"{key} must be between 0 and 1"}), 400
                base_preset[key] = val

        # Parse exit_target_r: v1 (int 2/3/4) or v2 (Rational from string)
        raw_etr = config_overrides.get("exit_target_r", 2)
        try:
            exit_target_r, is_v2 = parse_exit_target_r(raw_etr)
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400

        config = {
            "tick_size": config_overrides.get("tick_size", 0.01),
            "exit_target_r": exit_target_r,
            "engine_version": "1.0.0",
        }

        # Select runner: v1 for int {2,3,4}, v2 for Rational
        _runner = run_bdrr_strategy_v2 if is_v2 else run_bdrr_strategy

        # Run detector for each direction
        run_id = str(uuid.uuid4())
        t0 = time.time()
        all_results = []
        for dir_name, level_src in directions:
            preset = {
                **base_preset,
                "direction": dir_name,
                "level_source": level_src,
            }
            results = _runner(all_sessions, preset, config)
            for r in results:
                r["_direction"] = dir_name
                r["_level_source"] = level_src
            all_results.extend(results)

        elapsed = time.time() - t0

        # ── Deduplicate results ────────────────────────────────────────────
        # Two records from different (direction, level_source) expansions
        # could theoretically describe the same economic trade.
        # Dedup key: the fields that identify a unique trade setup.
        def _dedup_key(r):
            return (
                r.get("symbol", ""),
                r.get("session_date", ""),
                r.get("_direction", ""),
                r.get("_level_source", ""),
                r.get("entry_price_ticks"),
                r.get("stop_price_ticks"),
                r.get("entry_timestamp"),
            )

        seen = set()
        deduped = []
        for r in all_results:
            k = _dedup_key(r)
            if k in seen:
                continue
            seen.add(k)
            deduped.append(r)
        all_results = deduped

        # Compute metrics on deduped results
        metrics = _compute_metrics(all_results)

        # Build trade rows — sort chronologically
        valid_results = [r for r in all_results if r["detection_status"] == "VALID"]
        valid_results.sort(key=lambda r: (
            r.get("session_date", ""),
            r.get("_direction", ""),
            r.get("_level_source", ""),
            r.get("run_record_id", ""),
        ))
        trades = [_build_trade_row(r, i, sessions_data) for i, r in enumerate(valid_results)]

        # Build chart events for each trade
        chart_events = []
        for i, r in enumerate(valid_results):
            session = next(
                (s for s in all_sessions
                 if s["symbol"] == r["symbol"] and s["date"] == r["session_date"]),
                None,
            )
            if session is None:
                continue
            event = export_visual_event(session["candles"], r)
            event["symbol"] = r["symbol"]
            dir_tag = r.get("_direction", "LONG")
            event["sequence_id"] = f"{dir_tag[0]}-SEQ-{i + 1:03d}"
            event["direction"] = dir_tag
            event["level_source"] = r.get("_level_source", "ORB_HIGH")

            # v2 target fields for chart
            v2t = _extract_v2_target_fields(r)
            if v2t:
                event["target_price_ticks"] = v2t["target_price_ticks"]
                event["requested_target_r"] = v2t["requested_target_r"]
                event["effective_target_r"] = v2t["effective_target_r"]
                event["target_label"] = v2t["target_label"]

            # Add sequence validation data
            candles = session["candles"]
            ls_tag = r.get("_level_source", "ORB_HIGH")
            ec = {
                "timeframe_minutes": tf_minutes,
                "timezone": "America/New_York",
                "session_open": "09:30",
                "orb_start": "session_open",
                "orb_duration_minutes": base_preset["orb_duration_minutes"],
                "level_source": ls_tag,
                "direction": dir_tag,
                "tick_size": config["tick_size"],
                "min_displacement_ticks": base_preset["min_displacement_ticks"],
                "min_penetration_ticks": base_preset["min_penetration_ticks"],
                "min_close_beyond_level_ticks": base_preset["min_close_beyond_level_ticks"],
                "consecutive_orb_closes": base_preset["consecutive_orb_closes"],
            }
            try:
                sc = build_session_context(candles, ec)
                orb = build_orb(sc["candles"], sc, ec)
                brk = find_break(sc["candles"], orb, ec)
                disp = find_displacement(sc["candles"], orb, brk, ec)
                sv = validate_sequence(sc["candles"], orb, brk, disp, ec)
                if sv["status"] == "INVALIDATED":
                    event["invalidation_index"] = sv["invalidation_index"]
                    event["consecutive_inside_closes"] = [
                        {"bar_index": bi, "close": cv,
                         "time_ms": sc["candles"][bi]["time_ms"],
                         "open": sc["candles"][bi]["open"],
                         "high": sc["candles"][bi]["high"],
                         "low": sc["candles"][bi]["low"]}
                        for bi, cv in sv["consecutive_inside_closes"]
                    ]
                else:
                    event["invalidation_index"] = None
                    event["consecutive_inside_closes"] = []

                # Wick depth
                rc = {**ec}
                if sv["status"] == "INVALIDATED":
                    rc["_max_valid_index"] = sv["max_valid_index"]
                rt = find_retest_window(sc["candles"], orb, brk, disp, rc)
                if rt["status"] == "OK":
                    rej = find_rejection(sc["candles"], orb, brk, disp, rt, rc)
                    event["wick_depth_ticks"] = rej.get("wick_depth_ticks")
                    # Failed retests
                    frs = rej.get("failed_retests", [])
                    event["all_retest_candidates"] = [
                        {"bar_index": fr["candle_index"],
                         "failed_rules": fr["failed_rules"]}
                        for fr in frs
                    ]
            except Exception:
                event["invalidation_index"] = None
                event["consecutive_inside_closes"] = []
                event["wick_depth_ticks"] = None
                event["all_retest_candidates"] = []

            # PDH/PDL
            sym_data = sessions_data.get(r["symbol"], {})
            sym_dates = sym_data.get("dates", [])
            d_idx = sym_dates.index(r["session_date"]) if r["session_date"] in sym_dates else -1
            if d_idx > 0:
                cbd = sym_data.get("candles_by_date") or sym_data.get("sessions", {})
                prev_key = sym_dates[d_idx - 1]
                prev_c = cbd.get(prev_key, [])
                if prev_c:
                    event["pdh"] = max(c["high"] for c in prev_c)
                    event["pdl"] = min(c["low"] for c in prev_c)

            chart_events.append(event)

        response = {
            "run_id": run_id,
            "config_source": config_source,
            "timestamp": datetime.now(tz=__import__('datetime').timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 3),
            "symbols": symbols,
            "start_date": start_date or all_sessions[0]["date"],
            "end_date": end_date or all_sessions[-1]["date"],
            "timeframe": timeframe,
            "direction": direction,
            "level_source": level_source,
            "executed_combinations": [
                {"direction": d, "level_source": ls}
                for d, ls in directions
            ],
            "preset": base_preset,
            "config": config,
            "provenance": provenance,
            "metrics": metrics,
            "trades": trades,
            "chart_events": chart_events,
            "total_sessions": len(all_sessions),
        }
        if loaded_preset:
            response["preset_id"] = loaded_preset["preset_id"]
            response["preset_schema_version"] = loaded_preset["schema_version"]
            response["preset_name"] = loaded_preset["name"]
        return jsonify(response)

    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc(),
        }), 500


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Backtest Lab starting...")
    print(f"Repo root: {REPO_ROOT}")
    print(f"Data dir:  {DATI_DIR}")
    print(f"Lab dir:   {LAB_DIR}")
    print(f"Open http://localhost:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
