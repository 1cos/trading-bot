"""Canonical TradePlan/v1 contract type for the BDRR pipeline.

Ported from the frozen schema in BDRR_ENGINE_CANONICAL_HANDOFF.md §3.4
and the JavaScript reference in estrategie/bdrr_trade_plan.js.

This is a pure data contract.  It holds and validates a constructed
TradePlan.  It does not calculate entry, stop, target, risk, or any
derived value from a DetectionResult.

Frozen schema (§3.4):

    schema_version:     "TradePlan/v1"
    entry_model:        enum    CONFIRMATION_CLOSE | BREAK_OF_SIGNAL_BAR
    entry_buffer_ticks: int     >= 0
    stop_buffer_ticks:  int     >= 0
    tick_size:          Decimal
    entry_price:        PriceTicks
    stop_price:         PriceTicks
    risk:               AbsoluteTickDistance
    r2_price:           PriceTicks
    r3_price:           PriceTicks
    r4_price:           PriceTicks

Validation matches the authoritative JavaScript behavior:

    - schema_version must equal "TradePlan/v1" exactly
      (consumer validation: bdrr_trade_outcome.js:119)
    - entry_buffer_ticks: non-negative integer
      (builder validation: bdrr_trade_plan.js:114)
    - stop_buffer_ticks: non-negative integer
      (builder validation: bdrr_trade_plan.js:120)
    - tick_size: positive Decimal string
      (builder validation: bdrr_trade_plan.js:110)
    - All price fields: valid PriceTicks
      (consumer validation: bdrr_trade_outcome.js:125-133)
    - risk: AbsoluteTickDistance (ticks >= 0 per schema)
      (consumer validation: bdrr_trade_outcome.js:138 checks ticks > 0,
       but that is consumer eligibility, not constructor validation;
       AbsoluteTickDistance already enforces >= 0)
    - No cross-field invariants are enforced by the JavaScript
      TradePlan constructor — values are produced correctly by
      construction, not validated post-assembly.

EntryModel enum:

    Defined within this module as it is inseparable from TradePlan/v1
    and not used by any other existing contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from trading_lab.contracts.distances import AbsoluteTickDistance
from trading_lab.contracts.primitives import PriceTicks


# ── EntryModel enum ──────────────────────────────────────────────────────────


@unique
class EntryModel(StrEnum):
    """TradePlan/v1 entry_model field (§3.4 / §8.7)."""

    CONFIRMATION_CLOSE = "CONFIRMATION_CLOSE"
    BREAK_OF_SIGNAL_BAR = "BREAK_OF_SIGNAL_BAR"


# ── Validation helpers ────────────────────────────────────────────────────────


def _require_non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def _require_type(value: object, expected: type, name: str) -> object:
    if not isinstance(value, expected):
        raise TypeError(
            f"{name} must be a {expected.__name__} instance, "
            f"got {type(value).__name__}"
        )
    return value


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


# ── TradePlan ────────────────────────────────────────────────────────────────

_SCHEMA_VERSION = "TradePlan/v1"


@dataclass(frozen=True, slots=True)
class TradePlan:
    """Complete canonical TradePlan/v1 data contract.

    Every field matches the frozen schema in §3.4 exactly.
    Field order follows the canonical schema.
    """

    schema_version: str
    entry_model: EntryModel
    entry_buffer_ticks: int
    stop_buffer_ticks: int
    tick_size: str
    entry_price: PriceTicks
    stop_price: PriceTicks
    risk: AbsoluteTickDistance
    r2_price: PriceTicks
    r3_price: PriceTicks
    r4_price: PriceTicks

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(
                f'schema_version must be "{_SCHEMA_VERSION}", '
                f"got {self.schema_version!r}"
            )
        _require_type(self.entry_model, EntryModel, "entry_model")
        _require_non_negative_int(
            self.entry_buffer_ticks, "entry_buffer_ticks"
        )
        _require_non_negative_int(
            self.stop_buffer_ticks, "stop_buffer_ticks"
        )
        _require_str(self.tick_size, "tick_size")
        _require_type(self.entry_price, PriceTicks, "entry_price")
        _require_type(self.stop_price, PriceTicks, "stop_price")
        _require_type(self.risk, AbsoluteTickDistance, "risk")
        _require_type(self.r2_price, PriceTicks, "r2_price")
        _require_type(self.r3_price, PriceTicks, "r3_price")
        _require_type(self.r4_price, PriceTicks, "r4_price")

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape."""
        return {
            "schema_version": self.schema_version,
            "entry_model": str(self.entry_model),
            "entry_buffer_ticks": self.entry_buffer_ticks,
            "stop_buffer_ticks": self.stop_buffer_ticks,
            "tick_size": self.tick_size,
            "entry_price": self.entry_price.to_dict(),
            "stop_price": self.stop_price.to_dict(),
            "risk": self.risk.to_dict(),
            "r2_price": self.r2_price.to_dict(),
            "r3_price": self.r3_price.to_dict(),
            "r4_price": self.r4_price.to_dict(),
        }
