"""Detector Audit Visual Event Exporter.

Pure transformation:

    DetectorAuditRecord/v1  +  source session candles
        ↓
    DetectorAuditVisualEvent/v1-compatible dictionary

The output contains everything a later audit batch HTML generator
needs to render a chart with rejection diagnosis overlays.

Conventions follow the existing visual_review_exporter where possible:
same candle shape, same annotation index-mapping, same ORB tick export.

Does NOT generate HTML, render charts, produce persistence, or mutate inputs.

Public API
----------
    export_audit_visual_event(record, candles) → dict
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.detector_audit_record import (
    CandidateStatus,
    DetectorAuditRecord,
)
from trading_lab.contracts.enums import FailedStage
from trading_lab.contracts.rule_failure import RejectionAttempt, RuleFailure


# ── Schema constant ──────────────────────────────────────────────────────────

_SCHEMA_VERSION = "DetectorAuditVisualEvent/v1"


# ── Attribute access helper ──────────────────────────────────────────────────

def _get(obj, attr, default=None):
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


# ── Candle helpers ───────────────────────────────────────────────────────────

def _validate_candles(candles: object) -> list[dict]:
    """Validate and return a list of candle dicts with ordered timestamps."""
    if not isinstance(candles, (list, tuple, Sequence)):
        raise TypeError(
            f"candles must be a sequence, got {type(candles).__name__}"
        )

    result = list(candles)
    prev_ms = None
    for i, c in enumerate(result):
        if not isinstance(c, dict):
            raise TypeError(
                f"candles[{i}] must be a dict, got {type(c).__name__}"
            )
        ms = c.get("time_ms")
        if ms is None or not isinstance(ms, (int, float)):
            raise ValueError(
                f"candles[{i}] missing or invalid 'time_ms'"
            )
        for field in ("open", "high", "low", "close"):
            if field not in c:
                raise ValueError(
                    f"candles[{i}] missing required field '{field}'"
                )
        if prev_ms is not None:
            if ms == prev_ms:
                raise ValueError(
                    f"candles[{i}] has duplicate time_ms={ms}"
                )
            if ms < prev_ms:
                raise ValueError(
                    f"candles[{i}] time_ms={ms} is not strictly "
                    f"increasing (previous={prev_ms})"
                )
        prev_ms = ms

    return result


def _serialize_candle(candle: dict, index: int) -> dict:
    """Convert a raw session candle to the visual event format.

    Matches the existing visual_review_exporter convention.
    """
    return {
        "index": index,
        "time_ms": candle["time_ms"],
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "close": candle["close"],
        "volume": candle.get("volume"),
    }


def _build_time_index(candles: list[dict]) -> dict[int, int]:
    """Build a time_ms → candle index mapping."""
    return {c["time_ms"]: i for i, c in enumerate(candles)}


def _find_index(
    time_index: dict[int, int],
    bar: object,
    label: str,
) -> int | None:
    """Map a typed Bar's bar_utc_ms to a candle index.

    Returns None if bar is None or bar_utc_ms is None.
    Raises ValueError if the timestamp is not found in candles.
    """
    if bar is None:
        return None
    ms = _get(bar, "bar_utc_ms")
    if ms is None:
        return None
    idx = time_index.get(ms)
    if idx is None:
        raise ValueError(
            f"Referenced {label} bar_utc_ms={ms} not found in candles"
        )
    return idx


def _find_index_ms(
    time_index: dict[int, int],
    ms: int | None,
    label: str,
) -> int | None:
    """Map a raw timestamp to a candle index."""
    if ms is None:
        return None
    idx = time_index.get(ms)
    if idx is None:
        raise ValueError(
            f"Referenced {label} time_ms={ms} not found in candles"
        )
    return idx


# ── ORB export ───────────────────────────────────────────────────────────────

def _export_orb(record: DetectorAuditRecord) -> dict:
    """Export ORB data from the audit record."""
    return {
        "orb_high_ticks": record.orb_high.ticks if record.orb_high else None,
        "orb_low_ticks": record.orb_low.ticks if record.orb_low else None,
        "orb_candle_time_ms": record.orb_candle_time_ms,
    }


# ── Annotation builder ──────────────────────────────────────────────────────

def _build_annotations(
    dr: DetectionResult,
    time_index: dict[int, int],
    is_valid: bool,
) -> dict:
    """Build machine-readable annotations from DetectionResult fields."""
    ann: dict = {}

    # ── Break ────────────────────────────────────────────────────────────
    break_idx = _find_index(time_index, dr.break_bar, "break")
    ann["break_candle_index"] = break_idx
    ann["break_candle_time_ms"] = (
        dr.break_bar.bar_utc_ms if dr.break_bar else None
    )

    # ── Displacement window ──────────────────────────────────────────────
    dw = dr.displacement_window
    if dw and len(dw) > 0:
        ann["displacement_start_index"] = _find_index(
            time_index, dw[0], "displacement_start"
        )
        ann["displacement_end_index"] = _find_index(
            time_index, dw[-1], "displacement_end"
        )
    else:
        ann["displacement_start_index"] = None
        ann["displacement_end_index"] = None

    # ── Retest window ────────────────────────────────────────────────────
    rw = dr.retest_window
    if rw and len(rw) > 0:
        ann["retest_start_index"] = _find_index(
            time_index, rw[0], "retest_start"
        )
        ann["retest_end_index"] = _find_index(
            time_index, rw[-1], "retest_end"
        )
    else:
        ann["retest_start_index"] = None
        ann["retest_end_index"] = None

    # ── Confirmation / rejection candle ──────────────────────────────────
    conf_idx = _find_index(time_index, dr.confirmation_bar, "confirmation")
    ann["confirmation_candle_index"] = conf_idx
    ann["confirmation_candle_time_ms"] = (
        dr.confirmation_bar.bar_utc_ms if dr.confirmation_bar else None
    )

    return ann


# ── Failed retests export ────────────────────────────────────────────────────

def _export_failed_retests(
    dr: DetectionResult,
    time_index: dict[int, int],
) -> list[dict]:
    """Export failed retest attempts from the DetectionResult."""
    if not dr.failed_retests:
        return []

    result = []
    for attempt in dr.failed_retests:
        bar_idx = _find_index(time_index, attempt.bar, "failed_retest")
        bar_ms = attempt.bar.bar_utc_ms if attempt.bar else None

        rules = []
        for rf in attempt.failed_rules:
            rules.append({
                "rule_id": rf.rule_id,
                "stage": str(rf.stage),
                "message": rf.message,
            })

        result.append({
            "candle_index": bar_idx,
            "candle_time_ms": bar_ms,
            "failed_rules": rules,
        })

    return result


# ── Failed rules export ─────────────────────────────────────────────────────

def _export_failed_rules(rules: tuple[RuleFailure, ...]) -> list[dict]:
    """Export top-level failed rules to serializable dicts."""
    return [
        {
            "rule_id": rf.rule_id,
            "stage": str(rf.stage),
            "message": rf.message,
        }
        for rf in rules
    ]


# ── Primary export function ─────────────────────────────────────────────────

def export_audit_visual_event(
    record: DetectorAuditRecord,
    candles: list[dict],
) -> dict:
    """Export a single audit visual event payload.

    Parameters
    ----------
    record : DetectorAuditRecord
        A valid DetectorAuditRecord/v1 instance.
    candles : list[dict]
        Raw session candles with keys: time_ms, open, high, low, close,
        and optionally volume. Must be strictly ordered by time_ms.

    Returns
    -------
    dict
        Deterministic, JSON-serializable event payload.

    Raises
    ------
    TypeError
        If record or candles have wrong types.
    ValueError
        If candles are unordered, have duplicates, or referenced
        detector bars are missing.

    Does not mutate inputs.
    """
    if not isinstance(record, DetectorAuditRecord):
        raise TypeError(
            f"record must be a DetectorAuditRecord, "
            f"got {type(record).__name__}"
        )

    validated_candles = _validate_candles(candles)
    time_index = _build_time_index(validated_candles)

    dr = record.detection_result
    is_valid = record.candidate_status == CandidateStatus.VALID

    # Serialize candles
    serialized_candles = [
        _serialize_candle(c, i) for i, c in enumerate(validated_candles)
    ]

    # ORB
    orb = _export_orb(record)

    # Level source from DR
    level_source = str(dr.level_source) if dr.level_source else None
    level_price_ticks = dr.level_price.ticks if dr.level_price else None

    # Annotations
    annotations = _build_annotations(dr, time_index, is_valid)

    # Failed retests
    failed_retests = _export_failed_retests(dr, time_index)

    # Failed stage and rules
    failed_stage_str = (
        record.failed_stage.value if record.failed_stage else None
    )
    failed_rules = _export_failed_rules(record.failed_rules)

    return {
        "schema_version": _SCHEMA_VERSION,
        "audit_id": record.audit_id,
        "symbol": record.symbol,
        "session_date": record.session_date,
        "timeframe": record.timeframe,
        "direction": str(record.direction),
        "candidate_status": str(record.candidate_status),
        "failed_stage": failed_stage_str,
        "failed_rules": failed_rules,
        "reached_orb": record.reached_orb,
        "reached_break": record.reached_break,
        "reached_displacement": record.reached_displacement,
        "reached_retest": record.reached_retest,
        "reached_rejection_scan": record.reached_rejection_scan,
        "level_source": level_source,
        "level_price_ticks": level_price_ticks,
        "orb_high_ticks": orb["orb_high_ticks"],
        "orb_low_ticks": orb["orb_low_ticks"],
        "orb_candle_time_ms": orb["orb_candle_time_ms"],
        "candles": serialized_candles,
        "annotations": annotations,
        "failed_retests": failed_retests,
    }
