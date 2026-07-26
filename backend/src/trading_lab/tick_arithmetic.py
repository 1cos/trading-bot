"""Canonical tick-arithmetic utilities for the BDRR pipeline.

Ported from the authoritative JavaScript functions in:
  - estrategie/bdrr_engine.js       (priceToTicks, decimalsOf, ticksToPoints)
  - estrategie/bdrr_detection_result.js (priceToTicks, decimalsOf, ticksToPointsStr)

These are the boundary conversion functions between external float prices
and internal integer tick representations.  All canonical BDRR arithmetic
is tick-native internally; these utilities exist only at the input/output
boundary.

Functions:

    price_to_ticks(price, tick_size) → int
        External price (float) → integer tick count.
        Matches JS: Math.round(price / tickSize).
        Uses math.floor(x + 0.5) to reproduce JS Math.round exactly
        (round half toward +∞), which differs from Python's built-in
        round() (banker's rounding, half to even).

    ticks_to_points(ticks, tick_size) → float
        Integer tick count → external price (float).
        Matches JS: Number((ticks * tickSize).toFixed(max(decimalsOf(tickSize), 2))).
        Informational output only — not for canonical arithmetic.

    decimals_of(tick_size) → int
        Count decimal places in the string representation of tick_size.
        Matches JS: String(tickSize).indexOf('.') arithmetic.
        Internal helper exposed for parity testing.

Rounding rule:

    JS Math.round uses "round half toward +∞":
        Math.round(0.5)  =  1     (not 0, as Python round would give)
        Math.round(1.5)  =  2
        Math.round(-0.5) =  0     (-0 in JS, 0 as int)
        Math.round(-1.5) = -1

    Python's math.floor(x + 0.5) reproduces this behavior exactly.

IEEE 754 parity:

    Both JS Number and Python float use IEEE 754 binary64.
    The division price / tick_size produces identical results in both
    languages.  Verified on representative inputs including
    100.005/0.01, 750.44/0.01, and boundary cases.

Tick-size input:

    These functions accept tick_size as a Python float, matching the
    JavaScript Number type at the external boundary.  The canonical
    Python contracts store tick_size as a Decimal string; conversion
    between str and float is the caller's responsibility.
"""

from __future__ import annotations

import math


def price_to_ticks(price: float, tick_size: float) -> int:
    """Convert an external decimal price to integer ticks.

    Matches JS: ``Math.round(price / tickSize)`` in bdrr_engine.js:103.

    Parameters
    ----------
    price : float
        External price as an IEEE 754 double.  Must be finite.
    tick_size : float
        Instrument minimum price increment.  Must be finite and positive.

    Returns
    -------
    int
        Signed integer tick count.

    Raises
    ------
    TypeError
        If price or tick_size is not a float/int, or is bool.
    ValueError
        If price is not finite, or tick_size is not positive/finite.
    """
    # ── Validate price (matches bdrr_engine.js:100-102) ─────────────────
    if isinstance(price, bool):
        raise TypeError("price must be a finite number, got bool")
    if not isinstance(price, (int, float)):
        raise TypeError(
            f"price must be a finite number, got {type(price).__name__}"
        )
    price = float(price)
    if not math.isfinite(price):
        raise ValueError("price must be a finite number")

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

    # ── Conversion: Math.round(price / tickSize) ───────────────────────
    # math.floor(x + 0.5) reproduces JS Math.round exactly.
    return int(math.floor(price / tick_size + 0.5))


def decimals_of(tick_size: float) -> int:
    """Count decimal places in the string representation of tick_size.

    Matches JS: ``String(tickSize).indexOf('.')`` arithmetic in
    bdrr_engine.js:106-110.

    Parameters
    ----------
    tick_size : float
        Instrument tick size.

    Returns
    -------
    int
        Number of characters after the decimal point (0 if no decimal).
    """
    s = str(tick_size)
    dot = s.find(".")
    if dot == -1:
        return 0
    return len(s) - dot - 1


def ticks_to_points(ticks: int, tick_size: float) -> float:
    """Convert integer ticks to an external price as a float.

    Matches JS: ``Number((ticks * tickSize).toFixed(Math.max(decimalsOf(tickSize), 2)))``
    in bdrr_engine.js:112-116.

    This is for informational output only — not for canonical tick
    arithmetic.

    Parameters
    ----------
    ticks : int
        Integer tick count.  Bool is rejected.
    tick_size : float
        Instrument tick size.

    Returns
    -------
    float
        Price rounded to max(decimals_of(tick_size), 2) decimal places.
    """
    if isinstance(ticks, bool):
        raise TypeError("ticks must be an int, got bool")
    if not isinstance(ticks, int):
        raise TypeError(
            f"ticks must be an int, got {type(ticks).__name__}"
        )

    if isinstance(tick_size, bool):
        raise TypeError("tick_size must be a number, got bool")
    if not isinstance(tick_size, (int, float)):
        raise TypeError(
            f"tick_size must be a number, got {type(tick_size).__name__}"
        )
    tick_size = float(tick_size)

    decimals = decimals_of(tick_size)
    ndigits = max(decimals, 2)
    value = ticks * tick_size
    return round(value, ndigits)
