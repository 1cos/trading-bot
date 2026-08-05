"""Download 1-minute historical bars for futures from Interactive Brokers.

Requirements:
    pip install ib_insync

    IB Gateway or TWS must be running on localhost:4002 (paper)
    or localhost:4001 (live).

Usage:
    cd trading_bot
    source venv/bin/activate

    # Download a specific contract:
    python scripts/download_ib_futures_1m.py --root MES --localSymbol MESU6

    # Let IBKR resolve the front-month:
    python scripts/download_ib_futures_1m.py --root MES

    # Custom date range:
    python scripts/download_ib_futures_1m.py --root MES --start 2026-06-01 --end 2026-08-01

Output:
    dati/futures/1m/{ROOT}/{localSymbol}_1m.csv

    Format: time_ct,open,high,low,close,volume
    Timestamps are naive Central Time (America/Chicago).
    Globex bars (overnight session) are preserved in the raw file.

    The contract is registered in dati/futures/futures_manifest.json
    after successful download.

IMPORTANT:
    This script uses Future() not Stock(). It never treats MES/MNQ as equity.
    Contract identity (conId, localSymbol, expiry) is recorded in the manifest.
"""

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Python 3.14+: asyncio compatibility
import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

try:
    from ib_insync import IB, Future, util
except ImportError:
    print("ib_insync not installed. Run: pip install ib_insync")
    sys.exit(1)


# ── Configuration ────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
FUTURES_DIR = REPO_ROOT / "dati" / "futures"
MANIFEST_PATH = FUTURES_DIR / "futures_manifest.json"

CT = ZoneInfo("America/Chicago")

# IB pacing
PACING_SLEEP = 2.0


# ── Manifest helpers ─────────────────────────────────────────────────────────

def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {"root_symbols": {}}


def _save_manifest(manifest: dict):
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")


def _register_contract(manifest, root, contract_info):
    """Register downloaded contract in manifest."""
    if root not in manifest.get("root_symbols", {}):
        raise ValueError(f"Root symbol {root} not in futures manifest")
    contracts = manifest["root_symbols"][root].setdefault("contracts", [])
    # Replace if same localSymbol
    contracts[:] = [
        c for c in contracts
        if c.get("localSymbol") != contract_info["localSymbol"]
    ]
    contracts.append(contract_info)
    contracts.sort(key=lambda c: c.get("expiry", ""))


# ── Data helpers ─────────────────────────────────────────────────────────────

def _trading_days(start: datetime, end: datetime) -> list[str]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return days


def _fetch_one_day(ib, contract, date_str: str) -> list[dict]:
    """Fetch 1m bars for one trading day from IB."""
    end_dt = f"{date_str.replace('-', '')} 23:59:59"
    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end_dt,
            durationStr="1 D",
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=False,
            formatDate=1,
            timeout=30,
        )
    except Exception as e:
        print(f"    Error fetching {date_str}: {e}")
        return []

    if not bars:
        return []

    candles = []
    for bar in bars:
        dt = bar.date
        if hasattr(dt, "astimezone"):
            dt_ct = dt.astimezone(CT)
        else:
            dt_ct = datetime.fromisoformat(str(dt))
            if dt_ct.tzinfo is None:
                dt_ct = dt_ct.replace(tzinfo=CT)
            else:
                dt_ct = dt_ct.astimezone(CT)
        time_str = dt_ct.strftime("%Y-%m-%d %H:%M:%S")
        candles.append({
            "time_ct": time_str,
            "open": round(bar.open, 6),
            "high": round(bar.high, 6),
            "low": round(bar.low, 6),
            "close": round(bar.close, 6),
            "volume": int(bar.volume),
        })
    return candles


def _write_csv(filepath: Path, candles: list[dict], mode: str = "w"):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if mode == "a" and filepath.exists():
        with open(filepath, "a", newline="") as f:
            writer = csv.writer(f)
            for c in candles:
                writer.writerow([
                    c["time_ct"], c["open"], c["high"],
                    c["low"], c["close"], c["volume"],
                ])
        return
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(filepath.parent), suffix=".tmp", prefix=filepath.stem
    )
    try:
        with os.fdopen(tmp_fd, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["time_ct", "open", "high", "low", "close", "volume"])
            for c in candles:
                writer.writerow([
                    c["time_ct"], c["open"], c["high"],
                    c["low"], c["close"], c["volume"],
                ])
        os.replace(tmp_path, str(filepath))
    except Exception:
        os.unlink(tmp_path)
        raise


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download 1-minute futures bars from Interactive Brokers"
    )
    parser.add_argument(
        "--root", required=True,
        help="Root symbol (e.g. MES, MNQ)"
    )
    parser.add_argument(
        "--localSymbol", default=None,
        help="Specific local symbol (e.g. MESU6). If omitted, IBKR resolves front-month."
    )
    parser.add_argument(
        "--days", type=int, default=90,
        help="Calendar days to look back (default: 90)"
    )
    parser.add_argument(
        "--start", type=str, default=None,
        help="Start date YYYY-MM-DD"
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="End date YYYY-MM-DD"
    )
    parser.add_argument(
        "--port", type=int, default=4002,
        help="IB Gateway port (default: 4002 paper)"
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="IB Gateway host"
    )
    args = parser.parse_args()

    manifest = _load_manifest()
    root_spec = manifest.get("root_symbols", {}).get(args.root)
    if not root_spec:
        print(f"ERROR: Root symbol {args.root} not in futures_manifest.json")
        print(f"Available: {sorted(manifest.get('root_symbols', {}).keys())}")
        sys.exit(1)

    # Date range
    now_ct = datetime.now(CT)
    end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=CT) if args.end else now_ct
    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=CT)
    else:
        start_date = end_date - timedelta(days=args.days)

    print(f"IB Futures 1-Minute Data Downloader")
    print(f"  Root:    {args.root}")
    print(f"  Exchange: {root_spec['exchange']}")
    print(f"  Range:   {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")
    print()

    # Build IBKR contract — NEVER use Stock()
    if args.localSymbol:
        contract = Future(localSymbol=args.localSymbol, exchange=root_spec["exchange"])
    else:
        contract = Future(
            symbol=args.root,
            exchange=root_spec["exchange"],
            currency=root_spec["currency"],
        )

    # Connect
    ib = IB()
    try:
        ib.connect(args.host, args.port, clientId=99, readonly=True)
        print(f"  Connected to IB Gateway on {args.host}:{args.port}")
    except Exception as e:
        print(f"  ERROR: Cannot connect to IB Gateway: {e}")
        sys.exit(1)

    try:
        # Qualify contract — IBKR resolves conId, localSymbol, expiry
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            print(f"  ERROR: IBKR could not qualify contract for {args.root}")
            sys.exit(1)

        c = qualified[0] if isinstance(qualified, list) else contract
        local_sym = c.localSymbol
        expiry = c.lastTradeDateOrContractMonth
        con_id = c.conId
        trading_class = c.tradingClass
        exchange = c.exchange

        print(f"  Qualified: localSymbol={local_sym}, expiry={expiry}, "
              f"conId={con_id}, tradingClass={trading_class}")

        filepath = FUTURES_DIR / "1m" / args.root / f"{local_sym}_1m.csv"
        days = _trading_days(start_date, end_date)
        print(f"  {len(days)} trading days to fetch...")

        all_candles = []
        for i, day in enumerate(days):
            candles = _fetch_one_day(ib, c, day)
            if candles:
                all_candles.extend(candles)
                print(f"  [{i+1}/{len(days)}] {day}: {len(candles)} bars", flush=True)
            else:
                print(f"  [{i+1}/{len(days)}] {day}: no data", flush=True)
            if i < len(days) - 1:
                time.sleep(PACING_SLEEP)

        if not all_candles:
            print("  No data received")
            sys.exit(1)

        all_candles.sort(key=lambda c: c["time_ct"])

        # Dedup
        seen_ts = {}
        deduped = []
        for candle in all_candles:
            ts = candle["time_ct"]
            if ts in seen_ts:
                continue
            seen_ts[ts] = candle
            deduped.append(candle)
        removed = len(all_candles) - len(deduped)
        if removed:
            print(f"  Deduped: {removed} duplicates removed")
        all_candles = deduped

        _write_csv(filepath, all_candles)
        print(f"  Wrote {len(all_candles)} bars to {filepath}")

        dates = sorted(set(c["time_ct"][:10] for c in all_candles))

        # Register in manifest
        contract_info = {
            "localSymbol": local_sym,
            "expiry": expiry,
            "conId": con_id,
            "tradingClass": trading_class,
            "exchange": exchange,
            "file": f"1m/{args.root}/{local_sym}_1m.csv",
            "downloaded_at": datetime.now(CT).isoformat(),
            "earliest_bar": dates[0],
            "latest_bar": dates[-1],
            "session_count": len(dates),
        }
        _register_contract(manifest, args.root, contract_info)
        _save_manifest(manifest)
        print(f"  Registered contract in manifest")
        print(f"  Sessions: {len(dates)} ({dates[0]} → {dates[-1]})")

    finally:
        ib.disconnect()
        print("\n  Disconnected from IB")

    print("\nDone.")


if __name__ == "__main__":
    main()
