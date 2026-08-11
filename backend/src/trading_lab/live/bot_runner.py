"""MaxBot v0.1 IBKR Paper live runner — single-symbol OPTIONS bot.

Connects to IBKR Paper TWS/Gateway, subscribes to underlying 1m bars,
feeds completed bars into MaxBotTradeOrchestrator, and refreshes
pending broker state.

PAPER ONLY — the runner verifies paper-account status before allowing
any order-capable lifecycle.

Usage:
    python -m trading_lab.live.bot_runner \\
        --symbol QQQ --direction LONG \\
        --host 127.0.0.1 --port 7497 --client-id 1

Does NOT:
    - support live-money accounts
    - support multiple symbols concurrently
    - implement automatic reconnect
    - implement persistent state recovery
    - implement overnight position management
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ib_insync import IB, Stock, util

from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.option_selector import OptionContractSelector
from trading_lab.live.ibkr_option_executor import IBKROptionExecutor
from trading_lab.live.option_exit_executor import OptionExitExecutor
from trading_lab.live.trade_orchestrator import (
    MaxBotTradeOrchestrator,
    LifecycleState,
)

log = logging.getLogger("maxbot")


# ── Paper-account verification ───────────────────────────────────────────────

def verify_paper_account(ib: IB) -> str:
    """Verify the connected session is a PAPER account.

    IBKR paper accounts have IDs starting with 'D' (e.g. 'DU1234567').

    Returns the paper account ID.

    Raises RuntimeError if the account cannot be verified as paper.
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
    """IBKR Paper live runner for MaxBot v0.1.

    Parameters
    ----------
    symbol : str
        Underlying symbol (e.g. "QQQ").
    direction : str
        "LONG" or "SHORT".
    host : str
        IBKR TWS/Gateway host.
    port : int
        IBKR TWS/Gateway port.
    client_id : int
        IBKR API client ID.
    tick_size : float
        Underlying tick size (default 0.01 for equities).
    market_timezone : str
        IANA timezone (default "America/New_York").
    session_open : str
        Session open HH:MM (default "09:30").
    session_close : str
        Session close HH:MM (default "16:00").
    """

    def __init__(
        self,
        symbol: str,
        direction: str,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        tick_size: float = 0.01,
        market_timezone: str = "America/New_York",
        session_open: str = "09:30",
        session_close: str = "16:00",
    ):
        self._symbol = symbol
        self._direction = direction
        self._host = host
        self._port = port
        self._client_id = client_id
        self._tick_size = tick_size
        self._tz_str = market_timezone
        self._tz = ZoneInfo(market_timezone)
        self._session_open = session_open
        self._session_close = session_close

        self._ib: IB | None = None
        self._orchestrator: MaxBotTradeOrchestrator | None = None
        self._bars = None  # BarDataList from reqHistoricalData
        self._processed_times: set[int] = set()
        self._running = False
        self._paper_account: str | None = None

    def run(self) -> None:
        """Connect to IBKR Paper and run the trading session."""
        self._connect()
        try:
            self._verify_paper()
            self._setup_orchestrator()
            self._qualify_underlying()
            self._subscribe_bars()
            self._run_loop()
        except KeyboardInterrupt:
            log.info("Keyboard interrupt — shutting down")
        except Exception as e:
            log.error(f"Runner error: {e}")
            raise
        finally:
            self._shutdown()

    # ── Connection ───────────────────────────────────────────────────────

    def _connect(self) -> None:
        self._ib = IB()
        log.info(f"Connecting to {self._host}:{self._port} (clientId={self._client_id})")
        self._ib.connect(self._host, self._port, clientId=self._client_id)
        log.info("CONNECTED")

    def _verify_paper(self) -> None:
        self._paper_account = verify_paper_account(self._ib)
        log.info(f"PAPER VERIFIED — account: {self._paper_account}")

    def _shutdown(self) -> None:
        if self._orchestrator:
            state = self._orchestrator.lifecycle
            if state in (LifecycleState.POSITION_OPEN,
                         LifecycleState.ENTRY_SUBMITTED,
                         LifecycleState.EXIT_SUBMITTED,
                         LifecycleState.EXIT_FAILED):
                log.warning(
                    f"UNRESOLVED STATE at shutdown: {state}. "
                    f"Active position/order may remain open."
                )
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            log.info("Disconnected from IBKR")

    # ── Orchestrator setup ───────────────────────────────────────────────

    def _setup_orchestrator(self) -> None:
        sb = LiveSessionBuilder(self._symbol, self._tz_str)
        sd = LiveSignalDetector(
            symbol=self._symbol,
            direction=self._direction,
            tick_size=self._tick_size,
            market_timezone=self._tz_str,
            session_open=self._session_open,
        )
        tm = DailyTradeManager()
        os_ = OptionContractSelector(self._ib)
        ee = IBKROptionExecutor(self._ib)
        xe = OptionExitExecutor(self._ib)

        self._orchestrator = MaxBotTradeOrchestrator(
            underlying_symbol=self._symbol,
            direction=self._direction,
            tick_size=self._tick_size,
            session_builder=sb,
            signal_detector=sd,
            trade_manager=tm,
            option_selector=os_,
            entry_executor=ee,
            exit_executor=xe,
        )
        log.info(f"Orchestrator ready: {self._symbol} {self._direction}")

    # ── Underlying ───────────────────────────────────────────────────────

    def _qualify_underlying(self) -> None:
        self._stock = Stock(self._symbol, "SMART", "USD")
        qualified = self._ib.qualifyContracts(self._stock)
        if not qualified:
            raise RuntimeError(f"Failed to qualify underlying: {self._symbol}")
        log.info(f"UNDERLYING QUALIFIED: {self._symbol} (conId={self._stock.conId})")

    # ── Bar subscription ─────────────────────────────────────────────────

    def _subscribe_bars(self) -> None:
        self._bars = self._ib.reqHistoricalData(
            self._stock,
            endDateTime="",
            durationStr="1 D",
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
            keepUpToDate=True,
        )
        # Bootstrap: feed existing completed bars
        self._bootstrap_bars()
        # Subscribe to updates
        self._bars.updateEvent += self._on_bar_update
        log.info("STREAM ACTIVE — receiving 1m bars")

    def _bootstrap_bars(self) -> None:
        """Feed already-elapsed bars from the current session."""
        if not self._bars:
            return
        # All bars except the last one (which may be forming)
        completed = list(self._bars)[:-1] if len(self._bars) > 1 else []
        fed = 0
        for bar in completed:
            candle = ibkr_bar_to_candle(bar, self._tz)
            if not is_rth_bar(candle["time_ms"], self._tz,
                              self._session_open, self._session_close):
                continue
            if candle["time_ms"] in self._processed_times:
                continue
            self._processed_times.add(candle["time_ms"])
            self._orchestrator.on_bar(candle)
            fed += 1
        log.info(f"Bootstrap: fed {fed} historical bars")

    def _on_bar_update(self, bars, has_new_bar) -> None:
        """Callback from ib_insync when bar data updates.

        has_new_bar is True when a new completed bar has been added
        to the BarDataList. When False, the last bar is still forming.
        """
        if not has_new_bar:
            return  # partial/forming bar — ignore

        if len(bars) < 2:
            return

        # The newly completed bar is the second-to-last
        # (last bar is the currently forming one)
        completed_bar = bars[-2]
        candle = ibkr_bar_to_candle(completed_bar, self._tz)

        if not is_rth_bar(candle["time_ms"], self._tz,
                          self._session_open, self._session_close):
            return

        if candle["time_ms"] in self._processed_times:
            return  # duplicate prevention

        self._processed_times.add(candle["time_ms"])

        status = self._orchestrator.on_bar(candle)
        log.info(
            f"Bar {datetime.fromtimestamp(candle['time_ms']/1000, tz=self._tz).strftime('%H:%M')} "
            f"O={candle['open']:.2f} H={candle['high']:.2f} "
            f"L={candle['low']:.2f} C={candle['close']:.2f} "
            f"→ {status.lifecycle}"
        )

        # Log significant state transitions
        if status.lifecycle == LifecycleState.ENTRY_SUBMITTED:
            log.info(f"ENTRY SUBMITTED — orderId={status.entry_order_id}")
        elif status.lifecycle == LifecycleState.DONE_FOR_DAY:
            log.info(
                f"DONE FOR DAY — trades={status.trades_used} "
                f"W={status.wins} L={status.losses}"
            )

    # ── Main loop ────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Run the ib_insync event loop, refreshing pending state."""
        self._running = True
        log.info("Entering main loop")

        while self._running:
            self._ib.sleep(1)  # process events for 1 second

            if self._orchestrator is None:
                break

            state = self._orchestrator.lifecycle

            # Refresh pending broker state
            if state == LifecycleState.ENTRY_SUBMITTED:
                prev = state
                status = self._orchestrator.refresh_entry_status()
                if status.lifecycle != prev:
                    log.info(f"Entry status: {prev} → {status.lifecycle}")
                    if status.lifecycle == LifecycleState.POSITION_OPEN:
                        log.info("ENTRY FILLED — position open")

            elif state == LifecycleState.EXIT_SUBMITTED:
                prev = state
                status = self._orchestrator.refresh_exit_status()
                if status.lifecycle != prev:
                    log.info(f"Exit status: {prev} → {status.lifecycle}")
                    if status.lifecycle == LifecycleState.DONE_FOR_DAY:
                        log.info(
                            f"EXIT FILLED — {status.exit_reason} → "
                            f"{'WIN' if status.wins > 0 else 'LOSS'}"
                        )
                    elif status.lifecycle == LifecycleState.WAITING_FOR_SIGNAL:
                        log.info("EXIT FILLED — LOSS, waiting for next signal")

            # Check for session end
            if state == LifecycleState.DONE_FOR_DAY:
                log.info("Day complete — stopping")
                self._running = False
                break

            # Check if past session close
            now_et = datetime.now(self._tz)
            close_h, close_m = int(self._session_close[:2]), int(self._session_close[3:])
            if now_et.hour * 60 + now_et.minute >= close_h * 60 + close_m:
                if state in (LifecycleState.WAITING_FOR_SIGNAL,
                             LifecycleState.DONE_FOR_DAY):
                    log.info("Session close reached — stopping")
                    self._running = False
                    break
                else:
                    log.warning(
                        f"Session close reached but state is {state} — "
                        f"waiting for resolution"
                    )


# ── CLI entry point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MaxBot v0.1 IBKR Paper OPTIONS runner"
    )
    parser.add_argument("--symbol", required=True, help="Underlying symbol (e.g. QQQ)")
    parser.add_argument("--direction", default="LONG", choices=["LONG", "SHORT"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=1)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--session-open", default="09:30")
    parser.add_argument("--session-close", default="16:00")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    runner = MaxBotRunner(
        symbol=args.symbol,
        direction=args.direction,
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        tick_size=args.tick_size,
        market_timezone=args.timezone,
        session_open=args.session_open,
        session_close=args.session_close,
    )
    runner.run()


if __name__ == "__main__":
    main()
