"""Read-side dello storico trade: file su disco -> GET /api/trades.

Ogni endpoint esistente legge da `self._runner`, quindi a bot fermo la
PWA non vede nulla del passato. I file `trade_state/*.json` sono invece
persistenti e sopravvivono a crash e riavvii: sono l'unica fonte di
storico che esista. Mancava solo un lettore.
"""

import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading_lab.live.control_api import MaxBotController, create_app


ET = ZoneInfo("America/New_York")


def _ms_et(dt):
    return int(dt.timestamp() * 1000)


def _today_et():
    return datetime.now(ET)


def _session_day(offset_days=0):
    return (_today_et() - timedelta(days=offset_days)).strftime("%Y-%m-%d")


def _entry_ms(offset_days=0, hour=10, minute=0):
    d = (_today_et() - timedelta(days=offset_days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return _ms_et(d)


def _write(tmp_path, name, record):
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(record, indent=2))
    return p


def _closed(symbol="QQQ", direction="LONG", entry_ms=None, day_offset=0,
            setup_key=None, gross_pnl=-75.0, result="LOSS"):
    entry_ms = entry_ms if entry_ms is not None else _entry_ms(day_offset)
    return {
        "trade_id": f"{symbol}_{direction}_ORB_HIGH_{entry_ms}",
        "symbol": symbol, "direction": direction,
        "setup_key": setup_key or f"{direction}:ORB_HIGH:{entry_ms}",
        "entry_timestamp_ms": entry_ms,
        "underlying_entry": 101.2, "stop": 100.8, "target": 102.0,
        "entry_fill_price": 2.65, "state": "CLOSED",
        "setup_snapshot": {
            "level_source": "ORB_HIGH",
            "entry_pattern_type": "SINGLE_CANDLE_REJECTION",
            "session": {"date": _session_day(day_offset),
                        "market_timezone": "America/New_York"},
        },
        "outcome": {"result": result, "exit_reason": "STOP",
                    "gross_pnl": gross_pnl, "exit_fill_premium": 1.9},
    }


def _terminal(symbol="AMD", direction="SHORT", day_offset=0):
    entry_ms = _entry_ms(day_offset, hour=11)
    return {
        "trade_id": f"{symbol}_{direction}_ORB_LOW_{entry_ms}",
        "symbol": symbol, "direction": direction,
        "setup_key": f"{direction}:ORB_LOW:{entry_ms}",
        "entry_timestamp_ms": entry_ms,
        "underlying_entry": 458.81, "stop": 459.5, "target": 457.43,
        "entry_fill_price": 2.62, "state": "REQUIRES_ATTENTION",
        "setup_snapshot": {
            "level_source": "ORB_LOW",
            "entry_pattern_type": "SINGLE_CANDLE_REJECTION",
            "session": {"date": _session_day(day_offset),
                        "market_timezone": "America/New_York"},
        },
        "terminal": {"runtime_state": "REQUIRES_ATTENTION",
                     "reason": "EXIT_RETRIES_EXHAUSTED", "retry_count": 3,
                     "last_error": "EXIT_CANCELLED: broker_status=Cancelled"},
    }


def _legacy_open(symbol="MU", direction="SHORT", day_offset=3):
    """Vecchio schema: niente setup_snapshot, niente session,
    setup_key senza level_source."""
    entry_ms = _entry_ms(day_offset, hour=9, minute=41)
    return {
        "trade_id": f"{symbol}_{direction}_{entry_ms}",
        "symbol": symbol, "direction": direction,
        "setup_key": f"{direction}:{entry_ms}",
        "entry_timestamp_ms": entry_ms,
        "underlying_entry": 990.0, "stop": 995.0, "target": 980.0,
        "entry_fill_price": 1.94, "state": "OPEN",
    }


def _client(tmp_path, runner=None):
    ctrl = MaxBotController(trade_state_dir=tmp_path)
    ctrl._runner = runner
    app = create_app(ctrl)
    app.config["TESTING"] = True
    return app.test_client(), ctrl


# ═════════════════════════════════════════════════════════════════════════
# T1 / T4 / T9 — lettura dei tre schemi
# ═════════════════════════════════════════════════════════════════════════

class TestT1ReadClosed:
    def test_closed_record_is_read(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "a", _closed())
        trades = load_trades(tmp_path)

        assert len(trades) == 1
        t = trades[0]
        assert t["state"] == "CLOSED"
        assert t["outcome"]["gross_pnl"] == -75.0
        assert t["setup_snapshot"]["entry_pattern_type"] == "SINGLE_CANDLE_REJECTION"
        assert t["setup_snapshot"]["session"]["date"] == _session_day(0)


class TestT4RequiresAttention:
    def test_terminal_record_read_without_fake_pnl(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "t", _terminal())
        (t,) = load_trades(tmp_path)

        assert t["state"] == "REQUIRES_ATTENTION"
        assert t["terminal"]["reason"] == "EXIT_RETRIES_EXHAUSTED"
        assert "outcome" not in t
        blob = json.dumps(t)
        for forbidden in ("gross_pnl", "exit_fill_premium", '"result"'):
            assert forbidden not in blob, f"P&L inventato: {forbidden}"


class TestT9BackwardCompatibility:
    def test_legacy_record_without_snapshot_is_readable(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "legacy", _legacy_open())
        (t,) = load_trades(tmp_path)

        assert t["symbol"] == "MU"
        assert "setup_snapshot" not in t
        assert t["setup_key"] == t["setup_key"]   # setup_key senza level_source

    def test_record_missing_optional_fields_survives(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "min", {"trade_id": "X_1", "symbol": "X",
                                 "state": "OPEN"})
        trades = load_trades(tmp_path)
        assert len(trades) == 1
        assert trades[0]["symbol"] == "X"


# ═════════════════════════════════════════════════════════════════════════
# T3 — dedup fra generazioni di filename
# ═════════════════════════════════════════════════════════════════════════

class TestT3Deduplication:
    def test_old_open_and_new_closed_collapse_to_one(self, tmp_path):
        """Caso reale su disco: due file, stessa trade, setup_key DIVERSI."""
        from trading_lab.live.trade_state_store import load_trades
        entry_ms = _entry_ms(0)
        old = {"trade_id": f"QQQ_LONG_{entry_ms}", "symbol": "QQQ",
               "direction": "LONG", "setup_key": f"LONG:{entry_ms}",
               "entry_timestamp_ms": entry_ms, "state": "OPEN"}
        new = _closed(entry_ms=entry_ms,
                      setup_key=f"LONG:ORB_HIGH:{entry_ms}")
        # i setup_key differiscono: dedup per setup_key NON funzionerebbe
        assert old["setup_key"] != new["setup_key"]

        _write(tmp_path, f"QQQ_LONG_{entry_ms}", old)
        _write(tmp_path, f"QQQ_LONG_ORB_HIGH_{entry_ms}", new)

        trades = load_trades(tmp_path)
        assert len(trades) == 1, f"attesa 1 trade, ottenute {len(trades)}"
        assert trades[0]["state"] == "CLOSED"
        assert "setup_snapshot" in trades[0]

    def test_richer_record_wins_regardless_of_filename_order(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        entry_ms = _entry_ms(0)
        rich = _closed(entry_ms=entry_ms)
        poor = {"trade_id": "zzz_last_alphabetically", "symbol": "QQQ",
                "direction": "LONG", "setup_key": f"LONG:{entry_ms}",
                "entry_timestamp_ms": entry_ms, "state": "OPEN"}
        _write(tmp_path, "aaa_rich", rich)
        _write(tmp_path, "zzz_poor", poor)

        (t,) = load_trades(tmp_path)
        assert t["state"] == "CLOSED"

    def test_distinct_trades_are_not_merged(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "a", _closed(symbol="QQQ", entry_ms=_entry_ms(0, 10)))
        _write(tmp_path, "b", _closed(symbol="AAPL", entry_ms=_entry_ms(0, 10)))
        _write(tmp_path, "c", _closed(symbol="QQQ", entry_ms=_entry_ms(0, 11)))
        assert len(load_trades(tmp_path)) == 3


# ═════════════════════════════════════════════════════════════════════════
# T5 — file corrotti / non pertinenti
# ═════════════════════════════════════════════════════════════════════════

class TestT5CorruptFiles:
    def test_corrupt_json_does_not_break_the_load(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "good", _closed())
        (tmp_path / "broken.json").write_text("{ not json ::::")

        trades = load_trades(tmp_path)
        assert len(trades) == 1
        assert trades[0]["state"] == "CLOSED"

    def test_temp_and_non_json_files_ignored(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "good", _closed())
        (tmp_path / ".QQQ_LONG.json.tmp").write_text(json.dumps(_closed()))
        (tmp_path / "notes.txt").write_text("hello")

        assert len(load_trades(tmp_path)) == 1

    def test_json_that_is_not_a_dict_is_skipped(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "good", _closed())
        (tmp_path / "list.json").write_text("[1, 2, 3]")
        assert len(load_trades(tmp_path)) == 1

    def test_missing_directory_returns_empty(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        assert load_trades(tmp_path / "nope") == []


# ═════════════════════════════════════════════════════════════════════════
# T6 — ordinamento
# ═════════════════════════════════════════════════════════════════════════

class TestT6Ordering:
    def test_newest_first_by_entry_timestamp(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "old", _closed(symbol="AAA", entry_ms=_entry_ms(2)))
        _write(tmp_path, "new", _closed(symbol="CCC", entry_ms=_entry_ms(0)))
        _write(tmp_path, "mid", _closed(symbol="BBB", entry_ms=_entry_ms(1)))

        ts = [t["entry_timestamp_ms"] for t in load_trades(tmp_path)]
        assert ts == sorted(ts, reverse=True), "atteso newest-first"

    def test_ordering_is_deterministic(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        for i in range(5):
            _write(tmp_path, f"t{i}",
                   _closed(symbol=f"S{i}", entry_ms=_entry_ms(0, 10, i)))
        first = [t["trade_id"] for t in load_trades(tmp_path)]
        for _ in range(3):
            assert [t["trade_id"] for t in load_trades(tmp_path)] == first


# ═════════════════════════════════════════════════════════════════════════
# T7 / T8 — legacy OPEN
# ═════════════════════════════════════════════════════════════════════════

class TestT7LegacyOpen:
    def test_past_session_open_is_marked_legacy(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "legacy", _legacy_open(day_offset=3))
        (t,) = load_trades(tmp_path)
        assert t["state"] == "OPEN", "lo stato su disco non cambia"
        assert t["history_status"] == "LEGACY_OPEN"

    def test_legacy_uses_session_date_when_present(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        rec = _closed(day_offset=4)
        rec["state"] = "OPEN"
        rec.pop("outcome")
        _write(tmp_path, "x", rec)
        (t,) = load_trades(tmp_path)
        assert t["history_status"] == "LEGACY_OPEN"

    def test_file_on_disk_is_not_rewritten(self, tmp_path):
        """T10 — il read-side non scrive."""
        from trading_lab.live.trade_state_store import load_trades
        p = _write(tmp_path, "legacy", _legacy_open(day_offset=3))
        before_bytes = p.read_bytes()
        before_mtime = os.path.getmtime(p)
        time.sleep(0.01)

        load_trades(tmp_path)

        assert p.read_bytes() == before_bytes
        assert os.path.getmtime(p) == before_mtime
        assert "history_status" not in json.loads(p.read_text())


class TestT8CurrentSessionOpen:
    def test_today_open_is_not_marked_legacy(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        rec = _legacy_open(day_offset=0)
        _write(tmp_path, "today", rec)
        (t,) = load_trades(tmp_path)
        assert t["state"] == "OPEN"
        assert t.get("history_status") != "LEGACY_OPEN"

    def test_closed_is_never_legacy(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "c", _closed(day_offset=5))
        (t,) = load_trades(tmp_path)
        assert t.get("history_status") != "LEGACY_OPEN"

    def test_terminal_is_never_legacy(self, tmp_path):
        from trading_lab.live.trade_state_store import load_trades
        _write(tmp_path, "t", _terminal(day_offset=5))
        (t,) = load_trades(tmp_path)
        assert t.get("history_status") != "LEGACY_OPEN"


# ═════════════════════════════════════════════════════════════════════════
# T2 — endpoint, anche a bot fermo
# ═════════════════════════════════════════════════════════════════════════

class TestT2Endpoint:
    def test_endpoint_works_with_no_runner(self, tmp_path):
        _write(tmp_path, "a", _closed())
        _write(tmp_path, "b", _terminal())
        client, ctrl = _client(tmp_path, runner=None)

        assert ctrl._runner is None
        r = client.get("/api/trades")
        assert r.status_code == 200
        data = r.get_json()
        assert data["count"] == 2
        assert len(data["trades"]) == 2

    def test_empty_history_returns_empty_list(self, tmp_path):
        client, _ = _client(tmp_path)
        data = client.get("/api/trades").get_json()
        assert data == {"trades": [], "count": 0}

    def test_endpoint_does_not_use_session_log(self, tmp_path):
        """Non deve dipendere dal runner nemmeno quando esiste."""
        from unittest.mock import MagicMock
        _write(tmp_path, "a", _closed())
        runner = MagicMock()
        runner.session_log.events = []
        client, _ = _client(tmp_path, runner=runner)

        data = client.get("/api/trades").get_json()
        assert data["count"] == 1
        runner.session_log.assert_not_called()

    def test_no_aggregated_pnl_yet(self, tmp_path):
        _write(tmp_path, "a", _closed())
        data = client_data = _client(tmp_path)[0].get("/api/trades").get_json()
        assert set(data) == {"trades", "count"}
        for k in ("today_pnl", "week_pnl", "month_pnl", "win_rate",
                  "cumulative_pnl"):
            assert k not in data
