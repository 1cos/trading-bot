"""Canonical SessionMetadata contract type for the BDRR pipeline.

Ported from BDRR_ENGINE_CANONICAL_HANDOFF.md §3.2 and validated by
estrategie/bdrr_detection_result.js (validateMetadata, Step 9 assembly).

Canonical schema (§3.2):

    SessionMetadata {
        symbol:                 string
        date:                   string      "YYYY-MM-DD" in market_timezone
        market_timezone:        string      "America/New_York"
        session_open_utc_ms:    int64
        session_close_utc_ms:   int64
        timeframe_seconds:      int
    }

Validation rules (from validateMetadata in bdrr_detection_result.js):

    symbol:               non-empty string
    date:                 string matching /^\\d{4}-\\d{2}-\\d{2}$/
    market_timezone:      non-empty string
    session_open_utc_ms:  finite number (int in Python; bool rejected)
    session_close_utc_ms: finite number (int in Python; bool rejected)
    timeframe_seconds:    positive integer (> 0; bool rejected)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── Validation helpers ────────────────────────────────────────────────────────


def _require_non_empty_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_date_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not _DATE_RE.match(value):
        raise ValueError(
            f'{name} must match YYYY-MM-DD format, got {value!r}'
        )
    return value


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def _require_positive_int(value: object, name: str) -> int:
    _require_int(value, name)
    if value <= 0:  # type: ignore[operator]
        raise ValueError(f"{name} must be > 0, got {value}")
    return value  # type: ignore[return-value]


# ── SessionMetadata ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    """Immutable session context embedded in DetectionResult/v1.

    All fields are required. No field is nullable.
    """

    symbol: str
    date: str
    market_timezone: str
    session_open_utc_ms: int
    session_close_utc_ms: int
    timeframe_seconds: int

    def __post_init__(self) -> None:
        _require_non_empty_str(self.symbol, "symbol")
        _require_date_str(self.date, "date")
        _require_non_empty_str(self.market_timezone, "market_timezone")
        _require_int(self.session_open_utc_ms, "session_open_utc_ms")
        _require_int(self.session_close_utc_ms, "session_close_utc_ms")
        _require_positive_int(self.timeframe_seconds, "timeframe_seconds")

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape."""
        return {
            "symbol": self.symbol,
            "date": self.date,
            "market_timezone": self.market_timezone,
            "session_open_utc_ms": self.session_open_utc_ms,
            "session_close_utc_ms": self.session_close_utc_ms,
            "timeframe_seconds": self.timeframe_seconds,
        }
