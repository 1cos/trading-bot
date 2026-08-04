"""Download 1-minute historical bars from Interactive Brokers.

Requirements:
    pip install ib_insync

    IB Gateway or TWS must be running on localhost:4002 (paper)
    or localhost:4001 (live). Paper trading is recommended.

Usage:
    cd trading_bot
    source venv/bin/activate
    python scripts/download_ib_1m.py

    # Custom options:
    python scripts/download_ib_1m.py --days 365 --port 4002
    python scripts/download_ib_1m.py --symbols SPY QQQ NVDA
    python scripts/download_ib_1m.py --start 2025-08-01 --end 2026-08-01

IB Data Limits:
    - 1-minute bars: up to ~1 year of history
    - Pacing: max 6 requests per 10 seconds for historical data
    - Each request can fetch up to 1 day of 1m data
    - The script handles pacing automatically

Output:
    dati/1m/{SYMBOL}_1m.csv

    Format: time_et,open,high,low,close,volume
    Timestamps are naive Eastern Time (America/New_York).
    Includes pre-market (04:00) through post-market (20:00).

    Files are written atomically (temp file + rename) to avoid
    corrupting existing data during download.

Incremental Mode:
    By default, if a file already exists, the script reads its last
    date and downloads only newer data, then appends.
    Use --full to force a complete re-download.
"""

import argparse
import csv
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Python 3.14+: asyncio no longer auto-creates an event loop.
# ib_insync/eventkit needs one at import time.
import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

try:
    from ib_insync import IB, Stock, util
except ImportError:
    print("ib_insync not installed. Run: pip install ib_insync")
    sys.exit(1)


# ── Configuration ────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
DATI_DIR = REPO_ROOT / "dati" / "1m"

ET = ZoneInfo("America/New_York")

DEFAULT_SYMBOLS = [
    "SPY", "QQQ", "NVDA", "TSLA", "META",
    "AMZN", "MSFT", "GOOGL", "AAPL", "AMD",
]

# IB pacing: sleep between requests to avoid "Pacing violation"
PACING_SLEEP = 2.0  # seconds between each daily request


# ── Helpers ──────────────────────────────────────────────────────────────────


def _last_date_in_csv(filepath: Path) -> str | None:
    """Read the last date (YYYY-MM-DD) from an existing 1m CSV."""
    if not filepath.exists():
        return None
    last_line = ""
    with open(filepath) as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("time_et"):
                last_line = stripped
    if not last_line:
        return None
    # time_et column is first: "2026-07-30 09:30:00,..."
    ts = last_line.split(",")[0].strip()
    return ts[:10]  # "2026-07-30"


def _fetch_chunk(ib: IB, contract, end_date_str: str, duration: str = "1 W") -> list[dict]:
    """Fetch 1m bars for a time chunk (e.g. 1 week) from IB.

    Returns list of dicts: {time_et, open, high, low, close, volume}
    """
    end_dt = f"{end_date_str.replace('-', '')} 23:59:59"

    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end_dt,
            durationStr=duration,
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=False,
            formatDate=1,
            timeout=60,
        )
    except Exception as e:
        print(f"    Error fetching {date_str}: {e}")
        return []

    if not bars:
        return []

    candles = []
    for bar in bars:
        # bar.date is a datetime object in exchange timezone
        dt = bar.date
        if hasattr(dt, "astimezone"):
            dt_et = dt.astimezone(ET)
        else:
            # If it's a date string, parse it
            dt_et = datetime.fromisoformat(str(dt))
            if dt_et.tzinfo is None:
                dt_et = dt_et.replace(tzinfo=ET)
            else:
                dt_et = dt_et.astimezone(ET)

        # Format as naive ET string matching our CSV format
        time_str = dt_et.strftime("%Y-%m-%d %H:%M:%S")

        candles.append({
            "time_et": time_str,
            "open": round(bar.open, 6),
            "high": round(bar.high, 6),
            "low": round(bar.low, 6),
            "close": round(bar.close, 6),
            "volume": int(bar.volume),
        })

    return candles


def _write_csv(filepath: Path, candles: list[dict], mode: str = "w"):
    """Write candles to CSV atomically."""
    DATI_DIR.mkdir(parents=True, exist_ok=True)

    if mode == "a" and filepath.exists():
        # Append: write to temp, then append to existing
        with open(filepath, "a", newline="") as f:
            writer = csv.writer(f)
            for c in candles:
                writer.writerow([
                    c["time_et"], c["open"], c["high"],
                    c["low"], c["close"], c["volume"],
                ])
        return

    # Full write: atomic temp file + rename
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(DATI_DIR), suffix=".tmp", prefix=filepath.stem
    )
    try:
        with os.fdopen(tmp_fd, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["time_et", "open", "high", "low", "close", "volume"])
            for c in candles:
                writer.writerow([
                    c["time_et"], c["open"], c["high"],
                    c["low"], c["close"], c["volume"],
                ])
        os.replace(tmp_path, str(filepath))
    except Exception:
        os.unlink(tmp_path)
        raise


def _trading_days(start: datetime, end: datetime) -> list[str]:
    """Generate weekday date strings between start and end (inclusive)."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return days


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Download 1-minute historical bars from Interactive Brokers"
    )
    parser.add_argument(
        "--symbols", nargs="+", default=DEFAULT_SYMBOLS,
        help=f"Symbols to download (default: {' '.join(DEFAULT_SYMBOLS)})"
    )
    parser.add_argument(
        "--days", type=int, default=252,
        help="Number of calendar days to look back (default: 252, ~1 year)"
    )
    parser.add_argument(
        "--start", type=str, default=None,
        help="Start date YYYY-MM-DD (overrides --days)"
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="End date YYYY-MM-DD (default: today)"
    )
    parser.add_argument(
        "--port", type=int, default=4002,
        help="IB Gateway port (default: 4002 for paper trading)"
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="IB Gateway host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Force full re-download (ignore existing data)"
    )
    args = parser.parse_args()

    # Date range
    now_et = datetime.now(ET)
    if args.end:
        end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=ET)
    else:
        end_date = now_et

    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=ET)
    else:
        start_date = end_date - timedelta(days=args.days)

    print(f"IB 1-Minute Data Downloader")
    print(f"  Host:    {args.host}:{args.port}")
    print(f"  Range:   {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")
    print(f"  Symbols: {' '.join(args.symbols)}")
    print(f"  Output:  {DATI_DIR}/")
    print()

    # Connect to IB
    ib = IB()
    try:
        ib.connect(args.host, args.port, clientId=99, readonly=True)
        print(f"  Connected to IB Gateway on {args.host}:{args.port}")
    except Exception as e:
        print(f"  ERROR: Cannot connect to IB Gateway on {args.host}:{args.port}")
        print(f"         {e}")
        print()
        print("  Make sure IB Gateway or TWS is running and API is enabled.")
        print("  Paper trading: port 4002")
        print("  Live trading:  port 4001")
        sys.exit(1)

    try:
        for sym in args.symbols:
            filepath = DATI_DIR / f"{sym}_1m.csv"
            print(f"\n  {sym}:")

            # Determine start date for this symbol
            sym_start = start_date
            if not args.full:
                last = _last_date_in_csv(filepath)
                if last:
                    # Start from the day after the last existing date
                    resume = datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=ET)
                    resume += timedelta(days=1)
                    if resume > sym_start:
                        sym_start = resume
                        print(f"    Existing data through {last}, resuming from {sym_start.strftime('%Y-%m-%d')}")

            days = _trading_days(sym_start, end_date)
            if not days:
                print(f"    No new days to download")
                continue

            print(f"    {len(days)} trading days to fetch...")

            contract = Stock(sym, "SMART", "USD")
            ib.qualifyContracts(contract)

            all_candles = []
            for i, day in enumerate(days):
                candles = _fetch_one_day(ib, contract, day)
                if candles:
                    all_candles.extend(candles)
                    print(f"    [{i+1}/{len(days)}] {day}: {len(candles)} bars", flush=True)
                else:
                    print(f"    [{i+1}/{len(days)}] {day}: no data (holiday?)", flush=True)

                # Pacing: IB limits to ~6 requests per 10 seconds
                if i < len(days) - 1:
                    time.sleep(PACING_SLEEP)

            if not all_candles:
                print(f"    No data received")
                continue

            # Sort by timestamp
            all_candles.sort(key=lambda c: c["time_et"])

            # Write
            if not args.full and filepath.exists():
                _write_csv(filepath, all_candles, mode="a")
                print(f"    Appended {len(all_candles)} bars to {filepath.name}")
            else:
                _write_csv(filepath, all_candles, mode="w")
                print(f"    Wrote {len(all_candles)} bars to {filepath.name}")

            # Summary
            dates = sorted(set(c["time_et"][:10] for c in all_candles))
            print(f"    Sessions: {len(dates)} ({dates[0]} → {dates[-1]})")

    finally:
        ib.disconnect()
        print("\n  Disconnected from IB")

    print("\nDone. Restart the Backtest Lab server to see new data.")


if __name__ == "__main__":
    main()
