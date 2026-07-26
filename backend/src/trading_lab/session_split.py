"""Canonical session splitting for the BDRR pipeline.

Ported from ``splitIntoSessions`` in
estrategie/bdrr_strategy_runner.js (lines 113–126).

Groups raw candle dicts (as returned by ``parse_candles_from_csv``) by
calendar date in a given timezone, then returns the groups sorted
alphabetically by date key.

Behavior (matching JS exactly):

    1. For each candle, derive the calendar date string ("YYYY-MM-DD")
       by converting the candle's epoch-ms timestamp to the requested
       timezone.

    2. Group candles by that date key, preserving insertion order within
       each group.

    3. Sort groups alphabetically by date key (ascending).

    4. Return a list of ``{"date": str, "candles": list[dict]}``.

    5. The original candle dict objects are placed directly in the output
       lists — no copies are made.  Object identity is preserved, matching
       the JS ``map.get(d).push(c)`` behavior.

    6. No filtering, deduplication, validation, or normalization.

Timezone:

    The ``timezone`` parameter is a required IANA timezone string
    (e.g. ``"America/New_York"``).  The JS implementation uses
    ``Intl.DateTimeFormat('en-CA', {timeZone: timezone, ...})`` which
    correctly handles EST/EDT transitions.  The Python implementation
    uses ``zoneinfo.ZoneInfo`` for the same historical DST behavior.

Input candle shape:

    Each candle must be a dict with a ``time_ms`` key (int, epoch
    milliseconds UTC), as produced by ``parse_candles_from_csv``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def split_into_sessions(
    candles: list[dict],
    timezone_name: str,
) -> list[dict]:
    """Group raw candles by calendar date in the given timezone.

    Matches JS ``splitIntoSessions(candles, timezone)`` in
    bdrr_strategy_runner.js:113–126.

    Parameters
    ----------
    candles : list[dict]
        Raw candle dicts, each with a ``time_ms`` key (int, epoch ms).
    timezone_name : str
        IANA timezone string (e.g. ``"America/New_York"``).

    Returns
    -------
    list[dict]
        Each element is ``{"date": str, "candles": list[dict]}``.
        Sorted alphabetically by date key.
        Candle objects are the same dict instances from the input.

    Raises
    ------
    TypeError
        If candles is not a list or timezone_name is not a string.
    KeyError
        If timezone_name is not a valid IANA timezone.
    """
    if not isinstance(candles, list):
        raise TypeError(
            f"candles must be a list, got {type(candles).__name__}"
        )
    if not isinstance(timezone_name, str):
        raise TypeError(
            f"timezone_name must be a str, got {type(timezone_name).__name__}"
        )

    tz = ZoneInfo(timezone_name)

    # Group by date key, preserving insertion order (dict is ordered in 3.7+).
    groups: dict[str, list[dict]] = {}
    for c in candles:
        ms = c["time_ms"]
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(tz)
        date_key = dt.strftime("%Y-%m-%d")
        if date_key not in groups:
            groups[date_key] = []
        groups[date_key].append(c)

    # Sort by date key ascending (JS: .sort((a,b) => a[0].localeCompare(b[0])))
    sorted_keys = sorted(groups.keys())
    return [{"date": k, "candles": groups[k]} for k in sorted_keys]
