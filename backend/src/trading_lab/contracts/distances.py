"""Canonical tick distance types for the BDRR pipeline.

Ported from the frozen auxiliary type library defined in
BDRR_ENGINE_CANONICAL_HANDOFF.md §3.2 and implemented in JavaScript by
estrategie/bdrr_detection_result.js (absoluteTickDistance,
directionalTickDistance).

These types share the same serialized shape as PriceTicks
({ ticks: int, tick_size: str }) but carry distinct semantic meaning:

    DirectionalTickDistance
        ticks is signed; positive = favorable direction.

    AbsoluteTickDistance
        ticks is unsigned; always >= 0.
        The schema declares "unsigned; always >= 0".

Both types are immutable (frozen=True dataclasses with __slots__).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


# ── Validation helpers ────────────────────────────────────────────────────────


def _require_int(value: object, name: str) -> int:
    """Validate that *value* is a plain int (bool rejected)."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def _require_tick_size(value: object) -> str:
    """Validate that *value* is a string encoding a positive decimal."""
    if not isinstance(value, str):
        raise TypeError(
            f"tick_size must be a str, got {type(value).__name__}"
        )
    if not value:
        raise ValueError("tick_size must be a non-empty string")
    try:
        d = Decimal(value)
    except InvalidOperation:
        raise ValueError(
            f"tick_size is not a valid decimal string: {value!r}"
        ) from None
    if d <= 0:
        raise ValueError(f"tick_size must be positive, got {value!r}")
    return value


# ── DirectionalTickDistance ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DirectionalTickDistance:
    """Signed tick distance; positive = favorable direction.

    Canonical schema (§3.2):
        ticks:      int64   signed; positive = favorable direction
        tick_size:  Decimal
        to_price(): Decimal  ticks × tick_size
    """

    ticks: int
    tick_size: str

    def __post_init__(self) -> None:
        _require_int(self.ticks, "ticks")
        _require_tick_size(self.tick_size)

    def to_price(self) -> Decimal:
        """Compute ticks × tick_size as an exact Decimal."""
        return Decimal(self.ticks) * Decimal(self.tick_size)

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape."""
        return {"ticks": self.ticks, "tick_size": self.tick_size}


# ── AbsoluteTickDistance ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AbsoluteTickDistance:
    """Non-negative tick distance.

    Canonical schema (§3.2):
        ticks:      int64   unsigned; always >= 0
        tick_size:  Decimal
        to_price(): Decimal  ticks × tick_size
    """

    ticks: int
    tick_size: str

    def __post_init__(self) -> None:
        _require_int(self.ticks, "ticks")
        _require_tick_size(self.tick_size)
        if self.ticks < 0:
            raise ValueError(f"ticks must be >= 0, got {self.ticks}")

    def to_price(self) -> Decimal:
        """Compute ticks × tick_size as an exact Decimal."""
        return Decimal(self.ticks) * Decimal(self.tick_size)

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape."""
        return {"ticks": self.ticks, "tick_size": self.tick_size}
