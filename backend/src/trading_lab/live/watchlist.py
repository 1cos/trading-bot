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
    broker_position_blocked : bool
        True if an existing IBKR option position for this symbol was
        found at startup reconciliation — new entries are blocked.
    broker_position_info : dict or None
        Details of the detected existing position (conId, localSymbol,
        right, strike, expiry, quantity), or None if not blocked.
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
    context_levels: object | None = None  # ContextLevels (PDH/PDL/PMH/PML)

    # Existing broker position reconciliation (startup safety gate).
    # broker_position_blocked is True when an existing, non-zero IBKR
    # OPTION position for this symbol was found at startup — new
    # entries are blocked for the rest of the session (see
    # MaxBotRunner._reconcile_existing_positions).
    broker_position_blocked: bool = False
    broker_position_info: dict | None = None

    # Feed health
    last_bar_time_ms: int = 0
    processed_bar_count: int = 0
    feed_status: str = "INITIALIZING"  # INITIALIZING / LIVE / STALE
    last_resubscribe_time: float = 0.0  # monotonic time of last resubscribe
    resubscribe_count: int = 0
    subscription_start_time: float = 0.0  # monotonic time of subscription creation
    bars_object_id: int = 0   # id() of current BarDataList — tracks object identity
    listener_count: int = 0   # updateEvent listener count after registration
    last_known_bars_count: int = 0  # for detecting whether BarDataList is still growing

    # Decision trace — last N candle decisions for PWA display
    decision_trace: list = field(default_factory=list)
    max_trace_entries: int = 60  # keep last 60 candles (~1 hour)

    # Pipeline observability
    pipeline_stage: str = ""        # e.g. "BUILDING ORB", "ORB COMPLETE — NO BREAK"
    orb_high: float | None = None
    orb_low: float | None = None
    last_stage_context: dict | None = None


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
