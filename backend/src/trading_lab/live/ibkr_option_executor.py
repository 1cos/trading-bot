"""IBKR option entry executor — submits BUY LIMIT for MaxBot v0.1.

Translates an OptionEntryOrderSpec into a single ib_insync LimitOrder
and submits it via the injected IB instance.

This module does NOT:
    - create or manage the IB connection (injected)
    - select option contracts (already done by T7)
    - monitor fills (future task)
    - monitor underlying stop/target (future task)
    - build brackets or child orders
    - retry or reprice

The IB instance determines whether orders go to Paper or Live.
This module is account-agnostic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ib_insync import LimitOrder


# ── Submission result ────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class EntrySubmissionResult:
    """Result of submitting an option entry order.

    Represents SUBMITTED state, not FILLED.
    Fill monitoring is a separate concern.

    Attributes
    ----------
    underlying_symbol : str
        Strategy underlying (e.g. "QQQ").
    con_id : int or None
        IBKR option contract ID.
    right : str
        "C" or "P".
    expiration : str
        YYYYMMDD.
    strike : float
        Option strike.
    quantity : int
        Always 1.
    limit_price : float
        Submitted limit price.
    order_id : int
        Broker-assigned order ID (from Trade.order.orderId).
    perm_id : int
        Broker-assigned permanent ID (from Trade.order.permId).
    status : str
        Initial broker status (e.g. "PendingSubmit", "Submitted").
    trade : object
        Raw ib_insync Trade object for fill monitoring.
    """

    underlying_symbol: str
    con_id: int | None
    right: str
    expiration: str
    strike: float
    quantity: int
    limit_price: float
    order_id: int
    perm_id: int
    status: str
    trade: object


# ── Validation ───────────────────────────────────────────────────────────────

def _validate_spec(spec) -> None:
    """Validate OptionEntryOrderSpec before broker submission."""
    if spec.action != "BUY":
        raise ValueError(
            f"Only BUY is supported, got action={spec.action!r}"
        )
    if spec.order_type != "LMT":
        raise ValueError(
            f"Only LMT is supported, got order_type={spec.order_type!r}"
        )
    if spec.quantity != 1:
        raise ValueError(
            f"Only quantity=1 is supported, got {spec.quantity}"
        )
    if spec.limit_price is None or not math.isfinite(spec.limit_price):
        raise ValueError(
            f"limit_price must be finite, got {spec.limit_price}"
        )
    if spec.limit_price <= 0:
        raise ValueError(
            f"limit_price must be > 0, got {spec.limit_price}"
        )
    if spec.qualified_contract is None:
        raise ValueError(
            "qualified_contract is required for submission"
        )


# ── Executor ─────────────────────────────────────────────────────────────────


class IBKROptionExecutor:
    """IBKR option entry order executor.

    Submits a single BUY LIMIT order for a qualified option contract.

    Parameters
    ----------
    ib : ib_insync.IB
        Connected IB instance (injected, not created here).
    """

    def __init__(self, ib):
        self._ib = ib

    def submit_entry(self, order_spec) -> EntrySubmissionResult:
        """Submit a BUY LIMIT option entry order.

        Parameters
        ----------
        order_spec : OptionEntryOrderSpec
            Must have a qualified_contract and valid limit_price.

        Returns
        -------
        EntrySubmissionResult
            Represents SUBMITTED state.  Does not wait for fill.

        Raises
        ------
        ValueError
            If the spec fails pre-submission validation.
        RuntimeError
            If placeOrder fails or returns an unexpected result.
        """
        _validate_spec(order_spec)

        contract = order_spec.qualified_contract
        order = LimitOrder(
            action="BUY",
            totalQuantity=1,
            lmtPrice=order_spec.limit_price,
            tif="DAY",
            openClose="O",  # O = opening a new position
        )

        trade = self._ib.placeOrder(contract, order)

        # Extract broker-assigned IDs and status
        order_id = trade.order.orderId if trade.order else 0
        perm_id = trade.order.permId if trade.order else 0
        status = trade.orderStatus.status if trade.orderStatus else ""

        return EntrySubmissionResult(
            underlying_symbol=order_spec.underlying_symbol,
            con_id=order_spec.con_id,
            right=order_spec.right,
            expiration=order_spec.expiration,
            strike=order_spec.strike,
            quantity=1,
            limit_price=order_spec.limit_price,
            order_id=order_id,
            perm_id=perm_id,
            status=status,
            trade=trade,
        )
