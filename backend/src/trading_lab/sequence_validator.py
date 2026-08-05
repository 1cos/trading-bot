"""BDRR Sequence Validator — inserted between Stage 3 and Stage 4.

Determines whether the LONG/SHORT sequence remains alive after displacement
by checking for consecutive closes back inside the ORB band.

A sequence is ACTIVE from displacement until invalidated.
A sequence does NOT expire because of elapsed time or candle count.

Invalidation rule:
    If ``consecutive_orb_closes`` consecutive candles close back inside
    the ORB band (between ORB_L and ORB_H inclusive for LONG, same for
    SHORT), the sequence is invalidated at the last of those candles.

    A close beyond the ORB on the wrong side (below ORB_L for LONG,
    above ORB_H for SHORT) also counts toward the consecutive counter
    because the price has failed to maintain the broken boundary.

After invalidation:
    - The previous break cannot generate additional retests.
    - The previous break cannot generate confirmations.
    - Detector returns to IDLE.
    - Only a completely new Break → Displacement may open a sequence.

Config parameter:
    ``consecutive_orb_closes`` (int, default 2): how many consecutive
    closes not beyond the broken ORB boundary trigger invalidation.

Output:
    status: "OK" (sequence remains active) or "INVALIDATED"
    max_valid_index: last candle index that may contain a retest/confirmation
    invalidation_index: bar index where invalidation occurred (or None)
    invalidation_reason: string (or None)
    consecutive_inside_closes: list of (index, close_price) tuples
        for the consecutive closes that triggered invalidation
"""

from __future__ import annotations


DEFAULT_CONSECUTIVE_ORB_CLOSES = 2


def validate_sequence(
    candles: list[dict],
    orb: dict,
    break_result: dict,
    displacement_result: dict,
    config: dict,
) -> dict:
    """Check if the BDRR sequence remains alive after displacement.

    Parameters
    ----------
    candles : the session candle list (same identity used across pipeline)
    orb : ORB builder output (must be status OK)
    break_result : break finder output (must be status OK)
    displacement_result : displacement finder output (must be status OK)
    config : engine config dict

    Returns
    -------
    dict with keys: status, max_valid_index, invalidation_index,
    invalidation_reason, consecutive_inside_closes
    """
    if not isinstance(candles, list):
        raise TypeError("candles must be a list")

    # Failed upstream → pass through
    for name, result in [("orb", orb), ("break", break_result),
                         ("displacement", displacement_result)]:
        if not isinstance(result, dict) or result.get("status") != "OK":
            fs = result.get("failed_stage", f"{name}_FAILED") if isinstance(result, dict) else f"{name}_FAILED"
            return {
                "status": "FAILED",
                "failed_stage": fs,
                "reason": f"upstream {name} result is not OK",
            }

    direction = config.get("direction", "LONG")
    threshold = config.get("consecutive_orb_closes", DEFAULT_CONSECUTIVE_ORB_CLOSES)

    # ── ORB-specific guard ──────────────────────────────────────────────
    # The consecutive-closes-inside-ORB-band rule is meaningful ONLY for
    # ORB levels, where a defined zone (orb_high → orb_low) exists.
    # For non-ORB levels this check is not applicable and is skipped.
    level_source = config.get("level_source", "ORB_HIGH")
    if level_source not in ("ORB_HIGH", "ORB_LOW"):
        return {
            "status": "NOT_APPLICABLE",
            "reason": (
                f"ORB band invalidation check is not applicable for "
                f'level_source "{level_source}". '
                f"Only ORB_HIGH and ORB_LOW use this validator."
            ),
            "max_valid_index": len(candles) - 1,
            "invalidation_index": None,
            "invalidation_reason": None,
            "consecutive_inside_closes": [],
        }

    orb_high = orb["orb_high"]
    orb_low = orb["orb_low"]

    # Scan starts at first retest contact (first bar after displacement
    # whose wick touches the level)
    first_retest = displacement_result["first_retest_contact_index"]

    consecutive = 0
    consecutive_bars: list[tuple[int, float]] = []

    for i in range(first_retest, len(candles)):
        c = candles[i]
        close = c["close"]

        if direction == "LONG":
            # LONG: close must be above ORB High to maintain the break.
            # Any close <= ORB High counts as "back inside or below."
            inside = close <= orb_high
        else:
            # SHORT: close must be below ORB Low to maintain the break.
            # Any close >= ORB Low counts as "back inside or above."
            inside = close >= orb_low

        if inside:
            consecutive += 1
            consecutive_bars.append((i, close))

            if consecutive >= threshold:
                return {
                    "status": "INVALIDATED",
                    "max_valid_index": i - 1,
                    "invalidation_index": i,
                    "invalidation_reason": (
                        f"{threshold} consecutive close(s) back inside ORB "
                        f"(bars {consecutive_bars[0][0]}–{i})"
                    ),
                    "consecutive_inside_closes": list(consecutive_bars),
                    "threshold": threshold,
                }
        else:
            consecutive = 0
            consecutive_bars = []

    # No invalidation — sequence remains active to session end
    return {
        "status": "OK",
        "max_valid_index": len(candles) - 1,
        "invalidation_index": None,
        "invalidation_reason": None,
        "consecutive_inside_closes": [],
        "threshold": threshold,
    }
