"""Canonical Bar contract type for the BDRR pipeline.

Ported from the frozen auxiliary type library defined in
BDRR_ENGINE_CANONICAL_HANDOFF.md §3.2 and implemented in JavaScript by:
  - estrategie/bdrr_detection_result.js  (buildBar)
  - estrategie/bdrr_trade_outcome.js     (validateBars — consumer validation)
  - estrategie/bdrr_strategy_runner.js   (rawCandleToCanonicalBar)

Canonical schema (§3.2):

    Bar {
        bar_utc_ms:  int64           Unix milliseconds UTC, start of bar
        open:        PriceTicks
        high:        PriceTicks
        low:         PriceTicks
        close:       PriceTicks
        volume:      int64 | null
    }

Validation rules derived from the authoritative JavaScript:

    - bar_utc_ms: required int. The canonical schema declares int64. The
      JS buildBar factory can produce null for malformed runtime input, but
      that represents a construction failure — all downstream consumers
      (validateBars in trade_outcome.js) require a finite number.  The Python
      type follows the schema: int, required.

    - open, high, low, close: required PriceTicks.

    - volume: int | None.  The schema declares int64 | null.

    - OHLC relationship: high.ticks >= low.ticks is validated by
      validateBars in trade_outcome.js (line 198).  This is a downstream
      consumer validation, NOT performed by buildBar itself.  The Python
      Bar constructor does NOT enforce this, matching buildBar behavior.
      Consumers that need the guarantee validate it themselves.

    - Booleans are rejected for bar_utc_ms and volume (Python bool is a
      subclass of int and would silently pass isinstance checks).

Serialization (.to_dict()) produces the canonical JSON-compatible shape:

    {
        "bar_utc_ms": int,
        "open":       { "ticks": int, "tick_size": str },
        "high":       { "ticks": int, "tick_size": str },
        "low":        { "ticks": int, "tick_size": str },
        "close":      { "ticks": int, "tick_size": str },
        "volume":     int | None
    }
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_lab.contracts.primitives import PriceTicks


# ── Validation helpers ────────────────────────────────────────────────────────


def _require_int(value: object, name: str) -> int:
    """Validate that *value* is a plain int (bool rejected)."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def _require_price_ticks(value: object, name: str) -> PriceTicks:
    """Validate that *value* is a PriceTicks instance."""
    if not isinstance(value, PriceTicks):
        raise TypeError(
            f"{name} must be a PriceTicks instance, "
            f"got {type(value).__name__}"
        )
    return value


def _require_optional_int(value: object, name: str) -> int | None:
    """Validate that *value* is a plain int or None (bool rejected)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int or None, got bool")
    if not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int or None, got {type(value).__name__}"
        )
    return value


# ── Bar ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Bar:
    """Single OHLCV bar with integer-tick prices.

    Canonical schema (§3.2):
        bar_utc_ms:  int64           Unix milliseconds UTC, start of bar
        open:        PriceTicks
        high:        PriceTicks
        low:         PriceTicks
        close:       PriceTicks
        volume:      int64 | null
    """

    bar_utc_ms: int
    open: PriceTicks
    high: PriceTicks
    low: PriceTicks
    close: PriceTicks
    volume: int | None = None

    def __post_init__(self) -> None:
        _require_int(self.bar_utc_ms, "bar_utc_ms")
        _require_price_ticks(self.open, "open")
        _require_price_ticks(self.high, "high")
        _require_price_ticks(self.low, "low")
        _require_price_ticks(self.close, "close")
        _require_optional_int(self.volume, "volume")

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape."""
        return {
            "bar_utc_ms": self.bar_utc_ms,
            "open": self.open.to_dict(),
            "high": self.high.to_dict(),
            "low": self.low.to_dict(),
            "close": self.close.to_dict(),
            "volume": self.volume,
        }
