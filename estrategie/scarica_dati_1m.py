"""Download genuine 1-minute historical bars from Yahoo Finance.

Usage:
    cd trading_bot
    source venv/bin/activate
    pip install yfinance
    python estrategie/scarica_dati_1m.py

Limitations:
    Yahoo Finance retains only ~7-30 days of 1-minute data.
    For older dates, 1-minute bars are not available.
    The script downloads whatever is currently available.

Output:
    dati/{SYMBOL}_1m.csv for each symbol
"""

import os
import sys

try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed. Run: pip install yfinance")
    sys.exit(1)

DATI_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dati")
os.makedirs(DATI_DIR, exist_ok=True)

SYMBOLS = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "AMZN": "AMZN",
    "TSLA": "TSLA",
    "NVDA": "NVDA",
    "META": "META",
    "MSFT": "MSFT",
    "GOOGL": "GOOGL",
    "MU": "MU",
}

# Yahoo Finance 1m limit: max 7 days per request, ~30 days total retention
PERIOD = "7d"
INTERVAL = "1m"

print("Downloading genuine 1-minute bars...")
print(f"Period: {PERIOD} (Yahoo Finance retains ~7-30 days of 1m data)")
print(f"Output: {DATI_DIR}/")
print()

for name, ticker in SYMBOLS.items():
    print(f"  Downloading {name}...", end=" ", flush=True)
    try:
        df = yf.download(
            ticker,
            period=PERIOD,
            interval=INTERVAL,
            progress=False,
            auto_adjust=True,
        )
        if df.empty:
            print("no data available")
            continue

        path = os.path.join(DATI_DIR, f"{name}_1m.csv")
        df.to_csv(path)

        dates = df.index.date
        unique_dates = sorted(set(str(d) for d in dates))
        print(f"{len(df)} bars, {len(unique_dates)} sessions "
              f"({unique_dates[0]} → {unique_dates[-1]})")

    except Exception as e:
        print(f"error: {e}")

print()
print("Done. To verify: ls -la dati/*_1m.csv")
print()
print("After downloading, restart the Backtest Lab server.")
print("1m, 2m, 3m timeframes will become selectable automatically.")
