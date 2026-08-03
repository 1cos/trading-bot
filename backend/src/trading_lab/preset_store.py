"""Persistent preset store — atomic JSON file per preset.

Storage: one JSON file per preset in a configurable directory.
Write: temp file + flush + os.replace (atomic on POSIX).
IDs: uuid4 hex (safe for filenames, no path traversal).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

SCHEMA_VERSION = "StrategyPreset/v1"
STRATEGY_ID = "BDRR"

_VALID_DIRECTIONS = frozenset({"LONG", "SHORT", "BOTH"})
_VALID_LEVEL_SOURCES = frozenset({"ORB_HIGH", "ORB_LOW", "BOTH"})
_VALID_ENTRY_MODELS = frozenset({"CONFIRMATION_CLOSE", "BREAK_OF_SIGNAL_BAR"})
_VALID_TIMEFRAMES = frozenset({"1m", "5m", "10m", "15m", "30m"})

# Filename: only hex chars (uuid4 without dashes)
_SAFE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


# ── ID generation ────────────────────────────────────────────────────────────


def generate_preset_id() -> str:
    """Generate a safe, unique preset ID (uuid4 hex, no dashes)."""
    return uuid.uuid4().hex


def is_safe_preset_id(preset_id: str) -> bool:
    """Check that preset_id is safe for use as a filename."""
    return bool(_SAFE_ID_RE.match(preset_id))


# ── Decimal helpers ──────────────────────────────────────────────────────────


def _to_decimal_str(value, name: str) -> str:
    """Convert a numeric value to canonical decimal string.

    Accepts: str, int, float. Rejects: bool, None, non-numeric.
    Strips trailing zeros. No float artifacts.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got bool")
    if value is None:
        raise ValueError(f"{name} must not be None")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"{name} must be a valid decimal, got {value!r}")
    if d.is_nan() or d.is_infinite():
        raise ValueError(f"{name} must be finite, got {value!r}")
    return str(d.normalize()) if d != 0 else "0"


def _parse_decimal_str(s: str) -> Decimal:
    """Parse a canonical decimal string back to Decimal."""
    return Decimal(s)


# ── Validation ───────────────────────────────────────────────────────────────


def validate_preset_params(params: dict) -> list[str]:
    """Validate preset parameters. Returns list of error messages (empty = valid).

    Reuses the same rules as the backtest server.
    """
    errors = []

    # Required string fields
    for key, valid_set, label in [
        ("direction", _VALID_DIRECTIONS, "direction"),
        ("level_source", _VALID_LEVEL_SOURCES, "level_source"),
        ("entry_model", _VALID_ENTRY_MODELS, "entry_model"),
        ("timeframe", _VALID_TIMEFRAMES, "timeframe"),
    ]:
        v = params.get(key)
        if v not in valid_set:
            errors.append(f"{label} must be one of {sorted(valid_set)}, got {v!r}")

    # Required string
    sym = params.get("symbol")
    if not isinstance(sym, str) or not sym.strip():
        errors.append("symbol must be a non-empty string")

    # Positive int fields
    for key, mn, mx in [
        ("orb_duration_minutes", 1, 60),
        ("consecutive_orb_closes", 1, 20),
        ("entry_buffer_ticks", 0, 100),
        ("stop_buffer_ticks", 0, 100),
    ]:
        v = params.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or v < mn or v > mx:
            errors.append(f"{key} must be an integer {mn}-{mx}, got {v!r}")

    # Optional int or null
    mcb = params.get("min_close_beyond_level_ticks")
    if mcb is not None:
        if not isinstance(mcb, int) or isinstance(mcb, bool) or mcb < 0:
            errors.append(
                f"min_close_beyond_level_ticks must be non-negative int or null, got {mcb!r}"
            )

    # Decimal string fields — must be valid, positive (or zero for ratios)
    for key, allow_zero in [
        ("exit_target_r", False),
        ("tick_size", False),
    ]:
        v = params.get(key)
        try:
            d = _parse_decimal_str(v)
            if (not allow_zero and d <= 0) or d < 0:
                errors.append(f"{key} must be positive, got {v!r}")
        except (InvalidOperation, TypeError, ValueError):
            errors.append(f"{key} must be a valid decimal string, got {v!r}")

    # Ratio fields: 0-1 inclusive, decimal string
    for key in ("rejection_wick_ratio_min", "body_ratio_max"):
        v = params.get(key)
        if v is not None:
            try:
                d = _parse_decimal_str(v)
                if d < 0 or d > 1:
                    errors.append(f"{key} must be between 0 and 1, got {v!r}")
            except (InvalidOperation, TypeError, ValueError):
                errors.append(
                    f"{key} must be a valid decimal string or null, got {v!r}"
                )

    # Direction/Level Source coherence — canonical BDRR mapping
    dir_val = params.get("direction")
    ls_val = params.get("level_source")
    if dir_val and ls_val:
        canonical = {
            "LONG": {"ORB_HIGH"},
            "SHORT": {"ORB_LOW"},
            "BOTH": {"ORB_HIGH", "ORB_LOW", "BOTH"},
        }
        allowed = canonical.get(dir_val, set())
        if ls_val not in allowed:
            errors.append(
                f"level_source '{ls_val}' is not valid for direction "
                f"'{dir_val}'; LONG requires ORB_HIGH, SHORT requires ORB_LOW"
            )

    return errors


# ── Store ────────────────────────────────────────────────────────────────────


class PresetStore:
    """File-based persistent preset store."""

    def __init__(self, directory: Path | str):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, preset_id: str) -> Path:
        if not is_safe_preset_id(preset_id):
            raise ValueError(f"Invalid preset_id: {preset_id!r}")
        return self._dir / f"{preset_id}.json"

    def create(self, name: str, params: dict) -> dict:
        """Create and save a new preset. Returns the full preset dict."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Preset name must be a non-empty string")

        errors = validate_preset_params(params)
        if errors:
            raise ValueError("; ".join(errors))

        preset_id = generate_preset_id()
        now = datetime.now(timezone.utc).isoformat()

        preset = {
            "schema_version": SCHEMA_VERSION,
            "preset_id": preset_id,
            "name": name.strip(),
            "strategy_id": STRATEGY_ID,
            "created_at": now,
            "updated_at": now,
            "parameters": params,
        }

        self._save_atomic(preset_id, preset)
        return preset

    def get(self, preset_id: str) -> dict | None:
        """Load a preset by ID. Returns None if not found."""
        path = self._path(preset_id)
        if not path.exists():
            return None
        with open(path, "r") as f:
            return json.loads(f.read())

    def list_all(self) -> list[dict]:
        """List all valid presets as summary dicts.

        Skips temp files, non-JSON files, and corrupt files.
        Returns sorted by updated_at (newest first), then name, then id.
        """
        summaries = []
        for f in self._dir.iterdir():
            if f.suffix != ".json":
                continue
            stem = f.stem
            if not _SAFE_ID_RE.match(stem):
                continue
            try:
                with open(f, "r") as fh:
                    data = json.loads(fh.read())
                p = data.get("parameters", {})
                summaries.append({
                    "preset_id": data["preset_id"],
                    "name": data.get("name", ""),
                    "strategy_id": data.get("strategy_id", ""),
                    "symbol": p.get("symbol", ""),
                    "timeframe": p.get("timeframe", ""),
                    "direction": p.get("direction", ""),
                    "level_source": p.get("level_source", ""),
                    "updated_at": data.get("updated_at", ""),
                })
            except (json.JSONDecodeError, KeyError, TypeError):
                continue  # skip corrupt files

        summaries.sort(key=lambda s: (
            s["updated_at"],  # will sort ascending; we reverse below
            s["name"],
            s["preset_id"],
        ))
        summaries.reverse()  # newest first
        return summaries

    def _save_atomic(self, preset_id: str, preset: dict) -> None:
        """Write preset to disk atomically (temp + rename)."""
        path = self._path(preset_id)
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                f.write(json.dumps(preset, indent=2, sort_keys=False))
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(path))
        except BaseException:
            if tmp.exists():
                tmp.unlink()
            raise


# ── Preset → run config conversion ──────────────────────────────────────────


def preset_to_run_config(preset: dict) -> tuple[dict, dict]:
    """Convert a loaded preset into (preset_overrides, config_overrides)
    suitable for the backtest server's /api/run handler.

    Returns the same shape that the UI sends today.
    """
    p = preset["parameters"]

    preset_overrides = {
        "preset_id": preset["preset_id"],
        "direction": p["direction"],
        "level_source": p["level_source"],
        "orb_duration_minutes": p["orb_duration_minutes"],
        "consecutive_orb_closes": p["consecutive_orb_closes"],
        "entry_model": p["entry_model"],
        "entry_buffer_ticks": p["entry_buffer_ticks"],
        "stop_buffer_ticks": p["stop_buffer_ticks"],
    }

    # Optional params
    mcb = p.get("min_close_beyond_level_ticks")
    if mcb is not None:
        preset_overrides["min_close_beyond_level_ticks"] = mcb

    # Decimal string → float for wick/body (server expects float)
    for key in ("rejection_wick_ratio_min", "body_ratio_max"):
        v = p.get(key)
        if v is not None:
            preset_overrides[key] = float(_parse_decimal_str(v))

    config_overrides = {
        "exit_target_r": p["exit_target_r"],  # stays as string
        "tick_size": float(_parse_decimal_str(p["tick_size"])),
    }

    return preset_overrides, config_overrides
