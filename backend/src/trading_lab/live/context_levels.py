"""Live context levels — PDH/PDL from IBKR historical data.

Fetches the previous completed RTH session's bars at bootstrap
and computes PDH/PDL using the canonical ``compute_pdh_pdl``.

These are CONTEXT LEVELS only — they do NOT generate entries.

Does NOT:
    - create signal detectors for PDH/PDL
    - modify ORB entry logic
    - allow pre-ORB trading
    - implement PMH/PML
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.pdh_pdl_provider import compute_pdh_pdl

log = logging.getLogger("maxbot")


@dataclass(frozen=True, slots=True)
class ContextLevels:
    """Previous-day context levels for one symbol.

    These are observational — they never trigger entries.

    Attributes
    ----------
    symbol : str
    pdh : float or None
        Previous Day High.
    pdl : float or None
        Previous Day Low.
    prev_date : str or None
        Date of the previous session used (YYYY-MM-DD).
    status : str
        "OK" or failure reason.
    """

    symbol: str
    pdh: float | None = None
    pdl: float | None = None
    prev_date: str | None = None
    status: str = "OK"

    def to_dict(self) -> dict:
        d = {"symbol": self.symbol, "status": self.status}
        if self.pdh is not None:
            d["pdh"] = self.pdh
        if self.pdl is not None:
            d["pdl"] = self.pdl
        if self.prev_date is not None:
            d["prev_date"] = self.prev_date
        return d


def fetch_previous_session_bars(ib, stock, tz: ZoneInfo) -> list[dict]:
    """Fetch previous completed RTH session bars from IBKR.

    Uses reqHistoricalData with durationStr="2 D" and useRTH=True
    to get yesterday's + today's bars (if any). Returns only bars
    from the most recent COMPLETED session (not today).

    Parameters
    ----------
    ib : ib_insync.IB
        Connected IB instance.
    stock : ib_insync.Stock
        Qualified stock contract.
    tz : ZoneInfo
        Market timezone.

    Returns
    -------
    list[dict]
        Candle dicts with time_ms, open, high, low, close, volume.
        Empty list if no previous session data available.
    """
    try:
        bars = ib.reqHistoricalData(
            stock,
            endDateTime="",
            durationStr="2 D",
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
            keepUpToDate=False,  # one-shot, not streaming
        )
    except Exception as e:
        log.warning(f"PDH/PDL fetch failed for {stock.symbol}: {e}")
        return []

    if not bars:
        return []

    # Convert to candle dicts and group by date
    candles_by_date: dict[str, list[dict]] = {}
    for bar in bars:
        dt = bar.date
        if hasattr(dt, "astimezone"):
            dt_local = dt.astimezone(tz)
        elif hasattr(dt, "replace"):
            dt_utc = dt.replace(tzinfo=timezone.utc)
            dt_local = dt_utc.astimezone(tz)
        else:
            dt_local = datetime.fromisoformat(str(dt))

        date_str = dt_local.strftime("%Y-%m-%d")
        time_ms = int(dt_local.astimezone(timezone.utc).timestamp() * 1000)

        candle = {
            "time_ms": time_ms,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": int(bar.volume),
        }

        if date_str not in candles_by_date:
            candles_by_date[date_str] = []
        candles_by_date[date_str].append(candle)

    return _build_sessions_list(candles_by_date)


def _build_sessions_list(candles_by_date: dict[str, list[dict]]) -> list[dict]:
    """Convert candles-by-date dict to sessions list for compute_pdh_pdl."""
    sessions = []
    for date_str in sorted(candles_by_date.keys()):
        sessions.append({
            "date": date_str,
            "candles": candles_by_date[date_str],
        })
    return sessions


def compute_live_context_levels(
    symbol: str,
    current_date: str,
    previous_sessions: list[dict],
) -> ContextLevels:
    """Compute PDH/PDL from previous session data.

    Parameters
    ----------
    symbol : str
        Underlying symbol.
    current_date : str
        Current trading date YYYY-MM-DD.
    previous_sessions : list[dict]
        Sessions list (from fetch_previous_session_bars or test fixture).

    Returns
    -------
    ContextLevels
    """
    if not previous_sessions:
        return ContextLevels(
            symbol=symbol,
            status="NO_PREVIOUS_SESSION",
        )

    result = compute_pdh_pdl(current_date, previous_sessions)

    if result["status"] != "OK":
        return ContextLevels(
            symbol=symbol,
            status=result.get("status", "FAILED"),
        )

    return ContextLevels(
        symbol=symbol,
        pdh=result["pdh"],
        pdl=result["pdl"],
        prev_date=result["prev_date"],
        status="OK",
    )
