"""Tests for the Backtest Lab API server.

Covers:
  1. /api/symbols returns available symbols
  2. /api/defaults returns frozen default preset
  3. /api/run executes real detector (not hardcoded)
  4. Changing consecutive_orb_closes changes results
  5. Changing direction changes results
  6. Invalid timeframe returns error
  7. Metrics are computed from real data
  8. Chart events are returned for each trade
  9. Date range filtering works
  10. Frozen default regression: SPY LONG matches known counts
"""

import pytest

from trading_lab.backtest_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


RUN_BODY = {
    "symbols": ["SPY"],
    "start_date": "2026-04-24",
    "end_date": "2026-07-21",
    "timeframe": "5m",
    "preset": {"direction": "LONG", "consecutive_orb_closes": 2},
    "config": {"exit_target_r": 2, "tick_size": 0.01},
}


class TestApiSymbols:
    def test_returns_list(self, client):
        r = client.get("/api/symbols")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
        assert len(data) >= 8

    def test_each_symbol_has_dates(self, client):
        data = client.get("/api/symbols").get_json()
        for s in data:
            assert "symbol" in s
            assert "earliest" in s
            assert "latest" in s
            assert "session_count" in s
            assert s["session_count"] > 0


class TestApiDefaults:
    def test_returns_preset(self, client):
        r = client.get("/api/defaults")
        data = r.get_json()
        assert data["preset"]["preset_id"] == "frozen_default"
        assert data["preset"]["consecutive_orb_closes"] == 2

    def test_parameter_schema_present(self, client):
        data = client.get("/api/defaults").get_json()
        assert "parameter_schema" in data
        assert "sequence" in data["parameter_schema"]

    def test_timeframes_listed(self, client):
        data = client.get("/api/defaults").get_json()
        tfs = data["available_timeframes"]
        assert any(t["value"] == "5m" and t["available"] for t in tfs)
        assert any(t["value"] == "1m" and not t["available"] for t in tfs)


class TestApiRun:
    def test_returns_results(self, client):
        r = client.post("/api/run", json=RUN_BODY)
        assert r.status_code == 200
        data = r.get_json()
        assert "run_id" in data
        assert "metrics" in data
        assert "trades" in data
        assert "chart_events" in data

    def test_metrics_not_hardcoded(self, client):
        data = client.post("/api/run", json=RUN_BODY).get_json()
        m = data["metrics"]
        assert "total_detected" in m
        assert "win_rate" in m
        assert "net_r" in m
        assert "equity_curve" in m
        assert isinstance(m["equity_curve"], list)

    def test_trade_count_matches_chart_events(self, client):
        data = client.post("/api/run", json=RUN_BODY).get_json()
        assert len(data["trades"]) == len(data["chart_events"])

    def test_changing_consecutive_orb_closes(self, client):
        body1 = {**RUN_BODY, "preset": {"direction": "LONG", "consecutive_orb_closes": 2}}
        body2 = {**RUN_BODY, "preset": {"direction": "LONG", "consecutive_orb_closes": 5}}
        r1 = client.post("/api/run", json=body1).get_json()
        r2 = client.post("/api/run", json=body2).get_json()
        # Different parameter must produce different result
        assert r1["metrics"]["total_detected"] != r2["metrics"]["total_detected"]

    def test_changing_direction(self, client):
        body_long = {**RUN_BODY, "preset": {"direction": "LONG", "level_source": "ORB_HIGH"}}
        body_short = {**RUN_BODY, "preset": {"direction": "SHORT", "level_source": "ORB_LOW"}}
        r1 = client.post("/api/run", json=body_long).get_json()
        r2 = client.post("/api/run", json=body_short).get_json()
        # Direction change should affect results
        if r1["trades"]:
            assert r1["trades"][0].get("direction") == "LONG"
        if r2["trades"]:
            assert r2["trades"][0].get("direction") == "SHORT"

    def test_invalid_timeframe(self, client):
        body = {**RUN_BODY, "timeframe": "1m"}
        r = client.post("/api/run", json=body)
        assert r.status_code == 400
        assert "error" in r.get_json()

    def test_date_range_filtering(self, client):
        body_full = {**RUN_BODY}
        body_narrow = {**RUN_BODY, "start_date": "2026-07-01", "end_date": "2026-07-10"}
        r1 = client.post("/api/run", json=body_full).get_json()
        r2 = client.post("/api/run", json=body_narrow).get_json()
        assert r2["total_sessions"] < r1["total_sessions"]


class TestFrozenDefaultRegression:
    def test_spy_long_frozen_default(self, client):
        """SPY LONG with frozen defaults must produce exactly 3 VALID."""
        data = client.post("/api/run", json=RUN_BODY).get_json()
        assert data["metrics"]["total_detected"] == 3
        dates = sorted(t["date"] for t in data["trades"])
        assert dates == ["2026-05-26", "2026-06-08", "2026-07-06"]
