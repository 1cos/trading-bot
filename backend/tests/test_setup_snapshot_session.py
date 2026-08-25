"""La data di sessione canonica deve essere nel record, non ricostruita.

`DetectionResult/v1` porta `session` (SessionMetadata: date,
market_timezone, session_open/close_utc_ms, timeframe_seconds), ma
`_SETUP_SNAPSHOT_FIELDS` non lo includeva: il dato veniva scartato in
`build_setup_snapshot()`. Senza di esso l'attribuzione di una trade a
giorno/settimana/mese va ricalcolata da `entry_timestamp_ms` con una
timezone che nessun record e nessun endpoint espone.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
from datetime import datetime as dt_cls, timezone

from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.trade_state_store import build_setup_snapshot

from test_trade_close_persistence import (
    _entry_bars, _run_full_trade,
)
from test_trade_terminal_persistence import _run_to_exhaustion


def _signal():
    sb = LiveSessionBuilder("QQQ")
    for b in _entry_bars():
        sb.add_bar(b)
    det = LiveSignalDetector(symbol="QQQ", direction="LONG", tick_size=0.01)
    r = det.evaluate(sb.current_session())
    assert r.status == SignalStatus.SIGNAL
    return r


# ═════════════════════════════════════════════════════════════════════════
# T1 — session nello snapshot
# ═════════════════════════════════════════════════════════════════════════

class TestT1SessionInSnapshot:
    def test_detection_result_already_has_session(self):
        """Pre-condizione: il dato esiste a monte."""
        r = _signal()
        full = r.detection_result.to_dict()
        assert "session" in full
        assert full["session"]["date"]
        assert full["session"]["market_timezone"]

    def test_snapshot_carries_session(self):
        r = _signal()
        snap = build_setup_snapshot(r.detection_result,
                                    rejection_detail=r.rejection_detail)
        assert "session" in snap, (
            "la data di sessione canonica viene scartata dallo snapshot")

    def test_session_values_come_from_the_detection_result(self):
        """Copiati, non ricalcolati."""
        r = _signal()
        full = r.detection_result.to_dict()
        snap = build_setup_snapshot(r.detection_result,
                                    rejection_detail=r.rejection_detail)
        assert snap["session"] == full["session"]
        assert snap["session"]["market_timezone"] == "America/New_York"

    def test_session_is_plain_json(self):
        r = _signal()
        snap = build_setup_snapshot(r.detection_result)
        json.dumps(snap["session"])
        assert isinstance(snap["session"]["date"], str)

    def test_no_parallel_date_fields_introduced(self):
        """Niente trade_date/timezone paralleli: si usa il contratto."""
        r = _signal()
        snap = build_setup_snapshot(r.detection_result)
        for forbidden in ("trade_date", "timezone", "session_date",
                          "market_timezone"):
            assert forbidden not in snap, f"campo parallelo introdotto: {forbidden}"


# ═════════════════════════════════════════════════════════════════════════
# T2 / T3 / T4 — persistenza nei tre stati
# ═════════════════════════════════════════════════════════════════════════

class TestT2OpenPersistence:
    def test_open_record_has_session(self, tmp_path):
        _, _, open_rec, _, _ = _run_full_trade(tmp_path)
        sess = open_rec["setup_snapshot"]["session"]
        assert sess["date"]
        assert sess["market_timezone"] == "America/New_York"


class TestT3ClosedPreservesSession:
    def test_session_identical_after_close(self, tmp_path):
        _, _, open_rec, closed_rec, _ = _run_full_trade(tmp_path)
        assert closed_rec["state"] == "CLOSED"
        assert (closed_rec["setup_snapshot"]["session"]
                == open_rec["setup_snapshot"]["session"])


class TestT4TerminalPreservesSession:
    def test_session_identical_after_requires_attention(self, tmp_path):
        _, _, open_rec, final, _, _ = _run_to_exhaustion(tmp_path)
        assert final["state"] == "REQUIRES_ATTENTION"
        assert (final["setup_snapshot"]["session"]
                == open_rec["setup_snapshot"]["session"])


# ═════════════════════════════════════════════════════════════════════════
# T5 — nessun cambiamento di detection
# ═════════════════════════════════════════════════════════════════════════

class TestT5DetectionUnchanged:
    def test_signal_values_unchanged(self):
        r = _signal()
        assert r.status == SignalStatus.SIGNAL
        assert r.direction == "LONG"
        assert float(r.entry_price) == 101.20
        assert float(r.stop_price) == 100.80
        assert float(r.target_price) == 102.00
        assert r.setup_key.startswith("LONG:ORB_HIGH:")

    def test_pattern_unchanged(self):
        r = _signal()
        snap = build_setup_snapshot(r.detection_result,
                                    rejection_detail=r.rejection_detail)
        assert snap["entry_pattern_type"] == "SINGLE_CANDLE_REJECTION"

    def test_detection_result_schema_still_38(self):
        r = _signal()
        assert len(r.detection_result.to_dict()) == 38

    def test_open_record_prices_and_keys_unchanged(self, tmp_path):
        _, _, open_rec, _, _ = _run_full_trade(tmp_path)
        assert open_rec["underlying_entry"] == 101.20
        assert open_rec["stop"] == 100.80
        assert open_rec["target"] == 102.00


# ═════════════════════════════════════════════════════════════════════════
# T6 — backward compatibility, nessuna migrazione
# ═════════════════════════════════════════════════════════════════════════

class TestT6BackwardCompatibility:
    def test_old_record_without_session_stays_valid(self, tmp_path):
        from trading_lab.live.trade_state_store import persist_open_trade
        legacy = {
            "trade_id": "QQQ_LONG_1786455300000", "symbol": "QQQ",
            "setup_key": "LONG:1786455300000", "state": "OPEN",
            "setup_snapshot": {"level_source": "ORB_HIGH"},
        }
        back = json.loads(persist_open_trade(legacy, base_dir=tmp_path).read_text())
        assert "session" not in back["setup_snapshot"]
        assert back["state"] == "OPEN"

    def test_closing_an_old_record_does_not_invent_session(self, tmp_path):
        from trading_lab.live.trade_state_store import (
            persist_closed_trade, persist_open_trade,
        )
        legacy = {"trade_id": "QQQ_LONG_1", "symbol": "QQQ", "state": "OPEN",
                  "setup_snapshot": {"level_source": "ORB_HIGH"}}
        persist_open_trade(legacy, base_dir=tmp_path)
        path = persist_closed_trade("QQQ_LONG_1", {"result": "WIN"},
                                    base_dir=tmp_path)
        back = json.loads(path.read_text())
        assert back["state"] == "CLOSED"
        assert "session" not in back["setup_snapshot"]

    def test_snapshot_without_detection_result_is_none(self):
        assert build_setup_snapshot(None) is None
