"""Canonical BDRR break detection — Stage 2.

Ported from ``findBreak`` in estrategie/bdrr_engine.js (lines 306–372).

Scans candles strictly after the ORB candle for the first candle whose
close qualifies as a confirmed break.  Returns the first qualifying
candle only.

LONG rule:  candle["close"] > orb["level_price"]   (strict >)
SHORT rule: not implemented (returns FAILED / UNSUPPORTED_CONFIGURATION).

Key behaviors matching JS exactly:
  - Comparison uses raw float prices, NOT tick values.
  - Equality with the level does NOT qualify (strict >).
  - Only the close field is examined. A wick crossing without a qualifying
    close does not count.
  - The ORB candle itself (at orb_candle_index) is never eligible.
  - Candles before the ORB are never eligible.
  - The first qualifying candle stops the scan immediately.
  - If no candle qualifies → FAILED / BREAK_NOT_FOUND.
  - Failed/missing ORB → FAILED with upstream failed_stage.
  - direction != "LONG" → FAILED / UNSUPPORTED_CONFIGURATION.
  - Defensive cross-check: candles[orb_candle_index] must match orb_candle.

This function does NOT:
  - detect displacement (Stage 3);
  - detect retests, rejection, or confirmation (Stages 4–5);
  - convert candles to canonical Bars;
  - use any configuration beyond direction and tick_size.

Output (OK):
    {
        "status":                       "OK",
        "date":                         str,
        "break_candle_index":           int,   # absolute index in candles
        "break_candle":                 dict,  # the raw candle (identity)
        "break_timestamp":             int,    # candle["time_ms"]
        "directional_break_distance":  {
            "points": float,
            "ticks":  int,
        },
    }

Output (FAILED):
    {
        "status":       "FAILED",
        "failed_stage": str,
        "reason":       str,
    }
"""

from __future__ import annotations

from trading_lab.tick_arithmetic import price_to_ticks, ticks_to_points


# ── Config validation ────────────────────────────────────────────────────────

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


# ── Primary function ─────────────────────────────────────────────────────────


def find_break(
    candles: list[dict],
    orb: dict,
    config: dict,
) -> dict:
    """Scan for the first confirmed break beyond the ORB level.

    Matches JS ``findBreak(candles, orb, config)`` in
    bdrr_engine.js:306–372.

    Parameters
    ----------
    candles : list[dict]
        Same sorted raw candle list from build_session_context / build_orb.
    orb : dict
        Result of build_orb (must have status "OK").
    config : dict
        Engine configuration with the 8 required keys.

    Returns
    -------
    dict
        ``{"status": "OK", ...}`` or ``{"status": "FAILED", ...}``.
    """
    _assert_valid_config(config)

    # ── Failed or missing ORB ────────────────────────────────────────────
    if not isinstance(orb, dict) or orb.get("status") != "OK":
        failed_stage = "LEVEL_NOT_FOUND"
        reason_part = ""
        if isinstance(orb, dict):
            failed_stage = orb.get("failed_stage", "LEVEL_NOT_FOUND")
            reason_part = orb.get("reason", "")
        return {
            "status": "FAILED",
            "failed_stage": failed_stage,
            "reason": (
                "cannot search for a break: upstream ORB result failed "
                f"({reason_part})"
            ),
        }

    # ── Unsupported direction ────────────────────────────────────────────
    direction = config["direction"]
    if direction not in ("LONG", "SHORT"):
        return {
            "status": "FAILED",
            "failed_stage": "UNSUPPORTED_CONFIGURATION",
            "reason": (
                f'direction "{direction}" is not implemented in '
                f'this stage; only "LONG" and "SHORT" are supported'
            ),
        }

    # ── Input validation ─────────────────────────────────────────────────
    if not isinstance(candles, list):
        raise TypeError("candles must be a list")

    # ── Defensive cross-check ────────────────────────────────────────────
    orb_idx = orb["orb_candle_index"]
    if (orb_idx >= len(candles) or
            candles[orb_idx]["time_ms"] != orb["orb_candle"]["time_ms"]):
        return {
            "status": "FAILED",
            "failed_stage": "INVALID_INPUT",
            "reason": (
                "candles does not match the array used to build orb "
                "(orb_candle_index does not point at the same candle)"
            ),
        }

    # ── Scan for break ───────────────────────────────────────────────────
    level_price = orb["level_price"]
    level_ticks = orb["level_price_ticks"]
    tick_size = config["tick_size"]
    is_short = direction == "SHORT"

    for i in range(orb_idx + 1, len(candles)):
        c = candles[i]
        if is_short:
            # SHORT: strict < on raw float close
            if c["close"] < level_price:
                close_ticks = price_to_ticks(c["close"], tick_size)
                distance_ticks = level_ticks - close_ticks
                return {
                    "status": "OK",
                    "date": orb["date"],
                    "break_candle_index": i,
                    "break_candle": c,
                    "break_timestamp": c["time_ms"],
                    "directional_break_distance": {
                        "points": ticks_to_points(distance_ticks, tick_size),
                        "ticks": distance_ticks,
                    },
                }
        else:
            # LONG: strict > on raw float close
            if c["close"] > level_price:
                close_ticks = price_to_ticks(c["close"], tick_size)
                distance_ticks = close_ticks - level_ticks
                return {
                    "status": "OK",
                    "date": orb["date"],
                    "break_candle_index": i,
                    "break_candle": c,
                    "break_timestamp": c["time_ms"],
                    "directional_break_distance": {
                        "points": ticks_to_points(distance_ticks, tick_size),
                        "ticks": distance_ticks,
                    },
                }

    return {
        "status": "FAILED",
        "failed_stage": "BREAK_NOT_FOUND",
        "reason": (
            f"no candle closed beyond level_price ({level_price}) "
            f"after the ORB candle at index {orb_idx}"
        ),
    }
