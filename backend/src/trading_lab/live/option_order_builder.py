"""Option entry order builder — BUY LIMIT spec for MaxBot v0.1.

Builds a deterministic order specification from a qualified
OptionSelectionResult.  Does not connect to IBKR.  Does not submit.

MaxBot v0.1 policy:
    action     = BUY (long premium only)
    order_type = LMT (limit at current ask)
    quantity   = 1
    no bracket, no option stop, no option target

Limit-price rule:
    limit_price = ask (from OptionSelectionResult market data)

Bid/ask validation:
    Both bid and ask must be present, finite, and > 0.
    ask must be >= bid.
    If any condition fails → explicit error, no fallback.

Spread metadata (observational, no threshold):
    spread     = ask - bid
    spread_pct = (ask - bid) / ask
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ── Result ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class OptionEntryOrderSpec:
    """Immutable specification for an option entry order.

    Attributes
    ----------
    action : str
        Always "BUY".
    order_type : str
        Always "LMT".
    quantity : int
        Always 1 for v0.1.
    limit_price : float
        Limit price (= current ask).
    bid : float
        Observed bid at time of spec creation.
    ask : float
        Observed ask at time of spec creation.
    spread : float
        Absolute spread (ask - bid).
    spread_pct : float
        Percentage spread ((ask - bid) / ask).
    underlying_symbol : str
        Strategy underlying (e.g. "QQQ").
    right : str
        "C" or "P".
    expiration : str
        YYYYMMDD.
    strike : float
        Option strike.
    exchange : str
        Option exchange.
    multiplier : str
        Contract multiplier (e.g. "100").
    con_id : int or None
        IBKR contract ID.
    qualified_contract : object or None
        The qualified ib_insync.Option object.
    """

    action: str
    order_type: str
    quantity: int
    limit_price: float
    bid: float
    ask: float
    spread: float
    spread_pct: float
    underlying_symbol: str
    right: str
    expiration: str
    strike: float
    exchange: str
    multiplier: str
    con_id: int | None = None
    qualified_contract: object | None = None


# ── Validation ───────────────────────────────────────────────────────────────

def _validate_price(value, name: str) -> float:
    """Validate a price is present, finite, and > 0."""
    if value is None:
        raise ValueError(f"{name} is not available (None)")
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric, got {type(value).__name__}")
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"{name} must be finite, got {value}")
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return float(value)


# ── Builder ──────────────────────────────────────────────────────────────────

def build_option_entry_order(selection) -> OptionEntryOrderSpec:
    """Build a BUY LIMIT order spec from an OptionSelectionResult.

    Parameters
    ----------
    selection : OptionSelectionResult
        Must contain valid bid and ask market data.

    Returns
    -------
    OptionEntryOrderSpec

    Raises
    ------
    ValueError
        If bid or ask is missing, non-finite, zero, or negative,
        or if ask < bid.
    """
    bid = _validate_price(selection.bid, "bid")
    ask = _validate_price(selection.ask, "ask")

    if ask < bid:
        raise ValueError(
            f"Invalid bid/ask: ask ({ask}) < bid ({bid})"
        )

    spread = round(ask - bid, 6)
    spread_pct = round((ask - bid) / ask, 6) if ask > 0 else 0.0

    return OptionEntryOrderSpec(
        action="BUY",
        order_type="LMT",
        quantity=1,
        limit_price=ask,
        bid=bid,
        ask=ask,
        spread=spread,
        spread_pct=spread_pct,
        underlying_symbol=selection.underlying_symbol,
        right=selection.right,
        expiration=selection.expiration,
        strike=selection.strike,
        exchange=selection.exchange,
        multiplier=selection.multiplier,
        con_id=selection.con_id,
        qualified_contract=selection.qualified_contract,
    )
