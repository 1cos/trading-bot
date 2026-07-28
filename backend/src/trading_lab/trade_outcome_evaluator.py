"""Canonical TradeOutcome/v1 evaluator for the BDRR pipeline.

Ported from ``evaluateTradeOutcome`` in estrategie/bdrr_trade_outcome.js
(lines 220–489).

Exports one public function:

    evaluate_trade_outcome(detection_result, trade_plan,
                           post_confirmation_bars, config)

This module evaluates a chronological LONG trade outcome given a
canonical DetectionResult/v1, TradePlan/v1, and post-confirmation bars.

It does not run Stage 1–5, build DetectionResult, build TradePlan,
score, apply policy, or simulate.

Run tests: python -m pytest backend/tests/test_trade_outcome_evaluator.py
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from trading_lab.contracts.enums import Direction
from trading_lab.contracts.trade_outcome import TradeOutcome, TradeOutcomeStatus
from trading_lab.contracts.trade_plan import EntryModel


# ── Config dataclass ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TradeOutcomeConfig:
    """Configuration for evaluate_trade_outcome."""

    direction: object
    exit_target_r: object


# ── Failure wrapper ──────────────────────────────────────────────────────────


def _fail(code: str, reason: str) -> dict:
    return {"status": "FAILED", "failure_code": code, "reason": reason}


# ── Attribute access helper ──────────────────────────────────────────────────


def _get(obj: object, attr: str, default: object = None) -> object:
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _has_obj_shape(v: object) -> bool:
    if v is None:
        return False
    if isinstance(v, (str, int, float, bool, list, tuple)):
        return False
    return True


# ── JS-compatible helpers ────────────────────────────────────────────────────


def _is_positive_finite_number(v: object) -> bool:
    """Match JS isPositiveFiniteNumber."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return v > 0
    if isinstance(v, float):
        return math.isfinite(v) and v > 0
    return False


def _is_integer(v: object) -> bool:
    """Match JS Number.isInteger — rejects bool."""
    if isinstance(v, bool):
        return False
    return isinstance(v, int)


def _tick_size_numeric(v: object) -> object:
    """Convert tick_size (possibly str from Python contracts) to numeric."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v)
        except (ValueError, TypeError):
            return v
    return v


def _json_stringify(v: object) -> str:
    """Approximate JS JSON.stringify for reason strings.

    JS JSON.stringify(undefined) → undefined (no quotes)
    JS JSON.stringify(null) → null
    JS JSON.stringify(NaN) → null
    JS JSON.stringify('2') → "2" (with quotes)
    """
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return "null"
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (int, float)):
        # Match JS number formatting
        if isinstance(v, float) and v == int(v) and math.isfinite(v):
            return str(int(v))
        return str(v)
    if isinstance(v, (list, tuple)):
        return json.dumps(v)
    if isinstance(v, dict):
        return json.dumps(v)
    # For Python objects with no direct JSON equivalent, mimic undefined
    return "undefined"


# ── Input validation ─────────────────────────────────────────────────────────


def _validate_detection_result(dr: object) -> dict | None:
    if not _has_obj_shape(dr):
        return _fail(
            "INVALID_DETECTION_RESULT",
            "detectionResult must be a non-null object",
        )
    sv = _get(dr, "schema_version")
    if str(sv) != "DetectionResult/v1":
        return _fail(
            "INVALID_DETECTION_RESULT",
            f'detectionResult.schema_version must be "DetectionResult/v1";'
            f' got "{sv}"',
        )
    status = _get(dr, "status")
    if str(status) != "VALID":
        return _fail(
            "INVALID_DETECTION_RESULT",
            f'detectionResult.status must be "VALID"; got "{status}"',
        )
    return None


def _is_valid_price_ticks(pt: object, tick_size_num: object) -> bool:
    """Check if pt is a valid PriceTicks-like object.

    Matches JS: !pt || typeof pt !== 'object' || !Number.isInteger(pt.ticks) ||
                !isPositiveFiniteNumber(pt.tick_size)
    """
    if not _has_obj_shape(pt):
        return False
    ticks = _get(pt, "ticks")
    if not _is_integer(ticks):
        return False
    ts = _get(pt, "tick_size")
    ts_num = _tick_size_numeric(ts)
    if not _is_positive_finite_number(ts_num):
        return False
    return True


def _validate_trade_plan(tp: object) -> dict | None:
    if not _has_obj_shape(tp):
        return _fail("INVALID_TRADE_PLAN", "tradePlan must be a non-null object")
    sv = _get(tp, "schema_version")
    if str(sv) != "TradePlan/v1":
        return _fail(
            "INVALID_TRADE_PLAN",
            f'tradePlan.schema_version must be "TradePlan/v1"; got "{sv}"',
        )
    tp_tick_size = _get(tp, "tick_size")
    tp_tick_size_num = _tick_size_numeric(tp_tick_size)
    for field in ("entry_price", "stop_price", "risk",
                   "r2_price", "r3_price", "r4_price"):
        pt = _get(tp, field)
        if not _is_valid_price_ticks(pt, tp_tick_size_num):
            return _fail(
                "INVALID_TRADE_PLAN",
                f"tradePlan.{field} must be a valid PriceTicks object",
            )
    if not _is_positive_finite_number(tp_tick_size_num):
        return _fail(
            "INVALID_TRADE_PLAN",
            "tradePlan.tick_size must be a finite positive number",
        )
    risk = _get(tp, "risk")
    risk_ticks = _get(risk, "ticks")
    if risk_ticks <= 0:
        return _fail(
            "INVALID_TRADE_PLAN",
            "tradePlan.risk.ticks must be positive",
        )
    return None


_VALID_EXIT_TARGET_R = frozenset({2, 3, 4})


def _validate_config(config: object) -> dict | None:
    if not _has_obj_shape(config):
        return _fail("INVALID_CONFIG", "config must be a non-null object")
    direction = _get(config, "direction")
    direction_str = str(direction) if direction is not None else str(direction)
    if direction_str not in ("LONG", "SHORT"):
        return _fail(
            "UNSUPPORTED_DIRECTION",
            f'direction "{direction_str}" is not supported;'
            f' only "LONG" and "SHORT" are implemented',
        )
    exit_r = _get(config, "exit_target_r")
    # JS uses Set.has which requires exact value match (no coercion)
    # Must be exactly 2, 3, or 4 (int), not bool, not float, not string
    if not (isinstance(exit_r, int) and not isinstance(exit_r, bool)
            and exit_r in _VALID_EXIT_TARGET_R):
        return _fail(
            "INVALID_CONFIG",
            f"config.exit_target_r must be 2, 3, or 4;"
            f" got {_json_stringify(exit_r)}",
        )
    return None


def _validate_bars(bars: object, tick_size: object) -> dict | None:
    if not isinstance(bars, list):
        return _fail("INVALID_BARS", "postConfirmationBars must be an array")
    tick_size_num = _tick_size_numeric(tick_size)
    for i, b in enumerate(bars):
        if not _has_obj_shape(b):
            return _fail("INVALID_BARS", f"bar[{i}] must be a non-null object")
        for field in ("open", "high", "low", "close"):
            pt = _get(b, field)
            if not _is_valid_price_ticks(pt, tick_size_num):
                return _fail(
                    "INVALID_BARS",
                    f"bar[{i}].{field} must be a valid PriceTicks object",
                )
            pt_ts = _get(pt, "tick_size")
            pt_ts_num = _tick_size_numeric(pt_ts)
            if pt_ts_num != tick_size_num:
                return _fail(
                    "TICK_SIZE_MISMATCH",
                    f"bar[{i}].{field}.tick_size ({pt_ts})"
                    f" does not match tradePlan.tick_size ({tick_size})",
                )
        bar_ms = _get(b, "bar_utc_ms")
        if not (isinstance(bar_ms, (int, float))
                and not isinstance(bar_ms, bool)
                and math.isfinite(bar_ms)):
            return _fail(
                "INVALID_BARS",
                f"bar[{i}].bar_utc_ms must be a finite number",
            )
        if i > 0:
            prev_ms = _get(bars[i - 1], "bar_utc_ms")
            if bar_ms <= prev_ms:
                return _fail(
                    "BARS_NOT_CHRONOLOGICAL",
                    f"bar[{i}].bar_utc_ms ({bar_ms}) must be strictly after"
                    f" bar[{i - 1}].bar_utc_ms ({prev_ms})",
                )
        hi = _get(_get(b, "high"), "ticks")
        lo = _get(_get(b, "low"), "ticks")
        if hi < lo:
            return _fail(
                "INVALID_BARS",
                f"bar[{i}].high.ticks ({hi}) must be >= low.ticks ({lo})",
            )
    return None


# ── tick_size canonical string ───────────────────────────────────────────────


def _tick_size_to_str(ts: object) -> str:
    if isinstance(ts, str):
        return ts
    return str(ts)


# ── Primary export ───────────────────────────────────────────────────────────


def evaluate_trade_outcome(
    detection_result: object,
    trade_plan: object,
    post_confirmation_bars: object,
    config: object,
) -> dict:
    """Evaluate a chronological LONG trade outcome.

    Mirrors JS ``evaluateTradeOutcome`` in bdrr_trade_outcome.js exactly.

    Never raises for ordinary validation failures.
    Never mutates inputs.
    Deterministic for identical inputs.
    """
    # ── Step 1: validate inputs ──────────────────────────────────────────

    dr_err = _validate_detection_result(detection_result)
    if dr_err:
        return dr_err

    tp_err = _validate_trade_plan(trade_plan)
    if tp_err:
        return tp_err

    cfg_err = _validate_config(config)
    if cfg_err:
        return cfg_err

    tick_size = _get(trade_plan, "tick_size")

    bars_err = _validate_bars(post_confirmation_bars, tick_size)
    if bars_err:
        return bars_err

    # ── Step 2: extract trade plan values ────────────────────────────────

    entry_ticks = _get(_get(trade_plan, "entry_price"), "ticks")
    stop_ticks = _get(_get(trade_plan, "stop_price"), "ticks")
    r2_ticks = _get(_get(trade_plan, "r2_price"), "ticks")
    r3_ticks = _get(_get(trade_plan, "r3_price"), "ticks")
    r4_ticks = _get(_get(trade_plan, "r4_price"), "ticks")
    entry_model = _get(trade_plan, "entry_model")
    # Convert enum to string for comparison
    entry_model_str = str(entry_model) if entry_model is not None else None

    all_targets = [
        {"ticks": r2_ticks, "label": "2R", "r": 2},
        {"ticks": r3_ticks, "label": "3R", "r": 3},
        {"ticks": r4_ticks, "label": "4R", "r": 4},
    ]

    selected_r = _get(config, "exit_target_r")
    selected_label = f"{selected_r}R"
    terminal_idx = next(
        i for i, t in enumerate(all_targets) if t["r"] == selected_r
    )
    targets = all_targets[: terminal_idx + 1]

    # ── Step 3: entry timestamp ──────────────────────────────────────────

    is_cc = entry_model_str == "CONFIRMATION_CLOSE"
    direction_str = str(_get(config, "direction"))
    is_short = direction_str == "SHORT"

    entry_triggered = is_cc
    if is_cc:
        conf_bar = _get(detection_result, "confirmation_bar")
        entry_bar_utc_ms = (
            _get(conf_bar, "bar_utc_ms") if conf_bar else None
        )
    else:
        entry_bar_utc_ms = None

    first_eval_bar_index = None
    first_eval_bar_utc_ms = None
    bosb_entry_bar_index = None

    # ── Step 4: scan bars ────────────────────────────────────────────────
    # Direction-aware comparisons:
    # LONG:  target hit = hi >= target,  stop hit = lo <= stop,
    #        BOSB entry = hi >= entry
    # SHORT: target hit = lo <= target,  stop hit = hi >= stop,
    #        BOSB entry = lo <= entry

    highest_target_idx = -1
    outcome_type = None
    exit_bar_index = None
    exit_bar_utc_ms = None
    exit_price_ticks = None
    exit_target_label = None
    exit_target_r = None

    def _target_hit(bar_hi, bar_lo, target_ticks):
        if is_short:
            return bar_lo <= target_ticks
        return bar_hi >= target_ticks

    def _stop_hit(bar_hi, bar_lo, stop_t):
        if is_short:
            return bar_hi >= stop_t
        return bar_lo <= stop_t

    def _entry_trigger(bar_hi, bar_lo, entry_t):
        if is_short:
            return bar_lo <= entry_t
        return bar_hi >= entry_t

    for i, bar in enumerate(post_confirmation_bars):
        hi_ticks = _get(_get(bar, "high"), "ticks")
        lo_ticks = _get(_get(bar, "low"), "ticks")

        if i == 0:
            first_eval_bar_index = 0
            first_eval_bar_utc_ms = _get(bar, "bar_utc_ms")

        # ── BOSB Phase A: wait for entry trigger ────────────────────
        if not entry_triggered:
            if _entry_trigger(hi_ticks, lo_ticks, entry_ticks):
                entry_triggered = True
                entry_bar_utc_ms = _get(bar, "bar_utc_ms")
                bosb_entry_bar_index = i
                first_eval_bar_index = i
                first_eval_bar_utc_ms = _get(bar, "bar_utc_ms")

                stop_hit_flag = _stop_hit(hi_ticks, lo_ticks, stop_ticks)
                term_hit = _target_hit(
                    hi_ticks, lo_ticks, targets[terminal_idx]["ticks"])

                if stop_hit_flag and term_hit:
                    outcome_type = "AMBIGUOUS"
                    exit_bar_index = i
                    exit_bar_utc_ms = _get(bar, "bar_utc_ms")
                    exit_price_ticks = None
                    break
                if stop_hit_flag:
                    outcome_type = "STOPPED"
                    exit_bar_index = i
                    exit_bar_utc_ms = _get(bar, "bar_utc_ms")
                    exit_price_ticks = stop_ticks
                    break
                for t in range(len(targets)):
                    if _target_hit(
                            hi_ticks, lo_ticks, targets[t]["ticks"]):
                        highest_target_idx = t
                    else:
                        break
                if highest_target_idx == terminal_idx:
                    outcome_type = "TARGET_HIT"
                    exit_bar_index = i
                    exit_bar_utc_ms = _get(bar, "bar_utc_ms")
                    exit_target_label = targets[highest_target_idx]["label"]
                    exit_target_r = targets[highest_target_idx]["r"]
                    exit_price_ticks = targets[highest_target_idx]["ticks"]
                    break
            continue

        # ── Phase B: entry active ───────────────────────────────────

        next_idx = highest_target_idx + 1
        stop_hit_flag = _stop_hit(hi_ticks, lo_ticks, stop_ticks)
        terminal_hit = _target_hit(
            hi_ticks, lo_ticks, targets[terminal_idx]["ticks"])

        if stop_hit_flag and terminal_hit:
            outcome_type = "AMBIGUOUS"
            exit_bar_index = i
            exit_bar_utc_ms = _get(bar, "bar_utc_ms")
            exit_price_ticks = None
            break

        if stop_hit_flag:
            outcome_type = "STOPPED"
            exit_bar_index = i
            exit_bar_utc_ms = _get(bar, "bar_utc_ms")
            exit_price_ticks = stop_ticks
            break

        if next_idx < len(targets) and _target_hit(
                hi_ticks, lo_ticks, targets[next_idx]["ticks"]):
            for t in range(next_idx, len(targets)):
                if _target_hit(hi_ticks, lo_ticks, targets[t]["ticks"]):
                    highest_target_idx = t
                else:
                    break
            if highest_target_idx == terminal_idx:
                outcome_type = "TARGET_HIT"
                exit_bar_index = i
                exit_bar_utc_ms = _get(bar, "bar_utc_ms")
                exit_target_label = targets[highest_target_idx]["label"]
                exit_target_r = targets[highest_target_idx]["r"]
                exit_price_ticks = targets[highest_target_idx]["ticks"]
                break

    # ── Step 5: resolve session-end outcome ──────────────────────────────

    if not entry_triggered:
        outcome_type = "ENTRY_NOT_TRIGGERED"
    elif outcome_type is None:
        outcome_type = "OPEN"

    # ── Step 6: realized_r ───────────────────────────────────────────────

    realized_r = None
    if outcome_type == "STOPPED":
        realized_r = -1
    if outcome_type == "TARGET_HIT":
        realized_r = selected_r

    # ── Step 7: assemble TradeOutcome ────────────────────────────────────

    ts_str = _tick_size_to_str(tick_size)

    # Map entry_model to EntryModel enum
    try:
        em = EntryModel(entry_model_str)
    except (ValueError, KeyError):
        # If entry_model_str doesn't match an enum value, use the string
        # directly — but the frozen contract requires EntryModel, so we
        # use the closest match. Per JS behavior, non-CC is BOSB-like.
        # The JS stores the raw string. We must map to enum for contract.
        em = EntryModel.BREAK_OF_SIGNAL_BAR if not is_cc else EntryModel.CONFIRMATION_CLOSE

    outcome = TradeOutcome(
        schema_version="TradeOutcome/v1",
        direction=Direction.SHORT if is_short else Direction.LONG,
        entry_model=em,
        entry_price_ticks=entry_ticks,
        stop_price_ticks=stop_ticks,
        tick_size=ts_str,
        selected_exit_target_r=selected_r,
        selected_exit_target_label=selected_label,
        entry_triggered=entry_triggered,
        entry_bar_utc_ms=entry_bar_utc_ms,
        bosb_entry_bar_index=bosb_entry_bar_index,
        first_eval_bar_index=first_eval_bar_index,
        first_eval_bar_utc_ms=first_eval_bar_utc_ms,
        outcome=TradeOutcomeStatus(outcome_type),
        exit_bar_index=exit_bar_index,
        exit_bar_utc_ms=exit_bar_utc_ms,
        exit_price_ticks=exit_price_ticks,
        exit_target_label=exit_target_label,
        exit_target_r=exit_target_r,
        highest_target_achieved=(
            targets[highest_target_idx]["label"]
            if highest_target_idx >= 0
            else None
        ),
        highest_target_r=(
            targets[highest_target_idx]["r"]
            if highest_target_idx >= 0
            else None
        ),
        realized_r=realized_r,
        r2_price_ticks=r2_ticks,
        r3_price_ticks=r3_ticks,
        r4_price_ticks=r4_ticks,
    )

    return {"status": "OK", "outcome": outcome}
