"""Canonical SetupCandidate/v1 contract type (§3.5).

FROZEN — fully immutable after composition.

Combines a VALID DetectionResult/v1 and its corresponding TradePlan/v1
into a single scorable unit. The Quality Scorer is the only processing
layer that consumes it as direct pipeline input.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.trade_plan import TradePlan


def _require_str(val, name):
    if not isinstance(val, str):
        raise TypeError(
            f"{name} must be a str, got {type(val).__name__}"
        )


@dataclass(frozen=True, slots=True)
class SetupCandidate:
    """A validated detection + trade plan pair ready for scoring.

    Fields
    ------
    schema_version : str
        Must be exactly ``"SetupCandidate/v1"``.
    candidate_id : str
        UUID v4, distinct from detection_result.result_id.
    composed_at : str
        ISO 8601 UTC processing timestamp.
    detection_result : DetectionResult
        Embedded in full. status must equal VALID.
        schema_version must equal "DetectionResult/v1".
    trade_plan : TradePlan
        schema_version must equal "TradePlan/v1".

    Invariants (INV-C)
    ------------------
    INV-C-01: detection_result.status == VALID
    INV-C-02: schema versions match exactly
    INV-C-06: candidate_id != detection_result.result_id
    INV-C-07: no field modified after composed_at is set
    INV-C-08: composed_at is ISO 8601 UTC
    """

    schema_version: str
    candidate_id: str
    composed_at: str
    detection_result: DetectionResult
    trade_plan: TradePlan

    def __post_init__(self):
        # schema_version
        _require_str(self.schema_version, "schema_version")
        if self.schema_version != "SetupCandidate/v1":
            raise ValueError(
                f'schema_version must be "SetupCandidate/v1", '
                f"got {self.schema_version!r}"
            )

        # candidate_id
        _require_str(self.candidate_id, "candidate_id")
        if len(self.candidate_id) == 0:
            raise ValueError("candidate_id must be a non-empty string")

        # composed_at
        _require_str(self.composed_at, "composed_at")
        if len(self.composed_at) == 0:
            raise ValueError("composed_at must be a non-empty string")

        # detection_result
        if not isinstance(self.detection_result, DetectionResult):
            raise TypeError(
                "detection_result must be a DetectionResult, "
                f"got {type(self.detection_result).__name__}"
            )

        # INV-C-01: detection_result.status == VALID
        if str(self.detection_result.status) != "VALID":
            raise ValueError(
                "detection_result.status must be VALID for "
                "SetupCandidate composition"
            )

        # INV-C-02: detection_result schema version
        if self.detection_result.schema_version != "DetectionResult/v1":
            raise ValueError(
                "detection_result.schema_version must be "
                '"DetectionResult/v1"'
            )

        # trade_plan
        if not isinstance(self.trade_plan, TradePlan):
            raise TypeError(
                "trade_plan must be a TradePlan, "
                f"got {type(self.trade_plan).__name__}"
            )

        # INV-C-02: trade_plan schema version
        if self.trade_plan.schema_version != "TradePlan/v1":
            raise ValueError(
                'trade_plan.schema_version must be "TradePlan/v1"'
            )

        # INV-C-06: candidate_id != detection_result.result_id
        if self.candidate_id == self.detection_result.result_id:
            raise ValueError(
                "candidate_id must differ from "
                "detection_result.result_id (INV-C-06)"
            )

    def to_dict(self) -> dict:
        """Canonical JSON-compatible dict representation."""
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "composed_at": self.composed_at,
            "detection_result": self.detection_result.to_dict(),
            "trade_plan": self.trade_plan.to_dict(),
        }
