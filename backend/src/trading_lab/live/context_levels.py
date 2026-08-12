"""Live context levels — PDH/PDL + PMH/PML from IBKR historical data.

Fetches at bootstrap:
    PDH/PDL — previous completed RTH session bars (useRTH=True)
    PMH/PML — today's premarket bars (useRTH=False, filtered to
              pre-open window)

These are CONTEXT LEVELS only — they do NOT generate entries.

Premarket window definition (Max's convention):
    CT 03:00 → market open (CT 08:30 for equities)
    ET 04:00 → market open (ET 09:30 for equities)

Does NOT:
    - create signal detectors for PDH/PDL/PMH/PML
    - modify ORB entry logic
    - allow pre-ORB trading
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.pdh_pdl_provider import compute_pdh_pdl

log = logging.getLogger("maxbot")


# ── Premarket window (ET) ────────────────────────────────────────────────────

PREMARKET_START_ET = (4, 0)   # 04:00 ET = 03:00 CT
PREMARKET_END_ET = (9, 30)    # 09:30 ET = 08:30 CT (market open)


@dataclass(frozen=True, slots=True)
class ContextLevels:
    """Previous-day and premarket context levels for one symbol.

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
    pmh : float or None
        Premarket High (today).
    pml : float or None
        Premarket Low (today).
    pm_bar_count : int
        Number of premarket bars used.
    status : str
        "OK" or failure reason.
    """

    symbol: str
    pdh: float | None = None
    pdl: float | None = None
    prev_date: str | None = None
    pmh: float | None = None
    pml: float | None = None
    pm_bar_count: int = 0
    premarket_final: bool = False
    premarket_date: str | None = None
    status: str = "OK"

    def to_dict(self) -> dict:
        d = {"symbol": self.symbol, "status": self.status}
        if self.pdh is not None:
            d["pdh"] = self.pdh
        if self.pdl is not None:
            d["pdl"] = self.pdl
        if self.prev_date is not None:
            d["prev_date"] = self.prev_date
        if self.pmh is not None:
            d["pmh"] = self.pmh
        if self.pml is not None:
            d["pml"] = self.pml
        if self.pm_bar_count > 0:
            d["pm_bar_count"] = self.pm_bar_count
        if self.premarket_date is not None:
            d["premarket_date"] = self.premarket_date
        d["premarket_final"] = self.premarket_final
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


# ── Premarket fetch ──────────────────────────────────────────────────────────

def _is_premarket_bar(time_ms: int, tz: ZoneInfo,
                      pm_start: tuple[int, int] = PREMARKET_START_ET,
                      pm_end: tuple[int, int] = PREMARKET_END_ET) -> bool:
    """Check if a bar falls within the premarket window."""
    dt = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).astimezone(tz)
    bar_minutes = dt.hour * 60 + dt.minute
    start_minutes = pm_start[0] * 60 + pm_start[1]
    end_minutes = pm_end[0] * 60 + pm_end[1]
    return start_minutes <= bar_minutes < end_minutes


def fetch_premarket_bars(
    ib,
    stock,
    tz: ZoneInfo,
    today_date: str,
) -> list[dict]:
    """Fetch today's premarket bars from IBKR.

    Uses reqHistoricalData with useRTH=False to include extended hours.
    Filters to the premarket window (04:00–09:29 ET / 03:00–08:29 CT).

    Parameters
    ----------
    ib : ib_insync.IB
        Connected IB instance.
    stock : ib_insync.Stock
        Qualified stock contract.
    tz : ZoneInfo
        Market timezone (America/New_York).
    today_date : str
        Current date YYYY-MM-DD (for filtering).

    Returns
    -------
    list[dict]
        Premarket candle dicts. Empty if no premarket data.
    """
    try:
        bars = ib.reqHistoricalData(
            stock,
            endDateTime="",
            durationStr="1 D",
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=False,  # include extended hours
            formatDate=2,
            keepUpToDate=False,
        )
    except Exception as e:
        log.warning(f"PMH/PML fetch failed for {stock.symbol}: {e}")
        return []

    if not bars:
        return []

    premarket_candles = []
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
        if date_str != today_date:
            continue

        time_ms = int(dt_local.astimezone(timezone.utc).timestamp() * 1000)

        if not _is_premarket_bar(time_ms, tz):
            continue

        premarket_candles.append({
            "time_ms": time_ms,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": int(bar.volume),
        })

    return premarket_candles


def compute_pmh_pml(premarket_bars: list[dict]) -> dict:
    """Compute Premarket High and Low from premarket bars.

    Parameters
    ----------
    premarket_bars : list[dict]
        Candle dicts from the premarket window.

    Returns
    -------
    dict
        On success: {status: "OK", pmh: float, pml: float, bar_count: int}
        On failure: {status: "NO_PREMARKET_DATA"}
    """
    if not premarket_bars:
        return {"status": "NO_PREMARKET_DATA"}

    pmh = max(c["high"] for c in premarket_bars)
    pml = min(c["low"] for c in premarket_bars)

    return {
        "status": "OK",
        "pmh": pmh,
        "pml": pml,
        "bar_count": len(premarket_bars),
    }


# ── Combined context computation ─────────────────────────────────────────────

def compute_live_context_levels(
    symbol: str,
    current_date: str,
    previous_sessions: list[dict],
    premarket_bars: list[dict] | None = None,
    premarket_final: bool = False,
) -> ContextLevels:
    """Compute PDH/PDL + PMH/PML from session and premarket data.

    Parameters
    ----------
    symbol : str
        Underlying symbol.
    current_date : str
        Current trading date YYYY-MM-DD.
    previous_sessions : list[dict]
        Sessions list for PDH/PDL.
    premarket_bars : list[dict] or None
        Today's premarket candles for PMH/PML. None = skip.
    premarket_final : bool
        True if premarket window is complete (market open or after).

    Returns
    -------
    ContextLevels
    """
    # PDH/PDL
    pdh = None
    pdl = None
    prev_date = None
    pdh_status = "OK"

    if previous_sessions:
        result = compute_pdh_pdl(current_date, previous_sessions)
        if result["status"] == "OK":
            pdh = result["pdh"]
            pdl = result["pdl"]
            prev_date = result["prev_date"]
        else:
            pdh_status = result.get("status", "FAILED")
    else:
        pdh_status = "NO_PREVIOUS_SESSION"

    # PMH/PML
    pmh = None
    pml = None
    pm_bar_count = 0

    if premarket_bars:
        pm_result = compute_pmh_pml(premarket_bars)
        if pm_result["status"] == "OK":
            pmh = pm_result["pmh"]
            pml = pm_result["pml"]
            pm_bar_count = pm_result["bar_count"]

    # Overall status: OK if at least PDH/PDL succeeded
    status = pdh_status

    return ContextLevels(
        symbol=symbol,
        pdh=pdh,
        pdl=pdl,
        prev_date=prev_date,
        pmh=pmh,
        pml=pml,
        pm_bar_count=pm_bar_count,
        premarket_final=premarket_final,
        premarket_date=current_date if pmh is not None else None,
        status=status,
    )
