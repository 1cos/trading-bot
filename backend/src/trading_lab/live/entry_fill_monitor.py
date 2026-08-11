"""Entry fill monitor — detects option entry fill for MaxBot v0.1.

Inspects an ib_insync Trade object to determine whether the entry
order has been filled, cancelled, or is still pending.

Only after a confirmed FULL fill may the daily trade manager record
the trade as opened.

Fill detection rule:
    FILLED when orderStatus.status == "Filled"
    AND orderStatus.filled >= requested quantity (1)

Idempotency:
    Uses orderId to track which fills have already been applied.
    Repeated checks of the same filled order never double-count.

Does NOT:
    - connect to IBKR
    - submit or cancel orders
    - retry or reprice
    - monitor underlying stop/target
    - submit exit orders
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique


# ── Fill state ───────────────────────────────────────────────────────────────

@unique
class FillState(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# IBKR status → MaxBot fill state mapping
_STATUS_MAP = {
    "PendingSubmit": FillState.PENDING,
    "ApiPending": FillState.PENDING,
    "PreSubmitted": FillState.PENDING,
    "Submitted": FillState.PENDING,
    "Filled": FillState.FILLED,  # confirmed below with quantity check
    "Cancelled": FillState.CANCELLED,
    "ApiCancelled": FillState.CANCELLED,
    "PendingCancel": FillState.CANCELLED,
    "Inactive": FillState.REJECTED,
}


# ── Fill result ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class EntryFillResult:
    """Immutable result of checking an entry order's fill state.

    Attributes
    ----------
    state : FillState
        PENDING, FILLED, CANCELLED, or REJECTED.
    order_id : int
        Broker order ID.
    con_id : int or None
        Option contract ID.
    filled_quantity : float
        Quantity filled so far.
    remaining_quantity : float
        Quantity remaining.
    average_fill_price : float or None
        Average option premium fill price (None if not filled).
    broker_status : str
        Raw IBKR status string.
    fill_time : object
        Fill timestamp from execution (None if not filled).
    """

    state: FillState
    order_id: int
    con_id: int | None
    filled_quantity: float
    remaining_quantity: float
    average_fill_price: float | None
    broker_status: str
    fill_time: object


# ── Pure fill inspection ─────────────────────────────────────────────────────

def check_fill(submission_result, requested_quantity: int = 1) -> EntryFillResult:
    """Inspect the Trade object from an EntrySubmissionResult.

    Parameters
    ----------
    submission_result : EntrySubmissionResult
        Must have a ``trade`` attribute with ib_insync Trade semantics.
    requested_quantity : int
        Expected total quantity (default 1 for v0.1).

    Returns
    -------
    EntryFillResult
    """
    trade = submission_result.trade
    order_status = trade.orderStatus
    broker_status = order_status.status if order_status else ""
    filled = order_status.filled if order_status else 0.0
    remaining = order_status.remaining if order_status else float(requested_quantity)
    avg_price = order_status.avgFillPrice if order_status else 0.0

    order_id = trade.order.orderId if trade.order else 0
    con_id = submission_result.con_id

    # Determine state
    mapped = _STATUS_MAP.get(broker_status, FillState.PENDING)

    # Quantity-based fill confirmation: only FILLED if full quantity
    if mapped == FillState.FILLED and filled < requested_quantity:
        mapped = FillState.PENDING  # partial fill, not done yet

    # Fill price: only meaningful when actually filled
    fill_price = avg_price if mapped == FillState.FILLED and avg_price > 0 else None

    # Fill time from executions
    fill_time = None
    if mapped == FillState.FILLED and trade.fills:
        last_fill = trade.fills[-1]
        fill_time = getattr(last_fill, "time", None)

    return EntryFillResult(
        state=mapped,
        order_id=order_id,
        con_id=con_id,
        filled_quantity=filled,
        remaining_quantity=remaining,
        average_fill_price=fill_price,
        broker_status=broker_status,
        fill_time=fill_time,
    )


# ── DailyTradeManager activation (idempotent) ───────────────────────────────

class FillActivator:
    """Idempotent bridge between fill detection and DailyTradeManager.

    Tracks which order IDs have already been applied so repeated
    checks of the same fill never double-count.

    Parameters
    ----------
    trade_manager : DailyTradeManager
        The daily trade state manager.
    """

    def __init__(self, trade_manager):
        self._manager = trade_manager
        self._applied_order_ids: set[int] = set()

    def apply_if_filled(self, fill_result: EntryFillResult) -> bool:
        """If the fill result indicates FILLED and hasn't been applied
        yet, call record_trade_open() on the trade manager.

        Parameters
        ----------
        fill_result : EntryFillResult
            Result from ``check_fill()``.

        Returns
        -------
        bool
            True if record_trade_open() was called in this invocation.
            False if not filled, or already applied.
        """
        if fill_result.state != FillState.FILLED:
            return False

        if fill_result.order_id in self._applied_order_ids:
            return False  # idempotent — already counted

        self._manager.record_trade_open()
        self._applied_order_ids.add(fill_result.order_id)
        return True
