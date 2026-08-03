"""Tests for canonical Direction/Level Source mapping.

BDRR canonical mapping:
    LONG  → ORB_HIGH only
    SHORT → ORB_LOW only
    BOTH  → LONG/ORB_HIGH + SHORT/ORB_LOW

Cross combinations (LONG/ORB_LOW, SHORT/ORB_HIGH) must never be executed.
"""

import pytest
from trading_lab.backtest_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _run(client, direction="LONG", level_source=None):
    preset = {"direction": direction}
    if level_source:
        preset["level_source"] = level_source
    resp = client.post("/api/run", json={
        "symbols": ["SPY"], "timeframe": "5m",
        "preset": preset,
        "config": {"exit_target_r": "2"},
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    return resp.get_json()


# ── Canonical combinations ───────────────────────────────────────────────────


class TestCanonicalMapping:
    def test_long_produces_orb_high(self, client):
        d = _run(client, "LONG")
        assert d["executed_combinations"] == [
            {"direction": "LONG", "level_source": "ORB_HIGH"}
        ]

    def test_short_produces_orb_low(self, client):
        d = _run(client, "SHORT")
        assert d["executed_combinations"] == [
            {"direction": "SHORT", "level_source": "ORB_LOW"}
        ]

    def test_both_produces_two_canonical(self, client):
        d = _run(client, "BOTH")
        assert d["executed_combinations"] == [
            {"direction": "LONG", "level_source": "ORB_HIGH"},
            {"direction": "SHORT", "level_source": "ORB_LOW"},
        ]

    def test_long_ignores_level_source_override(self, client):
        """Even if level_source=ORB_LOW is sent, LONG still uses ORB_HIGH."""
        d = _run(client, "LONG", "ORB_LOW")
        assert d["executed_combinations"] == [
            {"direction": "LONG", "level_source": "ORB_HIGH"}
        ]

    def test_short_ignores_level_source_override(self, client):
        """Even if level_source=ORB_HIGH is sent, SHORT still uses ORB_LOW."""
        d = _run(client, "SHORT", "ORB_HIGH")
        assert d["executed_combinations"] == [
            {"direction": "SHORT", "level_source": "ORB_LOW"}
        ]


# ── Cross combinations never produced ───────────────────────────────────────


class TestCrossCombinationsBlocked:
    def test_no_long_orb_low_in_trades(self, client):
        d = _run(client, "BOTH")
        for t in d.get("trades", []):
            if t["direction"] == "LONG":
                assert t["level_source"] == "ORB_HIGH"

    def test_no_short_orb_high_in_trades(self, client):
        d = _run(client, "BOTH")
        for t in d.get("trades", []):
            if t["direction"] == "SHORT":
                assert t["level_source"] == "ORB_LOW"

    def test_chart_events_match(self, client):
        d = _run(client, "BOTH")
        for ev in d.get("chart_events", []):
            if ev["direction"] == "LONG":
                assert ev.get("level_source") == "ORB_HIGH"
            if ev["direction"] == "SHORT":
                assert ev.get("level_source") == "ORB_LOW"


# ── BOTH never in pipeline ──────────────────────────────────────────────────


class TestBothNeverInPipeline:
    def test_trade_directions_concrete(self, client):
        d = _run(client, "BOTH")
        for t in d.get("trades", []):
            assert t["direction"] in ("LONG", "SHORT")
            assert t["level_source"] in ("ORB_HIGH", "ORB_LOW")


# ── Metrics and alignment ───────────────────────────────────────────────────


class TestMetricsAndAlignment:
    def test_both_has_metrics(self, client):
        d = _run(client, "BOTH")
        assert "total_detected" in d["metrics"]
        assert "net_r" in d["metrics"]

    def test_trades_chart_events_aligned(self, client):
        d = _run(client, "BOTH")
        assert len(d.get("trades", [])) == len(d.get("chart_events", []))


# ── Deduplication ────────────────────────────────────────────────────────────


class TestDeduplication:
    def test_no_duplicate_keys(self, client):
        d = _run(client, "BOTH")
        keys = []
        for t in d.get("trades", []):
            k = (t["symbol"], t["date"], t["direction"], t["level_source"])
            keys.append(k)
        assert len(keys) == len(set(keys))


# ── Response fields ──────────────────────────────────────────────────────────


class TestResponseFields:
    def test_direction_in_response(self, client):
        d = _run(client, "BOTH")
        assert d["direction"] == "BOTH"

    def test_level_source_in_response(self, client):
        d = _run(client, "BOTH")
        assert d["level_source"] == "BOTH"

    def test_long_level_source(self, client):
        d = _run(client, "LONG")
        assert d["level_source"] == "ORB_HIGH"
