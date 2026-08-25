"""Il pattern che ha prodotto la Max Entry Candle deve sopravvivere.

`rejection_finder.py:517` produce `entry_pattern_type`
(SINGLE_CANDLE_REJECTION / TWO_CANDLE_ENGULFING_RECOVERY). Sul ramo
NO_SETUP quel dict arriva a SignalResult.rejection_detail
(`signal_detector.py:576`); sul ramo SIGNAL non veniva propagato, e
DetectionResult/v1 non ha un campo per il pattern — quindi una trade
eseguita non conservava con quale pattern era entrata.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
from datetime import datetime as dt_cls, timezone

from trading_lab.contracts.enums import EntryPatternType
from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator
from trading_lab.live.trade_state_store import build_setup_snapshot


_ET = ZoneInfo("America/New_York")
_BASE = int(dt_cls(2026, 8, 11, 9, 30, 0, tzinfo=_ET).timestamp() * 1000)


def _ms(m):
    return _BASE + m * 60_000


def _c(m, o, h, l, cl):
    return {"time_ms": _ms(m), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


def _orb():
    """ORB idx0-4: high 101.00, low 99.00."""
    return [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]


def _single_candle_bars():
    """LONG classico: break -> 3 disp -> retest -> Max Entry Candle."""
    return _orb() + [
        _c(5, 100.80, 101.60, 100.70, 101.50),
        _c(6, 101.55, 101.80, 101.20, 101.60),
        _c(7, 101.60, 101.90, 101.30, 101.70),
        _c(8, 101.70, 101.85, 101.10, 101.40),
        _c(9, 101.10, 101.30, 100.80, 101.20),
    ]


def _two_candle_bars():
    """LONG TWO_CANDLE_ENGULFING_RECOVERY.

    bar9  penetra il livello (low <= 101.00) e chiude DENTRO la zona
          (close <= 101.00), quindi SINGLE non qualifica.
    bar10 engulfing rialzista del corpo di bar9 (open < body_low,
          close > body_high) e recovery sopra il livello.
    """
    return _orb() + [
        _c(5, 100.80, 101.60, 100.70, 101.50),
        _c(6, 101.55, 101.90, 101.20, 101.70),
        _c(7, 101.70, 102.00, 101.30, 101.85),
        _c(8, 101.85, 102.10, 101.40, 101.95),
        _c(9, 101.40, 101.50, 100.30, 100.60),    # prima candela
        _c(10, 100.20, 101.80, 100.10, 101.60),   # engulfing + recovery
    ]


def _signal_for(bars):
    sb = LiveSessionBuilder("QQQ")
    for b in bars:
        sb.add_bar(b)
    det = LiveSignalDetector(symbol="QQQ", direction="LONG", tick_size=0.01)
    result = det.evaluate(sb.current_session())
    assert result.status == SignalStatus.SIGNAL, "fixture non produce un SIGNAL"
    return result


def _pattern_of(result):
    """Il pattern come lo vedrebbe chi persiste la trade."""
    snap = build_setup_snapshot(result.detection_result,
                                rejection_detail=result.rejection_detail)
    return snap.get("entry_pattern_type")


# ═════════════════════════════════════════════════════════════════════════
# T1 / T2 — il pattern reale sopravvive fino al consumatore
# ═════════════════════════════════════════════════════════════════════════

class TestT1SingleCandle:
    def test_single_candle_pattern_is_preserved(self):
        result = _signal_for(_single_candle_bars())
        assert _pattern_of(result) == EntryPatternType.SINGLE_CANDLE_REJECTION

    def test_signal_result_carries_the_rejection_detail(self):
        result = _signal_for(_single_candle_bars())
        assert result.rejection_detail is not None, (
            "sul ramo SIGNAL il dettaglio della rejection viene scartato")
        assert (result.rejection_detail["entry_pattern_type"]
                == "SINGLE_CANDLE_REJECTION")


class TestT2TwoCandle:
    def test_two_candle_pattern_is_preserved(self):
        result = _signal_for(_two_candle_bars())
        assert _pattern_of(result) == EntryPatternType.TWO_CANDLE_ENGULFING_RECOVERY

    def test_fixture_really_is_two_candle(self):
        """Guardia sulla fixture: se un giorno degenerasse in SINGLE,
        il test T2 diventerebbe vacuo senza accorgersene."""
        result = _signal_for(_two_candle_bars())
        assert result.rejection_detail is not None
        assert "pair_stop_basis_ticks" in result.rejection_detail


# ═════════════════════════════════════════════════════════════════════════
# T3 — end-to-end fino al JSON della trade OPEN
# ═════════════════════════════════════════════════════════════════════════

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
        raise AssertionError("exit non atteso")


def _persisted_record(bars, tmp_path):
    runner = MaxBotRunner("QQQ", "LONG", execution_mode="PAPER_EXECUTE")
    ib = MagicMock()
    ib.managedAccounts.return_value = ["DU123"]
    runner._ib = ib
    runner._verify_paper()
    runner._setup_all_symbols()

    orch = MaxBotTradeOrchestrator(
        underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
        session_builder=LiveSessionBuilder("QQQ"),
        signal_detector=runner._runtimes["QQQ"].signal_detector,
        trade_manager=DailyTradeManager(),
        option_selector=_FakeOptionSelector(),
        entry_executor=_FilledEntryExecutor(),
        exit_executor=_NoExitExecutor(),
        trade_state_dir=tmp_path)

    for bar in bars:
        orch.on_bar(bar)
        if orch.has_pending_signal:
            orch.execute_pending_signal()
            orch.refresh_entry_status()

    files = list(tmp_path.glob("*.json"))
    assert files, f"nessun record persistito (lifecycle={orch.lifecycle})"
    return json.loads(files[0].read_text())


class TestT3EndToEndSnapshot:
    def test_single_candle_pattern_reaches_the_open_trade_json(self, tmp_path):
        record = _persisted_record(_single_candle_bars(), tmp_path)
        snap = record["setup_snapshot"]
        assert snap["entry_pattern_type"] == "SINGLE_CANDLE_REJECTION"

    def test_two_candle_pattern_reaches_the_open_trade_json(self, tmp_path):
        record = _persisted_record(_two_candle_bars(), tmp_path)
        snap = record["setup_snapshot"]
        assert snap["entry_pattern_type"] == "TWO_CANDLE_ENGULFING_RECOVERY"

    def test_persisted_pattern_is_plain_json_string(self, tmp_path):
        record = _persisted_record(_single_candle_bars(), tmp_path)
        json.dumps(record)      # nessun decoder custom richiesto
        assert isinstance(record["setup_snapshot"]["entry_pattern_type"], str)


# ═════════════════════════════════════════════════════════════════════════
# T4 — la detection non cambia: solo metadata
# ═════════════════════════════════════════════════════════════════════════

class TestT4DetectionUnchanged:
    """Valori fissati esplicitamente: se la propagazione toccasse la
    detection, questi cambierebbero."""

    def test_single_candle_detection_identical(self):
        r = _signal_for(_single_candle_bars())
        assert r.status == SignalStatus.SIGNAL
        assert r.direction == "LONG"
        assert r.entry_timestamp_ms == _ms(9)
        assert float(r.entry_price) == 101.20
        assert float(r.stop_price) == 100.80
        assert float(r.target_price) == 102.00
        assert r.setup_key == "LONG:ORB_HIGH:%d" % _ms(5)
        assert r.signal_key == "LONG:ORB_HIGH:%d:%d" % (_ms(5), _ms(9))

    def test_two_candle_detection_identical(self):
        r = _signal_for(_two_candle_bars())
        assert r.status == SignalStatus.SIGNAL
        assert r.direction == "LONG"
        assert r.entry_timestamp_ms == _ms(10)
        assert float(r.entry_price) == 101.60
        assert float(r.stop_price) == 100.10
        assert float(r.target_price) == 104.60
        assert r.setup_key == "LONG:ORB_HIGH:%d" % _ms(5)

    def test_no_setup_case_unaffected(self):
        """Prima del break non c'e' segnale, come prima."""
        sb = LiveSessionBuilder("QQQ")
        for b in _orb():
            sb.add_bar(b)
        det = LiveSignalDetector(symbol="QQQ", direction="LONG", tick_size=0.01)
        r = det.evaluate(sb.current_session())
        assert r.status == SignalStatus.NO_SETUP

    def test_detection_result_schema_untouched(self):
        """DetectionResult/v1 resta a 38 campi: il pattern NON e' stato
        infilato in un contratto congelato con parita' JS."""
        r = _signal_for(_single_candle_bars())
        assert len(r.detection_result.to_dict()) == 38
        assert "entry_pattern_type" not in r.detection_result.to_dict()


# ═════════════════════════════════════════════════════════════════════════
# T5 — backward compatibility
# ═════════════════════════════════════════════════════════════════════════

class TestT5BackwardCompatibility:
    def test_snapshot_without_rejection_detail_still_works(self):
        r = _signal_for(_single_candle_bars())
        snap = build_setup_snapshot(r.detection_result)   # nessun 2o argomento
        assert "entry_pattern_type" in snap
        assert snap["entry_pattern_type"] is None

    def test_snapshot_tolerates_rejection_detail_without_the_key(self):
        r = _signal_for(_single_candle_bars())
        snap = build_setup_snapshot(r.detection_result, rejection_detail={})
        assert snap["entry_pattern_type"] is None

    def test_snapshot_tolerates_non_dict_rejection_detail(self):
        r = _signal_for(_single_candle_bars())
        snap = build_setup_snapshot(r.detection_result, rejection_detail="x")
        assert snap["entry_pattern_type"] is None

    def test_old_record_without_pattern_stays_readable(self, tmp_path):
        from trading_lab.live.trade_state_store import persist_open_trade
        legacy = {"trade_id": "QQQ_LONG_ORB_HIGH_1", "symbol": "QQQ",
                  "setup_key": "LONG:ORB_HIGH:1", "state": "OPEN",
                  "setup_snapshot": {"level_source": "ORB_HIGH"}}
        back = json.loads(persist_open_trade(legacy, base_dir=tmp_path).read_text())
        assert "entry_pattern_type" not in back["setup_snapshot"]
        assert back["state"] == "OPEN"
