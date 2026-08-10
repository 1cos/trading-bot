"""Trade Space Grader — B10 standalone prototype.

Evaluates the structural space between a candidate trade's entry and
target using the rejection wall analysis from B9.  Assigns an
informational grade that describes obstacle density — it does NOT
modify trade eligibility, entry, stop, target, or outcome.

Grading rule (deliberately simple, provisional)
------------------------------------------------
    A       — No active structural wall between entry and target.
    B_PLUS  — Active wall(s) exist but nearest is farther than 1R.
    B       — Active wall(s) exist and nearest is at or within 1R.

The grade is descriptive only.  A B-graded trade is still valid and
may still reach target.  The grader uses only information available
at trade-entry evaluation time plus the B9 structural wall analysis.

This module consumes existing B9.4 ``WallSpaceResult`` output.
It does not rerun wall detection, classification, or space analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from trading_lab.rejection_wall_space import WallSpaceResult


# ── Grade enum ────────────────────────────────────────────────────────────────


@unique
class TradeSpaceGrade(StrEnum):
    """Structural space grade for a candidate trade.

    Provisional — B10 prototype.  Does not replace or modify the
    existing QualityGrade (§3.6).
    """

    A = "A"
    B_PLUS = "B_PLUS"
    B = "B"


# ── Reason codes ──────────────────────────────────────────────────────────────


@unique
class TradeSpaceReason(StrEnum):
    """Machine-readable reason for the assigned grade."""

    CLEAR_PATH_TO_TARGET = "CLEAR_PATH_TO_TARGET"
    ACTIVE_WALL_BEYOND_1R = "ACTIVE_WALL_BEYOND_1R"
    ACTIVE_WALL_WITHIN_1R = "ACTIVE_WALL_WITHIN_1R"


# ── Result contract ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TradeSpaceGradeResult:
    """Result of trade space grading.

    Attributes
    ----------
    grade : TradeSpaceGrade
        A, B_PLUS, or B.
    reason : TradeSpaceReason
        Deterministic machine-readable reason for the grade.
    active_wall_count : int
        Number of active walls between entry and target.
    nearest_wall_distance_ticks : int | None
        Distance in ticks from entry to the nearest active wall
        between entry and target, or None if no such wall exists.
    nearest_wall_distance_r : float | None
        Same distance expressed as a multiple of risk, or None.
    has_wall_within_1r : bool
        Whether any active wall between entry and target is
        at or within 1R from entry.
    """

    grade: TradeSpaceGrade
    reason: TradeSpaceReason
    active_wall_count: int
    nearest_wall_distance_ticks: int | None
    nearest_wall_distance_r: float | None
    has_wall_within_1r: bool

    def to_dict(self) -> dict:
        """JSON-serializable representation."""
        return {
            "grade": self.grade.value,
            "reason": self.reason.value,
            "active_wall_count": self.active_wall_count,
            "nearest_wall_distance_ticks": self.nearest_wall_distance_ticks,
            "nearest_wall_distance_r": self.nearest_wall_distance_r,
            "has_wall_within_1r": self.has_wall_within_1r,
        }


# ── Public API ────────────────────────────────────────────────────────────────


def grade_trade_space(space: WallSpaceResult) -> TradeSpaceGradeResult:
    """Grade a candidate trade's structural space.

    Consumes the output of ``analyze_rejection_wall_space`` (B9.4).
    Uses only the active-wall-between-entry-and-target metrics
    already computed by B9.4 — no recalculation.

    Parameters
    ----------
    space : WallSpaceResult
        B9.4 space analysis result for the trade.

    Returns
    -------
    TradeSpaceGradeResult
        Deterministic grade with reason and diagnostics.

    Raises
    ------
    TypeError
        If space is not a WallSpaceResult.
    """
    if not isinstance(space, WallSpaceResult):
        raise TypeError(
            f"space must be a WallSpaceResult, "
            f"got {type(space).__name__}"
        )

    active_between = space.active_between_entry_and_target

    if active_between == 0:
        return TradeSpaceGradeResult(
            grade=TradeSpaceGrade.A,
            reason=TradeSpaceReason.CLEAR_PATH_TO_TARGET,
            active_wall_count=0,
            nearest_wall_distance_ticks=None,
            nearest_wall_distance_r=None,
            has_wall_within_1r=False,
        )

    # At least one active wall between entry and target.
    # Use nearest_active_between from B9.4 for distance.
    nearest = space.nearest_active_between
    if nearest is not None:
        dist_ticks = nearest.distance_ticks
        dist_r = nearest.distance_r
    else:
        # Should not happen if active_between > 0, but defensive
        dist_ticks = None
        dist_r = None

    # B9.4 convention: is_within_1r = distance_ticks <= risk_ticks (i.e. ≤1R)
    # Check if any active wall between E/T is within 1R
    has_within_1r = any(
        aw.is_within_1r and aw.is_between_entry_and_target
        and aw.classified_wall.is_active
        for aw in space.walls
    )

    if has_within_1r:
        grade = TradeSpaceGrade.B
        reason = TradeSpaceReason.ACTIVE_WALL_WITHIN_1R
    else:
        grade = TradeSpaceGrade.B_PLUS
        reason = TradeSpaceReason.ACTIVE_WALL_BEYOND_1R

    return TradeSpaceGradeResult(
        grade=grade,
        reason=reason,
        active_wall_count=active_between,
        nearest_wall_distance_ticks=dist_ticks,
        nearest_wall_distance_r=dist_r,
        has_wall_within_1r=has_within_1r,
    )
