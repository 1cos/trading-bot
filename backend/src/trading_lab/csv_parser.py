"""Canonical CSV candle parser for the BDRR pipeline.

Ported from ``parseCandlesFromCSV`` in
estrategie/bdrr_strategy_runner.js (lines 89–108).

Accepts the repository 5-minute CSV format as a text string and returns
a list of raw candle dictionaries.  Each candle has:

    time_ms : int       epoch milliseconds UTC (from the CSV timestamp)
    open    : float     open price (IEEE 754 double)
    high    : float     high price
    low     : float     low price
    close   : float     close price

This is the raw parser output — NOT canonical ``Bar`` objects.  The JS
parser returns ``{ time: Date, open, high, low, close }`` with float
prices; the Python equivalent returns epoch-ms instead of a Date object
because Python has no direct equivalent of JS Date that preserves the
exact same interface.  Epoch-ms is the underlying representation of JS
``Date.getTime()``.

Volume is deliberately excluded from the output, matching the JS parser
which ignores ``cols[5]``.

CSV format expected (matching dati/SPY_5m.csv):

    Line 0:  Price,Close,High,Low,Open,Volume     (header row 1)
    Line 1:  Ticker,SPY,SPY,SPY,SPY,SPY            (header row 2)
    Line 2:  Datetime,,,,,                          (header row 3)
    Line 3+: 2026-04-24 09:30:00-04:00,709.835,...  (data rows)

Lines 0–2 are skipped (hardcoded in JS: ``for (let i = 3; ...``).
Column order: datetime, close, high, low, open[, volume].

Parsing rules (from JS):

    - Input is trimmed, then split on ``\\n``.
    - Data rows start at index 3.
    - Each data line is trimmed.
    - Blank lines after trimming are skipped.
    - Lines with fewer than 5 comma-separated columns are skipped.
    - ``close`` (column 1) is parsed with parseFloat; if NaN, skip.
    - Timestamp: column 0 trimmed, first space replaced with ``T``,
      parsed as ISO 8601 with timezone offset → epoch milliseconds.
    - ``high``, ``low``, ``open`` parsed with parseFloat (columns 2–4).
    - Row order is preserved exactly.
    - No sorting, deduplication, or normalization.
    - No tick conversion (prices stay as floats).
"""

from __future__ import annotations

import math
from datetime import datetime


def parse_candles_from_csv(csv_content: str) -> list[dict]:
    """Parse repository 5-minute CSV text into raw candle dicts.

    Matches JS ``parseCandlesFromCSV(csvContent)`` in
    bdrr_strategy_runner.js:89–108 exactly.

    Parameters
    ----------
    csv_content : str
        Complete CSV text.  Must be a string.

    Returns
    -------
    list[dict]
        Each dict has keys: ``time_ms``, ``open``, ``high``, ``low``,
        ``close``.  Values are int (epoch ms) and float (prices).
        Order matches the CSV row order.
    """
    if not isinstance(csv_content, str):
        raise TypeError(
            f"csv_content must be a str, got {type(csv_content).__name__}"
        )

    lines = csv_content.strip().split("\n")
    candles: list[dict] = []

    # JS: for (let i = 3; i < lines.length; i++)
    for i in range(3, len(lines)):
        line = lines[i].strip()
        if not line:
            continue

        cols = line.split(",")
        if len(cols) < 5:
            continue

        # JS: const close = parseFloat(cols[1]); if (isNaN(close)) continue;
        try:
            close = float(cols[1])
        except (ValueError, IndexError):
            continue
        if math.isnan(close):
            continue

        # JS: new Date(cols[0].trim().replace(' ', 'T'))
        # Python: datetime.fromisoformat → epoch ms
        ts_str = cols[0].strip().replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        time_ms = int(dt.timestamp() * 1000)

        # JS: parseFloat for high, low, open — no NaN guard on these
        try:
            high = float(cols[2])
            low = float(cols[3])
            open_ = float(cols[4])
        except (ValueError, IndexError):
            continue

        candles.append({
            "time_ms": time_ms,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        })

    return candles
