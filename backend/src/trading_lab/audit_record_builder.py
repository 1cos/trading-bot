"""Detector Audit Record Builder.

Pure transformation:

    runner result dict  →  DetectorAuditRecord/v1

Converts one strategy runner result (VALID or INVALID) into a
DetectorAuditRecord/v1 contract instance using only information
already present in the runner result and its embedded DetectionResult.

Does NOT re-run detector stages, infer from raw candles, decide
audit-worthiness, or generate human-readable explanations.

Deterministic identity
----------------------
The audit_id is a UUID v5 derived from stable identity fields:
symbol, session_date, timeframe, direction, candidate_status,
and engine_version.  Same inputs always produce the same audit_id.

Progression flag derivation
---------------------------
Each flag is derived from typed DetectionResult fields only:

    reached_orb:            level_price is not None
    reached_break:          break_bar is not None
    reached_displacement:   displacement_bar_count is not None
    reached_retest:         retest_bar_count is not None
    reached_rejection_scan: failed_retest_count is not None

Known limitation
----------------
SEQUENCE_INVALIDATED failures produce a DetectionResult with
failed_stage=None (the FailedStage enum lacks this member) and
empty failed_rules.  This combination violates the audit record's
INV-A-09 invariant (REJECTED must have a non-null failed_stage or
non-empty failed_rules).  The builder raises ValueError for these
cases until the FailedStage enum is extended.

Public API
----------
    build_detector_audit_record(runner_result) → DetectorAuditRecord
"""

from __future__ import annotations

import json
import uuid

from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.detector_audit_record import (
    CandidateStatus,
    DetectorAuditRecord,
)
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
)
from trading_lab.contracts.primitives import PriceTicks


# ── Deterministic identity ───────────────────────────────────────────────────

_AUDIT_ID_NAMESPACE = uuid.UUID("c3d4e5f6-7890-4bcd-aef0-123456789abc")


def _deterministic_audit_id(fields: dict) -> str:
    """Generate a deterministic UUID v5 from audit identity fields.

    Uses the same convention as research_batch_runner:
    compact JSON with sorted keys.
    """
    canonical = json.dumps(
        {"type": "DetectorAuditRecord/v1", **fields},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return str(uuid.uuid5(_AUDIT_ID_NAMESPACE, canonical))


# ── Attribute access helper ──────────────────────────────────────────────────

def _get(obj, attr, default=None):
    """Access attribute from dict or object."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


# ── Timeframe derivation ────────────────────────────────────────────────────

def _timeframe_str(seconds: int) -> str:
    """Convert timeframe_seconds to canonical string (e.g. 300 → '5m')."""
    minutes = seconds // 60
    return f"{minutes}m"


# ── Primary export ───────────────────────────────────────────────────────────

def build_detector_audit_record(
    runner_result: dict,
) -> DetectorAuditRecord:
    """Convert one strategy runner result into a DetectorAuditRecord/v1.

    Parameters
    ----------
    runner_result : dict
        One result record from ``run_bdrr_strategy``.
        Must contain a ``detection_result`` key holding a typed
        ``DetectionResult`` instance.

    Returns
    -------
    DetectorAuditRecord
        Immutable audit record.

    Raises
    ------
    TypeError
        If runner_result is not a dict or detection_result is not
        a DetectionResult instance.
    ValueError
        If required identity fields are missing, detection status
        is inconsistent, or the result cannot satisfy audit record
        invariants (e.g. SEQUENCE_INVALIDATED edge case).
    """
    # ── Input validation ─────────────────────────────────────────────────
    if not isinstance(runner_result, dict):
        raise TypeError(
            f"runner_result must be a dict, "
            f"got {type(runner_result).__name__}"
        )

    dr = runner_result.get("detection_result")
    if dr is None:
        raise ValueError(
            "runner_result must contain a non-null detection_result"
        )
    if not isinstance(dr, DetectionResult):
        raise TypeError(
            f"detection_result must be a DetectionResult instance, "
            f"got {type(dr).__name__}"
        )

    # ── Identity fields ──────────────────────────────────────────────────
    symbol = runner_result.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise ValueError(
            "runner_result must contain a non-empty 'symbol' string"
        )

    session_date = runner_result.get("session_date")
    if not isinstance(session_date, str) or not session_date:
        raise ValueError(
            "runner_result must contain a non-empty 'session_date' string"
        )

    # Direction from the DetectionResult
    direction = dr.direction
    if direction is None:
        raise ValueError(
            "detection_result.direction must not be None"
        )
    if not isinstance(direction, Direction):
        raise ValueError(
            f"detection_result.direction must be a Direction enum member, "
            f"got {type(direction).__name__}"
        )

    # Timeframe from DR.session.timeframe_seconds
    session_meta = dr.session
    tf_seconds = session_meta.timeframe_seconds
    timeframe = _timeframe_str(tf_seconds)

    # ── Status mapping ───────────────────────────────────────────────────
    dr_status = dr.status
    if dr_status == DetectionStatus.VALID:
        candidate_status = CandidateStatus.VALID
    elif dr_status == DetectionStatus.INVALID:
        candidate_status = CandidateStatus.REJECTED
    else:
        raise ValueError(
            f"Unsupported detection status: {dr_status!r}"
        )

    # ── Consistency check ────────────────────────────────────────────────
    runner_detection_status = runner_result.get("detection_status")
    if runner_detection_status is not None:
        expected = "VALID" if candidate_status == CandidateStatus.VALID \
            else "INVALID"
        if str(runner_detection_status) != expected:
            raise ValueError(
                f"runner_result detection_status '{runner_detection_status}' "
                f"is inconsistent with DetectionResult status "
                f"'{dr_status}'"
            )

    # ── Failed stage and rules ───────────────────────────────────────────
    if candidate_status == CandidateStatus.VALID:
        failed_stage = None
        failed_rules: tuple = ()
    else:
        failed_stage = dr.failed_stage  # FailedStage | None
        failed_rules = dr.failed_rules  # tuple[RuleFailure, ...]

        # INV-A-09 pre-check: REJECTED must have at least one indicator
        if failed_stage is None and len(failed_rules) == 0:
            raise ValueError(
                "Cannot build audit record for REJECTED result: "
                "DetectionResult has failed_stage=None and empty "
                "failed_rules. This typically occurs for "
                "SEQUENCE_INVALIDATED failures where the FailedStage "
                "enum lacks coverage. Extend the FailedStage enum to "
                "resolve this."
            )

    # ── Progression flags ────────────────────────────────────────────────
    reached_orb = dr.level_price is not None
    reached_break = dr.break_bar is not None
    reached_displacement = dr.displacement_bar_count is not None
    reached_retest = dr.retest_bar_count is not None
    reached_rejection_scan = dr.failed_retest_count is not None

    # ── ORB prices ───────────────────────────────────────────────────────
    # level_bar is the ORB candle; extract high and low as PriceTicks
    orb_high = None
    orb_low = None
    if dr.level_bar is not None:
        orb_high = dr.level_bar.high
        orb_low = dr.level_bar.low

    # ── Timestamps ───────────────────────────────────────────────────────
    orb_candle_time_ms = None
    if dr.level_bar is not None:
        orb_candle_time_ms = dr.level_bar.bar_utc_ms

    break_candle_time_ms = None
    if dr.break_bar is not None:
        break_candle_time_ms = dr.break_bar.bar_utc_ms

    # last_relevant_time_ms: the latest timestamp from the most
    # advanced stage that was reached
    last_relevant_time_ms = None
    if candidate_status == CandidateStatus.VALID and \
            dr.confirmation_bar is not None:
        last_relevant_time_ms = dr.confirmation_bar.bar_utc_ms
    elif dr.retest_window and len(dr.retest_window) > 0:
        last_relevant_time_ms = dr.retest_window[-1].bar_utc_ms
    elif dr.displacement_window and len(dr.displacement_window) > 0:
        last_relevant_time_ms = dr.displacement_window[-1].bar_utc_ms
    elif dr.break_bar is not None:
        last_relevant_time_ms = dr.break_bar.bar_utc_ms
    elif dr.level_bar is not None:
        last_relevant_time_ms = dr.level_bar.bar_utc_ms

    # ── Deterministic audit_id ───────────────────────────────────────────
    audit_id = _deterministic_audit_id({
        "symbol": symbol,
        "session_date": session_date,
        "timeframe": timeframe,
        "direction": str(direction),
        "candidate_status": str(candidate_status),
        "engine_version": dr.engine_version,
    })

    # ── Build the record ─────────────────────────────────────────────────
    return DetectorAuditRecord(
        schema_version="DetectorAuditRecord/v1",
        audit_id=audit_id,
        symbol=symbol,
        session_date=session_date,
        timeframe=timeframe,
        direction=direction,
        candidate_status=candidate_status,
        failed_stage=failed_stage,
        failed_rules=failed_rules,
        reached_orb=reached_orb,
        reached_break=reached_break,
        reached_displacement=reached_displacement,
        reached_retest=reached_retest,
        reached_rejection_scan=reached_rejection_scan,
        orb_high=orb_high,
        orb_low=orb_low,
        orb_candle_time_ms=orb_candle_time_ms,
        break_candle_time_ms=break_candle_time_ms,
        last_relevant_time_ms=last_relevant_time_ms,
        detection_result=dr,
    )
