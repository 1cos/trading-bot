"""Futures manifest — loads and queries the futures instrument registry.

Provides:
  - load_futures_manifest(): parsed manifest dict
  - get_futures_root_symbols(): list of root symbols (MES, MNQ, ...)
  - get_futures_spec(root_symbol): spec for a root symbol
  - get_validated_contracts(root_symbol): list of IBKR-validated contracts
  - is_futures_symbol(symbol): True if symbol is a known futures root
  - register_contract(): write a new contract entry after IBKR download
  - futures_session_config(root_symbol): returns session/ORB config dict

The futures manifest is separate from the equity manifest (ibkr_manifest.json).
Futures symbols must never appear in the equity manifest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ── Paths ────────────────────────────────────────────────────────────────────

def _default_futures_dir() -> Path:
    """Return the canonical futures data directory."""
    return Path(__file__).resolve().parent.parent.parent.parent / "dati" / "futures"


def _manifest_path(futures_dir: Path | None = None) -> Path:
    d = futures_dir or _default_futures_dir()
    return d / "futures_manifest.json"


# ── Load ─────────────────────────────────────────────────────────────────────

def load_futures_manifest(futures_dir: Path | None = None) -> dict:
    """Load and return the parsed futures manifest.

    Returns empty dict structure if manifest does not exist.
    """
    mp = _manifest_path(futures_dir)
    if not mp.exists():
        return {"root_symbols": {}}
    try:
        data = json.loads(mp.read_text())
        return data
    except (json.JSONDecodeError, KeyError):
        return {"root_symbols": {}}


def get_futures_root_symbols(futures_dir: Path | None = None) -> list[str]:
    """Return sorted list of root symbols in the manifest."""
    m = load_futures_manifest(futures_dir)
    return sorted(m.get("root_symbols", {}).keys())


def get_futures_spec(root_symbol: str, futures_dir: Path | None = None) -> dict | None:
    """Return the spec dict for a root symbol, or None if not found."""
    m = load_futures_manifest(futures_dir)
    return m.get("root_symbols", {}).get(root_symbol)


def is_futures_symbol(symbol: str, futures_dir: Path | None = None) -> bool:
    """Return True if symbol is a known futures root symbol."""
    return symbol in get_futures_root_symbols(futures_dir)


def get_validated_contracts(
    root_symbol: str, futures_dir: Path | None = None
) -> list[dict]:
    """Return list of validated IBKR contracts for a root symbol.

    A contract is validated if it was registered by the downloader
    and its CSV file exists on disk.
    """
    spec = get_futures_spec(root_symbol, futures_dir)
    if not spec:
        return []
    fd = futures_dir or _default_futures_dir()
    validated = []
    for c in spec.get("contracts", []):
        csv_path = fd / c.get("file", "")
        if csv_path.exists():
            validated.append(c)
    return validated


def has_validated_data(root_symbol: str, futures_dir: Path | None = None) -> bool:
    """Return True if at least one validated IBKR contract exists."""
    return len(get_validated_contracts(root_symbol, futures_dir)) > 0


# ── Session config ───────────────────────────────────────────────────────────

def futures_session_config(
    root_symbol: str, futures_dir: Path | None = None
) -> dict | None:
    """Return session/ORB/tick config for the strategy pipeline.

    Returns dict with keys:
        timezone, session_open, session_close, orb_start,
        orb_duration_minutes, tick_size, tick_value_usd, point_value_usd

    Returns None if root_symbol not found.
    """
    spec = get_futures_spec(root_symbol, futures_dir)
    if not spec:
        return None
    return {
        "timezone": spec["strategy_session_timezone"],
        "session_open": spec["strategy_session_open"],
        "session_close": spec["strategy_session_close"],
        "orb_start": spec.get("orb_start", spec["strategy_session_open"]),
        "orb_duration_minutes": spec.get("orb_duration_minutes", 5),
        "tick_size": float(spec["tick_size"]),
        "tick_value_usd": float(spec["tick_value_usd"]),
        "point_value_usd": float(spec["point_value_usd"]),
    }


# ── Register (called by downloader after successful download) ────────────────

def register_contract(
    root_symbol: str,
    contract_info: dict[str, Any],
    futures_dir: Path | None = None,
) -> None:
    """Register a newly downloaded contract in the manifest.

    contract_info must contain:
        localSymbol, expiry, conId, tradingClass, exchange,
        file, downloaded_at, earliest_bar, latest_bar, session_count

    Raises ValueError if required fields are missing.
    """
    required = {
        "localSymbol", "expiry", "conId", "tradingClass", "exchange",
        "file", "downloaded_at", "earliest_bar", "latest_bar", "session_count",
    }
    missing = required - set(contract_info.keys())
    if missing:
        raise ValueError(f"Missing required contract fields: {sorted(missing)}")

    fd = futures_dir or _default_futures_dir()
    mp = _manifest_path(fd)
    manifest = load_futures_manifest(fd)

    if root_symbol not in manifest.get("root_symbols", {}):
        raise ValueError(
            f"Root symbol {root_symbol!r} not in manifest. "
            f"Available: {sorted(manifest.get('root_symbols', {}).keys())}"
        )

    contracts = manifest["root_symbols"][root_symbol].setdefault("contracts", [])

    # Replace if same localSymbol already registered (re-download)
    contracts[:] = [
        c for c in contracts
        if c.get("localSymbol") != contract_info["localSymbol"]
    ]
    contracts.append(contract_info)

    # Sort by expiry
    contracts.sort(key=lambda c: c.get("expiry", ""))

    mp.write_text(json.dumps(manifest, indent=2) + "\n")
