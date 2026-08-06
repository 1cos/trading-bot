"""Canonical zone contracts for composite and validated zones.

Defined in MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §7.

ZoneComponent — a single structural level contributing to a zone.
CompositeZone — one or more components merged into a tradeable area.

Both types are immutable (frozen dataclasses with __slots__).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_lab.contracts.enums import ZoneStatus, ZoneType


# ── Validation helpers ────────────────────────────────────────────────────────


def _require_finite_number(value: object, name: str) -> float:
    """Require a finite numeric value (int or float, not bool)."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number, got bool")
    if isinstance(value, int):
        value = float(value)
    if not isinstance(value, float):
        raise TypeError(
            f"{name} must be a number, got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return value


# ── ZoneComponent ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ZoneComponent:
    """A single structural level contributing to a composite zone.

    Fields
    ------
    source : str
        Provider label (e.g. ``"ORB_HIGH"``, ``"PIVOT_WICK"``).
        Free string — not constrained to ``LevelSource`` enum so
        that future providers can be represented without modifying
        the enum. Must be non-empty.
    price : float
        The reference price of the level. For zones this is the
        provider's canonical price (the near edge in provider
        terms). For lines it equals both bounds.
    lower_bound : float
        Lower edge of the level's price band.
    upper_bound : float
        Upper edge. For a line: ``lower_bound == upper_bound == price``.

        Invariant: ``lower_bound <= price <= upper_bound``.
    is_primary : bool
        True if this is the level that the retest must reach for
        a valid Entry Candle (spec §4.2).
    """

    source: str
    price: float
    lower_bound: float
    upper_bound: float
    is_primary: bool

    def __post_init__(self) -> None:
        # source
        if not isinstance(self.source, str):
            raise TypeError(
                f"source must be a str, got {type(self.source).__name__}"
            )
        if not self.source:
            raise ValueError("source must be non-empty")

        # Numeric fields — normalize int → float, reject bool/NaN/inf
        p = _require_finite_number(self.price, "price")
        lb = _require_finite_number(self.lower_bound, "lower_bound")
        ub = _require_finite_number(self.upper_bound, "upper_bound")
        object.__setattr__(self, "price", p)
        object.__setattr__(self, "lower_bound", lb)
        object.__setattr__(self, "upper_bound", ub)

        # Bound ordering
        if self.lower_bound > self.upper_bound:
            raise ValueError(
                f"lower_bound ({self.lower_bound}) must be "
                f"<= upper_bound ({self.upper_bound})"
            )

        # Price within bounds
        if not (self.lower_bound <= self.price <= self.upper_bound):
            raise ValueError(
                f"price ({self.price}) must be within "
                f"[lower_bound ({self.lower_bound}), "
                f"upper_bound ({self.upper_bound})]"
            )

        # is_primary
        if not isinstance(self.is_primary, bool):
            raise TypeError(
                f"is_primary must be a bool, "
                f"got {type(self.is_primary).__name__}"
            )

    def to_dict(self) -> dict:
        """Canonical JSON-compatible dict representation."""
        return {
            "source": self.source,
            "price": self.price,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "is_primary": self.is_primary,
        }


# ── CompositeZone ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CompositeZone:
    """One or more structural levels merged into a tradeable area.

    Fields
    ------
    zone_type : ZoneType
        ``VALIDATED_PIVOT_ZONE`` or ``COMPOSITE_CONFLUENCE_ZONE``.
    lower_bound : float
        Exact envelope minimum across all component lower_bounds.
    upper_bound : float
        Exact envelope maximum across all component upper_bounds.
    components : tuple[ZoneComponent, ...]
        Non-empty. Exactly one must have ``is_primary == True``.
    status : ZoneStatus
        Lifecycle state: ACTIVE, SECONDARY, or STORED.

    Properties
    ----------
    primary_component : ZoneComponent
        The single component with ``is_primary == True``.
    primary_level_price : float
        ``primary_component.price`` — the price the retest must
        reach for a valid Entry Candle (spec §4.2).
    """

    zone_type: ZoneType
    lower_bound: float
    upper_bound: float
    components: tuple[ZoneComponent, ...]
    status: ZoneStatus

    def __post_init__(self) -> None:
        # zone_type
        if not isinstance(self.zone_type, ZoneType):
            raise TypeError(
                f"zone_type must be a ZoneType, "
                f"got {type(self.zone_type).__name__}"
            )

        # Numeric bounds — normalize, reject bool/NaN/inf
        lb = _require_finite_number(self.lower_bound, "lower_bound")
        ub = _require_finite_number(self.upper_bound, "upper_bound")
        object.__setattr__(self, "lower_bound", lb)
        object.__setattr__(self, "upper_bound", ub)

        if self.lower_bound > self.upper_bound:
            raise ValueError(
                f"lower_bound ({self.lower_bound}) must be "
                f"<= upper_bound ({self.upper_bound})"
            )

        # components
        if not isinstance(self.components, tuple):
            raise TypeError(
                f"components must be a tuple, "
                f"got {type(self.components).__name__}"
            )
        if len(self.components) == 0:
            raise ValueError("components must be non-empty")
        for i, c in enumerate(self.components):
            if not isinstance(c, ZoneComponent):
                raise TypeError(
                    f"components[{i}] must be a ZoneComponent, "
                    f"got {type(c).__name__}"
                )

        # Exactly one primary
        primaries = [c for c in self.components if c.is_primary]
        if len(primaries) == 0:
            raise ValueError(
                "exactly one component must have is_primary=True, "
                "found 0"
            )
        if len(primaries) > 1:
            raise ValueError(
                f"exactly one component must have is_primary=True, "
                f"found {len(primaries)}"
            )

        # Bounds must be exact envelope of components
        env_lower = min(c.lower_bound for c in self.components)
        env_upper = max(c.upper_bound for c in self.components)
        if self.lower_bound != env_lower:
            raise ValueError(
                f"lower_bound ({self.lower_bound}) must equal "
                f"min component lower_bound ({env_lower})"
            )
        if self.upper_bound != env_upper:
            raise ValueError(
                f"upper_bound ({self.upper_bound}) must equal "
                f"max component upper_bound ({env_upper})"
            )

        # status
        if not isinstance(self.status, ZoneStatus):
            raise TypeError(
                f"status must be a ZoneStatus, "
                f"got {type(self.status).__name__}"
            )

    @property
    def primary_component(self) -> ZoneComponent:
        """The single component with ``is_primary == True``."""
        for c in self.components:
            if c.is_primary:
                return c
        # unreachable after __post_init__ validation
        raise RuntimeError("no primary component")  # pragma: no cover

    @property
    def primary_level_price(self) -> float:
        """Price the retest must reach (spec §4.2)."""
        return self.primary_component.price

    def to_dict(self) -> dict:
        """Canonical JSON-compatible dict representation."""
        return {
            "zone_type": str(self.zone_type),
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "status": str(self.status),
            "components": [c.to_dict() for c in self.components],
        }
