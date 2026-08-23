"""PREMARKET_OBSERVED -> RETEST_READY evaluator — pure, no synthetic data.

For a level already classified PREMARKET_OBSERVED (see
premarket_break_classifier.py), this uses the REAL break candle
already located by the classifier — via its real break_timestamp_ms —
to build a genuine BDRR structure on the premarket bars:

    real BREAK candle (located by break_timestamp_ms)
        -> find_displacement()   [Stage 3, unmodified, called for real]
        -> validate_sequence()   [Stage 3b, unmodified, called for real]
        -> RETEST_READY (or not, with a real reason)

This does NOT duplicate find_displacement()'s or validate_sequence()'s
geometry — both are called directly, unmodified. This module only
assembles the small "envelope" dicts they require (see
_build_envelopes below) from real data: a real break candle at a real
index, and a real preceding candle used purely as a technical anchor
for the existing defensive cross-checks those functions already
perform. That anchor candle is NEVER claimed to be a real ORB — it is
documented explicitly as a compatibility envelope only.

No timestamp, candle, or displacement is ever fabricated. If
break_timestamp_ms does not correspond to any real candle in
premarket_bars, this fails cleanly (no crash, retest_ready=False) —
see evaluate_observed_premarket_structure()'s BREAK_TIMESTAMP_NOT_FOUND
path.

Displacement being "complete" and a retest already having happened are
NOT the same thing, and this module is careful not to conflate them.
find_displacement() only returns status "OK" when it has already
located a first retest contact within the candles it was given. If
price simply stays beyond the level for the whole visible premarket
window (a real, valid displacement with no contact yet —
find_displacement() reports this as failed_stage "RETEST_NOT_FOUND"),
that is NOT treated as incomplete: if the reported bar count already
meets min_displacement_bars, the structure is genuinely, validly built
and simply hasn't been retested yet. That case returns
retest_ready=True with premarket_retest_already_seen=False — the
first touch of the level, whenever it happens (including during RTH),
is the retest to watch for. Only a real shortfall in separation bars
(a premature contact, or too few bars before one) is reported as
DISPLACEMENT_INCOMPLETE.

This module deliberately does NOT decide what happens if the retest
already occurred during premarket (i.e. whether a fresh RTH retest is
still required). It only reports the fact via
premarket_retest_already_seen — the policy decision is left for a
later task.
"""

from __future__ import annotations

from datetime import datetime, timezone as _dt_timezone
from zoneinfo import ZoneInfo

from trading_lab.displacement_finder import find_displacement
from trading_lab.sequence_validator import DEFAULT_LEVEL_INVALIDATION_CLOSES, validate_sequence
from trading_lab.tick_arithmetic import price_to_ticks, ticks_to_points


def _date_string(time_ms: int, tz_name: str) -> str:
    """Local calendar date (YYYY-MM-DD) for a candle timestamp."""
    dt = datetime.fromtimestamp(time_ms / 1000, tz=_dt_timezone.utc)
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def _build_envelopes(
    bars: list[dict],
    break_candle_index: int,
    level_price: float,
    level_price_ticks: int,
    level_source: str,
    tick_size: float,
    market_timezone: str,
) -> tuple[dict, dict]:
    """Build the small orb/break_result envelope dicts find_displacement()
    and validate_sequence() require — from real data only.

    orb_candle_index / orb_candle point at the real candle immediately
    preceding the real break candle. This is used ONLY so the existing
    defensive cross-checks inside find_displacement()/validate_sequence()
    (which verify candles[orb_candle_index] matches orb["orb_candle"])
    pass — it is a compatibility envelope, not a claim that this candle
    represents a real ORB window. level_price/level_price_ticks are the
    real PDH/PDL; break_candle_index/break_candle are the real,
    classifier-located break candle.
    """
    anchor_index = break_candle_index - 1
    anchor_candle = bars[anchor_index]
    break_candle = bars[break_candle_index]
    date_str = _date_string(break_candle["time_ms"], market_timezone)

    orb_envelope = {
        "status": "OK",
        "date": date_str,
        "orb_candle_index": anchor_index,
        "orb_candle": anchor_candle,
        "level_price": level_price,
        "level_price_ticks": level_price_ticks,
        "level_source": level_source,
    }

    break_close_ticks = price_to_ticks(break_candle["close"], tick_size)
    if level_source == "PREVIOUS_DAY_HIGH":
        distance_ticks = break_close_ticks - level_price_ticks
    else:
        distance_ticks = level_price_ticks - break_close_ticks

    break_result_envelope = {
        "status": "OK",
        "date": date_str,
        "break_candle_index": break_candle_index,
        "break_candle": break_candle,
        "break_timestamp": break_candle["time_ms"],
        "directional_break_distance": {
            "points": ticks_to_points(distance_ticks, tick_size),
            "ticks": distance_ticks,
        },
    }

    return orb_envelope, break_result_envelope


def evaluate_observed_premarket_structure(
    premarket_bars: list[dict] | None,
    direction: str,
    level_price: float,
    tick_size: float,
    break_timestamp_ms: int,
    level_source: str | None = None,
    market_timezone: str = "America/New_York",
    min_displacement_bars: int = 3,
    level_invalidation_closes: int = DEFAULT_LEVEL_INVALIDATION_CLOSES,
) -> dict:
    """Evaluate whether a PREMARKET_OBSERVED level is RETEST_READY.

    Parameters
    ----------
    premarket_bars : list[dict] | None
        Raw premarket candles (time_ms/open/high/low/close/volume).
    direction : str
        "LONG" (PDH) or "SHORT" (PDL).
    level_price : float
        The PDH (LONG) or PDL (SHORT) price.
    tick_size : float
        Instrument tick size.
    break_timestamp_ms : int
        The REAL break timestamp already produced by
        premarket_break_classifier.classify_premarket_context() for a
        PREMARKET_OBSERVED result. Must match a real candle's time_ms
        in premarket_bars — never fabricated, never a different candle
        is substituted.
    level_source : str | None
        "PREVIOUS_DAY_HIGH" / "PREVIOUS_DAY_LOW". Defaults to the
        canonical direction-derived mapping if omitted (LONG ->
        PREVIOUS_DAY_HIGH, SHORT -> PREVIOUS_DAY_LOW), same convention
        as LiveSignalDetector's own level_source parameter.
    market_timezone : str
        Used only to compute a local calendar date string for the
        compatibility envelopes.
    min_displacement_bars : int
        Same frozen BDRR invariant used by find_displacement() itself
        (default 3). Applied here explicitly for the "displacement
        complete but no contact observed yet" case (see below), since
        find_displacement()'s own RETEST_NOT_FOUND payload reports a
        raw bar count without validating it against this threshold.
    level_invalidation_closes : int
        Same threshold as sequence_validator's line-level mode
        (default imported directly from DEFAULT_LEVEL_INVALIDATION_CLOSES).

    Returns
    -------
    dict
        {
            "retest_ready": bool,
            "break_origin": "PREMARKET_OBSERVED",
            "break_timestamp_ms": int,
            "displacement_bar_count": int | None,
            "premarket_retest_already_seen": bool,
            "reason": str,
        }

        Three distinct "displacement complete" outcomes are possible,
        not just two:
          - A real contact already happened within premarket_bars
            (find_displacement() status "OK") and the structure is
            not invalidated -> retest_ready=True, reason="READY",
            premarket_retest_already_seen=True.
          - Displacement genuinely has fewer than
            min_displacement_bars real separation bars (a premature
            contact, or too few bars) -> retest_ready=False,
            reason="DISPLACEMENT_INCOMPLETE".
          - Displacement has AT LEAST min_displacement_bars real
            separation bars, but NO contact has happened yet anywhere
            in premarket_bars (price simply stayed beyond the level
            the whole visible window) -> the structure IS validly
            built and NOT yet invalidated (there is nothing to
            invalidate without a contact to measure wrong-side closes
            from) -> retest_ready=True,
            reason="DISPLACEMENT_COMPLETE_AWAITING_RETEST",
            premarket_retest_already_seen=False. This is exactly the
            case where RTH's first touch of the level is the retest to
            trade — no policy about that RTH touch is decided here.
    """
    base = {
        "break_origin": "PREMARKET_OBSERVED",
        "break_timestamp_ms": break_timestamp_ms,
        "direction": direction,
        "level_price": level_price,
    }

    if direction not in ("LONG", "SHORT"):
        return {
            **base, "retest_ready": False, "reason": "UNSUPPORTED_DIRECTION",
            "displacement_bar_count": None, "premarket_retest_already_seen": False,
        }

    if not premarket_bars:
        return {
            **base, "retest_ready": False, "reason": "NO_PREMARKET_DATA",
            "displacement_bar_count": None, "premarket_retest_already_seen": False,
        }

    resolved_level_source = level_source or (
        "PREVIOUS_DAY_HIGH" if direction == "LONG" else "PREVIOUS_DAY_LOW"
    )

    bars = sorted(premarket_bars, key=lambda c: c["time_ms"])

    # ── Step 1: locate the REAL break candle by its real timestamp. ────
    # Never choose a different candle; never fabricate a timestamp.
    break_candle_index = None
    for i, bar in enumerate(bars):
        if bar["time_ms"] == break_timestamp_ms:
            break_candle_index = i
            break

    if break_candle_index is None:
        return {
            **base, "retest_ready": False, "reason": "BREAK_TIMESTAMP_NOT_FOUND",
            "displacement_bar_count": None, "premarket_retest_already_seen": False,
        }

    if break_candle_index == 0:
        # No real preceding candle exists to use as the compatibility
        # anchor — this should not occur for a genuine
        # PREMARKET_OBSERVED classification (which requires at least
        # one un-broken candle before the crossing), but guard
        # defensively rather than fabricate an anchor.
        return {
            **base, "retest_ready": False, "reason": "NO_ANCHOR_CANDLE",
            "displacement_bar_count": None, "premarket_retest_already_seen": False,
        }

    level_price_ticks = price_to_ticks(level_price, tick_size)

    # ── Step 2: build the small compatibility envelopes from real data. ─
    orb_envelope, break_result_envelope = _build_envelopes(
        bars, break_candle_index, level_price, level_price_ticks,
        resolved_level_source, tick_size, market_timezone,
    )

    config = {
        "timeframe_minutes": 1,
        "timezone": market_timezone,
        "session_open": "09:30",
        "orb_start": "session_open",
        "orb_duration_minutes": 5,
        "level_source": resolved_level_source,
        "direction": direction,
        "tick_size": tick_size,
        "min_displacement_ticks": None,
        "level_invalidation_closes": level_invalidation_closes,
    }

    # ── Step 3: real displacement — find_displacement() called as-is. ──
    displacement_result = find_displacement(bars, orb_envelope, break_result_envelope, config)

    if displacement_result.get("status") != "OK":
        failed_stage = displacement_result.get("failed_stage")
        raw_bar_count = displacement_result.get("displacement_bar_count")

        if failed_stage == "RETEST_NOT_FOUND" and raw_bar_count is not None \
                and raw_bar_count >= min_displacement_bars:
            # No contact anywhere in premarket_bars — price simply
            # stayed beyond the level the whole visible window. This
            # is NOT "incomplete": displacement is genuinely complete
            # (>= min_displacement_bars real separation bars, per
            # find_displacement()'s own bar-count report). There is no
            # contact yet, so nothing to invalidate either. The
            # structure is valid and awaiting its first retest — that
            # first touch, whenever it happens (including in RTH), is
            # the retest to trade. No policy about it is decided here.
            return {
                **base,
                "retest_ready": True,
                "reason": "DISPLACEMENT_COMPLETE_AWAITING_RETEST",
                "displacement_bar_count": raw_bar_count,
                "premarket_retest_already_seen": False,
            }

        # Genuinely insufficient displacement: either a premature
        # contact (RETEST_BEFORE_DISPLACEMENT, 0 bars) or fewer than
        # min_displacement_bars real separation bars before a contact
        # (DISPLACEMENT_TOO_SHORT).
        return {
            **base,
            "retest_ready": False,
            "reason": "DISPLACEMENT_INCOMPLETE",
            "failed_stage": failed_stage,
            "displacement_bar_count": raw_bar_count,
            "premarket_retest_already_seen": False,
        }

    displacement_bar_count = displacement_result.get("displacement_bar_count")
    # status == "OK" structurally implies find_displacement() located a
    # first_retest_contact_index within the candles it was given — i.e.

    # within premarket_bars alone here. The retest already happened
    # during premarket; no policy about a fresh RTH retest is decided
    # here (see module docstring).
    premarket_retest_already_seen = displacement_result.get("first_retest_contact_index") is not None

    # ── Step 4: real invalidation — validate_sequence() called as-is. ──
    seq_result = validate_sequence(
        bars, orb_envelope, break_result_envelope, displacement_result, config,
    )

    if seq_result.get("status") == "INVALIDATED":
        return {
            **base,
            "retest_ready": False,
            "reason": "STRUCTURE_INVALIDATED",
            "displacement_bar_count": displacement_bar_count,
            "premarket_retest_already_seen": premarket_retest_already_seen,
            "invalidation_index": seq_result.get("invalidation_index"),
        }

    return {
        **base,
        "retest_ready": True,
        "reason": "READY",
        "displacement_bar_count": displacement_bar_count,
        "premarket_retest_already_seen": premarket_retest_already_seen,
    }
