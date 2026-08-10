"""Rejection Wall space analysis — B9.4 standalone trade-space diagnostics.

Measures the geometric relationship between each classified Rejection Wall
and a trade's entry, stop, and target prices.  Produces purely quantitative
diagnostics — no grading, filtering, or strategic decisions.

Conventions
-----------
Near bound: the wall edge first encountered from entry in the trade direction.
    LONG:  wall.lower_ticks  (price rises from entry toward the wall's bottom)
    SHORT: wall.upper_ticks  (price falls from entry toward the wall's top)

Distance: signed ticks from entry to near bound.
    Positive = wall is ahead of entry in the trade direction.
    Zero = wall edge coincides with entry.
    Negative = wall is behind entry.

Distance R: distance_ticks / risk_ticks.
    risk_ticks = abs(entry_ticks - stop_ticks), always > 0.

Geometry status: classifies each active wall relative to entry and target.
    BEHIND_ENTRY / AT_ENTRY / BETWEEN_ENTRY_AND_TARGET / AT_TARGET / BEYOND_TARGET.

Only walls with status ACTIVE_UNBROKEN are counted as obstacles in the
aggregate metrics.  Inactive walls remain in the output for inspection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum, unique

from trading_lab.contracts.enums import Direction
from trading_lab.rejection_wall_classifier import (
    ClassifiedWall,
    WallActivityStatus,
)


# ── Enums ─────────────────────────────────────────────────────────────────────


@unique
class WallGeometryStatus(StrEnum):
    """Geometric position of a wall relative to entry and target."""

    BEHIND_ENTRY = "BEHIND_ENTRY"
    AT_ENTRY = "AT_ENTRY"
    BETWEEN_ENTRY_AND_TARGET = "BETWEEN_ENTRY_AND_TARGET"
    AT_TARGET = "AT_TARGET"
    BEYOND_TARGET = "BEYOND_TARGET"


# ── Per-wall result ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AnalyzedWall:
    """A classified wall with geometric distance measurements.

    Attributes
    ----------
    classified_wall : ClassifiedWall
        The B9.3 classified wall, unmodified.
    geometry : WallGeometryStatus
        Position relative to entry and target.
    near_bound_ticks : int
        Wall edge first encountered from entry (lower for LONG, upper for SHORT).
    far_bound_ticks : int
        Opposite wall edge.
    entry_ticks : int
        Trade entry price in ticks.
    stop_ticks : int
        Trade stop price in ticks.
    target_ticks : int
        Trade target price in ticks.
    distance_ticks : int
        Signed distance from entry to near bound.
    risk_ticks : int
        abs(entry - stop), always > 0.
    distance_r : float
        distance_ticks / risk_ticks.
    is_ahead : bool
        True if near bound is strictly ahead of entry in trade direction.
    is_between_entry_and_target : bool
        geometry == BETWEEN_ENTRY_AND_TARGET.
    is_within_1r : bool
        is_ahead and 0 < distance_ticks <= risk_ticks.
    """

    classified_wall: ClassifiedWall
    geometry: WallGeometryStatus
    near_bound_ticks: int
    far_bound_ticks: int
    entry_ticks: int
    stop_ticks: int
    target_ticks: int
    distance_ticks: int
    risk_ticks: int
    distance_r: float
    is_ahead: bool
    is_between_entry_and_target: bool
    is_within_1r: bool

    def to_dict(self) -> dict:
        """JSON-serializable representation."""
        return {
            "classified_wall": self.classified_wall.to_dict(),
            "geometry": self.geometry.value,
            "near_bound_ticks": self.near_bound_ticks,
            "far_bound_ticks": self.far_bound_ticks,
            "entry_ticks": self.entry_ticks,
            "stop_ticks": self.stop_ticks,
            "target_ticks": self.target_ticks,
            "distance_ticks": self.distance_ticks,
            "risk_ticks": self.risk_ticks,
            "distance_r": self.distance_r,
            "is_ahead": self.is_ahead,
            "is_between_entry_and_target": self.is_between_entry_and_target,
            "is_within_1r": self.is_within_1r,
        }


# ── Aggregate result ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WallSpaceResult:
    """Aggregate space analysis for all walls relative to a trade.

    Attributes
    ----------
    walls : tuple[AnalyzedWall, ...]
        All walls analyzed, in input order.
    total_walls : int
        Total structural walls received.
    active_wall_count : int
        Walls with ACTIVE_UNBROKEN status.
    active_between_entry_and_target : int
        Active walls geometrically between entry and target (exclusive).
    nearest_active_ahead : AnalyzedWall | None
        Active wall with smallest positive distance from entry.
    nearest_active_between : AnalyzedWall | None
        Active wall between entry and target with smallest positive distance.
    nearest_distance_ticks : int | None
        Smallest positive distance among active-ahead walls.
    nearest_distance_r : float | None
        Corresponding R multiple.
    active_within_1r : int
        Active walls ahead with distance <= 1R.
    active_between_1r_and_target : int
        Active walls between entry and target with distance > 1R.
    has_active_within_1r : bool
        Whether any active wall is within 1R ahead.
    target_clear : bool
        True if no active wall is between entry and target.
    direction : Direction
    entry_ticks : int
    stop_ticks : int
    target_ticks : int
    risk_ticks : int
    """

    walls: tuple[AnalyzedWall, ...]
    total_walls: int
    active_wall_count: int
    active_between_entry_and_target: int
    nearest_active_ahead: AnalyzedWall | None
    nearest_active_between: AnalyzedWall | None
    nearest_distance_ticks: int | None
    nearest_distance_r: float | None
    active_within_1r: int
    active_between_1r_and_target: int
    has_active_within_1r: bool
    target_clear: bool
    direction: Direction
    entry_ticks: int
    stop_ticks: int
    target_ticks: int
    risk_ticks: int

    def to_dict(self) -> dict:
        """JSON-serializable representation."""
        return {
            "walls": [w.to_dict() for w in self.walls],
            "total_walls": self.total_walls,
            "active_wall_count": self.active_wall_count,
            "active_between_entry_and_target": self.active_between_entry_and_target,
            "nearest_active_ahead": (
                self.nearest_active_ahead.to_dict()
                if self.nearest_active_ahead is not None
                else None
            ),
            "nearest_active_between": (
                self.nearest_active_between.to_dict()
                if self.nearest_active_between is not None
                else None
            ),
            "nearest_distance_ticks": self.nearest_distance_ticks,
            "nearest_distance_r": self.nearest_distance_r,
            "active_within_1r": self.active_within_1r,
            "active_between_1r_and_target": self.active_between_1r_and_target,
            "has_active_within_1r": self.has_active_within_1r,
            "target_clear": self.target_clear,
            "direction": self.direction.value,
            "entry_ticks": self.entry_ticks,
            "stop_ticks": self.stop_ticks,
            "target_ticks": self.target_ticks,
            "risk_ticks": self.risk_ticks,
        }


# ── Validation ────────────────────────────────────────────────────────────────


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return value


# ── Internal: analyze one wall ────────────────────────────────────────────────


def _analyze_one(
    cw: ClassifiedWall,
    direction: Direction,
    entry_ticks: int,
    stop_ticks: int,
    target_ticks: int,
    risk_ticks: int,
) -> AnalyzedWall:
    wall = cw.wall

    if direction == Direction.LONG:
        near_bound = wall.lower_ticks
        far_bound = wall.upper_ticks
        distance = near_bound - entry_ticks
    else:
        near_bound = wall.upper_ticks
        far_bound = wall.lower_ticks
        distance = entry_ticks - near_bound

    distance_r = distance / risk_ticks

    is_ahead = distance > 0

    # Geometry classification using near_bound vs entry/target
    if direction == Direction.LONG:
        if near_bound < entry_ticks:
            geometry = WallGeometryStatus.BEHIND_ENTRY
        elif near_bound == entry_ticks:
            geometry = WallGeometryStatus.AT_ENTRY
        elif near_bound == target_ticks:
            geometry = WallGeometryStatus.AT_TARGET
        elif entry_ticks < near_bound < target_ticks:
            geometry = WallGeometryStatus.BETWEEN_ENTRY_AND_TARGET
        else:
            geometry = WallGeometryStatus.BEYOND_TARGET
    else:
        if near_bound > entry_ticks:
            geometry = WallGeometryStatus.BEHIND_ENTRY
        elif near_bound == entry_ticks:
            geometry = WallGeometryStatus.AT_ENTRY
        elif near_bound == target_ticks:
            geometry = WallGeometryStatus.AT_TARGET
        elif target_ticks < near_bound < entry_ticks:
            geometry = WallGeometryStatus.BETWEEN_ENTRY_AND_TARGET
        else:
            geometry = WallGeometryStatus.BEYOND_TARGET

    is_between = geometry == WallGeometryStatus.BETWEEN_ENTRY_AND_TARGET
    is_within_1r = is_ahead and 0 < distance <= risk_ticks

    return AnalyzedWall(
        classified_wall=cw,
        geometry=geometry,
        near_bound_ticks=near_bound,
        far_bound_ticks=far_bound,
        entry_ticks=entry_ticks,
        stop_ticks=stop_ticks,
        target_ticks=target_ticks,
        distance_ticks=distance,
        risk_ticks=risk_ticks,
        distance_r=distance_r,
        is_ahead=is_ahead,
        is_between_entry_and_target=is_between,
        is_within_1r=is_within_1r,
    )


# ── Public API ────────────────────────────────────────────────────────────────


def analyze_rejection_wall_space(
    classified_walls: tuple[ClassifiedWall, ...] | list[ClassifiedWall],
    direction: Direction,
    entry_ticks: int,
    stop_ticks: int,
    target_ticks: int,
) -> WallSpaceResult:
    """Measure geometric distance from trade entry to each rejection wall.

    Parameters
    ----------
    classified_walls : tuple or list of ClassifiedWall
        Walls from B9.3 ``classify_active_rejection_walls``.
    direction : Direction
        LONG or SHORT.
    entry_ticks, stop_ticks, target_ticks : int
        Trade prices in integer ticks.

    Returns
    -------
    WallSpaceResult
        Per-wall and aggregate space diagnostics.

    Raises
    ------
    TypeError
        On invalid argument types (including bool-as-int).
    ValueError
        On risk == 0, stop/target on wrong side, etc.
    """
    # ── Validate ──────────────────────────────────────────────────────
    if not isinstance(direction, Direction):
        raise TypeError(
            f"direction must be a Direction, got {type(direction).__name__}"
        )

    et = _require_int(entry_ticks, "entry_ticks")
    st = _require_int(stop_ticks, "stop_ticks")
    tt = _require_int(target_ticks, "target_ticks")

    if direction == Direction.LONG:
        if st >= et:
            raise ValueError(
                f"LONG stop_ticks ({st}) must be < entry_ticks ({et})"
            )
        if tt <= et:
            raise ValueError(
                f"LONG target_ticks ({tt}) must be > entry_ticks ({et})"
            )
    else:
        if st <= et:
            raise ValueError(
                f"SHORT stop_ticks ({st}) must be > entry_ticks ({et})"
            )
        if tt >= et:
            raise ValueError(
                f"SHORT target_ticks ({tt}) must be < entry_ticks ({et})"
            )

    risk = abs(et - st)
    if risk == 0:
        raise ValueError("risk_ticks must be > 0 (entry == stop)")

    for i, cw in enumerate(classified_walls):
        if not isinstance(cw, ClassifiedWall):
            raise TypeError(
                f"classified_walls[{i}] must be a ClassifiedWall, "
                f"got {type(cw).__name__}"
            )

    # ── Analyze each wall ─────────────────────────────────────────────
    analyzed: list[AnalyzedWall] = []
    for cw in classified_walls:
        analyzed.append(_analyze_one(cw, direction, et, st, tt, risk))

    # ── Aggregate metrics (active walls only) ─────────────────────────
    active_walls = [a for a in analyzed if a.classified_wall.is_active]
    active_ahead = [a for a in active_walls if a.is_ahead]
    active_between = [a for a in active_walls if a.is_between_entry_and_target]
    active_1r = [a for a in active_walls if a.is_within_1r]
    active_between_1r_and_target = [
        a for a in active_between if a.distance_ticks > risk
    ]

    nearest_ahead: AnalyzedWall | None = None
    if active_ahead:
        nearest_ahead = min(active_ahead, key=lambda a: a.distance_ticks)

    nearest_between: AnalyzedWall | None = None
    if active_between:
        nearest_between = min(active_between, key=lambda a: a.distance_ticks)

    return WallSpaceResult(
        walls=tuple(analyzed),
        total_walls=len(analyzed),
        active_wall_count=len(active_walls),
        active_between_entry_and_target=len(active_between),
        nearest_active_ahead=nearest_ahead,
        nearest_active_between=nearest_between,
        nearest_distance_ticks=(
            nearest_ahead.distance_ticks if nearest_ahead else None
        ),
        nearest_distance_r=(
            nearest_ahead.distance_r if nearest_ahead else None
        ),
        active_within_1r=len(active_1r),
        active_between_1r_and_target=len(active_between_1r_and_target),
        has_active_within_1r=len(active_1r) > 0,
        target_clear=len(active_between) == 0,
        direction=direction,
        entry_ticks=et,
        stop_ticks=st,
        target_ticks=tt,
        risk_ticks=risk,
    )
