"""Detector Audit Candidate Selector.

Pure selection layer that decides which DetectorAuditRecord/v1
records are eligible for human audit.

Selection policy (conservative initial version)
------------------------------------------------
Include:
    1. Every VALID record (controls / comparison examples).
    2. Every REJECTED record that reached a confirmed break
       and then failed at a downstream stage.

Exclude:
    - ORB / session construction failures (LEVEL_NOT_FOUND)
    - break not found (BREAK_NOT_FOUND)
    - REJECTED records where reached_break is False
    - structurally empty rejection records

FailedStage classification
--------------------------
Each FailedStage enum member is classified as:

    AUDIT-WORTHY (reached break, failed downstream):
        DISPLACEMENT_MINIMUM_NOT_MET  — break OK, displacement too small
        RETEST_BEFORE_DISPLACEMENT    — break OK, price retested before displacing
        RETEST_NOT_FOUND              — break OK, no retest contact found
        NO_QUALIFYING_REJECTION_CANDLE — full scan, no confirmation qualified

    NOT AUDIT-WORTHY (never reached break):
        LEVEL_NOT_FOUND               — no valid ORB / level
        BREAK_NOT_FOUND               — ORB OK but no break candle

    UNKNOWN:
        Any future FailedStage not in the above sets → ValueError.
        Never silently include or exclude unknown values.

Impossible combinations
-----------------------
The selector rejects records with logically impossible
stage/progression combinations:

    - AUDIT-WORTHY failed_stage but reached_break is False
    - NOT-AUDIT-WORTHY failed_stage but reached_break is True

These indicate a builder defect and must not be silently accepted.

Public API
----------
    is_audit_worthy(record) → bool
    select_audit_candidates(records) → tuple[DetectorAuditRecord, ...]
"""

from __future__ import annotations

from collections.abc import Iterable

from trading_lab.contracts.detector_audit_record import (
    CandidateStatus,
    DetectorAuditRecord,
)
from trading_lab.contracts.enums import FailedStage


# ── FailedStage classification ───────────────────────────────────────────────

# Audit-worthy: break was found, failure occurred downstream.
_AUDIT_WORTHY_STAGES: frozenset[FailedStage] = frozenset({
    FailedStage.DISPLACEMENT_MINIMUM_NOT_MET,
    FailedStage.RETEST_BEFORE_DISPLACEMENT,
    FailedStage.RETEST_NOT_FOUND,
    FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
})

# Not audit-worthy: failure occurred before or at the break stage.
_NOT_AUDIT_WORTHY_STAGES: frozenset[FailedStage] = frozenset({
    FailedStage.LEVEL_NOT_FOUND,
    FailedStage.BREAK_NOT_FOUND,
})

# All known stages must be classified.
_ALL_CLASSIFIED: frozenset[FailedStage] = (
    _AUDIT_WORTHY_STAGES | _NOT_AUDIT_WORTHY_STAGES
)


# ── Validation ───────────────────────────────────────────────────────────────

def _validate_record(record: DetectorAuditRecord) -> None:
    """Reject logically impossible stage/progression combinations.

    Raises ValueError for:
        - REJECTED with an audit-worthy failed_stage but reached_break=False
        - REJECTED with a not-audit-worthy failed_stage but reached_break=True
    """
    if record.candidate_status != CandidateStatus.REJECTED:
        return
    if record.failed_stage is None:
        return  # INV-A-09 guarantees failed_rules is non-empty; no stage to check

    if record.failed_stage in _AUDIT_WORTHY_STAGES:
        if not record.reached_break:
            raise ValueError(
                f"Impossible combination: failed_stage="
                f"{record.failed_stage.value} requires reached_break=True"
            )

    elif record.failed_stage in _NOT_AUDIT_WORTHY_STAGES:
        if record.failed_stage == FailedStage.BREAK_NOT_FOUND \
                and record.reached_break:
            raise ValueError(
                f"Impossible combination: failed_stage=BREAK_NOT_FOUND "
                f"but reached_break=True"
            )
        if record.failed_stage == FailedStage.LEVEL_NOT_FOUND \
                and record.reached_orb:
            raise ValueError(
                f"Impossible combination: failed_stage=LEVEL_NOT_FOUND "
                f"but reached_orb=True"
            )

    else:
        # Unknown FailedStage member — fail explicitly
        raise ValueError(
            f"Unknown FailedStage value: {record.failed_stage!r}. "
            f"Update the audit candidate selector to classify this value."
        )


# ── Predicate ────────────────────────────────────────────────────────────────

def is_audit_worthy(record: DetectorAuditRecord) -> bool:
    """Determine whether a single audit record merits human review.

    Parameters
    ----------
    record : DetectorAuditRecord
        A valid DetectorAuditRecord/v1 instance.

    Returns
    -------
    bool
        True if the record should be included in an audit batch.

    Raises
    ------
    TypeError
        If record is not a DetectorAuditRecord.
    ValueError
        If the record has an impossible stage/progression combination
        or an unknown FailedStage value.
    """
    if not isinstance(record, DetectorAuditRecord):
        raise TypeError(
            f"record must be a DetectorAuditRecord, "
            f"got {type(record).__name__}"
        )

    _validate_record(record)

    # Rule 1: VALID records are always audit-worthy
    if record.candidate_status == CandidateStatus.VALID:
        return True

    # Rule 2: REJECTED must have reached break
    if not record.reached_break:
        return False

    # Rule 3: failed_stage must be classified as audit-worthy
    if record.failed_stage is not None:
        return record.failed_stage in _AUDIT_WORTHY_STAGES

    # Rule 4: REJECTED with reached_break and no typed failed_stage
    # but non-empty failed_rules (INV-A-09 guarantees at least one
    # indicator). Include these — they represent a meaningful rejection
    # with rule-level detail.
    if len(record.failed_rules) > 0:
        return True

    # Should not be reachable (INV-A-09 prevents both being empty)
    return False  # pragma: no cover


# ── Batch selector ───────────────────────────────────────────────────────────

def select_audit_candidates(
    records: Iterable[DetectorAuditRecord],
) -> tuple[DetectorAuditRecord, ...]:
    """Select audit-worthy candidates from a sequence of audit records.

    Parameters
    ----------
    records : Iterable[DetectorAuditRecord]
        DetectorAuditRecord/v1 instances. May be a list, tuple, or
        generator.

    Returns
    -------
    tuple[DetectorAuditRecord, ...]
        Immutable tuple preserving input order. Only includes records
        where ``is_audit_worthy`` returns True.

    Raises
    ------
    TypeError
        If any item in records is not a DetectorAuditRecord.
    ValueError
        If any record has an impossible stage/progression combination
        or an unknown FailedStage value.
    """
    selected: list[DetectorAuditRecord] = []
    for record in records:
        if is_audit_worthy(record):
            selected.append(record)
    return tuple(selected)
