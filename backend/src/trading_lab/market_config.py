"""Market configuration reader — single source of truth for static instrument params.

Loads ``dati/market_manifest.json`` and exposes per-symbol configuration as
immutable ``MarketConfig`` objects.  All fields are validated on load;
unknown symbols, missing fields, and invalid values raise explicit errors.

Usage::

    from trading_lab.market_config import get_market_config

    cfg = get_market_config("MES")
    cfg.timezone          # "America/Chicago"
    cfg.tick_size         # 0.25
    cfg.orb_open_minutes  # 510  (08:30)

No fallback.  No silent defaults.  If a symbol is not in the manifest the
caller gets a ``KeyError``; if a field is invalid the caller gets a
``ValueError``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# ── Path ─────────────────────────────────────────────────────────────────────

def _default_manifest_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / "dati" / "market_manifest.json"


# ── Time helpers ─────────────────────────────────────────────────────────────

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def time_to_minutes(hhmm: str) -> int:
    """Convert ``"HH:MM"`` to minutes since midnight.

    >>> time_to_minutes("09:30")
    570
    >>> time_to_minutes("08:30")
    510
    """
    m = _TIME_RE.match(hhmm)
    if not m:
        raise ValueError(f"Invalid time format {hhmm!r}, expected HH:MM")
    return int(m.group(1)) * 60 + int(m.group(2))


# ── Dataclass ────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class MarketConfig:
    """Immutable static market configuration for one instrument."""

    symbol: str
    asset_class: str
    provider: str
    sec_type: str
    exchange: str
    currency: str
    timezone: str
    session_open: str
    session_close: str
    orb_open: str
    orb_close: str
    tick_size: float
    point_value: float
    price_scale: int

    # Derived — computed once at construction
    session_open_minutes: int
    session_close_minutes: int
    orb_open_minutes: int
    orb_close_minutes: int


_REQUIRED_FIELDS = {
    "asset_class", "provider", "sec_type", "exchange", "currency",
    "timezone", "session_open", "session_close",
    "orb_open", "orb_close", "tick_size", "point_value", "price_scale",
}

_VALID_ASSET_CLASSES = {"EQUITY", "FUTURE"}


def _validate_and_build(symbol: str, raw: dict) -> MarketConfig:
    """Validate raw manifest entry and return a MarketConfig.

    Raises ValueError for any invalid or missing field.
    """
    # ── Missing fields ───────────────────────────────────────────────
    missing = _REQUIRED_FIELDS - set(raw.keys())
    if missing:
        raise ValueError(
            f"[{symbol}] Missing required fields: {sorted(missing)}"
        )

    # ── asset_class ──────────────────────────────────────────────────
    ac = raw["asset_class"]
    if ac not in _VALID_ASSET_CLASSES:
        raise ValueError(
            f"[{symbol}] Invalid asset_class {ac!r}, "
            f"expected one of {sorted(_VALID_ASSET_CLASSES)}"
        )

    # ── timezone ─────────────────────────────────────────────────────
    tz = raw["timezone"]
    if not tz:
        raise ValueError(f"[{symbol}] Invalid timezone: empty string")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, KeyError):
        raise ValueError(f"[{symbol}] Invalid timezone {tz!r}")

    # ── time fields ──────────────────────────────────────────────────
    for field in ("session_open", "session_close", "orb_open", "orb_close"):
        val = raw[field]
        if not _TIME_RE.match(str(val)):
            raise ValueError(
                f"[{symbol}] Invalid {field} {val!r}, expected HH:MM"
            )

    # ── tick_size ────────────────────────────────────────────────────
    ts = raw["tick_size"]
    if not isinstance(ts, (int, float)) or ts <= 0:
        raise ValueError(f"[{symbol}] tick_size must be > 0, got {ts!r}")

    # ── point_value ──────────────────────────────────────────────────
    pv = raw["point_value"]
    if not isinstance(pv, (int, float)) or pv <= 0:
        raise ValueError(f"[{symbol}] point_value must be > 0, got {pv!r}")

    # ── price_scale ──────────────────────────────────────────────────
    ps = raw["price_scale"]
    if not isinstance(ps, int) or ps < 0:
        raise ValueError(f"[{symbol}] price_scale must be >= 0, got {ps!r}")

    # ── Build ────────────────────────────────────────────────────────
    return MarketConfig(
        symbol=symbol,
        asset_class=ac,
        provider=raw["provider"],
        sec_type=raw["sec_type"],
        exchange=raw["exchange"],
        currency=raw["currency"],
        timezone=tz,
        session_open=raw["session_open"],
        session_close=raw["session_close"],
        orb_open=raw["orb_open"],
        orb_close=raw["orb_close"],
        tick_size=float(ts),
        point_value=float(pv),
        price_scale=ps,
        session_open_minutes=time_to_minutes(raw["session_open"]),
        session_close_minutes=time_to_minutes(raw["session_close"]),
        orb_open_minutes=time_to_minutes(raw["orb_open"]),
        orb_close_minutes=time_to_minutes(raw["orb_close"]),
    )


# ── Loader ───────────────────────────────────────────────────────────────────

_cache: dict[str, MarketConfig] = {}
_default_loaded = False


def _load_all(manifest_path: Path | None = None) -> None:
    """Load and validate every entry in the manifest."""
    global _default_loaded
    mp = manifest_path or _default_manifest_path()

    if not mp.exists():
        raise FileNotFoundError(f"Market manifest not found: {mp}")

    try:
        data = json.loads(mp.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed market manifest JSON: {e}")

    if not isinstance(data, dict) or "instruments" not in data:
        raise ValueError(
            "Market manifest must have top-level 'instruments' dict"
        )

    instruments = data["instruments"]
    if not isinstance(instruments, dict) or len(instruments) == 0:
        raise ValueError("Market manifest 'instruments' must be a non-empty dict")

    new_entries: dict[str, MarketConfig] = {}
    for sym, raw in instruments.items():
        new_entries[sym] = _validate_and_build(sym, raw)

    # All validated — atomically swap cache
    _cache.clear()
    _cache.update(new_entries)

    # Only mark as "default loaded" if we used the default path
    if manifest_path is None:
        _default_loaded = True


def get_market_config(
    symbol: str,
    *,
    manifest_path: Path | None = None,
) -> MarketConfig:
    """Return the MarketConfig for *symbol*.

    Raises ``KeyError`` if the symbol is not in the manifest.
    Raises ``ValueError`` if the manifest is invalid.
    Raises ``FileNotFoundError`` if the manifest file does not exist.
    """
    if manifest_path is not None or not _default_loaded:
        _load_all(manifest_path)
    if symbol not in _cache:
        raise KeyError(
            f"Symbol {symbol!r} not in market manifest. "
            f"Available: {sorted(_cache.keys())}"
        )
    return _cache[symbol]


def get_all_symbols(*, manifest_path: Path | None = None) -> list[str]:
    """Return sorted list of all symbols in the manifest."""
    if manifest_path is not None or not _default_loaded:
        _load_all(manifest_path)
    return sorted(_cache.keys())


def reload(manifest_path: Path | None = None) -> None:
    """Force reload of the manifest (e.g. after editing)."""
    global _default_loaded
    _default_loaded = False
    _load_all(manifest_path)
