"""Tests for crash-safe OPEN trade-state persistence.

Verifies that MaxBotTradeOrchestrator writes an atomic, crash-safe
record to disk exactly once — immediately when an entry fill is
confirmed (ENTRY_FILLED -> POSITION_OPEN) — and never on rejected,
cancelled, or still-pending entries.

Uses the same fake-broker-adapter style as test_trade_orchestrator.py
(FakeOptionSelector / FakeEntryExecutor) combined with a mocked
signal_detector (as in test_one_setup_one_trade.py) so setup_key,
entry_timestamp_ms, and the underlying entry/stop/target prices can be
controlled precisely — needed for the exact-value round-trip test (F).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from trading_lab.live.signal_detector import SignalResult, SignalStatus
from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator, LifecycleState
from trading_lab.live.trade_state_store import build_trade_id


# ── Fake broker adapters (mirrors test_trade_orchestrator.py) ───────────────


class FakeOptionSelector:
    def select(self, **kwargs):
        return SimpleNamespace(
            underlying_symbol=kwargs.get("underlying_symbol", "SNDK"),
            underlying_price=kwargs.get("underlying_price", 1636.01),
            right=kwargs.get("right", "P"),
            expiration="20260821",
            strike=1640.0,
            exchange="SMART",
            trading_class="SNDK",
            multiplier="100",
            quantity=1,
            con_id=879030488,
            qualified_contract=SimpleNamespace(
                conId=879030488, symbol="SNDK",
                localSymbol="SNDK  260821P01640000",
            ),
            bid=53.00, ask=54.00, spread=1.00,
        )


class FakeEntryExecutor:
    """Simulates entry submission with controllable fill status."""

    def __init__(self, order_id=631, con_id=879030488):
        self.submissions = []
        self._order_id = order_id
        self._con_id = con_id
        self._status = SimpleNamespace(
            status="PendingSubmit", filled=0.0, remaining=1.0, avgFillPrice=0.0,
        )
        self._order = SimpleNamespace(orderId=order_id, permId=999)
        self._fills = []
        self._trade = SimpleNamespace(
            order=self._order, orderStatus=self._status,
            fills=self._fills, log=[],
        )

    def submit_entry(self, order_spec):
        self.submissions.append(order_spec)
        return SimpleNamespace(
            trade=self._trade, con_id=self._con_id, underlying_symbol="SNDK",
            right="P", expiration="20260821", strike=1640.0,
            quantity=1, limit_price=53.80, order_id=self._order_id,
            perm_id=999, status=self._status.status,
        )

    def set_filled(self, avg_price=53.80):
        fill_time = datetime(2026, 8, 19, 13, 47, 59, tzinfo=timezone.utc)
        self._status.status = "Filled"
        self._status.filled = 1.0
        self._status.remaining = 0.0
        self._status.avgFillPrice = avg_price
        self._fills.append(SimpleNamespace(time=fill_time))

    def set_cancelled(self):
        self._status.status = "Cancelled"

    def set_rejected(self):
        self._status.status = "Inactive"


# ── Orchestrator factory ─────────────────────────────────────────────────────


def _price(value):
    """A trade_plan price field with a .to_price() accessor, matching
    the real TradePlan's TickPrice-style fields (see execution_intent.py:
    build_option_execution_intent reads trade_plan.entry_price.to_price(),
    .stop_price.to_price(), .r2_price.to_price())."""
    return SimpleNamespace(to_price=lambda: Decimal(str(value)))


def _trade_plan(entry, stop, target):
    return SimpleNamespace(
        entry_price=_price(entry), stop_price=_price(stop),
        r2_price=_price(target), r3_price=_price(target), r4_price=_price(target),
    )


def _sig(setup_key, entry_ts, entry=1636.01, stop=1659.43, target=1589.17,
         direction="SHORT"):
    return SignalResult(
        status=SignalStatus.SIGNAL, direction=direction,
        pipeline_stage="SIGNAL", failed_stage=None,
        setup_key=setup_key, signal_key=f"{setup_key}:{entry_ts}",
        entry_timestamp_ms=entry_ts,
        stage_context={"break_bar_index": 5},
        trade_plan=_trade_plan(entry, stop, target),
        detection_result=MagicMock(),
    )


def _make_orch(signal_results, symbol="SNDK", direction="SHORT",
               trade_state_dir=None, entry_executor=None):
    sb = MagicMock()
    sb.current_session.return_value = {
        "date": "2026-08-19",
        "candles": [{"time_ms": 1787147160000, "open": 1651.49, "high": 1654.13,
                     "low": 1635.02, "close": 1636.01, "volume": 71323}],
    }
    sd = MagicMock()
    sd.evaluate = MagicMock(side_effect=signal_results)
    sd.last_result = None
    tm = MagicMock()
    tm.can_trade = True
    ee = entry_executor or FakeEntryExecutor()

    orch = MaxBotTradeOrchestrator(
        underlying_symbol=symbol, direction=direction, tick_size=0.01,
        session_builder=sb, signal_detector=sd, trade_manager=tm,
        option_selector=FakeOptionSelector(), entry_executor=ee,
        exit_executor=MagicMock(),
        trade_state_dir=trade_state_dir,
    )
    return orch, ee


def _bar(t=1787147160000):
    return {"time_ms": t, "open": 1651.49, "high": 1654.13,
            "low": 1635.02, "close": 1636.01, "volume": 71323}


# ═════════════════════════════════════════════════════════════════════════
# A. Confirmed fill writes an OPEN record with all expected fields
# ═════════════════════════════════════════════════════════════════════════


class TestConfirmedFillWritesRecord:
    def test_full_record_written_on_fill(self, tmp_path):
        sig = _sig("SHORT:1787146500000", 1787147160000)
        orch, ee = _make_orch([sig], trade_state_dir=tmp_path)

        orch.on_bar(_bar())
        assert orch.has_pending_signal
        orch.execute_pending_signal()
        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED

        # No file yet — entry only submitted, not filled.
        assert list(tmp_path.glob("*.json")) == []

        ee.set_filled(avg_price=53.80)
        orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.POSITION_OPEN

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        record = json.loads(files[0].read_text())

        assert record["trade_id"] == "SNDK_SHORT_1787146500000"
        assert record["symbol"] == "SNDK"
        assert record["setup_key"] == "SHORT:1787146500000"
        assert record["signal_key"] == "SHORT:1787146500000:1787147160000"
        assert record["direction"] == "SHORT"
        assert record["entry_timestamp_ms"] == 1787147160000
        assert record["underlying_entry"] == 1636.01
        assert record["stop"] == 1659.43
        assert record["target"] == 1589.17
        assert record["rr"] == 2
        assert record["option"]["con_id"] == 879030488
        assert record["option"]["local_symbol"] == "SNDK  260821P01640000"
        assert record["option"]["right"] == "P"
        assert record["option"]["strike"] == 1640.0
        assert record["option"]["expiry"] == "20260821"
        assert record["quantity"] == 1
        assert record["entry_order_id"] == 631
        assert record["entry_fill_price"] == 53.80
        assert record["state"] == "OPEN"


# ═════════════════════════════════════════════════════════════════════════
# B. Rejected entry writes nothing
# ═════════════════════════════════════════════════════════════════════════


class TestRejectedEntryWritesNothing:
    def test_no_file_on_rejection(self, tmp_path):
        sig = _sig("SHORT:2000", 2000)
        orch, ee = _make_orch([sig], trade_state_dir=tmp_path)

        orch.on_bar(_bar(2000))
        orch.execute_pending_signal()
        ee.set_rejected()
        orch.refresh_entry_status()

        assert orch.lifecycle == LifecycleState.WAITING_FOR_SIGNAL
        assert list(tmp_path.glob("*.json")) == []


# ═════════════════════════════════════════════════════════════════════════
# C. Unfilled/pending entry writes nothing
# ═════════════════════════════════════════════════════════════════════════


class TestPendingEntryWritesNothing:
    def test_no_file_while_pending(self, tmp_path):
        sig = _sig("SHORT:3000", 3000)
        orch, ee = _make_orch([sig], trade_state_dir=tmp_path)

        orch.on_bar(_bar(3000))
        orch.execute_pending_signal()
        # Still PendingSubmit — no fill, no rejection.
        orch.refresh_entry_status()

        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED
        assert list(tmp_path.glob("*.json")) == []


# ═════════════════════════════════════════════════════════════════════════
# D. Multiple trades on the same symbol produce distinct files
# ═════════════════════════════════════════════════════════════════════════


class TestMultipleTradesSameSymbol:
    def test_distinct_setup_keys_produce_distinct_files(self, tmp_path):
        sig1 = _sig("SHORT:1000", 1000, entry=100.0, stop=105.0, target=90.0)
        sig2 = _sig("SHORT:5000", 5000, entry=200.0, stop=205.0, target=190.0)
        ee = FakeEntryExecutor(order_id=701)
        orch, _ = _make_orch([sig1, sig2], trade_state_dir=tmp_path, entry_executor=ee)

        # Trade 1: signal -> submit -> fill -> POSITION_OPEN.
        orch.on_bar(_bar(1000))
        orch.execute_pending_signal()
        ee.set_filled(avg_price=10.0)
        orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.POSITION_OPEN

        # Force back to WAITING_FOR_SIGNAL to simulate the position
        # having been closed, freeing the orchestrator for a second,
        # genuinely different setup on the same symbol.
        orch._clear_active_trade()
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        ee2 = FakeEntryExecutor(order_id=702)
        orch._entry_executor = ee2

        orch.on_bar(_bar(5000))
        orch.execute_pending_signal()
        ee2.set_filled(avg_price=20.0)
        orch.refresh_entry_status()
        assert orch.lifecycle == LifecycleState.POSITION_OPEN

        files = sorted(f.name for f in tmp_path.glob("*.json"))
        assert files == ["SNDK_SHORT_1000.json", "SNDK_SHORT_5000.json"]

        rec1 = json.loads((tmp_path / "SNDK_SHORT_1000.json").read_text())
        rec2 = json.loads((tmp_path / "SNDK_SHORT_5000.json").read_text())
        assert rec1["underlying_entry"] == 100.0
        assert rec2["underlying_entry"] == 200.0
        assert rec1["entry_order_id"] == 701
        assert rec2["entry_order_id"] == 702


# ═════════════════════════════════════════════════════════════════════════
# E. Atomic replacement — no temp files left, valid JSON
# ═════════════════════════════════════════════════════════════════════════


class TestAtomicReplacement:
    def test_no_leftover_temp_file_and_valid_json(self, tmp_path):
        sig = _sig("SHORT:4000", 4000)
        orch, ee = _make_orch([sig], trade_state_dir=tmp_path)

        orch.on_bar(_bar(4000))
        orch.execute_pending_signal()
        ee.set_filled()
        orch.refresh_entry_status()

        all_files = list(tmp_path.iterdir())
        json_files = [f for f in all_files if f.suffix == ".json"]
        tmp_files = [f for f in all_files if f.name.endswith(".tmp")]

        assert len(json_files) == 1
        assert tmp_files == []  # no leftover temp artifact
        # Must be valid, complete JSON (would raise if truncated).
        json.loads(json_files[0].read_text())

    def test_persist_open_trade_uses_replace_pattern(self, tmp_path):
        """Directly exercises the store function: temp file is written
        then atomically replaced, never appended to directly."""
        from trading_lab.live.trade_state_store import persist_open_trade

        record = {"trade_id": "TEST_SHORT_1", "state": "OPEN"}
        path = persist_open_trade(record, base_dir=tmp_path)

        assert path == tmp_path / "TEST_SHORT_1.json"
        assert path.exists()
        assert not (tmp_path / ".TEST_SHORT_1.json.tmp").exists()
        assert json.loads(path.read_text()) == record


# ═════════════════════════════════════════════════════════════════════════
# F. SNDK-shaped record — exact round trip
# ═════════════════════════════════════════════════════════════════════════


class TestSndkShapedRoundTrip:
    def test_exact_sndk_values_round_trip(self, tmp_path):
        sig = _sig("SHORT:1787146500000", 1787147160000,
                    entry=1636.01, stop=1659.43, target=1589.17)
        ee = FakeEntryExecutor(order_id=631, con_id=879030488)
        orch, _ = _make_orch([sig], trade_state_dir=tmp_path, entry_executor=ee)

        orch.on_bar(_bar())
        orch.execute_pending_signal()
        ee.set_filled(avg_price=53.80)
        orch.refresh_entry_status()

        expected_id = build_trade_id("SNDK", "SHORT:1787146500000")
        path = tmp_path / f"{expected_id}.json"
        assert path.exists()

        record = json.loads(path.read_text())
        # I blocchi additivi (setup_snapshot, chart_context) sono
        # verificati dalle loro suite. Qui l'intento e' che i valori
        # SNDK preesistenti sopravvivano ESATTAMENTE al round-trip, e
        # che nessun campo storico sia sparito: il confronto e' quindi
        # sui campi originali, non sulla forma totale del record.
        additive = {"setup_snapshot", "chart_context"}
        core = {k: v for k, v in record.items() if k not in additive}
        assert set(record) - set(core) <= additive, (
            f"campi inattesi: {set(record) - set(core) - additive}")
        assert core == {
            "trade_id": "SNDK_SHORT_1787146500000",
            "symbol": "SNDK",
            "setup_key": "SHORT:1787146500000",
            "signal_key": "SHORT:1787146500000:1787147160000",
            "direction": "SHORT",
            "entry_timestamp_ms": 1787147160000,
            "underlying_entry": 1636.01,
            "stop": 1659.43,
            "target": 1589.17,
            "rr": 2,
            "option": {
                "con_id": 879030488,
                "local_symbol": "SNDK  260821P01640000",
                "right": "P",
                "strike": 1640.0,
                "expiry": "20260821",
            },
            "quantity": 1,
            "entry_order_id": 631,
            "entry_fill_price": 53.80,
            "state": "OPEN",
        }
