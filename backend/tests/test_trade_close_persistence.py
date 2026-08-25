"""Il record trade_state deve chiudersi, non restare OPEN per sempre.

`_persist_open_trade_state()` scrive il record al fill di entrata e non
lo tocca piu'. Alla chiusura viene emesso `TRADE_COMPLETED` con un
summary completo, ma il file su disco continua a dire `"state": "OPEN"`
(verificabile su disco: tutti i record storici sono OPEN, inclusi trade
chiusi in perdita). L'esito vive solo nel session log, che viene
esportato unicamente a shutdown pulito.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
from datetime import datetime as dt_cls, timezone

import pytest

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
    """LONG: break -> 3 disp -> retest -> Max Entry Candle a bar9.
    entry 101.20, stop 100.80, target 102.00."""
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
    """Barra che colpisce lo stop (low <= 100.80)."""
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


class _FilledExitExecutor:
    """Exit submit -> subito FILLED a 1.90 (perdita)."""

    def __init__(self):
        self.submissions = []
        self._status = SimpleNamespace(status="Filled", filled=1.0,
                                       remaining=0.0, avgFillPrice=1.90)
        self._trade = SimpleNamespace(
            order=SimpleNamespace(orderId=77, permId=888),
            orderStatus=self._status,
            fills=[SimpleNamespace(time=dt_cls(2026, 8, 11, 14, 5,
                                               tzinfo=timezone.utc))],
            log=[])

    def submit_exit(self, **kw):
        self.submissions.append(kw)
        return SimpleNamespace(
            trade=self._trade, exit_order_id=77, order_id=77,
            exit_reason="STOP", con_id=123456, quantity=1,
            entry_order_id=42, underlying_stop_price=100.80,
            underlying_target_price=102.00,
            status=self._status.status)


def _run_full_trade(tmp_path, exit_executor=None):
    """Percorso reale: entry fill -> stop -> exit fill -> TRADE_COMPLETED."""
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
        exit_executor=exit_executor or _FilledExitExecutor(),
        emit=lambda et, **kw: emitted.append(str(et)) or SimpleNamespace(
            event_type=str(et), symbol="QQQ",
            direction=kw.get("direction") or "LONG",
            timestamp_ms=_ms(12), data=kw.get("data") or {}),
        trade_state_dir=tmp_path)

    for bar in _entry_bars():
        orch.on_bar(bar)
        if orch.has_pending_signal:
            orch.execute_pending_signal()
            orch.refresh_entry_status()

    assert orch.lifecycle == LifecycleState.POSITION_OPEN, orch.lifecycle
    open_record = json.loads(list(tmp_path.glob("*.json"))[0].read_text())

    orch.on_bar(_stop_bar())                 # -> STOP_TRIGGERED, EXIT_SUBMITTED
    orch.refresh_exit_status()               # -> EXIT_FILLED, TRADE_COMPLETED

    files = list(tmp_path.glob("*.json"))
    closed_record = json.loads(files[0].read_text())
    return orch, emitted, open_record, closed_record, files


# ═════════════════════════════════════════════════════════════════════════
# T1 — OPEN -> CLOSED, stesso file
# ═════════════════════════════════════════════════════════════════════════

class TestT1OpenToClosed:
    def test_state_becomes_closed(self, tmp_path):
        orch, emitted, open_rec, closed_rec, files = _run_full_trade(tmp_path)
        assert "TRADE_COMPLETED" in emitted, "la trade non si e' conclusa"
        assert open_rec["state"] == "OPEN"
        assert closed_rec["state"] == "CLOSED", (
            "il record resta OPEN dopo TRADE_COMPLETED: l'esito non e' persistito")

    def test_same_trade_id_same_path_single_file(self, tmp_path):
        _, _, open_rec, closed_rec, files = _run_full_trade(tmp_path)
        assert len(files) == 1, f"attesi 1 file, trovati {len(files)}"
        assert closed_rec["trade_id"] == open_rec["trade_id"]
        assert files[0].name == f"{open_rec['trade_id']}.json"

    def test_no_leftover_temp_files(self, tmp_path):
        _run_full_trade(tmp_path)
        assert not list(tmp_path.glob(".*.tmp"))


# ═════════════════════════════════════════════════════════════════════════
# T2 — setup_snapshot preservato integralmente
# ═════════════════════════════════════════════════════════════════════════

class TestT2SetupSnapshotPreserved:
    def test_snapshot_identical_after_close(self, tmp_path):
        _, _, open_rec, closed_rec, _ = _run_full_trade(tmp_path)
        assert closed_rec["setup_snapshot"] == open_rec["setup_snapshot"]

    def test_entry_pattern_survives_the_close(self, tmp_path):
        _, _, _, closed_rec, _ = _run_full_trade(tmp_path)
        assert (closed_rec["setup_snapshot"]["entry_pattern_type"]
                == "SINGLE_CANDLE_REJECTION")

    def test_open_fields_all_preserved(self, tmp_path):
        _, _, open_rec, closed_rec, _ = _run_full_trade(tmp_path)
        for k, v in open_rec.items():
            if k == "state":
                continue
            assert closed_rec[k] == v, f"campo OPEN alterato: {k}"


# ═════════════════════════════════════════════════════════════════════════
# T3 — exit metadata
# ═════════════════════════════════════════════════════════════════════════

class TestT3ExitMetadata:
    def test_outcome_fields_present(self, tmp_path):
        _, _, _, closed_rec, _ = _run_full_trade(tmp_path)
        out = closed_rec.get("outcome")
        assert out is not None, "nessun blocco outcome"

        assert out["result"] == "LOSS"
        assert out["exit_reason"] == "STOP"
        assert out["exit_fill_premium"] == 1.90
        assert out["exit_fill_time_ms"] is not None
        assert out["gross_pnl"] == round((1.90 - 2.65) * 100, 2)
        assert "premium_return_pct" in out
        assert "duration_entry_to_exit_ms" in out
        assert out["exit_order_id"] == 77

    def test_no_invented_values(self, tmp_path):
        """Solo campi realmente prodotti dal summary/orchestrator."""
        _, _, _, closed_rec, _ = _run_full_trade(tmp_path)
        out = closed_rec["outcome"]
        allowed = {
            "result", "exit_reason", "trigger_time_ms", "exit_fill_time_ms",
            "exit_fill_premium", "gross_pnl", "gross_pnl_note",
            "premium_return_pct", "duration_entry_to_exit_ms",
            "duration_signal_to_exit_ms", "exit_order_id",
        }
        assert set(out) <= allowed, f"campi inattesi: {set(out) - allowed}"


# ═════════════════════════════════════════════════════════════════════════
# T5 — failure isolation
# ═════════════════════════════════════════════════════════════════════════

class TestT5PersistenceFailureIsolation:
    def test_close_persist_failure_does_not_break_the_trade(
            self, tmp_path, monkeypatch, caplog):
        import trading_lab.live.trade_orchestrator as mod

        def _boom(*a, **k):
            raise OSError("disco pieno")

        monkeypatch.setattr(mod, "persist_closed_trade", _boom)

        exec_ = _FilledExitExecutor()
        with caplog.at_level("ERROR"):
            orch, emitted, _, _, _ = _run_full_trade(tmp_path,
                                                     exit_executor=exec_)

        # la trade e' comunque conclusa
        assert "TRADE_COMPLETED" in emitted
        assert orch.lifecycle in (LifecycleState.WAITING_FOR_SIGNAL,
                                  LifecycleState.DONE_FOR_DAY)
        assert orch.lifecycle != LifecycleState.REQUIRES_ATTENTION
        # nessun ordine aggiuntivo
        assert len(exec_.submissions) == 1
        # stato attivo ripulito
        assert orch._active_setup_key is None
        assert orch._active_setup_snapshot is None
        # errore loggato
        assert any("disco pieno" in r.message or "CLOSED" in r.message
                   for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════
# T6 — la persistenza OPEN non cambia
# ═════════════════════════════════════════════════════════════════════════

class TestT6OpenPersistenceUnchanged:
    def test_open_record_shape_unchanged(self, tmp_path):
        _, _, open_rec, _, _ = _run_full_trade(tmp_path)
        for k in ("trade_id", "symbol", "setup_key", "signal_key", "direction",
                  "entry_timestamp_ms", "underlying_entry", "stop", "target",
                  "rr", "option", "quantity", "entry_order_id",
                  "entry_fill_price", "state", "setup_snapshot"):
            assert k in open_rec, f"campo OPEN mancante: {k}"
        assert open_rec["state"] == "OPEN"
        assert "outcome" not in open_rec

    def test_persist_open_trade_still_works_standalone(self, tmp_path):
        from trading_lab.live.trade_state_store import persist_open_trade
        rec = {"trade_id": "X_1", "symbol": "X", "state": "OPEN"}
        back = json.loads(persist_open_trade(rec, base_dir=tmp_path).read_text())
        assert back == rec


# ═════════════════════════════════════════════════════════════════════════
# T7 — nessun cambiamento di strategia
# ═════════════════════════════════════════════════════════════════════════

class TestT7NoStrategyChange:
    def test_entry_stop_target_and_keys_unchanged(self, tmp_path):
        _, _, open_rec, closed_rec, _ = _run_full_trade(tmp_path)
        assert open_rec["underlying_entry"] == 101.20
        assert open_rec["stop"] == 100.80
        assert open_rec["target"] == 102.00
        assert open_rec["setup_key"] == "LONG:ORB_HIGH:%d" % _ms(5)
        assert open_rec["signal_key"] == "LONG:ORB_HIGH:%d:%d" % (_ms(5), _ms(9))
        # invariati anche dopo la chiusura
        for k in ("underlying_entry", "stop", "target", "setup_key", "signal_key"):
            assert closed_rec[k] == open_rec[k]

    def test_exit_trigger_is_the_stop(self, tmp_path):
        _, _, _, closed_rec, _ = _run_full_trade(tmp_path)
        assert closed_rec["outcome"]["exit_reason"] == "STOP"
