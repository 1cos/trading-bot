"""MaxBot v0.1 IBKR Paper live runner — multi-symbol OPTIONS bot.

Connects to IBKR Paper TWS/Gateway, subscribes to underlying 1m bars
for each symbol in the watchlist, feeds completed bars into per-symbol
orchestrators, and refreshes pending broker state.

PAPER ONLY — verifies paper-account status before any order submission.

Usage (multi-symbol):
    python -m trading_lab.live.bot_runner \\
        --symbols QQQ,SPY,NVDA,AMD \\
        --direction BOTH \\
        --execution-mode OBSERVE_ONLY

Usage (single symbol, backward compatible):
    python -m trading_lab.live.bot_runner \\
        --symbol QQQ --direction LONG

One IB connection shared across all symbols.
Each symbol has independent strategy/session/lifecycle state.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ib_insync import IB, Stock

from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.dual_signal_detector import DualSignalDetector
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.option_selector import OptionContractSelector
from trading_lab.live.ibkr_option_executor import IBKROptionExecutor
from trading_lab.live.option_exit_executor import OptionExitExecutor
from trading_lab.live.trade_orchestrator import (
    MaxBotTradeOrchestrator,
    LifecycleState,
)
from trading_lab.live.observe_orchestrator import (
    ExecutionMode,
    ObserveOrchestrator,
    ObserveLifecycle,
)
from trading_lab.live.watchlist import SymbolRuntime, parse_symbols
from trading_lab.live.event_stream import EventFactory, SessionEventLog, EventType
from trading_lab.live.execution_queue import (
    ExecutionQueue,
    ExecutionWorkItem,
    WorkItemType,
)
from trading_lab.live.decision_trace import (
    build_candle_trace,
    format_trace_line,
    trace_to_dict,
)
from trading_lab.live.context_levels import (
    ContextLevels,
    fetch_previous_session_bars,
    fetch_premarket_bars,
    compute_live_context_levels,
)

log = logging.getLogger("maxbot")


# ── Paper-account verification ───────────────────────────────────────────────

def verify_paper_account(ib: IB) -> str:
    """Verify the connected session is a PAPER account.

    IBKR paper accounts have IDs starting with 'D' (e.g. 'DU1234567').

    Returns the paper account ID.
    Raises RuntimeError if not paper.
    """
    accounts = ib.managedAccounts()
    if not accounts:
        raise RuntimeError(
            "No managed accounts found — cannot verify paper status. "
            "Ensure TWS/Gateway is running and API access is enabled."
        )
    for acct in accounts:
        if acct.startswith("D"):
            return acct
    raise RuntimeError(
        f"No paper account found. Managed accounts: {accounts}. "
        f"MaxBot v0.1 is PAPER ONLY — refusing to proceed."
    )


# ── Bar conversion ───────────────────────────────────────────────────────────

def ibkr_bar_to_candle(bar, tz: ZoneInfo) -> dict:
    """Convert an ib_insync BarData to the project's candle dict format."""
    dt = bar.date
    if hasattr(dt, "astimezone"):
        dt_utc = dt.astimezone(timezone.utc)
    elif hasattr(dt, "replace"):
        dt_utc = dt.replace(tzinfo=timezone.utc)
    else:
        dt_utc = datetime.fromisoformat(str(dt)).replace(tzinfo=timezone.utc)
    time_ms = int(dt_utc.timestamp() * 1000)
    return {
        "time_ms": time_ms,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": int(bar.volume),
    }


def is_rth_bar(time_ms: int, tz: ZoneInfo, open_hhmm: str, close_hhmm: str) -> bool:
    """Check if a bar's timestamp falls within RTH session hours."""
    dt = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).astimezone(tz)
    bar_minutes = dt.hour * 60 + dt.minute
    open_h, open_m = int(open_hhmm[:2]), int(open_hhmm[3:])
    close_h, close_m = int(close_hhmm[:2]), int(close_hhmm[3:])
    open_minutes = open_h * 60 + open_m
    close_minutes = close_h * 60 + close_m
    return open_minutes <= bar_minutes < close_minutes


# ── Runner ───────────────────────────────────────────────────────────────────


def _format_stage(pipeline_stage: str, failed_stage: str | None,
                  ctx: dict) -> str:
    """Format pipeline stage into a detailed human-readable log suffix.

    The format reflects the CURRENT candle's relationship to the pipeline,
    not just the last historical event.  Each stage has a distinct name
    so the PWA/terminal can show where in the BDRR sequence the bot is.

    Stages (in order):
        BUILDING ORB           — ORB candles still accumulating
        WAITING FOR BREAK      — ORB complete, no break yet
        DISP BUILDING 1/3      — break found, displacement not confirmed
        RETEST TOO EARLY       — candle touched level before displacement
        DISP CONFIRMED         — displacement complete, waiting retest
        WAITING FOR RETEST     — displacement done, price hasn't returned
        RETEST — NO ENTRY      — retest found, no qualifying entry candle
        SEQUENCE INVALIDATED   — 2+ closes back inside ORB after displacement
        SIGNAL                 — entry candle accepted
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

    direction = ctx.get("direction", "")
    orb_h = ctx.get("orb_high")
    orb_l = ctx.get("orb_low")
    break_close = ctx.get("break_close")
    break_level = ctx.get("break_level")
    break_time = ctx.get("break_time_ms")
    disp_bars = ctx.get("displacement_bars")
    disp_req = ctx.get("displacement_required", 3)

    def _time_str(ms):
        if not ms:
            return "?"
        return datetime.fromtimestamp(ms / 1000, tz=ET).strftime("%H:%M")

    if failed_stage == "LEVEL_NOT_FOUND":
        return " [BUILDING ORB]"

    if failed_stage == "BREAK_NOT_FOUND":
        return f" [WAITING FOR BREAK] {direction} ORB H={orb_h:.2f} L={orb_l:.2f}"

    if failed_stage == "DISPLACEMENT_TOO_SHORT":
        bt = _time_str(break_time)
        return (f" [DISP BUILDING] {direction} {disp_bars}/{disp_req} bars"
                f", break={break_close:.2f}@{bt}")

    if failed_stage == "RETEST_BEFORE_DISPLACEMENT":
        bt = _time_str(break_time)
        return f" [RETEST TOO EARLY] {direction}, break={break_close:.2f}@{bt}"

    if failed_stage == "RETEST_NOT_FOUND":
        bt = _time_str(break_time)
        return (f" [WAITING FOR RETEST] {direction} disp={disp_bars}"
                f", break={break_close:.2f}@{bt}")

    if failed_stage == "SEQUENCE_INVALIDATED":
        inv_idx = ctx.get("invalidation_index")
        return f" [SETUP INVALIDATED] {direction}, at bar {inv_idx}"

    if failed_stage == "NO_QUALIFYING_REJECTION_CANDLE":
        rules = ctx.get("failed_rules", [])
        rules_str = ", ".join(rules[:3]) if rules else "none in window"
        bt = _time_str(break_time)
        return (f" [RETEST — NO ENTRY] {direction} disp={disp_bars}"
                f", break@{bt}, rules: {rules_str}")

    # Fallback
    return f" [{pipeline_stage}]"


class MaxBotRunner:
    """IBKR Paper live runner for MaxBot v0.1 — multi-symbol.

    Parameters
    ----------
    symbols : list[str]
        List of underlying symbols.
    direction : str
        "LONG", "SHORT", or "BOTH".
    host, port, client_id : connection config.
    tick_size : float
        Default tick size for all symbols.
    market_timezone, session_open, session_close : session config.
    execution_mode : str
        "OBSERVE_ONLY" (default) or "PAPER_EXECUTE".
    trade_limits_enabled : bool
        If False, DailyTradeManager uses unlimited mode (test phase).
    """

    def __init__(
        self,
        symbols: list[str] | str,
        direction: str = "BOTH",
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        tick_size: float = 0.01,
        market_timezone: str = "America/New_York",
        session_open: str = "09:30",
        session_close: str = "16:00",
        execution_mode: str = "OBSERVE_ONLY",
        trade_limits_enabled: bool = False,
    ):
        # Normalize symbols
        if isinstance(symbols, str):
            self._symbols = [symbols.upper()]
        else:
            self._symbols = [s.upper() for s in symbols]

        self._direction = direction
        self._host = host
        self._port = port
        self._client_id = client_id
        self._tick_size = tick_size
        self._tz_str = market_timezone
        self._tz = ZoneInfo(market_timezone)
        self._session_open = session_open
        self._session_close = session_close
        self._execution_mode = ExecutionMode(execution_mode)
        self._trade_limits_enabled = trade_limits_enabled

        self._ib: IB | None = None
        self._runtimes: dict[str, SymbolRuntime] = {}
        self._running = False
        self._paper_account: str | None = None

        # Execution queue — defers IBKR sync work outside callbacks
        self._execution_queue = ExecutionQueue()

        # Live boundary: only signals whose entry candle is AFTER this
        # timestamp can trigger execution.  Set when main loop starts.
        # Prevents mid-session restarts from executing stale setups.
        self._live_start_time_ms: int = 0

        # Event infrastructure
        self._event_factory = EventFactory(execution_mode)
        self._session_log = SessionEventLog(metadata={
            "trading_date": None,
            "execution_mode": execution_mode,
            "direction": direction,
            "watchlist": self._symbols,
            "trade_limits_enabled": trade_limits_enabled,
            "host": host,
            "port": port,
            "client_id": client_id,
        })

    # ── Public API ───────────────────────────────────────────────────────

    def run(self) -> None:
        """Connect to IBKR Paper and run the trading session."""
        self._emit(EventType.BOT_STARTED, data={
            "symbols": self._symbols, "direction": self._direction,
            "execution_mode": str(self._execution_mode),
        })
        self._connect()
        try:
            self._verify_paper()
            self._setup_all_symbols()
            self._qualify_all()
            self._reconcile_existing_positions()
            self._compute_context_levels()
            self._subscribe_all()
            self._run_loop()
        except KeyboardInterrupt:
            log.info("Keyboard interrupt — shutting down")
            self._emit(EventType.BOT_STOPPED, data={"reason": "keyboard_interrupt"})
        except Exception as e:
            log.error(f"Runner error: {e}")
            self._emit(EventType.ERROR, data={"error": str(e)})
            raise
        finally:
            self._shutdown()

    @property
    def symbol_statuses(self) -> dict[str, str]:
        """Aggregate status for all symbols."""
        result = {}
        for sym, rt in self._runtimes.items():
            if not rt.enabled:
                result[sym] = f"DISABLED: {rt.error}"
            elif rt.orchestrator is None:
                result[sym] = "NOT_INITIALIZED"
            elif self._execution_mode == ExecutionMode.OBSERVE_ONLY:
                result[sym] = str(rt.orchestrator.lifecycle)
            else:
                result[sym] = str(rt.orchestrator.lifecycle)
        return result

    @property
    def enabled_count(self) -> int:
        return sum(1 for rt in self._runtimes.values() if rt.enabled)

    @property
    def disabled_symbols(self) -> list[str]:
        return [sym for sym, rt in self._runtimes.items() if not rt.enabled]

    @property
    def total_open_positions(self) -> int:
        count = 0
        for rt in self._runtimes.values():
            if not rt.enabled or rt.orchestrator is None:
                continue
            if self._execution_mode == ExecutionMode.OBSERVE_ONLY:
                if rt.orchestrator.lifecycle == ObserveLifecycle.TRACKING_EXIT:
                    count += 1
            else:
                if rt.orchestrator.lifecycle in (
                    LifecycleState.POSITION_OPEN,
                    LifecycleState.EXIT_SUBMITTED,
                ):
                    count += 1
        return count

    # ── Event emission ───────────────────────────────────────────────────

    def _emit(self, event_type, symbol: str = "", direction: str | None = None,
              lifecycle: str | None = None, data: dict | None = None) -> LiveEvent:
        """Create and append an event to the session log."""
        event = self._event_factory.create(
            event_type=event_type, symbol=symbol, direction=direction,
            lifecycle=lifecycle, data=data,
        )
        self._session_log.append(event)
        return event

    @property
    def session_log(self) -> SessionEventLog:
        return self._session_log

    # ── Connection ───────────────────────────────────────────────────────

    def _connect(self) -> None:
        self._ib = IB()
        log.info(f"Connecting to {self._host}:{self._port} (clientId={self._client_id})")
        self._ib.connect(self._host, self._port, clientId=self._client_id)
        log.info("CONNECTED")
        self._emit(EventType.IBKR_CONNECTED)

    def _verify_paper(self) -> None:
        self._paper_account = verify_paper_account(self._ib)
        log.info(f"PAPER VERIFIED — account: {self._paper_account}")
        self._emit(EventType.PAPER_VERIFIED, data={"account": self._paper_account[:3] + "***"})

    def _shutdown(self) -> None:
        unresolved = []
        for sym, rt in self._runtimes.items():
            if not rt.enabled or rt.orchestrator is None:
                continue
            if self._execution_mode == ExecutionMode.OBSERVE_ONLY:
                state = rt.orchestrator.lifecycle
            else:
                state = rt.orchestrator.lifecycle
                if state in (LifecycleState.POSITION_OPEN,
                             LifecycleState.ENTRY_SUBMITTED,
                             LifecycleState.EXIT_SUBMITTED,
                             LifecycleState.EXIT_FAILED,
                             LifecycleState.REQUIRES_ATTENTION):
                    unresolved.append((sym, state))
        if unresolved:
            for sym, state in unresolved:
                log.warning(f"UNRESOLVED {sym}: {state}")

        self._emit(EventType.BOT_STOPPED, data={
            "statuses": self.symbol_statuses,
            "unresolved": [{"symbol": s, "state": str(st)} for s, st in unresolved],
        })

        # Export session log
        try:
            now = datetime.now(self._tz)
            self._session_log.set_metadata("trading_date", now.strftime("%Y-%m-%d"))
            self._session_log.set_metadata("runner_end_time", now.isoformat())
            ts = now.strftime("%Y-%m-%d_%H%M%S")
            log_dir = Path("logs/maxbot")
            json_path = self._session_log.export_json(log_dir / f"maxbot_{ts}.json")
            md_path = self._session_log.export_markdown(log_dir / f"maxbot_{ts}.md")
            log.info(f"Session log exported: {json_path}")
        except Exception as e:
            log.error(f"Failed to export session log: {e}")

        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            log.info("Disconnected from IBKR")

    # ── Per-symbol setup ─────────────────────────────────────────────────

    def _setup_all_symbols(self) -> None:
        for sym in self._symbols:
            rt = SymbolRuntime(symbol=sym)
            self._setup_symbol(rt)
            self._runtimes[sym] = rt
        enabled = self.enabled_count
        log.info(f"Symbols configured: {len(self._symbols)}, enabled: {enabled}")

    def _setup_symbol(self, rt: SymbolRuntime) -> None:
        sym = rt.symbol
        sb = LiveSessionBuilder(sym, self._tz_str)
        rt.session_builder = sb

        # Signal detector
        if self._direction == "BOTH":
            long_sd = LiveSignalDetector(
                symbol=sym, direction="LONG", tick_size=self._tick_size,
                market_timezone=self._tz_str, session_open=self._session_open,
            )
            short_sd = LiveSignalDetector(
                symbol=sym, direction="SHORT", tick_size=self._tick_size,
                market_timezone=self._tz_str, session_open=self._session_open,
            )
            sd = DualSignalDetector(long_sd, short_sd)
        else:
            sd = LiveSignalDetector(
                symbol=sym, direction=self._direction,
                tick_size=self._tick_size, market_timezone=self._tz_str,
                session_open=self._session_open,
            )
        rt.signal_detector = sd

        os_ = OptionContractSelector(self._ib)

        if self._execution_mode == ExecutionMode.OBSERVE_ONLY:
            rt.orchestrator = ObserveOrchestrator(
                underlying_symbol=sym, direction=self._direction,
                tick_size=self._tick_size, session_builder=sb,
                signal_detector=sd, option_selector=os_,
                emit=self._emit,
            )
        else:
            unlimited = not self._trade_limits_enabled
            tm = DailyTradeManager(unlimited=unlimited)
            rt.trade_manager = tm
            ee = IBKROptionExecutor(self._ib)
            xe = OptionExitExecutor(self._ib)
            rt.orchestrator = MaxBotTradeOrchestrator(
                underlying_symbol=sym, direction=self._direction,
                tick_size=self._tick_size, session_builder=sb,
                signal_detector=sd, trade_manager=tm,
                option_selector=os_, entry_executor=ee,
                exit_executor=xe, emit=self._emit,
            )

    def _qualify_all(self) -> None:
        for sym, rt in self._runtimes.items():
            try:
                stock = Stock(sym, "SMART", "USD")
                qualified = self._ib.qualifyContracts(stock)
                if not qualified:
                    raise RuntimeError(f"qualifyContracts returned empty for {sym}")
                rt.underlying_contract = stock
                log.info(f"QUALIFIED: {sym} (conId={stock.conId})")
                self._emit(EventType.SYMBOL_ENABLED, symbol=sym,
                           data={"con_id": stock.conId})
            except Exception as e:
                rt.enabled = False
                rt.error = str(e)
                log.error(f"DISABLED {sym}: {e}")
                self._emit(EventType.SYMBOL_DISABLED, symbol=sym,
                           data={"error": str(e)})

    def _reconcile_existing_positions(self) -> None:
        """Startup safety gate: block new entries on symbols that already
        have an existing, untracked IBKR option position.

        Runs once at startup, after qualification and before the run
        loop / any signal can be accepted. Does NOT attempt to recover
        the original trade's entry/stop/target/setup_key — that is a
        separate, later task. Positions found here are treated
        conservatively as external/existing broker positions, not
        automatically adopted as MaxBot-managed trades.

        Matching (per position):
          - position.position != 0        (zero/closed positions ignored)
          - contract.secType == "OPT"     (non-option positions, e.g. a
            plain stock position, are intentionally NOT treated as a
            block — MaxBot only ever holds options, so a stock position
            in the account is not evidence of an untracked MaxBot trade)
          - contract.symbol is a symbol in this runner's watchlist

        For each match: marks the SymbolRuntime and (for PAPER_EXECUTE)
        the orchestrator as blocked, transitions the orchestrator's
        lifecycle to EXISTING_BROKER_POSITION (which — via the existing
        on_bar() dispatch, unchanged — means _check_for_signal() is
        never called again for that symbol this session, so no new
        entry can ever be enqueued), and logs an explicit message.
        """
        try:
            positions = self._ib.positions()
        except Exception as e:
            log.error(f"Position reconciliation failed: {e}")
            return

        for pos in positions:
            contract = getattr(pos, "contract", None)
            quantity = getattr(pos, "position", 0)
            if contract is None or quantity == 0:
                continue
            if getattr(contract, "secType", None) != "OPT":
                continue

            underlying_symbol = getattr(contract, "symbol", None)
            rt = self._runtimes.get(underlying_symbol)
            if rt is None or not rt.enabled:
                continue  # not a symbol this runner manages

            info = {
                "conId": getattr(contract, "conId", None),
                "localSymbol": getattr(contract, "localSymbol", None),
                "right": getattr(contract, "right", None),
                "strike": getattr(contract, "strike", None),
                "expiry": getattr(contract, "lastTradeDateOrContractMonth", None),
                "quantity": quantity,
            }
            rt.broker_position_blocked = True
            rt.broker_position_info = info

            if rt.orchestrator is not None and hasattr(rt.orchestrator, "_broker_position_blocked"):
                rt.orchestrator._broker_position_blocked = True
                rt.orchestrator._lifecycle = LifecycleState.EXISTING_BROKER_POSITION

            log.warning(
                f"[{underlying_symbol}] EXISTING BROKER POSITION — "
                f"new entries blocked contract={info['localSymbol']} "
                f"qty={info['quantity']}"
            )
            self._emit(EventType.ERROR, symbol=underlying_symbol,
                       data={"error": "EXISTING_BROKER_POSITION", **info})

    def _compute_context_levels(self) -> None:
        """Fetch previous RTH session and premarket, compute PDH/PDL + PMH/PML."""
        from datetime import datetime as dt_cls
        now = dt_cls.now(self._tz)
        today = now.strftime("%Y-%m-%d")

        # Determine if premarket window is complete
        # Market open: parse session_open (e.g. "09:30" ET)
        open_h, open_m = int(self._session_open[:2]), int(self._session_open[3:])
        now_minutes = now.hour * 60 + now.minute
        pm_final = now_minutes >= open_h * 60 + open_m

        enabled_symbols = [
            (sym, rt) for sym, rt in self._runtimes.items()
            if rt.enabled and rt.underlying_contract is not None
        ]
        for idx, (sym, rt) in enumerate(enabled_symbols):
            # Pacing: IBKR limits ~6 similar historical-data requests
            # per 2 seconds. Each symbol makes 2 requests (prev session +
            # premarket), so stagger between symbols.
            if idx > 0:
                self._ib.sleep(0.5)

            try:
                # PDH/PDL from previous RTH session
                sessions = fetch_previous_session_bars(
                    self._ib, rt.underlying_contract, self._tz,
                )
                # Retain the raw sessions (all_sessions format) on the
                # runtime — not just the PDH/PDL scalars derived below —
                # so a later PDH/PDL detector task can reuse this data
                # without a second historical fetch.
                rt.previous_sessions = sessions

                # Propagate to the signal detector (LiveSignalDetector or
                # DualSignalDetector) already built in _setup_symbol().
                # level_source stays ORB for now — this only makes the
                # data reachable at build_level() time for a future task.
                if rt.signal_detector is not None:
                    rt.signal_detector.set_previous_sessions(sessions)

                # PMH/PML from today's premarket
                pm_bars = fetch_premarket_bars(
                    self._ib, rt.underlying_contract, self._tz, today,
                )

                ctx = compute_live_context_levels(
                    sym, today, sessions,
                    premarket_bars=pm_bars,
                    premarket_final=pm_final,
                )
                rt.context_levels = ctx

                parts = []
                if ctx.pdh is not None:
                    parts.append(f"PDH={ctx.pdh:.2f} PDL={ctx.pdl:.2f} (from {ctx.prev_date})")
                if ctx.pmh is not None:
                    pm_label = "FINAL" if pm_final else "BUILDING"
                    parts.append(f"PMH={ctx.pmh:.2f} PML={ctx.pml:.2f} ({ctx.pm_bar_count} bars, {pm_label})")
                else:
                    parts.append("PMH/PML=unavailable")

                if parts:
                    log.info(f"CONTEXT {sym}: {' | '.join(parts)}")

                ctx_data = ctx.to_dict()
                self._emit(EventType.SYMBOL_ENABLED, symbol=sym, data=ctx_data)

            except Exception as e:
                log.warning(f"CONTEXT {sym}: error: {e}")
                rt.context_levels = ContextLevels(symbol=sym, status=f"ERROR: {e}")

    def _subscribe_all(self) -> None:
        import time as _time
        enabled_symbols = [
            (sym, rt) for sym, rt in self._runtimes.items() if rt.enabled
        ]
        total = len(enabled_symbols)
        for idx, (sym, rt) in enumerate(enabled_symbols):
            # Stagger subscriptions to avoid IBKR pacing violations.
            # IBKR limits ~6 similar historical-data requests per 2 seconds;
            # with 9 symbols plus context-level fetches, back-to-back
            # requests cause silent drops (SPY dead feed root cause).
            if idx > 0:
                delay = 0.6  # seconds between subscriptions
                log.debug(f"Subscription pacing: waiting {delay}s before {sym}")
                self._ib.sleep(delay)

            try:
                bars = self._ib.reqHistoricalData(
                    rt.underlying_contract,
                    endDateTime="",
                    durationStr="1 D",
                    barSizeSetting="1 min",
                    whatToShow="TRADES",
                    useRTH=True,
                    formatDate=2,
                    keepUpToDate=True,
                )
                rt.bars = bars
                rt.bars_object_id = id(bars)  # diagnostic: track object identity
                self._bootstrap_symbol(rt)
                # Create closure to bind rt — with error protection
                def make_callback(runtime):
                    def cb(bars_list, has_new_bar):
                        try:
                            self._on_bar_update(runtime, bars_list, has_new_bar)
                        except Exception as e:
                            log.error(f"[{runtime.symbol}] Callback exception: {e}",
                                      exc_info=True)
                    return cb
                bars.updateEvent += make_callback(rt)
                rt.listener_count = len(bars.updateEvent)
                rt.subscription_start_time = _time.monotonic()
                log.info(
                    f"STREAM ACTIVE: {sym} ({idx + 1}/{total}, "
                    f"{len(bars)} bars, obj={id(bars)}, "
                    f"listeners={rt.listener_count})"
                )
            except Exception as e:
                rt.enabled = False
                rt.error = str(e)
                log.error(f"SUBSCRIPTION FAILED {sym}: {e}")

    def _bootstrap_symbol(self, rt: SymbolRuntime) -> None:
        """Feed historical bars into session builder for context only.

        Bootstrap bars are from previous sessions (loaded via reqHistoricalData
        before market open). They must NOT trigger signal evaluation — only
        populate the session builder so the ORB can be computed correctly
        when today's live bars arrive.

        SAFETY: marks ALL bars (including the last one) in processed_times
        to prevent _on_bar_update or _poll_bars_fallback from processing
        historical bars as if they were live.
        """
        if not rt.bars:
            return
        # Process ALL bars for context — the last bar is NOT a live bar
        # pre-market, it's just the last historical bar from yesterday.
        completed = list(rt.bars)
        fed = 0
        for bar in completed:
            candle = ibkr_bar_to_candle(bar, self._tz)
            if not is_rth_bar(candle["time_ms"], self._tz,
                              self._session_open, self._session_close):
                continue
            if candle["time_ms"] in rt.processed_times:
                continue
            rt.processed_times.add(candle["time_ms"])
            if rt.session_builder:
                rt.session_builder.add_bar(candle)
            fed += 1
        log.info(f"Bootstrap {rt.symbol}: {fed} bars (context only, no signals)")

    def _on_bar_update(self, rt: SymbolRuntime, bars, has_new_bar) -> None:
        """Bar callback — MUST NOT call any IBKR sync methods.

        Only pure computation is allowed here:
        - candle conversion
        - dedup
        - session builder update
        - signal evaluation (single, via orchestrator.on_bar)
        - telemetry update
        - enqueue execution work if signal detected

        IBKR sync calls (qualifyContracts, reqSecDefOptParams, reqMktData,
        placeOrder) are deferred to _process_execution_queue in the main loop.
        """
        try:
            if not has_new_bar:
                return
            if len(bars) < 2:
                return

            completed_bar = bars[-2]
            candle = ibkr_bar_to_candle(completed_bar, self._tz)

            # Track feed health — any new bar proves feed is alive
            rt.last_bar_time_ms = candle["time_ms"]
            rt.feed_status = "LIVE"

            if not is_rth_bar(candle["time_ms"], self._tz,
                              self._session_open, self._session_close):
                return
            if candle["time_ms"] in rt.processed_times:
                return

            rt.processed_times.add(candle["time_ms"])
            rt.processed_bar_count += 1

            # ── LIVE BOUNDARY CHECK ──────────────────────────────────
            # A bar from a previous session must NEVER trigger execution.
            # Feed it to session builder for context, then return.
            if not self._is_live_bar(candle, rt):
                if rt.session_builder:
                    rt.session_builder.add_bar(candle)
                return

            # ONE signal evaluation via orchestrator.on_bar()
            # This calls session_builder.add_bar + signal_detector.evaluate
            # but does NOT call any IBKR sync methods.
            result = rt.orchestrator.on_bar(candle)

            # Extract pipeline stage info from the signal detector's last result
            # (already evaluated inside orchestrator.on_bar, no second call)
            stage_info = ""
            if rt.signal_detector:
                last = rt.signal_detector.last_result
                if last and last.pipeline_stage:
                    rt.pipeline_stage = last.pipeline_stage
                    ctx = last.stage_context or {}
                    rt.last_stage_context = ctx
                    if ctx.get("orb_high") is not None:
                        rt.orb_high = ctx["orb_high"]
                        rt.orb_low = ctx["orb_low"]
                    stage_info = _format_stage(last.pipeline_stage, last.failed_stage, ctx)
                if last and last.status == SignalStatus.SIGNAL:
                    rt.pipeline_stage = "SIGNAL"

            # If orchestrator detected a signal, enqueue for deferred execution
            if rt.orchestrator.has_pending_signal:
                item = ExecutionWorkItem(
                    symbol=rt.symbol,
                    work_type=WorkItemType.SIGNAL_EXECUTION,
                    signal_result=None,  # signal stored in orchestrator
                    bar_time_ms=candle["time_ms"],
                )
                self._execution_queue.enqueue(item)

            time_str = datetime.fromtimestamp(
                candle["time_ms"] / 1000, tz=self._tz
            ).strftime("%H:%M")

            if self._execution_mode == ExecutionMode.OBSERVE_ONLY:
                state = rt.orchestrator.lifecycle
                log.info(f"[{rt.symbol}] {time_str} C={candle['close']:.2f} → {state}{stage_info}")
            else:
                log.info(
                    f"[{rt.symbol}] {time_str} C={candle['close']:.2f} → "
                    f"{result.lifecycle if result else '?'}{stage_info}"
                )

            # Record decision trace for PWA
            self._record_trace(rt, candle, time_str)
        except Exception as e:
            log.error(f"[{rt.symbol}] Bar callback error: {e}", exc_info=True)

    # ── Feed health ──────────────────────────────────────────────────────

    STALE_THRESHOLD_SECS = 180  # 3 minutes without a completed bar = stale
    RESUBSCRIBE_COOLDOWN_SECS = 300  # 5 minutes between resubscribe attempts

    def _check_feed_health(self, now_et) -> None:
        """Check for stale feeds and attempt resubscription."""
        import time as _time
        now_ms = int(now_et.timestamp() * 1000)
        mono_now = _time.monotonic()

        for sym, rt in self._runtimes.items():
            if not rt.enabled or rt.bars is None:
                continue

            if rt.feed_status == "INITIALIZING":
                # First bar received → transition to LIVE
                if rt.last_bar_time_ms > 0:
                    rt.feed_status = "LIVE"
                    continue

                # No first bar yet — check if subscription has timed out
                if rt.subscription_start_time != 0.0:
                    elapsed = mono_now - rt.subscription_start_time
                    if elapsed > self.STALE_THRESHOLD_SECS:
                        rt.feed_status = "STALE"
                        log.warning(
                            f"[{sym}] INITIALIZING TIMEOUT — "
                            f"no bar after {elapsed:.0f}s"
                        )
                        self._emit(EventType.ERROR, symbol=sym,
                                   data={"error": "initializing_timeout",
                                         "elapsed_secs": round(elapsed)})
                        # Attempt resubscribe with cooldown — but NOT if
                        # BarDataList is actively growing (data arriving,
                        # updateEvent broken). Compare current bar count
                        # to last known count to detect real growth.
                        since_last = mono_now - rt.last_resubscribe_time
                        current_count = len(rt.bars) if rt.bars is not None else 0
                        bars_growing = current_count > rt.last_known_bars_count
                        rt.last_known_bars_count = current_count
                        if bars_growing:
                            log.debug(
                                f"[{sym}] INIT but bars growing "
                                f"({current_count}) — skipping resubscribe, "
                                f"poll fallback active"
                            )
                        elif since_last >= self.RESUBSCRIBE_COOLDOWN_SECS:
                            self._resubscribe_symbol(rt, mono_now)
                continue

            # Check staleness for LIVE/STALE feeds
            if rt.last_bar_time_ms > 0:
                age_secs = (now_ms - rt.last_bar_time_ms) / 1000
            else:
                age_secs = self.STALE_THRESHOLD_SECS + 1

            if age_secs > self.STALE_THRESHOLD_SECS:
                if rt.feed_status != "STALE":
                    rt.feed_status = "STALE"
                    log.warning(f"[{sym}] FEED STALE — no bar for {age_secs:.0f}s")
                    self._emit(EventType.ERROR, symbol=sym,
                               data={"error": "feed_stale",
                                     "last_bar_age_secs": round(age_secs)})

                # Attempt resubscribe with cooldown — but NOT if
                # BarDataList is actively growing (poll fallback handles it)
                since_last = mono_now - rt.last_resubscribe_time
                current_count = len(rt.bars) if rt.bars is not None else 0
                bars_growing = current_count > rt.last_known_bars_count
                rt.last_known_bars_count = current_count
                if bars_growing:
                    log.debug(
                        f"[{sym}] STALE but bars growing "
                        f"({current_count}) — skipping resubscribe"
                    )
                elif since_last >= self.RESUBSCRIBE_COOLDOWN_SECS:
                    self._resubscribe_symbol(rt, mono_now)
            else:
                if rt.feed_status == "STALE":
                    rt.feed_status = "LIVE"
                    log.info(f"[{sym}] FEED LIVE — recovered")

    def _resubscribe_symbol(self, rt: SymbolRuntime, mono_now: float) -> None:
        """Cancel and re-create the bar subscription for one symbol.

        Guarantees:
        - Old BarDataList listeners removed before cancel
        - Exactly 1 listener on new BarDataList
        - Old BarDataList not retained
        - Feed state reset to INITIALIZING
        - Bootstrap bars fed for context (no signals)
        """
        import time as _time
        sym = rt.symbol
        rt.last_resubscribe_time = mono_now
        rt.resubscribe_count += 1

        old_bars = rt.bars
        old_id = id(old_bars) if old_bars is not None else None
        old_listener_count = len(old_bars.updateEvent) if old_bars is not None else 0

        log.info(
            f"[{sym}] RESUBSCRIBING (attempt #{rt.resubscribe_count}, "
            f"old_obj={old_id}, old_listeners={old_listener_count})"
        )

        # Remove ALL listeners from old BarDataList BEFORE cancelling.
        # This prevents any stale callbacks from firing during the
        # cancel/re-create window.
        if old_bars is not None:
            try:
                old_bars.updateEvent.clear()
                log.debug(f"[{sym}] Cleared {old_listener_count} old listeners")
            except Exception as e:
                log.warning(f"[{sym}] Failed to clear old listeners: {e}")

        # Cancel old subscription
        try:
            if old_bars is not None:
                self._ib.cancelHistoricalData(old_bars)
        except Exception as e:
            log.warning(f"[{sym}] Cancel old subscription: {e}")

        # Brief pause to let IBKR process the cancel before new request
        self._ib.sleep(0.5)

        # Create new subscription
        try:
            bars = self._ib.reqHistoricalData(
                rt.underlying_contract,
                endDateTime="",
                durationStr="1 D",
                barSizeSetting="1 min",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=2,
                keepUpToDate=True,
            )
            rt.bars = bars
            rt.bars_object_id = id(bars)
            rt.subscription_start_time = _time.monotonic()
            rt.feed_status = "INITIALIZING"  # wait for first live bar

            # Bootstrap new bars (dedup via processed_times)
            self._bootstrap_symbol(rt)

            # Register exactly ONE callback on the NEW BarDataList
            def make_callback(runtime):
                def cb(bars_list, has_new_bar):
                    try:
                        self._on_bar_update(runtime, bars_list, has_new_bar)
                    except Exception as e:
                        log.error(f"[{runtime.symbol}] Callback exception: {e}",
                                  exc_info=True)
                return cb
            bars.updateEvent += make_callback(rt)
            rt.listener_count = len(bars.updateEvent)

            log.info(
                f"[{sym}] RESUBSCRIBED (new_obj={id(bars)}, "
                f"{len(bars)} bars, listeners={rt.listener_count})"
            )
        except Exception as e:
            log.error(f"[{sym}] RESUBSCRIBE FAILED: {e}")
            rt.feed_status = "STALE"

    # ── Live boundary ─────────────────────────────────────────────────

    def _is_live_bar(self, candle: dict, rt: SymbolRuntime) -> bool:
        """Check if a candle belongs to TODAY's RTH session.

        A bar from a previous session date must NEVER trigger execution.
        Only bars whose date matches the current trading date are live.

        This is the primary safety gate against historical bootstrap
        bars reaching the execution queue.
        """
        bar_dt = datetime.fromtimestamp(
            candle["time_ms"] / 1000, tz=self._tz
        )
        today = datetime.now(self._tz).date()
        return bar_dt.date() == today

    def _record_trace(self, rt: SymbolRuntime, candle: dict, time_str: str) -> None:
        """Record decision trace for one completed bar."""
        try:
            sd = rt.signal_detector
            if sd is None or sd.last_result is None:
                return
            result = sd.last_result
            trace = build_candle_trace(
                candle=candle,
                signal_result=result,
                orb_high=rt.orb_high,
                orb_low=rt.orb_low,
                symbol=rt.symbol,
                time_str=time_str,
                rejection_data=result.rejection_detail,
            )
            rt.decision_trace.append(trace_to_dict(trace))
            if len(rt.decision_trace) > rt.max_trace_entries:
                rt.decision_trace.pop(0)
        except Exception as e:
            log.debug(f"[{rt.symbol}] Trace error: {e}")

    # ── Bar polling fallback ───────────────────────────────────────────

    def _poll_bars_fallback(self) -> None:
        """Poll BarDataList for new completed bars that updateEvent missed.

        ib_insync's updateEvent sometimes fails to fire even when the
        BarDataList grows (observed on SPY with Error 162 at startup).
        This fallback checks each symbol's BarDataList directly and
        processes any completed bars that haven't been seen yet.

        Uses the same dedup (processed_times) and code path as
        _on_bar_update, so bars are never processed twice.
        Does NOT process the last bar (it's the live/incomplete bar).
        """
        for sym, rt in self._runtimes.items():
            if not rt.enabled or rt.bars is None:
                continue
            try:
                bars = rt.bars
                if len(bars) < 2:
                    continue

                # Check the completed bar (second-to-last)
                completed_bar = bars[-2]
                candle = ibkr_bar_to_candle(completed_bar, self._tz)

                # Quick check: already processed?
                if candle["time_ms"] in rt.processed_times:
                    continue

                # Not yet processed — this bar was missed by updateEvent.
                # Feed it through the normal path.
                if not is_rth_bar(candle["time_ms"], self._tz,
                                  self._session_open, self._session_close):
                    continue

                # Track feed health
                rt.last_bar_time_ms = candle["time_ms"]
                if rt.feed_status != "LIVE":
                    log.info(
                        f"[{sym}] POLL FALLBACK — first bar detected, "
                        f"transitioning to LIVE"
                    )
                rt.feed_status = "LIVE"

                rt.processed_times.add(candle["time_ms"])
                rt.processed_bar_count += 1

                # ── LIVE BOUNDARY CHECK ──────────────────────────────
                if not self._is_live_bar(candle, rt):
                    if rt.session_builder:
                        rt.session_builder.add_bar(candle)
                    continue

                # ONE signal evaluation via orchestrator
                result = rt.orchestrator.on_bar(candle)

                # Extract pipeline stage info
                stage_info = ""
                if rt.signal_detector:
                    last = rt.signal_detector.last_result
                    if last and last.pipeline_stage:
                        rt.pipeline_stage = last.pipeline_stage
                        ctx = last.stage_context or {}
                        rt.last_stage_context = ctx
                        if ctx.get("orb_high") is not None:
                            rt.orb_high = ctx["orb_high"]
                            rt.orb_low = ctx["orb_low"]
                        stage_info = _format_stage(
                            last.pipeline_stage, last.failed_stage, ctx
                        )
                    if last and last.status == SignalStatus.SIGNAL:
                        rt.pipeline_stage = "SIGNAL"

                # Enqueue if signal detected
                if rt.orchestrator.has_pending_signal:
                    item = ExecutionWorkItem(
                        symbol=rt.symbol,
                        work_type=WorkItemType.SIGNAL_EXECUTION,
                        signal_result=None,
                        bar_time_ms=candle["time_ms"],
                    )
                    self._execution_queue.enqueue(item)

                time_str = datetime.fromtimestamp(
                    candle["time_ms"] / 1000, tz=self._tz
                ).strftime("%H:%M")

                if self._execution_mode == ExecutionMode.OBSERVE_ONLY:
                    state = rt.orchestrator.lifecycle
                    log.info(
                        f"[{sym}] {time_str} C={candle['close']:.2f} → "
                        f"{state}{stage_info} (poll)"
                    )
                else:
                    log.info(
                        f"[{sym}] {time_str} C={candle['close']:.2f} → "
                        f"{result.lifecycle if result else '?'}{stage_info} "
                        f"(poll)"
                    )

                # Record decision trace for PWA
                self._record_trace(rt, candle, time_str)
            except Exception as e:
                log.error(f"[{sym}] Poll fallback error: {e}", exc_info=True)

    # ── Execution queue processing ─────────────────────────────────────

    def _process_execution_queue(self) -> None:
        """Drain and process pending execution work items.

        Called from the main loop, OUTSIDE any bar callback.
        Safe to make IBKR sync calls here.
        """
        items = self._execution_queue.drain()
        for item in items:
            rt = self._runtimes.get(item.symbol)
            if rt is None or not rt.enabled or rt.orchestrator is None:
                self._execution_queue.fail(item, "symbol not available")
                continue
            try:
                rt.orchestrator.execute_pending_signal()
                self._execution_queue.complete(item)
            except Exception as e:
                self._execution_queue.fail(item, str(e))
                log.error(
                    f"[{item.symbol}] Execution failed: {e}",
                    exc_info=True,
                )

    # ── Main loop ────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        self._running = True

        # Set live boundary: only signals with entry candle AFTER this
        # time can trigger execution.  This prevents mid-session restarts
        # from executing stale setups found in historical bars.
        import time as _time
        self._live_start_time_ms = int(_time.time() * 1000)

        # Propagate live boundary to all orchestrators
        for sym, rt in self._runtimes.items():
            if rt.orchestrator and hasattr(rt.orchestrator, '_live_boundary_ms'):
                rt.orchestrator._live_boundary_ms = self._live_start_time_ms

        log.info(
            f"Main loop [{self._execution_mode}] — "
            f"{self.enabled_count} symbols active — "
            f"live_boundary={self._live_start_time_ms}"
        )

        loop_count = 0
        while self._running:
            self._ib.sleep(1)
            loop_count += 1

            # Process deferred execution work (IBKR sync calls safe here)
            self._process_execution_queue()

            # Periodic heartbeat (every 60 iterations ≈ 1 minute)
            if loop_count % 60 == 0:
                bar_counts = {}
                for sym, rt in self._runtimes.items():
                    if rt.enabled and rt.bars is not None:
                        bar_counts[sym] = len(rt.bars)
                log.info(f"Heartbeat: loop={loop_count}, bars={bar_counts}")

            # Finalize premarket levels after market open (once)
            now_et = datetime.now(self._tz)
            open_h, open_m = int(self._session_open[:2]), int(self._session_open[3:])
            close_h, close_m = int(self._session_close[:2]), int(self._session_close[3:])
            now_minutes = now_et.hour * 60 + now_et.minute
            is_rth = (now_et.weekday() < 5
                      and open_h * 60 + open_m <= now_minutes < close_h * 60 + close_m)

            if now_minutes >= open_h * 60 + open_m:
                for sym, rt in self._runtimes.items():
                    if (rt.enabled and rt.context_levels
                            and not rt.context_levels.premarket_final):
                        from dataclasses import replace
                        rt.context_levels = replace(rt.context_levels, premarket_final=True)

            # Feed health: detect stale and resubscribe (RTH only)
            if is_rth and loop_count % 10 == 0:
                self._check_feed_health(now_et)

            # Poll BarDataList fallback — catches bars that arrive
            # without updateEvent firing (observed on SPY).
            # Uses the same dedup (processed_times) as _on_bar_update.
            if is_rth and loop_count % 5 == 0:
                self._poll_bars_fallback()

            all_done = True
            for sym, rt in self._runtimes.items():
                if not rt.enabled or rt.orchestrator is None:
                    continue

                if self._execution_mode == ExecutionMode.OBSERVE_ONLY:
                    if rt.orchestrator.lifecycle != ObserveLifecycle.DONE_FOR_DAY:
                        all_done = False
                else:
                    state = rt.orchestrator.lifecycle
                    if state == LifecycleState.ENTRY_SUBMITTED:
                        all_done = False
                        prev = state
                        status = rt.orchestrator.refresh_entry_status()
                        if status.lifecycle != prev:
                            log.info(f"[{sym}] Entry: {prev} → {status.lifecycle}")
                    elif state == LifecycleState.EXIT_SUBMITTED:
                        all_done = False
                        prev = state
                        status = rt.orchestrator.refresh_exit_status()
                        if status.lifecycle != prev:
                            log.info(f"[{sym}] Exit: {prev} → {status.lifecycle}")
                    elif state == LifecycleState.EXIT_FAILED:
                        all_done = False
                        prev = state
                        status = rt.orchestrator.refresh_exit_status()
                        if status.lifecycle != prev:
                            log.info(f"[{sym}] Exit recovery: {prev} → {status.lifecycle}")
                    elif state != LifecycleState.DONE_FOR_DAY:
                        all_done = False

            if all_done and self.enabled_count > 0:
                log.info("All symbols done for day — stopping")
                self._running = False
                break

            now_et = datetime.now(self._tz)
            close_h, close_m = int(self._session_close[:2]), int(self._session_close[3:])
            if now_et.hour * 60 + now_et.minute >= close_h * 60 + close_m:
                has_active = any(
                    rt.enabled and rt.orchestrator and
                    (rt.orchestrator.lifecycle if self._execution_mode == ExecutionMode.OBSERVE_ONLY
                     else rt.orchestrator.lifecycle) in (
                        LifecycleState.POSITION_OPEN,
                        LifecycleState.ENTRY_SUBMITTED,
                        LifecycleState.EXIT_SUBMITTED,
                    )
                    for rt in self._runtimes.values()
                )
                if not has_active:
                    log.info("Session close reached — stopping")
                    self._running = False
                    break
                else:
                    log.warning("Session close reached but active positions remain")


# ── CLI entry point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MaxBot v0.1 IBKR Paper OPTIONS runner"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol", help="Single underlying symbol (e.g. QQQ)")
    group.add_argument("--symbols", help="Comma-separated symbols (e.g. QQQ,SPY,NVDA)")
    parser.add_argument("--direction", default="BOTH", choices=["LONG", "SHORT", "BOTH"])
    parser.add_argument("--execution-mode", default="OBSERVE_ONLY",
                        choices=["OBSERVE_ONLY", "PAPER_EXECUTE"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=1)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--session-open", default="09:30")
    parser.add_argument("--session-close", default="16:00")
    parser.add_argument("--trade-limits", action="store_true", default=False,
                        help="Enable daily trade limits (disabled by default for test phase)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = parse_symbols(args.symbols)

    runner = MaxBotRunner(
        symbols=symbols,
        direction=args.direction,
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        tick_size=args.tick_size,
        market_timezone=args.timezone,
        session_open=args.session_open,
        session_close=args.session_close,
        execution_mode=args.execution_mode,
        trade_limits_enabled=args.trade_limits,
    )
    runner.run()


if __name__ == "__main__":
    main()
