"""Multi-symbol watchlist manager for MaxBot v0.1.

Manages independent per-symbol runtime state while sharing a single
IBKR connection.

Each symbol gets:
    - its own LiveSessionBuilder
    - its own signal detector (or DualSignalDetector for BOTH)
    - its own orchestrator (observe or trade)
    - its own DailyTradeManager
    - its own processed-bar timestamps
    - its own historical bar subscription

No cross-symbol state contamination.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SymbolRuntime:
    """Per-symbol runtime container.

    Attributes
    ----------
    symbol : str
        Underlying symbol.
    underlying_contract : object or None
        Qualified Stock contract.
    bars : object or None
        BarDataList from reqHistoricalData.
    session_builder : object or None
        LiveSessionBuilder instance.
    signal_detector : object or None
        Signal detector (single or dual).
    orchestrator : object or None
        Trade or observe orchestrator.
    trade_manager : object or None
        DailyTradeManager (paper mode only).
    processed_times : set
        Set of processed bar time_ms values.
    enabled : bool
        False if qualification failed.
    error : str or None
        Error message if disabled.
    """

    symbol: str
    underlying_contract: object | None = None
    bars: object | None = None
    session_builder: object | None = None
    signal_detector: object | None = None
    orchestrator: object | None = None
    trade_manager: object | None = None
    processed_times: set = field(default_factory=set)
    enabled: bool = True
    error: str | None = None


def parse_symbols(symbols_str: str) -> list[str]:
    """Parse and normalize a comma-separated symbol string.

    Deduplicates, uppercases, and preserves order.

    Raises ValueError on empty input.
    """
    if not symbols_str or not symbols_str.strip():
        raise ValueError("Symbol list cannot be empty")

    seen = set()
    result = []
    for s in symbols_str.split(","):
        sym = s.strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            result.append(sym)

    if not result:
        raise ValueError("No valid symbols after parsing")

    return result
