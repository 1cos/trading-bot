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

    def evaluate(self, session: dict) -> SignalResult:
        """Evaluate the session for both LONG and SHORT setups.

        Returns the first valid SIGNAL found (LONG checked first).
        If neither direction has a signal, returns NO_SETUP.
        """
        long_result = self._long.evaluate(session)
        if long_result.status == SignalStatus.SIGNAL:
            return long_result

        short_result = self._short.evaluate(session)
        if short_result.status == SignalStatus.SIGNAL:
            return short_result

        # Return the LONG result (NO_SETUP) — arbitrary, both are NO_SETUP
        return long_result
