"""Composite Confluence Zone builder.

Defined in MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §7.2.

Given a list of ZoneComponents from different providers (ORB, PDH/PDL,
Pivot/OB Wick, etc.), this module builds a COMPOSITE_CONFLUENCE_ZONE
when components are close enough to the designated primary level.

Algorithm (``build_confluence_zone`` — core geometric builder)
--------------------------------------------------------------
1. Validate inputs (tolerance first, then components, then primary).
2. Identify the single primary component (anchor).
3. For each secondary, compute ``abs(component.price - primary.price)``.
4. If distance <= tolerance + EPSILON → included in zone.
5. If no secondary qualifies → zone=None, all components in unmerged.
6. If at least one qualifies → build CompositeZone with primary first,
   then included secondaries sorted by price (stable on input order).
7. Excluded components returned as unmerged in original input order.

Operational builder (``build_operational_confluence`` — B8)
-----------------------------------------------------------
Wraps the core builder with two gates that must both pass:

1. **Overlap gate** — the operative windows of both levels
   (displacement_index..max_valid_index) must overlap in time.
2. **Distance gate** — abs(price_a - price_b) <= ATR_post_ORB *
   composite_atr_tolerance.

ATR is frozen at the end of the ORB and reused for the entire session.
Default coefficient: 0.75.

Design decisions
----------------
- Primary is the anchor: every secondary is evaluated independently
  against the primary.  No transitive chaining.
- Distance metric: ``abs(a.price - b.price)``.  Bounds do not
  participate in the inclusion criterion.
- Float-safe comparison: ``distance <= tolerance + EPSILON`` with
  EPSILON = 1e-12, matching the convention in pivot_cluster.py.
  This is a local helper — not imported from pivot_cluster.
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


# ── B8: Operational Confluence Builder ────────────────────────────────────────

DEFAULT_COMPOSITE_ATR_TOLERANCE = 0.75


def validate_composite_atr_tolerance(value: object) -> float:
    """Validate composite_atr_tolerance coefficient.

    Must be numeric (not bool), finite, >= 0.
    Converts int to float.  Rejects bool, str, NaN, inf, negative.
    """
    if isinstance(value, bool):
        raise TypeError(
            "composite_atr_tolerance must be a number, got bool"
        )
    if isinstance(value, int):
        value = float(value)
    if not isinstance(value, (float, int)):
        raise TypeError(
            f"composite_atr_tolerance must be a number, "
            f"got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise ValueError(
            f"composite_atr_tolerance must be finite, got {value!r}"
        )
    if value < 0:
        raise ValueError(
            f"composite_atr_tolerance must be >= 0, got {value!r}"
        )
    return float(value)


# ── Reason codes ──────────────────────────────────────────────────────────────

REASON_COMPOSITE_CREATED = "COMPOSITE_CREATED"
REASON_EXCLUDED_DISTANCE = "EXCLUDED_DISTANCE"
REASON_EXCLUDED_NO_OVERLAP = "EXCLUDED_NO_OVERLAP"
REASON_EXCLUDED_ATR_UNAVAILABLE = "EXCLUDED_ATR_UNAVAILABLE"


# ── Operational result contract ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OperationalConfluenceResult:
    """Result of building an operational composite confluence zone (B8).

    This wraps the core geometric builder with ATR-based tolerance
    and an overlap gate.

    Fields
    ------
    zone : CompositeZone | None
        The COMPOSITE_CONFLUENCE_ZONE if both gates pass, None otherwise.
    unmerged : tuple[ZoneComponent, ...]
        Components not included in the zone.
    reason : str
        One of the REASON_* constants.
    atr_post_orb : float | None
        The frozen ATR(14) value used.
    composite_atr_tolerance : float
        The coefficient used.
    atr_tolerance : float | None
        atr_post_orb * composite_atr_tolerance (the effective tolerance).
    distance : float | None
        abs(price_a - price_b).
    overlap_start_index : int | None
        max(displacement_index_a, displacement_index_b).
    overlap_end_index : int | None
        min(max_valid_index_a, max_valid_index_b).
    components_detail : tuple[dict, ...]
        Per-component diagnostic info (source, price, displacement_index,
        max_valid_index).
    """

    zone: CompositeZone | None
    unmerged: tuple[ZoneComponent, ...]
    reason: str
    atr_post_orb: float | None
    composite_atr_tolerance: float
    atr_tolerance: float | None
    distance: float | None
    overlap_start_index: int | None
    overlap_end_index: int | None
    components_detail: tuple[dict, ...]


# ── Operational builder ───────────────────────────────────────────────────────


def build_operational_confluence(
    components: list[ZoneComponent],
    displacement_indices: list[int],
    max_valid_indices: list[int],
    atr_post_orb: float | None,
    composite_atr_tolerance: float = DEFAULT_COMPOSITE_ATR_TOLERANCE,
) -> OperationalConfluenceResult:
    """Build a COMPOSITE_CONFLUENCE_ZONE with ATR tolerance and overlap gate.

    Parameters
    ----------
    components : list[ZoneComponent]
        Exactly two components.  Exactly one must have is_primary=True.
    displacement_indices : list[int]
        Displacement start index for each component (same order).
    max_valid_indices : list[int]
        Last valid candle index for each component (same order).
    atr_post_orb : float or None
        ATR(14) frozen at the end of the ORB.  None if unavailable.
    composite_atr_tolerance : float
        Coefficient for ATR-based tolerance.  Default 0.75.

    Returns
    -------
    OperationalConfluenceResult
    """
    # ── Validate coefficient ──────────────────────────────────────
    coeff = validate_composite_atr_tolerance(composite_atr_tolerance)

    # ── Validate components (reuse existing) ──────────────────────
    _primary_idx, primary = _validate_components(components)

    if len(components) != 2:
        raise ValueError(
            f"operational confluence requires exactly 2 components, "
            f"got {len(components)}"
        )
    if len(displacement_indices) != 2:
        raise ValueError(
            f"displacement_indices must have 2 elements, "
            f"got {len(displacement_indices)}"
        )
    if len(max_valid_indices) != 2:
        raise ValueError(
            f"max_valid_indices must have 2 elements, "
            f"got {len(max_valid_indices)}"
        )

    # Build per-component detail
    detail = tuple(
        {
            "source": components[i].source,
            "price": components[i].price,
            "is_primary": components[i].is_primary,
            "displacement_index": displacement_indices[i],
            "max_valid_index": max_valid_indices[i],
        }
        for i in range(2)
    )

    all_unmerged = tuple(components)

    # ── ATR gate ──────────────────────────────────────────────────
    if (atr_post_orb is None
            or not isinstance(atr_post_orb, (int, float))
            or isinstance(atr_post_orb, bool)
            or (isinstance(atr_post_orb, float)
                and not math.isfinite(atr_post_orb))
            or atr_post_orb <= 0):
        return OperationalConfluenceResult(
            zone=None,
            unmerged=all_unmerged,
            reason=REASON_EXCLUDED_ATR_UNAVAILABLE,
            atr_post_orb=atr_post_orb if isinstance(atr_post_orb, (int, float)) and not isinstance(atr_post_orb, bool) else None,
            composite_atr_tolerance=coeff,
            atr_tolerance=None,
            distance=None,
            overlap_start_index=None,
            overlap_end_index=None,
            components_detail=detail,
        )

    atr_val = float(atr_post_orb)
    atr_tol = atr_val * coeff

    # ── Overlap gate ──────────────────────────────────────────────
    overlap_start = max(displacement_indices[0], displacement_indices[1])
    overlap_end = min(max_valid_indices[0], max_valid_indices[1])

    distance = abs(components[0].price - components[1].price)

    if overlap_start > overlap_end:
        return OperationalConfluenceResult(
            zone=None,
            unmerged=all_unmerged,
            reason=REASON_EXCLUDED_NO_OVERLAP,
            atr_post_orb=atr_val,
            composite_atr_tolerance=coeff,
            atr_tolerance=atr_tol,
            distance=distance,
            overlap_start_index=overlap_start,
            overlap_end_index=overlap_end,
            components_detail=detail,
        )

    # ── Distance gate ─────────────────────────────────────────────
    if not _within_tolerance(distance, atr_tol):
        return OperationalConfluenceResult(
            zone=None,
            unmerged=all_unmerged,
            reason=REASON_EXCLUDED_DISTANCE,
            atr_post_orb=atr_val,
            composite_atr_tolerance=coeff,
            atr_tolerance=atr_tol,
            distance=distance,
            overlap_start_index=overlap_start,
            overlap_end_index=overlap_end,
            components_detail=detail,
        )

    # ── Both gates pass — delegate to core builder ────────────────
    core_result = build_confluence_zone(components, atr_tol)

    return OperationalConfluenceResult(
        zone=core_result.zone,
        unmerged=core_result.unmerged,
        reason=REASON_COMPOSITE_CREATED,
        atr_post_orb=atr_val,
        composite_atr_tolerance=coeff,
        atr_tolerance=atr_tol,
        distance=distance,
        overlap_start_index=overlap_start,
        overlap_end_index=overlap_end,
        components_detail=detail,
    )
