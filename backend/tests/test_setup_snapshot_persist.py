"""Il record OPEN persistito deve conservare il PERCHE' della trade.

`_persist_open_trade_state()` scrive entry/stop/target/setup_key, cioe'
l'identita' e i prezzi, ma non la struttura che ha giustificato
l'ingresso. Quei dati esistono in `result.detection_result` dentro
`execute_pending_signal()` e vengono scartati con la variabile locale.
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


def _long_signal_bars():
    """ORB break -> displacement -> retest -> Max Entry Candle (LONG)."""
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
    """Submit -> subito FILLED, cosi' refresh_entry_status persiste."""

    def __init__(self):
        self.submissions = []
        self._status = SimpleNamespace(status="Filled", filled=1.0,
                                       remaining=0.0, avgFillPrice=2.65)
        self._trade = SimpleNamespace(
            order=SimpleNamespace(orderId=42, permId=999),
            orderStatus=self._status,
            fills=[SimpleNamespace(time=dt_cls(2026, 8, 11, 13, 40,
                                               tzinfo=timezone.utc))],
            log=[])

    def submit_entry(self, order_spec):
        self.submissions.append(order_spec)
        return SimpleNamespace(
            trade=self._trade, con_id=123456, underlying_symbol="QQQ",
            right="C", expiration="20260811", strike=101.0, quantity=1,
            limit_price=2.70, order_id=42, perm_id=999,
            status=self._status.status)


class _NoExitExecutor:
    def submit_exit(self, *a, **k):
        raise AssertionError("exit non atteso in questo test")


def _mock_ib():
    ib = MagicMock()
    ib.managedAccounts.return_value = ["DU123"]
    return ib


def _open_trade_record(tmp_path):
    """Percorso reale fino a POSITION_OPEN; ritorna il record persistito."""
    runner = MaxBotRunner("QQQ", "LONG", execution_mode="PAPER_EXECUTE")
    runner._ib = _mock_ib()
    runner._verify_paper()
    runner._setup_all_symbols()
    detector = runner._runtimes["QQQ"].signal_detector

    sb = LiveSessionBuilder("QQQ")
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
        session_builder=sb, signal_detector=detector,
        trade_manager=DailyTradeManager(),
        option_selector=_FakeOptionSelector(),
        entry_executor=_FilledEntryExecutor(),
        exit_executor=_NoExitExecutor(),
        trade_state_dir=tmp_path)

    for bar in _long_signal_bars():
        orch.on_bar(bar)
        if orch.has_pending_signal:
            orch.execute_pending_signal()
            orch.refresh_entry_status()      # -> ENTRY_FILLED -> persist

    files = list(tmp_path.glob("*.json"))
    assert files, f"nessun trade_state persistito (lifecycle={orch.lifecycle})"
    return json.loads(files[0].read_text()), orch


# ═════════════════════════════════════════════════════════════════════════
# Pre-fix: oggi il record OPEN non contiene il perche' della trade
# ═════════════════════════════════════════════════════════════════════════

class TestSetupSnapshotPresent:
    def test_open_record_carries_setup_snapshot(self, tmp_path):
        """T1 — una trade OPEN valida persiste setup_snapshot."""
        record, _ = _open_trade_record(tmp_path)
        assert "setup_snapshot" in record, (
            "il record OPEN non conserva la struttura del setup: "
            "break/displacement/retest/confirmation sono persi")

    def test_structural_stages_are_all_present(self, tmp_path):
        """T2 — i campi principali del setup."""
        record, _ = _open_trade_record(tmp_path)
        snap = record.get("setup_snapshot") or {}

        assert snap.get("level_source") == "ORB_HIGH"
        assert snap.get("level_price") is not None
        assert snap.get("level_bar") is not None
        assert snap.get("break_bar") is not None
        assert snap.get("displacement_bar_count") == 3
        assert len(snap.get("displacement_window") or []) == 3
        assert snap.get("retest_window") is not None
        assert snap.get("failed_retests") is not None
        assert snap.get("confirmation_bar") is not None
        # metriche della confirmation candle
        for k in ("confirmation_rej_wick", "confirmation_body",
                  "confirmation_opp_wick",
                  "confirmation_favorable_close_location"):
            assert k in snap, f"metrica mancante: {k}"
        # il pattern deve esistere come chiave, anche se non ricavabile
        assert "entry_pattern_type" in snap


class TestJsonRoundTrip:
    def test_record_is_plain_json(self, tmp_path):
        """T3 — leggibile con json.load() senza decoder custom."""
        record, _ = _open_trade_record(tmp_path)
        # gia' passato da json.loads in _open_trade_record; ri-serializzare
        # senza default= prova che non restano oggetti non-JSON
        json.dumps(record)

    def test_no_live_detector_objects_leak_into_the_record(self, tmp_path):
        record, _ = _open_trade_record(tmp_path)
        snap = record.get("setup_snapshot") or {}

        def _check(v, path="setup_snapshot"):
            assert isinstance(v, (dict, list, str, int, float, bool, type(None))), (
                f"{path}: tipo non-JSON {type(v).__name__}")
            if isinstance(v, dict):
                for k, sub in v.items():
                    _check(sub, f"{path}.{k}")
            elif isinstance(v, list):
                for i, sub in enumerate(v):
                    _check(sub, f"{path}[{i}]")

        _check(snap)


class TestSnapshotImmutability:
    def test_mutating_detection_result_does_not_change_snapshot(self, tmp_path):
        """T4 — lo snapshot e' una copia, non un riferimento vivo."""
        from trading_lab.live.trade_state_store import build_setup_snapshot
        from trading_lab.live.signal_detector import LiveSignalDetector

        sb = LiveSessionBuilder("QQQ")
        for b in _long_signal_bars():
            sb.add_bar(b)
        det = LiveSignalDetector(symbol="QQQ", direction="LONG", tick_size=0.01)
        result = det.evaluate(sb.current_session())
        dr = result.detection_result

        snap = build_setup_snapshot(dr)
        before = json.dumps(snap, sort_keys=True)

        # muta le strutture interne del DetectionResult
        object.__setattr__(dr, "displacement_bar_count", 999)
        object.__setattr__(dr, "break_bar", None)

        assert json.dumps(snap, sort_keys=True) == before


class TestBackwardCompatibility:
    def test_old_record_without_snapshot_stays_valid(self, tmp_path):
        """T5 — un record vecchio resta scrivibile/leggibile."""
        from trading_lab.live.trade_state_store import persist_open_trade

        legacy = {
            "trade_id": "QQQ_LONG_ORB_HIGH_1786455300000",
            "symbol": "QQQ", "setup_key": "LONG:ORB_HIGH:1786455300000",
            "signal_key": "LONG:ORB_HIGH:1786455300000:1786455540000",
            "direction": "LONG", "entry_timestamp_ms": 1786455540000,
            "underlying_entry": 101.2, "stop": 100.8, "target": 102.0,
            "rr": 2, "option": {"con_id": 123456}, "quantity": 1,
            "entry_order_id": 42, "entry_fill_price": 2.65, "state": "OPEN",
        }
        path = persist_open_trade(legacy, base_dir=tmp_path)
        back = json.loads(path.read_text())

        assert "setup_snapshot" not in back
        assert back["setup_key"] == legacy["setup_key"]
        assert back["state"] == "OPEN"

    def test_snapshot_is_additive_not_replacing(self, tmp_path):
        """I campi preesistenti restano tutti al loro posto."""
        record, _ = _open_trade_record(tmp_path)
        for k in ("trade_id", "symbol", "setup_key", "signal_key", "direction",
                  "entry_timestamp_ms", "underlying_entry", "stop", "target",
                  "rr", "option", "quantity", "entry_order_id",
                  "entry_fill_price", "state"):
            assert k in record, f"campo preesistente perso: {k}"
        assert record["state"] == "OPEN"
