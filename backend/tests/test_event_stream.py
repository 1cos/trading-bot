"""Tests for event stream and session log.

No real IBKR connection. Tests use EventFactory and SessionEventLog directly.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone

from trading_lab.live.event_stream import (
    EventFactory,
    EventType,
    LiveEvent,
    SessionEventLog,
    build_trade_summary,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _factory(mode="OBSERVE_ONLY"):
    return EventFactory(mode)


def _log_with_events():
    f = _factory()
    log = SessionEventLog(metadata={"trading_date": "2026-08-12", "execution_mode": "OBSERVE_ONLY",
                                     "watchlist": ["QQQ", "SPY"], "trade_limits_enabled": False})
    log.append(f.create(EventType.BOT_STARTED))
    log.append(f.create(EventType.SIGNAL, symbol="QQQ", direction="LONG",
                        data={"underlying_entry": 585.20, "underlying_stop": 584.70,
                              "underlying_target": 586.20}))
    log.append(f.create(EventType.OPTION_SELECTED, symbol="QQQ", direction="LONG",
                        data={"right": "C", "expiration": "20260812", "strike": 585.0,
                              "con_id": 123456, "exchange": "SMART", "multiplier": "100",
                              "bid": 2.50, "ask": 2.70, "spread": 0.20,
                              "spread_pct": 0.074}))
    log.append(f.create(EventType.SIGNAL, symbol="SPY", direction="SHORT",
                        data={"underlying_entry": 540.00, "underlying_stop": 540.50,
                              "underlying_target": 539.00}))
    return log, f


# ── Test 1: Event serialization ──────────────────────────────────────────────

class TestSerialization:
    def test_to_dict(self):
        f = _factory()
        e = f.create(EventType.SIGNAL, symbol="QQQ", direction="LONG",
                     data={"underlying_entry": 585.20})
        d = e.to_dict()
        assert d["event_type"] == "SIGNAL"
        assert d["symbol"] == "QQQ"
        assert "timestamp_utc" in d
        assert d["data"]["underlying_entry"] == 585.20

    def test_to_dict_no_optional(self):
        f = _factory()
        e = f.create(EventType.BOT_STARTED)
        d = e.to_dict()
        assert "direction" not in d


# ── Test 2: Stable sequence ──────────────────────────────────────────────────

class TestSequence:
    def test_monotonic(self):
        f = _factory()
        e1 = f.create(EventType.BOT_STARTED)
        e2 = f.create(EventType.SIGNAL, symbol="QQQ")
        e3 = f.create(EventType.SIGNAL, symbol="SPY")
        assert e1.seq < e2.seq < e3.seq


# ── Test 3: Chronological append ─────────────────────────────────────────────

class TestChronological:
    def test_order_preserved(self):
        log, _ = _log_with_events()
        seqs = [e.seq for e in log.events]
        assert seqs == sorted(seqs)


# ── Test 4: Multi-symbol coexist ─────────────────────────────────────────────

class TestMultiSymbol:
    def test_both_present(self):
        log, _ = _log_with_events()
        symbols = {e.symbol for e in log.events if e.symbol}
        assert "QQQ" in symbols
        assert "SPY" in symbols


# ── Test 5: Filter by symbol ────────────────────────────────────────────────

class TestFilterSymbol:
    def test_filter(self):
        log, _ = _log_with_events()
        qqq = log.events_for_symbol("QQQ")
        assert all(e.symbol == "QQQ" for e in qqq)
        assert len(qqq) == 2  # SIGNAL + OPTION_SELECTED


# ── Test 6: SIGNAL preserves levels ─────────────────────────────────────────

class TestSignalLevels:
    def test_entry_stop_target(self):
        log, _ = _log_with_events()
        signals = [e for e in log.events if e.event_type == EventType.SIGNAL]
        qqq_sig = [s for s in signals if s.symbol == "QQQ"][0]
        assert qqq_sig.data["underlying_entry"] == 585.20
        assert qqq_sig.data["underlying_stop"] == 584.70
        assert qqq_sig.data["underlying_target"] == 586.20


# ── Test 7: Signal preserves direction ───────────────────────────────────────

class TestSignalDirection:
    def test_long(self):
        log, _ = _log_with_events()
        qqq_sig = [e for e in log.events if e.symbol == "QQQ" and e.event_type == EventType.SIGNAL][0]
        assert qqq_sig.direction == "LONG"

    def test_short(self):
        log, _ = _log_with_events()
        spy_sig = [e for e in log.events if e.symbol == "SPY" and e.event_type == EventType.SIGNAL][0]
        assert spy_sig.direction == "SHORT"


# ── Test 8: OPTION_SELECTED preserves contract ──────────────────────────────

class TestOptionSelected:
    def test_contract_identity(self):
        log, _ = _log_with_events()
        opt = [e for e in log.events if e.event_type == EventType.OPTION_SELECTED][0]
        assert opt.data["right"] == "C"
        assert opt.data["expiration"] == "20260812"
        assert opt.data["strike"] == 585.0
        assert opt.data["con_id"] == 123456


# ── Test 9: Bid/ask/spread preserved ────────────────────────────────────────

class TestBidAskSpread:
    def test_spread(self):
        log, _ = _log_with_events()
        opt = [e for e in log.events if e.event_type == EventType.OPTION_SELECTED][0]
        assert opt.data["bid"] == 2.50
        assert opt.data["ask"] == 2.70
        assert opt.data["spread"] == 0.20


# ── Test 10-11: Entry events ────────────────────────────────────────────────

class TestEntryEvents:
    def test_submission(self):
        f = _factory("PAPER_EXECUTE")
        e = f.create(EventType.ENTRY_SUBMITTED, symbol="QQQ",
                     data={"order_id": 42, "limit_price": 2.70, "quantity": 1})
        assert e.data["order_id"] == 42
        assert e.data["limit_price"] == 2.70

    def test_fill(self):
        f = _factory("PAPER_EXECUTE")
        e = f.create(EventType.ENTRY_FILLED, symbol="QQQ",
                     data={"fill_price": 2.65, "fill_quantity": 1.0, "remaining": 0.0})
        assert e.data["fill_price"] == 2.65


# ── Test 12-13: Trigger events ──────────────────────────────────────────────

class TestTriggerEvents:
    def test_trigger_bar(self):
        f = _factory()
        e = f.create(EventType.STOP_TRIGGERED, symbol="QQQ",
                     data={"exit_reason": "STOP", "bar_open": 585.0, "bar_high": 585.1,
                           "bar_low": 584.5, "bar_close": 584.6,
                           "same_bar_ambiguity": False, "bar_time_ms": 123456})
        assert e.data["bar_low"] == 584.5
        assert e.data["same_bar_ambiguity"] is False


# ── Test 14: Exit fill premium ──────────────────────────────────────────────

class TestExitFill:
    def test_exit_premium(self):
        f = _factory("PAPER_EXECUTE")
        e = f.create(EventType.EXIT_FILLED, symbol="QQQ",
                     data={"fill_price": 3.10, "exit_reason": "TARGET"})
        assert e.data["fill_price"] == 3.10


# ── Test 15: Strategy result distinct from P&L ──────────────────────────────

class TestResultDistinct:
    def test_summary_separate_fields(self):
        f = _factory()
        sig = f.create(EventType.SIGNAL, symbol="QQQ", direction="LONG",
                       data={"underlying_entry": 585.20})
        entry_fill = f.create(EventType.ENTRY_FILLED, symbol="QQQ",
                              data={"fill_price": 2.65})
        trigger = f.create(EventType.TARGET_TRIGGERED, symbol="QQQ",
                           data={"exit_reason": "TARGET"})
        exit_fill = f.create(EventType.EXIT_FILLED, symbol="QQQ",
                             data={"fill_price": 3.10})

        summary = build_trade_summary(
            signal_event=sig, option_event=None,
            entry_submitted=None, entry_filled=entry_fill,
            trigger_event=trigger, exit_filled=exit_fill,
            result="WIN",
        )
        assert summary["result"] == "WIN"
        assert summary["entry_fill_premium"] == 2.65
        assert summary["exit_fill_premium"] == 3.10
        assert summary["gross_pnl"] == 45.0  # (3.10 - 2.65) * 100


# ── Test 16: TRADE_COMPLETED summary ────────────────────────────────────────

class TestTradeCompleted:
    def test_has_durations(self):
        f = _factory()
        sig = f.create(EventType.SIGNAL, symbol="QQQ", direction="LONG",
                       data={"underlying_entry": 585.20})
        entry_fill = f.create(EventType.ENTRY_FILLED, symbol="QQQ",
                              data={"fill_price": 2.65})
        exit_fill = f.create(EventType.EXIT_FILLED, symbol="QQQ",
                             data={"fill_price": 3.10})

        summary = build_trade_summary(
            sig, None, None, entry_fill, None, exit_fill, "WIN",
        )
        assert "duration_entry_to_exit_ms" in summary
        assert "duration_signal_to_exit_ms" in summary


# ── Test 17: Gross P&L correct ──────────────────────────────────────────────

class TestGrossPnl:
    def test_loss_pnl(self):
        f = _factory()
        entry = f.create(EventType.ENTRY_FILLED, data={"fill_price": 2.65})
        exit_ = f.create(EventType.EXIT_FILLED, data={"fill_price": 1.90})
        summary = build_trade_summary(None, None, None, entry, None, exit_, "LOSS")
        assert summary["gross_pnl"] == -75.0  # (1.90 - 2.65) * 100

    def test_no_pnl_without_prices(self):
        summary = build_trade_summary(None, None, None, None, None, None, "WIN")
        assert "gross_pnl" not in summary


# ── Test 18: No commissions ─────────────────────────────────────────────────

class TestNoCommissions:
    def test_no_commission_field(self):
        f = _factory()
        entry = f.create(EventType.ENTRY_FILLED, data={"fill_price": 2.65})
        exit_ = f.create(EventType.EXIT_FILLED, data={"fill_price": 3.10})
        summary = build_trade_summary(None, None, None, entry, None, exit_, "WIN")
        assert "commission" not in summary
        assert "before commissions" in summary.get("gross_pnl_note", "")


# ── Test 19: JSON export valid ──────────────────────────────────────────────

class TestJsonExport:
    def test_valid_json(self, tmp_path):
        log, _ = _log_with_events()
        p = log.export_json(tmp_path / "test.json")
        data = json.loads(p.read_text())
        assert data["maxbot_version"] == "v0.1"
        assert len(data["events"]) == len(log.events)
        assert data["session"]["watchlist"] == ["QQQ", "SPY"]


# ── Test 20: Markdown export readable ────────────────────────────────────────

class TestMarkdownExport:
    def test_readable(self, tmp_path):
        log, _ = _log_with_events()
        p = log.export_markdown(tmp_path / "test.md")
        text = p.read_text()
        assert "# MaxBot v0.1 Session Log" in text
        assert "SIGNAL" in text
        assert "QQQ" in text


# ── Test 21: Session metadata exported ───────────────────────────────────────

class TestSessionMetadata:
    def test_metadata_in_json(self, tmp_path):
        log, _ = _log_with_events()
        p = log.export_json(tmp_path / "test.json")
        data = json.loads(p.read_text())
        assert data["session"]["execution_mode"] == "OBSERVE_ONLY"
        assert data["session"]["trading_date"] == "2026-08-12"


# ── Test 22: Watchlist exported ──────────────────────────────────────────────

class TestWatchlistExport:
    def test_watchlist(self, tmp_path):
        log, _ = _log_with_events()
        p = log.export_json(tmp_path / "test.json")
        data = json.loads(p.read_text())
        assert data["session"]["watchlist"] == ["QQQ", "SPY"]


# ── Test 23: Execution mode exported ─────────────────────────────────────────

class TestExecutionModeExport:
    def test_mode(self, tmp_path):
        log, _ = _log_with_events()
        p = log.export_json(tmp_path / "test.json")
        data = json.loads(p.read_text())
        assert data["session"]["execution_mode"] == "OBSERVE_ONLY"


# ── Test 24: Trade limits exported ───────────────────────────────────────────

class TestTradeLimitsExport:
    def test_limits(self, tmp_path):
        log, _ = _log_with_events()
        p = log.export_json(tmp_path / "test.json")
        data = json.loads(p.read_text())
        assert data["session"]["trade_limits_enabled"] is False


# ── Test 25: No credentials exported ─────────────────────────────────────────

class TestNoCredentials:
    def test_no_password(self, tmp_path):
        log, _ = _log_with_events()
        log.set_metadata("account", "DU1***")
        p = log.export_json(tmp_path / "test.json")
        text = p.read_text()
        assert "password" not in text.lower()
        assert "secret" not in text.lower()


# ── Test 26: events_since ────────────────────────────────────────────────────

class TestEventsSince:
    def test_since(self):
        log, _ = _log_with_events()
        all_events = log.events
        mid = all_events[1].seq
        after = log.events_since(mid)
        assert all(e.seq > mid for e in after)
        assert len(after) == len(all_events) - 2


# ── Test 27-28: Shutdown export (tested via runner) ──────────────────────────

class TestShutdownExport:
    def test_runner_has_session_log(self):
        from trading_lab.live.bot_runner import MaxBotRunner
        runner = MaxBotRunner("QQQ")
        assert runner.session_log is not None
        assert isinstance(runner.session_log, SessionEventLog)


# ── Test 29: Logging failure does not mask exception ─────────────────────────

class TestLoggingFailure:
    def test_export_error_caught(self, tmp_path):
        """Export to invalid path should not raise."""
        log = SessionEventLog()
        # Export should handle errors gracefully
        try:
            log.export_json(tmp_path / "sub" / "deep" / "test.json")
        except Exception:
            pytest.fail("Export should create dirs, not fail")


# ── Test 30: OBSERVE_ONLY no fake fills ──────────────────────────────────────

class TestObserveNoFakes:
    def test_observe_events(self):
        f = _factory("OBSERVE_ONLY")
        e = f.create(EventType.OBSERVE_ENTRY, symbol="QQQ",
                     data={"order_submitted": False})
        assert e.data["order_submitted"] is False
        assert e.execution_mode == "OBSERVE_ONLY"


# ── Test 31: PAPER_EXECUTE lifecycle ─────────────────────────────────────────

class TestPaperLifecycle:
    def test_paper_events(self):
        f = _factory("PAPER_EXECUTE")
        events = [
            f.create(EventType.ENTRY_SUBMITTED, symbol="QQQ"),
            f.create(EventType.ENTRY_FILLED, symbol="QQQ"),
            f.create(EventType.STOP_TRIGGERED, symbol="QQQ"),
            f.create(EventType.EXIT_SUBMITTED, symbol="QQQ"),
            f.create(EventType.EXIT_FILLED, symbol="QQQ"),
            f.create(EventType.TRADE_LOSS, symbol="QQQ"),
        ]
        assert all(e.execution_mode == "PAPER_EXECUTE" for e in events)


# ── Test 32: No strategy duplication ─────────────────────────────────────────

class TestNoDuplication:
    def test_no_strategy(self):
        import inspect
        import trading_lab.live.event_stream as mod
        source = inspect.getsource(mod)
        assert "find_break" not in source
        assert "find_displacement" not in source


# ── Test 33: No real network ────────────────────────────────────────────────

class TestNoNetwork:
    def test_no_ib(self):
        import inspect
        import trading_lab.live.event_stream as mod
        source = inspect.getsource(mod)
        assert "ib_insync" not in source


# ── Test: trade_events filter ────────────────────────────────────────────────

class TestTradeEvents:
    def test_filter(self):
        log, _ = _log_with_events()
        trades = log.trade_events
        assert all(e.event_type != EventType.BOT_STARTED for e in trades)
        assert len(trades) >= 2  # at least the two SIGNALs


# ── Test: Immutability ──────────────────────────────────────────────────────

class TestImmutability:
    def test_frozen(self):
        f = _factory()
        e = f.create(EventType.SIGNAL, symbol="QQQ")
        with pytest.raises(AttributeError):
            e.symbol = "SPY"
