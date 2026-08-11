"""Live session builder — incremental 1m candle accumulator for MaxBot v0.1.

Accepts completed 1-minute bars one at a time and exposes the current
session in the exact dict format expected by ``_process_one_session``
in ``strategy_runner.py``.

Session dict contract (matching ``backtest_server.py``):

    {
        "symbol":               str,
        "date":                 str,    # "YYYY-MM-DD" in market timezone
        "market_timezone":      str,    # e.g. "America/New_York"
        "session_open_utc_ms":  int,    # time_ms of first candle
        "session_close_utc_ms": int,    # time_ms of last candle so far
        "timeframe":            str,    # "1m"
        "candles":              list,   # raw candle dicts, sorted by time_ms
    }

Candle dict contract (matching ``parse_csv_candles`` / ``timeframe_aggregation``):

    {
        "time_ms": int,     # epoch milliseconds UTC
        "open":    float,
        "high":    float,
        "low":     float,
        "close":   float,
        "volume":  int,     # optional in detection, but preserved
    }

Duplicate-bar policy:
    - Same time_ms + identical OHLCV → ignored (idempotent).
    - Same time_ms + changed values  → replaced (streaming update).

Out-of-order policy:
    - Bars older than the latest finalized bar are rejected (ValueError).
    - "Finalized" = any bar whose time_ms is strictly less than the
      current latest bar's time_ms.

New-session rollover:
    - If a new bar's date (in market timezone) differs from the current
      session date, the builder resets and starts a fresh session.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# ── Helpers ──────────────────────────────────────────────────────────────────

def _date_from_ms(time_ms: int, tz: ZoneInfo) -> str:
    """Convert epoch ms to 'YYYY-MM-DD' in the given timezone."""
    dt = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).astimezone(tz)
    return dt.strftime("%Y-%m-%d")


def _bars_identical(a: dict, b: dict) -> bool:
    """Check whether two candle dicts have identical OHLCV values."""
    return (
        a["open"] == b["open"]
        and a["high"] == b["high"]
        and a["low"] == b["low"]
        and a["close"] == b["close"]
        and a.get("volume", 0) == b.get("volume", 0)
    )


# ── Required candle fields ───────────────────────────────────────────────────

_REQUIRED_FIELDS = ("time_ms", "open", "high", "low", "close")


def _validate_bar(bar: dict) -> None:
    """Raise TypeError/ValueError if bar is missing required fields."""
    if not isinstance(bar, dict):
        raise TypeError(f"bar must be a dict, got {type(bar).__name__}")
    for field in _REQUIRED_FIELDS:
        if field not in bar:
            raise ValueError(f"bar missing required field: {field}")


# ── LiveSessionBuilder ───────────────────────────────────────────────────────


class LiveSessionBuilder:
    """Incremental 1-minute session accumulator.

    Parameters
    ----------
    symbol : str
        Instrument symbol (e.g. ``"SPY"``).
    market_timezone : str
        IANA timezone for session date determination
        (e.g. ``"America/New_York"``).
    """

    def __init__(self, symbol: str, market_timezone: str = "America/New_York"):
        self._symbol = symbol
        self._tz_str = market_timezone
        self._tz = ZoneInfo(market_timezone)

        # Current session state
        self._date: str | None = None
        self._candles: list[dict] = []
        self._time_index: dict[int, int] = {}  # time_ms → index in _candles

    # ── Public API ───────────────────────────────────────────────────────

    def add_bar(self, bar: dict) -> None:
        """Add a completed 1-minute bar to the current session.

        Parameters
        ----------
        bar : dict
            Raw candle dict with at minimum: time_ms, open, high, low, close.
            volume is optional (defaults to 0 if absent downstream).

        Raises
        ------
        TypeError
            If bar is not a dict.
        ValueError
            If bar is missing required fields, or is out-of-order
            (time_ms older than the latest finalized bar).
        """
        _validate_bar(bar)

        time_ms = bar["time_ms"]
        bar_date = _date_from_ms(time_ms, self._tz)

        # New session rollover
        if self._date is not None and bar_date != self._date:
            self._reset()

        # First bar of session
        if self._date is None:
            self._date = bar_date
            self._candles.append(bar)
            self._time_index[time_ms] = 0
            return

        # Duplicate timestamp
        if time_ms in self._time_index:
            idx = self._time_index[time_ms]
            existing = self._candles[idx]
            if _bars_identical(existing, bar):
                return  # idempotent — identical, ignore
            # Changed values — replace (streaming update)
            self._candles[idx] = bar
            return

        # Out-of-order check: reject bars older than latest
        if self._candles and time_ms < self._candles[-1]["time_ms"]:
            raise ValueError(
                f"Out-of-order bar rejected: received time_ms={time_ms} "
                f"but latest bar is time_ms={self._candles[-1]['time_ms']}"
            )

        # Normal append
        self._candles.append(bar)
        self._time_index[time_ms] = len(self._candles) - 1

    @property
    def bar_count(self) -> int:
        """Number of bars in the current session."""
        return len(self._candles)

    @property
    def current_date(self) -> str | None:
        """Current session date string, or None if empty."""
        return self._date

    def current_session(self) -> dict | None:
        """Return the current session in the format expected by the pipeline.

        Returns None if no bars have been added.

        The returned dict matches the session format built by
        ``backtest_server.py`` and consumed by
        ``strategy_runner._process_one_session()``.
        """
        if not self._candles:
            return None

        return {
            "symbol": self._symbol,
            "date": self._date,
            "market_timezone": self._tz_str,
            "session_open_utc_ms": self._candles[0]["time_ms"],
            "session_close_utc_ms": self._candles[-1]["time_ms"],
            "timeframe": "1m",
            "candles": list(self._candles),  # defensive copy
        }

    def reset(self) -> None:
        """Explicitly reset the builder, discarding current session."""
        self._reset()

    # ── Internals ────────────────────────────────────────────────────────

    def _reset(self) -> None:
        self._date = None
        self._candles = []
        self._time_index = {}
