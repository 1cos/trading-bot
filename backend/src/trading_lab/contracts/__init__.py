"""Canonical BDRR contract types.

Re-exports the primitive value types defined in
BDRR_ENGINE_CANONICAL_HANDOFF.md §3.2.
"""

from trading_lab.contracts.primitives import (
    PriceTicks,
    Rational,
)

__all__ = [
    "PriceTicks",
    "Rational",
]
