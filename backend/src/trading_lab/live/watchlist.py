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
    previous_sessions : list[dict] or None
        Previous-session historical bars as fetched at boot by
        ``fetch_previous_session_bars()`` — the same ``all_sessions``
        format expected by ``pdh_pdl_provider.compute_pdh_pdl()`` /
        ``build_level(..., all_sessions=...)``:
        ``[{"date": "YYYY-MM-DD", "candles": [...]}]``.
        Retained here (not just consumed into ``context_levels``) so
        it is available to the BDRR detector in a later task. Not
        wired into any detector yet.
    pdh_pdl_candidate : dict[str, dict] or None
        Observational-only PDH/PDL candidate result, keyed by
        direction ("LONG" for PDH, "SHORT" for PDL) — only the
        directions applicable to this runtime's configured direction
        are populated. Each value is
        ``evaluate_pdh_pdl_candidate()``'s return dict, enriched with
        ``direction`` and ``level_source``:
        ``{"direction": str, "level_source": str, "eligible": bool,
        "eligibility": dict, "signal_result": SignalResult | None}``.
        Recomputed every bar (see ``MaxBotRunner._on_bar_update``);
        nothing here ever reaches TradeOrchestrator, creates a pending
        order, or is sent to IBKR — it exists purely so a future
        PWA/audit view can display it.
    premarket_bars : list[dict] or None
        Today's raw premarket 1-minute candles as fetched at boot by
        ``fetch_premarket_bars()`` — the same shape as any other
        candle dict (``time_ms``/``open``/``high``/``low``/``close``/
        ``volume``), covering the 04:00-09:30 ET window up to boot
        time. Retained here (not just consumed into
        ``context_levels`` for the PMH/PML scalars) so it is available
        for future premarket-break-recognition work. Not used for
        break/eligibility/retest detection yet.
    premarket_context : dict[str, dict] or None
        Observational-only PDH/PDL premarket classification, keyed by
        direction ("LONG" for PDH, "SHORT" for PDL) — only the
        directions applicable to this runtime's configured direction
        are populated, mirroring ``pdh_pdl_candidate``. Each value is
        ``{"level_source": str, "level_price": float,
        "break_origin": "NONE" | "PREMARKET_OBSERVED" |
        "PREMARKET_CARRY_IN", "break_timestamp_ms": int | None}`` —
        exactly ``premarket_break_classifier.classify_premarket_context()``'s
        own output shape (plus the level price/source for convenience),
        never enriched with displacement, retest, or sequence-validity
        fields. Computed once at boot (see
        ``MaxBotRunner._update_premarket_context``), when both
        ``premarket_bars`` and ``context_levels`` first become
        available — premarket data and PDH/PDL are both fixed for the
        rest of the session, so there is nothing to recompute per bar.
        This is pure history/context: it is never read by
        ``evaluate_pdh_pdl_candidate()``, ``LiveSignalDetector``, or
        any other trading code, and can never change a SIGNAL/NO_SETUP
        outcome, entry, stop, target, or setup_key.
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
    previous_sessions: list | None = None  # all_sessions format for PDH/PDL provider
    pdh_pdl_candidate: dict | None = None  # observational only — see docstring above
    premarket_bars: list | None = None  # raw premarket 1m candles — see docstring above
    premarket_context: dict | None = None  # observational only — see docstring above

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
