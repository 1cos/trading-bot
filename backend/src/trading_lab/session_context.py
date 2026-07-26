"""Canonical BDRR session context construction — Stage 1a.

Ported from ``buildSessionContext`` in
estrategie/bdrr_engine.js (lines 147–186).

Validates that all supplied candles belong to a single calendar date
in the requested timezone, creates a defensive sorted copy, and returns
the session context used by downstream Stage 1b (buildORB) and later.

This function does NOT:
  - construct the ORB;
  - detect breaks, displacement, retests, or rejection;
  - convert raw candles to canonical Bars;
  - use tick_size or any price information.

Input:
    candles — list of raw candle dicts (from ``parse_candles_from_csv``),
              each with a ``time_ms`` key (int, epoch ms UTC).
    config  — dict with at minimum these required keys:
              timeframe_minutes, timezone, session_open, orb_start,
              orb_duration_minutes, level_source, direction, tick_size.
              Only ``timezone`` and ``session_open`` are read by this
              function; the rest are validated for presence only
              (matching ``assertValidConfig`` in bdrr_engine.js).

Output (OK):
    {
        "status":       "OK",
        "date":         str,       # "YYYY-MM-DD" in the configured timezone
        "timezone":     str,       # from config
        "session_open": str,       # from config (e.g. "09:30")
        "candles":      list,      # defensive copy, sorted ascending by time_ms
        "candle_count": int,
    }

Output (FAILED):
    {
        "status":       "FAILED",
        "failed_stage": str,
        "reason":       str,
    }

Behavior matches JS exactly:
  - Empty candles → FAILED / INVALID_SESSION_INPUT.
  - Multiple ET calendar dates → FAILED / INVALID_SESSION_INPUT.
  - Candles are defensively copied and sorted ascending by time_ms.
  - The original list is never mutated.
  - Original candle dict objects are preserved (identity) in the sorted copy.
  - Missing config keys → TypeError (matching assertValidConfig).
  - candles not a list → TypeError.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# ── Config validation (matches bdrr_engine.js assertValidConfig) ─────────────

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
    """Validate config has all required keys.  Throws TypeError on failure."""
    if not isinstance(config, dict):
        raise TypeError("config must be a dict")
    for key in _REQUIRED_CONFIG_KEYS:
        if key not in config:
            raise TypeError(f"config.{key} is required")


# ── Date formatting (matches bdrr_engine.js getETDateString) ─────────────────

def _get_date_string(time_ms: int, tz: ZoneInfo) -> str:
    """Convert epoch ms to 'YYYY-MM-DD' in the given timezone."""
    dt = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).astimezone(tz)
    return dt.strftime("%Y-%m-%d")


# ── Primary function ─────────────────────────────────────────────────────────


def build_session_context(
    candles: list[dict],
    config: dict,
) -> dict:
    """Validate and prepare a single-session candle collection.

    Matches JS ``buildSessionContext(candles, config)`` in
    bdrr_engine.js:147–186.

    Parameters
    ----------
    candles : list[dict]
        Raw candle dicts, each with ``time_ms`` (int, epoch ms).
    config : dict
        Engine configuration with required keys (see module docstring).

    Returns
    -------
    dict
        ``{"status": "OK", ...}`` or ``{"status": "FAILED", ...}``.
    """
    _assert_valid_config(config)

    if not isinstance(candles, list):
        raise TypeError("candles must be a list")

    if len(candles) == 0:
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_SESSION_INPUT",
            "reason": "no candles provided",
        }

    tz = ZoneInfo(config["timezone"])

    # Defensive copy, sorted ascending by time_ms.
    # Never mutates the input list.  Preserves original dict identity.
    sorted_candles = sorted(candles, key=lambda c: c["time_ms"])

    first_date = _get_date_string(sorted_candles[0]["time_ms"], tz)
    for c in sorted_candles[1:]:
        c_date = _get_date_string(c["time_ms"], tz)
        if c_date != first_date:
            return {
                "status": "FAILED",
                "failed_stage": "INVALID_SESSION_INPUT",
                "reason": (
                    f"candles span multiple ET calendar dates "
                    f"(found {first_date} and {c_date}); "
                    f"buildSessionContext expects candles from exactly "
                    f"one trading session"
                ),
            }

    return {
        "status": "OK",
        "date": first_date,
        "timezone": config["timezone"],
        "session_open": config["session_open"],
        "candles": sorted_candles,
        "candle_count": len(sorted_candles),
    }
