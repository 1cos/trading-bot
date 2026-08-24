"""Pure multi-source (ORB + PDH/PDL) signal collector.

Combines whatever the ORB detector and the PDH/PDL candidate
evaluator each independently produce into SignalObservations, then
hands them all to collect_actionable_signals() (signal_dedup.py) —
the single existing primitive responsible for current-bar filtering,
staleness, consumed-key exclusion, and same-entry-candle dedup. This
module does NOT reimplement any of that logic.

Rule 1 (structural prerequisite, preserved, never bypassed)
-------------------------------------------------------------
PDH/PDL candidates are ALWAYS produced via
evaluate_pdh_pdl_candidate() (pdh_pdl_candidate_evaluator.py), which
internally requires check_orb_to_level_eligibility() to return
eligible=True before it will even construct a PDH/PDL detector — i.e.
a valid ORB break + complete displacement on the corresponding ORB
side is always the prerequisite for PDH/PDL to become entry-eligible.
This module never constructs a raw LiveSignalDetector(level_source=
"PREVIOUS_DAY_HIGH"/"PREVIOUS_DAY_LOW") directly and never reads
PDH/PDL structure any other way — there is no shortcut around the
eligibility gate here.

Scope / what this module deliberately does NOT do
----------------------------------------------------
    - No runtime wiring: NOT imported or called from bot_runner.py,
      trade_orchestrator.py, or any execution/IBKR path. Wiring this
      into the live runner is a separate, future task.
    - No premarket continuation: never imports/uses
      premarket_observed_structure, carry_in_separation,
      evaluate_seeded, or premarket_context. Premarket stays
      context-only, exactly as it already is elsewhere in the
      codebase.
    - No trading policy: does not implement ORB-vs-PDH priority,
      "first detector wins", opposite-direction arbitration, a
      Decision Engine, or grade selection. If ORB and PDH/PDL are
      both genuinely actionable in the same direction on the same
      bar, collect_actionable_signals()'s existing same-entry dedup
      folds them into one candidate (per Max's confirmed rule — see
      signal_dedup.py). If they are actionable in OPPOSITE
      directions on the same bar, both survive as separate
      DedupedSignalCandidate entries; this module returns them as-is
      and arbitrates nothing.
    - No side effects, no order submission, no consumed-set
      mutation: this is a pure function of its arguments.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from trading_lab.live.pdh_pdl_candidate_evaluator import evaluate_pdh_pdl_candidate
from trading_lab.live.signal_dedup import (
    DedupedSignalCandidate,
    SignalObservation,
    collect_actionable_signals,
)
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus


def collect_multi_source_signals(
    *,
    symbol: str,
    session: dict,
    previous_sessions: list[dict] | None,
    orb_detector: LiveSignalDetector,
    current_bar_time_ms: int,
    tick_size: float,
    market_timezone: str = "America/New_York",
    session_open: str = "09:30",
    entry_model: str = "CONFIRMATION_CLOSE",
    entry_buffer_ticks: int = 0,
    stop_buffer_ticks: int = 0,
    exit_target_r: int = 2,
    pdh_pdl_directions: Sequence[str] = (),
    live_boundary_ms: int = 0,
    consumed_setup_keys: Collection[str] = (),
    consumed_signal_keys: Collection[str] = (),
) -> list[DedupedSignalCandidate]:
    """Evaluate ORB + PDH/PDL candidates for one symbol/bar and return
    only the actionable, deduped result.

    Parameters
    ----------
    symbol : str
        Underlying symbol — attached to every SignalObservation built
        here (SignalResult itself carries no symbol field).
    session : dict
        Session dict as produced by LiveSessionBuilder.current_session()
        — the same shape both LiveSignalDetector.evaluate() and
        evaluate_pdh_pdl_candidate() expect.
    previous_sessions : list[dict] | None
        Previous-session historical bars (all_sessions format),
        required by evaluate_pdh_pdl_candidate() to compute PDH/PDL
        and check eligibility. If falsy, PDH/PDL simply produces no
        candidate for any requested direction (see
        evaluate_pdh_pdl_candidate()'s own NO_PREVIOUS_SESSIONS
        handling) — never a crash, never an invented candidate.
    orb_detector : LiveSignalDetector
        An already-configured ORB detector for this symbol (the same
        object bot_runner.py's _setup_symbol() builds today — LONG
        with the direction-derived default level_source=ORB_HIGH, or
        SHORT with ORB_LOW). Evaluated exactly once, with
        `consumed_setup_keys` passed through the same way
        MaxBotTradeOrchestrator._check_for_signal() already does.
        This function evaluates only ONE ORB detector/direction per
        call — a BOTH-direction symbol calls this twice (once per
        direction's orb_detector) if both ORB candidates are wanted,
        exactly like DualSignalDetector wraps two separately
        configured LiveSignalDetector instances rather than mutating
        one. (This module does NOT use DualSignalDetector itself,
        since DualSignalDetector's LONG-wins tiebreak is an
        arbitration policy — out of scope here; see module docstring.)
    current_bar_time_ms, live_boundary_ms, consumed_setup_keys,
    consumed_signal_keys :
        Passed straight through to collect_actionable_signals() — see
        that function's docstring for exact semantics. Never mutated.
    tick_size, market_timezone, session_open, entry_model,
    entry_buffer_ticks, stop_buffer_ticks, exit_target_r :
        Passed straight through to evaluate_pdh_pdl_candidate() for
        each requested direction — same meaning as
        LiveSignalDetector's own constructor parameters (see that
        function's docstring). Not read from `orb_detector`'s
        internal config — kept explicit, mirroring how bot_runner.py
        already tracks these values itself (self._tick_size,
        self._tz_str, ...) rather than introspecting a detector
        instance's private state.
    pdh_pdl_directions : Sequence[str], default ()
        Which directions to evaluate for PDH/PDL — e.g. ("LONG",) for
        a LONG-only symbol, ("LONG", "SHORT") for BOTH, matching
        bot_runner.py's own direction-filtering
        (self._direction in ("LONG", "BOTH") / ("SHORT", "BOTH")).
        Defaults to empty: PDH/PDL is opt-in per call, never assumed.

    Returns
    -------
    list[DedupedSignalCandidate]
        Exactly collect_actionable_signals()'s own return value for
        the combined ORB + PDH/PDL observations. No pending order, no
        side effect, no mutation of any input — including
        `orb_detector`, whose only mutation (evaluate()'s documented
        `_last_result` cache) does not affect this function's return
        value on a repeated call with the same arguments.
    """
    observations: list[SignalObservation] = []

    # ── ORB candidate: exactly one detector, evaluated exactly once,
    # with consumed_setup_keys passed through the same way
    # _check_for_signal() already does today. ──
    orb_result = orb_detector.evaluate(session, consumed_setup_keys=consumed_setup_keys)
    if orb_result.status == SignalStatus.SIGNAL:
        observations.append(SignalObservation(symbol=symbol, signal=orb_result))

    # ── PDH/PDL candidates: ALWAYS through the real, eligibility-gated
    # evaluator — never a raw level_source="PREVIOUS_DAY_HIGH"/
    # "PREVIOUS_DAY_LOW" detector. This is what keeps Rule 1 (ORB
    # break+displacement prerequisite) enforced; see module docstring. ──
    for direction in pdh_pdl_directions:
        out = evaluate_pdh_pdl_candidate(
            session, previous_sessions, symbol=symbol, direction=direction,
            tick_size=tick_size, market_timezone=market_timezone,
            session_open=session_open, entry_model=entry_model,
            entry_buffer_ticks=entry_buffer_ticks,
            stop_buffer_ticks=stop_buffer_ticks, exit_target_r=exit_target_r,
        )
        pdh_pdl_result = out["pdh_pdl_result"]
        if pdh_pdl_result is not None and pdh_pdl_result.status == SignalStatus.SIGNAL:
            observations.append(SignalObservation(symbol=symbol, signal=pdh_pdl_result))

    # ── The ONLY place current-bar/stale/consumed/dedup logic lives —
    # never duplicated here. ──
    return collect_actionable_signals(
        observations,
        current_bar_time_ms,
        live_boundary_ms=live_boundary_ms,
        consumed_setup_keys=consumed_setup_keys,
        consumed_signal_keys=consumed_signal_keys,
    )
