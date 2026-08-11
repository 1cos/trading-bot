"""Underlying exit monitor — structural stop/target trigger for MaxBot v0.1.

Monitors completed 1m underlying bars to determine when the underlying
price reaches the strategy's structural STOP or TARGET level.

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

Activation: bars with time_ms < activation_time_ms are ignored,
preventing pre-fill candles from triggering exits.

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
        Bars with time_ms < this value are ignored (pre-fill protection).
    """

    def __init__(
        self,
        direction: str,
        stop_price: float,
        target_price: float,
        activation_time_ms: int,
    ):
        if direction not in ("LONG", "SHORT"):
            raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")

        self._direction = direction
        self._stop_price = stop_price
        self._target_price = target_price
        self._activation_time_ms = activation_time_ms
        self._terminal_result: ExitTriggerResult | None = None

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

        # Pre-activation: ignore bars before the fill
        if bar["time_ms"] < self._activation_time_ms:
            return self._hold()

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
        )
