"""Dual signal detector — evaluates both LONG and SHORT for BOTH mode.

Wraps two LiveSignalDetector instances (one LONG, one SHORT) and
returns the first valid signal found. The resolved direction comes
from the signal result itself (LONG or SHORT), never "BOTH".

Priority: LONG is checked first. If both signal simultaneously,
LONG wins. This is a deterministic tiebreak — the practical occurrence
is rare since ORB_HIGH and ORB_LOW setups require opposite price action.
"""

from __future__ import annotations

from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)


# ── Explicit semantic stage priority ─────────────────────────────────────────
#
# When neither direction has produced a SIGNAL, the direction shown to
# log/PWA should represent the live setup that is furthest along the
# real BDRR pipeline right now — not whichever result happens to carry
# more stage_context keys (a raw dict-size proxy that conflates "has
# accumulated more incidental fields" with "is more advanced").
#
# Every key below is a real failed_stage value actually produced by
# LiveSignalDetector (see _STAGE_LABELS in signal_detector.py) — no new
# stages are introduced. Higher number = further along = wins.
#
#   0 — no usable progress yet, or the sequence is dead.
#       SEQUENCE_INVALIDATED lives here on purpose: validate_sequence()
#       has confirmed this break will never produce a signal, so it
#       must never rank above a direction that is still genuinely
#       alive, however early-stage. In practice LiveSignalDetector's
#       own evaluate() loop already skips past SEQUENCE_INVALIDATED
#       internally (T20 fix), so DualSignalDetector should rarely see
#       it directly — this is a defensive floor for the rare case
#       (safety-cap exhaustion) where it still does.
#   1 — session exists, level/ORB not usable yet.
#   2 — level ready, no break yet.
#   3 — break found, displacement not yet complete (Stage 3: both
#       DISPLACEMENT_TOO_SHORT and RETEST_BEFORE_DISPLACEMENT are
#       returned by the displacement-detection stage itself, i.e. the
#       same real pipeline stage — a retest touching the level before
#       displacement has finished is not "further along" than
#       displacement still building, it is the same stage).
#   4 — displacement complete, no retest contact yet.
#   5 — retest window open, no qualifying entry candle yet.
#
# Any failed_stage not listed (including None) falls back to 0 — the
# safest default, since an unrecognized state should never be assumed
# to be more relevant than a recognized, live one.
_STAGE_PRIORITY: dict[str | None, int] = {
    # Tier 0 — no progress, or explicitly dead.
    None: 0,
    "NO_SESSION": 0,
    "NO_CANDLES": 0,
    "INVALID_SESSION_INPUT": 0,
    "INVALID_INPUT": 0,
    "MISSING_SESSIONS_DATA": 0,
    "NO_PREVIOUS_SESSION": 0,
    "SEQUENCE_INVALIDATED": 0,

    # Tier 1 — level/ORB not ready.
    "LEVEL_NOT_FOUND": 1,
    "PROVIDER_NOT_IMPLEMENTED": 1,
    "UNSUPPORTED_CONFIGURATION": 1,

    # Tier 2 — waiting for break.
    "BREAK_NOT_FOUND": 2,

    # Tier 3 — break found, displacement stage (Stage 3, either reason).
    "DISPLACEMENT_TOO_SHORT": 3,
    "RETEST_BEFORE_DISPLACEMENT": 3,

    # Tier 4 — waiting for retest.
    "RETEST_NOT_FOUND": 4,

    # Tier 5 — retest window open, waiting for a qualifying entry candle.
    "NO_QUALIFYING_REJECTION_CANDLE": 5,
}
_DEFAULT_STAGE_PRIORITY = 0


def _stage_priority(result: SignalResult) -> int:
    return _STAGE_PRIORITY.get(result.failed_stage, _DEFAULT_STAGE_PRIORITY)


def _more_relevant(candidate: SignalResult, incumbent: SignalResult) -> bool:
    """True if `candidate` should be shown in preference to `incumbent`.

    Both are NO_SETUP results. Compares explicit stage priority first;
    a more-advanced-but-older setup beats a less-advanced-but-newer one.
    Only when priority ties does the more recent break (break_time_ms)
    win — a side with no break at all (BREAK_NOT_FOUND, no
    break_time_ms in its stage_context) sorts as the oldest possible,
    so it never wins a tie against a side with a real break.
    """
    p_candidate = _stage_priority(candidate)
    p_incumbent = _stage_priority(incumbent)
    if p_candidate != p_incumbent:
        return p_candidate > p_incumbent

    t_candidate = (candidate.stage_context or {}).get("break_time_ms")
    t_incumbent = (incumbent.stage_context or {}).get("break_time_ms")
    t_candidate = t_candidate if t_candidate is not None else -1
    t_incumbent = t_incumbent if t_incumbent is not None else -1
    return t_candidate > t_incumbent


class DualSignalDetector:
    """Adapter that evaluates both LONG and SHORT on the same session.

    Parameters
    ----------
    long_detector : LiveSignalDetector
        Detector configured for LONG / ORB_HIGH.
    short_detector : LiveSignalDetector
        Detector configured for SHORT / ORB_LOW.
    """

    def __init__(self, long_detector: LiveSignalDetector, short_detector: LiveSignalDetector):
        self._long = long_detector
        self._short = short_detector
        self._last_result: SignalResult | None = None

    @property
    def last_result(self) -> SignalResult | None:
        """The result of the most recent evaluate() call."""
        return self._last_result

    def set_previous_sessions(self, previous_sessions: list | None) -> None:
        """Propagate previous-session historical bars to both directions.

        LONG and SHORT are the same underlying symbol, so both detectors
        receive the identical all_sessions data. See
        LiveSignalDetector.set_previous_sessions() for the expected format.
        """
        self._long.set_previous_sessions(previous_sessions)
        self._short.set_previous_sessions(previous_sessions)

    def evaluate(self, session: dict, consumed_setup_keys: set[str] | None = None) -> SignalResult:
        """Evaluate the session for both LONG and SHORT setups.

        Returns the first valid SIGNAL found (LONG checked first).
        If neither direction has a signal, returns the result from
        whichever direction represents the more advanced, more
        relevant live setup right now (explicit stage priority, with
        break recency as tie-break) — see _more_relevant().
        """
        long_result = self._long.evaluate(session, consumed_setup_keys=consumed_setup_keys)
        if long_result.status == SignalStatus.SIGNAL:
            self._last_result = long_result
            return long_result

        short_result = self._short.evaluate(session, consumed_setup_keys=consumed_setup_keys)
        if short_result.status == SignalStatus.SIGNAL:
            self._last_result = short_result
            return short_result

        if _more_relevant(short_result, long_result):
            self._last_result = short_result
            return short_result
        self._last_result = long_result
        return long_result
