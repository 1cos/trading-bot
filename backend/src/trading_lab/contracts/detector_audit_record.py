"""Canonical DetectorAuditRecord/v1 contract type.

Represents one detector result selected for later human audit.
Supports both VALID and REJECTED candidates.

This is a pure data contract.  It does not decide whether a runner
result is audit-worthy, generate human-readable explanations, or
contain visual/HTML logic.  Those belong to later builder tasks.

Invariants
----------
INV-A-01: schema_version == "DetectorAuditRecord/v1"
INV-A-02: audit_id is a non-empty string (UUID convention)
INV-A-03: symbol is a non-empty string
INV-A-04: session_date matches YYYY-MM-DD
INV-A-05: timeframe is a non-empty string
INV-A-06: direction is a Direction enum member
INV-A-07: candidate_status is VALID or REJECTED
INV-A-08: VALID → failed_stage is None AND failed_rules is empty
INV-A-09: REJECTED → failed_stage is not None OR failed_rules is not empty
INV-A-10: Progression flags are monotonic:
          reached_rejection_scan → reached_retest →
          reached_displacement → reached_break → reached_orb
INV-A-11: orb_high and orb_low are both present or both None;
          when present, orb_high.ticks > orb_low.ticks
INV-A-12: Optional timestamps are int or None (bool rejected)
INV-A-13: VALID → detection_result.status == VALID
          REJECTED → detection_result.status == INVALID
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, unique

from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
)
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.contracts.rule_failure import RuleFailure


# ── Schema constant ──────────────────────────────────────────────────────────

_SCHEMA_VERSION = "DetectorAuditRecord/v1"


# ── CandidateStatus enum ────────────────────────────────────────────────────

@unique
class CandidateStatus(StrEnum):
    """Audit candidate status."""

    VALID = "VALID"
    REJECTED = "REJECTED"


# ── Validation helpers ───────────────────────────────────────────────────────

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_non_empty_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_date_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not _DATE_RE.match(value):
        raise ValueError(
            f'{name} must match YYYY-MM-DD format, got {value!r}'
        )
    return value


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool, got {type(value).__name__}")
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


def _require_optional_type(
    value: object, expected: type, name: str,
) -> object | None:
    if value is None:
        return None
    if not isinstance(value, expected):
        raise TypeError(
            f"{name} must be a {expected.__name__} instance or None, "
            f"got {type(value).__name__}"
        )
    return value


# ── DetectorAuditRecord ─────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class DetectorAuditRecord:
    """Complete canonical DetectorAuditRecord/v1 data contract.

    Every field matches the audit record specification.
    Field order follows the canonical grouping:
    identity → status → progression → market structure →
    timestamps → source result.
    """

    # ── Identity ─────────────────────────────────────────────────────────
    schema_version: str
    audit_id: str
    symbol: str
    session_date: str
    timeframe: str
    direction: Direction

    # ── Status ───────────────────────────────────────────────────────────
    candidate_status: CandidateStatus
    failed_stage: FailedStage | None
    failed_rules: tuple[RuleFailure, ...]

    # ── Progression ──────────────────────────────────────────────────────
    reached_orb: bool
    reached_break: bool
    reached_displacement: bool
    reached_retest: bool
    reached_rejection_scan: bool

    # ── Market structure ─────────────────────────────────────────────────
    orb_high: PriceTicks | None
    orb_low: PriceTicks | None

    # ── Relevant timestamps ──────────────────────────────────────────────
    orb_candle_time_ms: int | None
    break_candle_time_ms: int | None
    last_relevant_time_ms: int | None

    # ── Source result ────────────────────────────────────────────────────
    detection_result: DetectionResult

    def __post_init__(self) -> None:
        # ── INV-A-01: schema_version ─────────────────────────────────────
        if not isinstance(self.schema_version, str):
            raise TypeError(
                f"schema_version must be a str, "
                f"got {type(self.schema_version).__name__}"
            )
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(
                f'schema_version must be "{_SCHEMA_VERSION}", '
                f"got {self.schema_version!r}"
            )

        # ── INV-A-02: audit_id ───────────────────────────────────────────
        _require_non_empty_str(self.audit_id, "audit_id")

        # ── INV-A-03: symbol ─────────────────────────────────────────────
        _require_non_empty_str(self.symbol, "symbol")

        # ── INV-A-04: session_date ───────────────────────────────────────
        _require_date_str(self.session_date, "session_date")

        # ── INV-A-05: timeframe ──────────────────────────────────────────
        _require_non_empty_str(self.timeframe, "timeframe")

        # ── INV-A-06: direction ──────────────────────────────────────────
        if not isinstance(self.direction, Direction):
            raise TypeError(
                f"direction must be a Direction enum member, "
                f"got {type(self.direction).__name__}"
            )

        # ── INV-A-07: candidate_status ───────────────────────────────────
        if not isinstance(self.candidate_status, CandidateStatus):
            raise TypeError(
                f"candidate_status must be a CandidateStatus enum member, "
                f"got {type(self.candidate_status).__name__}"
            )

        # ── failed_stage ─────────────────────────────────────────────────
        if self.failed_stage is not None:
            if not isinstance(self.failed_stage, FailedStage):
                raise TypeError(
                    f"failed_stage must be a FailedStage enum member "
                    f"or None, got {type(self.failed_stage).__name__}"
                )

        # ── failed_rules ─────────────────────────────────────────────────
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

        # ── INV-A-08: VALID constraints ──────────────────────────────────
        if self.candidate_status == CandidateStatus.VALID:
            if self.failed_stage is not None:
                raise ValueError(
                    "VALID audit record must have failed_stage=None"
                )
            if len(self.failed_rules) > 0:
                raise ValueError(
                    "VALID audit record must have empty failed_rules"
                )

        # ── INV-A-09: REJECTED constraints ───────────────────────────────
        if self.candidate_status == CandidateStatus.REJECTED:
            if self.failed_stage is None and len(self.failed_rules) == 0:
                raise ValueError(
                    "REJECTED audit record must have a non-null "
                    "failed_stage or non-empty failed_rules"
                )

        # ── Progression flags type check ─────────────────────────────────
        _require_bool(self.reached_orb, "reached_orb")
        _require_bool(self.reached_break, "reached_break")
        _require_bool(self.reached_displacement, "reached_displacement")
        _require_bool(self.reached_retest, "reached_retest")
        _require_bool(self.reached_rejection_scan, "reached_rejection_scan")

        # ── INV-A-10: Progression monotonicity ───────────────────────────
        if self.reached_rejection_scan and not self.reached_retest:
            raise ValueError(
                "reached_rejection_scan implies reached_retest"
            )
        if self.reached_retest and not self.reached_displacement:
            raise ValueError(
                "reached_retest implies reached_displacement"
            )
        if self.reached_displacement and not self.reached_break:
            raise ValueError(
                "reached_displacement implies reached_break"
            )
        if self.reached_break and not self.reached_orb:
            raise ValueError(
                "reached_break implies reached_orb"
            )

        # ── INV-A-11: ORB consistency ────────────────────────────────────
        _require_optional_type(self.orb_high, PriceTicks, "orb_high")
        _require_optional_type(self.orb_low, PriceTicks, "orb_low")

        if (self.orb_high is None) != (self.orb_low is None):
            raise ValueError(
                "orb_high and orb_low must be both present or both None"
            )
        if self.orb_high is not None and self.orb_low is not None:
            if self.orb_high.ticks <= self.orb_low.ticks:
                raise ValueError(
                    f"orb_high.ticks ({self.orb_high.ticks}) must be "
                    f"greater than orb_low.ticks ({self.orb_low.ticks})"
                )

        # ── INV-A-12: Timestamps ─────────────────────────────────────────
        _require_optional_int(self.orb_candle_time_ms, "orb_candle_time_ms")
        _require_optional_int(
            self.break_candle_time_ms, "break_candle_time_ms"
        )
        _require_optional_int(
            self.last_relevant_time_ms, "last_relevant_time_ms"
        )

        # ── INV-A-13: detection_result status consistency ────────────────
        if not isinstance(self.detection_result, DetectionResult):
            raise TypeError(
                f"detection_result must be a DetectionResult instance, "
                f"got {type(self.detection_result).__name__}"
            )
        dr_status = str(self.detection_result.status)
        if self.candidate_status == CandidateStatus.VALID:
            if dr_status != "VALID":
                raise ValueError(
                    "VALID audit record must embed a VALID "
                    "DetectionResult"
                )
        else:
            if dr_status != "INVALID":
                raise ValueError(
                    "REJECTED audit record must embed an INVALID "
                    "DetectionResult"
                )

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape.

        Every field is present. Nested contract types use their own
        to_dict(). Enum values serialize as their string value.
        """

        def _opt(v: object) -> object:
            if v is None:
                return None
            if hasattr(v, "to_dict"):
                return v.to_dict()
            return v

        def _enum_opt(v: object) -> object:
            if v is None:
                return None
            return str(v)

        return {
            "schema_version": self.schema_version,
            "audit_id": self.audit_id,
            "symbol": self.symbol,
            "session_date": self.session_date,
            "timeframe": self.timeframe,
            "direction": str(self.direction),
            "candidate_status": str(self.candidate_status),
            "failed_stage": _enum_opt(self.failed_stage),
            "failed_rules": [rf.to_dict() for rf in self.failed_rules],
            "reached_orb": self.reached_orb,
            "reached_break": self.reached_break,
            "reached_displacement": self.reached_displacement,
            "reached_retest": self.reached_retest,
            "reached_rejection_scan": self.reached_rejection_scan,
            "orb_high": _opt(self.orb_high),
            "orb_low": _opt(self.orb_low),
            "orb_candle_time_ms": self.orb_candle_time_ms,
            "break_candle_time_ms": self.break_candle_time_ms,
            "last_relevant_time_ms": self.last_relevant_time_ms,
            "detection_result": self.detection_result.to_dict(),
        }
