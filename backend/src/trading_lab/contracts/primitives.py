"""Canonical primitive contract types for the BDRR pipeline.

Ported from the frozen auxiliary type library defined in
BDRR_ENGINE_CANONICAL_HANDOFF.md §3.2 and implemented in JavaScript by:
  - estrategie/bdrr_detection_result.js  (priceTicks, rational,
    absoluteTickDistance, directionalTickDistance)
  - estrategie/bdrr_trade_plan.js        (priceTicks)

All types are immutable (frozen=True dataclasses with __slots__).

Serialization (.to_dict()) produces a plain Python dictionary whose keys
and value types match the canonical JSON-compatible shape used by the
JavaScript reference implementation:

    PriceTicks  → { "ticks": int, "tick_size": str }
    Rational    → { "numerator": int, "denominator": int }

tick_size is stored as a string (Decimal in the spec) to preserve exact
decimal representation and avoid floating-point artifacts.  The JavaScript
reference stores tick_size as a JS number (IEEE 754 double), but the
canonical schema declares it as Decimal { value: string }.  Python uses
the string form as the source of truth; a numeric accessor is provided
via the to_price() method for computation only.

Design decisions:
  - Booleans are explicitly rejected where int is required because
    Python's bool is a subclass of int and would silently pass isinstance
    checks.
  - Rational.denominator must be > 0 per the frozen contract.
  - PriceTicks.tick_size must be a non-empty string representing a
    positive decimal value.
  - No convenience behavior absent from the frozen contract is added.
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


# ── PriceTicks ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PriceTicks:
    """Integer tick count with its tick size.

    Canonical schema (§3.2):
        ticks:      int64   signed integer count from zero
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


# ── Rational ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Rational:
    """Exact rational number as numerator / denominator.

    Canonical schema (§3.2):
        numerator:      int
        denominator:    int     always > 0; never zero
        as_decimal():   Decimal computed on demand, never stored
    """

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        _require_int(self.numerator, "numerator")
        _require_int(self.denominator, "denominator")
        if self.denominator <= 0:
            raise ValueError(
                f"denominator must be > 0, got {self.denominator}"
            )

    def as_decimal(self) -> Decimal:
        """Compute numerator / denominator as an exact Decimal."""
        return Decimal(self.numerator) / Decimal(self.denominator)

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape."""
        return {"numerator": self.numerator, "denominator": self.denominator}
