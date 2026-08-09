"""BDRR Sequence Validator — inserted between Stage 3 and Stage 4.

Determines whether a LONG/SHORT sequence remains alive after displacement.

Two invalidation modes:

1. **ORB band invalidation** (ORB_HIGH, ORB_LOW):
   If ``consecutive_orb_closes`` consecutive candles close back inside
   the ORB band (between ORB_L and ORB_H inclusive for LONG, same for
   SHORT), the sequence is invalidated at the last of those candles.
   A close beyond the ORB on the wrong side (below ORB_L for LONG,
   above ORB_H for SHORT) also counts toward the consecutive counter
   because the price has failed to maintain the broken boundary.

2. **Line-level invalidation** (PREVIOUS_DAY_HIGH, PREVIOUS_DAY_LOW,
   and any future line-type level source):
   If ``level_invalidation_closes`` consecutive candles close on the
   wrong side of ``level_price``, the sequence is invalidated.
   LONG: close < level_price (strict).
   SHORT: close > level_price (strict).
   A close exactly at level_price does NOT count as wrong-side.

After invalidation (either mode):
    - The previous break cannot generate additional retests.
    - The previous break cannot generate confirmations.
    - Detector returns to IDLE.
    - Only a completely new Break → Displacement may open a sequence.

Config parameters:
    ``consecutive_orb_closes`` (int, default 2): ORB band mode.
    ``level_invalidation_closes`` (int, default 2): line-level mode.

Output:
    status: "OK" (active) | "INVALIDATED" | "FAILED"
    max_valid_index: last candle index that may contain a retest
    invalidation_index: bar index where invalidation occurred (or None)
    invalidation_reason: string (or None)
    consecutive_inside_closes: list of (index, close_price) tuples
    threshold: number of closes required
    level_source: the source that was validated
    invalidation_level: the price or band used for the check
"""

from __future__ import annotations


DEFAULT_CONSECUTIVE_ORB_CLOSES = 2
DEFAULT_LEVEL_INVALIDATION_CLOSES = 2

_ORB_SOURCES = frozenset({"ORB_HIGH", "ORB_LOW"})
_LINE_SOURCES = frozenset({"PREVIOUS_DAY_HIGH", "PREVIOUS_DAY_LOW"})
_ALL_SUPPORTED = _ORB_SOURCES | _LINE_SOURCES


def _validate_threshold(value: object, name: str) -> int:
    """Validate an integer threshold >= 1, rejecting bool."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int, got {type(value).__name__}"
        )
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


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
    orb : level builder output (must be status OK)
    break_result : break finder output (must be status OK)
    displacement_result : displacement finder output (must be status OK)
    config : engine config dict

    Returns
    -------
    dict with keys: status, max_valid_index, invalidation_index,
    invalidation_reason, consecutive_inside_closes, threshold,
    level_source, invalidation_level
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
    level_source = config.get("level_source", "ORB_HIGH")

    # ── Unsupported source ──────────────────────────────────────────────
    if level_source not in _ALL_SUPPORTED:
        return {
            "status": "NOT_APPLICABLE",
            "reason": (
                f"Sequence invalidation is not yet implemented for "
                f'level_source "{level_source}".'
            ),
            "max_valid_index": len(candles) - 1,
            "invalidation_index": None,
            "invalidation_reason": None,
            "consecutive_inside_closes": [],
            "level_source": level_source,
        }

    # ── Dispatch to the appropriate mode ────────────────────────────────
    if level_source in _ORB_SOURCES:
        return _validate_orb_band(
            candles, orb, displacement_result, config,
            direction, level_source,
        )
    else:
        return _validate_line_level(
            candles, orb, displacement_result, config,
            direction, level_source,
        )


# ── ORB band invalidation ────────────────────────────────────────────────────


def _validate_orb_band(
    candles: list[dict],
    orb: dict,
    displacement_result: dict,
    config: dict,
    direction: str,
    level_source: str,
) -> dict:
    """ORB-specific: invalidation by consecutive closes back inside
    the ORB band [orb_low, orb_high]."""

    threshold = config.get("consecutive_orb_closes", DEFAULT_CONSECUTIVE_ORB_CLOSES)
    threshold = _validate_threshold(threshold, "consecutive_orb_closes")

    orb_high = orb["orb_high"]
    orb_low = orb["orb_low"]

    first_retest = displacement_result["first_retest_contact_index"]

    consecutive = 0
    consecutive_bars: list[tuple[int, float]] = []

    for i in range(first_retest, len(candles)):
        c = candles[i]
        close = c["close"]

        if direction == "LONG":
            inside = close <= orb_high
        else:
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
                    "level_source": level_source,
                    "invalidation_level": {"orb_high": orb_high, "orb_low": orb_low},
                }
        else:
            consecutive = 0
            consecutive_bars = []

    return {
        "status": "OK",
        "max_valid_index": len(candles) - 1,
        "invalidation_index": None,
        "invalidation_reason": None,
        "consecutive_inside_closes": [],
        "threshold": threshold,
        "level_source": level_source,
        "invalidation_level": {"orb_high": orb_high, "orb_low": orb_low},
    }


# ── Line-level invalidation ──────────────────────────────────────────────────


def _validate_line_level(
    candles: list[dict],
    orb: dict,
    displacement_result: dict,
    config: dict,
    direction: str,
    level_source: str,
) -> dict:
    """Line-level: invalidation by consecutive closes on the wrong
    side of level_price.

    LONG: wrong side = close < level_price (strict <).
    SHORT: wrong side = close > level_price (strict >).
    Close exactly at level_price does NOT count.
    """

    threshold = config.get(
        "level_invalidation_closes", DEFAULT_LEVEL_INVALIDATION_CLOSES,
    )
    threshold = _validate_threshold(threshold, "level_invalidation_closes")

    level_price = orb["level_price"]

    first_retest = displacement_result["first_retest_contact_index"]

    consecutive = 0
    consecutive_bars: list[tuple[int, float]] = []

    for i in range(first_retest, len(candles)):
        c = candles[i]
        close = c["close"]

        if direction == "LONG":
            wrong_side = close < level_price
        else:
            wrong_side = close > level_price

        if wrong_side:
            consecutive += 1
            consecutive_bars.append((i, close))

            if consecutive >= threshold:
                return {
                    "status": "INVALIDATED",
                    "max_valid_index": i - 1,
                    "invalidation_index": i,
                    "invalidation_reason": (
                        f"{threshold} consecutive close(s) on wrong side of "
                        f"{level_source} {level_price} "
                        f"(bars {consecutive_bars[0][0]}–{i})"
                    ),
                    "consecutive_inside_closes": list(consecutive_bars),
                    "threshold": threshold,
                    "level_source": level_source,
                    "invalidation_level": level_price,
                }
        else:
            consecutive = 0
            consecutive_bars = []

    return {
        "status": "OK",
        "max_valid_index": len(candles) - 1,
        "invalidation_index": None,
        "invalidation_reason": None,
        "consecutive_inside_closes": [],
        "threshold": threshold,
        "level_source": level_source,
        "invalidation_level": level_price,
    }
