"""PDH/PDL eligibility predicate — V1 (approved, deliberately minimal).

Answers exactly one question, statelessly:

    "Right now, given the available candles, is PDH (LONG) / PDL
    (SHORT) eligible to become a candidate structural level?"

This is NOT a level selector. It does not choose which level is
operative, does not build a PDH/PDL BDRR sequence, and does not
generate a signal. It only answers the eligibility predicate above,
so a future task can decide what to do with that answer.

Rule V1 (approved) — no ATR, no percentage, no minimum distance, no
ORB-width multiple, no composite zones. Deliberately simple:

    LONG:  PDH eligible iff
               PDH > ORB_HIGH
           AND a valid LONG break of ORB_HIGH exists
           AND displacement for that break is complete per the
               existing BDRR rules (find_displacement() status "OK")
           AND the ORB structure is not currently invalidated per
               the existing BDRR rules (validate_sequence())

    SHORT: symmetric on ORB_LOW / PDL.

Statelessness / anti-stuck:
    This function persists nothing. Every call recomputes break,
    displacement, and invalidation status fresh from the candles
    passed in. If the ORB structure that justified eligibility is no
    longer valid (validate_sequence() reports INVALIDATED), the very
    next call — with the same or additional candles — returns
    eligible=False. There is no flag to reset, no timeout, and no new
    state machine: eligibility is a pure function of (candles,
    session_context, config, candidate_level_price).

Reuses the existing pipeline stages unmodified:
    orb_builder.build_orb          — Stage 1b (ORB construction)
    break_finder.find_break        — Stage 2
    displacement_finder.find_displacement — Stage 3
    sequence_validator.validate_sequence  — Stage 3b (invalidation)

No new stage is introduced, and none of the above modules are
modified by this file.
"""

from __future__ import annotations

from trading_lab.break_finder import find_break
from trading_lab.displacement_finder import find_displacement
from trading_lab.orb_builder import build_orb
from trading_lab.sequence_validator import validate_sequence


def check_orb_to_level_eligibility(
    candles: list[dict],
    session_context: dict,
    config: dict,
    candidate_level_price: float,
) -> dict:
    """Check whether PDH (LONG) / PDL (SHORT) is eligible right now.

    Parameters
    ----------
    candles : list[dict]
        Session candles (same identity used throughout the pipeline).
    session_context : dict
        Result of build_session_context() — must have status "OK".
    config : dict
        Engine configuration for the ORB side to check. Must contain
        "direction" ("LONG" or "SHORT") plus the other keys required
        by build_orb/find_break/find_displacement/validate_sequence
        (timeframe_minutes, timezone, session_open, orb_start,
        orb_duration_minutes, tick_size, etc.). "level_source" is
        overridden internally to the ORB side matching "direction"
        (ORB_HIGH for LONG, ORB_LOW for SHORT) regardless of what is
        passed in, so callers cannot accidentally mismatch the two.
    candidate_level_price : float
        The PDH (LONG) or PDL (SHORT) price to compare against the
        relevant ORB boundary.

    Returns
    -------
    dict
        {"eligible": bool, "reason": str, ...debug fields}.

        reason is one of:
            "UNSUPPORTED_DIRECTION"       — config["direction"] not
                                             LONG/SHORT
            "ORB_NOT_READY"                — ORB window not yet
                                             complete (see failed_stage)
            "WRONG_GEOMETRY"               — candidate level is not
                                             beyond the ORB boundary on
                                             the correct side
            "NO_ORB_BREAK"                  — no valid ORB break yet
                                             (see failed_stage)
            "DISPLACEMENT_INCOMPLETE"       — break exists but
                                             displacement is not yet
                                             complete (see failed_stage)
            "ORB_STRUCTURE_INVALIDATED"     — break + displacement were
                                             complete, but the ORB
                                             structure has since been
                                             invalidated
            "ORB_BREAK_AND_DISPLACEMENT_COMPLETE" — eligible=True
    """
    direction = config.get("direction")
    if direction not in ("LONG", "SHORT"):
        return {"eligible": False, "reason": "UNSUPPORTED_DIRECTION"}

    # The ORB side being checked is derived from direction, not taken
    # from the caller's config — LONG always checks ORB_HIGH, SHORT
    # always checks ORB_LOW, matching the canonical mapping used
    # elsewhere (e.g. LiveSignalDetector).
    orb_level_source = "ORB_HIGH" if direction == "LONG" else "ORB_LOW"
    orb_config = {**config, "level_source": orb_level_source}

    orb_result = build_orb(candles, session_context, orb_config)
    if orb_result.get("status") != "OK":
        return {
            "eligible": False,
            "reason": "ORB_NOT_READY",
            "failed_stage": orb_result.get("failed_stage"),
        }

    orb_high = orb_result["orb_high"]
    orb_low = orb_result["orb_low"]

    # ── Geometry gate ───────────────────────────────────────────────
    if direction == "LONG":
        geometry_ok = candidate_level_price > orb_high
    else:
        geometry_ok = candidate_level_price < orb_low

    if not geometry_ok:
        return {
            "eligible": False,
            "reason": "WRONG_GEOMETRY",
            "orb_high": orb_high,
            "orb_low": orb_low,
            "candidate_level_price": candidate_level_price,
        }

    # ── Break gate ──────────────────────────────────────────────────
    brk = find_break(candles, orb_result, orb_config)
    if brk.get("status") != "OK":
        return {
            "eligible": False,
            "reason": "NO_ORB_BREAK",
            "failed_stage": brk.get("failed_stage"),
            "orb_high": orb_high,
            "orb_low": orb_low,
        }

    # ── Displacement gate ───────────────────────────────────────────
    disp = find_displacement(candles, orb_result, brk, orb_config)
    if disp.get("status") != "OK":
        return {
            "eligible": False,
            "reason": "DISPLACEMENT_INCOMPLETE",
            "failed_stage": disp.get("failed_stage"),
            "orb_high": orb_high,
            "orb_low": orb_low,
            "break_candle_index": brk.get("break_candle_index"),
        }

    # ── Invalidation gate (anti-stuck: recomputed every call) ───────
    seq = validate_sequence(candles, orb_result, brk, disp, orb_config)
    if seq.get("status") == "INVALIDATED":
        return {
            "eligible": False,
            "reason": "ORB_STRUCTURE_INVALIDATED",
            "orb_high": orb_high,
            "orb_low": orb_low,
            "break_candle_index": brk.get("break_candle_index"),
            "invalidation_index": seq.get("invalidation_index"),
        }

    return {
        "eligible": True,
        "reason": "ORB_BREAK_AND_DISPLACEMENT_COMPLETE",
        "orb_high": orb_high,
        "orb_low": orb_low,
        "candidate_level_price": candidate_level_price,
        "break_candle_index": brk.get("break_candle_index"),
        "displacement_bar_count": disp.get("displacement_bar_count"),
    }
