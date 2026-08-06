"""Canonical BDRR Multi-Session Strategy Runner.

Ported from ``runBdrrStrategy`` in estrategie/bdrr_strategy_runner.js.

Pure orchestrator — imports and invokes the canonical pipeline modules.
Contains ZERO duplicated executable logic from any canonical module.

Pipeline per session:
  1. Run Stage 1–5.
  2. Build DetectionResult/v1.
  3. If VALID: build TradePlan/v1.
  4. If VALID: evaluate TradeOutcome/v1.
  5. Produce one result record dict.

Run tests: python -m pytest backend/tests/test_strategy_runner.py
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from enum import StrEnum, unique

from trading_lab.bar_adapter import raw_candle_to_canonical_bar
from trading_lab.break_finder import find_break
from trading_lab.detection_result_builder import build_detection_result
from trading_lab.displacement_finder import find_displacement
from trading_lab.level_provider import build_level
from trading_lab.tick_arithmetic import price_to_ticks
from trading_lab.rejection_finder import find_rejection
from trading_lab.retest_window import find_retest_window
from trading_lab.sequence_validator import validate_sequence
from trading_lab.session_context import build_session_context
from trading_lab.contracts.primitives import Rational
from trading_lab.timeframe_aggregation import timeframe_to_seconds
from trading_lab.trade_outcome_evaluator import evaluate_trade_outcome
from trading_lab.trade_outcome_evaluator import evaluate_trade_outcome_v2
from trading_lab.trade_plan_builder import build_trade_plan


# ── Outcome enum ─────────────────────────────────────────────────────────────

@unique
class Outcome(StrEnum):
    NO_VALID_SETUP = "NO_VALID_SETUP"
    ENTRY_NOT_TRIGGERED = "ENTRY_NOT_TRIGGERED"
    STOPPED = "STOPPED"
    TARGET_HIT = "TARGET_HIT"
    AMBIGUOUS = "AMBIGUOUS"
    OPEN = "OPEN"
    SESSION_CLOSE = "SESSION_CLOSE"
    PIPELINE_FAILURE = "PIPELINE_FAILURE"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _uuidv4() -> str:
    return str(uuid.uuid4())


def _ms_to_iso(ms: object) -> str | None:
    """Convert epoch ms to ISO 8601 UTC, matching JS new Date(ms).toISOString().

    JS truthiness: 0 and null are falsy → returns null.
    """
    if not ms:
        return None
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    except (OSError, ValueError, TypeError):
        return None


def _get(obj, attr, default=None):
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _get_ticks(tp, field):
    """Extract .ticks from a TradePlan price field (contract or dict)."""
    v = _get(tp, field)
    if hasattr(v, "ticks"):
        return v.ticks
    if isinstance(v, dict):
        return v.get("ticks")
    return None


# ── Result record builders ───────────────────────────────────────────────────

def _build_failure_record(
    run_record_id, session_meta, preset, config,
    outcome, detection_result, trade_plan, trade_outcome,
    failure_stage, reason,
):
    """Build a result record for non-VALID / pipeline-failure paths."""
    if detection_result is not None:
        dr_status = str(_get(detection_result, "status", "INVALID"))
        dr_rules = _get(detection_result, "failed_rules")
        if dr_rules is None:
            dr_rules = []
        dr_id = _get(detection_result, "result_id")
    else:
        dr_status = "INVALID"
        dr_rules = []
        dr_id = None

    return {
        "run_record_id": run_record_id,
        "symbol": session_meta["symbol"],
        "session_date": session_meta["date"],
        "preset_id": _get(preset, "preset_id", "default") or "default",
        "exit_target_r": _get(config, "exit_target_r"),
        "detection_status": dr_status,
        "failure_stage": failure_stage or None,
        "failed_rules": dr_rules,
        "detection_result_id": dr_id,
        "candidate_id": None,
        "confirmation_timestamp": None,
        "entry_timestamp": None,
        "first_evaluation_timestamp": None,
        "entry_price_ticks": None,
        "stop_price_ticks": None,
        "r2_price_ticks": None,
        "r3_price_ticks": None,
        "r4_price_ticks": None,
        "outcome": outcome,
        "realized_r": None,
        "highest_target_achieved": None,
        "exit_timestamp": None,
        "exit_price_ticks": None,
        "detection_result": detection_result,
        "trade_plan": trade_plan,
        "trade_outcome": trade_outcome,
    }


# ── Single-session pipeline ──────────────────────────────────────────────────

def _build_orb_from_override(override, session_context, engine_config):
    """Construct an ORB result dict from an externally precomputed override.

    Used by multi-timeframe runs where the canonical ORB is computed from
    the five 09:30–09:34 one-minute bars and injected into the pipeline.

    The override must contain at minimum:
        orb_high (float), orb_low (float), orb_candle (dict with time_ms),
        orb_candle_index (int).

    Returns the same dict shape as build_orb() so all downstream stages
    receive an identical contract.
    """
    required = ("orb_high", "orb_low", "orb_candle", "orb_candle_index")
    for key in required:
        if key not in override:
            return {
                "status": "FAILED",
                "failed_stage": "INVALID_ORB_OVERRIDE",
                "reason": f"orb_override missing required field: {key}",
            }

    orb_candle = override["orb_candle"]
    if not isinstance(orb_candle, dict) or "time_ms" not in orb_candle:
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_ORB_OVERRIDE",
            "reason": "orb_override.orb_candle must be a dict with time_ms",
        }

    orb_high = override["orb_high"]
    orb_low = override["orb_low"]
    level_source = engine_config.get("level_source", "ORB_HIGH")
    level_price = orb_low if level_source == "ORB_LOW" else orb_high
    tick_size = engine_config["tick_size"]

    return {
        "status": "OK",
        "date": session_context.get("date", ""),
        "orb_candle_index": override["orb_candle_index"],
        "orb_candle": orb_candle,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_low_active": level_source == "ORB_LOW",
        "level_source": level_source,
        "level_price": level_price,
        "level_price_ticks": price_to_ticks(level_price, tick_size),
        "direction": engine_config.get("direction", "LONG"),
    }


def _process_one_session(session, preset, engine_config, tp_config, outcome_config, config, id_factory=None, *, all_sessions=None, _evaluator=None):
    run_record_id = _uuidv4()
    tick_size = config["tick_size"]

    tf = _get(session, "timeframe")
    tf_seconds = timeframe_to_seconds(tf)

    session_meta = {
        "symbol": _get(session, "symbol"),
        "date": _get(session, "date"),
        "market_timezone": _get(session, "market_timezone"),
        "session_open_utc_ms": _get(session, "session_open_utc_ms"),
        "session_close_utc_ms": _get(session, "session_close_utc_ms"),
        "timeframe_seconds": tf_seconds,
    }

    dr_metadata = {
        "tick_size": tick_size,
        "session": session_meta,
        "preset_id": _get(preset, "preset_id", "default") or "default",
        "engine_version": config["engine_version"],
    }

    candles = _get(session, "candles")
    if not isinstance(candles, list) or len(candles) == 0:
        return _build_failure_record(
            run_record_id, session_meta, preset, config,
            Outcome.PIPELINE_FAILURE, None, None, None,
            "INVALID_SESSION_INPUT", "session contains no candles",
        )

    # Stage 1a
    try:
        sc = build_session_context(candles, engine_config)
    except Exception as e:
        return _build_failure_record(
            run_record_id, session_meta, preset, config,
            Outcome.PIPELINE_FAILURE, None, None, None,
            "INVALID_SESSION_INPUT", f"buildSessionContext threw: {e}",
        )
    if sc.get("status") != "OK":
        return _build_failure_record(
            run_record_id, session_meta, preset, config,
            Outcome.PIPELINE_FAILURE, None, None, None,
            sc.get("failed_stage"), sc.get("reason"),
        )

    sc_candles = sc["candles"]

    # Stage 1b — Level construction (generic dispatcher)
    # If an orb_override is provided (multi-timeframe runs with canonical
    # 1-minute ORB), use it instead of the dispatcher.
    orb_override = _get(session, "_orb_override")
    if orb_override is not None:
        level_result = _build_orb_from_override(orb_override, sc, engine_config)
    else:
        level_result = build_level(sc_candles, sc, engine_config, all_sessions=all_sessions)

    # Stage 2 — Break detection (generic: reads only level_price)
    if level_result.get("status") == "OK":
        brk = find_break(sc_candles, level_result, engine_config)
    else:
        brk = {"status": "FAILED", "failed_stage": level_result.get("failed_stage"), "reason": level_result.get("reason")}

    # Stage 3 — Displacement (generic: reads only level_price)
    if brk.get("status") == "OK":
        disp = find_displacement(sc_candles, level_result, brk, engine_config)
    else:
        disp = {"status": "FAILED", "failed_stage": brk.get("failed_stage"), "reason": brk.get("reason")}

    # Stage 3b — Sequence validation (ORB-specific: skipped for non-ORB)
    seq_val = None
    if disp.get("status") == "OK":
        seq_val = validate_sequence(sc_candles, level_result, brk, disp, engine_config)
        if seq_val.get("status") == "INVALIDATED":
            max_vi = seq_val["max_valid_index"]
            first_retest = disp["first_retest_contact_index"]
            if max_vi < first_retest:
                # Invalidation occurs before any retest is possible
                disp = {
                    "status": "FAILED",
                    "failed_stage": "SEQUENCE_INVALIDATED",
                    "reason": seq_val["invalidation_reason"],
                }
            else:
                # Cap the retest window via config passthrough
                engine_config = {**engine_config, "_max_valid_index": max_vi}

    # Stage 4 — Retest window (generic: reads only level_price)
    if disp.get("status") == "OK":
        retest = find_retest_window(sc_candles, level_result, brk, disp, engine_config)
    else:
        retest = {"status": "FAILED", "failed_stage": disp.get("failed_stage"), "reason": disp.get("reason")}

    # Stage 5 — Rejection / Entry Candle (generic: reads only level_price)
    if retest.get("status") == "OK":
        rej = find_rejection(sc_candles, level_result, brk, disp, retest, engine_config)
    else:
        rej = {"status": "FAILED", "failed_stage": retest.get("failed_stage"), "reason": retest.get("reason")}

    # DetectionResult/v1
    # Note: the key "orb" is a legacy name in the detection_result_builder
    # contract. It accepts any LevelResult dict.
    dr_build = build_detection_result(
        {"orb": level_result, "break_result": brk, "disp_result": disp,
         "retest_result": retest, "rej_result": rej},
        dr_metadata,
        id_factory=id_factory,
    )
    if dr_build.get("status") != "OK":
        return _build_failure_record(
            run_record_id, session_meta, preset, config,
            Outcome.PIPELINE_FAILURE, None, None, None,
            dr_build.get("failure_code"), dr_build.get("reason"),
        )

    detection_result = dr_build["detection_result"]

    # INVALID → NO_VALID_SETUP
    if str(_get(detection_result, "status")) != "VALID":
        return _build_failure_record(
            run_record_id, session_meta, preset, config,
            Outcome.NO_VALID_SETUP, detection_result, None, None,
            _get(detection_result, "failed_stage"), None,
        )

    # TradePlan/v1
    tp_build = build_trade_plan(detection_result, tp_config)
    if tp_build.get("status") != "OK":
        return _build_failure_record(
            run_record_id, session_meta, preset, config,
            Outcome.PIPELINE_FAILURE, detection_result, None, None,
            tp_build.get("failure_code"), tp_build.get("reason"),
        )

    trade_plan = tp_build["trade_plan"]

    # Post-confirmation bars
    conf_idx = rej["confirmation_candle_index"]
    post_conf_bars = []
    for i in range(conf_idx + 1, len(sc_candles)):
        post_conf_bars.append(raw_candle_to_canonical_bar(sc_candles[i], tick_size))

    # TradeOutcome — dispatch to v1 or v2 evaluator
    _eval_fn = _evaluator if _evaluator is not None else evaluate_trade_outcome
    to_build = _eval_fn(
        detection_result, trade_plan, post_conf_bars, outcome_config,
    )
    if to_build.get("status") != "OK":
        return _build_failure_record(
            run_record_id, session_meta, preset, config,
            Outcome.PIPELINE_FAILURE, detection_result, trade_plan, None,
            to_build.get("failure_code"), to_build.get("reason"),
        )

    trade_outcome = to_build["outcome"]

    # Map outcome
    outcome_map = {
        "STOPPED": Outcome.STOPPED,
        "TARGET_HIT": Outcome.TARGET_HIT,
        "AMBIGUOUS": Outcome.AMBIGUOUS,
        "OPEN": Outcome.OPEN,
        "SESSION_CLOSE": Outcome.SESSION_CLOSE,
        "ENTRY_NOT_TRIGGERED": Outcome.ENTRY_NOT_TRIGGERED,
    }
    runner_outcome = outcome_map.get(
        str(_get(trade_outcome, "outcome")), Outcome.PIPELINE_FAILURE
    )

    # Assemble full result record
    if id_factory is not None:
        candidate_id = id_factory("SetupCandidate/v1", {
            "result_id": _get(detection_result, "result_id"),
            "preset_id": _get(preset, "preset_id", "default") or "default",
            "entry_model": str(_get(trade_plan, "entry_model")),
            "entry_price_ticks": _get_ticks(trade_plan, "entry_price"),
            "entry_price_tick_size": _get(trade_plan, "tick_size"),
            "stop_price_ticks": _get_ticks(trade_plan, "stop_price"),
            "stop_price_tick_size": _get(trade_plan, "tick_size"),
            "exit_target_r": config["exit_target_r"],
        })
    else:
        candidate_id = _uuidv4()
    entry_ms = _get(trade_outcome, "entry_bar_utc_ms")
    eval_ms = _get(trade_outcome, "first_eval_bar_utc_ms")
    exit_ms = _get(trade_outcome, "exit_bar_utc_ms")

    return {
        "run_record_id": run_record_id,
        "symbol": session_meta["symbol"],
        "session_date": session_meta["date"],
        "preset_id": _get(preset, "preset_id", "default") or "default",
        "exit_target_r": config["exit_target_r"],
        "detection_status": "VALID",
        "failure_stage": None,
        "failed_rules": [],
        "detection_result_id": _get(detection_result, "result_id"),
        "candidate_id": candidate_id,
        "confirmation_timestamp": _ms_to_iso(entry_ms),
        "entry_timestamp": _ms_to_iso(entry_ms),
        "first_evaluation_timestamp": _ms_to_iso(eval_ms),
        "entry_price_ticks": _get_ticks(trade_plan, "entry_price"),
        "stop_price_ticks": _get_ticks(trade_plan, "stop_price"),
        "r2_price_ticks": _get_ticks(trade_plan, "r2_price"),
        "r3_price_ticks": _get_ticks(trade_plan, "r3_price"),
        "r4_price_ticks": _get_ticks(trade_plan, "r4_price"),
        "outcome": runner_outcome,
        "realized_r": _get(trade_outcome, "realized_r"),
        "highest_target_achieved": _get(trade_outcome, "highest_target_achieved"),
        "exit_timestamp": _ms_to_iso(exit_ms),
        "exit_price_ticks": _get(trade_outcome, "exit_price_ticks"),
        "detection_result": detection_result,
        "trade_plan": trade_plan,
        "trade_outcome": trade_outcome,
    }


# ── Primary export ───────────────────────────────────────────────────────────

def run_bdrr_strategy(sessions, preset, config, *, id_factory=None):
    """Run the BDRR strategy across multiple sessions.

    Mirrors JS ``runBdrrStrategy(sessions, preset, config)``.

    Raises TypeError for invalid top-level arguments (matches JS).
    """
    if not isinstance(sessions, list):
        raise TypeError("sessions must be an array")
    if not isinstance(preset, dict):
        raise TypeError("preset must be a non-null object")
    if not isinstance(config, dict):
        raise TypeError("config must be a non-null object")
    if config.get("exit_target_r") not in (2, 3, 4):
        raise TypeError("config.exit_target_r must be 2, 3, or 4")
    ts = config.get("tick_size")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        raise TypeError("config.tick_size must be a positive finite number")
    if not math.isfinite(ts) or ts <= 0:
        raise TypeError("config.tick_size must be a positive finite number")
    ev = config.get("engine_version")
    if not isinstance(ev, str) or len(ev) == 0:
        raise TypeError("config.engine_version must be a non-empty string")

    tick_size = config["tick_size"]

    engine_config = {
        "timeframe_minutes": preset.get("timeframe_minutes") or 5,
        "timezone": preset.get("timezone") or "America/New_York",
        "session_open": preset.get("session_open") or "09:30",
        "orb_start": preset.get("orb_start") or "session_open",
        "orb_duration_minutes": preset.get("orb_duration_minutes") or 5,
        "level_source": preset.get("level_source") or "ORB_HIGH",
        "direction": preset.get("direction") or "LONG",
        "tick_size": tick_size,
        "min_displacement_ticks": preset.get("min_displacement_ticks"),
        "min_penetration_ticks": preset.get("min_penetration_ticks"),
        "min_close_beyond_level_ticks": preset.get("min_close_beyond_level_ticks"),
        "min_displacement_bars": preset.get("min_displacement_bars"),
        "consecutive_orb_closes": preset.get("consecutive_orb_closes", 2),
        "rejection_wick_ratio_min": preset.get("rejection_wick_ratio_min"),
        "body_ratio_max": preset.get("body_ratio_max"),
        "confirmation_wick_penetration_pct_min": preset.get("confirmation_wick_penetration_pct_min"),
    }

    tp_config = {
        "direction": preset.get("direction") or "LONG",
        "entry_model": preset.get("entry_model") or "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": preset["entry_buffer_ticks"] if preset.get("entry_buffer_ticks") is not None else 0,
        "stop_buffer_ticks": preset["stop_buffer_ticks"] if preset.get("stop_buffer_ticks") is not None else 0,
        "tick_size": tick_size,
    }

    outcome_config = {
        "direction": preset.get("direction") or "LONG",
        "exit_target_r": config["exit_target_r"],
    }

    results = []
    for session in sessions:
        record = _process_one_session(
            session, preset, engine_config, tp_config, outcome_config, config,
            id_factory=id_factory,
            all_sessions=sessions,
        )
        results.append(record)

    return results


# ── v2 export: Rational R/R ──────────────────────────────────────────────────


def run_bdrr_strategy_v2(sessions, preset, config, *, id_factory=None):
    """Run the BDRR strategy with configurable Rational R/R target.

    Produces TradeOutcome/v2.  All detection and trade plan logic
    is identical to v1; only the outcome evaluation uses the v2 path.

    config.exit_target_r must be a Rational (strictly > 0).
    """
    if not isinstance(sessions, list):
        raise TypeError("sessions must be an array")
    if not isinstance(preset, dict):
        raise TypeError("preset must be a non-null object")
    if not isinstance(config, dict):
        raise TypeError("config must be a non-null object")

    exit_r = config.get("exit_target_r")
    if not isinstance(exit_r, Rational):
        raise TypeError(
            f"config.exit_target_r must be a Rational;"
            f" got {type(exit_r).__name__}"
        )
    if exit_r.numerator <= 0:
        raise TypeError(
            f"config.exit_target_r must be strictly positive;"
            f" got {exit_r.numerator}/{exit_r.denominator}"
        )

    ts = config.get("tick_size")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        raise TypeError("config.tick_size must be a positive finite number")
    if not math.isfinite(ts) or ts <= 0:
        raise TypeError("config.tick_size must be a positive finite number")
    ev = config.get("engine_version")
    if not isinstance(ev, str) or len(ev) == 0:
        raise TypeError("config.engine_version must be a non-empty string")

    tick_size = config["tick_size"]

    engine_config = {
        "timeframe_minutes": preset.get("timeframe_minutes") or 5,
        "timezone": preset.get("timezone") or "America/New_York",
        "session_open": preset.get("session_open") or "09:30",
        "orb_start": preset.get("orb_start") or "session_open",
        "orb_duration_minutes": preset.get("orb_duration_minutes") or 5,
        "level_source": preset.get("level_source") or "ORB_HIGH",
        "direction": preset.get("direction") or "LONG",
        "tick_size": tick_size,
        "min_displacement_ticks": preset.get("min_displacement_ticks"),
        "min_penetration_ticks": preset.get("min_penetration_ticks"),
        "min_close_beyond_level_ticks": preset.get("min_close_beyond_level_ticks"),
        "min_displacement_bars": preset.get("min_displacement_bars"),
        "consecutive_orb_closes": preset.get("consecutive_orb_closes", 2),
        "rejection_wick_ratio_min": preset.get("rejection_wick_ratio_min"),
        "body_ratio_max": preset.get("body_ratio_max"),
        "confirmation_wick_penetration_pct_min": preset.get("confirmation_wick_penetration_pct_min"),
    }

    tp_config = {
        "direction": preset.get("direction") or "LONG",
        "entry_model": preset.get("entry_model") or "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": preset["entry_buffer_ticks"] if preset.get("entry_buffer_ticks") is not None else 0,
        "stop_buffer_ticks": preset["stop_buffer_ticks"] if preset.get("stop_buffer_ticks") is not None else 0,
        "tick_size": tick_size,
    }

    outcome_config = {
        "direction": preset.get("direction") or "LONG",
        "exit_target_r": config["exit_target_r"],
    }

    results = []
    for session in sessions:
        record = _process_one_session(
            session, preset, engine_config, tp_config, outcome_config, config,
            id_factory=id_factory,
            all_sessions=sessions,
            _evaluator=evaluate_trade_outcome_v2,
        )
        results.append(record)

    return results
