"""Active Rejection Wall classifier — B9.3 standalone temporal classifier.

Determines, for each Rejection Wall detected by B9.1, whether the wall
is still ACTIVE at a specific entry index.

Provisional active-wall acceptance rule
---------------------------------------
A wall is classified as INACTIVE_ACCEPTED when at least one candle
between the wall's last contact and the entry bar closed beyond the
wall's bound on the favorable side:

    LONG:  close > wall.upper_ticks  (in tick-normalized comparison)
    SHORT: close < wall.lower_ticks

The comparison is strict: a close exactly at the bound does NOT break
the wall.

Only candles in the exclusive interval (last_contact_index, entry_index)
are considered.  The entry candle itself is excluded because it cannot
classify an obstacle that existed before entry.

This rule is PROVISIONAL — calibrated from a single documented case
(SPY 2026-08-06 §16).  It must not be described as frozen or normative
until validated on additional real-data cases.

Separation of concerns
----------------------
Detection (B9.1) and classification (B9.3) are intentionally separate.
A wall's structural identity is permanent; its activity status is
relative to a specific entry index and direction.  The same wall may be
active for one entry and inactive for a later re-entry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum, unique

from trading_lab.contracts.enums import Direction
from trading_lab.rejection_wall_finder import RejectionWall


# ── Status enum ───────────────────────────────────────────────────────────────


@unique
class WallActivityStatus(StrEnum):
    """Provisional activity classification for a Rejection Wall."""

    ACTIVE_UNBROKEN = "ACTIVE_UNBROKEN"
    INACTIVE_ACCEPTED = "INACTIVE_ACCEPTED"


# ── Result contract ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ClassifiedWall:
    """A Rejection Wall annotated with its activity status at entry time.

    Attributes
    ----------
    wall : RejectionWall
        The original structural wall from B9.1, unmodified.
    status : WallActivityStatus
        ACTIVE_UNBROKEN or INACTIVE_ACCEPTED.
    is_active : bool
        Convenience: status == ACTIVE_UNBROKEN.
    last_contact_index : int
        max(c.candle_index for c in wall.contacts).
    entry_index : int
        The entry bar index used for classification.
    direction : Direction
        LONG or SHORT.
    bound_compared : int
        The wall bound used for the acceptance check (ticks).
        LONG: wall.upper_ticks.  SHORT: wall.lower_ticks.
    acceptance_index : int | None
        Index of the first candle that closed beyond the bound,
        or None if the wall is active.
    acceptance_close_ticks : int | None
        Close price (in ticks) of the acceptance candle,
        or None if the wall is active.
    """

    wall: RejectionWall
    status: WallActivityStatus
    is_active: bool
    last_contact_index: int
    entry_index: int
    direction: Direction
    bound_compared: int
    acceptance_index: int | None
    acceptance_close_ticks: int | None

    def to_dict(self) -> dict:
        """JSON-serializable representation."""
        return {
            "wall": {
                "lower_ticks": self.wall.lower_ticks,
                "upper_ticks": self.wall.upper_ticks,
                "representative_ticks": self.wall.representative_ticks,
                "contact_count": self.wall.contact_count,
                "rejection_contact_count": self.wall.rejection_contact_count,
                "contacts": [
                    {
                        "candle_index": c.candle_index,
                        "extreme_ticks": c.extreme_ticks,
                        "rejection_wick_ratio": c.rejection_wick_ratio,
                        "is_rejection": c.is_rejection,
                    }
                    for c in self.wall.contacts
                ],
            },
            "status": self.status.value,
            "is_active": self.is_active,
            "last_contact_index": self.last_contact_index,
            "entry_index": self.entry_index,
            "direction": self.direction.value,
            "bound_compared": self.bound_compared,
            "acceptance_index": self.acceptance_index,
            "acceptance_close_ticks": self.acceptance_close_ticks,
        }


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


# ── Internal: classify one wall ───────────────────────────────────────────────


def _classify_one(
    wall: RejectionWall,
    candles: list[dict],
    entry_index: int,
    direction: Direction,
    tick_size: float,
) -> ClassifiedWall:
    """Classify a single wall as active or inactive."""
    if not wall.contacts:
        raise ValueError("Wall has no contacts")

    last_contact_index = max(c.candle_index for c in wall.contacts)

    if last_contact_index >= entry_index:
        raise ValueError(
            f"last_contact_index ({last_contact_index}) must be < "
            f"entry_index ({entry_index})"
        )

    # Determine the bound and comparison direction
    if direction == Direction.LONG:
        bound_ticks = wall.upper_ticks
    else:
        bound_ticks = wall.lower_ticks

    # Scan the exclusive interval (last_contact_index, entry_index)
    acceptance_index: int | None = None
    acceptance_close_ticks: int | None = None

    for i in range(last_contact_index + 1, entry_index):
        close = candles[i]["close"]

        # Validate close before tick conversion
        if not isinstance(close, (int, float)):
            raise TypeError(
                f"candles[{i}]['close'] must be a number, "
                f"got {type(close).__name__}"
            )
        if isinstance(close, float) and (
            math.isnan(close) or math.isinf(close)
        ):
            raise ValueError(
                f"candles[{i}]['close'] must be finite, got {close!r}"
            )

        close_ticks = round(close / tick_size)

        # Strict comparison: close must be STRICTLY beyond the bound
        if direction == Direction.LONG:
            accepted = close_ticks > bound_ticks
        else:
            accepted = close_ticks < bound_ticks

        if accepted:
            acceptance_index = i
            acceptance_close_ticks = close_ticks
            break

    if acceptance_index is not None:
        status = WallActivityStatus.INACTIVE_ACCEPTED
        is_active = False
    else:
        status = WallActivityStatus.ACTIVE_UNBROKEN
        is_active = True

    return ClassifiedWall(
        wall=wall,
        status=status,
        is_active=is_active,
        last_contact_index=last_contact_index,
        entry_index=entry_index,
        direction=direction,
        bound_compared=bound_ticks,
        acceptance_index=acceptance_index,
        acceptance_close_ticks=acceptance_close_ticks,
    )


# ── Public API ────────────────────────────────────────────────────────────────


def classify_active_rejection_walls(
    walls: tuple[RejectionWall, ...] | list[RejectionWall],
    candles: list[dict],
    entry_index: int,
    direction: Direction,
    tick_size: float,
) -> tuple[ClassifiedWall, ...]:
    """Classify each Rejection Wall as ACTIVE or INACTIVE at entry time.

    Provisional active-wall acceptance rule: a wall is INACTIVE_ACCEPTED
    if at least one candle after the wall's last contact and before the
    entry bar closed strictly beyond the wall's bound on the favorable
    side.  See module docstring for details.

    Parameters
    ----------
    walls : tuple[RejectionWall, ...] or list
        Walls from B9.1 ``find_rejection_walls``.
    candles : list[dict]
        Same candle array used for detection, with float OHLC keys.
    entry_index : int
        Index of the entry bar.  The entry candle itself is excluded
        from the acceptance scan.
    direction : Direction
        LONG or SHORT.
    tick_size : float
        Price increment for tick normalization.

    Returns
    -------
    tuple[ClassifiedWall, ...]
        One ClassifiedWall per input wall, in the same order.

    Raises
    ------
    TypeError
        On invalid argument types.
    ValueError
        On invalid indices, empty contacts, NaN/inf close, or
        last_contact_index >= entry_index.
    """
    # ── Validate ──────────────────────────────────────────────────────
    if not isinstance(direction, Direction):
        raise TypeError(
            f"direction must be a Direction, got {type(direction).__name__}"
        )
    if not isinstance(candles, list):
        raise TypeError(
            f"candles must be a list, got {type(candles).__name__}"
        )

    entry_idx = _require_int(entry_index, "entry_index")
    if entry_idx < 0:
        raise ValueError(f"entry_index must be >= 0, got {entry_idx}")
    if entry_idx > len(candles):
        raise ValueError(
            f"entry_index ({entry_idx}) out of range for "
            f"candles of length {len(candles)}"
        )

    if isinstance(tick_size, bool) or not isinstance(tick_size, (int, float)):
        raise TypeError(
            f"tick_size must be a number, got {type(tick_size).__name__}"
        )
    if tick_size <= 0:
        raise ValueError(f"tick_size must be > 0, got {tick_size!r}")

    # ── Classify each wall independently ──────────────────────────────
    results: list[ClassifiedWall] = []
    for i, wall in enumerate(walls):
        if not isinstance(wall, RejectionWall):
            raise TypeError(
                f"walls[{i}] must be a RejectionWall, "
                f"got {type(wall).__name__}"
            )
        results.append(
            _classify_one(wall, candles, entry_idx, direction, tick_size)
        )

    return tuple(results)
