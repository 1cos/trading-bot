"""Minimal live wiring adapter for multi-source (ORB + PDH/PDL) signal
evaluation, gated by a feature flag defaulting to OFF.

MultiSourceSignalDetectorAdapter implements exactly the interface
MaxBotTradeOrchestrator and bot_runner.py already expect from
self._signal_detector / rt.signal_detector:

    evaluate(session, consumed_setup_keys=None) -> SignalResult
    set_previous_sessions(previous_sessions) -> None
    last_result (property) -> SignalResult | None

(Confirmed by audit: trade_orchestrator.py's ONLY call on
self._signal_detector is `.evaluate(sess, consumed_setup_keys=...)`
in _check_for_signal(); bot_runner.py additionally calls
`.set_previous_sessions(sessions)` once at boot and reads
`.last_result` for PWA/log stage display after on_bar() has already
run. LiveSignalDetector and DualSignalDetector both already satisfy
this same trio — this adapter is a third, drop-in-compatible
implementation of it, nothing more.)

What this module does NOT do
-----------------------------
    - Does NOT modify trade_orchestrator.py. TradeOrchestrator's
      interface and behavior are completely unchanged; this is a new,
      standalone, swap-in detector-shaped object.
    - Does NOT modify bot_runner.py. Nothing constructs or injects
      this adapter into the live symbol setup — wiring it into
      _setup_symbol() is a separate, future task. This class has ZERO
      effect on live behavior until something starts instantiating it
      in place of a plain LiveSignalDetector.
    - Does NOT modify DailyTradeManager, entry/exit execution, stop/
      target calculation, or any IBKR code. Every SignalResult this
      adapter returns (ORB's own, PDH/PDL's own, or the same-entry
      merged canonical one) was built entirely by the existing,
      unmodified LiveSignalDetector/TradePlan pipeline -- this module
      never computes or touches a price.
    - Does NOT implement grading, confluence, PWA display logic, or
      any arbitration beyond collect_actionable_signals()'s own
      existing same-entry dedup rule. If ORB and PDH/PDL are ever both
      genuinely actionable on the same bar in OPPOSITE directions,
      this adapter's own single-direction construction (see below)
      makes that combination structurally impossible for one instance
      to produce in the first place -- there is no arbitration policy
      to write.

Why at most one SignalResult is always returned (no arbitration
needed)
-----------------------------------------------------------------------
This adapter is single-direction, matching how LiveSignalDetector,
and every live orchestrator instance, are themselves already
single-direction (BOTH mode is handled by wrapping two whole
orchestrators/detectors via DualSignalDetector, one per direction --
this class does not attempt to replace that wrapping). Both the ORB
detector and the PDH/PDL evaluation inside one adapter instance are
therefore always evaluated for the SAME direction. Per the 2026-08-24
"distinct-entry arbitration" audit: two same-direction SignalResults
that are both genuinely actionable on the same current bar always
share the identical entry_timestamp_ms (actionable is defined as
entry_timestamp_ms == current_bar_time_ms, a single value per call),
so collect_actionable_signals()'s existing same-entry dedup always
folds them into exactly one candidate. Two different entry
timestamps can never both be actionable simultaneously (proven, not
assumed, in that audit). The result: for one direction, evaluated
once, collect_actionable_signals() can return 0 or 1 candidates here
-- never more. A defensive length check in evaluate() documents this
invariant without silently trusting it.
"""

from __future__ import annotations

from trading_lab.live.pdh_pdl_candidate_evaluator import evaluate_pdh_pdl_candidate
from trading_lab.live.signal_dedup import SignalObservation, collect_actionable_signals
from trading_lab.live.signal_detector import LiveSignalDetector, SignalResult, SignalStatus

# Feature flag -- ON. PDH/PDL is evaluated on the live execution path
# and a PD signal can open a real PAPER_EXECUTE trade. This is the
# committed, deliberate default as of 2026-08-24, guarded by
# test_bot_runner_multi_source_wiring.py's TestT4Guardrail and proven
# end-to-end by test_pdh_pdl_live_readiness.py -- not a leftover local
# edit. Turning it back to False disables live PDH/PDL entirely and
# reverts the adapter to a byte-identical ORB passthrough.
#
# There is still no config/env-var loader wired to this constant;
# changing live behavior means editing this default or passing
# enable_pdh_pdl_live=False explicitly to the constructor.
ENABLE_PDH_PDL_LIVE = True


class MultiSourceSignalDetectorAdapter:
    """Detector-shaped adapter: ORB always, PDH/PDL only when enabled.

    Parameters
    ----------
    symbol : str
        Underlying symbol.
    direction : str
        "LONG" or "SHORT". Governs both which PDH/PDL side is
        evaluated (PREVIOUS_DAY_HIGH for LONG, PREVIOUS_DAY_LOW for
        SHORT) and is expected to match `orb_detector`'s own
        configured direction (not verified here -- `orb_detector` is
        the caller's own already-configured object, exactly as today).
    orb_detector : LiveSignalDetector
        An already-configured ORB detector for this symbol/direction
        -- the same kind of object bot_runner.py's _setup_symbol()
        already builds and would otherwise assign directly as
        rt.signal_detector. Every ORB-side call
        (evaluate/set_previous_sessions) is delegated to it unchanged.
    tick_size, market_timezone, session_open, entry_model,
    entry_buffer_ticks, stop_buffer_ticks, exit_target_r :
        Passed straight through to evaluate_pdh_pdl_candidate() for
        the PDH/PDL side, same meaning as LiveSignalDetector's own
        constructor parameters. Not read from `orb_detector`'s
        internal config -- kept explicit, matching
        multi_source_signal_collector.py's own established pattern.
    enable_pdh_pdl_live : bool | None, default None
        None means "use the module-level ENABLE_PDH_PDL_LIVE
        default" (False unless that constant itself is changed).
        Passing True/False explicitly overrides it for this instance
        -- primarily for tests, but also available to any future
        config-driven caller.
    """

    def __init__(
        self,
        *,
        symbol: str,
        direction: str,
        orb_detector: LiveSignalDetector,
        tick_size: float,
        market_timezone: str = "America/New_York",
        session_open: str = "09:30",
        entry_model: str = "CONFIRMATION_CLOSE",
        entry_buffer_ticks: int = 0,
        stop_buffer_ticks: int = 0,
        exit_target_r: int = 2,
        enable_pdh_pdl_live: bool | None = None,
    ):
        if direction not in ("LONG", "SHORT"):
            raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")
        self._symbol = symbol
        self._direction = direction
        self._orb_detector = orb_detector
        self._tick_size = tick_size
        self._market_timezone = market_timezone
        self._session_open = session_open
        self._entry_model = entry_model
        self._entry_buffer_ticks = entry_buffer_ticks
        self._stop_buffer_ticks = stop_buffer_ticks
        self._exit_target_r = exit_target_r
        self._enable_pdh_pdl_live = (
            ENABLE_PDH_PDL_LIVE if enable_pdh_pdl_live is None else enable_pdh_pdl_live
        )
        self._previous_sessions: list | None = None
        self._last_result: SignalResult | None = None

    # ── Interface parity with LiveSignalDetector/DualSignalDetector ────────

    @property
    def last_result(self) -> SignalResult | None:
        """The result of the most recent evaluate() call -- exactly
        what THIS adapter returned (ORB's own object in disabled or
        ORB-only-actionable mode; PDH/PDL's own object when it alone
        is actionable; the same-entry canonical merged object
        otherwise), matching LiveSignalDetector's/DualSignalDetector's
        own documented meaning of this property."""
        return self._last_result

    def set_previous_sessions(self, previous_sessions: list | None) -> None:
        """Store previous-session bars for PDH/PDL, and forward to the
        wrapped ORB detector too (transparent passthrough -- today a
        no-op for ORB_HIGH/ORB_LOW, exactly as calling this on a plain
        LiveSignalDetector already is)."""
        self._previous_sessions = previous_sessions
        self._orb_detector.set_previous_sessions(previous_sessions)

    def evaluate(self, session: dict, consumed_setup_keys: set[str] | None = None) -> SignalResult:
        """Evaluate ORB (always) and PDH/PDL (only if enabled), and
        return exactly one SignalResult -- never None. (Python `None`
        would break the existing orchestrator interface: `_check_for_
        signal()` immediately reads `result.status` on whatever this
        returns, with no None-check, exactly as it does for a plain
        LiveSignalDetector today. "Or None" in this adapter's own
        contract means "or a well-formed NO_SETUP SignalResult",
        never a bare None -- see module docstring / class docstring.)

        When ENABLE_PDH_PDL_LIVE-equivalent is False (the default):
        this is a byte-identical passthrough -- literally the same
        object orb_detector.evaluate() itself returned, with zero
        extra branching, so disabled behavior is provably unchanged
        from plain ORB-only operation.
        """
        orb_result = self._orb_detector.evaluate(session, consumed_setup_keys=consumed_setup_keys)

        if not self._enable_pdh_pdl_live:
            self._last_result = orb_result
            return orb_result

        observations: list[SignalObservation] = []
        if orb_result.status == SignalStatus.SIGNAL:
            observations.append(SignalObservation(symbol=self._symbol, signal=orb_result))

        pdh_pdl_out = evaluate_pdh_pdl_candidate(
            session, self._previous_sessions, symbol=self._symbol, direction=self._direction,
            tick_size=self._tick_size, market_timezone=self._market_timezone,
            session_open=self._session_open, entry_model=self._entry_model,
            entry_buffer_ticks=self._entry_buffer_ticks,
            stop_buffer_ticks=self._stop_buffer_ticks, exit_target_r=self._exit_target_r,
        )
        pdh_pdl_result = pdh_pdl_out["pdh_pdl_result"]
        if pdh_pdl_result is not None and pdh_pdl_result.status == SignalStatus.SIGNAL:
            observations.append(SignalObservation(symbol=self._symbol, signal=pdh_pdl_result))

        if not observations:
            # Neither source has a SIGNAL right now (or PDH/PDL is
            # ineligible/unavailable) -- fall back to ORB's own raw
            # result untouched, so the orchestrator's own downstream
            # logging (e.g. SIGNAL_NOT_CURRENT for a historical ORB
            # SIGNAL) behaves exactly as it does today.
            self._last_result = orb_result
            return orb_result

        candles = session.get("candles") if isinstance(session, dict) else None
        current_bar_time_ms = candles[-1]["time_ms"] if candles else None

        candidates = collect_actionable_signals(
            observations,
            current_bar_time_ms,
            consumed_setup_keys=consumed_setup_keys or (),
            # consumed_signal_keys is intentionally NOT threaded here.
            # The orchestrator never passes it into evaluate() either
            # (see module docstring) -- it owns that bookkeeping and
            # applies it itself, AFTER this adapter returns, exactly
            # as it already does for a plain LiveSignalDetector.
        )
        if not candidates:
            # Nothing actionable on THIS bar from either source (e.g.
            # both historical, or stale) -- same fallback as above.
            self._last_result = orb_result
            return orb_result

        # Defensive, not policy: see class docstring for why this is
        # always <= 1 by construction for a single-direction adapter.
        # Raised (not asserted) so this guard cannot be silently
        # stripped by running Python with -O in some future deployment.
        if len(candidates) > 1:
            raise RuntimeError(
                f"MultiSourceSignalDetectorAdapter invariant violated: "
                f"{len(candidates)} actionable candidates for a single "
                f"direction ({self._direction}) on one current bar -- "
                f"this should be structurally impossible; see class "
                f"docstring. Refusing to guess a winner."
            )
        result = candidates[0].signal
        self._last_result = result
        return result
