"""Deterministic Historical Research Dataset exporter.

Consumes Strategy Runner result records read-only and produces one flat
research row per valid setup candidate.  Completely independent from
TradeDataset/v1.

Public API:
    build_research_rows(runner_results, *, source_dataset_id, code_commit_hash)
    serialize_research_csv(rows)
    RESEARCH_DATASET_SCHEMA_VERSION
    RESEARCH_EXPORTER_VERSION
    FROZEN_COLUMNS
    ResearchDatasetValidationError
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal, ROUND_HALF_EVEN

# ── Constants ────────────────────────────────────────────────────────────────

RESEARCH_DATASET_SCHEMA_VERSION = "ResearchDataset/v1"
RESEARCH_EXPORTER_VERSION = "ResearchDatasetExporter/v1"

RATIO_DECIMAL_PLACES = 12
ROUNDING_MODE = ROUND_HALF_EVEN

FROZEN_COLUMNS = (
    "research_dataset_schema_version",
    "research_exporter_version",
    "source_dataset_id",
    "code_commit_hash",
    "symbol",
    "session_date",
    "preset_id",
    "candidate_id",
    "result_id",
    "direction",
    "detection_schema_version",
    "trade_plan_schema_version",
    "trade_outcome_schema_version",
    "engine_version",
    "session_open_utc_ms",
    "session_close_utc_ms",
    "break_bar_utc_ms",
    "confirmation_bar_utc_ms",
    "entry_bar_utc_ms",
    "exit_bar_utc_ms",
    "displacement_bar_count",
    "retest_bar_count",
    "bars_break_to_confirmation",
    "bars_break_to_first_retest",
    "level_price_ticks",
    "tick_size",
    "displacement_ticks",
    "minimum_rejection_side_clearance_ticks",
    "rejection_side_clearance_ratio_to_displacement",
    "average_rejection_side_clearance",
    "retest_penetration_through_level_ticks",
    "retest_retracement_pct_numerator",
    "retest_retracement_pct_denominator",
    "retest_closest_approach_ticks",
    "directional_break_distance_ticks",
    "failed_retest_count",
    "confirmation_rejection_wick_numerator",
    "confirmation_rejection_wick_denominator",
    "confirmation_body_numerator",
    "confirmation_body_denominator",
    "confirmation_favorable_close_location_numerator",
    "confirmation_favorable_close_location_denominator",
    "entry_price_ticks",
    "stop_price_ticks",
    "risk_ticks",
    "r2_price_ticks",
    "exit_target_r",
    "outcome",
    "entry_triggered",
    "realized_r",
    "exit_price_ticks",
    "highest_target_achieved",
)

_FROZEN_COLUMNS_SET = frozenset(FROZEN_COLUMNS)


# ── Exception ────────────────────────────────────────────────────────────────

class ResearchDatasetValidationError(Exception):
    """Raised when a runner record fails research-dataset validation."""


# ── Ratio computation ────────────────────────────────────────────────────────

_QUANT = Decimal("0." + "0" * RATIO_DECIMAL_PLACES)


def _compute_ratio(numerator_ticks: int, denominator_ticks: int) -> str:
    """Compute clearance-to-displacement ratio as a fixed-point Decimal string.

    Returns exactly RATIO_DECIMAL_PLACES digits after the decimal point.
    """
    if denominator_ticks == 0:
        raise ResearchDatasetValidationError(
            "displacement_ticks is zero; cannot compute "
            "rejection_side_clearance_ratio_to_displacement"
        )
    ratio = Decimal(numerator_ticks) / Decimal(denominator_ticks)
    quantized = ratio.quantize(_QUANT, rounding=ROUNDING_MODE)
    # Decimal(0).quantize() may produce "0E-12"; normalize to fixed-point
    result = format(quantized, "f")
    return result


# ── Record consistency validation ────────────────────────────────────────────

def _has_valid_detection(record: dict) -> bool:
    """Check if detection_result is present and VALID."""
    dr = record.get("detection_result")
    if dr is None:
        return False
    return str(getattr(dr, "status", None)) == "VALID"


def _classify_and_validate(record: dict, index: int) -> str:
    """Classify a runner record and enforce consistency.

    Returns one of: "ELIGIBLE", "LEGITIMATE_SKIP".
    Raises ResearchDatasetValidationError for contradictory records.
    """
    dr = record.get("detection_result")
    tp = record.get("trade_plan")
    to = record.get("trade_outcome")
    cid = record.get("candidate_id")
    outcome = str(record.get("outcome", ""))

    has_dr = dr is not None
    has_tp = tp is not None
    has_to = to is not None
    has_cid = cid is not None
    dr_valid = _has_valid_detection(record)

    # ── Full setup: all four present ─────────────────────────────────────
    if has_dr and has_tp and has_to and has_cid:
        if not dr_valid:
            raise ResearchDatasetValidationError(
                f"record[{index}]: detection_result.status is not VALID "
                f"but candidate_id, trade_plan, and trade_outcome are present"
            )
        if dr.minimum_rejection_side_clearance is None:
            raise ResearchDatasetValidationError(
                f"record[{index}]: minimum_rejection_side_clearance is null "
                f"for a VALID detection"
            )
        if dr.displacement_pts is None:
            raise ResearchDatasetValidationError(
                f"record[{index}]: displacement_pts is null "
                f"for a VALID detection"
            )
        return "ELIGIBLE"

    # ── Legitimate NO_VALID_SETUP: detection present but INVALID,
    #    no trade artifacts ────────────────────────────────────────────────
    if outcome == "NO_VALID_SETUP":
        if has_tp:
            raise ResearchDatasetValidationError(
                f"record[{index}]: NO_VALID_SETUP must not contain "
                f"a trade_plan"
            )
        if has_to:
            raise ResearchDatasetValidationError(
                f"record[{index}]: NO_VALID_SETUP must not contain "
                f"a trade_outcome"
            )
        if has_cid:
            raise ResearchDatasetValidationError(
                f"record[{index}]: NO_VALID_SETUP must not contain "
                f"a candidate_id"
            )
        return "LEGITIMATE_SKIP"

    # ── Legitimate PIPELINE_FAILURE: no setup artifacts ──────────────────
    if outcome == "PIPELINE_FAILURE":
        if has_dr and dr_valid:
            raise ResearchDatasetValidationError(
                f"record[{index}]: PIPELINE_FAILURE must not contain "
                f"a VALID detection_result"
            )
        if has_tp:
            raise ResearchDatasetValidationError(
                f"record[{index}]: PIPELINE_FAILURE must not contain "
                f"a trade_plan"
            )
        if has_to:
            raise ResearchDatasetValidationError(
                f"record[{index}]: PIPELINE_FAILURE must not contain "
                f"a trade_outcome"
            )
        if has_cid:
            raise ResearchDatasetValidationError(
                f"record[{index}]: PIPELINE_FAILURE must not contain "
                f"a candidate_id"
            )
        return "LEGITIMATE_SKIP"

    # ── Contradictory incomplete setups ──────────────────────────────────

    # candidate_id present but missing one or more setup objects
    if has_cid:
        missing = []
        if not has_dr:
            missing.append("detection_result")
        if not has_tp:
            missing.append("trade_plan")
        if not has_to:
            missing.append("trade_outcome")
        if missing:
            raise ResearchDatasetValidationError(
                f"record[{index}]: candidate_id is present but "
                f"missing: {', '.join(missing)}"
            )

    # valid detection present but missing downstream artifacts
    if dr_valid:
        missing = []
        if not has_tp:
            missing.append("trade_plan")
        if not has_to:
            missing.append("trade_outcome")
        if not has_cid:
            missing.append("candidate_id")
        if missing:
            raise ResearchDatasetValidationError(
                f"record[{index}]: detection_result is VALID but "
                f"missing: {', '.join(missing)}"
            )

    # trade_plan present without valid detection
    if has_tp and not dr_valid:
        raise ResearchDatasetValidationError(
            f"record[{index}]: trade_plan is present but "
            f"detection_result is missing or not VALID"
        )

    # trade_outcome present without valid detection or trade_plan
    if has_to and (not dr_valid or not has_tp):
        raise ResearchDatasetValidationError(
            f"record[{index}]: trade_outcome is present but "
            f"detection_result or trade_plan is missing"
        )

    # Any other non-setup record — legitimate skip
    return "LEGITIMATE_SKIP"


def _extract_row(
    record: dict,
    source_dataset_id: str,
    code_commit_hash: str,
) -> dict:
    """Extract a flat research row from a validated runner record."""
    dr = record["detection_result"]
    tp = record["trade_plan"]
    to = record["trade_outcome"]
    session = dr.session

    min_rsc_ticks = dr.minimum_rejection_side_clearance.ticks
    disp_ticks = dr.displacement_pts.ticks

    ratio = _compute_ratio(min_rsc_ticks, disp_ticks)

    return {
        "research_dataset_schema_version": RESEARCH_DATASET_SCHEMA_VERSION,
        "research_exporter_version": RESEARCH_EXPORTER_VERSION,
        "source_dataset_id": source_dataset_id,
        "code_commit_hash": code_commit_hash,
        "symbol": record["symbol"],
        "session_date": record["session_date"],
        "preset_id": record["preset_id"],
        "candidate_id": record["candidate_id"],
        "result_id": dr.result_id,
        "direction": str(dr.direction),
        "detection_schema_version": dr.schema_version,
        "trade_plan_schema_version": tp.schema_version,
        "trade_outcome_schema_version": to.schema_version,
        "engine_version": dr.engine_version,
        "session_open_utc_ms": session.session_open_utc_ms,
        "session_close_utc_ms": session.session_close_utc_ms,
        "break_bar_utc_ms": dr.break_bar.bar_utc_ms,
        "confirmation_bar_utc_ms": dr.confirmation_bar.bar_utc_ms,
        "entry_bar_utc_ms": to.entry_bar_utc_ms,
        "exit_bar_utc_ms": to.exit_bar_utc_ms,
        "displacement_bar_count": dr.displacement_bar_count,
        "retest_bar_count": dr.retest_bar_count,
        "bars_break_to_confirmation": dr.bars_break_to_confirmation,
        "bars_break_to_first_retest": dr.bars_break_to_first_retest,
        "level_price_ticks": dr.level_price.ticks,
        "tick_size": dr.level_price.tick_size,
        "displacement_ticks": disp_ticks,
        "minimum_rejection_side_clearance_ticks": min_rsc_ticks,
        "rejection_side_clearance_ratio_to_displacement": ratio,
        "average_rejection_side_clearance": dr.average_rejection_side_clearance,
        "retest_penetration_through_level_ticks":
            dr.retest_penetration_through_level.ticks,
        "retest_retracement_pct_numerator":
            dr.retest_displacement_retracement_pct.numerator,
        "retest_retracement_pct_denominator":
            dr.retest_displacement_retracement_pct.denominator,
        "retest_closest_approach_ticks": dr.retest_closest_approach.ticks,
        "directional_break_distance_ticks":
            dr.directional_break_distance.ticks,
        "failed_retest_count": dr.failed_retest_count,
        "confirmation_rejection_wick_numerator":
            dr.confirmation_rej_wick.numerator,
        "confirmation_rejection_wick_denominator":
            dr.confirmation_rej_wick.denominator,
        "confirmation_body_numerator": dr.confirmation_body.numerator,
        "confirmation_body_denominator": dr.confirmation_body.denominator,
        "confirmation_favorable_close_location_numerator":
            dr.confirmation_favorable_close_location.numerator,
        "confirmation_favorable_close_location_denominator":
            dr.confirmation_favorable_close_location.denominator,
        "entry_price_ticks": tp.entry_price.ticks,
        "stop_price_ticks": tp.stop_price.ticks,
        "risk_ticks": tp.risk.ticks,
        "r2_price_ticks": tp.r2_price.ticks,
        "exit_target_r": to.selected_exit_target_r,
        "outcome": str(to.outcome),
        "entry_triggered": to.entry_triggered,
        "realized_r": to.realized_r,
        "exit_price_ticks": to.exit_price_ticks,
        "highest_target_achieved": to.highest_target_achieved,
    }


# ── Public API ───────────────────────────────────────────────────────────────

def build_research_rows(
    runner_results: list,
    *,
    source_dataset_id: str,
    code_commit_hash: str,
) -> tuple[dict, ...]:
    """Build flat research rows from Strategy Runner results.

    Parameters
    ----------
    runner_results : list
        Result records from ``run_bdrr_strategy()``.  Read-only.
    source_dataset_id : str
        Caller-supplied dataset identifier.  Must be non-empty.
    code_commit_hash : str
        Caller-supplied commit hash.  Must be non-empty.

    Returns
    -------
    tuple[dict, ...]
        One dict per eligible valid setup, in runner-result order.
    """
    if not isinstance(runner_results, list):
        raise TypeError(
            "runner_results must be a list, "
            f"got {type(runner_results).__name__}"
        )
    if not isinstance(source_dataset_id, str) or len(source_dataset_id) == 0:
        raise ResearchDatasetValidationError(
            "source_dataset_id must be a non-empty string"
        )
    if not isinstance(code_commit_hash, str) or len(code_commit_hash) == 0:
        raise ResearchDatasetValidationError(
            "code_commit_hash must be a non-empty string"
        )

    rows = []
    for i, record in enumerate(runner_results):
        classification = _classify_and_validate(record, i)
        if classification == "ELIGIBLE":
            rows.append(
                _extract_row(record, source_dataset_id, code_commit_hash)
            )

    return tuple(rows)


def serialize_research_csv(rows: tuple | list) -> str:
    """Serialize research rows to a deterministic CSV string.

    Parameters
    ----------
    rows : tuple or list
        Research row dicts as returned by ``build_research_rows()``.

    Returns
    -------
    str
        Complete CSV string with header, ending with exactly one newline.
    """
    if not isinstance(rows, (tuple, list)):
        raise TypeError(
            "rows must be a tuple or list, "
            f"got {type(rows).__name__}"
        )

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")

    # Header
    writer.writerow(FROZEN_COLUMNS)

    for row in rows:
        if not isinstance(row, dict):
            raise ResearchDatasetValidationError(
                f"each row must be a dict, got {type(row).__name__}"
            )

        row_keys = set(row.keys())
        extra = row_keys - _FROZEN_COLUMNS_SET
        if extra:
            raise ResearchDatasetValidationError(
                f"unknown row keys: {sorted(extra)}"
            )
        missing = _FROZEN_COLUMNS_SET - row_keys
        if missing:
            raise ResearchDatasetValidationError(
                f"missing required row keys: {sorted(missing)}"
            )

        values = []
        for col in FROZEN_COLUMNS:
            v = row[col]
            if v is None:
                values.append("")
            elif isinstance(v, bool):
                values.append("true" if v else "false")
            else:
                values.append(str(v))
        writer.writerow(values)

    return output.getvalue()
