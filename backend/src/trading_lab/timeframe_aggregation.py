"""Timeframe aggregation — builds higher timeframes from genuine 1-minute bars.

Aggregation rules:
  - open  = first bar's open
  - high  = max of highs
  - low   = min of lows
  - close = last bar's close
  - volume = sum of volumes

Session boundaries are respected: bars are never aggregated across
different trading sessions.

Public API:

    aggregate_candles(candles_1m, target_minutes) → list[dict]
    load_or_aggregate(symbol, timeframe) → dict

Supported target timeframes: 1, 2, 3, 5, 10 minutes.
"""

from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path


def aggregate_candles(
    candles_1m: list[dict],
    target_minutes: int,
) -> list[dict]:
    """Aggregate 1-minute candles into N-minute candles.

    Parameters
    ----------
    candles_1m : list of candle dicts with time_ms, open, high, low, close, volume
    target_minutes : target bar size in minutes (1, 2, 3, 5, 10)

    Returns
    -------
    list of aggregated candle dicts
    """
    if target_minutes == 1:
        return list(candles_1m)

    if target_minutes not in (2, 3, 5, 10):
        raise ValueError(f"Unsupported target timeframe: {target_minutes}m")

    if not candles_1m:
        return []

    result = []
    bucket: list[dict] = []
    bucket_start_minute = None

    for candle in candles_1m:
        ts = candle["time_ms"] / 1000
        dt = datetime.fromtimestamp(ts)
        # Minutes since midnight
        total_minutes = dt.hour * 60 + dt.minute
        # Which bucket does this bar belong to?
        bucket_id = total_minutes // target_minutes

        if bucket_start_minute is None:
            bucket_start_minute = bucket_id

        if bucket_id != bucket_start_minute and bucket:
            # Flush current bucket
            result.append(_flush_bucket(bucket))
            bucket = []
            bucket_start_minute = bucket_id

        bucket.append(candle)

    # Flush last bucket
    if bucket:
        result.append(_flush_bucket(bucket))

    return result


def _flush_bucket(bucket: list[dict]) -> dict:
    """Aggregate a bucket of candles into one bar."""
    return {
        "time_ms": bucket[0]["time_ms"],
        "open": bucket[0]["open"],
        "high": max(c["high"] for c in bucket),
        "low": min(c["low"] for c in bucket),
        "close": bucket[-1]["close"],
        "volume": sum(c.get("volume", 0) for c in bucket),
    }


def parse_csv_candles(filepath: str | Path) -> list[dict]:
    """Parse a TradingView/yfinance CSV into candle dicts."""
    candles = []
    with open(filepath) as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i < 3:
                continue
            if not row[0].strip():
                continue
            dt = datetime.fromisoformat(row[0])
            candles.append({
                "time_ms": int(dt.timestamp() * 1000),
                "open": float(row[4]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[1]),
                "volume": int(float(row[5])),
            })
    return candles


def split_into_sessions(candles: list[dict]) -> dict[str, list[dict]]:
    """Split candles into per-date sessions."""
    sessions: dict[str, list[dict]] = {}
    for c in candles:
        dt = datetime.fromtimestamp(c["time_ms"] / 1000)
        date = dt.strftime("%Y-%m-%d")
        if date not in sessions:
            sessions[date] = []
        sessions[date].append(c)
    return sessions


def available_timeframes(dati_dir: str | Path, symbol: str) -> list[dict]:
    """Check which timeframes are available for a symbol."""
    dati = Path(dati_dir)
    result = []

    has_1m = (dati / f"{symbol}_1m.csv").exists()
    has_5m = (dati / f"{symbol}_5m.csv").exists()

    for tf_min, label in [(1, "1m"), (2, "2m"), (3, "3m"), (5, "5m"), (10, "10m")]:
        if tf_min == 1:
            available = has_1m
            reason = "" if available else "Requires genuine 1-minute data. Run: python estrategie/scarica_dati_1m.py"
        elif tf_min == 5:
            available = has_5m or has_1m
            reason = "" if available else "No 5m or 1m data available"
        elif tf_min in (2, 3):
            available = has_1m
            reason = "" if available else "Requires genuine 1-minute data to aggregate"
        elif tf_min == 10:
            available = has_1m or has_5m
            reason = "" if available else "Requires 1m or 5m source data"
        else:
            available = False
            reason = "Unsupported"

        result.append({
            "value": label,
            "minutes": tf_min,
            "label": f"{tf_min} minute{'s' if tf_min > 1 else ''}",
            "available": available,
            "reason": reason,
        })

    return result


def load_candles_for_timeframe(
    dati_dir: str | Path,
    symbol: str,
    timeframe_minutes: int,
) -> dict:
    """Load candles at the requested timeframe, aggregating from 1m if needed.

    Returns dict with keys: candles_by_date, source_timeframe, aggregation_method,
    dates, earliest, latest, session_count.
    """
    dati = Path(dati_dir)

    # Try direct file first
    direct_file = dati / f"{symbol}_{timeframe_minutes}m.csv"
    file_1m = dati / f"{symbol}_1m.csv"

    source_tf = None
    aggregation = "none"
    all_candles = []

    if timeframe_minutes == 1 and file_1m.exists():
        source_tf = "1m"
        all_candles = parse_csv_candles(file_1m)
    elif direct_file.exists() and timeframe_minutes in (5,):
        source_tf = f"{timeframe_minutes}m"
        all_candles = parse_csv_candles(direct_file)
    elif file_1m.exists():
        # Aggregate from 1m
        source_tf = "1m"
        aggregation = f"aggregated from 1m to {timeframe_minutes}m"
        raw_1m = parse_csv_candles(file_1m)
        # Aggregate per session to respect session boundaries
        sessions_1m = split_into_sessions(raw_1m)
        for date in sorted(sessions_1m.keys()):
            agg = aggregate_candles(sessions_1m[date], timeframe_minutes)
            all_candles.extend(agg)
    elif timeframe_minutes == 10 and (dati / f"{symbol}_5m.csv").exists():
        source_tf = "5m"
        aggregation = "aggregated from 5m to 10m"
        raw_5m = parse_csv_candles(dati / f"{symbol}_5m.csv")
        sessions_5m = split_into_sessions(raw_5m)
        for date in sorted(sessions_5m.keys()):
            agg = aggregate_candles(sessions_5m[date], 2)  # 2 × 5m bars
            all_candles.extend(agg)
    else:
        return {
            "error": f"No data available for {symbol} at {timeframe_minutes}m. "
                     f"Requires {symbol}_1m.csv or {symbol}_{timeframe_minutes}m.csv in dati/",
        }

    # Split into sessions
    candles_by_date = split_into_sessions(all_candles)
    dates = sorted(candles_by_date.keys())

    return {
        "candles_by_date": candles_by_date,
        "source_timeframe": source_tf,
        "selected_timeframe": f"{timeframe_minutes}m",
        "aggregation_method": aggregation,
        "dates": dates,
        "earliest": dates[0] if dates else None,
        "latest": dates[-1] if dates else None,
        "session_count": len(dates),
    }
