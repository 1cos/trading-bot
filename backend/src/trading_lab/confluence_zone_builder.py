"""Composite Confluence Zone builder.

Defined in MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §7.2.

Given a list of ZoneComponents from different providers (ORB, PDH/PDL,
Pivot/OB Wick, etc.), this module builds a COMPOSITE_CONFLUENCE_ZONE
when components are close enough to the designated primary level.

Algorithm
---------
1. Validate inputs (tolerance first, then components, then primary).
2. Identify the single primary component (anchor).
3. For each secondary, compute ``abs(component.price - primary.price)``.
4. If distance <= tolerance + EPSILON → included in zone.
5. If no secondary qualifies → zone=None, all components in unmerged.
6. If at least one qualifies → build CompositeZone with primary first,
   then included secondaries sorted by price (stable on input order).
7. Excluded components returned as unmerged in original input order.

Design decisions
----------------
- Primary is the anchor: every secondary is evaluated independently
  against the primary.  No transitive chaining.
- Distance metric: ``abs(a.price - b.price)``.  Bounds do not
  participate in the inclusion criterion.
- Float-safe comparison: ``distance <= tolerance + EPSILON`` with
  EPSILON = 1e-12, matching the convention in pivot_cluster.py.
  This is a local helper — not imported from pivot_cluster.
- Tolerance is OPEN per spec §18 — the caller must supply it.
- Status is hardcoded to ZoneStatus.ACTIVE (provisional, OPEN).
- Primary selection is the caller's responsibility.  The builder
  requires exactly one component with is_primary=True and never
  auto-selects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_lab.contracts.enums import ZoneStatus, ZoneType
from trading_lab.contracts.zone import CompositeZone, ZoneComponent


# ── Float-safe comparison ─────────────────────────────────────────────────────

_EPSILON = 1e-12


def _within_tolerance(distance: float, tolerance: float) -> bool:
    """True when *distance* is at most *tolerance*, float-safe."""
    return distance <= tolerance + _EPSILON


# ── Validation helpers ────────────────────────────────────────────────────────


def _validate_tolerance(value: object) -> float:
    """Validate and normalise tolerance.

    Raises TypeError for bool or non-numeric, ValueError for
    non-finite or negative.  Converts int to float.
    """
    if isinstance(value, bool):
        raise TypeError("tolerance must be a number, got bool")
    if isinstance(value, int):
        value = float(value)
    if not isinstance(value, float):
        raise TypeError(
            f"tolerance must be a number, "
            f"got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise ValueError(f"tolerance must be finite, got {value!r}")
    if value < 0:
        raise ValueError(f"tolerance must be >= 0, got {value!r}")
    return value


def _validate_components(
    components: object,
) -> tuple[int, ZoneComponent]:
    """Validate components list and find the primary.

    Returns (primary_index, primary_component).
    """
    if not isinstance(components, list):
        raise TypeError(
            f"components must be a list, "
            f"got {type(components).__name__}"
        )
    for i, c in enumerate(components):
        if not isinstance(c, ZoneComponent):
            raise TypeError(
                f"components[{i}] must be a ZoneComponent, "
                f"got {type(c).__name__}"
            )
    if len(components) == 0:
        raise ValueError(
            "components must not be empty (no primary found)"
        )

    primaries = [
        (i, c) for i, c in enumerate(components) if c.is_primary
    ]
    if len(primaries) == 0:
        raise ValueError(
            "exactly one component must have is_primary=True, found 0"
        )
    if len(primaries) > 1:
        raise ValueError(
            f"exactly one component must have is_primary=True, "
            f"found {len(primaries)}"
        )
    return primaries[0]


# ── Result contract ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConfluenceZoneResult:
    """Result of building a composite confluence zone.

    Fields
    ------
    zone : CompositeZone | None
        The COMPOSITE_CONFLUENCE_ZONE if at least one secondary
        component is within tolerance of the primary.  None
        otherwise.
    unmerged : tuple[ZoneComponent, ...]
        Components not included in the zone.  When zone is None,
        this contains all input components in their original order.
    tolerance : float
        The tolerance value that was used (normalised to float).
    """

    zone: CompositeZone | None
    unmerged: tuple[ZoneComponent, ...]
    tolerance: float

    def __post_init__(self) -> None:
        # ── zone ──────────────────────────────────────────────────
        if self.zone is not None:
            if not isinstance(self.zone, CompositeZone):
                raise TypeError(
                    f"zone must be a CompositeZone or None, "
                    f"got {type(self.zone).__name__}"
                )
            if len(self.zone.components) < 2:
                raise ValueError(
                    "zone must contain at least 2 components "
                    "(primary + at least one secondary), "
                    f"found {len(self.zone.components)}"
                )

        # ── unmerged ──────────────────────────────────────────────
        if not isinstance(self.unmerged, tuple):
            raise TypeError(
                f"unmerged must be a tuple, "
                f"got {type(self.unmerged).__name__}"
            )
        for i, c in enumerate(self.unmerged):
            if not isinstance(c, ZoneComponent):
                raise TypeError(
                    f"unmerged[{i}] must be a ZoneComponent, "
                    f"got {type(c).__name__}"
                )

        # ── tolerance ─────────────────────────────────────────────
        if isinstance(self.tolerance, bool):
            raise TypeError("tolerance must be a number, got bool")
        if not isinstance(self.tolerance, (int, float)):
            raise TypeError(
                f"tolerance must be a number, "
                f"got {type(self.tolerance).__name__}"
            )
        if isinstance(self.tolerance, int):
            object.__setattr__(self, "tolerance", float(self.tolerance))
        if not math.isfinite(self.tolerance):
            raise ValueError(
                f"tolerance must be finite, got {self.tolerance!r}"
            )
        if self.tolerance < 0:
            raise ValueError(
                f"tolerance must be >= 0, got {self.tolerance!r}"
            )

        # ── no overlap between zone components and unmerged ───────
        if self.zone is not None:
            zone_set = set(self.zone.components)
            unmerged_set = set(self.unmerged)
            overlap = zone_set & unmerged_set
            if overlap:
                raise ValueError(
                    f"components must not appear in both zone and "
                    f"unmerged, found {len(overlap)} overlap(s)"
                )


# ── Public API ────────────────────────────────────────────────────────────────


def build_confluence_zone(
    components: list[ZoneComponent],
    tolerance: float,
) -> ConfluenceZoneResult:
    """Build a COMPOSITE_CONFLUENCE_ZONE around a primary level.

    Parameters
    ----------
    components : list[ZoneComponent]
        Candidate components.  Exactly one must have
        ``is_primary=True`` — it serves as the anchor.
    tolerance : float
        Maximum price distance from the primary for a secondary
        to be included.  Must be >= 0, finite.  OPEN per spec §18.

    Returns
    -------
    ConfluenceZoneResult
        zone is present only when at least one secondary is
        within tolerance of the primary.

    Raises
    ------
    TypeError / ValueError
        See validation rules in module docstring.
    """
    # ── 1. Validate tolerance ─────────────────────────────────────
    tol = _validate_tolerance(tolerance)

    # ── 2–6. Validate components and find primary ─────────────────
    primary_idx, primary = _validate_components(components)

    # ── 7. Classify secondaries ───────────────────────────────────
    included: list[tuple[int, ZoneComponent]] = []
    excluded_indices: set[int] = set()

    for i, c in enumerate(components):
        if i == primary_idx:
            continue
        distance = abs(c.price - primary.price)
        if _within_tolerance(distance, tol):
            included.append((i, c))
        else:
            excluded_indices.add(i)

    # ── 8. No secondary qualifies → zone=None ─────────────────────
    if not included:
        return ConfluenceZoneResult(
            zone=None,
            unmerged=tuple(components),
            tolerance=tol,
        )

    # ── 9. Build zone components ──────────────────────────────────
    # Primary first, then secondaries sorted by price (stable on
    # original input index for equal prices).
    secondaries_sorted = sorted(
        included, key=lambda pair: (pair[1].price, pair[0])
    )
    zone_components = tuple(
        [primary] + [c for _, c in secondaries_sorted]
    )

    zone = CompositeZone(
        zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
        lower_bound=min(c.lower_bound for c in zone_components),
        upper_bound=max(c.upper_bound for c in zone_components),
        components=zone_components,
        status=ZoneStatus.ACTIVE,
    )

    # ── 10. Unmerged in original input order ──────────────────────
    unmerged = tuple(
        components[i] for i in range(len(components))
        if i in excluded_indices
    )

    return ConfluenceZoneResult(
        zone=zone,
        unmerged=unmerged,
        tolerance=tol,
    )
