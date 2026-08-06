"""Canonical TradePlan/v1 builder for the BDRR pipeline.

Ported from ``buildTradePlan`` in estrategie/bdrr_trade_plan.js
(lines 197–277).

Exports one public function:

    build_trade_plan(detection_result, config)

This module is deliberately isolated:
  - No import of Stage 1–5 detection logic.
  - No market data access of any kind.
  - No inspection of candles after the confirmation bar.
  - No outcome evaluation.
  - No mutation of caller-owned objects.
  - No UUID or timestamp generation.
  - No randomness or external state.

Input contract:
    detection_result must be a canonical DetectionResult/v1 instance
    (or a dict-like object whose fields are validated against the
    canonical schema).

    config must be a TradePlanConfig instance (or a dict-like object).

Output contract:
    Success: {"status": "OK", "trade_plan": TradePlan(...)}
    Failure: {"status": "FAILED", "failure_code": str, "reason": str}

    Never raises for ordinary validation failures.

Supported configuration:
    direction:          "LONG"  (SHORT → structured UNSUPPORTED_DIRECTION)
    entry_model:        "CONFIRMATION_CLOSE" | "BREAK_OF_SIGNAL_BAR"
    entry_buffer_ticks: int >= 0
    stop_buffer_ticks:  int >= 0
    tick_size:          positive finite float/int (numeric)

Run tests: python -m pytest backend/tests/test_trade_plan_builder.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_lab.contracts.distances import AbsoluteTickDistance
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.contracts.trade_plan import EntryModel, TradePlan


# ── TradePlanConfig ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TradePlanConfig:
    """Configuration for build_trade_plan.

    Mirrors the five required keys of the JavaScript config object
    passed to buildTradePlan in estrategie/bdrr_trade_plan.js.
    """

    direction: str
    entry_model: str
    entry_buffer_ticks: object  # validated at runtime, not constrained here
    stop_buffer_ticks: object   # validated at runtime, not constrained here
    tick_size: object            # validated at runtime, not constrained here


# ── Failure wrapper ──────────────────────────────────────────────────────────


def _fail(code: str, reason: str) -> dict:
    """Structured failure — mirrors JS ``fail(code, reason)``."""
    return {"status": "FAILED", "failure_code": code, "reason": reason}


# ── Validation helpers ───────────────────────────────────────────────────────


def _is_non_negative_integer(v: object) -> bool:
    """Match JS ``isNonNegativeInteger``.

    Rejects bools — JS Number.isInteger(true) is true, but the
    canonical JS function guards ``typeof v === 'number'`` which
    excludes booleans.  Python bool is a subclass of int, so we
    must reject it explicitly.
    """
    if isinstance(v, bool):
        return False
    return isinstance(v, int) and v >= 0


def _is_positive_finite_number(v: object) -> bool:
    """Match JS ``isPositiveFiniteNumber``.

    Accepts int or float, rejects bool, NaN, Inf, non-positive.
    """
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return v > 0
    if isinstance(v, float):
        return math.isfinite(v) and v > 0
    return False


# ── JS-compatible string rendering ──────────────────────────────────────────


def _js_str(v: object) -> str:
    """Approximate JS template-literal rendering of a value.

    Used only in failure reason strings to match the canonical JS
    output for common Python types.
    """
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
    return str(v)


# ── DetectionResult field access ─────────────────────────────────────────────
# The builder must work with both canonical DetectionResult contract instances
# and plain dict-like objects for validation testing.  These accessors handle
# both forms transparently.


def _get(obj: object, attr: str, default: object = None) -> object:
    """Get an attribute from a dataclass, object, or dict."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _has_obj_shape(v: object) -> bool:
    """Check if v is a non-None object (dict or instance with attributes)."""
    if v is None:
        return False
    if isinstance(v, (str, int, float, bool, list, tuple)):
        return False
    return True


# ── Input validation ─────────────────────────────────────────────────────────


def _validate_detection_result(dr: object) -> dict | None:
    """Validate detection_result — mirrors JS validateDetectionResult."""
    if not _has_obj_shape(dr):
        return _fail(
            "INVALID_DETECTION_RESULT",
            "detectionResult must be a non-null object",
        )
    schema_version = _get(dr, "schema_version")
    if str(schema_version) != "DetectionResult/v1":
        return _fail(
            "INVALID_DETECTION_RESULT",
            f'detectionResult.schema_version must be "DetectionResult/v1";'
            f' got "{schema_version}"',
        )
    status = _get(dr, "status")
    status_str = str(status) if status is not None else str(status)
    if status_str != "VALID":
        failed_stage = _get(dr, "failed_stage")
        reason = (
            f'detectionResult.status must be "VALID"; got "{status_str}"'
        )
        if failed_stage:
            reason += f" (failed_stage: {failed_stage})"
        return _fail("INVALID_DETECTION_RESULT", reason)
    return None


def _validate_config(config: object) -> dict | None:
    """Validate config — mirrors JS validateConfig."""
    if not _has_obj_shape(config):
        return _fail(
            "INVALID_DETECTION_RESULT",
            "config must be a non-null object",
        )

    direction = _get(config, "direction")
    direction_str = str(direction) if direction is not None else str(direction)
    if direction_str not in ("LONG", "SHORT"):
        return _fail(
            "UNSUPPORTED_DIRECTION",
            f'direction "{direction_str}" is not supported;'
            f' only "LONG" and "SHORT" are implemented',
        )

    entry_model = _get(config, "entry_model")
    entry_model_str = str(entry_model) if entry_model is not None else str(entry_model)
    if entry_model_str not in ("CONFIRMATION_CLOSE", "BREAK_OF_SIGNAL_BAR"):
        return _fail(
            "UNSUPPORTED_ENTRY_MODEL",
            f'entry_model "{entry_model_str}" is not recognized; '
            "supported values: CONFIRMATION_CLOSE, BREAK_OF_SIGNAL_BAR",
        )

    tick_size = _get(config, "tick_size")
    if not _is_positive_finite_number(tick_size):
        return _fail(
            "TICK_SIZE_MISMATCH",
            "config.tick_size must be a finite positive number",
        )

    entry_buffer = _get(config, "entry_buffer_ticks")
    if not _is_non_negative_integer(entry_buffer):
        return _fail(
            "INVALID_BUFFER",
            f"entry_buffer_ticks must be a non-negative integer;"
            f" got {_js_str(entry_buffer)}",
        )

    stop_buffer = _get(config, "stop_buffer_ticks")
    if not _is_non_negative_integer(stop_buffer):
        return _fail(
            "INVALID_BUFFER",
            f"stop_buffer_ticks must be a non-negative integer;"
            f" got {_js_str(stop_buffer)}",
        )

    return None


def _validate_confirmation_bar(dr: object, tick_size: object) -> dict | None:
    """Validate confirmation_bar OHLC — mirrors JS validateConfirmationBar."""
    bar = _get(dr, "confirmation_bar")
    if not _has_obj_shape(bar):
        return _fail(
            "MISSING_CONFIRMATION_BAR",
            "detectionResult.confirmation_bar is missing or not an object",
        )

    for field in ("open", "high", "low", "close"):
        pt = _get(bar, field)
        if not _has_obj_shape(pt):
            return _fail(
                "INVALID_TICK_VALUE",
                f"confirmation_bar.{field} must be a PriceTicks object;"
                f" got {_js_str(pt)}",
            )
        pt_ticks = _get(pt, "ticks")
        if isinstance(pt_ticks, bool) or not isinstance(pt_ticks, int):
            return _fail(
                "INVALID_TICK_VALUE",
                f"confirmation_bar.{field}.ticks must be an integer;"
                f" got {pt_ticks}",
            )
        pt_tick_size = _get(pt, "tick_size")
        # For canonical PriceTicks, tick_size is stored as a string.
        # Convert to float for numeric comparison if needed.
        pt_tick_size_num = _tick_size_to_number(pt_tick_size)
        if not _is_positive_finite_number(pt_tick_size_num):
            return _fail(
                "INVALID_TICK_VALUE",
                f"confirmation_bar.{field}.tick_size must be a finite"
                f" positive number; got {pt_tick_size}",
            )
        # tick_size must match config — compare numerically
        tick_size_num = _tick_size_to_number(tick_size)
        if pt_tick_size_num != tick_size_num:
            return _fail(
                "TICK_SIZE_MISMATCH",
                f"confirmation_bar.{field}.tick_size ({pt_tick_size})"
                f" does not match config.tick_size ({tick_size})",
            )

    return None


def _tick_size_to_number(v: object) -> object:
    """Convert a tick_size value to a numeric type for comparison.

    Handles both numeric (float/int) and string representations
    (as stored in canonical Python PriceTicks).
    """
    if isinstance(v, bool):
        return v  # will fail _is_positive_finite_number
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v)
        except (ValueError, TypeError):
            return v  # will fail _is_positive_finite_number
    return v


# ── tick_size canonical string ───────────────────────────────────────────────


def _tick_size_to_str(tick_size: object) -> str:
    """Convert numeric tick_size to the canonical Python string form.

    The JavaScript implementation stores tick_size as a JS number.
    The Python PriceTicks contract stores tick_size as a string.
    This conversion bridges the boundary.

    Uses the same approach as existing code: str(tick_size) for
    float values, which for 0.01 gives "0.01".
    """
    if isinstance(v := tick_size, str):
        return v
    return str(tick_size)


# ── Primary export ───────────────────────────────────────────────────────────


def build_trade_plan(
    detection_result: object,
    config: object,
    stop_override_ticks: int | None = None,
) -> dict:
    """Build a canonical TradePlan/v1 from a DetectionResult/v1.

    Mirrors JS ``buildTradePlan(detectionResult, config)`` in
    estrategie/bdrr_trade_plan.js exactly.

    Parameters
    ----------
    detection_result
        Canonical DetectionResult/v1 contract instance or compatible
        dict-like object.
    config
        TradePlanConfig instance or compatible dict-like object with
        keys: direction, entry_model, entry_buffer_ticks,
        stop_buffer_ticks, tick_size.
    stop_override_ticks : int or None
        When provided, overrides the stop basis derived from the
        confirmation bar.  Used by TWO_CANDLE_ENGULFING_RECOVERY
        where the stop is based on the entire pair's extreme, not
        just the confirmation candle.
        LONG: stop = stop_override_ticks - stop_buffer_ticks.
        SHORT: stop = stop_override_ticks + stop_buffer_ticks.

    Returns
    -------
    dict
        ``{"status": "OK", "trade_plan": TradePlan(...)}`` on success.
        ``{"status": "FAILED", "failure_code": str, "reason": str}``
        on validation failure.

    Never raises for ordinary validation failures.
    Never modifies detection_result or config.
    Never reads market data.
    """
    # ── Step 1: validate inputs ──────────────────────────────────────────

    dr_err = _validate_detection_result(detection_result)
    if dr_err:
        return dr_err

    cfg_err = _validate_config(config)
    if cfg_err:
        return cfg_err

    tick_size = _get(config, "tick_size")

    bar_err = _validate_confirmation_bar(detection_result, tick_size)
    if bar_err:
        return bar_err

    # ── Step 2: read confirmation bar tick values ────────────────────────

    bar = _get(detection_result, "confirmation_bar")
    high_ticks = _get(_get(bar, "high"), "ticks")
    low_ticks = _get(_get(bar, "low"), "ticks")
    close_ticks = _get(_get(bar, "close"), "ticks")

    # ── Step 3: compute entry price ──────────────────────────────────────

    entry_model_str = str(_get(config, "entry_model"))
    entry_buffer = _get(config, "entry_buffer_ticks")
    direction_str = str(_get(config, "direction"))
    is_short = direction_str == "SHORT"

    if is_short:
        if entry_model_str == "CONFIRMATION_CLOSE":
            entry_ticks = close_ticks - entry_buffer
        else:
            # BREAK_OF_SIGNAL_BAR: below the low for SHORT
            entry_ticks = low_ticks - entry_buffer
    else:
        if entry_model_str == "CONFIRMATION_CLOSE":
            entry_ticks = close_ticks + entry_buffer
        else:
            # BREAK_OF_SIGNAL_BAR
            entry_ticks = high_ticks + entry_buffer

    # ── Step 4: compute stop price ───────────────────────────────────────

    stop_buffer = _get(config, "stop_buffer_ticks")
    if stop_override_ticks is not None:
        # TWO_CANDLE_ENGULFING_RECOVERY: stop from pair extreme
        if is_short:
            stop_ticks = stop_override_ticks + stop_buffer
        else:
            stop_ticks = stop_override_ticks - stop_buffer
    else:
        if is_short:
            stop_ticks = high_ticks + stop_buffer
        else:
            stop_ticks = low_ticks - stop_buffer

    # ── Step 5: validate geometric relationship ──────────────────────────

    if is_short:
        if entry_ticks >= stop_ticks:
            return _fail(
                "INVALID_RISK",
                f"SHORT entry ({entry_ticks} ticks) must be strictly below"
                f" stop ({stop_ticks} ticks); check confirmation_bar"
                f" geometry or buffer configuration",
            )
    else:
        if entry_ticks <= stop_ticks:
            return _fail(
                "INVALID_RISK",
                f"LONG entry ({entry_ticks} ticks) must be strictly above"
                f" stop ({stop_ticks} ticks); check confirmation_bar"
                f" geometry or buffer configuration",
            )

    # ── Step 6: compute risk ─────────────────────────────────────────────

    risk_ticks = abs(entry_ticks - stop_ticks)

    if risk_ticks == 0:
        return _fail(
            "INVALID_RISK",
            "calculated risk is zero ticks; entry and stop are identical",
        )

    # ── Step 7: compute targets ──────────────────────────────────────────

    if is_short:
        r2_ticks = entry_ticks - 2 * risk_ticks
        r3_ticks = entry_ticks - 3 * risk_ticks
        r4_ticks = entry_ticks - 4 * risk_ticks
    else:
        r2_ticks = entry_ticks + 2 * risk_ticks
        r3_ticks = entry_ticks + 3 * risk_ticks
        r4_ticks = entry_ticks + 4 * risk_ticks

    # ── Step 8: assemble TradePlan/v1 ────────────────────────────────────

    ts = _tick_size_to_str(tick_size)

    # Map entry_model string to the canonical EntryModel enum
    em = EntryModel(entry_model_str)

    trade_plan = TradePlan(
        schema_version="TradePlan/v1",
        entry_model=em,
        entry_buffer_ticks=entry_buffer,
        stop_buffer_ticks=stop_buffer,
        tick_size=ts,
        entry_price=PriceTicks(ticks=entry_ticks, tick_size=ts),
        stop_price=PriceTicks(ticks=stop_ticks, tick_size=ts),
        risk=AbsoluteTickDistance(ticks=risk_ticks, tick_size=ts),
        r2_price=PriceTicks(ticks=r2_ticks, tick_size=ts),
        r3_price=PriceTicks(ticks=r3_ticks, tick_size=ts),
        r4_price=PriceTicks(ticks=r4_ticks, tick_size=ts),
    )

    return {"status": "OK", "trade_plan": trade_plan}
