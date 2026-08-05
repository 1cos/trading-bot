"""Previous Day High / Low level provider.

Calculates PDH and PDL from the last valid trading session before
the current session date. Uses only candle data that is strictly
before the current session — no look-ahead.

Definition of "previous session":
    The most recent calendar date (in the session timezone) that
    has at least one candle AND whose date is strictly less than
    the current session date. This handles weekends, holidays, and
    missing days naturally: if Friday's session is the last before
    Monday, Friday is the previous session.

PDH = max(high) of all candles in the previous session.
PDL = min(low) of all candles in the previous session.
"""

from __future__ import annotations


def compute_pdh_pdl(
    current_date: str,
    all_sessions: list[dict],
) -> dict:
    """Compute Previous Day High and Previous Day Low.

    Parameters
    ----------
    current_date : str
        The date of the current session ("YYYY-MM-DD").
    all_sessions : list[dict]
        All available sessions, each with "date" (str) and "candles"
        (list[dict]). Must be sorted by date ascending. Each candle
        must have "high" and "low" fields.

    Returns
    -------
    dict
        On success:
            status    str    "OK"
            pdh       float  Previous Day High
            pdl       float  Previous Day Low
            prev_date str    Date of the previous session used

        On failure:
            status    str    "NO_PREVIOUS_SESSION"
            reason    str    Explanation
    """
    prev_session = None

    for s in all_sessions:
        if s["date"] >= current_date:
            break
        if s.get("candles"):
            prev_session = s

    if prev_session is None:
        return {
            "status": "NO_PREVIOUS_SESSION",
            "reason": (
                f"No session with candles found before {current_date}. "
                f"PDH/PDL require at least one prior trading day."
            ),
        }

    candles = prev_session["candles"]
    pdh = max(c["high"] for c in candles)
    pdl = min(c["low"] for c in candles)

    return {
        "status": "OK",
        "pdh": pdh,
        "pdl": pdl,
        "prev_date": prev_session["date"],
    }
