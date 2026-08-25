"""Contesto grafico congelato nel record all'ENTRY.

`setup_snapshot` sopravvive al restart, ma le candele RTH, ORB, PDH/PDL
e PMH/PML vivono solo in memoria: dopo un crash il record non basta a
ricostruire cio' che MaxBot vedeva quando e' entrato.

Nessuna chiamata IBKR, nessun rendering: solo persistenza.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
from datetime import datetime as dt_cls, timezone

import pytest

from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.context_levels import ContextLevels
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


def _entry_bars(pad=0):
    """ORB(0-4) -> break(5) -> disp(6,7,8) -> entry candle(9).

    `pad` barre neutre inserite PRIMA dell'ORB per testare il margine.
    """
    bars = []
    for i in range(pad):
        bars.append(_c(-pad + i, 100.4, 100.6, 100.2, 100.5))
    bars += [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
        _c(5, 100.80, 101.60, 100.70, 101.50),   # break
        _c(6, 101.55, 101.80, 101.20, 101.60),
        _c(7, 101.60, 101.90, 101.30, 101.70),
        _c(8, 101.70, 101.85, 101.10, 101.40),
        _c(9, 101.10, 101.30, 100.80, 101.20),   # Max Entry Candle
    ]
    return bars


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
            orderStatus=SimpleNamespace(status=status, filled=1.0 if status == "Filled" else 0.0,
                                        remaining=0.0 if status == "Filled" else 1.0,
                                        avgFillPrice=1.90 if status == "Filled" else 0.0),
            fills=[SimpleNamespace(time=dt_cls(2026, 8, 11, 14, 5, tzinfo=timezone.utc))]
                  if status == "Filled" else [], log=[]),
        exit_order_id=order_id, order_id=order_id, exit_reason="STOP",
        con_id=123456, quantity=1, entry_order_id=42,
        underlying_stop_price=100.80, underlying_target_price=102.00,
        status=status)


class _FilledExitExecutor:
    def __init__(self):
        self.submissions = []

    def submit_exit(self, **kw):
        self.submissions.append(kw)
        return _exit_submission(77, "Filled")

    def allow_resubmit(self, entry_order_id):
        pass


class _CancelledExitExecutor:
    def __init__(self):
        self.submissions = []
        self._n = 77

    def submit_exit(self, **kw):
        self.submissions.append(kw)
        sub = _exit_submission(self._n, "Cancelled")
        self._n += 1
        return sub

    def allow_resubmit(self, entry_order_id):
        pass


DEFAULT_LEVELS = {"orb_high": 101.00, "orb_low": 99.00,
                  "pdh": 103.50, "pdl": 98.20,
                  "pmh": 102.10, "pml": 99.40}


def _build(tmp_path, bars=None, levels=DEFAULT_LEVELS, exit_executor=None):
    """Percorso reale fino a POSITION_OPEN, con provider dei livelli."""
    runner = MaxBotRunner("QQQ", "LONG", execution_mode="PAPER_EXECUTE")
    ib = MagicMock()
    ib.managedAccounts.return_value = ["DU123"]
    runner._ib = ib
    runner._verify_paper()
    runner._setup_all_symbols()

    sb = LiveSessionBuilder("QQQ")
    state = {"levels": dict(levels) if levels is not None else None}
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
        session_builder=sb,
        signal_detector=runner._runtimes["QQQ"].signal_detector,
        trade_manager=DailyTradeManager(),
        option_selector=_FakeOptionSelector(),
        entry_executor=_FilledEntryExecutor(),
        exit_executor=exit_executor or _FilledExitExecutor(),
        emit=lambda et, **kw: SimpleNamespace(
            event_type=str(et), symbol="QQQ",
            direction=kw.get("direction") or "LONG",
            timestamp_ms=_ms(12), data=kw.get("data") or {}),
        chart_levels_provider=lambda: state["levels"],
        trade_state_dir=tmp_path)
    return runner, orch, sb, state


def _open_record(tmp_path, bars=None, levels=DEFAULT_LEVELS, **kw):
    runner, orch, sb, state = _build(tmp_path, levels=levels, **kw)
    for bar in (bars if bars is not None else _entry_bars(pad=8)):
        orch.on_bar(bar)
        if orch.has_pending_signal:
            orch.execute_pending_signal()
            orch.refresh_entry_status()
    assert orch.lifecycle == LifecycleState.POSITION_OPEN, orch.lifecycle
    rec = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
    return rec, orch, sb, state


# ═════════════════════════════════════════════════════════════════════════
# T1 / T2 — candele e entry candle
# ═════════════════════════════════════════════════════════════════════════

class TestT1Candles:
    def test_open_record_has_chart_context(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        assert "chart_context" in rec, (
            "il record non conserva le candele: dopo un restart il "
            "grafico dell'ingresso e' irricostruibile")
        cc = rec["chart_context"]
        assert isinstance(cc.get("candles"), list) and cc["candles"]

    def test_candles_are_chronological_and_complete(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        cs = rec["chart_context"]["candles"]
        ts = [c["time_ms"] for c in cs]
        assert ts == sorted(ts)
        assert len(set(ts)) == len(ts)
        for c in cs:
            for k in ("time_ms", "open", "high", "low", "close", "volume"):
                assert k in c

    def test_metadata_present(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        cc = rec["chart_context"]
        assert cc["timeframe_seconds"] == 60
        assert cc["market_timezone"] == "America/New_York"
        w = cc["window"]
        assert w["start_time_ms"] == cc["candles"][0]["time_ms"]
        assert w["end_time_ms"] == cc["candles"][-1]["time_ms"]


class TestT2EntryCandle:
    def test_entry_candle_is_last(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        cc = rec["chart_context"]
        assert cc["candles"][-1]["time_ms"] == rec["entry_timestamp_ms"]
        assert cc["window"]["entry_time_ms"] == rec["entry_timestamp_ms"]


# ═════════════════════════════════════════════════════════════════════════
# T3 / T4 — finestra
# ═════════════════════════════════════════════════════════════════════════

class TestT3Margin:
    def test_starts_five_bars_before_break(self, tmp_path):
        rec, *_ = _open_record(tmp_path, bars=_entry_bars(pad=8))
        cs = rec["chart_context"]["candles"]
        break_ms = int(rec["setup_key"].rsplit(":", 1)[1])
        idx = [c["time_ms"] for c in cs].index(break_ms)
        assert idx == 5, f"attese 5 barre prima del break, trovate {idx}"

    def test_uses_all_available_when_fewer_than_five(self, tmp_path):
        """Il break e' alla barra 5: prima ce ne sono solo 5 (l'ORB)."""
        rec, *_ = _open_record(tmp_path, bars=_entry_bars(pad=0))
        cs = rec["chart_context"]["candles"]
        break_ms = int(rec["setup_key"].rsplit(":", 1)[1])
        idx = [c["time_ms"] for c in cs].index(break_ms)
        assert idx == 5
        assert cs[0]["time_ms"] == _ms(0), "parte dalla prima barra disponibile"

    def test_no_bars_after_entry(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        cs = rec["chart_context"]["candles"]
        assert cs[-1]["time_ms"] == rec["entry_timestamp_ms"]


class TestT4Cap:
    """Il cap si esercita solo con una finestra break->entry molto
    lunga. Barre aggiunte PRIMA dell'ORB non bastano: la finestra parte
    5 barre prima del break, quindi non le raggiunge mai — un test
    costruito cosi' passerebbe a vuoto.
    """

    def _long_window(self, tmp_path, n_between):
        """Sessione sintetica: break, n barre, entry candle."""
        runner, orch, sb, _ = _build(tmp_path)
        bars = [_c(i, 100.0, 100.5, 99.5, 100.2) for i in range(-10, 0)]
        bars.append(_c(0, 100.8, 101.6, 100.7, 101.5))              # break
        bars += [_c(i, 101.5, 101.8, 101.2, 101.6)
                 for i in range(1, n_between + 1)]
        entry_i = n_between + 1
        bars.append(_c(entry_i, 101.1, 101.3, 100.8, 101.2))        # entry
        for b in bars:
            sb.add_bar(b)

        orch._active_entry_timestamp_ms = _ms(entry_i)
        orch._active_setup_snapshot = {
            "break_bar": {"bar_utc_ms": _ms(0)},
            "confirmation_bar": {"bar_utc_ms": _ms(entry_i)},
        }
        return orch._build_chart_context(), _ms(0), _ms(entry_i)

    def test_never_more_than_120_bars(self, tmp_path):
        cc, _, _ = self._long_window(tmp_path, n_between=300)
        assert len(cc["candles"]) == 120, (
            f"cap non applicato: {len(cc['candles'])} barre")

    def test_cap_trims_from_the_oldest_end(self, tmp_path):
        """Il break puo' cadere fuori se il setup e' enorme, ma l'entry
        candle e le barre piu' recenti restano sempre."""
        cc, break_ms, entry_ms = self._long_window(tmp_path, n_between=300)
        ts = [c["time_ms"] for c in cc["candles"]]
        assert ts[-1] == entry_ms, "l'entry candle e' sempre l'ultima"
        assert break_ms not in ts, "con 300 barre il break cade fuori dal cap"
        assert cc["window"]["entry_time_ms"] == entry_ms

    def test_structure_intact_when_under_the_cap(self, tmp_path):
        """Sotto il cap, break/displacement/retest/entry restano tutti
        dentro la finestra."""
        rec, *_ = _open_record(tmp_path, bars=_entry_bars(pad=8))
        ts = {c["time_ms"] for c in rec["chart_context"]["candles"]}
        snap = rec["setup_snapshot"]
        assert snap["break_bar"]["bar_utc_ms"] in ts
        assert snap["confirmation_bar"]["bar_utc_ms"] in ts
        for b in snap["displacement_window"]:
            assert b["bar_utc_ms"] in ts
        for b in snap["retest_window"]:
            assert b["bar_utc_ms"] in ts

    def test_long_but_under_cap_keeps_the_break(self, tmp_path):
        cc, break_ms, entry_ms = self._long_window(tmp_path, n_between=100)
        ts = [c["time_ms"] for c in cc["candles"]]
        assert len(ts) == 107, f"5 margine + break + 100 + entry, ottenute {len(ts)}"
        assert break_ms in ts and ts[-1] == entry_ms


# ═════════════════════════════════════════════════════════════════════════
# T5 / T6 / T7 / T8 — livelli
# ═════════════════════════════════════════════════════════════════════════

class TestLevels:
    def test_t5_both_orb_edges(self, tmp_path):
        """La trade e' LONG (ORB_HIGH) ma serve anche ORB_LOW per il chart."""
        rec, *_ = _open_record(tmp_path)
        lv = rec["chart_context"]["levels"]
        assert lv["orb_high"] == 101.00
        assert lv["orb_low"] == 99.00

    def test_t6_pdh_pdl_regardless_of_level_source(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        lv = rec["chart_context"]["levels"]
        assert rec["setup_snapshot"]["level_source"] == "ORB_HIGH"
        assert lv["pdh"] == 103.50 and lv["pdl"] == 98.20

    def test_t7_pmh_pml(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        lv = rec["chart_context"]["levels"]
        assert lv["pmh"] == 102.10 and lv["pml"] == 99.40

    def test_t8_missing_level_is_null(self, tmp_path):
        rec, *_ = _open_record(tmp_path, levels={"orb_high": 101.0})
        lv = rec["chart_context"]["levels"]
        assert lv["orb_high"] == 101.0
        for k in ("orb_low", "pdh", "pdl", "pmh", "pml"):
            assert lv[k] is None, f"{k} avrebbe dovuto essere null"

    def test_t8_no_provider_at_all(self, tmp_path):
        rec, *_ = _open_record(tmp_path, levels=None)
        lv = rec["chart_context"]["levels"]
        assert all(lv[k] is None for k in
                   ("orb_high", "orb_low", "pdh", "pdl", "pmh", "pml"))

    def test_levels_are_not_recomputed_from_candles(self, tmp_path):
        """Valori arbitrari, incoerenti con le candele: devono passare
        cosi' come sono, senza ricalcolo."""
        rec, *_ = _open_record(tmp_path, levels={"orb_high": 777.0,
                                                 "orb_low": 111.0})
        lv = rec["chart_context"]["levels"]
        assert lv["orb_high"] == 777.0 and lv["orb_low"] == 111.0


# ═════════════════════════════════════════════════════════════════════════
# T9 / T10 — JSON puro e copia difensiva
# ═════════════════════════════════════════════════════════════════════════

class TestT9PlainJson:
    def test_round_trip_without_custom_encoder(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        assert json.loads(json.dumps(rec)) == rec

    def test_only_json_types(self, tmp_path):
        rec, *_ = _open_record(tmp_path)

        def _check(v, path="chart_context"):
            assert isinstance(v, (dict, list, str, int, float, bool,
                                  type(None))), f"{path}: {type(v).__name__}"
            if isinstance(v, dict):
                for k, s in v.items():
                    _check(s, f"{path}.{k}")
            elif isinstance(v, list):
                for i, s in enumerate(v):
                    _check(s, f"{path}[{i}]")

        _check(rec["chart_context"])


class TestT10CopySafety:
    def test_later_mutation_does_not_change_the_record(self, tmp_path):
        rec, orch, sb, state = _open_record(tmp_path)
        before = json.dumps(rec["chart_context"], sort_keys=True)

        sb.add_bar(_c(10, 999.0, 999.0, 999.0, 999.0))   # nuove barre
        state["levels"]["orb_high"] = 12345.0            # livelli mutati

        again = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
        assert json.dumps(again["chart_context"], sort_keys=True) == before


# ═════════════════════════════════════════════════════════════════════════
# T11 / T12 — sopravvivenza agli stati finali
# ═════════════════════════════════════════════════════════════════════════

class TestFinalStates:
    def test_t11_closed_preserves_chart_context(self, tmp_path):
        rec, orch, *_ = _open_record(tmp_path)
        before = rec["chart_context"]
        orch.on_bar(_c(11, 101.15, 101.20, 100.60, 100.70))   # stop
        orch.refresh_exit_status()
        final = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
        assert final["state"] == "CLOSED"
        assert final["chart_context"] == before

    def test_t12_requires_attention_preserves_chart_context(self, tmp_path):
        ex = _CancelledExitExecutor()
        rec, orch, *_ = _open_record(tmp_path, exit_executor=ex)
        before = rec["chart_context"]
        orch._exit_max_retries = 2
        orch._exit_retry_cooldown_secs = 0.0
        orch.on_bar(_c(11, 101.15, 101.20, 100.60, 100.70))
        for _ in range(12):
            orch.refresh_exit_status()
            if orch.lifecycle == LifecycleState.REQUIRES_ATTENTION:
                break
        final = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
        assert final["state"] == "REQUIRES_ATTENTION"
        assert final["chart_context"] == before


# ═════════════════════════════════════════════════════════════════════════
# T13 — record legacy
# ═════════════════════════════════════════════════════════════════════════

class TestT13Legacy:
    def test_old_record_without_chart_context_is_readable(self, tmp_path):
        from trading_lab.live.trade_state_store import (
            load_trades, persist_open_trade,
        )
        legacy = {"trade_id": "QQQ_LONG_ORB_HIGH_1", "symbol": "QQQ",
                  "direction": "LONG", "setup_key": "LONG:ORB_HIGH:1",
                  "entry_timestamp_ms": _ms(9), "state": "OPEN"}
        persist_open_trade(legacy, base_dir=tmp_path)
        (t,) = load_trades(tmp_path)
        assert "chart_context" not in t
        assert t["symbol"] == "QQQ"

    def test_api_trades_serves_records_with_and_without(self, tmp_path):
        from trading_lab.live.control_api import MaxBotController, create_app
        from trading_lab.live.trade_state_store import persist_open_trade
        rec, *_ = _open_record(tmp_path)
        persist_open_trade({"trade_id": "OLD_1", "symbol": "MU",
                            "direction": "SHORT", "setup_key": "SHORT:1",
                            "entry_timestamp_ms": _ms(2), "state": "OPEN"},
                           base_dir=tmp_path)
        ctrl = MaxBotController(trade_state_dir=tmp_path)
        app = create_app(ctrl)
        app.config["TESTING"] = True
        d = app.test_client().get("/api/trades").get_json()
        assert d["count"] == 2
        assert sum("chart_context" in t for t in d["trades"]) == 1


# ═════════════════════════════════════════════════════════════════════════
# T14 / T15 — nessun cambiamento di strategia, nessuna chiamata IBKR
# ═════════════════════════════════════════════════════════════════════════

class TestT14NoStrategyChange:
    def test_signal_values_unchanged(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        assert rec["underlying_entry"] == 101.20
        assert rec["stop"] == 100.80
        assert rec["target"] == 102.00
        assert rec["entry_timestamp_ms"] == _ms(9)
        assert rec["setup_key"] == "LONG:ORB_HIGH:%d" % _ms(5)
        assert rec["entry_fill_price"] == 2.65

    def test_setup_snapshot_unchanged(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        snap = rec["setup_snapshot"]
        assert snap["level_source"] == "ORB_HIGH"
        assert snap["entry_pattern_type"] == "SINGLE_CANDLE_REJECTION"
        assert snap["displacement_bar_count"] == 3

    def test_chart_context_does_not_duplicate_setup_snapshot(self, tmp_path):
        rec, *_ = _open_record(tmp_path)
        cc = rec["chart_context"]
        for k in ("break_bar", "displacement_window", "retest_window",
                  "confirmation_bar", "entry_pattern_type",
                  "level_source", "level_price"):
            assert k not in cc, f"{k} duplicato in chart_context"


class TestT15NoIbkrFetch:
    def test_chart_capture_never_calls_ibkr(self, tmp_path):
        """L'asserzione e' sul metodo di cattura, non sull'intera classe:
        execute_pending_signal ha un docstring PREESISTENTE che elenca le
        sue chiamate IBKR, e scandire tutta la classe lo intercetterebbe
        producendo un falso positivo."""
        import inspect
        from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator
        src = inspect.getsource(MaxBotTradeOrchestrator._build_chart_context)
        for m in ("reqHistoricalData", "reqMktData", "reqRealTimeBars",
                  "qualifyContracts", "reqContractDetails", "self._ib", "IB("):
            assert m not in src, f"chiamata IBKR nella cattura: {m}"

    def test_orchestrator_holds_no_ib_connection(self, tmp_path):
        """Non possiede un client IBKR: le sync call passano dagli
        executor iniettati, non dal contesto grafico."""
        _, orch, _, _ = _build(tmp_path)
        assert not hasattr(orch, "_ib")
        assert getattr(orch, "_chart_levels_provider", None) is not None

    def test_capture_uses_only_the_session_builder(self, tmp_path):
        """Con session_builder vuoto non ci sono candele, e nessun
        tentativo di recuperarle altrove."""
        runner, orch, sb, _ = _build(tmp_path)
        cc = orch._build_chart_context()
        assert cc is None or cc["candles"] == []
