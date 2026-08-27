"""Post-exit underlying observation — the R-multiple probe.

A trade closes at its live target, and the question the record cannot
answer today is the obvious one: *would it also have reached 3R, or 4R?*
Once the position is closed the orchestrator stops caring about that
symbol, so the path that would answer it is never recorded and is gone
for good the moment the process ends.

This module keeps watching. It is purely observational: it places no
orders, touches no lifecycle, and never changes the trade's real
outcome. It only remembers where the underlying went.

The path it records starts at the ACTUAL FILL, not at the entry bar's
open. Those are not the same instant: on the real MSFT trade of
2026-08-26 the fill landed at 14:14:09.960 and the stop was touched at
14:14:15 — five seconds later, inside the same minute. Using that bar's
high/low would have counted the 14:14:05 low too, which happened before
the position existed; skipping the bar entirely would have lost the
stop. Neither is the market path the trade actually experienced.

So the fill minute is fed from the live price updates that already
arrive roughly every five seconds (the same feed the exit trigger uses),
filtered to those after the fill, plus that bar's close, which is the
one price in it provably post-fill. From the first bar wholly after the
fill, ordinary 1m high/low resumes — the probe is not tick-based.

What it records, from the fill to the end of the session:

    mfe_r / mae_r         the best and worst excursion, in R
    first_touch_r         the first time each of 2R / 2.5R / 3R / 3.5R /
                          4R was reached
    stop_first_touch_ms   the first time the technical stop was reached
    first_touch_source    "PRICE" (a live sample) or "BAR" (1m high/low),
                          so a reader knows the resolution behind a
                          timestamp
    path                  the raw bars and the fill-minute live samples,
                          so a level this probe does not currently track
                          can still be answered later

Both are kept on purpose. The summary answers the question that was
asked; the path is the evidence, and keeps a future question (1.5R? 5R?)
answerable without another day of data.

Deliberately NOT decided here
-----------------------------
Whether a target or the stop came first when a single observation
touched both is a question that observation cannot answer, so this
module does not pretend to: it records both first-touch timestamps and
leaves the arbitration to whoever reads them. `same_bar_as_stop` on a
level means the two were seen at the same instant — the case where the
honest answer is "unknown at this resolution".

Live samples reduce that ambiguity in the fill minute without inventing
anything: one price cannot be both beyond the target and beyond the
stop, so two touches seen at two different samples are genuinely
ordered. What happens BETWEEN two samples is simply not observed, and
the probe never claims otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# The R multiples the probe reports on. Extending this list changes only
# what is pre-computed — `path` keeps every bar, so any other multiple
# stays derivable after the fact.
R_LEVELS: tuple[float, ...] = (2.0, 2.5, 3.0, 3.5, 4.0)


def _level_key(multiple: float) -> str:
    """2.0 -> "2r", 2.5 -> "2_5r" — a JSON-safe, stable field name."""
    text = f"{multiple:g}".replace(".", "_")
    return f"{text}r"


@dataclass
class RProbe:
    """One trade's post-entry observation. One probe per trade_id."""

    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    entry_timestamp_ms: int
    fill_timestamp_ms: int
    r_distance: float
    bar_duration_ms: int = 60_000

    mfe_r: float = 0.0
    mae_r: float = 0.0
    mfe_timestamp_ms: int | None = None
    mae_timestamp_ms: int | None = None
    stop_first_touch_ms: int | None = None
    first_touch: dict[str, int] = field(default_factory=dict)
    first_touch_source: dict[str, str] = field(default_factory=dict)
    same_bar: dict[str, bool] = field(default_factory=dict)
    stop_first_touch_source: str | None = None
    live_samples: int = 0
    path: list[dict] = field(default_factory=list)
    bars_observed: int = 0
    last_observed_ms: int | None = None
    closed_reason: str | None = None

    # ── Construction ─────────────────────────────────────────────────

    @classmethod
    def create(cls, *, trade_id, symbol, direction, entry_price,
               stop_price, target_price, entry_timestamp_ms,
               fill_timestamp_ms=None, bar_duration_ms=60_000) -> "RProbe | None":
        """Build a probe, or None if R is not a usable distance.

        A zero (or inverted) stop distance makes every R multiple
        meaningless — reporting "47R" off a one-tick stop would be
        noise dressed as a measurement — so no probe is created at all.
        """
        try:
            r = abs(float(entry_price) - float(stop_price))
        except (TypeError, ValueError):
            return None
        if not r or r != r:            # zero, or NaN
            return None
        if direction not in ("LONG", "SHORT"):
            return None
        entry_ms = int(entry_timestamp_ms)
        # Without a real fill time the safest assumption is that the
        # position existed from the end of the entry candle: that is the
        # earliest instant an entry-at-close could have been filled, so
        # nothing before it can be attributed to the trade.
        fill_ms = (entry_ms + int(bar_duration_ms)
                   if fill_timestamp_ms is None else int(fill_timestamp_ms))
        return cls(
            trade_id=trade_id, symbol=symbol, direction=direction,
            entry_price=float(entry_price), stop_price=float(stop_price),
            target_price=float(target_price),
            entry_timestamp_ms=entry_ms, fill_timestamp_ms=fill_ms,
            r_distance=r, bar_duration_ms=int(bar_duration_ms),
        )

    # ── Observation ──────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self.closed_reason is None

    def r_of(self, price: float) -> float:
        """Signed R multiple of a price: positive in the trade's favour."""
        if self.direction == "LONG":
            return (price - self.entry_price) / self.r_distance
        return (self.entry_price - price) / self.r_distance

    @property
    def fill_minute_start_ms(self) -> int:
        """Open of the bar the fill landed inside."""
        return (self.fill_timestamp_ms // self.bar_duration_ms) * self.bar_duration_ms

    @property
    def fill_minute_end_ms(self) -> int:
        return self.fill_minute_start_ms + self.bar_duration_ms

    def _apply(self, favorable: float, adverse: float, t: int, source: str) -> None:
        """Fold one observation into the running extremes and touches.

        `favorable`/`adverse` are the same price for a live sample and
        the bar's two extremes for a completed bar — the only thing that
        differs between the two paths.
        """
        fav_r = self.r_of(favorable)
        adv_r = self.r_of(adverse)

        if fav_r > self.mfe_r:
            self.mfe_r, self.mfe_timestamp_ms = fav_r, t
        if adv_r < self.mae_r:
            self.mae_r, self.mae_timestamp_ms = adv_r, t

        stop_hit_now = adv_r <= -1.0
        if stop_hit_now and self.stop_first_touch_ms is None:
            self.stop_first_touch_ms = t
            self.stop_first_touch_source = source

        for multiple in R_LEVELS:
            key = _level_key(multiple)
            if key in self.first_touch or fav_r < multiple:
                continue
            self.first_touch[key] = t
            self.first_touch_source[key] = source
            # Ambiguous only when both were seen at the same instant.
            # A live sample is a single price and cannot be on both
            # sides at once, so this is only ever true on the bar path.
            self.same_bar[key] = stop_hit_now and self.stop_first_touch_ms == t

    def observe_price(self, price: float, time_ms: int) -> None:
        """Record one live price sample from the fill minute.

        Accepted only for samples at or after the fill and before the
        fill minute ends: that window is the one the bar path cannot
        cover honestly, because the bar's own high/low may predate the
        fill. Everything after it is covered by completed bars, so the
        probe stays bar-based and does not become tick-based.

        Purely observational, like the rest of this module.
        """
        if not self.is_open:
            return
        try:
            t = int(time_ms)
            price = float(price)
        except (TypeError, ValueError):
            return
        if price != price or price <= 0:
            return
        if not (self.fill_timestamp_ms <= t < self.fill_minute_end_ms):
            return

        self._apply(price, price, t, "PRICE")
        self.path.append({"time_ms": t, "price": price, "source": "PRICE"})
        self.live_samples += 1
        if self.last_observed_ms is None or t > self.last_observed_ms:
            self.last_observed_ms = t

    def observe(self, bar: dict) -> None:
        """Record one completed bar.

        Three cases, decided by where the bar sits relative to the fill:

          * ends at or before the fill — entirely pre-fill, ignored;
          * contains the fill — its high/low may predate the position,
            so only its close is used (provably post-fill). The rest of
            that minute comes from observe_price();
          * wholly after the fill — ordinary high/low.
        """
        if not self.is_open:
            return
        try:
            t = int(bar["time_ms"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            return

        bar_end = t + self.bar_duration_ms
        if bar_end <= self.fill_timestamp_ms:
            return                                  # entirely pre-fill
        if t < self.fill_timestamp_ms:
            # The fill minute. Close only — never high/low.
            self._apply(close, close, t, "BAR")
            self.path.append({"time_ms": t, "open": bar.get("open"),
                              "high": high, "low": low, "close": close,
                              "source": "BAR", "partial_post_fill": True})
            self.bars_observed += 1
            self.last_observed_ms = max(self.last_observed_ms or 0, t)
            return
        if self.bars_observed and self.path:
            last_bar = next((x for x in reversed(self.path)
                             if x.get("source") == "BAR"), None)
            if last_bar is not None and t <= last_bar["time_ms"]:
                return                              # replay / out of order

        favorable, adverse = (high, low) if self.direction == "LONG" else (low, high)
        self._apply(favorable, adverse, t, "BAR")
        self.path.append({"time_ms": t, "open": bar.get("open"), "high": high,
                          "low": low, "close": close, "source": "BAR"})
        self.bars_observed += 1
        self.last_observed_ms = max(self.last_observed_ms or 0, t)

    def close(self, reason: str = "SESSION_END") -> None:
        """Stop observing. Terminal and idempotent."""
        if self.is_open:
            self.closed_reason = reason

    # ── Persistence shape ────────────────────────────────────────────

    def to_block(self) -> dict:
        """The additive record block. Plain JSON types only."""
        return {
            "schema_version": "RProbe/v1",
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "entry_timestamp_ms": self.entry_timestamp_ms,
            "r_distance": round(self.r_distance, 6),
            "r_levels": list(R_LEVELS),
            "mfe_r": round(self.mfe_r, 4),
            "mae_r": round(self.mae_r, 4),
            "mfe_timestamp_ms": self.mfe_timestamp_ms,
            "mae_timestamp_ms": self.mae_timestamp_ms,
            "stop_first_touch_ms": self.stop_first_touch_ms,
            "fill_timestamp_ms": self.fill_timestamp_ms,
            "first_touch": dict(self.first_touch),
            "first_touch_source": dict(self.first_touch_source),
            "stop_first_touch_source": self.stop_first_touch_source,
            "live_samples": self.live_samples,
            "same_bar_as_stop": dict(self.same_bar),
            "bars_observed": self.bars_observed,
            "last_observed_ms": self.last_observed_ms,
            "observation_closed_reason": self.closed_reason,
            "path": list(self.path),
        }
