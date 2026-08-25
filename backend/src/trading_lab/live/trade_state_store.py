"""Crash-safe persistence for OPEN MaxBot trade state.

Written once, synchronously, immediately after a confirmed entry fill
(ENTRY_FILLED -> POSITION_OPEN) — so that an open option position can,
in principle, later be identified against a real IBKR position after a
restart or crash. This module implements ONLY the write side; reading
these records back and matching/adopting a position at startup is a
separate, later task.

NOT a replacement for SessionEventLog (event_stream.py). That log
remains useful for full-session audit trails, but is only exported to
disk at clean shutdown (see the STOP/RESTART persistence audit) — it
is therefore insufficient for crash recovery. This module writes
one small, atomic file per trade the moment the entry fill is
confirmed, independent of session lifecycle.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_TRADE_STATE_DIR = Path("logs/maxbot/trade_state")


def build_trade_id(symbol: str, setup_key: str) -> str:
    """Deterministic trade identifier from symbol + setup_key.

    setup_key is already "{direction}:{level_source}:{break_time_ms}"
    (see signal_detector.py), so this is stable and collision-free
    across genuinely distinct BDRR sequences — including two setups
    that share the same direction and break timestamp but originate
    from different structural levels (e.g. ORB_HIGH vs
    PREVIOUS_DAY_HIGH). Different setups on the same symbol in the
    same session therefore always produce different trade_ids/
    filenames — no random UUID needed, no overwrite risk. This
    function treats setup_key as an opaque string (just sanitizes
    colons for filename-safety) and does not depend on the exact
    number of colon-separated fields.
    """
    return f"{symbol}_{setup_key.replace(':', '_')}"


def persist_open_trade(record: dict, base_dir: Path | str = DEFAULT_TRADE_STATE_DIR) -> Path:
    """Atomically write an OPEN trade-state record to disk.

    `record` must contain a "trade_id" key; the file is named
    "<trade_id>.json" so the filename identifies the specific trade,
    not just the symbol.

    Crash-safety: writes to a hidden temp file in the same directory
    (same filesystem, so os.replace() is guaranteed atomic), flushes
    and fsyncs before replacing — a reader can never observe a
    partially-written file, and a crash mid-write leaves only a
    harmless, easily-ignored leftover .tmp file, never a corrupt
    "real" record.
    """
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    trade_id = record["trade_id"]
    final_path = base_dir / f"{trade_id}.json"
    tmp_path = base_dir / f".{trade_id}.json.tmp"

    with open(tmp_path, "w") as f:
        json.dump(record, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, final_path)

    return final_path


# ── Setup snapshot ───────────────────────────────────────────────────────────

# The structural fields copied out of DetectionResult/v1 — the "why"
# behind a trade. Order is the contract's own; every one of these is
# produced by the detector and is simply carried over here.
_SETUP_SNAPSHOT_FIELDS = (
    # Level
    "level_source", "level_price", "level_bar", "direction",
    # Break
    "break_bar", "directional_break_distance",
    # Displacement
    "displacement_window", "displacement_bar_count",
    "displacement_pts", "displacement_pct",
    # Retest
    "retest_window", "retest_bar_count",
    "failed_retest_count", "failed_retests",
    "bars_break_to_first_retest", "bars_break_to_confirmation",
    "retest_closest_approach", "retest_penetration_through_level",
    "retest_displacement_retracement_pct",
    # Confirmation / Max Entry Candle
    "confirmation_bar", "confirmation_rej_wick", "confirmation_body",
    "confirmation_opp_wick", "confirmation_favorable_close_location",
    "confirmation_penetration", "confirmation_close_beyond_level",
    # Provenance
    "schema_version", "result_id", "produced_at", "engine_version",
)


def build_setup_snapshot(detection_result: object,
                        rejection_detail: object = None) -> dict | None:
    """Freeze the structural reason for a trade into plain JSON.

    Copies — never recomputes — the break / displacement / retest /
    confirmation data the detector already produced, so a completed
    trade can later be explained without re-running anything. The
    source is DetectionResult/v1's own ``to_dict()``, so this function
    owns no serialization rules of its own and cannot drift from the
    contract.

    The result is a detached, pure-JSON value: no live reference to the
    DetectionResult or to any detector object survives in it, and
    mutating the original afterwards cannot change what was persisted.

    Returns None when there is nothing to snapshot (no detection result,
    or an object that does not expose the contract's to_dict()) —
    persistence of the trade itself must never depend on this.

    ``entry_pattern_type`` (SINGLE_CANDLE_REJECTION vs
    TWO_CANDLE_ENGULFING_RECOVERY) comes from `rejection_detail` — the
    raw rejection_finder result carried on SignalResult — because
    DetectionResult/v1 has no field for it and that contract is frozen
    for JS parity (38 fields, guarded by TestNoExtraFields). The key is
    always present in the output: None when the caller has no
    rejection_detail, so older callers and older records keep the same
    shape.

    Parameters
    ----------
    detection_result : DetectionResult/v1 or None
        Source of every structural field.
    rejection_detail : dict or None
        ``SignalResult.rejection_detail``. Only ``entry_pattern_type``
        is read from it; anything else is ignored. A non-dict or a dict
        without that key yields None rather than an error.
    """
    if detection_result is None:
        return None
    to_dict = getattr(detection_result, "to_dict", None)
    if not callable(to_dict):
        return None

    full = to_dict()
    if not isinstance(full, dict):
        return None

    snapshot = {k: full[k] for k in _SETUP_SNAPSHOT_FIELDS if k in full}
    pattern = None
    if isinstance(rejection_detail, dict):
        pattern = rejection_detail.get("entry_pattern_type")
    snapshot["entry_pattern_type"] = pattern

    # Round-trip through JSON: guarantees the stored value is plain
    # JSON and fully detached from the source object in one step.
    return json.loads(json.dumps(snapshot, default=str))
