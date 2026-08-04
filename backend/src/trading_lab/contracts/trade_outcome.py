"""Canonical TradeOutcome/v1 contract type for the BDRR pipeline.

Ported from the frozen output in estrategie/bdrr_trade_outcome.js
(Object.freeze, lines 443–486) and the chronological outcome rules
in BDRR_ENGINE_CANONICAL_HANDOFF.md §10.

This is a pure data contract.  It holds and validates an already
assembled trade outcome.  It does not simulate bar-by-bar evaluation,
calculate entries/exits, compute R multiples, or apply any strategy
logic.

Canonical identity resolution:

    TradeOutcome/v1  — chronological trade-execution outcome produced by
        evaluateTradeOutcome() in bdrr_trade_outcome.js.
        schema_version = "TradeOutcome/v1"
        Used by bdrr_strategy_runner.js (line 337).

    DecisionOutcome/v1 — policy decision outcome produced by Decision
        Policy.  schema_version = "DecisionOutcome/v1".
        Defined in handoff §3.7.  NOT STARTED (§12.7).
        A completely separate contract.

These are two distinct contracts; this module implements only
TradeOutcome/v1.

Frozen fields (from Object.freeze in bdrr_trade_outcome.js:443-486):

    schema_version              str         "TradeOutcome/v1"
    direction                   str         "LONG" (Direction enum)
    entry_model                 str         EntryModel enum value
    entry_price_ticks           int         tick count
    stop_price_ticks            int         tick count
    tick_size                   number      instrument tick size
    selected_exit_target_r      int         2 | 3 | 4
    selected_exit_target_label  str         "2R" | "3R" | "4R"
    entry_triggered             bool
    entry_bar_utc_ms            int | null
    bosb_entry_bar_index        int | null
    first_eval_bar_index        int | null
    first_eval_bar_utc_ms       int | null
    outcome                     str         TradeOutcomeStatus enum
    exit_bar_index              int | null
    exit_bar_utc_ms             int | null
    exit_price_ticks            int | null
    exit_target_label           str | null
    exit_target_r               int | null
    highest_target_achieved     str | null
    highest_target_r            int | null
    realized_r                  int | null
    r2_price_ticks              int
    r3_price_ticks              int
    r4_price_ticks              int

Validation matches the authoritative JavaScript behavior:
    The JS constructor produces values correctly by construction and
    freezes the result.  No post-assembly validator exists.  The Python
    type validates field types and the schema_version literal.  No
    cross-field invariants are enforced because the JS does not enforce
    them on assembled outcomes.

Note on tick_size:
    The JS stores tick_size as a JS number (from tradePlan.tick_size).
    The canonical schema declares Decimal.  Python uses str per the
    established contract convention.

Note on entry_model and direction:
    The JS stores these as plain strings copied from the TradePlan.
    The Python type uses the existing EntryModel and Direction enums
    for type safety, serializing them as their canonical string values.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from trading_lab.contracts.enums import Direction
from trading_lab.contracts.trade_plan import EntryModel


# ── TradeOutcomeStatus enum ──────────────────────────────────────────────────


@unique
class TradeOutcomeStatus(StrEnum):
    """TradeOutcome/v1 outcome field.

    Values from bdrr_trade_outcome.js lines 58–59, 428–432.
    """

    TARGET_HIT = "TARGET_HIT"
    STOPPED = "STOPPED"
    AMBIGUOUS = "AMBIGUOUS"
    OPEN = "OPEN"
    SESSION_CLOSE = "SESSION_CLOSE"
    ENTRY_NOT_TRIGGERED = "ENTRY_NOT_TRIGGERED"


# ── Validation helpers ────────────────────────────────────────────────────────

_SCHEMA_VERSION = "TradeOutcome/v1"
_VALID_EXIT_R = frozenset({2, 3, 4})
_VALID_EXIT_LABELS = frozenset({"2R", "3R", "4R"})


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


def _require_optional_number(value: object, name: str) -> int | float | None:
    """Like _require_optional_int but also accepts float (for realized_r)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number or None, got bool")
    if not isinstance(value, (int, float)):
        raise TypeError(
            f"{name} must be a number or None, got {type(value).__name__}"
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


# ── TradeOutcome ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    """Complete canonical TradeOutcome/v1 data contract.

    Every field matches the frozen Object.freeze output in
    bdrr_trade_outcome.js exactly.
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    schema_version: str
    direction: Direction
    entry_model: EntryModel
    entry_price_ticks: int
    stop_price_ticks: int
    tick_size: str

    # ── Selected target configuration ────────────────────────────────────────
    selected_exit_target_r: int
    selected_exit_target_label: str

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
    exit_target_r: int | None

    # ── Progress tracking ────────────────────────────────────────────────────
    highest_target_achieved: str | None
    highest_target_r: int | None

    # ── Realized P&L ─────────────────────────────────────────────────────────
    realized_r: int | None

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

        _require_int(self.selected_exit_target_r, "selected_exit_target_r")
        if self.selected_exit_target_r not in _VALID_EXIT_R:
            raise ValueError(
                f"selected_exit_target_r must be 2, 3, or 4, "
                f"got {self.selected_exit_target_r}"
            )
        _require_str(
            self.selected_exit_target_label, "selected_exit_target_label"
        )
        if self.selected_exit_target_label not in _VALID_EXIT_LABELS:
            raise ValueError(
                f"selected_exit_target_label must be '2R', '3R', or '4R', "
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
        _require_optional_int(self.exit_target_r, "exit_target_r")

        _require_optional_str(
            self.highest_target_achieved, "highest_target_achieved"
        )
        _require_optional_int(self.highest_target_r, "highest_target_r")
        _require_optional_number(self.realized_r, "realized_r")

        _require_int(self.r2_price_ticks, "r2_price_ticks")
        _require_int(self.r3_price_ticks, "r3_price_ticks")
        _require_int(self.r4_price_ticks, "r4_price_ticks")

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape."""
        return {
            "schema_version": self.schema_version,
            "direction": str(self.direction),
            "entry_model": str(self.entry_model),
            "entry_price_ticks": self.entry_price_ticks,
            "stop_price_ticks": self.stop_price_ticks,
            "tick_size": self.tick_size,
            "selected_exit_target_r": self.selected_exit_target_r,
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
            "exit_target_r": self.exit_target_r,
            "highest_target_achieved": self.highest_target_achieved,
            "highest_target_r": self.highest_target_r,
            "realized_r": self.realized_r,
            "r2_price_ticks": self.r2_price_ticks,
            "r3_price_ticks": self.r3_price_ticks,
            "r4_price_ticks": self.r4_price_ticks,
        }
