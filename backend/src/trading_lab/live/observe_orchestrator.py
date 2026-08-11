"""Observe-only orchestrator — runs full pipeline without submitting orders.

MaxBot v0.1 OBSERVE_ONLY mode:
    - Connects to IBKR and streams real underlying 1m data
    - Detects real MaxBot signals
    - Selects real option contracts (chain, expiration, strike)
    - Builds exact theoretical BUY LIMIT order
    - NEVER calls placeOrder
    - Tracks theoretical underlying STOP/TARGET outcome
    - Respects daily trade rules (max 2, first WIN = done)

All decision events are emitted as structured ObservationEvent objects
for comparison against Max's live trading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum, unique

from trading_lab.live.execution_intent import build_option_execution_intent
from trading_lab.live.option_order_builder import build_option_entry_order
from trading_lab.live.signal_detector import SignalStatus
from trading_lab.live.underlying_exit_monitor import ExitState, UnderlyingExitMonitor

log = logging.getLogger("maxbot")


# ── Execution mode ──────────────────────────────────────────────────────────

@unique
class ExecutionMode(StrEnum):
    OBSERVE_ONLY = "OBSERVE_ONLY"
    PAPER_EXECUTE = "PAPER_EXECUTE"


# ── Observe lifecycle ────────────────────────────────────────────────────────

@unique
class ObserveLifecycle(StrEnum):
    WAITING_FOR_SIGNAL = "WAITING_FOR_SIGNAL"
    TRACKING_EXIT = "TRACKING_EXIT"
    DONE_FOR_DAY = "DONE_FOR_DAY"


# ── Observation event ────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ObservationEvent:
    """Structured event for a theoretical trade decision.

    Attributes
    ----------
    event_type : str
        "SIGNAL", "TARGET_TRIGGERED", "STOP_TRIGGERED", "DONE_FOR_DAY"
    underlying_symbol : str
    direction : str
    underlying_entry : float or None
    underlying_stop : float or None
    underlying_target : float or None
    option_right : str or None
    expiration : str or None
    strike : float or None
    con_id : int or None
    bid : float or None
    ask : float or None
    spread : float or None
    spread_pct : float or None
    quantity : int or None
    order_type : str or None
    limit_price : float or None
    order_submitted : bool
        Always False in OBSERVE_ONLY.
    bar_time_ms : int or None
    theoretical_trades_used : int
    theoretical_wins : int
    theoretical_losses : int
    """

    event_type: str
    underlying_symbol: str
    direction: str
    underlying_entry: float | None = None
    underlying_stop: float | None = None
    underlying_target: float | None = None
    option_right: str | None = None
    expiration: str | None = None
    strike: float | None = None
    con_id: int | None = None
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None
    spread_pct: float | None = None
    quantity: int | None = None
    order_type: str | None = None
    limit_price: float | None = None
    order_submitted: bool = False
    bar_time_ms: int | None = None
    theoretical_trades_used: int = 0
    theoretical_wins: int = 0
    theoretical_losses: int = 0


# ── Observe orchestrator ────────────────────────────────────────────────────


class ObserveOrchestrator:
    """Observe-only trade lifecycle — no broker orders.

    Parameters
    ----------
    underlying_symbol : str
    direction : str
    tick_size : float
    session_builder : LiveSessionBuilder
    signal_detector : LiveSignalDetector
    option_selector : OptionContractSelector
    exit_target_r : int
    max_trades : int
    """

    def __init__(
        self,
        underlying_symbol: str,
        direction: str,
        tick_size: float,
        session_builder,
        signal_detector,
        option_selector,
        *,
        exit_target_r: int = 2,
        max_trades: int = 2,
    ):
        self._symbol = underlying_symbol
        self._direction = direction
        self._tick_size = tick_size
        self._session_builder = session_builder
        self._signal_detector = signal_detector
        self._option_selector = option_selector
        self._exit_target_r = exit_target_r

        self._lifecycle = ObserveLifecycle.WAITING_FOR_SIGNAL
        self._exit_monitor: UnderlyingExitMonitor | None = None
        self._current_signal_event: ObservationEvent | None = None
        self._events: list[ObservationEvent] = []

        # Theoretical daily counters
        self._max_trades = max_trades
        self._trades_used = 0
        self._wins = 0
        self._losses = 0
        self._day_finished = False
        self._trading_date: str | None = None

    @property
    def lifecycle(self) -> ObserveLifecycle:
        return self._lifecycle

    @property
    def events(self) -> list[ObservationEvent]:
        return list(self._events)

    @property
    def trades_used(self) -> int:
        return self._trades_used

    @property
    def wins(self) -> int:
        return self._wins

    @property
    def losses(self) -> int:
        return self._losses

    @property
    def day_finished(self) -> bool:
        return self._day_finished

    def on_bar(self, bar: dict) -> ObservationEvent | None:
        """Process a completed underlying bar.

        Returns an ObservationEvent if a significant event occurred,
        or None for routine HOLD/WAITING bars.
        """
        self._session_builder.add_bar(bar)

        # Date rollover
        date = self._session_builder.current_date
        if date and date != self._trading_date:
            self._reset_day(date)

        if self._lifecycle == ObserveLifecycle.WAITING_FOR_SIGNAL:
            return self._check_for_signal(bar)

        elif self._lifecycle == ObserveLifecycle.TRACKING_EXIT:
            return self._check_exit(bar)

        return None

    # ── Signal detection ─────────────────────────────────────────────────

    def _check_for_signal(self, bar: dict) -> ObservationEvent | None:
        if self._day_finished:
            self._lifecycle = ObserveLifecycle.DONE_FOR_DAY
            return None

        if self._trades_used >= self._max_trades:
            self._day_finished = True
            self._lifecycle = ObserveLifecycle.DONE_FOR_DAY
            return None

        sess = self._session_builder.current_session()
        if sess is None:
            return None

        result = self._signal_detector.evaluate(sess)
        if result.status != SignalStatus.SIGNAL:
            return None

        # Build execution intent
        intent = build_option_execution_intent(
            result.trade_plan,
            self._symbol,
            self._direction,
            exit_target_r=self._exit_target_r,
            detection_result=result.detection_result,
        )

        # Select option contract (real IBKR chain query)
        trading_date_yyyymmdd = sess["date"].replace("-", "")
        underlying_price = sess["candles"][-1]["close"]
        right = "C" if self._direction == "LONG" else "P"

        try:
            selection = self._option_selector.select(
                underlying_symbol=self._symbol,
                right=right,
                underlying_price=underlying_price,
                trading_date=trading_date_yyyymmdd,
                fetch_market_data=True,
            )
        except Exception as e:
            log.warning(f"[OBSERVE] Option selection failed: {e}")
            return None

        # Build theoretical entry order
        try:
            order_spec = build_option_entry_order(selection)
        except Exception as e:
            log.warning(f"[OBSERVE] Order spec failed: {e}")
            return None

        triggers = intent.underlying_triggers
        entry_f = float(triggers.entry_price)
        stop_f = float(triggers.stop_price)
        target_f = float(triggers.target_price)

        # Record theoretical trade
        self._trades_used += 1

        event = ObservationEvent(
            event_type="SIGNAL",
            underlying_symbol=self._symbol,
            direction=self._direction,
            underlying_entry=entry_f,
            underlying_stop=stop_f,
            underlying_target=target_f,
            option_right=selection.right,
            expiration=selection.expiration,
            strike=selection.strike,
            con_id=selection.con_id,
            bid=order_spec.bid,
            ask=order_spec.ask,
            spread=order_spec.spread,
            spread_pct=order_spec.spread_pct,
            quantity=1,
            order_type="LMT",
            limit_price=order_spec.limit_price,
            order_submitted=False,
            bar_time_ms=bar["time_ms"],
            theoretical_trades_used=self._trades_used,
            theoretical_wins=self._wins,
            theoretical_losses=self._losses,
        )
        self._events.append(event)
        self._current_signal_event = event

        # Start exit monitoring
        self._exit_monitor = UnderlyingExitMonitor(
            direction=self._direction,
            stop_price=stop_f,
            target_price=target_f,
            activation_time_ms=bar["time_ms"],
        )
        self._lifecycle = ObserveLifecycle.TRACKING_EXIT

        log.info(
            f"[OBSERVE] SIGNAL {self._symbol} {self._direction}\n"
            f"[OBSERVE] OPTION {self._symbol} {selection.expiration} "
            f"{selection.strike} {'CALL' if right == 'C' else 'PUT'}\n"
            f"[OBSERVE] BID {order_spec.bid} ASK {order_spec.ask} "
            f"SPREAD {order_spec.spread:.4f}\n"
            f"[OBSERVE] WOULD BUY 1 @ {order_spec.limit_price} LMT\n"
            f"[OBSERVE] ORDER NOT SUBMITTED"
        )

        return event

    # ── Exit monitoring ──────────────────────────────────────────────────

    def _check_exit(self, bar: dict) -> ObservationEvent | None:
        if self._exit_monitor is None:
            return None

        result = self._exit_monitor.evaluate_bar(bar)

        if result.state == ExitState.STOP_TRIGGERED:
            return self._record_exit("STOP_TRIGGERED", bar)
        elif result.state == ExitState.TARGET_TRIGGERED:
            return self._record_exit("TARGET_TRIGGERED", bar)

        return None

    def _record_exit(self, event_type: str, bar: dict) -> ObservationEvent:
        if event_type == "TARGET_TRIGGERED":
            self._wins += 1
            self._day_finished = True
        elif event_type == "STOP_TRIGGERED":
            self._losses += 1
            if self._trades_used >= self._max_trades:
                self._day_finished = True

        sig = self._current_signal_event

        event = ObservationEvent(
            event_type=event_type,
            underlying_symbol=self._symbol,
            direction=self._direction,
            underlying_entry=sig.underlying_entry if sig else None,
            underlying_stop=sig.underlying_stop if sig else None,
            underlying_target=sig.underlying_target if sig else None,
            option_right=sig.option_right if sig else None,
            expiration=sig.expiration if sig else None,
            strike=sig.strike if sig else None,
            con_id=sig.con_id if sig else None,
            order_submitted=False,
            bar_time_ms=bar["time_ms"],
            theoretical_trades_used=self._trades_used,
            theoretical_wins=self._wins,
            theoretical_losses=self._losses,
        )
        self._events.append(event)
        self._exit_monitor = None
        self._current_signal_event = None

        log.info(f"[OBSERVE] UNDERLYING {event_type}")

        if self._day_finished:
            self._lifecycle = ObserveLifecycle.DONE_FOR_DAY
            log.info(
                f"[OBSERVE] DONE FOR DAY — trades={self._trades_used} "
                f"W={self._wins} L={self._losses}"
            )
        else:
            self._lifecycle = ObserveLifecycle.WAITING_FOR_SIGNAL

        return event

    def _reset_day(self, date: str) -> None:
        self._trading_date = date
        self._trades_used = 0
        self._wins = 0
        self._losses = 0
        self._day_finished = False
        self._exit_monitor = None
        self._current_signal_event = None
        self._lifecycle = ObserveLifecycle.WAITING_FOR_SIGNAL
