"""P&L giorno / settimana / mese dai record persistenti.

Fonte unica: l'output di `load_trades()`. Nessuna rilettura del
filesystem, nessun session_log, nessun evento in memoria.
"""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading_lab.live.control_api import MaxBotController, create_app


ET = ZoneInfo("America/New_York")


def _d(y=2026, m=8, d=19):
    """Mercoledi' 2026-08-19 come 'oggi' di riferimento nei test."""
    return date(y, m, d)


AS_OF = _d()                      # mercoledi
MONDAY = _d(d=17)                 # lunedi della stessa settimana
SUNDAY_PREV = _d(d=16)            # domenica precedente
LAST_MONTH = _d(m=7, d=30)
FIRST_OF_MONTH = _d(d=1)


def _entry_ms(day: date, hour=10, minute=0):
    dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)
    return int(dt.timestamp() * 1000)


def _closed(day: date, gross_pnl=100.0, result="WIN", symbol="QQQ",
            hour=10, with_session=True, with_pnl=True):
    entry_ms = _entry_ms(day, hour)
    rec = {
        "trade_id": f"{symbol}_LONG_ORB_HIGH_{entry_ms}",
        "symbol": symbol, "direction": "LONG",
        "setup_key": f"LONG:ORB_HIGH:{entry_ms}",
        "entry_timestamp_ms": entry_ms,
        "state": "CLOSED",
        "outcome": {"result": result, "exit_reason": "TARGET",
                    "gross_pnl_note": "before commissions, assumes multiplier=100"},
    }
    if with_pnl:
        rec["outcome"]["gross_pnl"] = gross_pnl
    if with_session:
        rec["setup_snapshot"] = {
            "level_source": "ORB_HIGH",
            "session": {"date": day.strftime("%Y-%m-%d"),
                        "market_timezone": "America/New_York"},
        }
    return rec


def _terminal(day: date, symbol="AMD"):
    entry_ms = _entry_ms(day, 11)
    return {
        "trade_id": f"{symbol}_SHORT_ORB_LOW_{entry_ms}",
        "symbol": symbol, "direction": "SHORT",
        "entry_timestamp_ms": entry_ms,
        "state": "REQUIRES_ATTENTION",
        "setup_snapshot": {"session": {"date": day.strftime("%Y-%m-%d"),
                                       "market_timezone": "America/New_York"}},
        "terminal": {"reason": "EXIT_RETRIES_EXHAUSTED", "retry_count": 3},
    }


def _legacy_open(day: date, symbol="MU"):
    entry_ms = _entry_ms(day, 9)
    return {
        "trade_id": f"{symbol}_SHORT_{entry_ms}",
        "symbol": symbol, "direction": "SHORT",
        "entry_timestamp_ms": entry_ms,
        "state": "OPEN", "history_status": "LEGACY_OPEN",
    }


def _open_now(day: date, symbol="MSFT"):
    entry_ms = _entry_ms(day, 9, 40)
    return {
        "trade_id": f"{symbol}_LONG_{entry_ms}",
        "symbol": symbol, "direction": "LONG",
        "entry_timestamp_ms": entry_ms, "state": "OPEN",
    }


def _summary(trades, as_of=AS_OF):
    from trading_lab.live.trade_state_store import build_trade_performance_summary
    return build_trade_performance_summary(trades, as_of_date=as_of)


# ═════════════════════════════════════════════════════════════════════════
# T1 — today
# ═════════════════════════════════════════════════════════════════════════

class TestT1Today:
    def test_two_closed_today(self):
        s = _summary([_closed(AS_OF, 100.0, "WIN"),
                      _closed(AS_OF, -40.0, "LOSS", symbol="AAPL", hour=11)])
        t = s["today"]
        assert t["gross_pnl"] == 60.0
        assert t["closed_trades"] == 2
        assert t["wins"] == 1
        assert t["losses"] == 1
        assert t["win_rate"] == 0.5

    def test_yesterday_not_in_today(self):
        s = _summary([_closed(AS_OF - timedelta(days=1), 999.0, "WIN")])
        assert s["today"]["gross_pnl"] == 0.0
        assert s["today"]["closed_trades"] == 0
        assert s["week"]["closed_trades"] == 1


# ═════════════════════════════════════════════════════════════════════════
# T2 — settimana Monday -> Sunday
# ═════════════════════════════════════════════════════════════════════════

class TestT2WeekBoundary:
    def test_monday_of_current_week_is_included(self):
        s = _summary([_closed(MONDAY, 50.0, "WIN")])
        assert s["week"]["closed_trades"] == 1
        assert s["week"]["gross_pnl"] == 50.0

    def test_previous_sunday_is_excluded(self):
        s = _summary([_closed(SUNDAY_PREV, 999.0, "WIN")])
        assert s["week"]["closed_trades"] == 0
        assert s["week"]["gross_pnl"] == 0.0

    def test_week_spans_monday_to_as_of(self):
        s = _summary([
            _closed(SUNDAY_PREV, 1000.0, "WIN"),
            _closed(MONDAY, 10.0, "WIN", symbol="A"),
            _closed(AS_OF, 5.0, "WIN", symbol="B"),
        ])
        assert s["week"]["closed_trades"] == 2
        assert s["week"]["gross_pnl"] == 15.0


# ═════════════════════════════════════════════════════════════════════════
# T3 — mese
# ═════════════════════════════════════════════════════════════════════════

class TestT3MonthBoundary:
    def test_first_of_month_included(self):
        s = _summary([_closed(FIRST_OF_MONTH, 20.0, "WIN")])
        assert s["month"]["closed_trades"] == 1
        assert s["month"]["gross_pnl"] == 20.0

    def test_previous_month_excluded(self):
        s = _summary([_closed(LAST_MONTH, 999.0, "WIN")])
        assert s["month"]["closed_trades"] == 0
        assert s["month"]["gross_pnl"] == 0.0


# ═════════════════════════════════════════════════════════════════════════
# T4 / T5 / T6 — cosa NON entra nel P&L
# ═════════════════════════════════════════════════════════════════════════

class TestT4RequiresAttentionExcluded:
    def test_counted_but_not_in_pnl(self):
        s = _summary([_closed(AS_OF, 100.0, "WIN"), _terminal(AS_OF)])
        assert s["attention_count"] == 1
        t = s["today"]
        assert t["gross_pnl"] == 100.0
        assert t["closed_trades"] == 1
        assert t["wins"] == 1
        assert t["losses"] == 0
        assert t["win_rate"] == 1.0


class TestT5LegacyOpen:
    def test_counted_but_not_in_pnl(self):
        s = _summary([_closed(AS_OF, 100.0, "WIN"),
                      _legacy_open(AS_OF - timedelta(days=1))])
        assert s["legacy_open_count"] == 1
        assert s["today"]["gross_pnl"] == 100.0
        assert s["week"]["closed_trades"] == 1


class TestT6CurrentOpen:
    def test_open_not_in_pnl(self):
        s = _summary([_open_now(AS_OF)])
        assert s["today"]["gross_pnl"] == 0.0
        assert s["today"]["closed_trades"] == 0
        assert s["open_count"] == 1
        assert s["legacy_open_count"] == 0


# ═════════════════════════════════════════════════════════════════════════
# T7 — CLOSED senza gross_pnl: contata, ma non come $0
# ═════════════════════════════════════════════════════════════════════════

class TestT7ClosedWithoutPnl:
    def test_not_treated_as_zero(self):
        """Semantica fissata: e' una trade chiusa a tutti gli effetti,
        quindi conta in closed_trades e in wins/losses, ma NON entra
        nella somma gross_pnl. Lo scarto e' esposto, non nascosto."""
        s = _summary([_closed(AS_OF, 100.0, "WIN"),
                      _closed(AS_OF, None, "LOSS", symbol="ZZZ", hour=12,
                              with_pnl=False)])
        t = s["today"]
        assert t["closed_trades"] == 2
        assert t["closed_without_pnl"] == 1
        assert t["gross_pnl"] == 100.0, "la trade senza P&L non vale 0"
        assert t["wins"] == 1
        assert t["losses"] == 1
        assert t["win_rate"] == 0.5

    def test_non_numeric_pnl_is_treated_as_missing(self):
        rec = _closed(AS_OF, 0, "WIN")
        rec["outcome"]["gross_pnl"] = "n/a"
        s = _summary([rec])
        assert s["today"]["closed_without_pnl"] == 1
        assert s["today"]["gross_pnl"] == 0.0

    def test_zero_pnl_is_a_real_value_not_missing(self):
        s = _summary([_closed(AS_OF, 0.0, "LOSS")])
        assert s["today"]["closed_without_pnl"] == 0
        assert s["today"]["closed_trades"] == 1


# ═════════════════════════════════════════════════════════════════════════
# T8 — win_rate null
# ═════════════════════════════════════════════════════════════════════════

class TestT8WinRateNull:
    def test_no_determinable_results(self):
        s = _summary([_terminal(AS_OF), _open_now(AS_OF)])
        for period in ("today", "week", "month"):
            assert s[period]["win_rate"] is None
            assert s[period]["gross_pnl"] == 0.0

    def test_empty_history(self):
        s = _summary([])
        assert s["today"]["win_rate"] is None
        assert s["today"]["closed_trades"] == 0
        assert s["attention_count"] == 0
        assert s["legacy_open_count"] == 0

    def test_win_rate_is_null_not_zero(self):
        s = _summary([_closed(AS_OF, 10.0, "UNKNOWN")])
        assert s["today"]["win_rate"] is None
        assert s["today"]["wins"] == 0
        assert s["today"]["losses"] == 0


# ═════════════════════════════════════════════════════════════════════════
# T9 — precedenza di session.date
# ═════════════════════════════════════════════════════════════════════════

class TestT9SessionDatePrecedence:
    def test_session_date_wins_over_entry_timestamp(self):
        rec = _closed(AS_OF, 100.0, "WIN")
        rec["entry_timestamp_ms"] = _entry_ms(AS_OF - timedelta(days=1))
        rec["setup_snapshot"]["session"]["date"] = AS_OF.strftime("%Y-%m-%d")

        s = _summary([rec])
        assert s["today"]["closed_trades"] == 1
        assert s["today"]["gross_pnl"] == 100.0

    def test_fallback_to_entry_timestamp_without_session(self):
        rec = _closed(AS_OF, 100.0, "WIN", with_session=False)
        assert "setup_snapshot" not in rec
        s = _summary([rec])
        assert s["today"]["closed_trades"] == 1

    def test_gross_pnl_note_uses_persisted_text(self):
        s = _summary([_closed(AS_OF, 100.0, "WIN")])
        assert s["gross_pnl_note"] == (
            "before commissions, assumes multiplier=100")


# ═════════════════════════════════════════════════════════════════════════
# T10 / T11 — endpoint
# ═════════════════════════════════════════════════════════════════════════

def _client(tmp_path, runner=None):
    ctrl = MaxBotController(trade_state_dir=tmp_path)
    ctrl._runner = runner
    app = create_app(ctrl)
    app.config["TESTING"] = True
    return app.test_client()


def _write(tmp_path, name, record):
    (tmp_path / f"{name}.json").write_text(json.dumps(record, indent=2))


class TestT10EndpointWithBotStopped:
    def test_performance_present_with_no_runner(self, tmp_path):
        today = datetime.now(ET).date()
        _write(tmp_path, "w", _closed(today, 100.0, "WIN"))
        _write(tmp_path, "l", _closed(today, -40.0, "LOSS",
                                      symbol="AAPL", hour=11))
        data = _client(tmp_path, runner=None).get("/api/trades").get_json()

        assert "performance" in data
        p = data["performance"]
        assert p["today"]["gross_pnl"] == 60.0
        assert p["today"]["win_rate"] == 0.5

    def test_performance_on_empty_history(self, tmp_path):
        data = _client(tmp_path).get("/api/trades").get_json()
        assert data["performance"]["today"]["win_rate"] is None
        assert data["performance"]["attention_count"] == 0


class TestT11BackwardCompatibleEndpoint:
    def test_existing_keys_unchanged(self, tmp_path):
        today = datetime.now(ET).date()
        _write(tmp_path, "a", _closed(today, 100.0, "WIN"))
        data = _client(tmp_path).get("/api/trades").get_json()

        assert "trades" in data and "count" in data
        assert data["count"] == 1
        assert len(data["trades"]) == 1
        assert data["trades"][0]["state"] == "CLOSED"

    def test_payload_shape_is_additive(self, tmp_path):
        data = _client(tmp_path).get("/api/trades").get_json()
        assert set(data) == {"trades", "count", "performance"}
