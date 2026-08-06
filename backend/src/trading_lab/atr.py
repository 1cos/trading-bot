"""ATR (Average True Range) foundation for the MaxBot pipeline.

Defined in MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §9.

Method: **SMA of True Range** — simple arithmetic mean of the True
Range values over a fixed window.  This is a specific, frozen choice
of the MaxBot project.  Wilder smoothing is NOT used.

Terminology
-----------
- ``atr_series(candles, period)[i]`` **includes** candle ``i`` in the
  window: it is the SMA of TR values ``[i-period+1 .. i]``.
- ``previous_atr(candles, index, period)`` **excludes** candle
  ``index``: it is the SMA of TR values ``[index-period .. index-1]``.
  This is the value the News Candle filter (spec §9.2) must use.

Properties
----------
- Deterministic: no seed, no warm-up, no look-ahead.
- ``None`` means exactly one thing: insufficient history.
- NaN and Infinity are never produced; invalid inputs raise.
- ``true_range`` always returns ``>= 0``.

Input format
------------
Raw candle dicts as produced by ``csv_parser``:
``{"time_ms": int, "open": float, "high": float, "low": float,
"close": float}``.  Integer prices are accepted and promoted to
float.  Booleans are rejected.
"""

from __future__ import annotations

import math


# ── Validation helpers ────────────────────────────────────────────────────────


def _require_finite_number(value: object, name: str) -> float:
    """Require a finite numeric value (int or float, not bool)."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number, got bool")
    if isinstance(value, int):
        return float(value)
    if not isinstance(value, float):
        raise TypeError(
            f"{name} must be a number, got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return value


def _require_non_bool_int(value: object, name: str) -> int:
    """Require a real int, rejecting bool."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int, got {type(value).__name__}"
        )
    return value


def _extract_hl(candle: dict, label: str) -> tuple[float, float]:
    """Extract and validate high/low from a candle dict.

    Raises KeyError if fields are missing, TypeError/ValueError if
    values are invalid, and ValueError if high < low.
    """
    try:
        raw_high = candle["high"]
        raw_low = candle["low"]
    except KeyError as exc:
        raise KeyError(
            f"{label} missing required field: {exc}"
        ) from None

    high = _require_finite_number(raw_high, f"{label}['high']")
    low = _require_finite_number(raw_low, f"{label}['low']")

    if high < low:
        raise ValueError(
            f"{label} has high ({high}) < low ({low})"
        )
    return high, low


def _extract_close(candle: dict, label: str) -> float:
    """Extract and validate close from a candle dict."""
    try:
        raw = candle["close"]
    except KeyError as exc:
        raise KeyError(
            f"{label} missing required field: {exc}"
        ) from None
    return _require_finite_number(raw, f"{label}['close']")


# ── True Range ────────────────────────────────────────────────────────────────


def true_range(
    candle: dict,
    previous_close: float | None = None,
) -> float:
    """Compute True Range for a single candle.

    Parameters
    ----------
    candle : dict
        Raw candle dict with at least ``high`` and ``low`` (float).
    previous_close : float or None
        Close price of the immediately preceding candle.  ``None``
        when the candle is the very first observation in the dataset
        (not merely the first in a sliced segment — see module
        docstring).  When ``None``, TR = ``high - low``.

    Returns
    -------
    float
        True Range, always ``>= 0``.

    Raises
    ------
    KeyError
        If ``candle`` is missing ``high`` or ``low``.
    TypeError
        If prices are non-numeric or boolean.
    ValueError
        If prices are NaN/Inf or ``high < low``.
    """
    high, low = _extract_hl(candle, "candle")

    if previous_close is None:
        return high - low

    pc = _require_finite_number(previous_close, "previous_close")
    return max(high, pc) - min(low, pc)


# ── ATR Series ────────────────────────────────────────────────────────────────


def atr_series(
    candles: list[dict],
    period: int = 14,
    initial_previous_close: float | None = None,
) -> list[float | None]:
    """Compute the ATR (SMA of True Range) for every candle.

    ``atr_series[i]`` is the simple arithmetic mean of the True Range
    values for candles ``[i - period + 1 .. i]``.  It **includes**
    candle ``i`` in the window.

    Parameters
    ----------
    candles : list[dict]
        Chronologically ordered raw candle dicts.
    period : int
        ATR lookback window.  Must be ``>= 1``.  Default ``14``.
    initial_previous_close : float or None
        Close of the candle immediately before ``candles[0]``.
        Pass this when ``candles`` is a segment cut from a larger
        dataset, so the gap between the preceding session and
        ``candles[0]`` is captured in TR(0).  ``None`` when
        ``candles[0]`` is truly the first observation.

    Returns
    -------
    list[float | None]
        Length equals ``len(candles)``.  ``None`` for indices where
        the window cannot be filled (``i < period - 1``).

    Complexity
    ----------
    O(n) time via rolling sum.
    """
    period = _require_non_bool_int(period, "period")
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    n = len(candles)
    if n == 0:
        return []

    if initial_previous_close is not None:
        initial_previous_close = _require_finite_number(
            initial_previous_close, "initial_previous_close"
        )

    # ── Compute all TR values ─────────────────────────────────────────
    tr_values: list[float] = []
    for i in range(n):
        if i == 0:
            pc = initial_previous_close
        else:
            pc = _extract_close(candles[i - 1], f"candles[{i - 1}]")
        tr_values.append(true_range(candles[i], pc))

    # ── Rolling SMA via running sum ───────────────────────────────────
    result: list[float | None] = [None] * n
    running_sum = 0.0

    for i in range(n):
        running_sum += tr_values[i]
        if i >= period:
            running_sum -= tr_values[i - period]
        if i >= period - 1:
            result[i] = running_sum / period

    return result


# ── Previous ATR ──────────────────────────────────────────────────────────────


def previous_atr(
    candles: list[dict],
    index: int,
    period: int = 14,
    initial_previous_close: float | None = None,
    *,
    _atr_cache: list[float | None] | None = None,
) -> float | None:
    """ATR(period) of the candles BEFORE ``index``, excluding it.

    This is the value the News Candle filter (spec §9.2) uses:
    the candle being evaluated is never included in its own ATR.

    Equivalent to ``atr_series(candles, period)[index - 1]`` when
    ``index >= 1``.

    Parameters
    ----------
    candles : list[dict]
        Chronologically ordered raw candle dicts.
    index : int
        Index of the candle being evaluated.  Must be ``>= 0``
        and ``< len(candles)``.
    period : int
        ATR lookback window.  Default ``14``.
    initial_previous_close : float or None
        See ``atr_series``.
    _atr_cache : list or None
        Pre-computed ``atr_series`` result.  When provided, the
        function is O(1) — a simple lookup.  When ``None``, the
        full series is computed internally (O(n)).  Callers that
        evaluate many candles in sequence should pre-compute the
        series once and pass it here to avoid O(n²).

    Returns
    -------
    float or None
        ``None`` when ``index < period`` (insufficient prior
        candles to fill the window).
    """
    index = _require_non_bool_int(index, "index")
    period = _require_non_bool_int(period, "period")
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if index < 0:
        raise ValueError(f"index must be >= 0, got {index}")
    if index >= len(candles):
        raise ValueError(
            f"index must be < len(candles) ({len(candles)}), "
            f"got {index}"
        )

    if index == 0:
        return None

    if _atr_cache is not None:
        return _atr_cache[index - 1]

    series = atr_series(candles, period, initial_previous_close)
    return series[index - 1]
