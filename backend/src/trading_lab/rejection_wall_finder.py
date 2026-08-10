"""Rejection Wall detector — B9.1 standalone structural detector.

Scans a window of candles for clustered directional extremes that form
Rejection Walls.  A wall is a price area where multiple candles pushed
to approximately the same extreme and failed to advance, indicating
structural resistance (LONG highs) or support (SHORT lows).

Contact model: HYBRID (calibrated from SPY 2026-08-06 §16)
-----------------------------------------------------------
A cluster qualifies as a Rejection Wall when:

    total contacts  >= min_contacts          (default 2)
    rejection contacts >= min_rejection_contacts  (default 1)

A *rejection contact* is a candle whose directional wick ratio meets
``min_rejection_wick_ratio`` (default 0.20).

    LONG: upper_wick / range >= threshold
    SHORT: lower_wick / range >= threshold

Non-rejection contacts (stalls, failed advances) may reinforce a
cluster, but a cluster with zero genuine rejection contacts does NOT
qualify.

Clustering
----------
Uses span-based tolerance consistent with B6 pivot_cluster:
the total spread of a cluster (max extreme - min extreme) must be
<= ``cluster_tolerance_ticks``.  Transitive chaining is prevented.

The algorithm sorts candidate extremes, finds all valid windows of
>= min_contacts points within the tolerance span, selects the best
window (most contacts, tightest spread), extracts it, and repeats
on the residuals.

Representative price: median of the contact extremes, following B6
pivot_cluster convention.

Directional symmetry
--------------------
LONG: contacts from candle highs, rejection from upper wick.
SHORT: contacts from candle lows, rejection from lower wick.
Exact mirror — no direction-specific logic paths.

Scan window
-----------
The detector receives explicit ``scan_start_index`` (inclusive) and
``scan_end_index`` (exclusive).  It does NOT assume any structural
semantics (ORB, break, displacement, entry).  The caller determines
the appropriate window.

Spatial bounds (optional)
-------------------------
``min_price_exclusive`` and ``max_price_exclusive`` allow the caller
to restrict which extremes are considered as candidates.  Extremes
at or outside these bounds are ignored.  Both are optional; omitting
them means all extremes in the scan window are candidates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_lab.contracts.enums import Direction

# ── Float-safe comparison (matching B6 pivot_cluster) ─────────────────────────

_EPSILON = 1e-12


def _spread_within_tolerance(spread: int, tolerance: int) -> bool:
    """True when integer tick *spread* is at most *tolerance*."""
    return spread <= tolerance


# ── Validation helpers ────────────────────────────────────────────────────────


def _require_int(value: object, name: str) -> int:
    """Require a real int, rejecting bool."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int, got {type(value).__name__}"
        )
    return value


def _require_non_negative_int(value: object, name: str) -> int:
    v = _require_int(value, name)
    if v < 0:
        raise ValueError(f"{name} must be >= 0, got {v}")
    return v


def _require_positive_int(value: object, name: str) -> int:
    v = _require_int(value, name)
    if v < 1:
        raise ValueError(f"{name} must be >= 1, got {v}")
    return v


def _require_float_ratio(value: object, name: str) -> float:
    """Require a finite float in [0.0, 1.0], rejecting bool."""
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
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{name} must be in [0.0, 1.0], got {value!r}")
    return value


# ── Result contracts ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WallContact:
    """One candle contributing to a Rejection Wall.

    Attributes
    ----------
    candle_index : int
        Index into the candle array passed to the detector.
    extreme_ticks : int
        The directional extreme in ticks (high for LONG, low for SHORT).
    rejection_wick_ratio : float
        Directional wick / candle range.  0.0 for zero-range candles.
    is_rejection : bool
        True when rejection_wick_ratio >= the configured threshold.
    """

    candle_index: int
    extreme_ticks: int
    rejection_wick_ratio: float
    is_rejection: bool


@dataclass(frozen=True, slots=True)
class RejectionWall:
    """A detected Rejection Wall — a cluster of nearby directional extremes.

    Attributes
    ----------
    lower_ticks : int
        Minimum extreme in the cluster (ticks).
    upper_ticks : int
        Maximum extreme in the cluster (ticks).
    representative_ticks : int
        Median extreme in the cluster (ticks).  Follows B6 convention:
        for even-count clusters, the lower of the two middle values.
    contacts : tuple[WallContact, ...]
        Contacts sorted by candle_index (temporal order).
    contact_count : int
        len(contacts).
    rejection_contact_count : int
        Number of contacts where is_rejection is True.
    """

    lower_ticks: int
    upper_ticks: int
    representative_ticks: int
    contacts: tuple[WallContact, ...]
    contact_count: int
    rejection_contact_count: int


@dataclass(frozen=True, slots=True)
class RejectionWallResult:
    """Result of rejection wall detection.

    Attributes
    ----------
    walls : tuple[RejectionWall, ...]
        Detected walls.  LONG: sorted ascending by representative_ticks
        (nearest wall first).  SHORT: sorted descending (nearest first).
    scan_start_index : int
        Inclusive start of the scan window.
    scan_end_index : int
        Exclusive end of the scan window.
    direction : Direction
        LONG or SHORT.
    """

    walls: tuple[RejectionWall, ...]
    scan_start_index: int
    scan_end_index: int
    direction: Direction


# ── Internal: extract candidate contacts ──────────────────────────────────────


def _extract_candidates(
    candles: list[dict],
    scan_start: int,
    scan_end: int,
    direction: Direction,
    tick_size: float,
    min_wick_ratio: float,
    min_price_exclusive: int | None,
    max_price_exclusive: int | None,
) -> list[WallContact]:
    """Extract candidate contacts from the scan window."""
    candidates: list[WallContact] = []

    for idx in range(scan_start, scan_end):
        c = candles[idx]
        h = c["high"]
        l = c["low"]
        o = c["open"]
        cl = c["close"]

        # Determine the directional extreme and wick
        if direction == Direction.LONG:
            extreme = h
            wick = h - max(o, cl)
        else:
            extreme = l
            wick = min(o, cl) - l

        # Convert to ticks for integer comparison
        extreme_ticks = round(extreme / tick_size)

        # Spatial bounds check
        if min_price_exclusive is not None and extreme_ticks <= min_price_exclusive:
            continue
        if max_price_exclusive is not None and extreme_ticks >= max_price_exclusive:
            continue

        # Wick ratio
        rng = h - l
        if rng <= 0:
            wick_ratio = 0.0
        else:
            wick_ratio = wick / rng

        is_rejection = wick_ratio >= min_wick_ratio - _EPSILON

        candidates.append(WallContact(
            candle_index=idx,
            extreme_ticks=extreme_ticks,
            rejection_wick_ratio=round(wick_ratio, 8),
            is_rejection=is_rejection,
        ))

    return candidates


# ── Internal: representative price (median, B6 convention) ────────────────────


def _median_ticks(values: list[int]) -> int:
    """Median of integer tick values.

    For even count, returns the lower of the two middle values.
    This matches B6 pivot_cluster's convention of selecting the
    component closest to the median and breaking ties toward lower
    original index / lower price.
    """
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    else:
        # Lower-median for even counts
        return s[n // 2 - 1]


# ── Internal: find best window (span-based, B6 style) ────────────────────────


def _find_best_window(
    sorted_contacts: list[WallContact],
    tolerance: int,
    min_contacts: int,
    min_rejection_contacts: int,
) -> tuple[int, int] | None:
    """Find the best consecutive window in sorted contacts.

    Returns (start, end) inclusive indices, or None.

    Selection: (a) most contacts, (b) smallest spread,
    (c) lowest start index.  Hybrid constraint: the window must
    contain >= min_rejection_contacts contacts with is_rejection=True.
    """
    n = len(sorted_contacts)
    if n < min_contacts:
        return None

    best: tuple[int, int] | None = None
    best_count = 0
    best_spread = float("inf")

    for start in range(n):
        for end in range(start + min_contacts - 1, n):
            spread = (sorted_contacts[end].extreme_ticks
                      - sorted_contacts[start].extreme_ticks)
            if not _spread_within_tolerance(spread, tolerance):
                break  # wider windows from this start will also fail

            count = end - start + 1

            # Check hybrid rejection requirement
            rej_count = sum(
                1 for i in range(start, end + 1)
                if sorted_contacts[i].is_rejection
            )
            if rej_count < min_rejection_contacts:
                continue

            if (count > best_count
                    or (count == best_count and spread < best_spread)):
                best = (start, end)
                best_count = count
                best_spread = spread

    return best


# ── Internal: build a RejectionWall from a window ─────────────────────────────


def _build_wall(contacts: list[WallContact]) -> RejectionWall:
    """Build a RejectionWall from a list of contacts."""
    extremes = [c.extreme_ticks for c in contacts]
    rej_count = sum(1 for c in contacts if c.is_rejection)

    # Sort contacts by candle_index (temporal order)
    sorted_contacts = sorted(contacts, key=lambda c: c.candle_index)

    return RejectionWall(
        lower_ticks=min(extremes),
        upper_ticks=max(extremes),
        representative_ticks=_median_ticks(extremes),
        contacts=tuple(sorted_contacts),
        contact_count=len(contacts),
        rejection_contact_count=rej_count,
    )


# ── Public API ────────────────────────────────────────────────────────────────


def find_rejection_walls(
    candles: list[dict],
    scan_start_index: int,
    scan_end_index: int,
    direction: Direction,
    tick_size: float,
    *,
    min_contacts: int = 2,
    min_rejection_contacts: int = 1,
    min_rejection_wick_ratio: float = 0.20,
    cluster_tolerance_ticks: int = 5,
    min_price_exclusive: int | None = None,
    max_price_exclusive: int | None = None,
) -> RejectionWallResult:
    """Detect Rejection Walls in a candle window.

    Parameters
    ----------
    candles : list[dict]
        Candle array with float OHLC keys: "open", "high", "low", "close".
    scan_start_index : int
        Inclusive start of the scan window.
    scan_end_index : int
        Exclusive end of the scan window.
    direction : Direction
        LONG scans highs for upper-wick rejection.
        SHORT scans lows for lower-wick rejection.
    tick_size : float
        Price increment for tick normalization.
    min_contacts : int
        Minimum contacts to form a wall (default 2).
    min_rejection_contacts : int
        Minimum contacts with genuine rejection wicks (default 1).
    min_rejection_wick_ratio : float
        Wick ratio threshold to qualify as a rejection (default 0.20).
    cluster_tolerance_ticks : int
        Max spread in ticks for a cluster (default 5).
    min_price_exclusive : int | None
        If set, only extremes > this tick value are candidates.
    max_price_exclusive : int | None
        If set, only extremes < this tick value are candidates.

    Returns
    -------
    RejectionWallResult

    Raises
    ------
    TypeError, ValueError
        On invalid parameters.
    """
    # ── Validate ──────────────────────────────────────────────────────
    if not isinstance(candles, list):
        raise TypeError(
            f"candles must be a list, got {type(candles).__name__}"
        )
    if not isinstance(direction, Direction):
        raise TypeError(
            f"direction must be a Direction, got {type(direction).__name__}"
        )

    scan_start = _require_non_negative_int(scan_start_index, "scan_start_index")
    scan_end = _require_non_negative_int(scan_end_index, "scan_end_index")
    if scan_end < scan_start:
        raise ValueError(
            f"scan_end_index ({scan_end}) must be >= "
            f"scan_start_index ({scan_start})"
        )

    mc = _require_positive_int(min_contacts, "min_contacts")
    mrc = _require_positive_int(min_rejection_contacts, "min_rejection_contacts")
    if mrc > mc:
        raise ValueError(
            f"min_rejection_contacts ({mrc}) must be <= "
            f"min_contacts ({mc})"
        )

    wr = _require_float_ratio(min_rejection_wick_ratio, "min_rejection_wick_ratio")
    tol = _require_positive_int(cluster_tolerance_ticks, "cluster_tolerance_ticks")

    if min_price_exclusive is not None:
        _require_int(min_price_exclusive, "min_price_exclusive")
    if max_price_exclusive is not None:
        _require_int(max_price_exclusive, "max_price_exclusive")

    if isinstance(tick_size, bool) or not isinstance(tick_size, (int, float)):
        raise TypeError(
            f"tick_size must be a number, got {type(tick_size).__name__}"
        )
    if tick_size <= 0:
        raise ValueError(f"tick_size must be > 0, got {tick_size!r}")

    # ── Extract candidates ────────────────────────────────────────────
    candidates = _extract_candidates(
        candles, scan_start, scan_end, direction, tick_size, wr,
        min_price_exclusive, max_price_exclusive,
    )

    # ── Iterative clustering (B6 style) ───────────────────────────────
    # Pool: list of WallContact, re-sorted by extreme_ticks each round.
    pool = list(candidates)
    walls: list[RejectionWall] = []

    while True:
        pool.sort(key=lambda c: c.extreme_ticks)

        window = _find_best_window(pool, tol, mc, mrc)
        if window is None:
            break

        start, end = window
        cluster = pool[start:end + 1]
        walls.append(_build_wall(cluster))

        consumed = set(range(start, end + 1))
        pool = [p for i, p in enumerate(pool) if i not in consumed]

    # ── Sort walls by direction ───────────────────────────────────────
    if direction == Direction.LONG:
        walls.sort(key=lambda w: w.representative_ticks)
    else:
        walls.sort(key=lambda w: w.representative_ticks, reverse=True)

    return RejectionWallResult(
        walls=tuple(walls),
        scan_start_index=scan_start,
        scan_end_index=scan_end,
        direction=direction,
    )
