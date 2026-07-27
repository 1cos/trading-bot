"""Canonical ConfluenceResult contract type (§3.6).

FROZEN — immutable after construction.

Represents the result of evaluating confluence data against a
SetupCandidate. Embedded within ScoredSetup/v1.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_lab.contracts.enums import ConfluenceStatus, EvaluationStatus


_CONFLUENCE_EVAL_STATUSES = frozenset({
    EvaluationStatus.SCORED,
    EvaluationStatus.DATA_UNAVAILABLE,
    EvaluationStatus.ERROR,
})


def _require_str(val, name):
    if not isinstance(val, str):
        raise TypeError(
            f"{name} must be a str, got {type(val).__name__}"
        )


def _require_decimal_str(val, name):
    if not isinstance(val, str):
        raise TypeError(
            f"{name} must be a Decimal string, got {type(val).__name__}"
        )
    try:
        from decimal import Decimal, InvalidOperation
        Decimal(val)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} is not a valid Decimal string: {val!r}")


@dataclass(frozen=True, slots=True)
class ConfluenceResult:
    """Result of evaluating confluence data.

    Fields
    ------
    evaluation_status : EvaluationStatus
        Restricted to SCORED, DATA_UNAVAILABLE, or ERROR.
        NOT_RUN is not valid for ConfluenceResult.
    score : str | None
        Decimal string. Non-null iff SCORED.
    confluence_status : ConfluenceStatus
        CONFIRMING, NEUTRAL, CONFLICTING, or DATA_UNAVAILABLE.
    data_source : str | None
        Source identifier for the confluence data.
    data_timestamp_utc_ms : int | None
        Epoch milliseconds UTC of the confluence data snapshot.
    input_fields : tuple[str, ...]
        Field names consumed by the confluence module.
    """

    evaluation_status: EvaluationStatus
    score: str | None
    confluence_status: ConfluenceStatus
    data_source: str | None
    data_timestamp_utc_ms: int | None
    input_fields: tuple[str, ...]

    def __post_init__(self):
        # evaluation_status restricted to SCORED|DATA_UNAVAILABLE|ERROR
        if not isinstance(self.evaluation_status, EvaluationStatus):
            raise TypeError(
                "evaluation_status must be an EvaluationStatus, "
                f"got {type(self.evaluation_status).__name__}"
            )
        if self.evaluation_status not in _CONFLUENCE_EVAL_STATUSES:
            raise ValueError(
                "evaluation_status for ConfluenceResult must be "
                "SCORED, DATA_UNAVAILABLE, or ERROR, "
                f"got {self.evaluation_status!r}"
            )

        # confluence_status
        if not isinstance(self.confluence_status, ConfluenceStatus):
            raise TypeError(
                "confluence_status must be a ConfluenceStatus, "
                f"got {type(self.confluence_status).__name__}"
            )

        # score: non-null iff SCORED
        if self.evaluation_status == EvaluationStatus.SCORED:
            if self.score is None:
                raise ValueError(
                    "score must be non-null when evaluation_status "
                    "is SCORED"
                )
            _require_decimal_str(self.score, "score")
        else:
            if self.score is not None:
                raise ValueError(
                    "score must be null when evaluation_status "
                    "is not SCORED"
                )

        # data_source
        if self.data_source is not None:
            _require_str(self.data_source, "data_source")

        # data_timestamp_utc_ms
        if self.data_timestamp_utc_ms is not None:
            if isinstance(self.data_timestamp_utc_ms, bool):
                raise TypeError(
                    "data_timestamp_utc_ms must be an int, got bool"
                )
            if not isinstance(self.data_timestamp_utc_ms, int):
                raise TypeError(
                    "data_timestamp_utc_ms must be an int, "
                    f"got {type(self.data_timestamp_utc_ms).__name__}"
                )

        # input_fields
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

    def to_dict(self) -> dict:
        """Canonical JSON-compatible dict representation."""
        return {
            "evaluation_status": str(self.evaluation_status),
            "score": self.score,
            "confluence_status": str(self.confluence_status),
            "data_source": self.data_source,
            "data_timestamp_utc_ms": self.data_timestamp_utc_ms,
            "input_fields": list(self.input_fields),
        }
