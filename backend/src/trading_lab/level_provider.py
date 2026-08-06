"""Generic Level Provider — dispatcher and contract definition.

Phase 2 of MAXBOT_SPECIFICATION.md: make the pipeline accept a
generic level instead of assuming ORB.

This module:
  1. Defines the universal LevelResult contract.
  2. Dispatches level construction to the correct provider based on
     config["level_source"].
  3. In this phase, only ORB_HIGH and ORB_LOW are implemented.
     All other level sources fail explicitly.

The LevelResult contract
========================

Every provider must return a dict with these fields on success:

    CANONICAL FIELDS (required for all providers):
        status             str    "OK"
        date               str    Session date "YYYY-MM-DD"
        level_source       str    Provider label from the registry
        direction          str    "LONG" or "SHORT"
        level_price        float  The tradeable price of the level
        level_price_ticks  int    Same price expressed in ticks
        scan_from_index    int    Last candle of the level's formation
                                  window; downstream stages scan from
                                  scan_from_index + 1 onward
        scan_from_bar      dict   The candle at scan_from_index (used
                                  by downstream cross-checks)

    PROVIDER-SPECIFIC METADATA (optional, provider-dependent):
        provider_data      dict   Opaque bag of provider-specific fields.
                                  For ORB: orb_high, orb_low, orb_high_ticks,
                                  orb_low_ticks, orb_low_active.
                                  Downstream stages MUST NOT read from
                                  provider_data directly; only the sequence
                                  validator and visualization layers may
                                  inspect it when they know the provider type.

    DEPRECATED LEGACY FIELDS (ORB only, kept for backward compat):
        orb_candle_index   int    Alias of scan_from_index
        orb_candle         dict   Alias of scan_from_bar
        orb_high           float  ORB zone high boundary
        orb_low            float  ORB zone low boundary
        orb_high_ticks     int    ORB zone high in ticks
        orb_low_ticks      int    ORB zone low in ticks
        orb_low_active     bool   True when level_source == "ORB_LOW"

        These fields exist ONLY when the provider is ORB_HIGH or ORB_LOW.
        New code SHOULD read from provider_data instead.
        These aliases will be removed in a future phase.

On failure, every provider returns:
    status        str    "FAILED"
    failed_stage  str    Failure classification
    reason        str    Human-readable explanation
"""

from __future__ import annotations

# ── Supported and known level sources ────────────────────────────────────────

IMPLEMENTED_SOURCES = frozenset({
    "ORB_HIGH", "ORB_LOW",
    "PREVIOUS_DAY_HIGH", "PREVIOUS_DAY_LOW",
})

KNOWN_FUTURE_SOURCES = frozenset({
    "PMH", "PML", "PIVOT_WICK", "OCL",
})

ALL_KNOWN_SOURCES = IMPLEMENTED_SOURCES | KNOWN_FUTURE_SOURCES

# ── Canonical field names ────────────────────────────────────────────────────

LEVEL_RESULT_REQUIRED_FIELDS = (
    "status",
    "date",
    "level_source",
    "direction",
    "level_price",
    "level_price_ticks",
    "scan_from_index",
    "scan_from_bar",
)


def _is_orb_source(level_source: str) -> bool:
    """Return True if level_source is an ORB provider."""
    return level_source in ("ORB_HIGH", "ORB_LOW")


def _is_pdh_pdl_source(level_source: str) -> bool:
    """Return True if level_source is a Previous Day provider."""
    return level_source in ("PREVIOUS_DAY_HIGH", "PREVIOUS_DAY_LOW")


# ── Dispatcher ───────────────────────────────────────────────────────────────


def build_level(
    candles: list[dict],
    session_context: dict,
    config: dict,
    *,
    all_sessions: list[dict] | None = None,
) -> dict:
    """Dispatch level construction to the appropriate provider.

    Parameters
    ----------
    candles : list[dict]
        Sorted raw candle list (same as used by build_session_context).
    session_context : dict
        Result of build_session_context (must have status "OK").
    config : dict
        Engine configuration. Must contain "level_source".
    all_sessions : list[dict] | None
        All sessions in the dataset, sorted by date ascending.
        Required for providers that need cross-session data
        (PREVIOUS_DAY_HIGH, PREVIOUS_DAY_LOW). Optional for ORB.

    Returns
    -------
    dict
        A LevelResult dict on success, or a failure dict.
        See module docstring for the full contract.
    """
    level_source = config.get("level_source", "ORB_HIGH")

    if _is_orb_source(level_source):
        return _build_orb_level(candles, session_context, config)

    if _is_pdh_pdl_source(level_source):
        return _build_pdh_pdl_level(
            candles, session_context, config, all_sessions,
        )

    if level_source in KNOWN_FUTURE_SOURCES:
        return {
            "status": "FAILED",
            "failed_stage": "PROVIDER_NOT_IMPLEMENTED",
            "reason": (
                f'Level source "{level_source}" is defined in '
                f"MAXBOT_SPECIFICATION.md but the provider is not yet "
                f"implemented. No fallback will be attempted."
            ),
        }

    return {
        "status": "FAILED",
        "failed_stage": "UNKNOWN_LEVEL_SOURCE",
        "reason": (
            f'Level source "{level_source}" is not recognized. '
            f"Known sources: {sorted(ALL_KNOWN_SOURCES)}."
        ),
    }


def _build_orb_level(
    candles: list[dict],
    session_context: dict,
    config: dict,
) -> dict:
    """Build a level from the ORB provider and add generic contract fields.

    Delegates to the existing orb_builder.build_orb(), then enriches
    the result with the canonical LevelResult fields (scan_from_index,
    scan_from_bar, provider_data).

    The original ORB-specific fields (orb_candle_index, orb_candle,
    orb_high, orb_low, etc.) are preserved as deprecated legacy aliases
    for backward compatibility with existing consumers.
    """
    from trading_lab.orb_builder import build_orb

    orb_result = build_orb(candles, session_context, config)

    if orb_result.get("status") != "OK":
        return orb_result

    # Add canonical contract fields
    orb_result["scan_from_index"] = orb_result["orb_candle_index"]
    orb_result["scan_from_bar"] = orb_result["orb_candle"]
    orb_result["provider_data"] = {
        "orb_high": orb_result["orb_high"],
        "orb_low": orb_result["orb_low"],
        "orb_high_ticks": orb_result["orb_high_ticks"],
        "orb_low_ticks": orb_result["orb_low_ticks"],
        "orb_low_active": orb_result["orb_low_active"],
    }

    return orb_result


def _build_pdh_pdl_level(
    candles: list[dict],
    session_context: dict,
    config: dict,
    all_sessions: list[dict] | None,
) -> dict:
    """Build a level from the Previous Day High/Low provider.

    Uses the ORB window end as scan_from_index (no trades during ORB
    construction, per MAXBOT_SPECIFICATION.md §3.2). The level price
    comes from compute_pdh_pdl.
    """
    from trading_lab.orb_builder import build_orb
    from trading_lab.pdh_pdl_provider import compute_pdh_pdl
    from trading_lab.tick_arithmetic import price_to_ticks

    level_source = config["level_source"]
    direction = config.get("direction", "LONG")
    tick_size = config["tick_size"]

    if all_sessions is None:
        return {
            "status": "FAILED",
            "failed_stage": "MISSING_SESSIONS_DATA",
            "reason": (
                f"{level_source} requires all_sessions to compute the "
                f"previous day's high/low, but all_sessions was not provided."
            ),
        }

    current_date = session_context.get("date", "")

    pdh_pdl = compute_pdh_pdl(current_date, all_sessions)
    if pdh_pdl["status"] != "OK":
        return {
            "status": "FAILED",
            "failed_stage": "NO_PREVIOUS_SESSION",
            "reason": pdh_pdl["reason"],
        }

    if level_source == "PREVIOUS_DAY_HIGH":
        level_price = pdh_pdl["pdh"]
    else:
        level_price = pdh_pdl["pdl"]

    level_price_ticks = price_to_ticks(level_price, tick_size)

    # Build ORB to get scan_from_index (post-ORB start point).
    # Override level_source to ORB_HIGH since we only need the ORB
    # window geometry, not the ORB level price.
    orb_config = {**config, "level_source": "ORB_HIGH", "direction": "LONG"}
    orb_result = build_orb(candles, session_context, orb_config)
    if orb_result.get("status") != "OK":
        return {
            "status": "FAILED",
            "failed_stage": "ORB_WINDOW_FAILED",
            "reason": (
                f"Cannot determine post-ORB scan start for {level_source}: "
                f"{orb_result.get('reason', 'ORB construction failed')}."
            ),
        }

    scan_from_index = orb_result["orb_candle_index"]
    scan_from_bar = candles[scan_from_index]

    return {
        "status": "OK",
        "date": current_date,
        "level_source": level_source,
        "direction": direction,
        "level_price": level_price,
        "level_price_ticks": level_price_ticks,
        "scan_from_index": scan_from_index,
        "scan_from_bar": scan_from_bar,
        "orb_candle_index": scan_from_index,
        "orb_candle": scan_from_bar,
        "provider_data": {
            "pdh": pdh_pdl["pdh"],
            "pdl": pdh_pdl["pdl"],
            "prev_date": pdh_pdl["prev_date"],
        },
    }


def validate_level_result(result: dict) -> tuple[bool, str]:
    """Validate that a dict conforms to the LevelResult contract.

    Returns (True, "") if valid, (False, reason) if not.
    Used by tests to verify provider implementations.
    """
    if not isinstance(result, dict):
        return False, "result is not a dict"

    if result.get("status") != "OK":
        return False, f"result status is {result.get('status')!r}, not 'OK'"

    missing = [f for f in LEVEL_RESULT_REQUIRED_FIELDS if f not in result]
    if missing:
        return False, f"missing required fields: {missing}"

    if not isinstance(result["scan_from_index"], int):
        return False, (
            f"scan_from_index must be int, "
            f"got {type(result['scan_from_index']).__name__}"
        )

    if not isinstance(result["scan_from_bar"], dict):
        return False, (
            f"scan_from_bar must be dict, "
            f"got {type(result['scan_from_bar']).__name__}"
        )

    if not isinstance(result["level_price"], (int, float)):
        return False, (
            f"level_price must be numeric, "
            f"got {type(result['level_price']).__name__}"
        )

    if not isinstance(result["level_price_ticks"], int):
        return False, (
            f"level_price_ticks must be int, "
            f"got {type(result['level_price_ticks']).__name__}"
        )

    return True, ""
