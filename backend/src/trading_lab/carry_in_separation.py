"""PREMARKET_CARRY_IN visible-separation predicate — pure, stateless.

Answers exactly one question for a level already classified as
PREMARKET_CARRY_IN (see premarket_break_classifier.py): "do the
premarket bars we actually have show enough real separation from
PDH/PDL to consider the structure RETEST_READY?"

This does NOT invent:
    - a break candle
    - a break timestamp
    - a break_result
    - displacement that happened before the data we have

The very first available bar is never called a "break candle" — it is
only `first_observed_bar`. break_origin stays "PREMARKET_CARRY_IN" and
break_timestamp_ms stays None throughout; a real, honest break was
never observed, so none is fabricated here.

Rule V1 (approved, reusing the already-approved displacement rule
verbatim — no ATR, no percentage, no new minimum distance, no
ORB-width multiple):

    A "separation bar" uses exactly the same geometric criterion
    find_displacement() uses for a genuine displacement bar:
        LONG  (PDH): bar.low  strictly >  level_price
        SHORT (PDL): bar.high strictly <  level_price
    A "contact" bar is exactly find_displacement()'s contact criterion:
        LONG  (PDH): bar.low  <= level_price
        SHORT (PDL): bar.high >= level_price

    RETEST_READY only if at least `min_displacement_bars` (default 3,
    the same frozen BDRR invariant) consecutive separation bars exist
    in the visible premarket data, counted from first_observed_bar
    (inclusive) up to (but not including) the first contact bar, if
    any. If fewer bars are available before any contact — or fewer
    total visible bars than the threshold, when no contact has
    happened yet — the structure is not yet RETEST_READY.

Invalidation reuses sequence_validator's existing line-level semantics
(level_invalidation_closes, same default of 2) verbatim, applied from
the first observed contact index onward: LONG wrong side is
close < level_price (strict), SHORT wrong side is close > level_price
(strict). No new invalidation criterion is introduced.

Statelessness / anti-stuck: this function persists nothing. Every call
recomputes everything from the premarket_bars passed in. Calling it
again on the exact same pre-invalidation bars after having already
seen an invalidated result on a longer bar list still returns
retest_ready=True — there is no flag, cache, or other state carried
between calls.
"""

from __future__ import annotations

from trading_lab.sequence_validator import DEFAULT_LEVEL_INVALIDATION_CLOSES

DEFAULT_MIN_DISPLACEMENT_BARS = 3  # same frozen BDRR invariant used by find_displacement()


def evaluate_carry_in_separation(
    premarket_bars: list[dict] | None,
    direction: str,
    level_price: float,
    min_displacement_bars: int = DEFAULT_MIN_DISPLACEMENT_BARS,
    level_invalidation_closes: int = DEFAULT_LEVEL_INVALIDATION_CLOSES,
) -> dict:
    """Evaluate whether a PREMARKET_CARRY_IN level is RETEST_READY.

    Parameters
    ----------
    premarket_bars : list[dict] | None
        Raw premarket candles (time_ms/open/high/low/close/volume) —
        e.g. SymbolRuntime.premarket_bars. Not required pre-sorted;
        this function sorts a local copy and never mutates the input.
    direction : str
        "LONG" (checking PDH) or "SHORT" (checking PDL).
    level_price : float
        The PDH (LONG) or PDL (SHORT) price.
    min_displacement_bars : int
        Same frozen BDRR invariant as find_displacement() (default 3).
    level_invalidation_closes : int
        Same threshold as sequence_validator's line-level mode
        (default 2, DEFAULT_LEVEL_INVALIDATION_CLOSES).

    Returns
    -------
    dict
        {
            "retest_ready": bool,
            "break_origin": "PREMARKET_CARRY_IN",
            "break_timestamp_ms": None,   # always — never fabricated
            "direction": str,
            "level_price": float,
            "first_observed_bar_time_ms": int | None,
            "visible_displacement_bar_count": int,
            "reason": str,   # present when retest_ready is False
        }
    """
    base = {
        "break_origin": "PREMARKET_CARRY_IN",
        "break_timestamp_ms": None,
        "direction": direction,
        "level_price": level_price,
    }

    if direction not in ("LONG", "SHORT"):
        return {**base, "retest_ready": False, "reason": "UNSUPPORTED_DIRECTION",
                "first_observed_bar_time_ms": None, "visible_displacement_bar_count": 0}

    if not premarket_bars:
        return {**base, "retest_ready": False, "reason": "NO_PREMARKET_DATA",
                "first_observed_bar_time_ms": None, "visible_displacement_bar_count": 0}

    bars = sorted(premarket_bars, key=lambda c: c["time_ms"])
    first_observed_bar = bars[0]  # NOT a break candle — see module docstring

    def _is_carry_in_close(close: float) -> bool:
        return close > level_price if direction == "LONG" else close < level_price

    if not _is_carry_in_close(first_observed_bar["close"]):
        # Defensive: this predicate is only meaningful for a level the
        # classifier already found to be PREMARKET_CARRY_IN.
        return {
            **base, "retest_ready": False, "reason": "NOT_CARRY_IN",
            "first_observed_bar_time_ms": first_observed_bar["time_ms"],
            "visible_displacement_bar_count": 0,
        }

    def _is_contact(bar: dict) -> bool:
        if direction == "LONG":
            return bar["low"] <= level_price
        else:
            return bar["high"] >= level_price

    first_contact_index = None
    for i, bar in enumerate(bars):
        if _is_contact(bar):
            first_contact_index = i
            break

    visible_bar_count = first_contact_index if first_contact_index is not None else len(bars)

    if visible_bar_count < min_displacement_bars:
        return {
            **base,
            "retest_ready": False,
            "reason": "VISIBLE_DISPLACEMENT_INCOMPLETE",
            "first_observed_bar_time_ms": first_observed_bar["time_ms"],
            "visible_displacement_bar_count": visible_bar_count,
        }

    # ── Invalidation check (line-level semantics, reused verbatim) ──────
    # Only meaningful once a contact has actually been observed — before
    # that, price hasn't touched back at all, so there is nothing to
    # invalidate yet (mirrors sequence_validator._validate_line_level(),
    # which only scans from first_retest_contact_index onward).
    if first_contact_index is not None:
        def _wrong_side(close: float) -> bool:
            return close < level_price if direction == "LONG" else close > level_price

        consecutive = 0
        for i in range(first_contact_index, len(bars)):
            close = bars[i]["close"]
            if _wrong_side(close):
                consecutive += 1
                if consecutive >= level_invalidation_closes:
                    return {
                        **base,
                        "retest_ready": False,
                        "reason": "STRUCTURE_INVALIDATED",
                        "first_observed_bar_time_ms": first_observed_bar["time_ms"],
                        "visible_displacement_bar_count": visible_bar_count,
                        "invalidation_index": i,
                    }
            else:
                consecutive = 0

    return {
        **base,
        "retest_ready": True,
        "first_observed_bar_time_ms": first_observed_bar["time_ms"],
        "visible_displacement_bar_count": visible_bar_count,
    }
