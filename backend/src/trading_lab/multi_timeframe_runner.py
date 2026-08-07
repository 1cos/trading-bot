"""Multi-Timeframe Runner — orchestrates canonical ORB + post-ORB detection.

This module does NOT contain strategy logic. It is an orchestrator that:
1. Computes the canonical ORB from five 09:30–09:34 one-minute bars.
2. Aggregates post-ORB bars (09:35+) to the target timeframe.
3. Constructs the session with [orb_summary, ...post_orb_candles].
4. Delegates to the frozen run_bdrr_strategy with an _orb_override.

The ORB High and ORB Low are identical across all timeframes because
they are always derived from the same five source bars.

Post-ORB candle alignment:
  1m:  09:35, 09:36, 09:37, ...
  2m:  09:35–09:37, 09:37–09:39, ...
  3m:  09:35–09:38, 09:38–09:41, ...
  5m:  09:35–09:40, 09:40–09:45, ...  (matches standard 5m boundaries)
  10m: 09:35–09:45, 09:45–09:55, ...

Note: 2m, 3m, and 10m bars are anchored at 09:35 and may NOT match
standard chart-provider bars anchored at 09:30. This is intentional —
it ensures no aggregated candle contains ORB-formation data.

Public API:

    run_multi_timeframe(candles_1m_by_date, symbol, target_minutes,
                        direction, preset_overrides, config) → list[dict]
"""

from __future__ import annotations

from trading_lab.strategy_runner import run_bdrr_strategy
from trading_lab.timeframe_aggregation import aggregate_post_orb


def run_multi_timeframe(
    candles_1m_by_date: dict[str, list[dict]],
    symbol: str,
    target_minutes: int,
    direction: str = "LONG",
    preset_overrides: dict | None = None,
    config: dict | None = None,
) -> list[dict]:
    """Run the frozen BDRR detector at a target timeframe using 1m source data.

    Parameters
    ----------
    candles_1m_by_date : dict mapping date strings to lists of 1m candle dicts
    symbol : instrument symbol
    target_minutes : 1, 2, 3, 5, or 10
    direction : "LONG" or "SHORT"
    preset_overrides : optional dict of preset fields to override
    config : optional config dict (tick_size, exit_target_r, engine_version)

    Returns
    -------
    list of strategy runner result dicts (same shape as run_bdrr_strategy)
    """
    if target_minutes not in (1, 2, 3, 5, 10):
        raise ValueError(f"Unsupported timeframe: {target_minutes}m")

    if config is None:
        config = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0"}

    po = preset_overrides or {}
    level_source = po.get("level_source")
    if level_source is None:
        level_source = "ORB_LOW" if direction == "SHORT" else "ORB_HIGH"

    preset = {
        "preset_id": po.get("preset_id", "multi_tf"),
        "timeframe_minutes": target_minutes,
        "timezone": "America/New_York",
        "session_open": "09:30",
        "orb_start": "session_open",
        "orb_duration_minutes": target_minutes,  # matches timeframe so build_orb would accept (but we override)
        "level_source": level_source,
        "direction": direction,
        "entry_model": po.get("entry_model", "CONFIRMATION_CLOSE"),
        "entry_buffer_ticks": po.get("entry_buffer_ticks", 0),
        "stop_buffer_ticks": po.get("stop_buffer_ticks", 0),
        "min_displacement_ticks": po.get("min_displacement_ticks"),
        "min_penetration_ticks": po.get("min_penetration_ticks"),
        "min_close_beyond_level_ticks": po.get("min_close_beyond_level_ticks"),
        "consecutive_orb_closes": po.get("consecutive_orb_closes", 2),
    }

    sessions = []
    for date in sorted(candles_1m_by_date.keys()):
        bars_1m = candles_1m_by_date[date]

        try:
            orb_summary, post_orb = aggregate_post_orb(
                bars_1m, target_minutes, "America/New_York",
            )
        except ValueError:
            # Skip sessions with incomplete ORB data
            continue

        if not post_orb:
            continue

        # Build candle array: [orb_summary at index 0, post_orb candles at 1+]
        candles = [orb_summary] + post_orb

        sessions.append({
            "symbol": symbol,
            "date": date,
            "market_timezone": "America/New_York",
            "session_open_utc_ms": candles[0]["time_ms"],
            "session_close_utc_ms": candles[-1]["time_ms"],
            "timeframe": f"{target_minutes}m",
            "candles": candles,
            "_orb_override": {
                "orb_high": orb_summary["high"],
                "orb_low": orb_summary["low"],
                "orb_candle": orb_summary,
                "orb_candle_index": 0,
            },
        })

    if not sessions:
        return []

    # ── ATR warm-up: inject warmup_candles from previous session ─────
    for i, sess in enumerate(sessions):
        if i == 0:
            continue
        prev = sessions[i - 1]["candles"]
        warmup = prev[-14:]
        prev_close = prev[-(14 + 1)]["close"] if len(prev) > 14 else None
        sess["warmup_candles"] = warmup
        sess["warmup_previous_close"] = prev_close

    return run_bdrr_strategy(sessions, preset, config)
