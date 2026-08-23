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
