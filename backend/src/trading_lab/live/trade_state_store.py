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
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("maxbot")

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
    return _atomic_write(record, base_dir)


def _atomic_write(record: dict, base_dir: Path | str) -> Path:
    """Write one trade-state record atomically. Shared by open/closed.

    Temp file in the same directory (same filesystem, so os.replace()
    is guaranteed atomic), flushed and fsynced before the replace — a
    reader never observes a partial file, and a crash mid-write leaves
    at most a harmless .tmp leftover, never a corrupt record. Writing
    the CLOSED update through this same path means an in-flight close
    can never destroy the OPEN record.
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


def persist_terminal_trade(
    trade_id: str,
    state: str,
    terminal: dict,
    base_dir: Path | str = DEFAULT_TRADE_STATE_DIR,
) -> Path:
    """Mark a trade whose automatic management ended WITHOUT a
    confirmed exit fill.

    Same file, same atomic rewrite, same preservation rules as
    persist_closed_trade() — only the state value and the block name
    differ. The distinction matters operationally:

        OPEN               position under normal management
        CLOSED             exit fill confirmed, outcome known
        REQUIRES_ATTENTION automatic management stopped; the position
                           may still be open at the broker

    `terminal` is stored verbatim under "terminal". It deliberately
    carries no exit fill price and no P&L: none exists, and inventing
    either would make an unresolved position look settled.
    """
    return _persist_final(trade_id, state, "terminal", terminal, base_dir)


def persist_closed_trade(
    trade_id: str,
    outcome: dict,
    base_dir: Path | str = DEFAULT_TRADE_STATE_DIR,
) -> Path:
    """Close out an existing trade-state record in place.

    Rewrites the SAME "<trade_id>.json" the entry fill created, through
    the same atomic write: one file per trade for its whole life, never
    a second file and never an append. Everything already in the record
    is carried over untouched — in particular ``setup_snapshot``, which
    is the only durable copy of why the trade was taken — and only
    ``state`` flips to "CLOSED" with an ``outcome`` block added.

    `outcome` is stored verbatim under that key. Callers pass values the
    system already produced (the TRADE_COMPLETED summary plus the exit
    order id); nothing is computed or inferred here.

    If no prior record exists — the entry-fill write failed, or the file
    was removed — a CLOSED record is still written from what is known,
    so an outcome is never silently dropped. Such a record simply has no
    setup_snapshot, which is visible rather than hidden.
    """
    return _persist_final(trade_id, "CLOSED", "outcome", outcome, base_dir)


def _persist_final(
    trade_id: str,
    state: str,
    block_key: str,
    block: dict,
    base_dir: Path | str,
) -> Path:
    """Rewrite an existing record with its final state. Shared by
    persist_closed_trade() and persist_terminal_trade().

    Everything already on disk is carried over untouched — in
    particular ``setup_snapshot``, the only durable copy of why the
    trade was taken. Only ``state`` and the one final block are set.
    """
    base_dir = Path(base_dir)
    existing_path = base_dir / f"{trade_id}.json"

    record: dict = {}
    if existing_path.exists():
        try:
            loaded = json.loads(existing_path.read_text())
            if isinstance(loaded, dict):
                record = loaded
        except (OSError, ValueError):
            # Unreadable prior record: prefer writing the final state we
            # do have over losing it. Never raise on the read side.
            record = {}

    record["trade_id"] = trade_id
    record["state"] = state
    record[block_key] = block

    return _atomic_write(record, base_dir)


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
    # Session — the canonical trading date and market timezone the
    # engine itself used (SessionMetadata: date, market_timezone,
    # session_open/close_utc_ms, timeframe_seconds). Carried so a trade
    # states which session it belongs to instead of a later reader
    # having to re-derive it from entry_timestamp_ms with a timezone
    # nothing in the record exposes. Copied from the contract, never
    # recomputed, and no parallel trade_date/timezone field is added.
    "session",
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


# ── Read side ────────────────────────────────────────────────────────────────

# Timezone used to decide which trading session a record belongs to when
# the record itself does not say. New records carry
# setup_snapshot.session.market_timezone and that is always preferred;
# this is only the fallback for pre-session-metadata records, and matches
# MaxBotRunner's own market_timezone default.
_FALLBACK_MARKET_TIMEZONE = "America/New_York"

# Richness ranking for choosing between records of the same trade. A
# state that can only be reached at the end of a trade carries strictly
# more information than OPEN.
_STATE_RANK = {
    "CLOSED": 2,
    "REQUIRES_ATTENTION": 2,
    "OPEN": 1,
}


def _trade_identity(record: dict, fallback: str) -> tuple:
    """Logical identity of a trade, independent of its filename.

    Deliberately NOT setup_key. Two files can describe the same trade
    with different setup_keys: the format gained a level_source segment
    partway through, so the same sequence is on disk as both
    "LONG:1786455300000" and "LONG:ORB_HIGH:1786455300000". Keying on
    setup_key would leave that trade counted twice.

    (symbol, direction, entry_timestamp_ms) is the system's own trade
    identity — it is exactly the dedup key signal_dedup.py uses to fold
    same-entry-candle signals into one executable candidate, so two
    genuinely distinct trades can never share it.

    Records too malformed to identify that way fall back to setup_key,
    then to `fallback` (the filename stem), so a broken record stays
    separate instead of being silently merged into another trade.
    """
    symbol = record.get("symbol")
    direction = record.get("direction")
    entry_ms = record.get("entry_timestamp_ms")
    if symbol and direction and entry_ms:
        return ("entry", symbol, direction, entry_ms)
    setup_key = record.get("setup_key")
    if symbol and setup_key:
        return ("setup", symbol, setup_key)
    return ("file", fallback)


def _richness(record: dict) -> tuple:
    """Sort key for picking the better of two records of one trade.

    Terminal states beat OPEN, then having a setup_snapshot, then having
    an outcome/terminal block, then sheer field count. Never the file's
    mtime or name: a legacy record can easily be the newer file on disk.
    """
    state = str(record.get("state") or "")
    return (
        _STATE_RANK.get(state, 0),
        1 if isinstance(record.get("setup_snapshot"), dict) else 0,
        1 if ("outcome" in record or "terminal" in record) else 0,
        len(record),
    )


def _session_date_of(record: dict) -> tuple[str | None, str]:
    """(session date YYYY-MM-DD, market timezone) for one record.

    Prefers the canonical date the engine itself recorded. Only when
    that is absent — records written before session metadata was
    persisted — is the date derived from entry_timestamp_ms.
    """
    snapshot = record.get("setup_snapshot")
    session = snapshot.get("session") if isinstance(snapshot, dict) else None
    tz_name = _FALLBACK_MARKET_TIMEZONE
    if isinstance(session, dict):
        tz_name = session.get("market_timezone") or tz_name
        date = session.get("date")
        if isinstance(date, str) and date:
            return date, tz_name

    entry_ms = record.get("entry_timestamp_ms")
    if not isinstance(entry_ms, (int, float)):
        return None, tz_name
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(_FALLBACK_MARKET_TIMEZONE)
    dt = datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc).astimezone(tz)
    return dt.strftime("%Y-%m-%d"), tz_name


def load_trades(
    base_dir: Path | str = DEFAULT_TRADE_STATE_DIR,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Read every persisted trade record, newest first.

    Pure read: never writes, never repairs, never rewrites a file. What
    is on disk is the source of truth; anything this function adds is
    read-side annotation on the returned copy only.

    Robustness is the point — this is the only view of history that
    survives a crash. A directory that does not exist yields []. A file
    that is unreadable, not JSON, or not a JSON object is skipped with a
    warning and never aborts the load.

    Duplicates are folded by logical identity (see _trade_identity), and
    the richer record wins (see _richness).

    Annotation: an OPEN record whose trading session has already ended
    gets ``history_status: "LEGACY_OPEN"``. It marks a record written
    before final-state persistence existed, or a trade left unresolved
    by a crash — either way, an OPEN that is not actually being managed
    right now. The record's own ``state`` is left untouched: this
    function has no way to know how such a trade really ended, and
    guessing CLOSED would invent an outcome. An OPEN from the current
    session is genuinely live and is never marked.

    Parameters
    ----------
    base_dir : Path or str
        Directory holding "<trade_id>.json" records.
    now : datetime or None
        Reference time for the current-session check. Defaults to the
        real clock; injectable so the boundary is testable.

    Returns
    -------
    list[dict]
        Plain JSON dicts, sorted by entry_timestamp_ms descending, with
        trade_id as a stable tie-break.
    """
    base_dir = Path(base_dir)
    if not base_dir.is_dir():
        return []

    best: dict[tuple, dict] = {}
    for path in sorted(base_dir.glob("*.json")):
        # Skip the atomic-write temp files (".<trade_id>.json.tmp" also
        # fails this glob, but a leftover ".foo.json" would not).
        if path.name.startswith("."):
            continue
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            log.warning(f"trade_state: skipping unreadable {path.name}: {e}")
            continue
        if not isinstance(record, dict):
            log.warning(f"trade_state: skipping non-object {path.name}")
            continue

        identity = _trade_identity(record, path.stem)
        current = best.get(identity)
        if current is None or _richness(record) > _richness(current):
            best[identity] = record

    trades = []
    for record in best.values():
        record = dict(record)          # annotate the copy, never the file
        if str(record.get("state") or "") == "OPEN":
            session_date, tz_name = _session_date_of(record)
            if session_date and session_date < _today_in(tz_name, now):
                record["history_status"] = "LEGACY_OPEN"
        trades.append(record)

    trades.sort(
        key=lambda r: (r.get("entry_timestamp_ms") or 0,
                       str(r.get("trade_id") or "")),
        reverse=True,
    )
    return trades


def _today_in(tz_name: str, now: datetime | None) -> str:
    """Current trading date in `tz_name`, as YYYY-MM-DD."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(_FALLBACK_MARKET_TIMEZONE)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference.astimezone(tz).strftime("%Y-%m-%d")
