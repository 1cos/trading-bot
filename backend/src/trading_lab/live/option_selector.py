"""Option contract selector — deterministic v0.1 policy + IBKR adapter.

MaxBot v0.1 option selection policy:

    LONG  → BUY 1 CALL, 1 strike ITM (largest strike < underlying)
    SHORT → BUY 1 PUT,  1 strike ITM (smallest strike > underlying)

    Expiration:
      - 0DTE if today's date is in the chain's expirations
      - otherwise nearest future expiration

    Quantity: 1 contract (fixed)

Architecture:

    PURE POLICY (no network):
        select_expiration()  — deterministic expiration choice
        select_strike()      — deterministic strike choice

    IBKR ADAPTER (requires connection):
        OptionContractSelector.select()  — full workflow:
            qualify underlying → reqSecDefOptParams → policy →
            construct Option → qualifyContracts → optional mktdata

The pure policy functions are fully unit-testable without IBKR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


# ── Pure policy: expiration ──────────────────────────────────────────────────

def select_expiration(
    trading_date: str,
    available_expirations: list[str],
) -> str:
    """Choose expiration per v0.1 policy.

    Parameters
    ----------
    trading_date : str
        Current trading date in YYYYMMDD format.
    available_expirations : list[str]
        Expirations from the option chain, each YYYYMMDD.

    Returns
    -------
    str
        Selected expiration in YYYYMMDD format.

    Raises
    ------
    ValueError
        If no valid current or future expiration exists.
    """
    if not available_expirations:
        raise ValueError("No expirations available in option chain")

    # 0DTE: use today if available
    if trading_date in available_expirations:
        return trading_date

    # Nearest future expiration
    future = sorted(e for e in available_expirations if e > trading_date)
    if not future:
        raise ValueError(
            f"No valid expiration on or after {trading_date}; "
            f"available: {sorted(available_expirations)[-3:]}"
        )
    return future[0]


# ── Pure policy: strike ──────────────────────────────────────────────────────

def select_strike(
    right: str,
    underlying_price: float,
    available_strikes: list[float],
) -> float:
    """Choose strike per v0.1 policy (1 strike ITM).

    CALL: largest available strike strictly below underlying_price.
    PUT:  smallest available strike strictly above underlying_price.

    Parameters
    ----------
    right : str
        "C" for CALL, "P" for PUT.
    underlying_price : float
        Current underlying price.
    available_strikes : list[float]
        Strikes from the option chain.

    Returns
    -------
    float
        Selected strike.

    Raises
    ------
    ValueError
        If no valid ITM strike exists on the required side.
    """
    if not available_strikes:
        raise ValueError("No strikes available in option chain")

    if right == "C":
        # CALL: largest strike < underlying
        candidates = sorted(s for s in available_strikes if s < underlying_price)
        if not candidates:
            raise ValueError(
                f"No CALL strike below underlying {underlying_price}; "
                f"lowest available: {min(available_strikes)}"
            )
        return candidates[-1]  # largest below

    elif right == "P":
        # PUT: smallest strike > underlying
        candidates = sorted(s for s in available_strikes if s > underlying_price)
        if not candidates:
            raise ValueError(
                f"No PUT strike above underlying {underlying_price}; "
                f"highest available: {max(available_strikes)}"
            )
        return candidates[0]  # smallest above

    else:
        raise ValueError(f"right must be 'C' or 'P', got {right!r}")


# ── Selection result ─────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class OptionSelectionResult:
    """Result of option contract selection.

    All prices are underlying prices unless explicitly noted.
    No option-premium stop or target is generated.

    Attributes
    ----------
    underlying_symbol : str
        Strategy instrument (e.g. "QQQ").
    underlying_price : float
        Underlying price used for strike selection.
    right : str
        "C" (CALL) or "P" (PUT).
    expiration : str
        Selected expiration YYYYMMDD.
    strike : float
        Selected strike price.
    exchange : str
        Option exchange (e.g. "SMART").
    trading_class : str
        IBKR trading class from chain.
    multiplier : str
        Contract multiplier (e.g. "100").
    quantity : int
        Always 1 for v0.1.
    con_id : int or None
        IBKR contract ID after qualification (None if not qualified).
    qualified_contract : object or None
        The qualified ib_insync.Option object (None in pure-policy mode).
    bid : float or None
        Observed bid (None if market data unavailable).
    ask : float or None
        Observed ask (None if market data unavailable).
    spread : float or None
        ask - bid (None if either is unavailable).
    """

    underlying_symbol: str
    underlying_price: float
    right: str
    expiration: str
    strike: float
    exchange: str
    trading_class: str
    multiplier: str
    quantity: int
    con_id: int | None = None
    qualified_contract: object | None = None
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None


# ── IBKR adapter ─────────────────────────────────────────────────────────────

def _pick_chain(chains: list, prefer_exchange: str = "SMART") -> dict | None:
    """Select the best option chain from IBKR results.

    Prefers the chain whose exchange matches ``prefer_exchange``.
    Falls back to the first chain if no match.

    Parameters
    ----------
    chains : list
        List of OptionChain objects from reqSecDefOptParams.
    prefer_exchange : str
        Preferred exchange (default "SMART").

    Returns
    -------
    dict or None
        Selected chain as a dict with keys: exchange, tradingClass,
        multiplier, expirations, strikes.  None if chains is empty.
    """
    if not chains:
        return None

    selected = None
    for chain in chains:
        if chain.exchange == prefer_exchange:
            selected = chain
            break

    if selected is None:
        selected = chains[0]

    return {
        "exchange": selected.exchange,
        "tradingClass": selected.tradingClass,
        "multiplier": selected.multiplier,
        "expirations": list(selected.expirations),
        "strikes": list(selected.strikes),
    }


class OptionContractSelector:
    """IBKR adapter for option contract selection.

    Wraps the pure policy functions with actual IBKR calls.

    Parameters
    ----------
    ib : ib_insync.IB
        Connected IB instance.
    """

    def __init__(self, ib):
        self._ib = ib

    def select(
        self,
        underlying_symbol: str,
        right: str,
        underlying_price: float,
        trading_date: str,
        *,
        exchange: str = "SMART",
        currency: str = "USD",
        fetch_market_data: bool = False,
    ) -> OptionSelectionResult:
        """Select an option contract using v0.1 policy.

        Parameters
        ----------
        underlying_symbol : str
            e.g. "QQQ", "SPY".
        right : str
            "C" for CALL, "P" for PUT.
        underlying_price : float
            Current underlying price for strike selection.
        trading_date : str
            Current trading date YYYYMMDD.
        exchange : str
            Preferred exchange (default "SMART").
        currency : str
            Currency (default "USD").
        fetch_market_data : bool
            If True, request snapshot bid/ask for the selected contract.

        Returns
        -------
        OptionSelectionResult

        Raises
        ------
        ValueError
            On policy failures (no valid expiration, no valid strike).
        RuntimeError
            On IBKR failures (qualification, chain retrieval).
        """
        from ib_insync import Stock, Option

        # 1. Qualify underlying
        stock = Stock(underlying_symbol, exchange, currency)
        qualified = self._ib.qualifyContracts(stock)
        if not qualified:
            raise RuntimeError(
                f"Failed to qualify underlying {underlying_symbol}"
            )
        underlying_con_id = stock.conId

        # 2. Request option chain
        chains = self._ib.reqSecDefOptParams(
            underlying_symbol, "", "STK", underlying_con_id
        )
        if not chains:
            raise RuntimeError(
                f"No option chains returned for {underlying_symbol}"
            )

        chain = _pick_chain(chains, prefer_exchange=exchange)
        if chain is None:
            raise RuntimeError(
                f"No usable option chain for {underlying_symbol}"
            )

        # 3. Apply pure policy
        expiration = select_expiration(trading_date, chain["expirations"])
        strike = select_strike(right, underlying_price, chain["strikes"])

        # 4. Construct and qualify option contract
        option = Option(
            underlying_symbol,
            expiration,
            strike,
            right,
            chain["exchange"],
            chain["multiplier"],
            currency,
        )
        option.tradingClass = chain["tradingClass"]

        qual_result = self._ib.qualifyContracts(option)
        if not qual_result:
            raise RuntimeError(
                f"Failed to qualify option: {underlying_symbol} "
                f"{expiration} {strike} {right}"
            )

        # 5. Optional market data
        bid = None
        ask = None
        spread = None
        if fetch_market_data:
            ticker = self._ib.reqMktData(option, "106", snapshot=True)
            self._ib.sleep(2)  # allow snapshot to arrive
            if ticker.bid is not None and ticker.bid > 0:
                bid = float(ticker.bid)
            if ticker.ask is not None and ticker.ask > 0:
                ask = float(ticker.ask)
            if bid is not None and ask is not None:
                spread = round(ask - bid, 4)
            self._ib.cancelMktData(option)

        return OptionSelectionResult(
            underlying_symbol=underlying_symbol,
            underlying_price=underlying_price,
            right=right,
            expiration=expiration,
            strike=strike,
            exchange=chain["exchange"],
            trading_class=chain["tradingClass"],
            multiplier=chain["multiplier"],
            quantity=1,
            con_id=option.conId if option.conId else None,
            qualified_contract=option if option.conId else None,
            bid=bid,
            ask=ask,
            spread=spread,
        )
