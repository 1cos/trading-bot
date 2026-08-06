"""Canonical EntryPatternResult contract type.

Defined in MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §4–§6.

Represents the outcome of evaluating a retest for an entry pattern.
This is a pure data contract — it does not detect patterns.

Immutability: frozen dataclass with ``metadata`` stored as
``types.MappingProxyType`` to prevent post-construction mutation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType

from trading_lab.contracts.enums import Direction, EntryPatternType


# ── Allowed metadata value types (JSON-safe) ─────────────────────────────────

_METADATA_VALUE_TYPES = (str, int, float, type(None))


def _validate_metadata(raw: dict) -> MappingProxyType:
    """Validate and return an immutable defensive copy of *raw*."""
    if not isinstance(raw, dict):
        raise TypeError(
            f"metadata must be a dict, got {type(raw).__name__}"
        )
    copy = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            raise TypeError(
                f"metadata keys must be str, got {type(k).__name__} "
                f"for key {k!r}"
            )
        if isinstance(v, bool):
            # bool is allowed in metadata
            copy[k] = v
            continue
        if not isinstance(v, _METADATA_VALUE_TYPES):
            raise TypeError(
                f"metadata[{k!r}] must be str|int|float|bool|None, "
                f"got {type(v).__name__}"
            )
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError(
                f"metadata[{k!r}] must be finite, got {v!r}"
            )
        copy[k] = v
    return MappingProxyType(copy)


# ── Validation helpers ────────────────────────────────────────────────────────


def _require_non_bool_int(value: object, name: str) -> int:
    """Require a real int, rejecting bool."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int, got {type(value).__name__}"
        )
    return value


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


# ── EntryPatternResult ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntryPatternResult:
    """Result of evaluating a retest for an entry pattern.

    Fields
    ------
    pattern_type : EntryPatternType
        Which entry pattern was matched.
    direction : Direction
        LONG or SHORT.
    entry_bar_index : int
        Index of the candle at which the entry is taken (>= 0).
    entry_price : float
        Close price of the entry candle.
    stop_price : float
        Stop price derived from the pattern geometry.
    candle_indices : tuple[int, ...]
        Indices of all candles involved in the pattern.
        Must be non-empty; ``entry_bar_index`` must be present.
    metadata : MappingProxyType
        Immutable bag of pattern-specific auxiliary data.
        Keys must be str; values must be str, int, float, bool, or
        None. Float values must be finite.
    """

    pattern_type: EntryPatternType
    direction: Direction
    entry_bar_index: int
    entry_price: float
    stop_price: float
    candle_indices: tuple[int, ...]
    metadata: MappingProxyType

    def __post_init__(self) -> None:
        # pattern_type
        if not isinstance(self.pattern_type, EntryPatternType):
            raise TypeError(
                f"pattern_type must be an EntryPatternType, "
                f"got {type(self.pattern_type).__name__}"
            )

        # direction
        if not isinstance(self.direction, Direction):
            raise TypeError(
                f"direction must be a Direction, "
                f"got {type(self.direction).__name__}"
            )

        # entry_bar_index
        idx = _require_non_bool_int(self.entry_bar_index, "entry_bar_index")
        if idx < 0:
            raise ValueError(
                f"entry_bar_index must be >= 0, got {idx}"
            )

        # entry_price
        ep = _require_finite_number(self.entry_price, "entry_price")
        object.__setattr__(self, "entry_price", ep)

        # stop_price
        sp = _require_finite_number(self.stop_price, "stop_price")
        object.__setattr__(self, "stop_price", sp)

        # candle_indices
        if not isinstance(self.candle_indices, tuple):
            raise TypeError(
                f"candle_indices must be a tuple, "
                f"got {type(self.candle_indices).__name__}"
            )
        if len(self.candle_indices) == 0:
            raise ValueError("candle_indices must be non-empty")
        for i, ci in enumerate(self.candle_indices):
            v = _require_non_bool_int(ci, f"candle_indices[{i}]")
            if v < 0:
                raise ValueError(
                    f"candle_indices[{i}] must be >= 0, got {v}"
                )
        if self.entry_bar_index not in self.candle_indices:
            raise ValueError(
                f"entry_bar_index ({self.entry_bar_index}) must be "
                f"present in candle_indices {self.candle_indices}"
            )

        # metadata — defensive copy + immutability
        proxy = _validate_metadata(
            dict(self.metadata) if isinstance(self.metadata, MappingProxyType)
            else self.metadata
        )
        object.__setattr__(self, "metadata", proxy)

    def to_dict(self) -> dict:
        """Canonical JSON-compatible dict representation."""
        return {
            "pattern_type": str(self.pattern_type),
            "direction": str(self.direction),
            "entry_bar_index": self.entry_bar_index,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "candle_indices": list(self.candle_indices),
            "metadata": dict(self.metadata),
        }
