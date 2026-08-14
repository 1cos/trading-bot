"""Execution queue — defers IBKR-sync work outside event callbacks.

The bar callback (fired inside ib.sleep → event loop) must NEVER call
IBKR sync methods like qualifyContracts, reqSecDefOptParams, reqMktData,
placeOrder, etc.  These internally call loop.run_until_complete() which
raises ``RuntimeError: This event loop is already running``.

Architecture:

    _on_bar_update (callback, inside event loop)
        → pure computation only
        → if SIGNAL detected: enqueue ExecutionWorkItem
        → return immediately

    _run_loop (main loop, OUTSIDE callback)
        → drain queue
        → for each work item: run IBKR sync calls safely

"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Any

log = logging.getLogger("maxbot")


# ── Work item ────────────────────────────────────────────────────────────────

@unique
class WorkItemType(StrEnum):
    SIGNAL_EXECUTION = "SIGNAL_EXECUTION"


@unique
class WorkItemStatus(StrEnum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class ExecutionWorkItem:
    """A deferred unit of work requiring IBKR sync calls.

    Attributes
    ----------
    symbol : str
        Underlying symbol.
    work_type : WorkItemType
        Type of work to perform.
    signal_result : object
        The signal detector result that triggered this work.
    bar_time_ms : int
        Timestamp of the bar that produced the signal.
    created_at : float
        Monotonic time when enqueued.
    status : WorkItemStatus
        Current processing status.
    error : str | None
        Error message if FAILED.
    """

    symbol: str
    work_type: WorkItemType
    signal_result: object
    bar_time_ms: int
    created_at: float = field(default_factory=time.monotonic)
    status: WorkItemStatus = WorkItemStatus.PENDING
    error: str | None = None

    @property
    def key(self) -> str:
        """Dedup key: symbol + bar_time."""
        return f"{self.symbol}:{self.bar_time_ms}"


# ── Queue ────────────────────────────────────────────────────────────────────


class ExecutionQueue:
    """FIFO queue for deferred IBKR execution work.

    Thread-safe is NOT required — both enqueue (callback) and drain
    (main loop) run on the same asyncio event loop thread.

    Guarantees
    ----------
    - FIFO ordering
    - Duplicate suppression (same symbol + bar_time)
    - At most one active work item per symbol
    - Error isolation per symbol
    """

    def __init__(self) -> None:
        self._queue: deque[ExecutionWorkItem] = deque()
        self._seen_keys: set[str] = set()
        self._active_symbols: set[str] = set()

    def enqueue(self, item: ExecutionWorkItem) -> bool:
        """Add a work item to the queue.

        Returns True if enqueued, False if duplicate or symbol busy.
        """
        if item.key in self._seen_keys:
            log.debug(f"EXEC_QUEUE: duplicate suppressed {item.key}")
            return False
        if item.symbol in self._active_symbols:
            log.warning(
                f"EXEC_QUEUE: symbol {item.symbol} already has active work, "
                f"rejecting {item.key}"
            )
            return False
        self._seen_keys.add(item.key)
        self._queue.append(item)
        log.info(
            f"EXECUTION_WORK_ENQUEUED symbol={item.symbol} "
            f"bar_time_ms={item.bar_time_ms}"
        )
        return True

    def drain(self) -> list[ExecutionWorkItem]:
        """Remove and return all pending items, marking their symbols active.

        Returns items whose symbols are not currently active.
        Items for busy symbols are left in the queue.
        """
        ready = []
        deferred = deque()
        while self._queue:
            item = self._queue.popleft()
            if item.symbol in self._active_symbols:
                deferred.append(item)
            else:
                self._active_symbols.add(item.symbol)
                item.status = WorkItemStatus.STARTED
                ready.append(item)
        self._queue = deferred
        return ready

    def complete(self, item: ExecutionWorkItem) -> None:
        """Mark a work item as completed."""
        item.status = WorkItemStatus.COMPLETED
        self._active_symbols.discard(item.symbol)
        log.info(
            f"EXECUTION_WORK_COMPLETED symbol={item.symbol} "
            f"bar_time_ms={item.bar_time_ms}"
        )

    def fail(self, item: ExecutionWorkItem, error: str) -> None:
        """Mark a work item as failed."""
        item.status = WorkItemStatus.FAILED
        item.error = error
        self._active_symbols.discard(item.symbol)
        log.error(
            f"EXECUTION_WORK_FAILED symbol={item.symbol} "
            f"bar_time_ms={item.bar_time_ms} error={error}"
        )

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def active_symbols(self) -> set[str]:
        return set(self._active_symbols)

    def clear(self) -> None:
        """Reset queue state (for testing)."""
        self._queue.clear()
        self._seen_keys.clear()
        self._active_symbols.clear()
