"""Canonical BDRR contract types.

Re-exports the value types defined in
BDRR_ENGINE_CANONICAL_HANDOFF.md §3.2–§3.3.
"""

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.distances import (
    AbsoluteTickDistance,
    DirectionalTickDistance,
)
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
    LevelSource,
    Operator,
    Stage,
    ValueType,
)
from trading_lab.contracts.primitives import (
    PriceTicks,
    Rational,
)
from trading_lab.contracts.rule_failure import (
    RejectionAttempt,
    RuleFailure,
)
from trading_lab.contracts.session_metadata import SessionMetadata

__all__ = [
    "AbsoluteTickDistance",
    "Bar",
    "DetectionStatus",
    "Direction",
    "DirectionalTickDistance",
    "FailedStage",
    "LevelSource",
    "Operator",
    "PriceTicks",
    "Rational",
    "RejectionAttempt",
    "RuleFailure",
    "SessionMetadata",
    "Stage",
    "ValueType",
]
