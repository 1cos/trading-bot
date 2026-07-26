"""Canonical raw-candle-to-Bar adapter for the BDRR pipeline.

Ported from ``rawCandleToCanonicalBar`` in
estrategie/bdrr_strategy_runner.js (lines 133–142).

Converts one raw candle dict (as returned by ``parse_candles_from_csv``)
into a canonical immutable ``Bar`` contract object, using
``price_to_ticks`` for the external-float-to-tick conversion.

Comparison of JavaScript converters:

    rawCandleToCanonicalBar (bdrr_strategy_runner.js:133)
        Input:  raw candle {time: Date, open, high, low, close}
        Output: plain {bar_utc_ms, open, high, low, close} (no volume)
        Used:   strategy runner post-confirmation bars (line 333)

    buildBar (bdrr_detection_result.js:206)
        Input:  raw candle {time: Date, open, high, low, close, volume?}
        Output: frozen {bar_utc_ms, open, high, low, close, volume}
        Used:   DetectionResult adapter (6 call sites)

Both produce the same canonical OHLC tick values via ``priceToTicks``.
The differences are cosmetic: volume inclusion and Object.freeze.

This Python function follows ``rawCandleToCanonicalBar`` (the adapter for
raw CSV candles) but returns a canonical ``Bar`` (which includes
``volume=None`` since the Python CSV parser omits volume).

Input shape (from ``parse_candles_from_csv``):

    {
        "time_ms": int,    epoch milliseconds UTC
        "open":    float,
        "high":    float,
        "low":     float,
        "close":   float,
    }

Output: immutable ``Bar`` with ``PriceTicks`` OHLC fields.
"""

from __future__ import annotations

import math

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.tick_arithmetic import price_to_ticks


def raw_candle_to_canonical_bar(candle: dict, tick_size: float) -> Bar:
    """Convert one raw candle dict to a canonical Bar.

    Matches JS ``rawCandleToCanonicalBar(candle, tickSize)`` in
    bdrr_strategy_runner.js:133–142.

    Parameters
    ----------
    candle : dict
        Raw candle with keys: ``time_ms``, ``open``, ``high``, ``low``,
        ``close``.  As returned by ``parse_candles_from_csv``.
    tick_size : float
        Instrument minimum price increment (e.g. 0.01).

    Returns
    -------
    Bar
        Immutable canonical Bar with PriceTicks OHLC fields and
        volume=None.

    Raises
    ------
    TypeError
        If candle is not a dict, tick_size is not numeric, or OHLC
        values are not numeric.
    KeyError
        If required candle fields are missing.
    ValueError
        If tick_size is not positive/finite, or OHLC values are not
        finite.
    """
    if not isinstance(candle, dict):
        raise TypeError(
            f"candle must be a dict, got {type(candle).__name__}"
        )

    # ── Validate tick_size ──────────────────────────────────────────────
    if isinstance(tick_size, bool):
        raise TypeError("tick_size must be a positive finite number, got bool")
    if not isinstance(tick_size, (int, float)):
        raise TypeError(
            f"tick_size must be a positive finite number, "
            f"got {type(tick_size).__name__}"
        )
    tick_size = float(tick_size)
    if not math.isfinite(tick_size) or tick_size <= 0:
        raise ValueError("tick_size must be a positive finite number")

    # ── Timestamp ──────────────────────────────────────────────────────
    # JS: candle.time instanceof Date ? candle.time.getTime() : candle.time
    # Python: time_ms is already epoch ms (int)
    bar_utc_ms = candle["time_ms"]

    # ── OHLC → PriceTicks ──────────────────────────────────────────────
    # price_to_ticks handles validation (finite, non-bool, etc.)
    tick_size_str = str(tick_size)

    open_pt = PriceTicks(
        ticks=price_to_ticks(candle["open"], tick_size),
        tick_size=tick_size_str,
    )
    high_pt = PriceTicks(
        ticks=price_to_ticks(candle["high"], tick_size),
        tick_size=tick_size_str,
    )
    low_pt = PriceTicks(
        ticks=price_to_ticks(candle["low"], tick_size),
        tick_size=tick_size_str,
    )
    close_pt = PriceTicks(
        ticks=price_to_ticks(candle["close"], tick_size),
        tick_size=tick_size_str,
    )

    # ── Construct canonical Bar ────────────────────────────────────────
    # Volume is None: the Python CSV parser intentionally omits volume,
    # matching rawCandleToCanonicalBar which also omits it.
    return Bar(
        bar_utc_ms=bar_utc_ms,
        open=open_pt,
        high=high_pt,
        low=low_pt,
        close=close_pt,
        volume=None,
    )
