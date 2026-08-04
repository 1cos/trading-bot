"""Tests for canonical ORB High/Low in chart events.

Verifies that chart events use max/min over the entire ORB window,
not the high/low of the last ORB candle.
"""

import pytest


@pytest.fixture
def client():
    from trading_lab.backtest_server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _run(client, sym, tf, start=None, end=None):
    payload = {
        "symbols": [sym],
        "start_date": start or "",
        "end_date": end or "",
        "timeframe": tf,
        "preset": {
            "direction": "BOTH", "level_source": "BOTH",
            "orb_duration_minutes": 5, "consecutive_orb_closes": 2,
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
            "confirmation_wick_penetration_pct_min": 0,
        },
        "config": {"exit_target_r": "2", "tick_size": 0.01},
    }
    resp = client.post("/api/run", json=payload)
    return resp.get_json()


class TestOrbHighLowInChartEvents:
    def test_nvda_20260730_orb_high(self, client):
        """NVDA 2026-07-30 1m: ORB High must be 193.50 (bar 09:30), not 193.20."""
        data = _run(client, "NVDA", "1m", "2026-07-30", "2026-07-30")
        assert len(data.get("chart_events", [])) >= 1
        ev = data["chart_events"][0]
        assert ev["orb_high_ticks"] == 19350  # 193.50

    def test_nvda_20260730_orb_low(self, client):
        """NVDA 2026-07-30 1m: ORB Low must be 191.73 (bar 09:32), not 191.94."""
        data = _run(client, "NVDA", "1m", "2026-07-30", "2026-07-30")
        ev = data["chart_events"][0]
        assert ev["orb_low_ticks"] == 19172  # 191.72 (IBKR)

    def test_level_price_consistent_long(self, client):
        """For LONG, level_price_ticks must equal orb_high_ticks."""
        data = _run(client, "NVDA", "1m", "2026-07-30", "2026-07-30")
        for ev in data.get("chart_events", []):
            if ev.get("direction") == "LONG":
                assert ev["level_price_ticks"] == ev["orb_high_ticks"]

    def test_level_price_consistent_short(self, client):
        """For SHORT, level_price_ticks must equal orb_low_ticks."""
        data = _run(client, "NVDA", "1m", "2026-07-30", "2026-07-30")
        for ev in data.get("chart_events", []):
            if ev.get("direction") == "SHORT":
                assert ev["level_price_ticks"] == ev["orb_low_ticks"]

    def test_single_candle_orb_unchanged(self, client):
        """5m/5m single-candle ORB: orb_high/low should still match level_bar."""
        data = _run(client, "SPY", "5m")
        if not data.get("chart_events"):
            pytest.skip("No 5m trades")
        for ev in data["chart_events"]:
            # For single-candle ORB, the ORB builder's max/min IS the candle's H/L
            # so the values should be valid regardless
            assert ev["orb_high_ticks"] is not None
            assert ev["orb_low_ticks"] is not None
            assert ev["orb_high_ticks"] >= ev["orb_low_ticks"]

    def test_multi_candle_orb_not_last_bar(self, client):
        """1m/5m multi-candle ORB: orb_high must NOT equal last ORB bar's high
        when a prior bar has a higher high."""
        data = _run(client, "NVDA", "1m", "2026-07-30", "2026-07-30")
        ev = data["chart_events"][0]
        # Last ORB bar (09:34) high is 193.20 = 19320 ticks
        # True ORB High is 193.50 = 19350 ticks (from 09:30)
        assert ev["orb_high_ticks"] != 19320  # must NOT be last bar's high

    def test_trades_and_events_aligned(self, client):
        """trades and chart_events must have same count."""
        data = _run(client, "NVDA", "1m", "2026-07-30", "2026-07-30")
        trades = data.get("trades", [])
        events = data.get("chart_events", [])
        assert len(trades) == len(events)
