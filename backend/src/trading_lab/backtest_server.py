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
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from trading_lab.strategy_runner import run_bdrr_strategy
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


# ── App setup ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)
CORS(app)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATI_DIR = REPO_ROOT / "dati"
LAB_DIR = REPO_ROOT / "lab"

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
    """Scan dati/ for available symbol files."""
    symbols = []
    seen = set()
    for f in sorted(DATI_DIR.iterdir()):
        if f.suffix != ".csv":
            continue
        # Extract symbol from filename like SPY_5m.csv or SPY_1m.csv
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

    win_rate = n_wins / n_trades if n_trades > 0 else 0
    loss_rate = n_losses / n_trades if n_trades > 0 else 0

    # R values
    r_values = []
    for r in valid:
        rr = r.get("realized_r")
        if rr is not None:
            r_values.append(float(rr))

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
        float("inf") if gross_profit > 0 else 0
    )

    # Equity curve + drawdown
    equity_curve = []
    cumulative = 0
    peak = 0
    max_dd = 0
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
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "Inf",
        "max_drawdown": round(max_dd, 4),
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
        "r_values": r_values,
        "avg_trade_duration": "unavailable",  # would need bar-level timestamps
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

    return {
        "trade_number": idx + 1,
        "run_record_id": r.get("run_record_id"),
        "symbol": r.get("symbol"),
        "date": r.get("session_date"),
        "direction": r.get("_direction", str(_g(dr, "direction", "?"))),
        "sequence_id": f"{r.get('_direction', 'L')[0]}-SEQ-{idx + 1:03d}",
        "break_time": _ms_to_time(_g(_g(dr, "break_bar"), "bar_utc_ms")),
        "confirmation_time": r.get("confirmation_timestamp"),
        "entry_time": r.get("entry_timestamp"),
        "exit_time": r.get("exit_timestamp"),
        "entry_price": round(entry_ticks * tick_size, 2) if entry_ticks else None,
        "stop_price": round(stop_ticks * tick_size, 2) if stop_ticks else None,
        "target_price": round(r2_ticks * tick_size, 2) if r2_ticks else None,
        "exit_price": round(exit_ticks * tick_size, 2) if exit_ticks else None,
        "outcome": str(r.get("outcome", "")),
        "realized_r": r.get("realized_r"),
        "wick_depth_ticks": None,  # from rejection result
        "failed_retest_count": 0,
        "detection_status": r.get("detection_status"),
        "preset_id": r.get("preset_id"),
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


@app.route("/api/run", methods=["POST"])
def api_run():
    """Execute a backtest run with the real Python detector."""
    try:
        body = request.get_json()
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

        # Determine which directions to run
        if direction == "BOTH":
            directions = [("LONG", "ORB_HIGH"), ("SHORT", "ORB_LOW")]
        elif direction == "SHORT":
            directions = [("SHORT", preset_overrides.get("level_source", "ORB_LOW"))]
        else:
            directions = [("LONG", preset_overrides.get("level_source", "ORB_HIGH"))]

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
            "consecutive_orb_closes": preset_overrides.get("consecutive_orb_closes", 2),
        }

        config = {
            "tick_size": config_overrides.get("tick_size", 0.01),
            "exit_target_r": config_overrides.get("exit_target_r", 2),
            "engine_version": "1.0.0",
        }

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
            results = run_bdrr_strategy(all_sessions, preset, config)
            # Tag each result with direction
            for r in results:
                r["_direction"] = dir_name
            all_results.extend(results)

        elapsed = time.time() - t0

        # Compute metrics
        metrics = _compute_metrics(all_results)

        # Build trade rows — sort chronologically for BOTH
        valid_results = [r for r in all_results if r["detection_status"] == "VALID"]
        valid_results.sort(key=lambda r: r.get("session_date", ""))
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

            # Add sequence validation data
            candles = session["candles"]
            ec = {
                "timeframe_minutes": tf_minutes,
                "timezone": "America/New_York",
                "session_open": "09:30",
                "orb_start": "session_open",
                "orb_duration_minutes": base_preset["orb_duration_minutes"],
                "level_source": "ORB_HIGH" if dir_tag == "LONG" else "ORB_LOW",
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

        return jsonify({
            "run_id": run_id,
            "timestamp": datetime.now(tz=__import__('datetime').timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 3),
            "symbols": symbols,
            "start_date": start_date or all_sessions[0]["date"],
            "end_date": end_date or all_sessions[-1]["date"],
            "timeframe": timeframe,
            "direction": direction,
            "preset": base_preset,
            "config": config,
            "provenance": provenance,
            "metrics": metrics,
            "trades": trades,
            "chart_events": chart_events,
            "total_sessions": len(all_sessions),
        })

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
