"""Exit fill monitor — confirms option exit fill for MaxBot v0.1.

Inspects the ib_insync Trade object from an ExitSubmissionResult to
determine when the SELL MARKET exit order is fully filled.

Only after confirmed full exit fill:
    TARGET → record_trade_result(WIN)
    STOP   → record_trade_result(LOSS)

WIN/LOSS is determined by the structural exit reason from T11/T12,
NOT by option premium P&L.

Reuses the same conventions as T10 (entry fill monitor):
    check_exit_fill()     — pure fill inspection
    ExitResultActivator   — idempotent DailyTradeManager mutation

Does NOT:
    - submit orders
    - connect to IBKR
    - retry cancelled/rejected exits
    - calculate realized P&L
    - use premium P&L for strategy result
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from trading_lab.live.trade_manager import TradeResult


# ── Fill state (mirrors T10) ─────────────────────────────────────────────────

@unique
class ExitFillState(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


_STATUS_MAP = {
    "PendingSubmit": ExitFillState.PENDING,
    "ApiPending": ExitFillState.PENDING,
    "PreSubmitted": ExitFillState.PENDING,
    "Submitted": ExitFillState.PENDING,
    "Filled": ExitFillState.FILLED,
    "Cancelled": ExitFillState.CANCELLED,
    "ApiCancelled": ExitFillState.CANCELLED,
    "PendingCancel": ExitFillState.CANCELLED,
    "Inactive": ExitFillState.REJECTED,
}

# Exit reason → trade result
_EXIT_REASON_MAP = {
    "TARGET": TradeResult.WIN,
    "STOP": TradeResult.LOSS,
}


# ── Exit fill result ─────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ExitFillResult:
    """Immutable result of checking an exit order's fill state.

    Attributes
    ----------
    state : ExitFillState
        PENDING, FILLED, CANCELLED, or REJECTED.
    exit_reason : str
        "STOP" or "TARGET" (from ExitSubmissionResult).
    entry_order_id : int
        The entry order this exit corresponds to.
    exit_order_id : int
        Broker exit order ID.
    con_id : int or None
        Option contract ID.
    filled_quantity : float
        Quantity filled so far.
    remaining_quantity : float
        Quantity remaining.
    average_exit_fill_price : float or None
        Option premium received on exit (None if not filled).
    broker_status : str
        Raw IBKR status string.
    fill_time : object
        Fill timestamp (None if not filled).
    underlying_stop_price : float
        Structural stop level (preserved for provenance).
    underlying_target_price : float
        Structural target level (preserved for provenance).
    """

    state: ExitFillState
    exit_reason: str
    entry_order_id: int
    exit_order_id: int
    con_id: int | None
    filled_quantity: float
    remaining_quantity: float
    average_exit_fill_price: float | None
    broker_status: str
    fill_time: object
    underlying_stop_price: float
    underlying_target_price: float


# ── Pure fill inspection ─────────────────────────────────────────────────────

def check_exit_fill(
    exit_submission,
    requested_quantity: int = 1,
) -> ExitFillResult:
    """Inspect the Trade object from an ExitSubmissionResult.

    Parameters
    ----------
    exit_submission : ExitSubmissionResult
        Must have a ``trade`` attribute with ib_insync Trade semantics.
    requested_quantity : int
        Expected total quantity (default 1).

    Returns
    -------
    ExitFillResult
    """
    trade = exit_submission.trade
    order_status = trade.orderStatus
    broker_status = order_status.status if order_status else ""
    filled = order_status.filled if order_status else 0.0
    remaining = order_status.remaining if order_status else float(requested_quantity)
    avg_price = order_status.avgFillPrice if order_status else 0.0

    exit_order_id = trade.order.orderId if trade.order else 0

    # Map state
    mapped = _STATUS_MAP.get(broker_status, ExitFillState.PENDING)

    # Quantity-based confirmation
    if mapped == ExitFillState.FILLED and filled < requested_quantity:
        mapped = ExitFillState.PENDING

    fill_price = avg_price if mapped == ExitFillState.FILLED and avg_price > 0 else None

    fill_time = None
    if mapped == ExitFillState.FILLED and trade.fills:
        last_fill = trade.fills[-1]
        fill_time = getattr(last_fill, "time", None)

    return ExitFillResult(
        state=mapped,
        exit_reason=exit_submission.exit_reason,
        entry_order_id=exit_submission.entry_order_id,
        exit_order_id=exit_order_id,
        con_id=exit_submission.con_id,
        filled_quantity=filled,
        remaining_quantity=remaining,
        average_exit_fill_price=fill_price,
        broker_status=broker_status,
        fill_time=fill_time,
        underlying_stop_price=exit_submission.underlying_stop_price,
        underlying_target_price=exit_submission.underlying_target_price,
    )


# ── DailyTradeManager activation (idempotent) ───────────────────────────────

class ExitResultActivator:
    """Idempotent bridge between exit fill and DailyTradeManager.

    Maps:
        TARGET filled → record_trade_result(WIN)
        STOP filled   → record_trade_result(LOSS)

    Uses exit_order_id for idempotency — the same exit fill
    never records WIN/LOSS twice.

    Parameters
    ----------
    trade_manager : DailyTradeManager
        The daily trade state manager.
    """

    def __init__(self, trade_manager):
        self._manager = trade_manager
        self._applied_exit_ids: set[int] = set()

    def apply_if_filled(self, fill_result: ExitFillResult) -> bool:
        """If exit is FILLED and not yet applied, record WIN or LOSS.

        Parameters
        ----------
        fill_result : ExitFillResult

        Returns
        -------
        bool
            True if record_trade_result() was called in this invocation.
        """
        if fill_result.state != ExitFillState.FILLED:
            return False

        if fill_result.exit_order_id in self._applied_exit_ids:
            return False

        trade_result = _EXIT_REASON_MAP.get(fill_result.exit_reason)
        if trade_result is None:
            raise ValueError(
                f"Unknown exit_reason: {fill_result.exit_reason!r}"
            )

        self._manager.record_trade_result(trade_result)
        self._applied_exit_ids.add(fill_result.exit_order_id)
        return True
