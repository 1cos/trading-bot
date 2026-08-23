"""FIRST_RTH_RETEST_CONTACT -> Max Entry Candle evaluator.

Answers exactly one question: "if the first RTH retest of PDH/PDL
happens on a candle that is already a valid Max Entry Candle, can we
recognize it using exactly the same geometry the ORB BDRR pipeline
already uses?"

This uses evaluate_single_candle_rejection_geometry() (the frozen
geometry extracted in a prior task) directly and unmodified — no new
geometry, no duplicated rule. It only:
    1. Reads the contact_result already produced by
       find_first_rth_level_contact().
    2. Propagates WAITING_FOR_RETEST / NOT_RETEST_READY /
       PREMARKET_RETEST_ALREADY_SEEN untouched — no geometry is ever
       evaluated for these statuses. In particular,
       PREMARKET_RETEST_ALREADY_SEEN policy (whether a further RTH
       contact after an already-seen premarket retest should count)
       is deliberately left undecided here, exactly as
       find_first_rth_level_contact() already left it undecided.
    3. When status is CONTACT_FOUND, evaluates ONLY
       contact_result["contact_candle"] — the real, already-located
       first contact candle — against the level using the same
       threshold-resolution pattern find_rejection() already uses
       (config.get(...) falling back to the same frozen constants;
       no new numbers invented).

Does NOT decide what happens after a FAIL: no second candle is
searched, no TWO_CANDLE_ENGULFING_RECOVERY is attempted, no rejection-
window timeout is applied, no second-retest policy is implemented.
That is explicitly out of scope for this task.

Stateless: no state is persisted anywhere. Every call derives its
result purely from the contact_result, direction, level_price,
tick_size, and config passed in.
"""

from __future__ import annotations

from trading_lab.rejection_finder import (
    BODY_RATIO_MAX,
    REJECTION_WICK_RATIO_MIN,
    evaluate_single_candle_rejection_geometry,
)


def evaluate_first_rth_entry_candle(
    contact_result: dict,
    direction: str,
    level_price: float,
    tick_size: float,
    config: dict | None = None,
) -> dict:
    """Evaluate the first RTH contact candle as a Max Entry Candle.

    Parameters
    ----------
    contact_result : dict
        The exact return value of
        first_rth_contact.find_first_rth_level_contact() — one of
        CONTACT_FOUND / WAITING_FOR_RETEST / NOT_RETEST_READY /
        PREMARKET_RETEST_ALREADY_SEEN.
    direction : str
        "LONG" (PDH) or "SHORT" (PDL). Should match the direction
        already used to produce contact_result.
    level_price : float
        The PDH (LONG) or PDL (SHORT) price. Should match the level
        already used to produce contact_result.
    tick_size : float
        Instrument tick size.
    config : dict | None
        Optional engine config carrying the same optional threshold
        overrides find_rejection() already supports:
        "rejection_wick_ratio_min", "body_ratio_max",
        "min_close_beyond_level_ticks",
        "confirmation_wick_penetration_pct_min". Missing/None keys
        fall back to the exact same frozen defaults find_rejection()
        uses — no new threshold values are introduced.

    Returns
    -------
    dict
        One of four shapes, discriminated by "status":

        WAITING_FOR_RETEST / NOT_RETEST_READY /
        PREMARKET_RETEST_ALREADY_SEEN (propagated untouched from
        contact_result, no geometry evaluated):
            {"status": str, "direction": str, "level_price": float}

        ENTRY_CANDLE_FOUND (the contact candle qualifies):
            {"status": "ENTRY_CANDLE_FOUND",
             "entry_type": "SINGLE_CANDLE_REJECTION",
             "direction": str, "level_price": float,
             "entry_candle": dict, "entry_timestamp_ms": int,
             "geometry": dict}

        CONTACT_FOUND_NO_ENTRY (the contact candle does not qualify):
            {"status": "CONTACT_FOUND_NO_ENTRY",
             "direction": str, "level_price": float,
             "contact_candle": dict, "geometry": dict,
             "failed_rules": list[str]}
    """
    base = {"direction": direction, "level_price": level_price}

    status = contact_result.get("status") if isinstance(contact_result, dict) else None

    # ── Propagate non-contact statuses untouched — no geometry ever
    # evaluated for these. PREMARKET_RETEST_ALREADY_SEEN policy stays
    # undecided here, matching find_first_rth_level_contact(). ────────
    if status in ("WAITING_FOR_RETEST", "NOT_RETEST_READY", "PREMARKET_RETEST_ALREADY_SEEN"):
        return {**base, "status": status}

    if status != "CONTACT_FOUND":
        # Defensive: unrecognized/malformed contact_result shape.
        return {**base, "status": "NOT_RETEST_READY"}

    contact_candle = contact_result["contact_candle"]
    contact_timestamp_ms = contact_result["contact_timestamp_ms"]

    # ── Resolve thresholds the exact same way find_rejection() already
    # does — same canonical config keys, same frozen-constant
    # fallbacks. No second copy of the thresholds is invented. ────────
    cfg = config or {}
    wick_min = cfg.get("rejection_wick_ratio_min")
    if wick_min is None:
        wick_min = REJECTION_WICK_RATIO_MIN
    body_max = cfg.get("body_ratio_max")
    if body_max is None:
        body_max = BODY_RATIO_MAX
    min_close_beyond = cfg.get("min_close_beyond_level_ticks")
    wick_pen_min = cfg.get("confirmation_wick_penetration_pct_min")
    if wick_pen_min is None:
        wick_pen_min = 0.20
    else:
        wick_pen_min = float(wick_pen_min)

    geometry_result = evaluate_single_candle_rejection_geometry(
        contact_candle, direction, level_price, tick_size,
        rejection_wick_ratio_min=wick_min,
        body_ratio_max=body_max,
        min_close_beyond_level_ticks=min_close_beyond,
        confirmation_wick_penetration_pct_min=wick_pen_min,
    )

    if geometry_result["qualifies"]:
        return {
            **base,
            "status": "ENTRY_CANDLE_FOUND",
            "entry_type": "SINGLE_CANDLE_REJECTION",
            "entry_candle": contact_candle,
            "entry_timestamp_ms": contact_timestamp_ms,
            "geometry": geometry_result["geometry"],
        }

    return {
        **base,
        "status": "CONTACT_FOUND_NO_ENTRY",
        "contact_candle": contact_candle,
        "geometry": geometry_result["geometry"],
        "failed_rules": geometry_result["failed_rules"],
    }
