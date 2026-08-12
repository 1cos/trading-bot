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
from trading_lab.live.signal_detector import LiveSignalDetector
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
                             LifecycleState.EXIT_FAILED):
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

        for sym, rt in self._runtimes.items():
            if not rt.enabled or rt.underlying_contract is None:
                continue
            try:
                # PDH/PDL from previous RTH session
                sessions = fetch_previous_session_bars(
                    self._ib, rt.underlying_contract, self._tz,
                )

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
        for sym, rt in self._runtimes.items():
            if not rt.enabled:
                continue
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
                log.info(f"STREAM ACTIVE: {sym} ({len(bars)} bars, "
                         f"updateEvent listeners: {len(bars.updateEvent)})")
            except Exception as e:
                rt.enabled = False
                rt.error = str(e)
                log.error(f"SUBSCRIPTION FAILED {sym}: {e}")

    def _bootstrap_symbol(self, rt: SymbolRuntime) -> None:
        if not rt.bars:
            return
        completed = list(rt.bars)[:-1] if len(rt.bars) > 1 else []
        fed = 0
        for bar in completed:
            candle = ibkr_bar_to_candle(bar, self._tz)
            if not is_rth_bar(candle["time_ms"], self._tz,
                              self._session_open, self._session_close):
                continue
            if candle["time_ms"] in rt.processed_times:
                continue
            rt.processed_times.add(candle["time_ms"])
            rt.orchestrator.on_bar(candle)
            fed += 1
        log.info(f"Bootstrap {rt.symbol}: {fed} bars")

    def _on_bar_update(self, rt: SymbolRuntime, bars, has_new_bar) -> None:
        try:
            if not has_new_bar:
                return
            if len(bars) < 2:
                return

            completed_bar = bars[-2]
            candle = ibkr_bar_to_candle(completed_bar, self._tz)

            if not is_rth_bar(candle["time_ms"], self._tz,
                              self._session_open, self._session_close):
                return
            if candle["time_ms"] in rt.processed_times:
                return

            rt.processed_times.add(candle["time_ms"])
            result = rt.orchestrator.on_bar(candle)
            time_str = datetime.fromtimestamp(
                candle["time_ms"] / 1000, tz=self._tz
            ).strftime("%H:%M")

            if self._execution_mode == ExecutionMode.OBSERVE_ONLY:
                state = rt.orchestrator.lifecycle
                log.info(f"[{rt.symbol}] {time_str} C={candle['close']:.2f} → {state}")
            else:
                log.info(
                    f"[{rt.symbol}] {time_str} C={candle['close']:.2f} → "
                    f"{result.lifecycle if result else '?'}"
                )
        except Exception as e:
            log.error(f"[{rt.symbol}] Bar callback error: {e}", exc_info=True)

    # ── Main loop ────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        self._running = True
        log.info(
            f"Main loop [{self._execution_mode}] — "
            f"{self.enabled_count} symbols active"
        )

        loop_count = 0
        while self._running:
            self._ib.sleep(1)
            loop_count += 1

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
            now_minutes = now_et.hour * 60 + now_et.minute
            if now_minutes >= open_h * 60 + open_m:
                for sym, rt in self._runtimes.items():
                    if (rt.enabled and rt.context_levels
                            and not rt.context_levels.premarket_final):
                        # Replace with finalized version
                        ctx = rt.context_levels
                        from dataclasses import replace
                        rt.context_levels = replace(ctx, premarket_final=True)

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
