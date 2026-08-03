#!/usr/bin/env python3
"""
Download 1-minute candle data with pre-market for the Max Bot.

Usage:
    cd ~/trading_bot
    pip install yfinance
    python3 scripts/download_1m_data.py

Output:
    dati/1m/SPY_1m.csv
    dati/1m/QQQ_1m.csv
    dati/1m/TSLA_1m.csv
    dati/1m/NVDA_1m.csv
    dati/1m/AMD_1m.csv
    dati/1m/AAPL_1m.csv
    dati/1m/AMZN_1m.csv
    dati/1m/META_1m.csv
    dati/1m/MSFT_1m.csv
    dati/1m/GOOGL_1m.csv

Notes:
    - Yahoo Finance provides max 7 days of 1m data
    - Pre-market starts at 04:00 ET, after-hours until 20:00 ET
    - Run this daily to accumulate history
    - Existing data is preserved; new data is appended
    - All timestamps are ET (US/Eastern)
    - Canonical format: time_et, open, high, low, close, volume
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("Install required packages:")
    print("  pip install yfinance pandas")
    sys.exit(1)

# ─── Configuration ───────────────────────────────────────────────

SYMBOLS = [
    # Index ETFs (for alignment)
    "SPY", "QQQ",
    # Tech names Max trades
    "TSLA", "NVDA", "AMD", "AAPL",
    # Other watchlist
    "AMZN", "META", "MSFT", "GOOGL",
]

# Futures — yfinance tickers for continuous contracts
FUTURES = {
    "MES": "MES=F",    # Micro E-mini S&P 500
    "MNQ": "MNQ=F",    # Micro E-mini Nasdaq-100
}

OUTPUT_DIR = Path(__file__).parent.parent / "dati" / "1m"
COLUMNS = ["time_et", "open", "high", "low", "close", "volume"]

# ─── Functions ───────────────────────────────────────────────────

def download_symbol(symbol: str, yf_ticker: str = None) -> pd.DataFrame:
    """Download 1m data with pre/post market for a symbol."""
    ticker = yf_ticker or symbol
    print(f"  Downloading {symbol} ({ticker})...", end=" ", flush=True)

    try:
        t = yf.Ticker(ticker)
        df = t.history(period="7d", interval="1m", prepost=True)

        if df.empty:
            print("NO DATA")
            return pd.DataFrame()

        # Normalize timezone to US/Eastern
        if df.index.tz is not None:
            df.index = df.index.tz_convert("US/Eastern")
        
        # Build canonical format
        result = pd.DataFrame({
            "time_et": df.index.strftime("%Y-%m-%d %H:%M:%S"),
            "open": df["Open"].round(4),
            "high": df["High"].round(4),
            "low": df["Low"].round(4),
            "close": df["Close"].round(4),
            "volume": df["Volume"].astype(int),
        })

        print(f"{len(result)} rows, {result['time_et'].iloc[0]} to {result['time_et'].iloc[-1]}")
        return result

    except Exception as e:
        print(f"ERROR: {e}")
        return pd.DataFrame()


def merge_and_save(symbol: str, new_data: pd.DataFrame):
    """Append new data to existing file, deduplicating by timestamp."""
    filepath = OUTPUT_DIR / f"{symbol}_1m.csv"

    if filepath.exists():
        existing = pd.read_csv(filepath)
        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = combined.drop_duplicates(subset=["time_et"], keep="last")
        combined = combined.sort_values("time_et").reset_index(drop=True)
        new_rows = len(combined) - len(existing)
        print(f"    Merged: {len(existing)} existing + {new_rows} new = {len(combined)} total")
    else:
        combined = new_data
        print(f"    New file: {len(combined)} rows")

    combined.to_csv(filepath, index=False)


# ─── Main ────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Max Bot Data Download — 1-minute candles with pre-market")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Download equities/ETFs
    print("\nEquities & ETFs:")
    for symbol in SYMBOLS:
        df = download_symbol(symbol)
        if not df.empty:
            merge_and_save(symbol, df)

    # Download futures
    print("\nFutures:")
    for name, ticker in FUTURES.items():
        df = download_symbol(name, ticker)
        if not df.empty:
            merge_and_save(name, df)

    # Summary
    print("\n" + "=" * 60)
    print("Summary:")
    for f in sorted(OUTPUT_DIR.glob("*_1m.csv")):
        df = pd.read_csv(f)
        dates = pd.to_datetime(df["time_et"])
        n_days = dates.dt.date.nunique()
        print(f"  {f.name}: {len(df)} rows, {n_days} days, "
              f"{dates.iloc[0].strftime('%Y-%m-%d')} to {dates.iloc[-1].strftime('%Y-%m-%d')}")

    print(f"\nAll files saved to: {OUTPUT_DIR.resolve()}")
    print("\nTo accumulate history, run this script daily.")
    print("Each run adds new data and preserves existing rows.")


if __name__ == "__main__":
    main()
