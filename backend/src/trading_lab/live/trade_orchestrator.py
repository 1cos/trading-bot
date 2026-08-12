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
        self._signal_status: str | None = None
        self._underlying_triggers = None
        self._resolved_direction: str | None = None

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
            # Create exit monitor with fill timestamp as activation
            activation_ms = 0
            if fill_result.fill_time is not None:
                if hasattr(fill_result.fill_time, 'timestamp'):
                    activation_ms = int(fill_result.fill_time.timestamp() * 1000)

            # Fall back to latest bar time if no fill time
            sess = self._session_builder.current_session()
            if activation_ms == 0 and sess and sess["candles"]:
                activation_ms = sess["candles"][-1]["time_ms"]

            intent = self._underlying_triggers
            stop = float(intent.entry_price) if intent else 0.0  # unused fallback
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
            self._clear_active_trade()
            self._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        return self.status

    def refresh_exit_status(self) -> OrchestratorStatus:
        """Check exit fill status (call periodically while EXIT_SUBMITTED).

        Returns
        -------
        OrchestratorStatus
        """
        if self._lifecycle != LifecycleState.EXIT_SUBMITTED:
            return self.status

        if self._exit_submission is None:
            return self.status

        exit_fill = check_exit_fill(self._exit_submission)

        if exit_fill.state == ExitFillState.FILLED:
            self._exit_activator.apply_if_filled(exit_fill)
            self._exit_reason = exit_fill.exit_reason
            self._clear_active_trade()

            if self._trade_manager.can_trade:
                self._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
            else:
                self._lifecycle = LifecycleState.DONE_FOR_DAY

        elif exit_fill.state in (ExitFillState.CANCELLED, ExitFillState.REJECTED):
            self._lifecycle = LifecycleState.EXIT_FAILED

        return self.status

    @property
    def lifecycle(self) -> LifecycleState:
        return self._lifecycle

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
        if not self._trade_manager.can_trade:
            self._lifecycle = LifecycleState.DONE_FOR_DAY
            return

        sess = self._session_builder.current_session()
        if sess is None:
            return

        result = self._signal_detector.evaluate(sess)
        if result.status != SignalStatus.SIGNAL:
            return

        # Use resolved direction from signal (supports BOTH mode)
        resolved_direction = result.direction

        # Build execution intent
        intent = build_option_execution_intent(
            result.trade_plan,
            self._symbol,
            resolved_direction,
            exit_target_r=self._exit_target_r,
            detection_result=result.detection_result,
        )

        # Select option contract
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

        # Build entry order spec
        order_spec = build_option_entry_order(selection)

        # Submit entry
        submission = self._entry_executor.submit_entry(order_spec)

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
        self._signal_status = None
        self._underlying_triggers = None
        self._resolved_direction = None
