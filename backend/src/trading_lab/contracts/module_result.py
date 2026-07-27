"""Canonical ModuleResult contract type (§3.2).

FROZEN — immutable after construction.

Represents the result of evaluating a single scoring module
(core or contextual) against a SetupCandidate.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_lab.contracts.enums import EvaluationStatus


def _require_type(val, expected, name):
    if not isinstance(val, expected):
        raise TypeError(
            f"{name} must be {expected.__name__}, "
            f"got {type(val).__name__}"
        )


def _require_str(val, name):
    if not isinstance(val, str):
        raise TypeError(
            f"{name} must be a str, got {type(val).__name__}"
        )


def _require_bool(val, name):
    if not isinstance(val, bool):
        raise TypeError(
            f"{name} must be a bool, got {type(val).__name__}"
        )


def _require_decimal_str(val, name):
    """Validate a Decimal-as-string field."""
    if not isinstance(val, str):
        raise TypeError(
            f"{name} must be a Decimal string, got {type(val).__name__}"
        )
    try:
        from decimal import Decimal, InvalidOperation
        Decimal(val)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} is not a valid Decimal string: {val!r}")


def _require_optional_decimal_str(val, name):
    """Validate a nullable Decimal-as-string field."""
    if val is None:
        return
    _require_decimal_str(val, name)


@dataclass(frozen=True, slots=True)
class ModuleResult:
    """Result of evaluating a single scoring module.

    Fields
    ------
    enabled : bool
        Whether the module was enabled for this scoring pass.
    evaluation_status : EvaluationStatus
        NOT_RUN, SCORED, DATA_UNAVAILABLE, or ERROR.
    score : str | None
        Decimal string in [0.0, 1.0]. Non-null iff SCORED.
    weight : str
        Decimal string. Module weight at scoring time.
    weighted_score : str | None
        Decimal string. Non-null iff SCORED. Equals score × weight.
    input_fields : tuple[str, ...]
        Field names consumed by this module.
    error_detail : str | None
        Non-null iff ERROR.
    notes : str
        Human-readable notes about the evaluation.
    """

    enabled: bool
    evaluation_status: EvaluationStatus
    score: str | None
    weight: str
    weighted_score: str | None
    input_fields: tuple[str, ...]
    error_detail: str | None
    notes: str

    def __post_init__(self):
        _require_bool(self.enabled, "enabled")
        _require_type(
            self.evaluation_status, EvaluationStatus,
            "evaluation_status",
        )
        _require_decimal_str(self.weight, "weight")
        _require_str(self.notes, "notes")

        if not isinstance(self.input_fields, tuple):
            raise TypeError(
                "input_fields must be a tuple of str, "
                f"got {type(self.input_fields).__name__}"
            )
        for i, v in enumerate(self.input_fields):
            if not isinstance(v, str):
                raise TypeError(
                    f"input_fields[{i}] must be a str, "
                    f"got {type(v).__name__}"
                )

        # SCORED constraints: score and weighted_score must be non-null
        if self.evaluation_status == EvaluationStatus.SCORED:
            if self.score is None:
                raise ValueError(
                    "score must be non-null when evaluation_status is SCORED"
                )
            _require_decimal_str(self.score, "score")
            if self.weighted_score is None:
                raise ValueError(
                    "weighted_score must be non-null when "
                    "evaluation_status is SCORED"
                )
            _require_decimal_str(self.weighted_score, "weighted_score")
        else:
            # Non-SCORED: score and weighted_score must be null
            if self.score is not None:
                raise ValueError(
                    "score must be null when evaluation_status is not SCORED"
                )
            if self.weighted_score is not None:
                raise ValueError(
                    "weighted_score must be null when "
                    "evaluation_status is not SCORED"
                )

        # ERROR constraints: error_detail must be non-null
        if self.evaluation_status == EvaluationStatus.ERROR:
            if self.error_detail is None:
                raise ValueError(
                    "error_detail must be non-null when "
                    "evaluation_status is ERROR"
                )
            _require_str(self.error_detail, "error_detail")
        else:
            if self.error_detail is not None:
                raise ValueError(
                    "error_detail must be null when "
                    "evaluation_status is not ERROR"
                )

    def to_dict(self) -> dict:
        """Canonical JSON-compatible dict representation."""
        return {
            "enabled": self.enabled,
            "evaluation_status": str(self.evaluation_status),
            "score": self.score,
            "weight": self.weight,
            "weighted_score": self.weighted_score,
            "input_fields": list(self.input_fields),
            "error_detail": self.error_detail,
            "notes": self.notes,
        }
