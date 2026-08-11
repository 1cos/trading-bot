"""Daily trade manager — in-memory daily state for MaxBot v0.1 Paper.

Enforces two rules:
    Rule A — Maximum 2 trades per trading day.
    Rule B — After 1 winning trade, trading is finished for the day.

Additional safety:
    Only one active (open) trade at a time.
    Signals/evaluations do not consume trade count — only explicit
    ``record_trade_open()`` increments the counter.

Broker-independent.  Strategy-independent.  No outcome simulation.
No persistence.  No IBKR dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique


# ── Trade result ─────────────────────────────────────────────────────────────

@unique
class TradeResult(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"


# ── State snapshot ───────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class DailyState:
    """Read-only snapshot of the daily trade manager state."""

    trading_date: str | None
    trades_used: int
    wins: int
    losses: int
    has_active_trade: bool
    day_finished: bool
    can_trade: bool


# ── Constants ────────────────────────────────────────────────────────────────

MAX_DAILY_TRADES = 2


# ── DailyTradeManager ───────────────────────────────────────────────────────


class DailyTradeManager:
    """In-memory daily trade state enforcing MaxBot v0.1 rules.

    Parameters
    ----------
    max_trades : int
        Maximum trades per day (default 2).
    """

    def __init__(self, max_trades: int = MAX_DAILY_TRADES):
        self._max_trades = max_trades
        self._trading_date: str | None = None
        self._trades_used: int = 0
        self._wins: int = 0
        self._losses: int = 0
        self._active: bool = False
        self._day_finished: bool = False

    # ── Date management ──────────────────────────────────────────────────

    def ensure_date(self, trading_date: str) -> None:
        """Set or roll over to a new trading date.

        If ``trading_date`` differs from the current date, all daily
        counters are reset.  If it matches, this is a no-op.

        Parameters
        ----------
        trading_date : str
            Trading date string (e.g. "2026-08-11").
        """
        if not isinstance(trading_date, str) or not trading_date:
            raise ValueError("trading_date must be a non-empty string")

        if trading_date == self._trading_date:
            return

        self._trading_date = trading_date
        self._trades_used = 0
        self._wins = 0
        self._losses = 0
        self._active = False
        self._day_finished = False

    # ── Query ────────────────────────────────────────────────────────────

    @property
    def can_trade(self) -> bool:
        """Whether a new trade is currently allowed."""
        if self._trading_date is None:
            return False
        if self._day_finished:
            return False
        if self._active:
            return False
        if self._trades_used >= self._max_trades:
            return False
        return True

    @property
    def state(self) -> DailyState:
        """Return an immutable snapshot of current state."""
        return DailyState(
            trading_date=self._trading_date,
            trades_used=self._trades_used,
            wins=self._wins,
            losses=self._losses,
            has_active_trade=self._active,
            day_finished=self._day_finished,
            can_trade=self.can_trade,
        )

    # ── Trade lifecycle ──────────────────────────────────────────────────

    def record_trade_open(self) -> None:
        """Record that a trade has been accepted and opened.

        Increments the daily trade counter and marks a trade as active.

        Raises
        ------
        RuntimeError
            If no trading date is set, if a trade is already active,
            or if trading is finished for the day.
        """
        if self._trading_date is None:
            raise RuntimeError("No trading date set — call ensure_date() first")
        if self._active:
            raise RuntimeError("Cannot open a trade while another is active")
        if self._day_finished:
            raise RuntimeError("Trading is finished for the day")
        if self._trades_used >= self._max_trades:
            raise RuntimeError(
                f"Daily trade limit reached ({self._max_trades})"
            )

        self._trades_used += 1
        self._active = True

    def record_trade_result(self, result: TradeResult) -> None:
        """Record the final result of the active trade.

        Parameters
        ----------
        result : TradeResult
            WIN or LOSS.

        Raises
        ------
        RuntimeError
            If no trade is currently active.
        """
        if not self._active:
            raise RuntimeError("No active trade to close")

        self._active = False

        if result == TradeResult.WIN:
            self._wins += 1
            self._day_finished = True
        elif result == TradeResult.LOSS:
            self._losses += 1
            if self._trades_used >= self._max_trades:
                self._day_finished = True
        else:
            raise ValueError(f"Unknown trade result: {result!r}")
