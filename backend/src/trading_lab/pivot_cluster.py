"""Pivot clustering — group nearby ZoneComponents into VALIDATED_PIVOT_ZONE.

Defined in MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §7.1.

Given a list of ZoneComponents (typically from Pivot/OB Wick sources),
this module clusters components whose prices fall within a configurable
tolerance and returns a CompositeZone of type VALIDATED_PIVOT_ZONE
when the cluster has >= ``min_contacts`` members (FROZEN default: 3).

The module does NOT produce the input ZoneComponents — those come from
a Level Provider (e.g. a future Pivot/OB Wick provider).  This module
operates on generic ZoneComponents regardless of source label, so it
is usable as soon as any provider emits candidate levels.

Design decisions
----------------
- Clustering algorithm: single-pass greedy sort-and-sweep.  Components
  are sorted by price; consecutive components within ``tolerance`` of
  their predecessor belong to the same cluster.  This is deterministic
  and O(n log n).
- Primary selection: the component closest to the cluster median price
  is designated ``is_primary=True``.  Ties broken by earliest position
  in the input list (stable).  This is a heuristic — the caller or a
  downstream module (e.g. CompositeConfluenceZoneBuilder) may override
  primary selection when merging with structural levels.
- Tolerance is a **price distance** (same unit as the prices), NOT an
  ATR ratio.  The parameter is OPEN per §18 and must be supplied by
  the caller.  There is no hidden default.
- Components in the output are re-created with ``is_primary`` adjusted.
  The original ``is_primary`` flag on input components is ignored by
  the clustering logic.

Properties
----------
- Deterministic: same inputs → same outputs, always.
- Pure function: no side effects, no mutable state.
- Components with fewer than ``min_contacts`` neighbours are returned
  individually (not in a zone) so nothing is silently dropped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_lab.contracts.enums import ZoneStatus, ZoneType
from trading_lab.contracts.zone import CompositeZone, ZoneComponent


# ── Validation helpers ────────────────────────────────────────────────────────


def _require_finite_positive(value: object, name: str) -> float:
    """Require a finite numeric value > 0 (not bool)."""
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
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value!r}")
    return value


def _require_non_bool_int(value: object, name: str) -> int:
    """Require a real int >= 1, rejecting bool."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int, got {type(value).__name__}"
        )
    return value


# ── Cluster result ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PivotClusterResult:
    """Result of running pivot clustering on a list of ZoneComponents.

    Fields
    ------
    zones : tuple[CompositeZone, ...]
        VALIDATED_PIVOT_ZONE instances for clusters that met
        ``min_contacts``.  May be empty.
    unclustered : tuple[ZoneComponent, ...]
        Components that did not belong to any qualifying cluster.
        These are returned unchanged (original ``is_primary`` flag
        preserved) so nothing is silently discarded.
    tolerance : float
        The tolerance value that was used.
    min_contacts : int
        The minimum cluster size that was used.
    """

    zones: tuple[CompositeZone, ...]
    unclustered: tuple[ZoneComponent, ...]
    tolerance: float
    min_contacts: int

    def __post_init__(self) -> None:
        if not isinstance(self.zones, tuple):
            raise TypeError(
                f"zones must be a tuple, got {type(self.zones).__name__}"
            )
        for i, z in enumerate(self.zones):
            if not isinstance(z, CompositeZone):
                raise TypeError(
                    f"zones[{i}] must be a CompositeZone, "
                    f"got {type(z).__name__}"
                )
        if not isinstance(self.unclustered, tuple):
            raise TypeError(
                f"unclustered must be a tuple, "
                f"got {type(self.unclustered).__name__}"
            )
        for i, c in enumerate(self.unclustered):
            if not isinstance(c, ZoneComponent):
                raise TypeError(
                    f"unclustered[{i}] must be a ZoneComponent, "
                    f"got {type(c).__name__}"
                )


# ── Internal: build a CompositeZone from a cluster ────────────────────────────


def _select_primary_index(
    cluster: list[tuple[int, ZoneComponent]],
) -> int:
    """Return the index *within cluster* of the primary component.

    Strategy: component whose ``price`` is closest to the cluster
    median price.  Ties broken by original input order (lower
    ``original_index`` wins).
    """
    prices = sorted(c.price for _, c in cluster)
    n = len(prices)
    if n % 2 == 1:
        median = prices[n // 2]
    else:
        median = (prices[n // 2 - 1] + prices[n // 2]) / 2.0

    best_idx = 0
    best_dist = abs(cluster[0][1].price - median)
    best_orig = cluster[0][0]

    for i in range(1, len(cluster)):
        orig_idx, comp = cluster[i]
        dist = abs(comp.price - median)
        if dist < best_dist or (dist == best_dist and orig_idx < best_orig):
            best_idx = i
            best_dist = dist
            best_orig = orig_idx

    return best_idx


def _build_zone(
    cluster: list[tuple[int, ZoneComponent]],
) -> CompositeZone:
    """Build a VALIDATED_PIVOT_ZONE from a qualifying cluster."""
    primary_idx = _select_primary_index(cluster)

    components: list[ZoneComponent] = []
    for i, (_, comp) in enumerate(cluster):
        is_primary = (i == primary_idx)
        # Re-create component with correct is_primary flag
        components.append(
            ZoneComponent(
                source=comp.source,
                price=comp.price,
                lower_bound=comp.lower_bound,
                upper_bound=comp.upper_bound,
                is_primary=is_primary,
            )
        )

    env_lower = min(c.lower_bound for c in components)
    env_upper = max(c.upper_bound for c in components)

    return CompositeZone(
        zone_type=ZoneType.VALIDATED_PIVOT_ZONE,
        lower_bound=env_lower,
        upper_bound=env_upper,
        components=tuple(components),
        status=ZoneStatus.ACTIVE,
    )


# ── Public API ────────────────────────────────────────────────────────────────


def cluster_pivots(
    components: list[ZoneComponent],
    tolerance: float,
    min_contacts: int = 3,
) -> PivotClusterResult:
    """Cluster nearby ZoneComponents into VALIDATED_PIVOT_ZONE.

    Parameters
    ----------
    components : list[ZoneComponent]
        Candidate components to cluster.  May be empty.  Source
        labels are not filtered — the caller decides what to pass.
    tolerance : float
        Maximum price distance between consecutive sorted
        components for them to belong to the same cluster.
        Must be > 0.  This parameter is OPEN per spec §18;
        there is no hidden default.
    min_contacts : int
        Minimum number of components in a cluster for it to
        qualify as a VALIDATED_PIVOT_ZONE.  FROZEN default: 3
        (spec §7.1).

    Returns
    -------
    PivotClusterResult
        Contains qualifying zones and unclustered components.

    Raises
    ------
    TypeError
        If arguments have wrong types.
    ValueError
        If tolerance <= 0, min_contacts < 1, or components
        contains non-ZoneComponent elements.
    """
    # ── Validate parameters ───────────────────────────────────────────
    tol = _require_finite_positive(tolerance, "tolerance")
    mc = _require_non_bool_int(min_contacts, "min_contacts")
    if mc < 1:
        raise ValueError(f"min_contacts must be >= 1, got {mc}")

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

    # ── Empty input ───────────────────────────────────────────────────
    if len(components) == 0:
        return PivotClusterResult(
            zones=(),
            unclustered=(),
            tolerance=tol,
            min_contacts=mc,
        )

    # ── Sort by price, preserving original index for tie-breaking ─────
    indexed = list(enumerate(components))
    indexed.sort(key=lambda pair: pair[1].price)

    # ── Sweep: group consecutive components within tolerance ──────────
    clusters: list[list[tuple[int, ZoneComponent]]] = []
    current_cluster: list[tuple[int, ZoneComponent]] = [indexed[0]]

    for j in range(1, len(indexed)):
        prev_price = indexed[j - 1][1].price
        curr_price = indexed[j][1].price
        if curr_price - prev_price <= tol:
            current_cluster.append(indexed[j])
        else:
            clusters.append(current_cluster)
            current_cluster = [indexed[j]]
    clusters.append(current_cluster)

    # ── Separate qualifying clusters from unclustered ─────────────────
    zones: list[CompositeZone] = []
    unclustered: list[ZoneComponent] = []

    for cluster in clusters:
        if len(cluster) >= mc:
            zones.append(_build_zone(cluster))
        else:
            for _, comp in cluster:
                unclustered.append(comp)

    return PivotClusterResult(
        zones=tuple(zones),
        unclustered=tuple(unclustered),
        tolerance=tol,
        min_contacts=mc,
    )
