"""First RTH contact detector for an already-RETEST_READY PDH/PDL structure.

Answers exactly one question: "a PDH/PDL structure that already
arrived at RTH open as RETEST_READY (with no premarket retest yet) —
has it made its first real RTH contact with the level?"

This is deliberately NOT find_retest_window(): it does not require (or
accept) a full break/displacement chain, does not apply rejection
geometry, and does not select a Max Entry Candle. It only recognizes
the first candle whose wick reaches the level from the correct side —
the single fact this task exists to establish. What happens after
contact (rejection geometry, entry candle selection) is a later task.

Works uniformly for a structure that reached RETEST_READY via either
PREMARKET_OBSERVED (premarket_observed_structure.py) or
PREMARKET_CARRY_IN (carry_in_separation.py) — this function does not
care which one produced retest_ready=True; it only consumes that
boolean plus premarket_retest_already_seen.

No synthetic break is used or required: contact is decided purely by
comparing rth_candles against level_price.

Policy note: if premarket_retest_already_seen is True, this task does
NOT decide whether a further RTH contact should be considered a valid
(second) retest. That policy is intentionally left for a separate
task. This function returns a neutral, explicit
PREMARKET_RETEST_ALREADY_SEEN status and does not search RTH candles
at all in that case.

Anti-stuck: no state is persisted anywhere. Every call derives its
result purely from the rth_candles list passed in.
"""

from __future__ import annotations


def find_first_rth_level_contact(
    rth_candles: list[dict] | None,
    direction: str,
    level_price: float,
    retest_ready: bool,
    premarket_retest_already_seen: bool = False,
) -> dict:
    """Find the first RTH candle that contacts level_price from the
    correct side, for a structure already known to be RETEST_READY.

    Parameters
    ----------
    rth_candles : list[dict] | None
        RTH candles (time_ms/open/high/low/close/volume) accumulated
        so far this session. Not required pre-sorted; a local sorted
        copy is used and the input is never mutated.
    direction : str
        "LONG" (PDH) or "SHORT" (PDL).
    level_price : float
        The PDH (LONG) or PDL (SHORT) price.
    retest_ready : bool
        The retest_ready value already produced by
        evaluate_observed_premarket_structure() or
        evaluate_carry_in_separation() for this level. This function
        trusts that value as-is — it does not recompute eligibility or
        premarket structure itself.
    premarket_retest_already_seen : bool
        If True, a retest contact already happened during premarket
        (per the same upstream evaluator). This function does NOT
        search for or decide about a further RTH contact in that case
        — see module docstring. Default False.

    Returns
    -------
    dict
        One of four shapes, discriminated by "status":

        CONTACT_FOUND (the first qualifying RTH candle was found):
            {"status": "CONTACT_FOUND", "direction": str,
             "level_price": float, "contact_index": int,
             "contact_timestamp_ms": int, "contact_candle": dict}

        WAITING_FOR_RETEST (ready, no premarket retest, but no RTH
        candle has touched the level yet):
            {"status": "WAITING_FOR_RETEST", "direction": str,
             "level_price": float, "candles_checked": int}

        NOT_RETEST_READY (retest_ready is False — nothing is scanned):
            {"status": "NOT_RETEST_READY", "direction": str,
             "level_price": float}

        PREMARKET_RETEST_ALREADY_SEEN (premarket_retest_already_seen
        is True — nothing is scanned; see module docstring):
            {"status": "PREMARKET_RETEST_ALREADY_SEEN", "direction": str,
             "level_price": float}
    """
    base = {"direction": direction, "level_price": level_price}

    if direction not in ("LONG", "SHORT"):
        return {**base, "status": "NOT_RETEST_READY", "direction": direction}

    if not retest_ready:
        return {**base, "status": "NOT_RETEST_READY"}

    if premarket_retest_already_seen:
        # Deliberately not decided here — see module docstring. No RTH
        # candle is inspected in this branch.
        return {**base, "status": "PREMARKET_RETEST_ALREADY_SEEN"}

    if not rth_candles:
        return {**base, "status": "WAITING_FOR_RETEST", "candles_checked": 0}

    bars = sorted(rth_candles, key=lambda c: c["time_ms"])

    def _is_contact(bar: dict) -> bool:
        if direction == "LONG":
            return bar["low"] <= level_price
        else:
            return bar["high"] >= level_price

    for i, bar in enumerate(bars):
        if _is_contact(bar):
            return {
                **base,
                "status": "CONTACT_FOUND",
                "contact_index": i,
                "contact_timestamp_ms": bar["time_ms"],
                "contact_candle": bar,
            }

    return {**base, "status": "WAITING_FOR_RETEST", "candles_checked": len(bars)}
