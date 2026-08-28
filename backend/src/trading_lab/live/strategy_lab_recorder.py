"""Append-only persistence for Strategy Lab observations.

Two files per session date, both JSONL so a crash costs at most the
last line:

    logs/maxbot/strategy_lab/YYYY-MM-DD/candidates.jsonl
    logs/maxbot/strategy_lab/YYYY-MM-DD/<SYMBOL>_bars.jsonl

The bar tape is the less obvious half and the more important one. Every
audit run so far has stalled on the same gap: `trade_state` keeps a
window of candles around a trade and nothing else, so a shadow candidate
that was never traded has no forward data and its R outcome cannot be
settled at any price. The historical CSVs stop weeks back and do not
cover every symbol. Writing the completed bars as they arrive costs one
line per symbol per minute — roughly 390 lines a day — and makes every
question in this file answerable offline, afterwards, without a second
IBKR connection during a live session.

Every entry point swallows its own exceptions. A recorder that can break
a trading session is worse than no recorder at all, and there is nothing
here whose failure should cost anything more than a log line.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("maxbot")

DEFAULT_LAB_DIR = "logs/maxbot/strategy_lab"
CANDIDATES_FILE = "candidates.jsonl"


def _session_date(time_ms: int, tz: str) -> str:
    return datetime.fromtimestamp(time_ms / 1000, ZoneInfo(tz)).strftime("%Y-%m-%d")


def _append(path: Path, payload: dict) -> bool:
    """One JSON object per line, flushed. Returns success, never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, separators=(",", ":"), default=str))
            fh.write("\n")
            fh.flush()
        return True
    except Exception as e:                                  # noqa: BLE001
        log.debug(f"strategy lab append failed for {path}: {e}")
        return False


class StrategyLabRecorder:
    """Writes shadow observations. Holds no trading state and is never
    consulted by anything that decides."""

    def __init__(self, base_dir: str | Path = DEFAULT_LAB_DIR,
                 market_timezone: str = "America/New_York",
                 enabled: bool = True):
        self._base = Path(base_dir)
        self._tz = market_timezone
        self.enabled = enabled
        self._bars_written = 0
        self._candidates_written = 0

    # ── paths ───────────────────────────────────────────────────────────
    def candidates_path(self, date: str) -> Path:
        return self._base / date / CANDIDATES_FILE

    def bars_path(self, date: str, symbol: str) -> Path:
        return self._base / date / f"{symbol}_bars.jsonl"

    # ── writes ──────────────────────────────────────────────────────────
    def record_bar(self, symbol: str, candle: dict) -> bool:
        """Append one completed bar to the symbol's tape."""
        if not self.enabled:
            return False
        try:
            date = _session_date(candle["time_ms"], self._tz)
            row = {"time_ms": candle["time_ms"], "open": candle["open"],
                   "high": candle["high"], "low": candle["low"],
                   "close": candle["close"], "volume": candle.get("volume")}
            ok = _append(self.bars_path(date, symbol), row)
            self._bars_written += int(ok)
            return ok
        except Exception as e:                              # noqa: BLE001
            log.debug(f"[{symbol}] strategy lab bar capture failed: {e}")
            return False

    def record_candidate(self, symbol: str, record: dict) -> bool:
        """Append one shadow candidate. `record` comes from strategy_lab."""
        if not self.enabled or not record:
            return False
        try:
            anchor = (record.get("candle1") or record.get("candle") or {}).get("time_ms")
            if anchor is None:
                return False
            date = _session_date(anchor, self._tz)
            payload = {"symbol": symbol, "session_date": date,
                       "bar_time_ms": anchor, **record}
            ok = _append(self.candidates_path(date), payload)
            self._candidates_written += int(ok)
            return ok
        except Exception as e:                              # noqa: BLE001
            log.debug(f"[{symbol}] strategy lab candidate capture failed: {e}")
            return False

    # ── reads (offline, for the weekly report) ──────────────────────────
    def load_candidates(self, date: str) -> list[dict]:
        return _read_jsonl(self.candidates_path(date))

    def load_bars(self, date: str, symbol: str) -> list[dict]:
        rows = _read_jsonl(self.bars_path(date, symbol))
        # The tape is append-only and a resubscribe can replay a bar, so
        # de-duplicate on time_ms keeping the last copy seen.
        seen: dict[int, dict] = {}
        for r in rows:
            if isinstance(r.get("time_ms"), int):
                seen[r["time_ms"]] = r
        return [seen[k] for k in sorted(seen)]

    @property
    def counters(self) -> dict:
        return {"bars": self._bars_written, "candidates": self._candidates_written}


def _read_jsonl(path: Path) -> list[dict]:
    """Tolerant reader: a truncated final line is skipped, not fatal."""
    out: list[dict] = []
    try:
        if not path.exists():
            return out
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:                                  # noqa: BLE001
        log.debug(f"strategy lab read failed for {path}: {e}")
    return out


def build_from_setup_snapshot(snapshot, candles, entry_index, *, direction,
                              levels, stop_price=None, engine_verdict="PASS"):
    """Shadow metrics for an entry the bot has just taken.

    This lives here rather than in the orchestrator on purpose. The
    orchestrator is required to stay level-source agnostic — it must
    never name or branch on ORB vs PDH/PDL, and a test enforces that on
    its source text. Reading the source and the far edge out of the
    snapshot is exactly the kind of knowledge that belongs on this side
    of the line, so the caller passes state and gets a dict back
    without ever mentioning a level source.

    Returns None whenever the snapshot cannot support a record. A
    missing shadow row is a gap in a lab file; a raise here would be a
    gap in a trade record.
    """
    from trading_lab import strategy_lab
    from trading_lab.atr import atr_series

    if not snapshot or entry_index is None or entry_index >= len(candles):
        return None
    level = snapshot.get("level_price") or {}
    tick_size = float(level.get("tick_size") or 0)
    if not tick_size or "ticks" not in level:
        return None
    level_price = int(level["ticks"]) * tick_size
    source = snapshot.get("level_source")

    # The far edge is the opposite side of the structural zone. Line
    # sources (PDH/PDL) have none, which is precisely why the engine
    # never offers them a TWO_CANDLE.
    levels = levels or {}
    far_edge = levels.get("orb_low") if direction == "LONG" else levels.get("orb_high")

    atr_cache = atr_series(candles, 14)
    i = entry_index

    if str(snapshot.get("entry_pattern_type", "")).startswith("TWO"):
        if i < 1:
            return None
        return strategy_lab.two_candle_shadow(
            candles[i - 1], candles[i], direction=direction,
            level_price=level_price, level_source=source, far_edge=far_edge,
            tick_size=tick_size, atr=atr_cache[i - 2] if i >= 2 else None,
            engine_verdict=engine_verdict,
        )
    return strategy_lab.single_shadow(
        candles[i], direction=direction, level_price=level_price,
        level_source=source, tick_size=tick_size,
        atr=atr_cache[i - 1] if i >= 1 else None, stop_price=stop_price,
        engine_verdict=engine_verdict,
    )


def available_dates(base_dir: str | Path = DEFAULT_LAB_DIR) -> list[str]:
    base = Path(base_dir)
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())
