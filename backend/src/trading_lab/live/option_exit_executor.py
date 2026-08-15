"""Option exit executor — submits SELL MARKET after underlying trigger.

When UnderlyingExitMonitor fires STOP_TRIGGERED or TARGET_TRIGGERED,
this executor submits a single SELL MARKET order to close the open
option position.

MaxBot v0.1 exit policy:
    action     = SELL
    order_type = MKT (market — immediate close once strategy decides)
    quantity   = 1

Entry remains LIMIT; exit is MARKET for v0.1.

Duplicate-submission protection:
    Uses entry_order_id to track which positions have already had an
    exit submitted.  A second submit_exit call for the same
    entry_order_id is rejected.

Does NOT:
    - monitor exit fills
    - record WIN/LOSS
    - update DailyTradeManager
    - calculate option-premium stop/target
    - reselect option contracts
    - connect to IBKR (IB instance injected)
"""

from __future__ import annotations

from dataclasses import dataclass

from ib_insync import MarketOrder

from trading_lab.live.underlying_exit_monitor import ExitState


# ── Exit submission result ───────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ExitSubmissionResult:
    """Result of submitting an option exit order.

    Represents SUBMITTED state, not FILLED.

    Attributes
    ----------
    exit_reason : str
        "STOP" or "TARGET".
    underlying_stop_price : float
        Structural stop level that was monitored.
    underlying_target_price : float
        Structural target level that was monitored.
    trigger_bar_time_ms : int or None
        time_ms of the underlying bar that triggered the exit.
    con_id : int or None
        Option contract ID.
    right : str
        "C" or "P".
    expiration : str
        YYYYMMDD.
    strike : float
        Option strike.
    quantity : int
        Always 1.
    order_id : int
        Broker-assigned exit order ID.
    perm_id : int
        Broker-assigned permanent ID.
    status : str
        Initial broker status.
    trade : object
        Raw ib_insync Trade object for future fill monitoring.
    entry_order_id : int
        The entry order ID this exit corresponds to.
    """

    exit_reason: str
    underlying_stop_price: float
    underlying_target_price: float
    trigger_bar_time_ms: int | None
    con_id: int | None
    right: str
    expiration: str
    strike: float
    quantity: int
    order_id: int
    perm_id: int
    status: str
    trade: object
    entry_order_id: int


# ── Exit trigger to reason mapping ───────────────────────────────────────────

_TRIGGER_TO_REASON = {
    ExitState.STOP_TRIGGERED: "STOP",
    ExitState.TARGET_TRIGGERED: "TARGET",
}

_VALID_EXIT_STATES = frozenset(_TRIGGER_TO_REASON.keys())


# ── Executor ─────────────────────────────────────────────────────────────────


class OptionExitExecutor:
    """Submits SELL MARKET to close an open option position.

    Parameters
    ----------
    ib : ib_insync.IB
        Connected IB instance (injected).
    """

    def __init__(self, ib):
        self._ib = ib
        self._submitted_entry_ids: set[int] = set()

    def allow_resubmit(self, entry_order_id: int) -> None:
        """Clear duplicate protection for an entry, allowing retry.

        Used by the orchestrator when an exit order fails and needs
        to be re-submitted.  Only the orchestrator should call this,
        and only after confirming the previous exit order is
        CANCELLED/REJECTED (not pending or filled).
        """
        self._submitted_entry_ids.discard(entry_order_id)

    def submit_exit(
        self,
        qualified_contract,
        exit_trigger,
        *,
        entry_order_id: int,
        con_id: int | None = None,
        right: str = "",
        expiration: str = "",
        strike: float = 0.0,
        quantity: int = 1,
    ) -> ExitSubmissionResult:
        """Submit a SELL MARKET order to close the option position.

        Parameters
        ----------
        qualified_contract
            The exact qualified ib_insync.Option from the entry fill.
        exit_trigger : ExitTriggerResult
            Terminal result from UnderlyingExitMonitor.
        entry_order_id : int
            The entry order ID — used for duplicate protection.
        con_id : int or None
            Option contract ID for result metadata.
        right : str
            "C" or "P".
        expiration : str
            YYYYMMDD.
        strike : float
            Option strike.
        quantity : int
            Must be 1 for v0.1.

        Returns
        -------
        ExitSubmissionResult

        Raises
        ------
        ValueError
            On invalid trigger state, missing contract, wrong quantity,
            or duplicate submission.
        """
        # ── Validation ───────────────────────────────────────────────
        if exit_trigger.state not in _VALID_EXIT_STATES:
            raise ValueError(
                f"Exit trigger must be STOP_TRIGGERED or TARGET_TRIGGERED, "
                f"got {exit_trigger.state!r}"
            )

        if qualified_contract is None:
            raise ValueError("qualified_contract is required for exit")

        if quantity != 1:
            raise ValueError(f"Only quantity=1 supported, got {quantity}")

        # ── Duplicate protection ─────────────────────────────────────
        if entry_order_id in self._submitted_entry_ids:
            raise ValueError(
                f"Exit already submitted for entry_order_id={entry_order_id}"
            )

        # ── Build and submit ─────────────────────────────────────────
        order = MarketOrder(action="SELL", totalQuantity=1)

        trade = self._ib.placeOrder(qualified_contract, order)

        self._submitted_entry_ids.add(entry_order_id)

        # ── Result ───────────────────────────────────────────────────
        exit_order_id = trade.order.orderId if trade.order else 0
        exit_perm_id = trade.order.permId if trade.order else 0
        status = trade.orderStatus.status if trade.orderStatus else ""

        return ExitSubmissionResult(
            exit_reason=_TRIGGER_TO_REASON[exit_trigger.state],
            underlying_stop_price=exit_trigger.stop_price,
            underlying_target_price=exit_trigger.target_price,
            trigger_bar_time_ms=exit_trigger.trigger_bar_time_ms,
            con_id=con_id,
            right=right,
            expiration=expiration,
            strike=strike,
            quantity=1,
            order_id=exit_order_id,
            perm_id=exit_perm_id,
            status=status,
            trade=trade,
            entry_order_id=entry_order_id,
        )
