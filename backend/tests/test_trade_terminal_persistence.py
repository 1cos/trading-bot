"""Un'uscita che non si completa non deve lasciare il record su OPEN.

Quando i retry di exit si esauriscono l'orchestrator passa a
REQUIRES_ATTENTION (`trade_orchestrator.py:487`) e NON chiama
`_clear_active_trade()` — la posizione potrebbe essere ancora aperta sul
broker. Il file `trade_state/<trade_id>.json` restava pero' `"OPEN"`,
indistinguibile da una trade in normale gestione.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
from datetime import datetime as dt_cls, timezone

from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.trade_orchestrator import (
    LifecycleState,
    MaxBotTradeOrchestrator,
)


_ET = ZoneInfo("America/New_York")
_BASE = int(dt_cls(2026, 8, 11, 9, 30, 0, tzinfo=_ET).timestamp() * 1000)


def _ms(m):
    return _BASE + m * 60_000


def _c(m, o, h, l, cl):
    return {"time_ms": _ms(m), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


def _entry_bars():
    return [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
        _c(5, 100.80, 101.60, 100.70, 101.50),
        _c(6, 101.55, 101.80, 101.20, 101.60),
        _c(7, 101.60, 101.90, 101.30, 101.70),
        _c(8, 101.70, 101.85, 101.10, 101.40),
        _c(9, 101.10, 101.30, 100.80, 101.20),
    ]


def _stop_bar():
    return _c(11, 101.15, 101.20, 100.60, 100.70)


class _FakeOptionSelector:
    def select(self, **kw):
        return SimpleNamespace(
            underlying_symbol="QQQ", underlying_price=101.20,
            right=kw.get("right", "C"), expiration="20260811", strike=101.0,
            exchange="SMART", trading_class="QQQ", multiplier="100",
            quantity=1, con_id=123456,
            qualified_contract=SimpleNamespace(conId=123456, symbol="QQQ",
                                               localSymbol="QQQ 260811C00101000"),
            bid=2.50, ask=2.70, spread=0.20)


class _FilledEntryExecutor:
    def __init__(self):
        self._status = SimpleNamespace(status="Filled", filled=1.0,
                                       remaining=0.0, avgFillPrice=2.65)
        self._trade = SimpleNamespace(
            order=SimpleNamespace(orderId=42, permId=999),
            orderStatus=self._status,
            fills=[SimpleNamespace(time=dt_cls(2026, 8, 11, 13, 40,
                                               tzinfo=timezone.utc))],
            log=[])

    def submit_entry(self, order_spec):
        return SimpleNamespace(
            trade=self._trade, con_id=123456, underlying_symbol="QQQ",
            right="C", expiration="20260811", strike=101.0, quantity=1,
            limit_price=2.70, order_id=42, perm_id=999,
            status=self._status.status)


def _exit_submission(order_id, status):
    return SimpleNamespace(
        trade=SimpleNamespace(
            order=SimpleNamespace(orderId=order_id, permId=888),
            orderStatus=SimpleNamespace(status=status, filled=0.0,
                                        remaining=1.0, avgFillPrice=0.0),
            fills=[], log=[]),
        exit_order_id=order_id, order_id=order_id, exit_reason="STOP",
        con_id=123456, quantity=1, entry_order_id=42,
        underlying_stop_price=100.80, underlying_target_price=102.00,
        status=status)


class _AlwaysCancelledExitExecutor:
    """Ogni submit di exit torna Cancelled: porta a EXIT_FAILED e retry."""

    def __init__(self):
        self.submissions = []
        self.resubmits = []
        self._next_id = 77

    def submit_exit(self, **kw):
        self.submissions.append(kw)
        sub = _exit_submission(self._next_id, "Cancelled")
        self._next_id += 1
        return sub

    def allow_resubmit(self, entry_order_id):
        self.resubmits.append(entry_order_id)


class _FilledExitExecutor:
    def __init__(self):
        self.submissions = []
        self._trade = SimpleNamespace(
            order=SimpleNamespace(orderId=77, permId=888),
            orderStatus=SimpleNamespace(status="Filled", filled=1.0,
                                        remaining=0.0, avgFillPrice=1.90),
            fills=[SimpleNamespace(time=dt_cls(2026, 8, 11, 14, 5,
                                               tzinfo=timezone.utc))],
            log=[])

    def submit_exit(self, **kw):
        self.submissions.append(kw)
        return SimpleNamespace(
            trade=self._trade, exit_order_id=77, order_id=77,
            exit_reason="STOP", con_id=123456, quantity=1, entry_order_id=42,
            underlying_stop_price=100.80, underlying_target_price=102.00,
            status="Filled")

    def allow_resubmit(self, entry_order_id):
        pass


def _build(tmp_path, exit_executor, max_retries=2):
    runner = MaxBotRunner("QQQ", "LONG", execution_mode="PAPER_EXECUTE")
    ib = MagicMock()
    ib.managedAccounts.return_value = ["DU123"]
    runner._ib = ib
    runner._verify_paper()
    runner._setup_all_symbols()

    emitted = []
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
        session_builder=LiveSessionBuilder("QQQ"),
        signal_detector=runner._runtimes["QQQ"].signal_detector,
        trade_manager=DailyTradeManager(),
        option_selector=_FakeOptionSelector(),
        entry_executor=_FilledEntryExecutor(),
        exit_executor=exit_executor,
        emit=lambda et, **kw: emitted.append(str(et)) or SimpleNamespace(
            event_type=str(et), symbol="QQQ",
            direction=kw.get("direction") or "LONG",
            timestamp_ms=_ms(12), data=kw.get("data") or {}),
        trade_state_dir=tmp_path)
    orch._exit_max_retries = max_retries
    orch._exit_retry_cooldown_secs = 0.0
    return orch, emitted


def _run_to_exhaustion(tmp_path, exit_executor=None):
    """entry fill -> stop -> exit cancelled -> retry x N -> exhausted."""
    exec_ = exit_executor or _AlwaysCancelledExitExecutor()
    orch, emitted = _build(tmp_path, exec_)

    for bar in _entry_bars():
        orch.on_bar(bar)
        if orch.has_pending_signal:
            orch.execute_pending_signal()
            orch.refresh_entry_status()
    assert orch.lifecycle == LifecycleState.POSITION_OPEN
    open_record = json.loads(list(tmp_path.glob("*.json"))[0].read_text())

    orch.on_bar(_stop_bar())                     # -> EXIT_SUBMITTED
    for _ in range(12):                          # cicli del main loop
        orch.refresh_exit_status()
        if orch.lifecycle == LifecycleState.REQUIRES_ATTENTION:
            break

    files = list(tmp_path.glob("*.json"))
    final = json.loads(files[0].read_text())
    return orch, emitted, open_record, final, files, exec_


# ═════════════════════════════════════════════════════════════════════════
# T1 / T2 — terminal state persistito
# ═════════════════════════════════════════════════════════════════════════

class TestT1T2TerminalState:
    def test_reaches_requires_attention(self, tmp_path):
        orch, emitted, _, _, _, _ = _run_to_exhaustion(tmp_path)
        assert orch.lifecycle == LifecycleState.REQUIRES_ATTENTION
        assert "EXIT_RETRIES_EXHAUSTED" in emitted

    def test_state_is_requires_attention_not_open(self, tmp_path):
        _, _, open_rec, final, _, _ = _run_to_exhaustion(tmp_path)
        assert open_rec["state"] == "OPEN"
        assert final["state"] == "REQUIRES_ATTENTION", (
            "il record resta OPEN: una trade non chiusa e' indistinguibile "
            "da una in gestione normale")

    def test_same_file_same_trade_id(self, tmp_path):
        _, _, open_rec, final, files, _ = _run_to_exhaustion(tmp_path)
        assert len(files) == 1
        assert final["trade_id"] == open_rec["trade_id"]
        assert not list(tmp_path.glob(".*.tmp"))


# ═════════════════════════════════════════════════════════════════════════
# T3 — setup_snapshot preservato
# ═════════════════════════════════════════════════════════════════════════

class TestT3SnapshotPreserved:
    def test_snapshot_identical(self, tmp_path):
        _, _, open_rec, final, _, _ = _run_to_exhaustion(tmp_path)
        assert final["setup_snapshot"] == open_rec["setup_snapshot"]
        assert (final["setup_snapshot"]["entry_pattern_type"]
                == "SINGLE_CANDLE_REJECTION")

    def test_entry_fields_preserved(self, tmp_path):
        _, _, open_rec, final, _, _ = _run_to_exhaustion(tmp_path)
        for k, v in open_rec.items():
            if k == "state":
                continue
            assert final[k] == v, f"campo OPEN alterato: {k}"


# ═════════════════════════════════════════════════════════════════════════
# T4 — nessun falso CLOSED, nessun P&L inventato
# ═════════════════════════════════════════════════════════════════════════

class TestT4NoFalseClosed:
    def test_not_closed(self, tmp_path):
        _, _, _, final, _, _ = _run_to_exhaustion(tmp_path)
        assert final["state"] != "CLOSED"

    def test_no_invented_exit_or_pnl(self, tmp_path):
        _, _, _, final, _, _ = _run_to_exhaustion(tmp_path)
        assert "outcome" not in final, (
            "il blocco outcome implica un exit fill confermato")
        blob = json.dumps(final)
        for forbidden in ("exit_fill_premium", "gross_pnl",
                          "premium_return_pct", "\"result\""):
            assert forbidden not in blob, f"valore inventato: {forbidden}"


# ═════════════════════════════════════════════════════════════════════════
# T5 — failure metadata
# ═════════════════════════════════════════════════════════════════════════

class TestT5FailureMetadata:
    def test_terminal_block_fields(self, tmp_path):
        _, _, _, final, _, _ = _run_to_exhaustion(tmp_path)
        term = final.get("terminal")
        assert term is not None, "nessun blocco terminal"

        assert term["runtime_state"] == "REQUIRES_ATTENTION"
        assert term["reason"] == "EXIT_RETRIES_EXHAUSTED"
        assert term["exit_reason"] == "STOP"
        assert term["retry_count"] == 2
        assert term["max_retries"] == 2
        assert term["exit_order_id"] is not None
        assert term["terminal_timestamp_ms"] is not None
        assert term["last_error"], "ultimo errore broker non conservato"

    def test_last_error_reflects_the_broker_status(self, tmp_path):
        _, _, _, final, _, _ = _run_to_exhaustion(tmp_path)
        assert "Cancelled" in final["terminal"]["last_error"]


# ═════════════════════════════════════════════════════════════════════════
# T6 — failure isolation
# ═════════════════════════════════════════════════════════════════════════

class TestT6PersistenceFailureIsolation:
    def test_persist_failure_keeps_requires_attention(
            self, tmp_path, monkeypatch, caplog):
        import trading_lab.live.trade_orchestrator as mod

        def _boom(*a, **k):
            raise OSError("disco pieno")

        monkeypatch.setattr(mod, "persist_terminal_trade", _boom)

        with caplog.at_level("ERROR"):
            orch, emitted, _, _, _, exec_ = _run_to_exhaustion(tmp_path)

        assert orch.lifecycle == LifecycleState.REQUIRES_ATTENTION
        assert "EXIT_RETRIES_EXHAUSTED" in emitted
        # retry non azzerati
        assert orch._exit_retry_count == 2
        # nessun ordine oltre a submit iniziale + i retry consentiti
        assert len(exec_.submissions) == 3
        assert any("disco pieno" in r.message or "terminal" in r.message.lower()
                   for r in caplog.records)

    def test_no_further_orders_after_exhaustion(self, tmp_path):
        orch, _, _, _, _, exec_ = _run_to_exhaustion(tmp_path)
        before = len(exec_.submissions)
        for _ in range(5):
            orch.refresh_exit_status()
        assert len(exec_.submissions) == before
        assert orch.lifecycle == LifecycleState.REQUIRES_ATTENTION

    def test_terminal_write_happens_once(self, tmp_path):
        orch, emitted, _, _, _, _ = _run_to_exhaustion(tmp_path)
        for _ in range(5):
            orch.refresh_exit_status()
        assert emitted.count("EXIT_RETRIES_EXHAUSTED") == 1


# ═════════════════════════════════════════════════════════════════════════
# T7 — CLOSED normale invariato
# ═════════════════════════════════════════════════════════════════════════

class TestT7ClosedPathUnchanged:
    def test_normal_exit_still_produces_closed(self, tmp_path):
        exec_ = _FilledExitExecutor()
        orch, emitted = _build(tmp_path, exec_)

        for bar in _entry_bars():
            orch.on_bar(bar)
            if orch.has_pending_signal:
                orch.execute_pending_signal()
                orch.refresh_entry_status()
        open_rec = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
        orch.on_bar(_stop_bar())
        orch.refresh_exit_status()

        final = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
        assert final["state"] == "CLOSED"
        assert "outcome" in final
        assert "terminal" not in final
        assert final["outcome"]["result"] == "LOSS"
        assert final["setup_snapshot"] == open_rec["setup_snapshot"]
