"""Pivot clustering — group nearby ZoneComponents into VALIDATED_PIVOT_ZONE.

Defined in MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §7.1.

Given a list of ZoneComponents (typically from Pivot/OB Wick sources),
this module clusters components whose prices fall within a configurable
tolerance and returns CompositeZone instances of type
VALIDATED_PIVOT_ZONE when the cluster has >= ``min_contacts`` members
(FROZEN default: 3).

The module does NOT produce the input ZoneComponents — those come from
a Level Provider (e.g. a future Pivot/OB Wick provider).  This module
operates on generic ZoneComponents regardless of source label, so it
is usable as soon as any provider emits candidate levels.

Algorithm
---------
1. Sort components by price (stable).
2. Find all consecutive windows of length >= ``min_contacts`` whose
   spread (``max(price) - min(price)``) is <= ``tolerance``.
3. Select the best window by: (a) most contacts, (b) smallest spread,
   (c) lowest start index in the sorted array (tie-breaker, no
   strategic meaning).
4. Create a zone from that window.
5. Remove consumed components from the pool.
6. Re-run globally on residuals (re-sort, re-scan).
7. Repeat until no valid windows remain.
8. Return remaining components as unclustered.

Transitive chaining is prevented: the tolerance constrains the total
spread of each zone, not just pairwise distances.

Design decisions
----------------
- Primary selection: OPEN — provisional.  The component whose price
  is closest to the cluster median is designated ``is_primary=True``.
  Ties broken by earliest position in the original input list (stable
  tie-breaker, no strategic meaning).  This does NOT establish a
  retest validation rule.  It exists solely to satisfy the
  ``CompositeZone`` contract invariant (exactly one primary).
  Subject to override when primary selection criteria are approved.
- Tolerance is a **price distance** (same unit as the prices), NOT an
  ATR ratio.  The parameter is OPEN per §18 and must be supplied by
  the caller.  There is no hidden default.
- ``status`` is an explicit parameter.  Default ``ZoneStatus.ACTIVE``
  is provisional and OPEN — the spec does not prescribe the initial
  status of a freshly clustered zone.  The caller should set the
  appropriate status based on session context.
- Components in the output are re-created with ``is_primary`` adjusted.
  The original ``is_primary`` flag on input components is ignored by
  the clustering logic.

Floating-point safety
---------------------
Spread comparisons use a centralised ``_spread_within_tolerance``
function that adds a minimal epsilon (1e-12) to the tolerance before
comparing.  This prevents mathematically-equal spreads from being
rejected due to IEEE 754 representation error, without introducing
a strategically meaningful additional tolerance.

Properties
----------
- Deterministic: same inputs → same outputs, always.
- Pure function: no side effects, no mutable state.
- Components that do not belong to any qualifying cluster are returned
  as unclustered so nothing is silently dropped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_lab.contracts.enums import ZoneStatus, ZoneType
from trading_lab.contracts.zone import CompositeZone, ZoneComponent


# ── Float-safe comparison ─────────────────────────────────────────────────────

_EPSILON = 1e-12


def _spread_within_tolerance(spread: float, tolerance: float) -> bool:
    """True when *spread* is at most *tolerance*, float-safe.

    Adds a minimal epsilon to tolerance so that a spread that is
    mathematically equal but IEEE-754-larger is not rejected.
    """
    return spread <= tolerance + _EPSILON


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
    """Require a real int, rejecting bool."""
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


# ── Internal: primary selection ───────────────────────────────────────────────


def _select_primary_index(
    cluster: list[tuple[int, ZoneComponent]],
) -> int:
    """Return the index *within cluster* of the primary component.

    OPEN — provisional selection to satisfy B1 contract invariant.
    Does NOT establish a retest validation rule.  Subject to override
    when primary selection criteria are approved.

    Strategy: component whose ``price`` is closest to the cluster
    median price.  Ties broken by original input order (lower
    ``original_index`` wins — stable, no strategic meaning).
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


# ── Internal: build a CompositeZone from a cluster ────────────────────────────


def _build_zone(
    cluster: list[tuple[int, ZoneComponent]],
    status: ZoneStatus,
) -> CompositeZone:
    """Build a VALIDATED_PIVOT_ZONE from a qualifying cluster."""
    primary_idx = _select_primary_index(cluster)

    components: list[ZoneComponent] = []
    for i, (_, comp) in enumerate(cluster):
        is_primary = (i == primary_idx)
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
        status=status,
    )


# ── Internal: find the best valid window in a sorted list ─────────────────────


def _find_best_window(
    sorted_items: list[tuple[int, ZoneComponent]],
    tolerance: float,
    min_contacts: int,
) -> tuple[int, int] | None:
    """Find the best consecutive window in *sorted_items*.

    Returns (start, end) inclusive indices into *sorted_items*,
    or None if no valid window exists.

    Selection criteria (in order):
    (a) most contacts (largest window);
    (b) smallest spread;
    (c) lowest start index (tie-breaker, no strategic meaning).
    """
    n = len(sorted_items)
    if n < min_contacts:
        return None

    best: tuple[int, int] | None = None
    best_count = 0
    best_spread = float("inf")

    for start in range(n):
        for end in range(start + min_contacts - 1, n):
            spread = sorted_items[end][1].price - sorted_items[start][1].price
            count = end - start + 1

            if not _spread_within_tolerance(spread, tolerance):
                # All wider windows from this start will also fail
                break

            # Check if this window is better
            if (count > best_count
                    or (count == best_count and spread < best_spread)):
                best = (start, end)
                best_count = count
                best_spread = spread
            # Criterion (c) — lowest start index — is satisfied
            # automatically: we iterate start from 0 upward, so the
            # first window found at a given (count, spread) wins.

    return best


# ── Public API ────────────────────────────────────────────────────────────────


def cluster_pivots(
    components: list[ZoneComponent],
    tolerance: float,
    min_contacts: int = 3,
    status: ZoneStatus = ZoneStatus.ACTIVE,
) -> PivotClusterResult:
    """Cluster nearby ZoneComponents into VALIDATED_PIVOT_ZONE.

    Parameters
    ----------
    components : list[ZoneComponent]
        Candidate components to cluster.  May be empty.  Source
        labels are not filtered — the caller decides what to pass.
    tolerance : float
        Maximum total spread (``max(price) - min(price)``) allowed
        within a single cluster.  Must be > 0.  This parameter is
        OPEN per spec §18; there is no hidden default.
    min_contacts : int
        Minimum number of components in a cluster for it to
        qualify as a VALIDATED_PIVOT_ZONE.  FROZEN default: 3
        (spec §7.1).
    status : ZoneStatus
        Lifecycle status assigned to each produced zone.
        Default ``ZoneStatus.ACTIVE`` is provisional and OPEN —
        the spec does not prescribe the initial status of a freshly
        clustered zone.  The caller should set the appropriate
        status based on session context (e.g. whether a break with
        displacement has occurred).

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

    if not isinstance(status, ZoneStatus):
        raise TypeError(
            f"status must be a ZoneStatus, "
            f"got {type(status).__name__}"
        )

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

    # ── Iterative extraction ──────────────────────────────────────────
    # Pool: (original_index, component) pairs — re-sorted each round.
    pool: list[tuple[int, ZoneComponent]] = list(enumerate(components))
    zones: list[CompositeZone] = []

    while True:
        # Sort pool by price (stable — preserves original index order
        # for equal prices).
        pool.sort(key=lambda pair: pair[1].price)

        window = _find_best_window(pool, tol, mc)
        if window is None:
            break

        start, end = window
        cluster = pool[start:end + 1]
        zones.append(_build_zone(cluster, status))

        # Remove consumed components from pool
        consumed = set(range(start, end + 1))
        pool = [p for i, p in enumerate(pool) if i not in consumed]

    # ── Unclustered: whatever remains in pool ─────────────────────────
    unclustered = tuple(comp for _, comp in pool)

    return PivotClusterResult(
        zones=tuple(zones),
        unclustered=unclustered,
        tolerance=tol,
        min_contacts=mc,
    )
