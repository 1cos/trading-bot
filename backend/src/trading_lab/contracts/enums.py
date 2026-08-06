"""Canonical enum and literal vocabularies for the BDRR pipeline.

Ported from the frozen contract in BDRR_ENGINE_CANONICAL_HANDOFF.md §3.2–§3.6
and the JavaScript reference implementation in
estrategie/bdrr_detection_result.js.

Every enum value preserves the exact canonical string used in serialization.
Python's StrEnum is used so that:
  - enum members serialize as their canonical string value;
  - comparison with plain strings works naturally;
  - no behavior is added beyond what the frozen contract defines.
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class DetectionStatus(StrEnum):
    """DetectionResult/v1 status field (§3.3)."""

    VALID = "VALID"
    INVALID = "INVALID"


@unique
class FailedStage(StrEnum):
    """DetectionResult/v1 failed_stage field (§3.3).

    Null when status is VALID.
    """

    LEVEL_NOT_FOUND = "LEVEL_NOT_FOUND"
    BREAK_NOT_FOUND = "BREAK_NOT_FOUND"
    DISPLACEMENT_MINIMUM_NOT_MET = "DISPLACEMENT_MINIMUM_NOT_MET"
    RETEST_BEFORE_DISPLACEMENT = "RETEST_BEFORE_DISPLACEMENT"
    RETEST_NOT_FOUND = "RETEST_NOT_FOUND"
    NO_QUALIFYING_REJECTION_CANDLE = "NO_QUALIFYING_REJECTION_CANDLE"
    SEQUENCE_INVALIDATED = "SEQUENCE_INVALIDATED"


@unique
class LevelSource(StrEnum):
    """DetectionResult/v1 level_source field (§3.3)."""

    ORB_HIGH = "ORB_HIGH"
    ORB_LOW = "ORB_LOW"
    PREVIOUS_DAY_HIGH = "PREVIOUS_DAY_HIGH"
    PREVIOUS_DAY_LOW = "PREVIOUS_DAY_LOW"
    PMH = "PMH"
    PML = "PML"
    OB = "OB"
    SR = "SR"


@unique
class Direction(StrEnum):
    """DetectionResult/v1 direction field (§3.3)."""

    LONG = "LONG"
    SHORT = "SHORT"


@unique
class Stage(StrEnum):
    """RuleFailure stage field (§3.2)."""

    LEVEL = "LEVEL"
    BREAK = "BREAK"
    DISPLACEMENT = "DISPLACEMENT"
    RETEST = "RETEST"
    REJECTION_CANDLE = "REJECTION_CANDLE"


@unique
class ValueType(StrEnum):
    """RuleFailure value_type field (§3.2)."""

    DECIMAL = "DECIMAL"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"
    ENUM = "ENUM"
    MISSING = "MISSING"


@unique
class Operator(StrEnum):
    """RuleFailure operator field (§3.2)."""

    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    EQ = "EQ"
    NEQ = "NEQ"


@unique
class EvaluationStatus(StrEnum):
    """ModuleResult evaluation_status field (§3.2).

    Also used in ConfluenceResult but restricted to SCORED,
    DATA_UNAVAILABLE, and ERROR there.
    """

    NOT_RUN = "NOT_RUN"
    SCORED = "SCORED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    ERROR = "ERROR"


@unique
class QualityGrade(StrEnum):
    """ScoredSetup/v1 core_quality_grade field (§3.6).

    Permanent once assigned. SKIP never appears here.
    Null when core_quality_score is null.
    D threshold is frozen at 0.00.
    """

    A_PLUS = "A_PLUS"
    A = "A"
    B = "B"
    C = "C"
    D = "D"


@unique
class ConfluenceStatus(StrEnum):
    """ConfluenceResult confluence_status field (§3.6)."""

    CONFIRMING = "CONFIRMING"
    NEUTRAL = "NEUTRAL"
    CONFLICTING = "CONFLICTING"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
