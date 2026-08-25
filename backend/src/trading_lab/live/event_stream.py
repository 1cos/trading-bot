"""Structured live event stream and session log for MaxBot v0.1.

Every important MaxBot action generates a ``LiveEvent``.  Events are
collected in a ``SessionEventLog`` for export/analysis.

Design goals:
    - Reconstruct exactly what MaxBot saw, decided, submitted, filled
    - Feed a future PWA via ``events_since(seq)`` without parsing console
    - Export to JSON (machine) and Markdown (human)
    - No secrets or credentials in events
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import StrEnum, unique
from pathlib import Path
from typing import Any


# ── Event types ──────────────────────────────────────────────────────────────

@unique
class EventType(StrEnum):
    # Infrastructure
    BOT_STARTED = "BOT_STARTED"
    IBKR_CONNECTED = "IBKR_CONNECTED"
    PAPER_VERIFIED = "PAPER_VERIFIED"
    SYMBOL_ENABLED = "SYMBOL_ENABLED"
    SYMBOL_DISABLED = "SYMBOL_DISABLED"
    STREAM_STARTED = "STREAM_STARTED"
    BOT_STOPPED = "BOT_STOPPED"
    ERROR = "ERROR"

    # Strategy
    SIGNAL = "SIGNAL"

    # Observation / audit (never a trading decision — see pd_audit.py)
    PD_AUDIT = "PD_AUDIT"

    # Option selection
    OPTION_SELECTED = "OPTION_SELECTED"

    # Entry
    ENTRY_ORDER_BUILT = "ENTRY_ORDER_BUILT"
    ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
    ENTRY_FILLED = "ENTRY_FILLED"
    ENTRY_CANCELLED = "ENTRY_CANCELLED"
    ENTRY_REJECTED = "ENTRY_REJECTED"

    # Position / underlying
    POSITION_OPEN = "POSITION_OPEN"
    STOP_TRIGGERED = "STOP_TRIGGERED"
    TARGET_TRIGGERED = "TARGET_TRIGGERED"

    # Exit
    EXIT_SUBMITTED = "EXIT_SUBMITTED"
    EXIT_FILLED = "EXIT_FILLED"
    EXIT_CANCELLED = "EXIT_CANCELLED"
    EXIT_REJECTED = "EXIT_REJECTED"

    # Final
    TRADE_WIN = "TRADE_WIN"
    TRADE_LOSS = "TRADE_LOSS"
    TRADE_COMPLETED = "TRADE_COMPLETED"

    # Observe-only
    OBSERVE_ENTRY = "OBSERVE_ENTRY"
    OBSERVE_STOP = "OBSERVE_STOP"
    OBSERVE_TARGET = "OBSERVE_TARGET"


# ── Live event ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class LiveEvent:
    """One structured MaxBot event.

    Attributes
    ----------
    seq : int
        Monotonic sequence number for stable ordering.
    timestamp_ms : int
        Event creation time (epoch ms UTC).
    event_type : str
        One of EventType values.
    symbol : str
        Underlying symbol (empty for infrastructure events).
    execution_mode : str
        "OBSERVE_ONLY" or "PAPER_EXECUTE".
    direction : str or None
        Resolved direction if applicable.
    lifecycle : str or None
        Current lifecycle state if applicable.
    data : dict
        Event-type-specific payload.
    """

    seq: int
    timestamp_ms: int
    event_type: str
    symbol: str
    execution_mode: str
    direction: str | None = None
    lifecycle: str | None = None
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON export."""
        d = {
            "seq": self.seq,
            "timestamp_ms": self.timestamp_ms,
            "timestamp_utc": datetime.fromtimestamp(
                self.timestamp_ms / 1000, tz=timezone.utc
            ).isoformat(),
            "event_type": self.event_type,
            "symbol": self.symbol,
            "execution_mode": self.execution_mode,
        }
        if self.direction is not None:
            d["direction"] = self.direction
        if self.lifecycle is not None:
            d["lifecycle"] = self.lifecycle
        if self.data:
            d["data"] = self.data
        return d


# ── Event factory ────────────────────────────────────────────────────────────


class EventFactory:
    """Creates LiveEvent instances with monotonic sequence numbers."""

    def __init__(self, execution_mode: str):
        self._mode = execution_mode
        self._seq = 0

    def create(
        self,
        event_type: str | EventType,
        symbol: str = "",
        direction: str | None = None,
        lifecycle: str | None = None,
        data: dict | None = None,
    ) -> LiveEvent:
        self._seq += 1
        return LiveEvent(
            seq=self._seq,
            timestamp_ms=int(time.time() * 1000),
            event_type=str(event_type),
            symbol=symbol,
            execution_mode=self._mode,
            direction=direction,
            lifecycle=lifecycle,
            data=data or {},
        )


# ── Session event log ────────────────────────────────────────────────────────


class SessionEventLog:
    """In-memory session-scoped event store.

    Parameters
    ----------
    metadata : dict
        Session-level metadata (trading_date, watchlist, etc.).
    """

    def __init__(self, metadata: dict | None = None):
        self._events: list[LiveEvent] = []
        self._metadata = metadata or {}

    def append(self, event: LiveEvent) -> None:
        """Append an event (chronological order preserved by caller)."""
        self._events.append(event)

    @property
    def events(self) -> list[LiveEvent]:
        """All events in chronological order."""
        return list(self._events)

    def events_for_symbol(self, symbol: str) -> list[LiveEvent]:
        """Filter events by underlying symbol."""
        return [e for e in self._events if e.symbol == symbol]

    def events_since(self, seq: int) -> list[LiveEvent]:
        """Return events with seq > the given sequence number."""
        return [e for e in self._events if e.seq > seq]

    @property
    def trade_events(self) -> list[LiveEvent]:
        """Events that are trade-lifecycle related."""
        trade_types = {
            EventType.SIGNAL, EventType.OPTION_SELECTED,
            EventType.ENTRY_ORDER_BUILT, EventType.ENTRY_SUBMITTED,
            EventType.ENTRY_FILLED, EventType.ENTRY_CANCELLED,
            EventType.ENTRY_REJECTED, EventType.POSITION_OPEN,
            EventType.STOP_TRIGGERED, EventType.TARGET_TRIGGERED,
            EventType.EXIT_SUBMITTED, EventType.EXIT_FILLED,
            EventType.EXIT_CANCELLED, EventType.EXIT_REJECTED,
            EventType.TRADE_WIN, EventType.TRADE_LOSS,
            EventType.TRADE_COMPLETED,
            EventType.OBSERVE_ENTRY, EventType.OBSERVE_STOP,
            EventType.OBSERVE_TARGET,
        }
        return [e for e in self._events if e.event_type in trade_types]

    @property
    def metadata(self) -> dict:
        return dict(self._metadata)

    def set_metadata(self, key: str, value) -> None:
        self._metadata[key] = value

    # ── Export ────────────────────────────────────────────────────────────

    def export_json(self, path: str | Path) -> Path:
        """Export session to JSON file.

        Returns the Path written.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "maxbot_version": "v0.1",
            "session": self._metadata,
            "event_count": len(self._events),
            "events": [e.to_dict() for e in self._events],
        }
        p.write_text(json.dumps(payload, indent=2, default=str))
        return p

    def export_markdown(self, path: str | Path) -> Path:
        """Export session to human-readable Markdown.

        Returns the Path written.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        lines = []
        lines.append("# MaxBot v0.1 Session Log\n")

        # Metadata
        for k, v in self._metadata.items():
            lines.append(f"- **{k}**: {v}")
        lines.append(f"- **events**: {len(self._events)}")
        lines.append("")

        # Events
        lines.append("## Events\n")
        for e in self._events:
            ts = datetime.fromtimestamp(
                e.timestamp_ms / 1000, tz=timezone.utc
            ).strftime("%H:%M:%S")
            sym = f" [{e.symbol}]" if e.symbol else ""
            dir_ = f" {e.direction}" if e.direction else ""
            line = f"- `{ts}` **{e.event_type}**{sym}{dir_}"
            if e.data:
                # Show key data points inline
                highlights = []
                for key in ("underlying_entry", "underlying_stop",
                            "underlying_target", "strike", "expiration",
                            "limit_price", "fill_price", "exit_reason",
                            "result", "gross_pnl", "error",
                            # PD_AUDIT payload (see pd_audit.py)
                            "level_source", "level_price", "current_price",
                            "eligible", "failed_reason", "pipeline_stage",
                            "current_state", "setup_key"):
                    if key in e.data:
                        highlights.append(f"{key}={e.data[key]}")
                if highlights:
                    line += f" — {', '.join(highlights)}"
            lines.append(line)

        lines.append("")
        p.write_text("\n".join(lines))
        return p


# ── Trade summary builder ────────────────────────────────────────────────────

def build_trade_summary(
    signal_event: LiveEvent | None,
    option_event: LiveEvent | None,
    entry_submitted: LiveEvent | None,
    entry_filled: LiveEvent | None,
    trigger_event: LiveEvent | None,
    exit_filled: LiveEvent | None,
    result: str,  # "WIN" or "LOSS"
) -> dict:
    """Build a TRADE_COMPLETED payload from lifecycle events.

    Calculates gross P&L from option premiums if both entry and exit
    fill prices are available.  Does not estimate commissions.

    Strategy result (WIN/LOSS) and option P&L are kept as separate fields.
    """
    summary: dict[str, Any] = {"result": result}

    if signal_event:
        d = signal_event.data
        summary["symbol"] = signal_event.symbol
        summary["direction"] = signal_event.direction
        summary["signal_time_ms"] = signal_event.timestamp_ms
        for k in ("underlying_entry", "underlying_stop", "underlying_target"):
            if k in d:
                summary[k] = d[k]

    if option_event:
        d = option_event.data
        for k in ("right", "expiration", "strike", "con_id", "exchange",
                   "multiplier"):
            if k in d:
                summary[f"option_{k}"] = d[k]

    entry_fill_price = None
    if entry_submitted:
        summary["entry_submission_time_ms"] = entry_submitted.timestamp_ms
    if entry_filled:
        summary["entry_fill_time_ms"] = entry_filled.timestamp_ms
        entry_fill_price = entry_filled.data.get("fill_price")
        if entry_fill_price is not None:
            summary["entry_fill_premium"] = entry_fill_price

    exit_fill_price = None
    if trigger_event:
        summary["exit_reason"] = trigger_event.data.get("exit_reason",
                                                         trigger_event.event_type)
        summary["trigger_time_ms"] = trigger_event.timestamp_ms

    if exit_filled:
        summary["exit_fill_time_ms"] = exit_filled.timestamp_ms
        exit_fill_price = exit_filled.data.get("fill_price")
        if exit_fill_price is not None:
            summary["exit_fill_premium"] = exit_fill_price

    # Gross P&L (1 contract, multiplier 100 standard)
    if entry_fill_price is not None and exit_fill_price is not None:
        gross_pnl = round((exit_fill_price - entry_fill_price) * 100, 2)
        summary["gross_pnl"] = gross_pnl
        summary["gross_pnl_note"] = "before commissions, assumes multiplier=100"
        if entry_fill_price > 0:
            summary["premium_return_pct"] = round(
                (exit_fill_price - entry_fill_price) / entry_fill_price * 100, 2
            )

    # Durations
    if entry_filled and exit_filled:
        summary["duration_entry_to_exit_ms"] = (
            exit_filled.timestamp_ms - entry_filled.timestamp_ms
        )
    if signal_event and exit_filled:
        summary["duration_signal_to_exit_ms"] = (
            exit_filled.timestamp_ms - signal_event.timestamp_ms
        )

    return summary
