"""Canonical TradeOutcome/v2 contract type for the BDRR pipeline.

Extends TradeOutcome/v1 to support configurable Risk/Reward targets
using the canonical Rational type (numerator/denominator) instead of
the fixed {2, 3, 4} integer set.

Changes from v1:
    schema_version             "TradeOutcome/v2"
    selected_exit_target_r     int         →  Rational  (must be > 0)
    selected_exit_target_label "2R"|"3R"|"4R" →  str    (derived from Rational)
    exit_target_r              int | None  →  Rational | None
    highest_target_r           int | None  →  Rational | None
    realized_r                 int | None  →  Rational | None

All other fields are identical to v1 in type and semantics.

TradeOutcome/v1 remains fully operational and unmodified.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_lab.contracts.enums import Direction
from trading_lab.contracts.primitives import Rational
from trading_lab.contracts.trade_outcome import TradeOutcomeStatus
from trading_lab.contracts.trade_plan import EntryModel


# ── Constants ────────────────────────────────────────────────────────────────

_SCHEMA_VERSION = "TradeOutcome/v2"


# ── Validation helpers ───────────────────────────────────────────────────────
# Reused from v1 patterns; not imported to keep v1 untouched.


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def _require_optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int or None, got bool")
    if not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int or None, got {type(value).__name__}"
        )
    return value


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool, got {type(value).__name__}")
    return value


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_optional_str(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            f"{name} must be a str or None, got {type(value).__name__}"
        )
    return value


def _require_type(value: object, expected: type, name: str) -> object:
    if not isinstance(value, expected):
        raise TypeError(
            f"{name} must be a {expected.__name__} instance, "
            f"got {type(value).__name__}"
        )
    return value


def _require_rational(value: object, name: str) -> Rational:
    """Validate that *value* is a Rational instance."""
    if not isinstance(value, Rational):
        raise TypeError(
            f"{name} must be a Rational instance, "
            f"got {type(value).__name__}"
        )
    return value


def _require_optional_rational(value: object, name: str) -> Rational | None:
    """Validate that *value* is a Rational instance or None."""
    if value is None:
        return None
    return _require_rational(value, name)


def _require_positive_rational(value: Rational, name: str) -> Rational:
    """Validate that a Rational is strictly positive (> 0)."""
    if value.numerator <= 0:
        raise ValueError(
            f"{name} must be strictly positive, "
            f"got {value.numerator}/{value.denominator}"
        )
    return value


def rational_to_label(r: Rational) -> str:
    """Format a Rational as a canonical R-label string.

    Examples:
        Rational(2, 1)  → "2R"
        Rational(21, 10) → "2.1R"
        Rational(9, 4)  → "2.25R"
        Rational(15, 4) → "3.75R"

    Uses Decimal arithmetic to avoid float representation artifacts.
    Trailing zeros are removed; integer values have no decimal point.
    """
    d = Decimal(r.numerator) / Decimal(r.denominator)
    return str(d.normalize()) + "R"


# ── TradeOutcomeV2 ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TradeOutcomeV2:
    """Canonical TradeOutcome/v2 data contract.

    Identical to TradeOutcome/v1 except the R/R fields use Rational
    instead of int, enabling configurable decimal targets like 2.25R.
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    schema_version: str
    direction: Direction
    entry_model: EntryModel
    entry_price_ticks: int
    stop_price_ticks: int
    tick_size: str

    # ── Selected target configuration ────────────────────────────────────────
    selected_exit_target_r: Rational            # v1: int {2,3,4}
    selected_exit_target_label: str             # v1: {"2R","3R","4R"}

    # ── Entry ────────────────────────────────────────────────────────────────
    entry_triggered: bool
    entry_bar_utc_ms: int | None
    bosb_entry_bar_index: int | None

    # ── First evaluation bar ─────────────────────────────────────────────────
    first_eval_bar_index: int | None
    first_eval_bar_utc_ms: int | None

    # ── Outcome ──────────────────────────────────────────────────────────────
    outcome: TradeOutcomeStatus
    exit_bar_index: int | None
    exit_bar_utc_ms: int | None
    exit_price_ticks: int | None
    exit_target_label: str | None
    exit_target_r: Rational | None              # v1: int | None

    # ── Progress tracking ────────────────────────────────────────────────────
    highest_target_achieved: str | None
    highest_target_r: Rational | None           # v1: int | None

    # ── Realized P&L ─────────────────────────────────────────────────────────
    realized_r: Rational | None                 # v1: int | None

    # ── Reference ticks ──────────────────────────────────────────────────────
    r2_price_ticks: int
    r3_price_ticks: int
    r4_price_ticks: int

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(
                f'schema_version must be "{_SCHEMA_VERSION}", '
                f"got {self.schema_version!r}"
            )
        _require_type(self.direction, Direction, "direction")
        _require_type(self.entry_model, EntryModel, "entry_model")
        _require_int(self.entry_price_ticks, "entry_price_ticks")
        _require_int(self.stop_price_ticks, "stop_price_ticks")
        _require_str(self.tick_size, "tick_size")

        # ── R/R fields: Rational, strictly positive ──────────────────────────
        _require_rational(
            self.selected_exit_target_r, "selected_exit_target_r"
        )
        _require_positive_rational(
            self.selected_exit_target_r, "selected_exit_target_r"
        )
        _require_str(
            self.selected_exit_target_label, "selected_exit_target_label"
        )
        # Label must match the Rational value
        expected_label = rational_to_label(self.selected_exit_target_r)
        if self.selected_exit_target_label != expected_label:
            raise ValueError(
                f"selected_exit_target_label must be '{expected_label}' "
                f"for selected_exit_target_r="
                f"{self.selected_exit_target_r.numerator}/"
                f"{self.selected_exit_target_r.denominator}, "
                f"got {self.selected_exit_target_label!r}"
            )

        _require_bool(self.entry_triggered, "entry_triggered")
        _require_optional_int(self.entry_bar_utc_ms, "entry_bar_utc_ms")
        _require_optional_int(
            self.bosb_entry_bar_index, "bosb_entry_bar_index"
        )

        _require_optional_int(
            self.first_eval_bar_index, "first_eval_bar_index"
        )
        _require_optional_int(
            self.first_eval_bar_utc_ms, "first_eval_bar_utc_ms"
        )

        _require_type(self.outcome, TradeOutcomeStatus, "outcome")
        _require_optional_int(self.exit_bar_index, "exit_bar_index")
        _require_optional_int(self.exit_bar_utc_ms, "exit_bar_utc_ms")
        _require_optional_int(self.exit_price_ticks, "exit_price_ticks")
        _require_optional_str(self.exit_target_label, "exit_target_label")
        _require_optional_rational(self.exit_target_r, "exit_target_r")

        _require_optional_str(
            self.highest_target_achieved, "highest_target_achieved"
        )
        _require_optional_rational(self.highest_target_r, "highest_target_r")
        _require_optional_rational(self.realized_r, "realized_r")

        _require_int(self.r2_price_ticks, "r2_price_ticks")
        _require_int(self.r3_price_ticks, "r3_price_ticks")
        _require_int(self.r4_price_ticks, "r4_price_ticks")

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape.

        Rational fields are serialized as {numerator, denominator} dicts,
        matching the canonical Rational serialization format.
        """
        def _r_to_dict(r: Rational | None) -> dict | None:
            return r.to_dict() if r is not None else None

        return {
            "schema_version": self.schema_version,
            "direction": str(self.direction),
            "entry_model": str(self.entry_model),
            "entry_price_ticks": self.entry_price_ticks,
            "stop_price_ticks": self.stop_price_ticks,
            "tick_size": self.tick_size,
            "selected_exit_target_r": self.selected_exit_target_r.to_dict(),
            "selected_exit_target_label": self.selected_exit_target_label,
            "entry_triggered": self.entry_triggered,
            "entry_bar_utc_ms": self.entry_bar_utc_ms,
            "bosb_entry_bar_index": self.bosb_entry_bar_index,
            "first_eval_bar_index": self.first_eval_bar_index,
            "first_eval_bar_utc_ms": self.first_eval_bar_utc_ms,
            "outcome": str(self.outcome),
            "exit_bar_index": self.exit_bar_index,
            "exit_bar_utc_ms": self.exit_bar_utc_ms,
            "exit_price_ticks": self.exit_price_ticks,
            "exit_target_label": self.exit_target_label,
            "exit_target_r": _r_to_dict(self.exit_target_r),
            "highest_target_achieved": self.highest_target_achieved,
            "highest_target_r": _r_to_dict(self.highest_target_r),
            "realized_r": _r_to_dict(self.realized_r),
            "r2_price_ticks": self.r2_price_ticks,
            "r3_price_ticks": self.r3_price_ticks,
            "r4_price_ticks": self.r4_price_ticks,
        }
