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
        emit=None,
    ):
        self._symbol = underlying_symbol
        self._direction = direction
        self._tick_size = tick_size
        self._session_builder = session_builder
        self._signal_detector = signal_detector
        self._option_selector = option_selector
        self._exit_target_r = exit_target_r
        self._emit_fn = emit

        self._lifecycle = ObserveLifecycle.WAITING_FOR_SIGNAL
        self._exit_monitor: UnderlyingExitMonitor | None = None
        self._current_signal_event: ObservationEvent | None = None
        self._events: list[ObservationEvent] = []
        self._resolved_direction: str | None = None

        # Pending signal (set by _check_for_signal, consumed by execute_pending_signal)
        self._pending_signal = None
        self._pending_signal_bar: dict | None = None

        # Consumed setup keys — same-setup re-entry prevention
        self._consumed_setups: set[str] = set()
        # Consumed signal keys — exactly-once per signal
        self._consumed_signals: set[str] = set()

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

    @property
    def has_pending_signal(self) -> bool:
        """True if a signal was detected but not yet executed."""
        return self._pending_signal is not None

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
        """Pure signal detection — NO IBKR sync calls.

        If a signal is found, stores it in _pending_signal for
        later execution by execute_pending_signal().
        """
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

        result = self._signal_detector.evaluate(
            sess, consumed_setup_keys=self._consumed_setups
        )
        if result.status != SignalStatus.SIGNAL:
            return None

        # Reject same-setup re-entry
        if result.setup_key and result.setup_key in self._consumed_setups:
            return None

        # Reject same-signal re-emission (exactly-once)
        if result.signal_key and result.signal_key in self._consumed_signals:
            return None

        # Store pending signal — execution deferred outside callback
        self._pending_signal = result
        self._pending_signal_bar = bar
        return None

    def execute_pending_signal(self) -> ObservationEvent | None:
        """Execute IBKR sync work for a pending signal.

        MUST be called OUTSIDE the bar callback (from the main loop).

        Contains all IBKR sync calls:
        - qualifyContracts (via option_selector)
        - reqSecDefOptParams (via option_selector)
        - reqMktData (via option_selector)
        """
        result = self._pending_signal
        bar = self._pending_signal_bar
        if result is None or bar is None:
            return None
        self._pending_signal = None
        self._pending_signal_bar = None

        # Mark setup and signal as consumed
        if result.setup_key:
            self._consumed_setups.add(result.setup_key)
        if result.signal_key:
            self._consumed_signals.add(result.signal_key)

        # Use resolved direction from signal (supports BOTH mode)
        resolved_direction = result.direction

        # Build execution intent (pure computation)
        intent = build_option_execution_intent(
            result.trade_plan,
            self._symbol,
            resolved_direction,
            exit_target_r=self._exit_target_r,
            detection_result=result.detection_result,
        )

        # Select option contract (real IBKR chain query) — IBKR SYNC
        sess = self._session_builder.current_session()
        if sess is None:
            return None
        trading_date_yyyymmdd = sess["date"].replace("-", "")
        underlying_price = sess["candles"][-1]["close"]
        right = "C" if resolved_direction == "LONG" else "P"

        try:
            selection = self._option_selector.select(
                underlying_symbol=self._symbol,
                right=right,
                underlying_price=underlying_price,
                trading_date=trading_date_yyyymmdd,
                fetch_market_data=True,
            )
        except Exception as e:
            log.warning(f"[OBSERVE] {self._symbol} Option selection failed: {e}")
            selection = None

        # Build theoretical entry order (may fail if bid/ask unavailable)
        order_spec = None
        if selection:
            try:
                order_spec = build_option_entry_order(selection)
            except Exception as e:
                log.warning(f"[OBSERVE] {self._symbol} Order spec failed: {e}")
                order_spec = None

        triggers = intent.underlying_triggers
        entry_f = float(triggers.entry_price)
        stop_f = float(triggers.stop_price)
        target_f = float(triggers.target_price)

        # Record theoretical trade — even if option unavailable
        self._trades_used += 1

        event = ObservationEvent(
            event_type="SIGNAL",
            underlying_symbol=self._symbol,
            direction=resolved_direction,
            underlying_entry=entry_f,
            underlying_stop=stop_f,
            underlying_target=target_f,
            option_right=right if not selection else selection.right,
            expiration=selection.expiration if selection else "",
            strike=selection.strike if selection else 0.0,
            con_id=selection.con_id if selection else None,
            bid=order_spec.bid if order_spec else None,
            ask=order_spec.ask if order_spec else None,
            spread=order_spec.spread if order_spec else None,
            spread_pct=order_spec.spread_pct if order_spec else None,
            quantity=1,
            order_type="LMT",
            limit_price=order_spec.limit_price if order_spec else None,
            order_submitted=False,
            bar_time_ms=bar["time_ms"],
            theoretical_trades_used=self._trades_used,
            theoretical_wins=self._wins,
            theoretical_losses=self._losses,
        )
        self._events.append(event)
        self._current_signal_event = event

        # Emit to shared session log
        self._do_emit("SIGNAL", direction=resolved_direction, data={
            "underlying_entry": entry_f, "underlying_stop": stop_f,
            "underlying_target": target_f,
        })
        if selection:
            self._do_emit("OPTION_SELECTED", direction=resolved_direction, data={
                "right": selection.right, "expiration": selection.expiration,
                "strike": selection.strike, "con_id": selection.con_id,
                "exchange": selection.exchange, "multiplier": selection.multiplier,
                "bid": selection.bid, "ask": selection.ask, "spread": selection.spread,
            })
        if order_spec:
            self._do_emit("ENTRY_ORDER_BUILT", direction=resolved_direction, data={
                "order_type": "LMT", "quantity": 1, "limit_price": order_spec.limit_price,
            })
        self._do_emit("OBSERVE_ENTRY", direction=resolved_direction, data={
            "order_submitted": False,
            "limit_price": order_spec.limit_price if order_spec else None,
            "option_unavailable": selection is None,
        })

        # Start exit monitoring
        self._exit_monitor = UnderlyingExitMonitor(
            direction=resolved_direction,
            stop_price=stop_f,
            target_price=target_f,
            activation_time_ms=bar["time_ms"],
        )
        self._resolved_direction = resolved_direction
        self._lifecycle = ObserveLifecycle.TRACKING_EXIT

        if selection and order_spec:
            log.info(
                f"[OBSERVE] SIGNAL {self._symbol} {resolved_direction}\n"
                f"[OBSERVE] OPTION {self._symbol} {selection.expiration} "
                f"{selection.strike} {'CALL' if right == 'C' else 'PUT'}\n"
                f"[OBSERVE] BID {order_spec.bid} ASK {order_spec.ask} "
                f"SPREAD {order_spec.spread:.4f}\n"
                f"[OBSERVE] WOULD BUY 1 @ {order_spec.limit_price} LMT\n"
                f"[OBSERVE] ORDER NOT SUBMITTED"
            )
        else:
            log.info(
                f"[OBSERVE] SIGNAL {self._symbol} {resolved_direction} "
                f"(option {'unavailable' if not selection else 'order spec failed'})"
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
            direction=self._resolved_direction or self._direction,
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

        # Emit to shared session log
        observe_type = "OBSERVE_TARGET" if event_type == "TARGET_TRIGGERED" else "OBSERVE_STOP"
        self._do_emit(observe_type, direction=self._resolved_direction or self._direction,
                      data={"exit_reason": "TARGET" if "TARGET" in event_type else "STOP"})

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
        self._resolved_direction = None
        self._lifecycle = ObserveLifecycle.WAITING_FOR_SIGNAL

    def _do_emit(self, event_type, direction=None, data=None):
        if self._emit_fn:
            return self._emit_fn(event_type, symbol=self._symbol,
                                 direction=direction, data=data)
        return None
