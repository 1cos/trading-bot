"""PDH/PDL candidate evaluator — wiring only, NOT execution.

Connects, for the first time, the eligibility predicate
(``pdh_pdl_eligibility.check_orb_to_level_eligibility``) to a real
``LiveSignalDetector`` configured with ``level_source="PREVIOUS_DAY_HIGH"``
or ``"PREVIOUS_DAY_LOW"``, so the full BDRR pipeline (break ->
displacement -> retest -> Max Entry Candle) can be exercised end-to-end
on PDH/PDL in the live track:

    check_orb_to_level_eligibility()
        -> eligible=True
        -> a fresh LiveSignalDetector(direction=..., level_source=...)
        -> BREAK -> DISPLACEMENT -> RETEST -> SINGLE_CANDLE_REJECTION
        -> NO_SETUP (with a real stage) or SIGNAL

This module is observational only:
    - It does NOT create a pending order.
    - It does NOT touch TradeOrchestrator / ObserveOrchestrator.
    - It does NOT talk to IBKR.
    - It is NOT called from bot_runner.py or any live execution path.

Architecture note: this deliberately does NOT mutate an existing ORB
LiveSignalDetector's level_source. Doing so would risk mixing setup
identity/scanning state between two structurally different pipelines
sharing one instance. Instead, a fresh, explicitly-configured
LiveSignalDetector is constructed only when eligible=True — the same
"distinct detector per level_source" pattern already established by
LiveSignalDetector's own level_source constructor parameter (see
micro-task 5) and by DualSignalDetector wrapping two separately
configured instances (LONG/SHORT) rather than mutating one.

TWO_CANDLE_ENGULFING_RECOVERY is unaffected and unmodified: it is
already skipped for PDH/PDL by rejection_finder.py's existing
has_zone_edges gate (PDH/PDL provider results have no top-level
orb_high/orb_low keys), so only SINGLE_CANDLE_REJECTION (the standard
Max Entry Candle) is reachable for PDH/PDL through this evaluator,
exactly as intended for this task.
"""

from __future__ import annotations

from trading_lab.live.signal_detector import LiveSignalDetector, SignalResult
from trading_lab.pdh_pdl_eligibility import check_orb_to_level_eligibility
from trading_lab.pdh_pdl_provider import compute_pdh_pdl
from trading_lab.session_context import build_session_context


def _build_orb_config(
    direction: str,
    tick_size: float,
    market_timezone: str,
    session_open: str,
) -> dict:
    """Engine config for the ORB side used by the eligibility check.

    Mirrors LiveSignalDetector.__init__'s own engine_config
    construction (same defaults) so the eligibility predicate sees an
    ORB exactly as the ORB detector itself would.
    """
    orb_level_source = "ORB_HIGH" if direction == "LONG" else "ORB_LOW"
    return {
        "timeframe_minutes": 1,
        "timezone": market_timezone,
        "session_open": session_open,
        "orb_start": "session_open",
        "orb_duration_minutes": 5,
        "level_source": orb_level_source,
        "direction": direction,
        "tick_size": tick_size,
        "min_displacement_ticks": None,
        "min_penetration_ticks": None,
        "min_close_beyond_level_ticks": None,
        "min_displacement_bars": None,
        "consecutive_orb_closes": 2,
        "rejection_wick_ratio_min": None,
        "body_ratio_max": None,
        "confirmation_wick_penetration_pct_min": None,
    }


def evaluate_pdh_pdl_candidate(
    session: dict,
    previous_sessions: list[dict] | None,
    symbol: str,
    direction: str,
    tick_size: float,
    market_timezone: str = "America/New_York",
    session_open: str = "09:30",
    entry_model: str = "CONFIRMATION_CLOSE",
    entry_buffer_ticks: int = 0,
    stop_buffer_ticks: int = 0,
    exit_target_r: int = 2,
) -> dict:
    """Evaluate whether PDH (LONG) / PDL (SHORT) currently forms a
    complete BDRR sequence, but ONLY if it is eligible right now.

    This does not execute anything. It is purely observational: the
    caller decides what (if anything) to do with the returned
    SignalResult; no order, no orchestrator, no IBKR call happens
    here or as a side effect of this function.

    Parameters
    ----------
    session : dict
        Session dict as produced by LiveSessionBuilder.current_session()
        — same shape LiveSignalDetector.evaluate() expects.
    previous_sessions : list[dict] | None
        Previous-session historical bars (all_sessions format), the
        same data already retained on SymbolRuntime.previous_sessions
        and propagated via LiveSignalDetector.set_previous_sessions()
        (micro-tasks 3-4). Required both to compute the PDH/PDL price
        and to build the PDH/PDL level itself.
    symbol, direction, tick_size, market_timezone, session_open,
    entry_model, entry_buffer_ticks, stop_buffer_ticks, exit_target_r :
        Same meaning as the corresponding LiveSignalDetector
        constructor parameters. direction must be "LONG" or "SHORT".

    Returns
    -------
    dict
        {
            "eligibility": dict,       # from check_orb_to_level_eligibility()
            "pdh_pdl_result": SignalResult | None,
        }
        pdh_pdl_result is None whenever the PDH/PDL pipeline was NOT
        evaluated at all (not eligible, no candles, no previous
        session data). When eligible, pdh_pdl_result is always a real
        SignalResult (SIGNAL or NO_SETUP with a genuine failed_stage)
        from a fresh, explicitly-configured LiveSignalDetector.
    """
    if direction not in ("LONG", "SHORT"):
        return {
            "eligibility": {"eligible": False, "reason": "UNSUPPORTED_DIRECTION"},
            "pdh_pdl_result": None,
        }

    candles = session.get("candles") if isinstance(session, dict) else None
    if not candles:
        return {
            "eligibility": {"eligible": False, "reason": "NO_CANDLES"},
            "pdh_pdl_result": None,
        }

    orb_config = _build_orb_config(direction, tick_size, market_timezone, session_open)

    session_context = build_session_context(candles, orb_config)
    if session_context.get("status") != "OK":
        return {
            "eligibility": {
                "eligible": False,
                "reason": "NO_SESSION",
                "failed_stage": session_context.get("failed_stage"),
            },
            "pdh_pdl_result": None,
        }

    if not previous_sessions:
        return {
            "eligibility": {"eligible": False, "reason": "NO_PREVIOUS_SESSIONS"},
            "pdh_pdl_result": None,
        }

    pdh_pdl = compute_pdh_pdl(session_context["date"], previous_sessions)
    if pdh_pdl.get("status") != "OK":
        return {
            "eligibility": {
                "eligible": False,
                "reason": "NO_PREVIOUS_SESSION",
            },
            "pdh_pdl_result": None,
        }

    candidate_level_price = pdh_pdl["pdh"] if direction == "LONG" else pdh_pdl["pdl"]

    eligibility = check_orb_to_level_eligibility(
        session_context["candles"], session_context, orb_config,
        candidate_level_price,
    )

    if not eligibility.get("eligible"):
        return {"eligibility": eligibility, "pdh_pdl_result": None}

    # ── Eligible: construct a FRESH, explicitly-configured PDH/PDL
    # detector — never mutate the ORB detector's level_source. ───────
    level_source = "PREVIOUS_DAY_HIGH" if direction == "LONG" else "PREVIOUS_DAY_LOW"
    pdh_pdl_detector = LiveSignalDetector(
        symbol=symbol,
        direction=direction,
        tick_size=tick_size,
        market_timezone=market_timezone,
        session_open=session_open,
        entry_model=entry_model,
        entry_buffer_ticks=entry_buffer_ticks,
        stop_buffer_ticks=stop_buffer_ticks,
        exit_target_r=exit_target_r,
        level_source=level_source,
    )
    pdh_pdl_detector.set_previous_sessions(previous_sessions)

    result: SignalResult = pdh_pdl_detector.evaluate(session)

    return {"eligibility": eligibility, "pdh_pdl_result": result}
