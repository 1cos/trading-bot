"""News Candle classification based on ATR ratio.

Defined in MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §9.

Classifies a single candle's range relative to the previous ATR(14).
This module does NOT exclude entries, modify detectors, or interact
with the pipeline state machine.  It produces a pure, deterministic
classification result.

The classifier receives a pre-computed ``prev_atr`` value (from the
ATR foundation in ``atr.py``).  It does NOT call ``previous_atr``
directly.  Future integration must pre-compute the ATR series once
(O(n)) and pass each shifted value here (O(1) per candle), avoiding
O(n²) patterns.

Classification bands (spec §9.3):

    ratio <= 2.0                    → NORMAL
    2.0 < ratio <= news_threshold   → LARGE
    ratio > news_threshold          → NEWS_CANDLE

``news_threshold`` must be >= 2.0 to avoid contradicting the frozen
NORMAL band.  Default is 3.0 (spec §9.4, FROZEN initial).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_lab.contracts.enums import CandleAtrStatus


# ── Validation helpers (local, no import of atr.py internals) ─────────────────


def _require_finite_number(value: object, name: str) -> float:
    """Require a finite numeric value (int or float, not bool)."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number, got bool")
    if isinstance(value, int):
        return float(value)
    if not isinstance(value, float):
        raise TypeError(
            f"{name} must be a number, got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return value


def _extract_candle_range(candle: dict) -> float:
    """Extract high - low from a candle dict with full validation.

    Raises
    ------
    KeyError
        If 'high' or 'low' is missing.
    TypeError
        If values are non-numeric or boolean.
    ValueError
        If values are NaN/Inf or high < low.
    """
    try:
        raw_high = candle["high"]
    except KeyError:
        raise KeyError("candle missing required field: 'high'") from None
    try:
        raw_low = candle["low"]
    except KeyError:
        raise KeyError("candle missing required field: 'low'") from None

    high = _require_finite_number(raw_high, "candle['high']")
    low = _require_finite_number(raw_low, "candle['low']")

    if high < low:
        raise ValueError(
            f"candle has high ({high}) < low ({low})"
        )
    return high - low


# ── Classification result ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class CandleAtrClassification:
    """Result of classifying a candle's range relative to ATR.

    Fields
    ------
    status : CandleAtrStatus
        Classification outcome.
    candle_range : float
        ``high - low`` of the candle.  Always populated, always >= 0.
    previous_atr : float or None
        The ATR value used.  ``None`` only when status is
        ``INSUFFICIENT_HISTORY``.
    ratio : float or None
        ``candle_range / previous_atr``.  ``None`` when ATR is
        unavailable or zero.
    news_threshold : float
        The threshold used for classification.  Always >= 2.0.
    """

    status: CandleAtrStatus
    candle_range: float
    previous_atr: float | None
    ratio: float | None
    news_threshold: float

    def __post_init__(self) -> None:
        if not isinstance(self.status, CandleAtrStatus):
            raise TypeError(
                f"status must be a CandleAtrStatus, "
                f"got {type(self.status).__name__}"
            )

        # candle_range: always finite >= 0
        if not isinstance(self.candle_range, (int, float)):
            raise TypeError("candle_range must be a number")
        if not math.isfinite(self.candle_range):
            raise ValueError("candle_range must be finite")
        if self.candle_range < 0:
            raise ValueError("candle_range must be >= 0")

        # news_threshold: always finite >= 2.0
        if not isinstance(self.news_threshold, (int, float)):
            raise TypeError("news_threshold must be a number")
        if not math.isfinite(self.news_threshold):
            raise ValueError("news_threshold must be finite")
        if self.news_threshold < 2.0:
            raise ValueError("news_threshold must be >= 2.0")

        # Status-specific invariants
        if self.status in (
            CandleAtrStatus.NORMAL,
            CandleAtrStatus.LARGE,
            CandleAtrStatus.NEWS_CANDLE,
        ):
            if self.previous_atr is None:
                raise ValueError(
                    f"status {self.status} requires previous_atr "
                    f"to be a positive number, got None"
                )
            if not isinstance(self.previous_atr, (int, float)):
                raise TypeError("previous_atr must be a number")
            if self.previous_atr <= 0:
                raise ValueError(
                    f"status {self.status} requires previous_atr > 0"
                )
            if self.ratio is None:
                raise ValueError(
                    f"status {self.status} requires ratio to be set"
                )
            if not isinstance(self.ratio, (int, float)):
                raise TypeError("ratio must be a number")
            if not math.isfinite(self.ratio):
                raise ValueError("ratio must be finite")
            if self.ratio < 0:
                raise ValueError("ratio must be >= 0")

        elif self.status == CandleAtrStatus.INSUFFICIENT_HISTORY:
            if self.previous_atr is not None:
                raise ValueError(
                    "INSUFFICIENT_HISTORY requires previous_atr=None"
                )
            if self.ratio is not None:
                raise ValueError(
                    "INSUFFICIENT_HISTORY requires ratio=None"
                )

        elif self.status == CandleAtrStatus.ATR_ZERO:
            if self.previous_atr != 0.0:
                raise ValueError(
                    "ATR_ZERO requires previous_atr=0.0"
                )
            if self.ratio is not None:
                raise ValueError(
                    "ATR_ZERO requires ratio=None"
                )

    def to_dict(self) -> dict[str, object]:
        """Canonical JSON-compatible dict representation."""
        return {
            "status": self.status.value,
            "candle_range": self.candle_range,
            "previous_atr": self.previous_atr,
            "ratio": self.ratio,
            "news_threshold": self.news_threshold,
        }


# ── Classifier ────────────────────────────────────────────────────────────────


def classify_candle_atr(
    candle: dict,
    prev_atr: float | None,
    news_threshold: float = 3.0,
) -> CandleAtrClassification:
    """Classify a single candle's range relative to its previous ATR.

    Parameters
    ----------
    candle : dict
        Raw candle dict with ``high`` and ``low``.
    prev_atr : float or None
        Pre-computed ``previous_atr`` for this candle (from B2).
        ``None`` means insufficient history.
    news_threshold : float
        Ratio strictly above which the candle is classified
        ``NEWS_CANDLE``.  Must be ``>= 2.0``.  Default ``3.0``
        (spec §9.4).

    Returns
    -------
    CandleAtrClassification
        Frozen result with status, candle_range, previous_atr,
        ratio, and news_threshold.

    Raises
    ------
    KeyError
        If candle is missing ``high`` or ``low``.
    TypeError
        If numeric fields are boolean or wrong type.
    ValueError
        If numeric fields are NaN/Inf, high < low, prev_atr < 0,
        or news_threshold < 2.0.
    """
    # ── Validate news_threshold ───────────────────────────────────────
    nt = _require_finite_number(news_threshold, "news_threshold")
    if nt < 2.0:
        raise ValueError(
            f"news_threshold must be >= 2.0, got {nt}"
        )

    # ── Extract candle range ──────────────────────────────────────────
    candle_range = _extract_candle_range(candle)

    # ── Handle prev_atr = None → INSUFFICIENT_HISTORY ─────────────────
    if prev_atr is None:
        return CandleAtrClassification(
            status=CandleAtrStatus.INSUFFICIENT_HISTORY,
            candle_range=candle_range,
            previous_atr=None,
            ratio=None,
            news_threshold=nt,
        )

    # ── Validate prev_atr ─────────────────────────────────────────────
    pa = _require_finite_number(prev_atr, "prev_atr")
    if pa < 0:
        raise ValueError(f"prev_atr must be >= 0, got {pa}")

    # ── Handle prev_atr = 0.0 → ATR_ZERO ─────────────────────────────
    if pa == 0.0:
        return CandleAtrClassification(
            status=CandleAtrStatus.ATR_ZERO,
            candle_range=candle_range,
            previous_atr=0.0,
            ratio=None,
            news_threshold=nt,
        )

    # ── Compute ratio and classify ────────────────────────────────────
    ratio = candle_range / pa

    if ratio > nt:
        status = CandleAtrStatus.NEWS_CANDLE
    elif ratio > 2.0:
        status = CandleAtrStatus.LARGE
    else:
        status = CandleAtrStatus.NORMAL

    return CandleAtrClassification(
        status=status,
        candle_range=candle_range,
        previous_atr=pa,
        ratio=ratio,
        news_threshold=nt,
    )
