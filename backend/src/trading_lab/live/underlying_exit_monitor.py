"""Underlying exit monitor — structural stop/target trigger for MaxBot v0.1.

Determines when the underlying price reaches the strategy's structural
STOP or TARGET level.

Two evaluation paths, sharing one terminal result:

    evaluate_price(price)  — PRIMARY. A live post-fill price tick.
                             Fires the moment the level is crossed.
    evaluate_bar(bar)      — BACKSTOP. A completed 1m bar, for a
                             crossing the live feed did not deliver.

Setup detection stays candle-close based; only the exit, once the
position is open, is price-event based.

The OPTION is the execution vehicle; the UNDERLYING is the exit authority.
No option-premium stop or target is calculated.

Trigger rules (candle HIGH/LOW, not close-only):

    LONG:
        STOP:   bar.low  <= stop_price
        TARGET: bar.high >= target_price

    SHORT:
        STOP:   bar.high >= stop_price
        TARGET: bar.low  <= target_price

Same-bar ambiguity (both stop and target touched in one bar):
    → STOP_TRIGGERED (conservative, avoids optimistic backtest bias)

Activation: no price observed before the entry fill may close the
position. A live price is post-fill by construction — the monitor only
exists once the fill is confirmed. For bars, only a bar that ENDS at or
before the fill is skipped outright; the bar the fill landed inside is
evaluated on its close alone, which is the one price in it that is
provably after the fill. Its high/low are not used, because they
accumulate from the bar's open and may predate the fill.

Once triggered, the monitor is terminal and idempotently returns
the same result on subsequent evaluations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique


# ── Exit state ───────────────────────────────────────────────────────────────

@unique
class ExitState(StrEnum):
    HOLD = "HOLD"
    STOP_TRIGGERED = "STOP_TRIGGERED"
    TARGET_TRIGGERED = "TARGET_TRIGGERED"
    # Not produced by this monitor: the runner raises it when the
    # session is ending and a position is still open. It exists so a
    # forced exit travels the ordinary exit path with an honest label,
    # instead of being disguised as a stop that never happened.
    SESSION_END_TRIGGERED = "SESSION_END_TRIGGERED"


# ── Exit result ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ExitTriggerResult:
    """Immutable result of evaluating an underlying bar.

    Attributes
    ----------
    state : ExitState
        HOLD, STOP_TRIGGERED, or TARGET_TRIGGERED.
    direction : str
        "LONG" or "SHORT".
    stop_price : float
        Underlying structural stop level.
    target_price : float
        Underlying structural target level.
    trigger_bar_time_ms : int or None
        time_ms of the bar that triggered the exit (None if HOLD).
    trigger_bar_open : float or None
        Open of the trigger bar.
    trigger_bar_high : float or None
        High of the trigger bar.
    trigger_bar_low : float or None
        Low of the trigger bar.
    trigger_bar_close : float or None
        Close of the trigger bar.
    same_bar_ambiguity : bool
        True if both stop and target were touched in the same bar.
    trigger_source : str or None
        "PRICE" for a live tick, "BAR" for the completed-bar backstop,
        None while HOLD. Telemetry only — never a decision input.
    trigger_price : float or None
        The exact price that crossed the level, when a live tick fired.
    """

    state: ExitState
    direction: str
    stop_price: float
    target_price: float
    trigger_bar_time_ms: int | None = None
    trigger_bar_open: float | None = None
    trigger_bar_high: float | None = None
    trigger_bar_low: float | None = None
    trigger_bar_close: float | None = None
    same_bar_ambiguity: bool = False
    trigger_source: str | None = None
    trigger_price: float | None = None


# ── Monitor ──────────────────────────────────────────────────────────────────


class UnderlyingExitMonitor:
    """Incremental underlying exit trigger monitor.

    Parameters
    ----------
    direction : str
        "LONG" or "SHORT".
    stop_price : float
        Underlying structural stop level.
    target_price : float
        Underlying structural target level.
    activation_time_ms : int
        Entry-fill wall clock. Nothing observed before it may close the
        position.
    bar_duration_ms : int
        Length of one bar, used only to tell whether a bar ended before
        the fill. Default 60_000 (1m), the only timeframe live trading
        uses today.
    """

    def __init__(
        self,
        direction: str,
        stop_price: float,
        target_price: float,
        activation_time_ms: int,
        bar_duration_ms: int = 60_000,
    ):
        if direction not in ("LONG", "SHORT"):
            raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")

        self._direction = direction
        self._stop_price = stop_price
        self._target_price = target_price
        self._activation_time_ms = activation_time_ms
        self._bar_duration_ms = bar_duration_ms
        self._terminal_result: ExitTriggerResult | None = None

    def evaluate_price(self, price: float) -> ExitTriggerResult:
        """Evaluate a single live underlying price — the primary path.

        Called on every post-fill price update while the position is
        open, so a level crossing fires as soon as it is observed
        instead of waiting for the bar to close.

        No activation check is needed or possible here: the monitor is
        constructed only once the entry fill is confirmed, so every
        price it can ever see is already post-fill. That is also why a
        single price is used rather than the forming bar's high/low —
        those accumulate from the bar's open and can predate the fill.

        Terminal and idempotent: once STOP or TARGET has fired, by
        either path, this returns that same result forever.
        """
        if self._terminal_result is not None:
            return self._terminal_result
        if price is None:
            return self._hold()
        try:
            price = float(price)
        except (TypeError, ValueError):
            return self._hold()
        if price != price or price <= 0:      # NaN or non-price
            return self._hold()

        # A single price cannot be on both sides of a well-formed
        # stop/target pair, so there is no same-bar ambiguity to
        # resolve. Stop is still checked first: if the two levels were
        # ever mis-ordered, the conservative branch must win.
        if self._check_stop(price, price):
            result = self._price_result(ExitState.STOP_TRIGGERED, price)
        elif self._check_target(price, price):
            result = self._price_result(ExitState.TARGET_TRIGGERED, price)
        else:
            return self._hold()

        self._terminal_result = result
        return result

    def evaluate_bar(self, bar: dict) -> ExitTriggerResult:
        """Evaluate a completed underlying 1m bar.

        Parameters
        ----------
        bar : dict
            Candle with time_ms, open, high, low, close.

        Returns
        -------
        ExitTriggerResult
        """
        # Terminal: once triggered, idempotently return same result
        if self._terminal_result is not None:
            return self._terminal_result

        # Pre-activation. A bar that ENDS at or before the fill is
        # entirely pre-fill — skip it. A bar the fill landed inside is
        # NOT skipped (most of it can be post-fill: the MU trade of
        # 2026-08-26 filled 10s into a bar that reached target 15s
        # later, and discarding that whole bar cost 60s). For that bar
        # only the close is provably post-fill, so only the close is
        # used — its high/low accumulate from the bar's open.
        bar_end_ms = bar["time_ms"] + self._bar_duration_ms
        if bar_end_ms <= self._activation_time_ms:
            return self._hold()

        if bar["time_ms"] < self._activation_time_ms:
            close = bar["close"]
            if self._check_stop(close, close):
                result = self._make_result(ExitState.STOP_TRIGGERED, bar)
            elif self._check_target(close, close):
                result = self._make_result(ExitState.TARGET_TRIGGERED, bar)
            else:
                return self._hold()
            self._terminal_result = result
            return result

        high = bar["high"]
        low = bar["low"]

        stop_hit = self._check_stop(high, low)
        target_hit = self._check_target(high, low)

        if stop_hit and target_hit:
            # Same-bar ambiguity: conservative → STOP
            result = self._make_result(
                ExitState.STOP_TRIGGERED, bar, same_bar_ambiguity=True
            )
        elif stop_hit:
            result = self._make_result(ExitState.STOP_TRIGGERED, bar)
        elif target_hit:
            result = self._make_result(ExitState.TARGET_TRIGGERED, bar)
        else:
            return self._hold()

        self._terminal_result = result
        return result

    # ── Internals ────────────────────────────────────────────────────────

    def _check_stop(self, high: float, low: float) -> bool:
        if self._direction == "LONG":
            return low <= self._stop_price
        else:  # SHORT
            return high >= self._stop_price

    def _check_target(self, high: float, low: float) -> bool:
        if self._direction == "LONG":
            return high >= self._target_price
        else:  # SHORT
            return low <= self._target_price

    def _hold(self) -> ExitTriggerResult:
        return ExitTriggerResult(
            state=ExitState.HOLD,
            direction=self._direction,
            stop_price=self._stop_price,
            target_price=self._target_price,
        )

    def _price_result(self, state: ExitState, price: float) -> ExitTriggerResult:
        return ExitTriggerResult(
            state=state,
            direction=self._direction,
            stop_price=self._stop_price,
            target_price=self._target_price,
            trigger_source="PRICE",
            trigger_price=price,
        )

    def _make_result(
        self, state: ExitState, bar: dict, *, same_bar_ambiguity: bool = False
    ) -> ExitTriggerResult:
        return ExitTriggerResult(
            state=state,
            direction=self._direction,
            stop_price=self._stop_price,
            target_price=self._target_price,
            trigger_bar_time_ms=bar["time_ms"],
            trigger_bar_open=bar["open"],
            trigger_bar_high=bar["high"],
            trigger_bar_low=bar["low"],
            trigger_bar_close=bar["close"],
            same_bar_ambiguity=same_bar_ambiguity,
            trigger_source="BAR",
        )
