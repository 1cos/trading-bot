"""Canonical BDRR ORB construction — Stage 1b.

Ported from ``buildORB`` in estrategie/bdrr_engine.js (lines 207–290).

Locates the single ORB candle at config["session_open"] within the
session context's sorted candles, reads its high/low, and selects the
directional level.

This function does NOT:
  - detect breaks (Stage 2);
  - detect displacement, retests, or rejection (Stages 3–5);
  - convert candles to canonical Bars;
  - filter RTH, premarket, or after-hours.

Input:
    candles          — list of raw candle dicts (same list as
                       session_context["candles"]).
    session_context  — dict returned by build_session_context (status OK).
    config           — dict with required keys (same as build_session_context).

Output (OK):
    {
        "status":            "OK",
        "date":              str,
        "orb_candle_index":  int,
        "orb_candle":        dict,        # the raw candle dict (identity)
        "orb_high":          float,
        "orb_low":           float,
        "orb_low_active":    False,
        "level_source":      "ORB_HIGH",
        "level_price":       float,
        "level_price_ticks": int,
        "direction":         str,
    }

Output (FAILED):
    {
        "status":       "FAILED",
        "failed_stage": str,
        "reason":       str,
    }

Behavior matches JS exactly:
  - config validated via assertValidConfig (8 required keys).
  - FAILED session_context → FAILED / LEVEL_NOT_FOUND.
  - orb_start != "session_open" → FAILED / UNSUPPORTED_CONFIGURATION.
  - orb_duration_minutes != timeframe_minutes → FAILED / UNSUPPORTED_CONFIGURATION.
  - level_source != "ORB_HIGH" → FAILED / UNSUPPORTED_CONFIGURATION.
  - Defensive cross-check: candles vs session_context["candles"].
  - ORB candle: first in session_context["candles"] whose local time
    (HH:MM in config["timezone"]) == config["session_open"].
  - Missing ORB candle → FAILED / LEVEL_NOT_FOUND.
  - level_price = orb_candle["high"] (because level_source is ORB_HIGH).
  - level_price_ticks via price_to_ticks.
  - direction from config["direction"].
  - orb_low_active = False always.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.tick_arithmetic import price_to_ticks


# ── Config validation (reused from session_context) ──────────────────────────

_REQUIRED_CONFIG_KEYS = (
    "timeframe_minutes",
    "timezone",
    "session_open",
    "orb_start",
    "orb_duration_minutes",
    "level_source",
    "direction",
    "tick_size",
)


def _assert_valid_config(config: object) -> None:
    if not isinstance(config, dict):
        raise TypeError("config must be a dict")
    for key in _REQUIRED_CONFIG_KEYS:
        if key not in config:
            raise TypeError(f"config.{key} is required")


# ── Time formatting (matches bdrr_engine.js getETTimeString) ─────────────────

def _get_time_string(time_ms: int, tz: ZoneInfo) -> str:
    """Convert epoch ms to 'HH:MM' in the given timezone."""
    dt = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).astimezone(tz)
    return dt.strftime("%H:%M")


# ── Primary function ─────────────────────────────────────────────────────────

def build_orb(
    candles: list[dict],
    session_context: dict,
    config: dict,
) -> dict:
    """Locate the ORB candle and derive the directional level.

    Matches JS ``buildORB(candles, sessionContext, config)`` in
    bdrr_engine.js:207–290.

    Parameters
    ----------
    candles : list[dict]
        Same raw candle list passed to / returned by build_session_context.
    session_context : dict
        Result of build_session_context (must have status "OK").
    config : dict
        Engine configuration with the 8 required keys.

    Returns
    -------
    dict
        ``{"status": "OK", ...}`` or ``{"status": "FAILED", ...}``.
    """
    _assert_valid_config(config)

    # ── Failed or missing session context ────────────────────────────────
    if not isinstance(session_context, dict) or session_context.get("status") != "OK":
        reason_part = ""
        if isinstance(session_context, dict):
            reason_part = session_context.get("reason", "")
        return {
            "status": "FAILED",
            "failed_stage": "LEVEL_NOT_FOUND",
            "reason": (
                "cannot build ORB: sessionContext is missing or failed "
                f"({reason_part})"
            ),
        }

    # ── Unsupported configuration ────────────────────────────────────────
    if config["orb_start"] != "session_open":
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                f'orb_start "{config["orb_start"]}" is not implemented; '
                f'only "session_open" is supported'
            ),
        }

    if config["orb_duration_minutes"] != config["timeframe_minutes"]:
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                "multi-candle ORB windows are not implemented in this stage; "
                f'orb_duration_minutes ({config["orb_duration_minutes"]}) must equal '
                f'timeframe_minutes ({config["timeframe_minutes"]})'
            ),
        }

    if config["level_source"] != "ORB_HIGH":
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                f'level_source "{config["level_source"]}" is not implemented '
                f'in this stage; only "ORB_HIGH" is supported'
            ),
        }

    # ── Defensive cross-check ────────────────────────────────────────────
    source = session_context["candles"]

    if (isinstance(candles, list) and len(candles) > 0 and
            (len(candles) != len(source) or
             candles[0]["time_ms"] != source[0]["time_ms"])):
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_INPUT",
            "reason": (
                "candles does not match sessionContext.candles; buildORB requires "
                "the same array (or an equivalent copy) used to build sessionContext"
            ),
        }

    # ── Locate ORB candle ────────────────────────────────────────────────
    tz = ZoneInfo(config["timezone"])
    session_open = config["session_open"]

    orb_index = -1
    for i, c in enumerate(source):
        if _get_time_string(c["time_ms"], tz) == session_open:
            orb_index = i
            break

    if orb_index == -1:
        return {
            "status": "FAILED",
            "failed_stage": "LEVEL_NOT_FOUND",
            "reason": (
                f"ORB candle not found at {session_open} for session "
                f"{session_context['date']}"
            ),
        }

    orb_candle = source[orb_index]
    orb_high = orb_candle["high"]
    orb_low = orb_candle["low"]
    level_price = orb_high  # level_source === 'ORB_HIGH'

    return {
        "status": "OK",
        "date": session_context["date"],
        "orb_candle_index": orb_index,
        "orb_candle": orb_candle,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_low_active": False,
        "level_source": "ORB_HIGH",
        "level_price": level_price,
        "level_price_ticks": price_to_ticks(level_price, config["tick_size"]),
        "direction": config["direction"],
    }
