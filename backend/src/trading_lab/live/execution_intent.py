"""Options execution intent — MaxBot v0.1 execution architecture.

MaxBot generates strategy signals on an **underlying instrument**
(e.g. QQQ, SPY, NVDA) but executes via **option contracts**.

This module models the intent to execute an option trade based on
a strategy signal, without selecting the specific option contract.

Key architectural distinction:

    STRATEGY INSTRUMENT (underlying)
        QQQ at 585.20 — generates ORB, break, displacement, retest,
        rejection, entry/stop/target.

    EXECUTION INSTRUMENT (option)
        QQQ CALL or PUT — the actual contract traded through IBKR.

The prices in TradePlan are UNDERLYING prices and must never be
interpreted as option premium prices.

    underlying_entry_price  = level at which the underlying triggers entry
    underlying_stop_price   = level at which the underlying triggers stop
    underlying_target_price = level at which the underlying triggers target

The broker execution layer will later:
    1. Select a specific option contract (strike, expiration, delta).
    2. Buy the option when entry is triggered.
    3. Close the option when underlying reaches stop or target.

Direction mapping:
    LONG underlying signal  → BUY CALL
    SHORT underlying signal → BUY PUT

MaxBot v0.1 buys premium only.  No option selling.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique


# ── Enums ────────────────────────────────────────────────────────────────────

@unique
class ExecutionInstrumentType(StrEnum):
    """Type of instrument actually traded."""
    OPTION = "OPTION"


@unique
class OptionRight(StrEnum):
    """Option right (call or put)."""
    CALL = "CALL"
    PUT = "PUT"


@unique
class OptionAction(StrEnum):
    """Option trade action.  MaxBot v0.1: buy premium only."""
    BUY = "BUY"


# ── Underlying trigger levels ────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class UnderlyingTriggerLevels:
    """Structural price levels on the underlying instrument.

    These are NOT option premium prices.  They are the underlying
    prices at which the execution layer should act.

    Attributes
    ----------
    entry_price : Decimal
        Underlying price that triggers option entry.
    stop_price : Decimal
        Underlying price that triggers position close (stop).
    target_price : Decimal
        Underlying price that triggers position close (target).
    """

    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal


# ── Option execution intent ─────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class OptionExecutionIntent:
    """Intent to execute an option trade based on a strategy signal.

    This is the bridge between the strategy layer (which operates on
    underlying prices) and the broker execution layer (which will
    select and trade an option contract).

    Attributes
    ----------
    execution_type : ExecutionInstrumentType
        Always OPTION for MaxBot v0.1.
    underlying_symbol : str
        The instrument the strategy analyzed (e.g. "QQQ").
    direction : str
        Strategy direction: "LONG" or "SHORT".
    option_right : OptionRight
        CALL (for LONG) or PUT (for SHORT).
    option_action : OptionAction
        Always BUY for MaxBot v0.1 (long premium only).
    underlying_triggers : UnderlyingTriggerLevels
        Structural entry/stop/target on the underlying.
    trade_plan : object
        The full TradePlan/v1 from the strategy (preserved for
        provenance; prices are underlying prices).
    detection_result : object or None
        The DetectionResult/v1 if available (for logging/provenance).
    """

    execution_type: ExecutionInstrumentType
    underlying_symbol: str
    direction: str
    option_right: OptionRight
    option_action: OptionAction
    underlying_triggers: UnderlyingTriggerLevels
    trade_plan: object
    detection_result: object | None = None


# ── Builder ──────────────────────────────────────────────────────────────────

def _require_finite(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")


def build_option_execution_intent(
    trade_plan,
    underlying_symbol: str,
    direction: str,
    *,
    exit_target_r: int = 2,
    detection_result=None,
) -> OptionExecutionIntent:
    """Build an option execution intent from a strategy TradePlan.

    Parameters
    ----------
    trade_plan
        TradePlan/v1 from the strategy pipeline.  All prices are
        underlying prices.
    underlying_symbol : str
        The instrument the strategy analyzed (e.g. "QQQ").
    direction : str
        "LONG" or "SHORT".
    exit_target_r : int
        Which R-target to use (2, 3, or 4).  Default 2.
    detection_result
        Optional DetectionResult/v1 for provenance.

    Returns
    -------
    OptionExecutionIntent

    Raises
    ------
    ValueError
        On invalid direction, non-finite prices, or invalid R target.
    """
    # Direction → option right
    if direction == "LONG":
        option_right = OptionRight.CALL
    elif direction == "SHORT":
        option_right = OptionRight.PUT
    else:
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")

    # Extract underlying prices from trade plan
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

    # Validate finite
    _require_finite(entry_price, "underlying_entry_price")
    _require_finite(stop_price, "underlying_stop_price")
    _require_finite(target_price, "underlying_target_price")

    triggers = UnderlyingTriggerLevels(
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
    )

    return OptionExecutionIntent(
        execution_type=ExecutionInstrumentType.OPTION,
        underlying_symbol=underlying_symbol,
        direction=direction,
        option_right=option_right,
        option_action=OptionAction.BUY,
        underlying_triggers=triggers,
        trade_plan=trade_plan,
        detection_result=detection_result,
    )
