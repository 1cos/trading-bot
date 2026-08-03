"""Tests for frozen displacement rule: min 3 bars completely outside level.

The rule is temporarily frozen at 3. Not configurable from UI or presets.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading_lab.displacement_finder import find_displacement
from trading_lab.session_context import build_session_context
from trading_lab.orb_builder import build_orb
from trading_lab.break_finder import find_break

ET = ZoneInfo("America/New_York")
MS_0930 = int(datetime(2026, 7, 1, 9, 30, tzinfo=ET).timestamp() * 1000)


def _c(ms, o=100.0, h=101.0, l=99.0, cl=100.5):
    return {"time_ms": ms, "open": o, "high": h, "low": l, "close": cl, "volume": 1000}


def _cfg(**kw):
    base = {
        "timeframe_minutes": 5, "timezone": "America/New_York",
        "session_open": "09:30", "orb_start": "session_open",
        "orb_duration_minutes": 5, "level_source": "ORB_HIGH",
        "direction": "LONG", "tick_size": 0.01,
        "min_displacement_ticks": None, "min_penetration_ticks": None,
        "min_close_beyond_level_ticks": None,
    }
    base.update(kw)
    return base


def _make_session(n_disp_bars, direction="LONG"):
    """Build candles: ORB, break, N displacement bars, then retest contact."""
    level = 101.0 if direction == "LONG" else 99.0
    candles = [_c(MS_0930, o=100.0, h=101.0, l=99.0, cl=100.5)]  # ORB

    if direction == "LONG":
        # Break above ORB high
        candles.append(_c(MS_0930 + 300000, o=100.5, h=101.5, l=100.3, cl=101.3))
        # N displacement bars with low > ORB high
        for i in range(n_disp_bars):
            ms = MS_0930 + (2 + i) * 300000
            candles.append(_c(ms, o=101.2 + i*0.1, h=101.5 + i*0.1,
                             l=101.05 + i*0.05, cl=101.3 + i*0.1))
        # Retest contact: low touches ORB high
        ms = MS_0930 + (2 + n_disp_bars) * 300000
        candles.append(_c(ms, o=101.2, h=101.3, l=100.95, cl=101.1))
        # More bars for pipeline
        for i in range(5):
            ms = MS_0930 + (3 + n_disp_bars + i) * 300000
            candles.append(_c(ms, o=101.0, h=101.5, l=100.8, cl=101.2))
    else:
        # Break below ORB low
        candles.append(_c(MS_0930 + 300000, o=99.5, h=99.7, l=98.5, cl=98.7))
        # N displacement bars with high < ORB low
        for i in range(n_disp_bars):
            ms = MS_0930 + (2 + i) * 300000
            candles.append(_c(ms, o=98.8 - i*0.1, h=98.95 - i*0.05,
                             l=98.5 - i*0.1, cl=98.7 - i*0.1))
        # Retest contact
        ms = MS_0930 + (2 + n_disp_bars) * 300000
        candles.append(_c(ms, o=98.8, h=99.05, l=98.7, cl=98.9))
        for i in range(5):
            ms = MS_0930 + (3 + n_disp_bars + i) * 300000
            candles.append(_c(ms, o=98.9, h=99.2, l=98.5, cl=98.8))

    return candles


def _run_pipeline(candles, cfg):
    sc = build_session_context(candles, cfg)
    orb = build_orb(sc["candles"], sc, cfg)
    brk = find_break(sc["candles"], orb, cfg)
    return find_displacement(sc["candles"], orb, brk, cfg)


# ── Default is 3 ─────────────────────────────────────────────────────────────


class TestDefaultIs3:
    def test_finder_default_is_1(self):
        """Displacement finder default is 1 (low-level, preserves test compat)."""
        cfg = _cfg()
        assert "min_displacement_bars" not in cfg
        disp = _run_pipeline(_make_session(1), cfg)
        assert disp["status"] == "OK"

    def test_finder_with_explicit_3(self):
        """When config says 3, fewer than 3 bars are rejected."""
        cfg = _cfg(min_displacement_bars=3)
        disp = _run_pipeline(_make_session(2), cfg)
        assert disp["status"] == "FAILED"
        assert "2 bar" in disp["reason"]
        assert "3" in disp["reason"]

    def test_config_none_uses_finder_default(self):
        """min_displacement_bars=None → finder uses its default (1)."""
        cfg = _cfg(min_displacement_bars=None)
        disp = _run_pipeline(_make_session(1), cfg)
        assert disp["status"] == "OK"

    def test_server_sends_3(self):
        """Through the server, the strategy constant is always 3."""
        from trading_lab.backtest_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.post("/api/run", json={
                "symbols": ["SPY"], "timeframe": "5m",
                "preset": {"direction": "LONG"},
                "config": {"exit_target_r": "2"},
            })
            data = resp.get_json()
            # The preset in the response should show 3
            assert data["preset"]["min_displacement_bars"] == 3


# ── LONG ─────────────────────────────────────────────────────────────────────


class TestLong:
    def test_2_bars_rejected(self):
        disp = _run_pipeline(_make_session(2), _cfg(min_displacement_bars=3))
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "DISPLACEMENT_TOO_SHORT"

    def test_3_bars_accepted(self):
        disp = _run_pipeline(_make_session(3), _cfg(min_displacement_bars=3))
        assert disp["status"] == "OK"
        assert disp["displacement_bar_count"] >= 3

    def test_1_bar_rejected(self):
        disp = _run_pipeline(_make_session(1), _cfg(min_displacement_bars=3))
        assert disp["status"] == "FAILED"

    def test_5_bars_accepted(self):
        disp = _run_pipeline(_make_session(5), _cfg(min_displacement_bars=3))
        assert disp["status"] == "OK"


# ── SHORT ────────────────────────────────────────────────────────────────────


class TestShort:
    def test_2_bars_rejected(self):
        cfg = _cfg(direction="SHORT", level_source="ORB_LOW", min_displacement_bars=3)
        disp = _run_pipeline(_make_session(2, "SHORT"), cfg)
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "DISPLACEMENT_TOO_SHORT"

    def test_3_bars_accepted(self):
        cfg = _cfg(direction="SHORT", level_source="ORB_LOW", min_displacement_bars=3)
        disp = _run_pipeline(_make_session(3, "SHORT"), cfg)
        assert disp["status"] == "OK"


# ── QQQ verification ────────────────────────────────────────────────────────


class TestQQQVerification:
    @pytest.fixture
    def client(self):
        from trading_lab.backtest_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_qqq_0506_filtered(self, client):
        """QQQ 2026-05-06 (1 disp bar) must be filtered."""
        resp = client.post("/api/run", json={
            "symbols": ["QQQ"], "timeframe": "5m",
            "preset": {"direction": "LONG"},
            "config": {"exit_target_r": "2"},
        })
        data = resp.get_json()
        dates = [t["date"] for t in data.get("trades", [])]
        assert "2026-05-06" not in dates

    def test_qqq_0513_valid(self, client):
        """QQQ 2026-05-13 (6 disp bars) must remain valid."""
        resp = client.post("/api/run", json={
            "symbols": ["QQQ"], "timeframe": "5m",
            "preset": {"direction": "LONG"},
            "config": {"exit_target_r": "2"},
        })
        data = resp.get_json()
        dates = [t["date"] for t in data.get("trades", [])]
        assert "2026-05-13" in dates


# ── UI does not expose the control ──────────────────────────────────────────


class TestUINotExposed:
    @pytest.fixture
    def html(self):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent.parent / "lab" / "index.html").read_text()

    def test_no_displacement_input(self, html):
        assert "pMinDispBars" not in html

    def test_no_displacement_in_payload(self, html):
        assert "min_displacement_bars" not in html


# ── Preset does not contain the field ────────────────────────────────────────


class TestPresetExclusion:
    @pytest.fixture
    def client(self):
        from trading_lab.backtest_server import app
        from trading_lab.preset_store import PresetStore
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            from trading_lab import backtest_server as bs
            old = bs._preset_store
            bs._preset_store = PresetStore(Path(td))
            app.config["TESTING"] = True
            with app.test_client() as c:
                yield c
            bs._preset_store = old

    def test_new_preset_no_displacement_field(self, client):
        resp = client.post("/api/presets", json={
            "name": "Test",
            "parameters": {
                "symbol": "SPY", "timeframe": "5m",
                "direction": "LONG", "level_source": "ORB_HIGH",
                "orb_duration_minutes": 5, "consecutive_orb_closes": 2,
                "entry_model": "CONFIRMATION_CLOSE",
                "min_close_beyond_level_ticks": None,
                "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
                "exit_target_r": "2",
                "rejection_wick_ratio_min": "0.47",
                "body_ratio_max": "0.4",
                "tick_size": "0.01",
            }
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert "min_displacement_bars" not in data["parameters"]
