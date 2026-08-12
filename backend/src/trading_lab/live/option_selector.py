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
    preferred_strike: float | None = None
    fallback_attempts: int = 0


# ── IBKR adapter ─────────────────────────────────────────────────────────────

def _pick_chain(chains: list, underlying_symbol: str = "",
                underlying_price: float = 0.0, trading_date: str = "",
                prefer_exchange: str = "SMART") -> dict | None:
    """Select the best standard option chain from IBKR results.

    Prefers the standard chain whose tradingClass matches the underlying
    symbol, has multiplier "100", and brackets the underlying price.

    Ranking:
      1. tradingClass == underlying_symbol (case-insensitive)
      2. multiplier == "100"
      3. exchange == prefer_exchange
      4. strikes bracket underlying_price (at least 1 below + 1 above)
      5. at least 1 expiration >= trading_date
      6. among ties: more expirations and strikes win

    Adjusted classes (e.g. 2QQQ, 2SPY) never beat the standard class.

    Parameters
    ----------
    chains : list
        OptionChain objects from reqSecDefOptParams.
    underlying_symbol : str
        The underlying symbol for tradingClass matching.
    underlying_price : float
        Current underlying price for strike-bracket validation.
    trading_date : str
        YYYYMMDD for expiration validation.
    prefer_exchange : str
        Preferred exchange (default "SMART").

    Returns
    -------
    dict or None
    """
    if not chains:
        return None

    def _score(chain):
        tc = chain.tradingClass.upper()
        sym = underlying_symbol.upper()
        mult = str(chain.multiplier)
        strikes = list(chain.strikes)
        expirations = list(chain.expirations)

        # Hard filter: must have multiplier 100
        if mult != "100":
            return (-1,)

        # Must have at least one future expiration
        future_exps = [e for e in expirations if e >= trading_date]
        if not future_exps:
            return (-1,)

        # Must bracket underlying price
        has_below = any(s < underlying_price for s in strikes)
        has_above = any(s > underlying_price for s in strikes)
        if not (has_below and has_above):
            return (-1,)

        # Score components (higher = better)
        tc_match = 1 if tc == sym else 0
        exch_match = 1 if chain.exchange == prefer_exchange else 0
        exp_count = len(future_exps)
        strike_count = len(strikes)

        return (tc_match, exch_match, exp_count, strike_count)

    scored = []
    for chain in chains:
        s = _score(chain)
        if s[0] >= 0:  # passed hard filters
            scored.append((s, chain))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]

    return {
        "exchange": best.exchange,
        "tradingClass": best.tradingClass,
        "multiplier": best.multiplier,
        "expirations": list(best.expirations),
        "strikes": list(best.strikes),
    }


def _fallback_strikes(right: str, preferred: float, underlying_price: float,
                       available_strikes: list[float]) -> list[float]:
    """Generate fallback ITM strikes deeper than preferred.

    Never crosses the ATM/OTM boundary.

    CALL: strikes progressively lower than preferred, all < underlying_price.
    PUT:  strikes progressively higher than preferred, all > underlying_price.

    Returns list sorted by proximity to preferred (nearest first).
    """
    if right == "C":
        candidates = sorted(
            (s for s in available_strikes if s < preferred),
            reverse=True,
        )
    elif right == "P":
        candidates = sorted(
            s for s in available_strikes if s > preferred
        )
    else:
        return []
    return candidates


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
        from ib_insync import Stock

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

        chain = _pick_chain(
            chains,
            underlying_symbol=underlying_symbol,
            underlying_price=underlying_price,
            trading_date=trading_date,
            prefer_exchange=exchange,
        )
        if chain is None:
            raise RuntimeError(
                f"No usable standard option chain for {underlying_symbol}"
            )

        # 3. Apply pure policy
        expiration = select_expiration(trading_date, chain["expirations"])
        preferred_strike = select_strike(right, underlying_price, chain["strikes"])

        # 4. Construct and qualify option contract with fallback
        from ib_insync import Option

        fallback_attempts = 0
        strike = preferred_strike
        option = None

        # Try preferred strike first, then fallback deeper ITM
        strikes_to_try = [preferred_strike] + _fallback_strikes(
            right, preferred_strike, underlying_price, chain["strikes"],
        )

        for candidate_strike in strikes_to_try:
            opt = Option(
                underlying_symbol,
                expiration,
                candidate_strike,
                right,
                chain["exchange"],
                chain["multiplier"],
                currency,
            )
            opt.tradingClass = chain["tradingClass"]

            try:
                qual_result = self._ib.qualifyContracts(opt)
                if qual_result and opt.conId:
                    option = opt
                    strike = candidate_strike
                    break
            except Exception:
                pass

            if candidate_strike != preferred_strike:
                fallback_attempts += 1

        if option is None:
            raise RuntimeError(
                f"Failed to qualify any ITM option: {underlying_symbol} "
                f"{expiration} {right}, preferred={preferred_strike}, "
                f"tried {fallback_attempts} fallbacks"
            )

        # 5. Optional market data (snapshot, no generic ticks)
        bid = None
        ask = None
        spread = None
        if fetch_market_data:
            ticker = self._ib.reqMktData(option, "", snapshot=True)
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
            preferred_strike=preferred_strike,
            fallback_attempts=fallback_attempts,
        )
