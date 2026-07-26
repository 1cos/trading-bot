"""Canonical RuleFailure and RejectionAttempt contract types.

Ported from BDRR_ENGINE_CANONICAL_HANDOFF.md §3.2 and implemented in
JavaScript by estrategie/bdrr_detection_result.js (lines 458–472).

Canonical schemas (§3.2):

    RuleFailure {
        rule_id:        string
        stage:          enum    LEVEL | BREAK | DISPLACEMENT | RETEST | REJECTION_CANDLE
        value_type:     enum    DECIMAL | INTEGER | BOOLEAN | ENUM | MISSING
        actual_value:   string | null   null if value_type = MISSING
        operator:       enum | null     GT | GTE | LT | LTE | EQ | NEQ
        required_value: string | null   null if value_type = MISSING
        unit:           string | null   "ticks"|"ratio"|"bars"|"pct"|"pts"|null
        message:        string          always present
    }

    RejectionAttempt {
        bar:            Bar
        failed_rules:   RuleFailure[]
    }

Validation rules (from JS reference):

    RuleFailure:
      - rule_id:        non-empty string (JS: typeof r === 'string' ? r : String(r))
      - stage:          must be a valid Stage enum value
      - value_type:     must be a valid ValueType enum value
      - actual_value:   string or null
      - operator:       valid Operator enum value or null
      - required_value: string or null
      - unit:           string or null
      - message:        non-empty string (JS: always present)

    RejectionAttempt:
      - bar:            must be a Bar instance
      - failed_rules:   tuple of RuleFailure instances (frozen from list)
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.enums import Operator, Stage, ValueType


# ── Validation helpers ────────────────────────────────────────────────────────


def _require_non_empty_str(value: object, name: str) -> str:
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


# ── RuleFailure ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RuleFailure:
    """A single rule that prevented qualification.

    All fields match the canonical schema in §3.2.
    """

    rule_id: str
    stage: Stage
    value_type: ValueType
    actual_value: str | None
    operator: Operator | None
    required_value: str | None
    unit: str | None
    message: str

    def __post_init__(self) -> None:
        _require_non_empty_str(self.rule_id, "rule_id")

        if not isinstance(self.stage, Stage):
            raise TypeError(
                f"stage must be a Stage enum member, "
                f"got {type(self.stage).__name__}"
            )
        if not isinstance(self.value_type, ValueType):
            raise TypeError(
                f"value_type must be a ValueType enum member, "
                f"got {type(self.value_type).__name__}"
            )

        _require_optional_str(self.actual_value, "actual_value")

        if self.operator is not None and not isinstance(self.operator, Operator):
            raise TypeError(
                f"operator must be an Operator enum member or None, "
                f"got {type(self.operator).__name__}"
            )

        _require_optional_str(self.required_value, "required_value")
        _require_optional_str(self.unit, "unit")
        _require_non_empty_str(self.message, "message")

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape."""
        return {
            "rule_id": self.rule_id,
            "stage": str(self.stage),
            "value_type": str(self.value_type),
            "actual_value": self.actual_value,
            "operator": str(self.operator) if self.operator is not None else None,
            "required_value": self.required_value,
            "unit": self.unit,
            "message": self.message,
        }


# ── RejectionAttempt ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RejectionAttempt:
    """A retest attempt that failed rejection qualification.

    Canonical schema (§3.2):
        bar:            Bar
        failed_rules:   RuleFailure[]
    """

    bar: Bar
    failed_rules: tuple[RuleFailure, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.bar, Bar):
            raise TypeError(
                f"bar must be a Bar instance, got {type(self.bar).__name__}"
            )
        if not isinstance(self.failed_rules, tuple):
            raise TypeError(
                f"failed_rules must be a tuple, "
                f"got {type(self.failed_rules).__name__}"
            )
        for i, rf in enumerate(self.failed_rules):
            if not isinstance(rf, RuleFailure):
                raise TypeError(
                    f"failed_rules[{i}] must be a RuleFailure instance, "
                    f"got {type(rf).__name__}"
                )

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape."""
        return {
            "bar": self.bar.to_dict(),
            "failed_rules": [rf.to_dict() for rf in self.failed_rules],
        }
