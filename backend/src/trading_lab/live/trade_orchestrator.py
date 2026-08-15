"""MaxBot v0.1 trade orchestrator — single-trade lifecycle coordinator.

Connects all existing live components into one coherent flow:

    underlying bar
    → session builder → signal detector → daily permission
    → option intent → contract selection → entry order spec
    → entry submission → entry fill → position open
    → underlying exit monitor → exit submission → exit fill
    → WIN / LOSS finalization

One orchestrator instance manages:
    - one underlying symbol
    - one trading date at a time
    - at most one active option position at a time

Does NOT:
    - create IBKR connections
    - implement scheduler/sleep loops
    - handle reconnect
    - manage multiple symbols
    - persist state
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum, unique

from trading_lab.live.entry_fill_monitor import FillState, check_fill, FillActivator
from trading_lab.live.execution_intent import build_option_execution_intent
from trading_lab.live.exit_fill_monitor import (
    ExitFillState,
    check_exit_fill,
    ExitResultActivator,
)
from trading_lab.live.option_order_builder import build_option_entry_order
from trading_lab.live.signal_detector import SignalStatus
from trading_lab.live.underlying_exit_monitor import ExitState, UnderlyingExitMonitor

log = logging.getLogger("maxbot")


# ── Lifecycle state ──────────────────────────────────────────────────────────

@unique
class LifecycleState(StrEnum):
    WAITING_FOR_SIGNAL = "WAITING_FOR_SIGNAL"
    ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
    POSITION_OPEN = "POSITION_OPEN"
    EXIT_SUBMITTED = "EXIT_SUBMITTED"
    DONE_FOR_DAY = "DONE_FOR_DAY"
    ENTRY_FAILED = "ENTRY_FAILED"
    EXIT_FAILED = "EXIT_FAILED"
    REQUIRES_ATTENTION = "REQUIRES_ATTENTION"  # exit retries exhausted


# ── Orchestrator status snapshot ─────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class OrchestratorStatus:
    """Snapshot of orchestrator state after an action."""

    lifecycle: LifecycleState
    underlying_symbol: str
    trading_date: str | None
    signal_status: str | None
    option_con_id: int | None
    entry_order_id: int | None
    exit_order_id: int | None
    exit_reason: str | None
    trades_used: int
    wins: int
    losses: int
    day_finished: bool


# ── Orchestrator ─────────────────────────────────────────────────────────────


class MaxBotTradeOrchestrator:
    """Coordinates one complete MaxBot v0.1 option trade lifecycle.

    Parameters
    ----------
    underlying_symbol : str
        e.g. "QQQ".
    direction : str
        "LONG" or "SHORT".
    tick_size : float
        Underlying tick size (e.g. 0.01).
    session_builder : LiveSessionBuilder
    signal_detector : LiveSignalDetector
    trade_manager : DailyTradeManager
    option_selector : OptionContractSelector
    entry_executor : IBKROptionExecutor
    exit_executor : OptionExitExecutor
    exit_target_r : int
        R-multiple target (default 2).
    """

    def __init__(
        self,
        underlying_symbol: str,
        direction: str,
        tick_size: float,
        session_builder,
        signal_detector,
        trade_manager,
        option_selector,
        entry_executor,
        exit_executor,
        *,
        exit_target_r: int = 2,
        emit=None,
    ):
        self._symbol = underlying_symbol
        self._direction = direction
        self._tick_size = tick_size
        self._session_builder = session_builder
        self._signal_detector = signal_detector
        self._trade_manager = trade_manager
        self._option_selector = option_selector
        self._entry_executor = entry_executor
        self._exit_executor = exit_executor
        self._exit_target_r = exit_target_r
        self._emit_fn = emit  # optional callback(event_type, symbol, **kw)

        self._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
        self._fill_activator = FillActivator(trade_manager)
        self._exit_activator = ExitResultActivator(trade_manager)

        # Active trade state
        self._entry_submission = None
        self._entry_con_id: int | None = None
        self._entry_order_id: int | None = None
        self._qualified_contract = None
        self._option_right: str = ""
        self._option_expiration: str = ""
        self._option_strike: float = 0.0
        self._exit_monitor: UnderlyingExitMonitor | None = None
        self._exit_submission = None
        self._exit_order_id: int | None = None
        self._exit_reason: str | None = None
        self._last_exit_trigger = None  # stored for retry
        self._signal_status: str | None = None
        self._underlying_triggers = None
        self._resolved_direction: str | None = None

        # Exit retry state
        self._exit_retry_count: int = 0
        self._exit_max_retries: int = 3
        self._exit_retry_cooldown_secs: float = 30.0
        self._exit_last_retry_time: float = 0.0  # monotonic

        # Pending signal (set by _check_for_signal, consumed by execute_pending_signal)
        self._pending_signal = None

        # Consumed setup keys — a setup that has produced a trade cannot
        # re-enter.  Keyed by setup_key (direction:break_time_ms).
        # A genuinely new BDRR sequence will have a different break,
        # producing a different setup_key.
        self._consumed_setups: set[str] = set()

        # Per-trade telemetry context (events for TRADE_COMPLETED)
        self._trade_events: dict[str, object] = {}
        self._emitted_terminal: set[str] = set()

    # ── Public API ───────────────────────────────────────────────────────

    def on_bar(self, bar: dict) -> OrchestratorStatus:
        """Process a completed underlying 1m bar.

        This is the main entry point called by the future runner
        on each new bar.

        Returns
        -------
        OrchestratorStatus
        """
        # Add bar to session
        self._session_builder.add_bar(bar)

        # Ensure trading date
        date = self._session_builder.current_date
        if date:
            self._trade_manager.ensure_date(date)

        # State-dependent processing
        if self._lifecycle == LifecycleState.WAITING_FOR_SIGNAL:
            self._check_for_signal()

        elif self._lifecycle == LifecycleState.POSITION_OPEN:
            self._check_exit_trigger(bar)

        return self.status

    def refresh_entry_status(self) -> OrchestratorStatus:
        """Check entry fill status (call periodically while ENTRY_SUBMITTED).

        Returns
        -------
        OrchestratorStatus
        """
        if self._lifecycle != LifecycleState.ENTRY_SUBMITTED:
            return self.status

        if self._entry_submission is None:
            return self.status

        fill_result = check_fill(self._entry_submission)

        if fill_result.state == FillState.FILLED:
            activated = self._fill_activator.apply_if_filled(fill_result)
            if self._emit_once("ENTRY_FILLED"):
                fill_data = {
                    "fill_price": fill_result.average_fill_price,
                    "fill_quantity": fill_result.filled_quantity,
                    "remaining": fill_result.remaining_quantity,
                    "order_id": fill_result.order_id,
                }
                ev = self._do_emit("ENTRY_FILLED", direction=self._resolved_direction, data=fill_data)
                self._trade_events["entry_filled"] = ev
                self._do_emit("POSITION_OPEN", direction=self._resolved_direction)
                self._emitted_terminal.add("POSITION_OPEN")

            activation_ms = 0
            if fill_result.fill_time is not None:
                if hasattr(fill_result.fill_time, 'timestamp'):
                    activation_ms = int(fill_result.fill_time.timestamp() * 1000)

            sess = self._session_builder.current_session()
            if activation_ms == 0 and sess and sess["candles"]:
                activation_ms = sess["candles"][-1]["time_ms"]

            stop = float(self._underlying_triggers.stop_price) if self._underlying_triggers else 0.0
            target = float(self._underlying_triggers.target_price) if self._underlying_triggers else 0.0

            self._exit_monitor = UnderlyingExitMonitor(
                direction=self._resolved_direction or self._direction,
                stop_price=stop,
                target_price=target,
                activation_time_ms=activation_ms,
            )
            self._lifecycle = LifecycleState.POSITION_OPEN

        elif fill_result.state in (FillState.CANCELLED, FillState.REJECTED):
            cancel_type = "ENTRY_CANCELLED" if fill_result.state == FillState.CANCELLED else "ENTRY_REJECTED"
            if self._emit_once(cancel_type):
                self._do_emit(cancel_type, direction=self._resolved_direction,
                              data={"order_id": fill_result.order_id})
            self._clear_active_trade()
            self._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        return self.status

    def refresh_exit_status(self) -> OrchestratorStatus:
        """Check exit fill status (call periodically while EXIT_SUBMITTED).

        EXIT_FAILED recovery:
        - If exit order is CANCELLED/REJECTED, log detailed error info
        - After cooldown, retry exit submission (up to max retries)
        - After retries exhausted → REQUIRES_ATTENTION terminal state
        - Never declares trade closed without confirmed fill

        Returns
        -------
        OrchestratorStatus
        """
        if self._lifecycle == LifecycleState.EXIT_FAILED:
            return self._handle_exit_retry()

        if self._lifecycle != LifecycleState.EXIT_SUBMITTED:
            return self.status

        if self._exit_submission is None:
            return self.status

        exit_fill = check_exit_fill(self._exit_submission)

        if exit_fill.state == ExitFillState.FILLED:
            self._exit_activator.apply_if_filled(exit_fill)

            if self._emit_once("EXIT_FILLED"):
                fill_data = {
                    "fill_price": exit_fill.average_exit_fill_price,
                    "fill_quantity": exit_fill.filled_quantity,
                    "remaining": exit_fill.remaining_quantity,
                    "exit_reason": exit_fill.exit_reason,
                }
                ev = self._do_emit("EXIT_FILLED", direction=self._resolved_direction, data=fill_data)
                self._trade_events["exit_filled"] = ev

                # WIN/LOSS
                result_str = "WIN" if exit_fill.exit_reason == "TARGET" else "LOSS"
                result_type = "TRADE_WIN" if result_str == "WIN" else "TRADE_LOSS"
                self._do_emit(result_type, direction=self._resolved_direction,
                              data={"result": result_str, "exit_reason": exit_fill.exit_reason})

                # TRADE_COMPLETED summary
                from trading_lab.live.event_stream import build_trade_summary
                summary = build_trade_summary(
                    signal_event=self._trade_events.get("signal"),
                    option_event=self._trade_events.get("option"),
                    entry_submitted=self._trade_events.get("entry_submitted"),
                    entry_filled=self._trade_events.get("entry_filled"),
                    trigger_event=self._trade_events.get("trigger"),
                    exit_filled=ev,
                    result=result_str,
                )
                self._do_emit("TRADE_COMPLETED", direction=self._resolved_direction, data=summary)

            saved_direction = self._resolved_direction
            self._exit_reason = exit_fill.exit_reason
            self._clear_active_trade()

            if self._trade_manager.can_trade:
                self._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
            else:
                self._lifecycle = LifecycleState.DONE_FOR_DAY

        elif exit_fill.state in (ExitFillState.CANCELLED, ExitFillState.REJECTED):
            # Log detailed error for diagnostics
            log.error(
                f"[{self._symbol}] EXIT ORDER FAILED — "
                f"status={exit_fill.broker_status} "
                f"exit_order_id={exit_fill.exit_order_id} "
                f"entry_order_id={exit_fill.entry_order_id} "
                f"con_id={exit_fill.con_id} "
                f"reason={exit_fill.exit_reason} "
                f"retry={self._exit_retry_count}/{self._exit_max_retries}"
            )
            cancel_type = "EXIT_CANCELLED" if exit_fill.state == ExitFillState.CANCELLED else "EXIT_REJECTED"
            if self._emit_once(cancel_type):
                self._do_emit(cancel_type, direction=self._resolved_direction, data={
                    "broker_status": exit_fill.broker_status,
                    "exit_order_id": exit_fill.exit_order_id,
                    "con_id": exit_fill.con_id,
                    "retry_count": self._exit_retry_count,
                    "max_retries": self._exit_max_retries,
                })
            self._lifecycle = LifecycleState.EXIT_FAILED

        return self.status

    def _handle_exit_retry(self) -> OrchestratorStatus:
        """Retry exit submission when in EXIT_FAILED state.

        Called by refresh_exit_status when lifecycle == EXIT_FAILED.
        Respects cooldown and max retries.  After retries exhausted,
        transitions to REQUIRES_ATTENTION.
        """
        import time as _time

        if self._exit_retry_count >= self._exit_max_retries:
            if self._lifecycle != LifecycleState.REQUIRES_ATTENTION:
                log.critical(
                    f"[{self._symbol}] EXIT RETRIES EXHAUSTED "
                    f"({self._exit_max_retries}/{self._exit_max_retries}) — "
                    f"REQUIRES MANUAL ATTENTION. "
                    f"con_id={self._entry_con_id} "
                    f"entry_order_id={self._entry_order_id}"
                )
                self._do_emit("EXIT_RETRIES_EXHAUSTED", direction=self._resolved_direction, data={
                    "con_id": self._entry_con_id,
                    "entry_order_id": self._entry_order_id,
                    "retry_count": self._exit_retry_count,
                    "symbol": self._symbol,
                })
                self._lifecycle = LifecycleState.REQUIRES_ATTENTION
            return self.status

        # Cooldown check
        now = _time.monotonic()
        elapsed = now - self._exit_last_retry_time
        if elapsed < self._exit_retry_cooldown_secs:
            return self.status

        # Retry: re-submit exit
        self._exit_retry_count += 1
        self._exit_last_retry_time = now

        log.warning(
            f"[{self._symbol}] EXIT RETRY #{self._exit_retry_count} — "
            f"re-submitting SELL for con_id={self._entry_con_id}"
        )

        try:
            # Allow re-submission by clearing duplicate protection
            self._exit_executor.allow_resubmit(self._entry_order_id)

            # Re-create exit trigger from stored state
            exit_sub = self._exit_executor.submit_exit(
                qualified_contract=self._qualified_contract,
                exit_trigger=self._last_exit_trigger,
                entry_order_id=self._entry_order_id,
                con_id=self._entry_con_id,
                right=self._option_right,
                expiration=self._option_expiration,
                strike=self._option_strike,
                quantity=1,
            )
            self._exit_submission = exit_sub
            self._exit_order_id = exit_sub.order_id
            self._lifecycle = LifecycleState.EXIT_SUBMITTED
            # Reset emit-once for exit status tracking
            self._emitted_terminal.discard("EXIT_CANCELLED")
            self._emitted_terminal.discard("EXIT_REJECTED")

            log.info(
                f"[{self._symbol}] EXIT RETRY #{self._exit_retry_count} "
                f"submitted — order_id={exit_sub.order_id}"
            )
            self._do_emit("EXIT_RETRY", direction=self._resolved_direction, data={
                "retry_count": self._exit_retry_count,
                "order_id": exit_sub.order_id,
                "con_id": self._entry_con_id,
            })

        except Exception as e:
            log.error(
                f"[{self._symbol}] EXIT RETRY #{self._exit_retry_count} "
                f"FAILED: {e}"
            )
            # Stay in EXIT_FAILED for next retry attempt
            self._do_emit("EXIT_RETRY_FAILED", direction=self._resolved_direction, data={
                "retry_count": self._exit_retry_count,
                "error": str(e),
            })

        return self.status

    @property
    def lifecycle(self) -> LifecycleState:
        return self._lifecycle

    @property
    def has_pending_signal(self) -> bool:
        """True if a signal was detected but not yet executed."""
        return self._pending_signal is not None

    @property
    def status(self) -> OrchestratorStatus:
        mgr = self._trade_manager.state
        return OrchestratorStatus(
            lifecycle=self._lifecycle,
            underlying_symbol=self._symbol,
            trading_date=mgr.trading_date,
            signal_status=self._signal_status,
            option_con_id=self._entry_con_id,
            entry_order_id=self._entry_order_id,
            exit_order_id=self._exit_order_id,
            exit_reason=self._exit_reason,
            trades_used=mgr.trades_used,
            wins=mgr.wins,
            losses=mgr.losses,
            day_finished=mgr.day_finished,
        )

    # ── Internal flows ───────────────────────────────────────────────────

    def _check_for_signal(self) -> None:
        """Pure signal detection — NO IBKR sync calls.

        If a signal is found, stores it in _pending_signal for
        later execution by execute_pending_signal().

        A setup whose setup_key has already been consumed (produced
        a trade) is rejected.  A new entry requires a structurally
        different BDRR sequence (different break candle).
        """
        if not self._trade_manager.can_trade:
            self._lifecycle = LifecycleState.DONE_FOR_DAY
            return

        sess = self._session_builder.current_session()
        if sess is None:
            return

        result = self._signal_detector.evaluate(sess)
        if result.status != SignalStatus.SIGNAL:
            return

        # Reject same-setup re-entry: a consumed setup cannot produce
        # another trade.  Only a genuinely new BDRR sequence (new break)
        # is allowed.
        if result.setup_key and result.setup_key in self._consumed_setups:
            log.info(
                f"[{self._symbol}] SETUP_ALREADY_CONSUMED "
                f"key={result.setup_key} — skipping re-entry"
            )
            return

        # Store pending signal — execution deferred outside callback
        self._pending_signal = result

    def execute_pending_signal(self) -> None:
        """Execute IBKR sync work for a pending signal.

        MUST be called OUTSIDE the bar callback (i.e. from the main
        loop), never from _on_bar_update.

        Contains all IBKR sync calls:
        - qualifyContracts
        - reqSecDefOptParams
        - reqMktData
        - placeOrder
        """
        result = self._pending_signal
        if result is None:
            return
        self._pending_signal = None

        # Mark this setup as consumed — prevents same-setup re-entry
        # after exit.  Only a new BDRR sequence can generate another trade.
        if result.setup_key:
            self._consumed_setups.add(result.setup_key)

        resolved_direction = result.direction

        # Build execution intent (pure computation)
        intent = build_option_execution_intent(
            result.trade_plan,
            self._symbol,
            resolved_direction,
            exit_target_r=self._exit_target_r,
            detection_result=result.detection_result,
        )
        triggers = intent.underlying_triggers
        triggers_data = {
            "underlying_entry": float(triggers.entry_price),
            "underlying_stop": float(triggers.stop_price),
            "underlying_target": float(triggers.target_price),
        }

        sig_event = self._do_emit("SIGNAL", direction=resolved_direction, data=triggers_data)
        self._trade_events = {"signal": sig_event}

        # Select option contract — IBKR SYNC
        sess = self._session_builder.current_session()
        if sess is None:
            return
        trading_date_yyyymmdd = sess["date"].replace("-", "")
        underlying_price = sess["candles"][-1]["close"]
        right = "C" if resolved_direction == "LONG" else "P"

        selection = self._option_selector.select(
            underlying_symbol=self._symbol,
            right=right,
            underlying_price=underlying_price,
            trading_date=trading_date_yyyymmdd,
            fetch_market_data=True,
        )

        opt_data = {
            "right": selection.right, "expiration": selection.expiration,
            "strike": selection.strike, "con_id": selection.con_id,
            "exchange": selection.exchange, "multiplier": selection.multiplier,
            "bid": selection.bid, "ask": selection.ask, "spread": selection.spread,
        }
        opt_event = self._do_emit("OPTION_SELECTED", direction=resolved_direction, data=opt_data)
        self._trade_events["option"] = opt_event

        # Build entry order spec (pure computation)
        order_spec = build_option_entry_order(selection)

        entry_built_data = {
            "order_type": "LMT", "quantity": 1,
            "limit_price": order_spec.limit_price,
            "bid": order_spec.bid, "ask": order_spec.ask,
            "spread": order_spec.spread,
        }
        self._do_emit("ENTRY_ORDER_BUILT", direction=resolved_direction, data=entry_built_data)

        # Submit entry — IBKR SYNC
        submission = self._entry_executor.submit_entry(order_spec)

        sub_data = {
            "order_id": submission.order_id, "perm_id": submission.perm_id,
            "status": submission.status, "quantity": 1,
            "limit_price": order_spec.limit_price,
        }
        sub_event = self._do_emit("ENTRY_SUBMITTED", direction=resolved_direction, data=sub_data)
        self._trade_events["entry_submitted"] = sub_event

        # Record state
        self._entry_submission = submission
        self._entry_con_id = submission.con_id
        self._entry_order_id = submission.order_id
        self._qualified_contract = order_spec.qualified_contract
        self._option_right = selection.right
        self._option_expiration = selection.expiration
        self._option_strike = selection.strike
        self._signal_status = "SIGNAL"
        self._underlying_triggers = intent.underlying_triggers
        self._resolved_direction = resolved_direction
        self._lifecycle = LifecycleState.ENTRY_SUBMITTED

    def _check_exit_trigger(self, bar: dict) -> None:
        if self._exit_monitor is None:
            return

        result = self._exit_monitor.evaluate_bar(bar)

        if result.state in (ExitState.STOP_TRIGGERED, ExitState.TARGET_TRIGGERED):
            trigger_type = "TARGET_TRIGGERED" if result.state == ExitState.TARGET_TRIGGERED else "STOP_TRIGGERED"
            if self._emit_once(trigger_type):
                trigger_data = {
                    "exit_reason": "TARGET" if result.state == ExitState.TARGET_TRIGGERED else "STOP",
                    "bar_time_ms": result.trigger_bar_time_ms,
                    "bar_open": result.trigger_bar_open, "bar_high": result.trigger_bar_high,
                    "bar_low": result.trigger_bar_low, "bar_close": result.trigger_bar_close,
                    "same_bar_ambiguity": result.same_bar_ambiguity,
                }
                ev = self._do_emit(trigger_type, direction=self._resolved_direction, data=trigger_data)
                self._trade_events["trigger"] = ev

            exit_sub = self._exit_executor.submit_exit(
                qualified_contract=self._qualified_contract,
                exit_trigger=result,
                entry_order_id=self._entry_order_id,
                con_id=self._entry_con_id,
                right=self._option_right,
                expiration=self._option_expiration,
                strike=self._option_strike,
                quantity=1,
            )
            self._last_exit_trigger = result  # stored for retry
            exit_data = {
                "order_id": exit_sub.order_id, "exit_reason": exit_sub.exit_reason,
                "con_id": self._entry_con_id, "quantity": 1, "status": exit_sub.status,
            }
            self._do_emit("EXIT_SUBMITTED", direction=self._resolved_direction, data=exit_data)
            self._trade_events["exit_submitted"] = True

            self._exit_submission = exit_sub
            self._exit_order_id = exit_sub.order_id
            self._exit_reason = exit_sub.exit_reason
            self._lifecycle = LifecycleState.EXIT_SUBMITTED

    def _clear_active_trade(self) -> None:
        self._entry_submission = None
        self._entry_con_id = None
        self._entry_order_id = None
        self._qualified_contract = None
        self._option_right = ""
        self._option_expiration = ""
        self._option_strike = 0.0
        self._exit_monitor = None
        self._exit_submission = None
        self._exit_order_id = None
        self._last_exit_trigger = None
        self._signal_status = None
        self._underlying_triggers = None
        self._resolved_direction = None
        self._trade_events = {}
        self._emitted_terminal = set()
        self._exit_retry_count = 0
        self._exit_last_retry_time = 0.0

    def _do_emit(self, event_type, direction=None, data=None):
        """Emit an event via the injected callback, if present."""
        if self._emit_fn:
            return self._emit_fn(event_type, symbol=self._symbol,
                                 direction=direction, data=data)
        return None

    def _emit_once(self, key: str) -> bool:
        """Return True only the first time this key is checked."""
        if key in self._emitted_terminal:
            return False
        self._emitted_terminal.add(key)
        return True
