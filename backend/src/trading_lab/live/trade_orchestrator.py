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
from trading_lab.live.trade_state_store import (
    DEFAULT_TRADE_STATE_DIR,
    build_setup_snapshot,
    build_trade_id,
    persist_closed_trade,
    persist_open_trade,
    persist_terminal_trade,
)
from trading_lab.live.underlying_exit_monitor import ExitState, UnderlyingExitMonitor

log = logging.getLogger("maxbot")

# Entry chart window. Anchored to the setup's own break candle rather
# than a flat "last N bars": the structure is what makes the chart
# readable, and a setup can be 8 bars long or 60.
CHART_BARS_BEFORE_BREAK = 5
# Safety cap. Trimmed from the OLDEST end, so break, displacement,
# retest and the entry candle always survive — losing lead-in context
# is acceptable, losing the setup is not.
CHART_MAX_BARS = 120

# The exit window must never lose the path from entry to exit just to
# respect a cap, and a trade can run most of the session. 390 is one
# full RTH day of 1m bars, so in practice nothing is ever trimmed.
CHART_MAX_BARS_EXIT = 390

_CHART_LEVEL_KEYS = ("orb_high", "orb_low", "pdh", "pdl", "pmh", "pml")


# Keys lifted from the TRADE_COMPLETED summary into the CLOSED record's
# "outcome" block. Deliberately excludes what the OPEN record already
# holds (symbol, direction, underlying_*, option_*, entry_*) so the file
# does not carry the same value twice under two names.
_OUTCOME_SUMMARY_FIELDS = (
    "result",
    "exit_reason",
    "trigger_time_ms",
    "exit_fill_time_ms",
    "exit_fill_premium",
    "gross_pnl",
    "gross_pnl_note",
    "premium_return_pct",
    "duration_entry_to_exit_ms",
    "duration_signal_to_exit_ms",
)


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
    EXISTING_BROKER_POSITION = "EXISTING_BROKER_POSITION"  # startup reconciliation found an untracked open option position — new entries blocked


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
        trade_state_dir: object | None = None,
        chart_levels_provider=None,
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
        self._trade_state_dir = trade_state_dir if trade_state_dir is not None else DEFAULT_TRADE_STATE_DIR
        # Structural levels for the entry chart (ORB high/low, PDH/PDL,
        # PMH/PML). Injected as a CALLABLE, not values: the orchestrator
        # is built in _setup_symbol() before context_levels exists and
        # before any ORB is known, so a snapshot taken at construction
        # would be all None. bot_runner passes a closure over its own
        # SymbolRuntime — no circular import, no global lookup, and the
        # orchestrator still owns nothing it should not own.
        self._chart_levels_provider = chart_levels_provider

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

        # Setup/signal identity for the currently active trade — preserved
        # from signal acceptance through to the confirmed fill so a crash-
        # safe OPEN record can be persisted with correct identity (see
        # execute_pending_signal() and refresh_entry_status()). Otherwise
        # these values are only ever transient locals inside
        # execute_pending_signal() and are lost the moment it returns.
        self._active_setup_key: str | None = None
        self._active_signal_key: str | None = None
        self._active_entry_timestamp_ms: int | None = None
        # Frozen, plain-JSON copy of the DetectionResult behind the
        # active trade — the structural "why" (break/displacement/
        # retest/confirmation). Same lifetime problem as the keys
        # above: it lives only inside execute_pending_signal()'s local
        # `result` unless captured here.
        self._active_setup_snapshot: dict | None = None
        # Start of the entry chart window, so the exit chart can open on
        # the same bar and the two images are directly comparable.
        self._active_chart_start_ms: int | None = None

        # Exit retry state
        self._exit_retry_count: int = 0
        self._exit_max_retries: int = 3
        self._exit_retry_cooldown_secs: float = 30.0
        self._exit_last_retry_time: float = 0.0  # monotonic
        # Last broker-side exit failure, kept for the audit record.
        # The value is already produced (broker_status on a cancel/
        # reject, the exception text on a failed retry) but was only
        # logged and emitted, never retained — so by the time retries
        # were exhausted nothing could say WHY.
        self._exit_last_error: str | None = None

        # Pending signal (set by _check_for_signal, consumed by execute_pending_signal)
        self._pending_signal = None

        # Consumed setup keys — a setup that has produced a trade cannot
        # re-enter.  Keyed by setup_key (direction:break_time_ms).
        # A genuinely new BDRR sequence will have a different break,
        # producing a different setup_key.
        self._consumed_setups: set[str] = set()

        # Consumed signal keys — exactly-once execution per signal.
        # Keyed by signal_key (setup_key:entry_candle_time_ms).
        # Even if an entry is cancelled, the same signal cannot re-fire.
        # A new entry candle produces a new signal_key.
        self._consumed_signals: set[str] = set()

        # Live boundary: signals with entry_timestamp_ms before this
        # are stale (from before the bot started) and non-executable.
        self._live_boundary_ms: int = 0

        # Startup safety gate: set externally (by the runner, after
        # startup position reconciliation) when IBKR already shows an
        # existing, untracked option position for this symbol. While
        # True, execute_pending_signal() refuses to submit any entry
        # order, regardless of lifecycle state — a defensive check
        # placed right next to the real order-submission call, not
        # just a display/UI condition. See
        # MaxBotRunner._reconcile_existing_positions() for how this
        # gets set.
        self._broker_position_blocked: bool = False

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
            self._check_for_signal(bar)

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

        # Diagnostic logging for every entry status check
        log.debug(
            f"[{self._symbol}] ENTRY_STATUS_CHECK "
            f"order={fill_result.order_id} "
            f"conId={fill_result.con_id} "
            f"status={fill_result.broker_status} "
            f"filled={fill_result.filled_quantity} "
            f"remaining={fill_result.remaining_quantity} "
            f"avgPrice={fill_result.average_fill_price} "
            f"state={fill_result.state} "
            f"lifecycle={self._lifecycle}"
        )

        if fill_result.state == FillState.FILLED:
            # SAFETY: require positive evidence of fill before POSITION_OPEN.
            # An order that shows "Filled" status but filled_quantity == 0
            # must NOT transition to POSITION_OPEN.
            if fill_result.filled_quantity <= 0:
                log.error(
                    f"[{self._symbol}] FILL ANOMALY — status=Filled but "
                    f"filled_qty={fill_result.filled_quantity}, "
                    f"order_id={fill_result.order_id}. "
                    f"Treating as CANCELLED for safety."
                )
                self._do_emit("ENTRY_FILL_ANOMALY", direction=self._resolved_direction,
                              data={"order_id": fill_result.order_id,
                                    "broker_status": fill_result.broker_status,
                                    "filled_quantity": fill_result.filled_quantity})
                self._clear_active_trade()
                self._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
                return self.status

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
                self._persist_open_trade_state(fill_result)

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
            log.info(
                f"[{self._symbol}] ENTRY_STATUS_CHECK → POSITION_OPEN "
                f"order={fill_result.order_id} "
                f"filled={fill_result.filled_quantity} "
                f"avgPrice={fill_result.average_fill_price} "
                f"status={fill_result.broker_status}"
            )

        elif fill_result.state in (FillState.CANCELLED, FillState.REJECTED):
            cancel_type = "ENTRY_CANCELLED" if fill_result.state == FillState.CANCELLED else "ENTRY_REJECTED"
            log.info(
                f"[{self._symbol}] ENTRY_STATUS_CHECK → {cancel_type} "
                f"order={fill_result.order_id} "
                f"filled={fill_result.filled_quantity} "
                f"status={fill_result.broker_status}"
            )
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

                # Close out the on-disk record while the active-trade
                # fields are still populated — _clear_active_trade()
                # below wipes setup_key and the rest.
                self._persist_closed_trade_state(summary)

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
            self._exit_last_error = (
                f"{cancel_type}: broker_status={exit_fill.broker_status} "
                f"exit_order_id={exit_fill.exit_order_id}"
            )
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
                ev = self._do_emit("EXIT_RETRIES_EXHAUSTED", direction=self._resolved_direction, data={
                    "con_id": self._entry_con_id,
                    "entry_order_id": self._entry_order_id,
                    "retry_count": self._exit_retry_count,
                    "symbol": self._symbol,
                })
                self._lifecycle = LifecycleState.REQUIRES_ATTENTION
                # Record the terminal state while the active-trade
                # fields are still populated. This path never calls
                # _clear_active_trade() — the position may still be
                # open at the broker — but the on-disk record must
                # stop claiming the trade is under normal management.
                self._persist_terminal_trade_state(ev)
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
            self._exit_last_error = f"EXIT_RETRY_FAILED: {e}"
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

    def _check_for_signal(self, bar: dict | None = None) -> None:
        """Pure signal detection — NO IBKR sync calls.

        If a signal is found, stores it in _pending_signal for
        later execution by execute_pending_signal().

        A setup whose setup_key has already been consumed (produced
        a trade) is rejected.  A new entry requires a structurally
        different BDRR sequence (different break candle).

        ``bar`` is the underlying bar that was just added to the
        session (the "current completed bar").  It is used for the
        edge-trigger gate below — a SIGNAL is only actionable when
        its entry/rejection candle IS this bar, never a historical one.
        """
        if not self._trade_manager.can_trade:
            self._lifecycle = LifecycleState.DONE_FOR_DAY
            return

        sess = self._session_builder.current_session()
        if sess is None:
            return

        result = self._signal_detector.evaluate(
            sess, consumed_setup_keys=self._consumed_setups
        )
        if result.status != SignalStatus.SIGNAL:
            return

        # Reject same-setup re-entry (defense in depth)
        if result.setup_key and result.setup_key in self._consumed_setups:
            log.info(
                f"[{self._symbol}] SETUP_ALREADY_CONSUMED "
                f"key={result.setup_key} — skipping re-entry"
            )
            return

        # Reject same-signal re-emission (exactly-once)
        if result.signal_key and result.signal_key in self._consumed_signals:
            log.info(
                f"[{self._symbol}] SIGNAL_ALREADY_CONSUMED "
                f"signal_key={result.signal_key} — skipping"
            )
            return

        # Reject stale signals from before bot startup (mid-session restart)
        if (self._live_boundary_ms > 0
                and result.entry_timestamp_ms
                and result.entry_timestamp_ms < self._live_boundary_ms):
            log.info(
                f"[{self._symbol}] SIGNAL_STALE — entry candle "
                f"{result.entry_timestamp_ms} < live boundary "
                f"{self._live_boundary_ms} — skipping"
            )
            # Mark consumed for SCANNING purposes only (Gap B, 2026-08-21
            # audit). _live_boundary_ms above is what prevents this signal
            # from ever being EXECUTED — that gate is unconditional and
            # unaffected by this. But without also marking setup_key/
            # signal_key consumed here, the detector would keep
            # re-deriving and re-reporting this exact pre-restart setup
            # on every subsequent bar for the rest of the session (it is
            # never "consumed" by the normal accept-a-signal path below,
            # since we return before reaching it), masking any
            # genuinely new setup that forms later — the detector's break
            # scan cursor never advances past a break that evaluate()'s
            # scan loop does not yet know to skip. This can only ever
            # mark a signal whose
            # entry candle is provably in the past relative to bot
            # startup, so it can never consume a live/current signal.
            if result.setup_key:
                self._consumed_setups.add(result.setup_key)
            if result.signal_key:
                self._consumed_signals.add(result.signal_key)
            return

        # Edge-trigger gate: the entry/rejection candle found by the
        # detector must BE the current completed bar, not a historical
        # candle re-discovered by scanning the session from the start.
        # A rejection candle from earlier in the session is valid
        # historical context but must never trigger an order now — the
        # only moment Max's rule allows execution is the close of the
        # candle that itself satisfies break→displacement→retest→reject.
        # entry_timestamp_ms/current bar time_ms are only compared when
        # both are known; otherwise the check is skipped (cannot verify).
        current_bar_time_ms = bar.get("time_ms") if isinstance(bar, dict) else None
        if (result.entry_timestamp_ms is not None
                and current_bar_time_ms is not None
                and result.entry_timestamp_ms != current_bar_time_ms):
            log.info(
                f"[{self._symbol}] SIGNAL_NOT_CURRENT "
                f"setup_key={result.setup_key} "
                f"entry_candle_time_ms={result.entry_timestamp_ms} "
                f"current_bar_time_ms={current_bar_time_ms} — skipping"
            )
            # Archive for SCANNING purposes only — the same treatment
            # SIGNAL_STALE above already gets, and for the same reason.
            # The edge-trigger gate is what prevents execution, and it is
            # unconditional; but without also marking this setup consumed
            # the detector re-derives and re-reports this identical setup
            # on every subsequent bar, so its scan cursor never advances
            # past this break and any genuinely new setup formed later is
            # masked (observed live on AAPL SHORT from 10:09 ET onwards,
            # 2026-08-25 audit).
            #
            # This can never suppress a distinct future setup. Stage 5
            # returns the FIRST qualifying candle in the retest window,
            # scanning chronologically, so once a break has produced
            # entry candle E that pairing is fixed — later bars only
            # extend the window's end, never insert an earlier
            # qualifier. An entry candle that is historical now is
            # therefore historical forever, and a structurally different
            # sequence necessarily has a different break_time_ms, hence
            # a different setup_key. Both properties are asserted in
            # test_detector_scan_cursor.py.
            if result.setup_key:
                self._consumed_setups.add(result.setup_key)
            if result.signal_key:
                self._consumed_signals.add(result.signal_key)
            return

        # Consume setup_key and signal_key IMMEDIATELY when a signal
        # is accepted.  This prevents re-entry even if the execution
        # fails, the order is cancelled, or the exit completes and
        # lifecycle returns to WAITING_FOR_SIGNAL.
        # One setup/break → at most one trade.
        if result.setup_key:
            self._consumed_setups.add(result.setup_key)
            log.info(
                f"[{self._symbol}] SETUP_CONSUMED at signal acceptance "
                f"key={result.setup_key}"
            )
        if result.signal_key:
            self._consumed_signals.add(result.signal_key)

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

        # Defensive gate, right next to the real order-submission path:
        # refuse to execute if startup reconciliation found an existing,
        # untracked broker option position for this symbol. The primary
        # gate is the lifecycle transition in _check_for_signal (which
        # normally prevents _pending_signal from ever being set while
        # blocked) — this is a second, independent check so a BUY can
        # never reach the broker for this symbol regardless of how
        # _pending_signal got populated.
        if self._broker_position_blocked:
            log.error(
                f"[{self._symbol}] EXISTING BROKER POSITION — refusing to "
                f"execute pending signal (setup_key={result.setup_key})"
            )
            self._pending_signal = None
            self._lifecycle = LifecycleState.EXISTING_BROKER_POSITION
            return

        self._pending_signal = None

        # Mark this setup AND signal as consumed — prevents:
        # 1. Same setup re-entry (setup_key)
        # 2. Same signal re-emission (signal_key, exactly-once)
        if result.setup_key:
            self._consumed_setups.add(result.setup_key)
        if result.signal_key:
            self._consumed_signals.add(result.signal_key)

        # Preserve identity for this active trade — result itself is a
        # local variable and is discarded once this method returns, so
        # without this these values would be unavailable by the time the
        # entry fill is confirmed (see refresh_entry_status()).
        self._active_setup_key = result.setup_key
        self._active_signal_key = result.signal_key
        self._active_entry_timestamp_ms = result.entry_timestamp_ms
        # Same reason, for the structural "why" behind the trade:
        # result.detection_result holds the break/displacement/retest/
        # confirmation the detector already computed, and dies with
        # `result`. Frozen to plain JSON here so the OPEN trade record
        # can carry it. Copy only — nothing is recomputed.
        self._active_setup_snapshot = build_setup_snapshot(
            result.detection_result,
            rejection_detail=result.rejection_detail)

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

    def on_price(self, price: float) -> None:
        """Live underlying price while a position is open — the primary
        exit path.

        Setup detection stays candle-close based; only the exit becomes
        price-event based, and only once the position is open. Called
        from the runner's existing bar-update callback on every price
        update, so a level crossing fires when it is observed instead of
        at the next bar close (the MU trade of 2026-08-26 waited 100.9s
        for a target that had already been reached).

        Safe to call on every update: the monitor is terminal and
        idempotent, and this returns immediately unless a position is
        actually open.
        """
        if self._lifecycle != LifecycleState.POSITION_OPEN:
            return
        if self._exit_monitor is None:
            return
        result = self._exit_monitor.evaluate_price(price)
        self._submit_exit_for(result)

    def _check_exit_trigger(self, bar: dict) -> None:
        """Completed-bar backstop for the live price path above.

        Retained for a crossing the live feed never delivered (a gap, a
        dropped update, a stale tick). It shares the monitor's single
        terminal result, so it can never produce a second exit for a
        trigger the price path already fired.
        """
        if self._exit_monitor is None:
            return

        result = self._exit_monitor.evaluate_bar(bar)
        self._submit_exit_for(result)

    def _submit_exit_for(self, result) -> None:
        """Submit the exit for a terminal trigger, whatever produced it.

        Shared by the live-price and completed-bar paths so both go
        through exactly one submission route. Idempotent at four
        independent layers: the monitor is terminal, _emit_once guards
        the event, the lifecycle leaves POSITION_OPEN, and the executor
        refuses a duplicate for the same entry_order_id.
        """
        if self._lifecycle != LifecycleState.POSITION_OPEN:
            return

        if result.state in (ExitState.STOP_TRIGGERED, ExitState.TARGET_TRIGGERED):
            trigger_type = "TARGET_TRIGGERED" if result.state == ExitState.TARGET_TRIGGERED else "STOP_TRIGGERED"
            if self._emit_once(trigger_type):
                trigger_data = {
                    "exit_reason": "TARGET" if result.state == ExitState.TARGET_TRIGGERED else "STOP",
                    "bar_time_ms": result.trigger_bar_time_ms,
                    "bar_open": result.trigger_bar_open, "bar_high": result.trigger_bar_high,
                    "bar_low": result.trigger_bar_low, "bar_close": result.trigger_bar_close,
                    "same_bar_ambiguity": result.same_bar_ambiguity,
                    "trigger_source": result.trigger_source,
                    "trigger_price": result.trigger_price,
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

    def _persist_open_trade_state(self, fill_result) -> None:
        """Write a crash-safe OPEN trade-state record to disk.

        Called exactly once per confirmed entry fill (guarded by the
        _emit_once("ENTRY_FILLED") check at the call site) — never on
        rejected/cancelled/still-pending entries. Best-effort: a
        persistence failure is logged but must never break the trading
        lifecycle itself.
        """
        if not self._active_setup_key or self._underlying_triggers is None:
            log.error(
                f"[{self._symbol}] Cannot persist OPEN trade state — "
                f"missing setup_key or underlying_triggers"
            )
            return
        try:
            trade_id = build_trade_id(self._symbol, self._active_setup_key)
            local_symbol = getattr(self._qualified_contract, "localSymbol", None)
            record = {
                "trade_id": trade_id,
                "symbol": self._symbol,
                "setup_key": self._active_setup_key,
                "signal_key": self._active_signal_key,
                "direction": self._resolved_direction,
                "entry_timestamp_ms": self._active_entry_timestamp_ms,
                "underlying_entry": float(self._underlying_triggers.entry_price),
                "stop": float(self._underlying_triggers.stop_price),
                "target": float(self._underlying_triggers.target_price),
                "rr": self._exit_target_r,
                "option": {
                    "con_id": self._entry_con_id,
                    "local_symbol": local_symbol,
                    "right": self._option_right,
                    "strike": self._option_strike,
                    "expiry": self._option_expiration,
                },
                "quantity": 1,
                "entry_order_id": self._entry_order_id,
                "entry_fill_price": fill_result.average_fill_price,
                "state": "OPEN",
            }
            # Additive: absent on records written before this existed,
            # and omitted entirely when there is nothing to snapshot,
            # so the previous record shape stays valid either way.
            if self._active_setup_snapshot is not None:
                record["setup_snapshot"] = self._active_setup_snapshot
            # Additive, best-effort: a chart is an audit nicety and must
            # never cost a trade its OPEN record.
            try:
                chart_context = self._build_chart_context()
            except Exception as e:
                log.error(f"[{self._symbol}] chart context capture failed: {e}")
                chart_context = None
            if chart_context is not None:
                record["chart_context"] = chart_context
            path = persist_open_trade(record, base_dir=self._trade_state_dir)
            log.info(f"[{self._symbol}] TRADE_STATE_PERSISTED trade_id={trade_id} path={path}")
        except Exception as e:
            log.error(f"[{self._symbol}] Failed to persist OPEN trade state: {e}")

    def _chart_levels_now(self) -> dict:
        """Structural levels as they stand right now, or nulls.

        Copied from the injected provider, never recomputed from the
        candles and never guessed: a chart can render "not drawn" for a
        null, but a wrong price is worse than a missing one.
        """
        levels = {k: None for k in _CHART_LEVEL_KEYS}
        if self._chart_levels_provider is None:
            return levels
        try:
            provided = self._chart_levels_provider()
        except Exception as e:
            log.debug(f"[{self._symbol}] chart levels unavailable: {e}")
            return levels
        if isinstance(provided, dict):
            for k in _CHART_LEVEL_KEYS:
                v = provided.get(k)
                # bool is an int subclass — never a price.
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    levels[k] = float(v)
        return levels

    def _index_of_bar(self, candles: list, time_ms: int | None) -> int | None:
        if time_ms is None:
            return None
        for i, c in enumerate(candles):
            if c.get("time_ms") == time_ms:
                return i
        return None

    def _default_window_start(self, candles: list, end: int) -> int:
        """CHART_BARS_BEFORE_BREAK bars before the break candle.

        Anchored to the setup rather than a flat "last N bars": the
        structure is what makes a chart readable, and a setup can be 8
        bars long or 60. Fewer bars before the break than asked for
        simply means the session started there.
        """
        snapshot = self._active_setup_snapshot or {}
        break_ms = (snapshot.get("break_bar") or {}).get("bar_utc_ms")
        i = self._index_of_bar(candles[:end], break_ms)
        return max(0, i - CHART_BARS_BEFORE_BREAK) if i is not None else 0

    def _freeze_chart(self, session: dict, window: list, cap: int,
                      extra_window: dict) -> dict:
        """Build one chart block from a slice of candles.

        Trims from the OLDEST end so the most recent action always
        survives — losing lead-in context is acceptable, losing the
        entry or the exit move is not. Candles are rebuilt as fresh
        dicts: the session builder keeps growing after this point.
        """
        if len(window) > cap:
            window = window[-cap:]
        frozen = [
            {"time_ms": c.get("time_ms"), "open": c.get("open"),
             "high": c.get("high"), "low": c.get("low"),
             "close": c.get("close"), "volume": c.get("volume")}
            for c in window
        ]
        block = {
            "timeframe_seconds": 60,
            "market_timezone": session.get("market_timezone"),
            "candles": frozen,
            "levels": self._chart_levels_now(),
            "window": {
                "start_time_ms": frozen[0]["time_ms"] if frozen else None,
                "end_time_ms": frozen[-1]["time_ms"] if frozen else None,
            },
        }
        block["window"].update(extra_window)
        return block

    def _build_chart_context(self) -> dict | None:
        """Freeze what the ENTRY chart will need, from memory only.

        The candles and the structural levels live solely in the
        runner's memory: after a restart they are gone, and the trade
        record alone cannot show what MaxBot was looking at. This
        copies them into the record while they still exist.

        Window: CHART_BARS_BEFORE_BREAK bars before the break candle
        through the entry candle inclusive. There are no bars after the
        entry candle by construction — the edge-trigger gate requires
        the entry candle to BE the current bar.

        Deliberately carries no break/displacement/retest/confirmation
        data: that is setup_snapshot's job, and duplicating it would
        create two versions of one truth.
        """
        session = self._session_builder.current_session()
        if session is None:
            return None
        candles = session.get("candles") or []

        entry_ms = self._active_entry_timestamp_ms
        i = self._index_of_bar(candles, entry_ms)
        end = i + 1 if i is not None else len(candles)
        start = self._default_window_start(candles, end)

        block = self._freeze_chart(
            session, candles[start:end], CHART_MAX_BARS,
            {"entry_time_ms": entry_ms},
        )
        # Remembered so the exit chart can open on the same bar.
        self._active_chart_start_ms = block["window"]["start_time_ms"]
        return block

    def _build_exit_chart_context(self) -> dict | None:
        """Freeze the path from entry to exit, from memory only.

        Opens on the SAME bar as the entry chart, so the two images
        share an origin and can be read side by side. When that start
        is unknown — a legacy trade, or a record whose entry context
        was never built — it falls back to the same break-anchored rule
        the entry chart uses.

        Ends on the TRIGGER BAR, not on the fill: trigger_time_ms and
        exit_fill_time_ms in the outcome are event wall clocks, while
        the chart needs the bar whose movement actually caused the
        exit. On REQUIRES_ATTENTION there is no fill at all, and the
        window simply runs to the last completed bar.
        """
        session = self._session_builder.current_session()
        if session is None:
            return None
        candles = session.get("candles") or []
        if not candles:
            return None

        trigger_bar_ms = getattr(
            self._last_exit_trigger, "trigger_bar_time_ms", None)
        i = self._index_of_bar(candles, trigger_bar_ms)
        end = i + 1 if i is not None else len(candles)

        start = self._index_of_bar(candles, self._active_chart_start_ms)
        if start is None:
            start = self._default_window_start(candles, end)

        return self._freeze_chart(
            session, candles[start:end], CHART_MAX_BARS_EXIT,
            {"entry_time_ms": self._active_entry_timestamp_ms,
             "exit_time_ms": trigger_bar_ms},
        )

    def _exit_chart_block(self) -> dict | None:
        """Best-effort exit chart capture, shared by both terminal
        paths. A chart is an audit nicety and must never change how a
        trade ends."""
        try:
            return self._build_exit_chart_context()
        except Exception as e:
            log.error(f"[{self._symbol}] exit chart context failed: {e}")
            return None

    def _persist_closed_trade_state(self, summary: dict) -> None:
        """Flip the on-disk trade-state record to CLOSED.

        Called exactly once per completed trade, from inside the
        _emit_once("EXIT_FILLED") block that also emits
        TRADE_COMPLETED, and BEFORE _clear_active_trade() — which is
        the only window where the trade id can still be derived.

        Best-effort audit, mirroring _persist_open_trade_state(): a
        failure here is logged and swallowed. The trade is already
        closed at the broker; this must never change its outcome, never
        submit an order, and never push the lifecycle to
        REQUIRES_ATTENTION. Losing the audit record is strictly better
        than acting on a persistence error.

        Values are copied from `summary` (the TRADE_COMPLETED payload
        build_trade_summary() already produced) plus the exit order id
        the orchestrator is holding. Nothing is recomputed.
        """
        if not self._active_setup_key:
            log.error(
                f"[{self._symbol}] Cannot persist CLOSED trade state — "
                f"missing setup_key"
            )
            return
        try:
            trade_id = build_trade_id(self._symbol, self._active_setup_key)
            outcome = {k: summary[k]
                       for k in _OUTCOME_SUMMARY_FIELDS if k in summary}
            if self._exit_order_id is not None:
                outcome["exit_order_id"] = self._exit_order_id
            path = persist_closed_trade(trade_id, outcome,
                                        base_dir=self._trade_state_dir,
                                        exit_chart_context=self._exit_chart_block())
            log.info(
                f"[{self._symbol}] TRADE_STATE_CLOSED trade_id={trade_id} "
                f"path={path}"
            )
        except Exception as e:
            log.error(
                f"[{self._symbol}] Failed to persist CLOSED trade state: {e}"
            )

    def _persist_terminal_trade_state(self, event=None) -> None:
        """Write the REQUIRES_ATTENTION terminal state to disk.

        Called exactly once per trade, from inside the
        `_lifecycle != REQUIRES_ATTENTION` guard that also emits
        EXIT_RETRIES_EXHAUSTED, so repeated refresh_exit_status() calls
        cannot rewrite it.

        Deliberately writes NO exit fill price and NO P&L: the exit was
        never confirmed, so neither exists. A CLOSED record would claim
        a settled trade that may still be open at the broker.

        Best-effort audit, same isolation as the OPEN and CLOSED
        writes: on failure the error is logged and swallowed. It must
        never submit an order, never reset the retry counter, and never
        move the lifecycle away from REQUIRES_ATTENTION — the runtime
        decision has already been made and stands regardless of whether
        the audit record could be written.
        """
        if not self._active_setup_key:
            log.error(
                f"[{self._symbol}] Cannot persist terminal trade state — "
                f"missing setup_key"
            )
            return
        try:
            trade_id = build_trade_id(self._symbol, self._active_setup_key)
            ts = getattr(event, "timestamp_ms", None)
            if ts is None:
                ts = int(datetime.now(timezone.utc).timestamp() * 1000)
            terminal = {
                "runtime_state": str(LifecycleState.REQUIRES_ATTENTION),
                "reason": "EXIT_RETRIES_EXHAUSTED",
                "exit_reason": self._exit_reason,
                "retry_count": self._exit_retry_count,
                "max_retries": self._exit_max_retries,
                "exit_order_id": self._exit_order_id,
                "entry_order_id": self._entry_order_id,
                "con_id": self._entry_con_id,
                "last_error": self._exit_last_error,
                "terminal_timestamp_ms": ts,
            }
            path = persist_terminal_trade(
                trade_id, str(LifecycleState.REQUIRES_ATTENTION), terminal,
                base_dir=self._trade_state_dir,
                exit_chart_context=self._exit_chart_block(),
            )
            log.info(
                f"[{self._symbol}] TRADE_STATE_TERMINAL trade_id={trade_id} "
                f"state=REQUIRES_ATTENTION path={path}"
            )
        except Exception as e:
            log.error(
                f"[{self._symbol}] Failed to persist terminal trade state: {e}"
            )

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
        self._active_setup_key = None
        self._active_signal_key = None
        self._active_entry_timestamp_ms = None
        self._active_setup_snapshot = None
        self._active_chart_start_ms = None
        self._trade_events = {}
        self._emitted_terminal = set()
        self._exit_retry_count = 0
        self._exit_last_retry_time = 0.0
        self._exit_last_error = None

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
