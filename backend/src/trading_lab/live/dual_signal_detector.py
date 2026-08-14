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

    def evaluate(self, session: dict) -> SignalResult:
        """Evaluate the session for both LONG and SHORT setups.

        Returns the first valid SIGNAL found (LONG checked first).
        If neither direction has a signal, returns the result from
        whichever direction progressed further in the pipeline.
        """
        long_result = self._long.evaluate(session)
        if long_result.status == SignalStatus.SIGNAL:
            self._last_result = long_result
            return long_result

        short_result = self._short.evaluate(session)
        if short_result.status == SignalStatus.SIGNAL:
            self._last_result = short_result
            return short_result

        # Return whichever progressed further (more stage context = further)
        long_depth = len(long_result.stage_context or {})
        short_depth = len(short_result.stage_context or {})
        if short_depth > long_depth:
            self._last_result = short_result
            return short_result
        self._last_result = long_result
        return long_result
