"""Bracket order builder — broker-agnostic order specification for MaxBot v0.1.

**DIRECT-INSTRUMENT execution model only.**

This module builds bracket orders where the traded instrument IS the
strategy instrument (e.g. buy/sell SPY shares directly).  All prices
are direct instrument prices.

For MaxBot v0.1 OPTIONS execution, use ``execution_intent.py`` instead.
The option execution path treats TradePlan prices as underlying trigger
levels and must NOT pass them to ``build_bracket_order()`` as if they
were option premium prices.

Translates a TradePlan into a deterministic BracketOrderSpec containing
entry, take-profit, and stop-loss legs.  No broker connection, no order
IDs, no submission.

Entry order type: LMT (limit) at the trade-plan entry price.
Rationale: the CONFIRMATION_CLOSE entry model defines a known price.
A limit order at that price provides deterministic paper fills and
avoids slippage ambiguity.

Take-profit: LMT at the trade-plan target price.
Stop-loss: STP at the trade-plan stop price.

Transmit intent for IBKR bracket sequencing:
    entry       → transmit=False (held until children attached)
    take_profit → transmit=False (attached to parent)
    stop_loss   → transmit=True  (final child transmits the group)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique


# ── Enums ────────────────────────────────────────────────────────────────────

@unique
class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@unique
class OrderType(StrEnum):
    LMT = "LMT"
    STP = "STP"


@unique
class LegRole(StrEnum):
    ENTRY = "ENTRY"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"


# ── Order leg spec ───────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class OrderLegSpec:
    """Specification for a single order leg.

    Attributes
    ----------
    role : LegRole
        ENTRY, TAKE_PROFIT, or STOP_LOSS.
    action : Action
        BUY or SELL.
    order_type : OrderType
        LMT or STP.
    price : Decimal
        Limit price (for LMT) or stop price (for STP).
    quantity : int
        Number of shares/contracts.
    transmit : bool
        IBKR transmit flag for bracket sequencing.
    """

    role: LegRole
    action: Action
    order_type: OrderType
    price: Decimal
    quantity: int
    transmit: bool


# ── Bracket order spec ───────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class BracketOrderSpec:
    """Complete bracket order specification.

    Attributes
    ----------
    direction : str
        "LONG" or "SHORT".
    symbol : str
        Instrument symbol.
    quantity : int
        Shares/contracts per leg.
    entry : OrderLegSpec
        Parent entry order.
    take_profit : OrderLegSpec
        Take-profit child order.
    stop_loss : OrderLegSpec
        Stop-loss child order.
    """

    direction: str
    symbol: str
    quantity: int
    entry: OrderLegSpec
    take_profit: OrderLegSpec
    stop_loss: OrderLegSpec


# ── Validation helpers ───────────────────────────────────────────────────────

def _require_finite_decimal(value: Decimal, name: str) -> None:
    """Raise ValueError if value is not a finite Decimal."""
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")


# ── Builder ──────────────────────────────────────────────────────────────────

def build_bracket_order(
    trade_plan,
    symbol: str,
    quantity: int,
    direction: str,
    *,
    exit_target_r: int = 2,
) -> BracketOrderSpec:
    """Build a bracket order spec from an existing TradePlan.

    Parameters
    ----------
    trade_plan
        TradePlan/v1 instance from ``build_trade_plan()``.
    symbol : str
        Instrument symbol (e.g. "SPY").
    quantity : int
        Number of shares/contracts (must be > 0).
    direction : str
        "LONG" or "SHORT".
    exit_target_r : int
        Which R-target to use (2, 3, or 4). Default 2.

    Returns
    -------
    BracketOrderSpec

    Raises
    ------
    ValueError
        On invalid geometry, quantity, direction, or non-finite prices.
    """
    # ── Validate direction ───────────────────────────────────────────────
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")

    # ── Validate quantity ────────────────────────────────────────────────
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise TypeError(f"quantity must be an int, got {type(quantity).__name__}")
    if quantity <= 0:
        raise ValueError(f"quantity must be > 0, got {quantity}")

    # ── Extract prices from trade plan ───────────────────────────────────
    entry_price = trade_plan.entry_price.to_price()
    stop_price = trade_plan.stop_price.to_price()

    target_map = {
        2: trade_plan.r2_price,
        3: trade_plan.r3_price,
        4: trade_plan.r4_price,
    }
    target_pt = target_map.get(exit_target_r)
    if target_pt is None:
        raise ValueError(f"exit_target_r must be 2, 3, or 4, got {exit_target_r}")
    target_price = target_pt.to_price()

    # ── Validate prices are finite ───────────────────────────────────────
    _require_finite_decimal(entry_price, "entry_price")
    _require_finite_decimal(stop_price, "stop_price")
    _require_finite_decimal(target_price, "target_price")

    # ── Validate geometric relationship ──────────────────────────────────
    if direction == "LONG":
        if not (stop_price < entry_price < target_price):
            raise ValueError(
                f"LONG bracket requires stop < entry < target, "
                f"got stop={stop_price} entry={entry_price} target={target_price}"
            )
        entry_action = Action.BUY
        exit_action = Action.SELL
    else:
        if not (target_price < entry_price < stop_price):
            raise ValueError(
                f"SHORT bracket requires target < entry < stop, "
                f"got target={target_price} entry={entry_price} stop={stop_price}"
            )
        entry_action = Action.SELL
        exit_action = Action.BUY

    # ── Build leg specs ──────────────────────────────────────────────────
    entry_leg = OrderLegSpec(
        role=LegRole.ENTRY,
        action=entry_action,
        order_type=OrderType.LMT,
        price=entry_price,
        quantity=quantity,
        transmit=False,
    )

    tp_leg = OrderLegSpec(
        role=LegRole.TAKE_PROFIT,
        action=exit_action,
        order_type=OrderType.LMT,
        price=target_price,
        quantity=quantity,
        transmit=False,
    )

    sl_leg = OrderLegSpec(
        role=LegRole.STOP_LOSS,
        action=exit_action,
        order_type=OrderType.STP,
        price=stop_price,
        quantity=quantity,
        transmit=True,
    )

    return BracketOrderSpec(
        direction=direction,
        symbol=symbol,
        quantity=quantity,
        entry=entry_leg,
        take_profit=tp_leg,
        stop_loss=sl_leg,
    )
