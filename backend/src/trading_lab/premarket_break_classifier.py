"""Premarket PDH/PDL break-origin classifier — pure predicate.

Answers exactly one question, using only data already available at
boot: "when the bot starts, what do we actually know about the
relationship between price and PDH/PDL during premarket?"

This is NOT a signal, NOT a setup, and NOT wired into the eligibility
predicate, the candidate evaluator, or anything execution-related. It
produces a single classification per level, with no side effects:

    NONE                — price has not crossed the level and does not
                           already sit on the broken side.
    PREMARKET_OBSERVED   — a real candle in premarket_bars closes on the
                           broken side, with an earlier candle (or the
                           first bar) closing on the un-broken side —
                           i.e. an actual crossing is present in the
                           data, with a real break_timestamp_ms.
    PREMARKET_CARRY_IN   — the very first available premarket bar
                           already closes on the broken side. The
                           actual crossing moment is not observable
                           (it happened before the earliest fetched
                           bar, or before premarket data starts at
                           all). break_timestamp_ms is deliberately
                           None — never fabricated.

Direction / level semantics (V1, symmetric):
    LONG  (PDH): "broken" means close > level_price (strict).
                 Equality (close == level_price) is NOT broken.
    SHORT (PDL): "broken" means close < level_price (strict).
                 Equality (close == level_price) is NOT broken.

No retest, no displacement, no synthetic break, no setup_key. This
module only classifies the starting context.
"""

from __future__ import annotations


def classify_premarket_context(
    premarket_bars: list[dict] | None,
    level_price: float,
    direction: str,
    level_source: str,
) -> dict:
    """Classify the premarket relationship between price and a PDH/PDL level.

    Parameters
    ----------
    premarket_bars : list[dict] | None
        Raw premarket candles (same shape as any other candle dict:
        time_ms/open/high/low/close/volume) — e.g.
        SymbolRuntime.premarket_bars. Not required to be pre-sorted;
        this function sorts a local copy by time_ms and never mutates
        the input.
    level_price : float
        The PDH (LONG) or PDL (SHORT) price to classify against.
    direction : str
        "LONG" (checking PDH) or "SHORT" (checking PDL).
    level_source : str
        Passed through unchanged into the output for identification
        (e.g. "PREVIOUS_DAY_HIGH" / "PREVIOUS_DAY_LOW").

    Returns
    -------
    dict
        {
            "level_source": str,
            "direction": str,
            "level_price": float,
            "break_origin": "NONE" | "PREMARKET_OBSERVED" | "PREMARKET_CARRY_IN",
            "break_timestamp_ms": int | None,
        }
        break_timestamp_ms is a real candle timestamp for
        PREMARKET_OBSERVED, and is always None for NONE and
        PREMARKET_CARRY_IN — never fabricated.
    """
    base = {
        "level_source": level_source,
        "direction": direction,
        "level_price": level_price,
    }

    if direction not in ("LONG", "SHORT"):
        return {**base, "break_origin": "NONE", "break_timestamp_ms": None}

    if not premarket_bars:
        return {**base, "break_origin": "NONE", "break_timestamp_ms": None}

    bars = sorted(premarket_bars, key=lambda c: c["time_ms"])

    def _is_broken(close: float) -> bool:
        if direction == "LONG":
            return close > level_price
        else:
            return close < level_price

    # ── Carry-in guard: the first available bar is already on the
    # broken side. Equality does NOT count as broken (PMB7). ──────────
    if _is_broken(bars[0]["close"]):
        return {**base, "break_origin": "PREMARKET_CARRY_IN", "break_timestamp_ms": None}

    # ── Scan the rest for a real, observed crossing. The first bar
    # was confirmed not-broken above, so the first later bar found
    # broken here is genuinely the crossing candle (PMB1/PMB2/PMB8). ──
    for bar in bars[1:]:
        if _is_broken(bar["close"]):
            return {
                **base,
                "break_origin": "PREMARKET_OBSERVED",
                "break_timestamp_ms": bar["time_ms"],
            }

    # ── Never crossed at all (PMB5/PMB6). ───────────────────────────
    return {**base, "break_origin": "NONE", "break_timestamp_ms": None}
