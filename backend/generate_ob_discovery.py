#!/usr/bin/env python3
"""Generate Order Block Discovery Workspace HTML.

Produces a standalone HTML file containing 45 sessions (5 per symbol,
stratified by momentum intensity) for manual Order Block labeling.

Usage:
    python backend/generate_ob_discovery.py

Output:
    backend/output/order_block_discovery_45.html
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Path setup ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR / "src"))

from trading_lab.csv_parser import parse_candles_from_csv
from trading_lab.session_split import split_into_sessions

# ── Constants ───────────────────────────────────────────────────────────────
SYMBOLS = ["SPY", "QQQ", "AMZN", "TSLA", "NVDA", "META", "MSFT", "GOOGL", "MU"]
SESSIONS_PER_SYMBOL = 5
SEED = 42
OUTPUT_PATH = SCRIPT_DIR / "output" / "order_block_discovery_45.html"
ET = ZoneInfo("America/New_York")


# ── Session metrics ─────────────────────────────────────────────────────────

def _compute_session_metrics(candles: list[dict]) -> dict:
    """Compute momentum metrics for stratified sampling."""
    max_run = 0
    max_move = 0.0
    run = 0
    run_dir = None
    run_start = None

    for i in range(1, len(candles)):
        c = candles[i]
        if c["close"] > c["open"]:
            d = "bull"
        elif c["close"] < c["open"]:
            d = "bear"
        else:
            d = None

        if d and d == run_dir:
            run += 1
        elif d:
            run = 1
            run_dir = d
            run_start = c["open"]
        else:
            run = 0
            run_dir = None
            run_start = None

        max_run = max(max_run, run)
        if run >= 2 and run_start:
            if d == "bull":
                move = (c["close"] - run_start) / run_start * 100
            else:
                move = (run_start - c["close"]) / run_start * 100
            max_move = max(max_move, move)

    return {"max_run": max_run, "max_move_pct": max_move}


# ── Session selection ───────────────────────────────────────────────────────

def select_sessions() -> list[dict]:
    """Select 45 sessions: 5 per symbol, stratified by momentum."""
    rng = random.Random(SEED)
    batch = []

    for sym in SYMBOLS:
        csv_path = PROJECT_ROOT / "dati" / f"{sym}_5m.csv"
        csv_text = csv_path.read_text()
        candles = parse_candles_from_csv(csv_text)
        groups = split_into_sessions(candles, "America/New_York")

        sym_sessions = []
        for g in groups:
            metrics = _compute_session_metrics(g["candles"])
            sym_sessions.append({
                "symbol": sym,
                "date": g["date"],
                "candles": g["candles"],
                **metrics,
            })

        sym_sessions.sort(key=lambda x: x["max_move_pct"], reverse=True)
        n = len(sym_sessions)
        top = sym_sessions[: n // 3]
        mid = sym_sessions[n // 3: 2 * n // 3]
        bot = sym_sessions[2 * n // 3:]

        picks = []
        picks.extend(rng.sample(top, min(2, len(top))))
        picks.extend(rng.sample(mid, min(2, len(mid))))
        picks.extend(rng.sample(bot, min(1, len(bot))))

        for s in picks:
            batch.append({
                "symbol": s["symbol"],
                "date": s["date"],
                "candles": s["candles"],
            })

    batch.sort(key=lambda x: (x["symbol"], x["date"]))
    return batch


# ── Candle export ───────────────────────────────────────────────────────────

def _export_candles(candles: list[dict]) -> list[dict]:
    """Convert candles to Lightweight Charts format (ET timestamps)."""
    out = []
    for i, c in enumerate(candles):
        dt = datetime.fromtimestamp(c["time_ms"] / 1000, tz=ET)
        ts = int(dt.timestamp())
        out.append({
            "time": ts,
            "open": round(c["open"], 4),
            "high": round(c["high"], 4),
            "low": round(c["low"], 4),
            "close": round(c["close"], 4),
            "volume": c.get("volume", 0),
            "idx": i,
            "time_ms": c["time_ms"],
        })
    return out


# ── HTML generation ─────────────────────────────────────────────────────────

def _build_html(sessions: list[dict]) -> str:
    """Build the complete HTML workspace."""
    events = []
    for s in sessions:
        events.append({
            "symbol": s["symbol"],
            "session_date": s["date"],
            "timeframe": "5m",
            "session_id": f"{s['symbol']}_{s['date']}_5m",
            "candles": _export_candles(s["candles"]),
        })

    gen_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events_json = json.dumps(events, separators=(",", ":"))

    # NOTE: HTML/JS template is in a separate file to avoid f-string
    # brace-escaping corruption of JavaScript code.
    template_path = Path(__file__).resolve().parent / "ob_discovery_template.html"
    template = template_path.read_text(encoding="utf-8")
    template = template.replace("__EVENTS_JSON__", events_json)
    template = template.replace("__GEN_TS__", gen_ts)
    return template



# ── Main ────────────────────────────────────────────────────────────────────

def main():
    sessions = select_sessions()
    html = _build_html(sessions)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")

    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"Generated: {OUTPUT_PATH}")
    print(f"Sessions:  {len(sessions)}")
    print(f"Symbols:   {len(set(s['symbol'] for s in sessions))}")
    print(f"File size: {size_kb:.0f} KB")

    # Per-symbol count
    from collections import Counter
    counts = Counter(s["symbol"] for s in sessions)
    for sym in SYMBOLS:
        print(f"  {sym}: {counts[sym]} sessions")


if __name__ == "__main__":
    main()
