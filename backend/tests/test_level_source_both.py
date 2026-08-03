"""Tests for Level Source BOTH — cartesian product of Direction × Level Source.

Covers all 9 combinations, deduplication, metrics, UI, regression.
"""

import pytest

from trading_lab.backtest_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _run(client, direction="LONG", level_source="ORB_HIGH"):
    resp = client.post("/api/run", json={
        "symbols": ["SPY"], "timeframe": "5m",
        "preset": {"direction": direction, "level_source": level_source},
        "config": {"exit_target_r": "2"},
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    return resp.get_json()


# ── All 9 combinations ──────────────────────────────────────────────────────


class TestAllCombinations:
    def test_long_orb_high(self, client):
        d = _run(client, "LONG", "ORB_HIGH")
        assert d["executed_combinations"] == [
            {"direction": "LONG", "level_source": "ORB_HIGH"}
        ]

    def test_long_orb_low(self, client):
        d = _run(client, "LONG", "ORB_LOW")
        assert d["executed_combinations"] == [
            {"direction": "LONG", "level_source": "ORB_LOW"}
        ]

    def test_long_both(self, client):
        d = _run(client, "LONG", "BOTH")
        assert d["executed_combinations"] == [
            {"direction": "LONG", "level_source": "ORB_HIGH"},
            {"direction": "LONG", "level_source": "ORB_LOW"},
        ]

    def test_short_orb_high(self, client):
        d = _run(client, "SHORT", "ORB_HIGH")
        assert d["executed_combinations"] == [
            {"direction": "SHORT", "level_source": "ORB_HIGH"}
        ]

    def test_short_orb_low(self, client):
        d = _run(client, "SHORT", "ORB_LOW")
        assert d["executed_combinations"] == [
            {"direction": "SHORT", "level_source": "ORB_LOW"}
        ]

    def test_short_both(self, client):
        d = _run(client, "SHORT", "BOTH")
        assert d["executed_combinations"] == [
            {"direction": "SHORT", "level_source": "ORB_HIGH"},
            {"direction": "SHORT", "level_source": "ORB_LOW"},
        ]

    def test_both_orb_high(self, client):
        d = _run(client, "BOTH", "ORB_HIGH")
        assert d["executed_combinations"] == [
            {"direction": "LONG", "level_source": "ORB_HIGH"},
            {"direction": "SHORT", "level_source": "ORB_HIGH"},
        ]

    def test_both_orb_low(self, client):
        d = _run(client, "BOTH", "ORB_LOW")
        assert d["executed_combinations"] == [
            {"direction": "LONG", "level_source": "ORB_LOW"},
            {"direction": "SHORT", "level_source": "ORB_LOW"},
        ]

    def test_both_both(self, client):
        d = _run(client, "BOTH", "BOTH")
        assert d["executed_combinations"] == [
            {"direction": "LONG", "level_source": "ORB_HIGH"},
            {"direction": "LONG", "level_source": "ORB_LOW"},
            {"direction": "SHORT", "level_source": "ORB_HIGH"},
            {"direction": "SHORT", "level_source": "ORB_LOW"},
        ]


# ── BOTH never reaches pipeline ─────────────────────────────────────────────


class TestBothNeverInPipeline:
    def test_trade_directions_are_concrete(self, client):
        d = _run(client, "BOTH", "BOTH")
        for t in d.get("trades", []):
            assert t["direction"] in ("LONG", "SHORT")
            assert t["level_source"] in ("ORB_HIGH", "ORB_LOW")

    def test_chart_event_directions_concrete(self, client):
        d = _run(client, "BOTH", "BOTH")
        for ev in d.get("chart_events", []):
            assert ev["direction"] in ("LONG", "SHORT")
            assert ev.get("level_source") in ("ORB_HIGH", "ORB_LOW")


# ── Metrics on combined dataset ──────────────────────────────────────────────


class TestCombinedMetrics:
    def test_both_both_has_metrics(self, client):
        d = _run(client, "BOTH", "BOTH")
        m = d["metrics"]
        assert "total_detected" in m
        assert "net_r" in m
        assert "equity_curve" in m

    def test_both_both_trades_gte_single(self, client):
        """BOTH/BOTH should find >= trades than any single combination."""
        d_single = _run(client, "LONG", "ORB_HIGH")
        d_both = _run(client, "BOTH", "BOTH")
        assert d_both["metrics"]["total_detected"] >= d_single["metrics"]["total_detected"]

    def test_level_source_both_adds_trades(self, client):
        """LONG/BOTH should find >= trades than LONG/ORB_HIGH alone."""
        d_single = _run(client, "LONG", "ORB_HIGH")
        d_both = _run(client, "LONG", "BOTH")
        assert d_both["metrics"]["total_detected"] >= d_single["metrics"]["total_detected"]


# ── Deduplication ────────────────────────────────────────────────────────────


class TestDeduplication:
    def test_no_duplicate_run_record_ids(self, client):
        d = _run(client, "BOTH", "BOTH")
        ids = [t["run_record_id"] for t in d.get("trades", []) if t.get("run_record_id")]
        assert len(ids) == len(set(ids)), "Duplicate run_record_ids found"


# ── Ordering ─────────────────────────────────────────────────────────────────


class TestOrdering:
    def test_trades_chronological(self, client):
        d = _run(client, "BOTH", "BOTH")
        dates = [t["date"] for t in d.get("trades", [])]
        assert dates == sorted(dates)

    def test_chart_events_match_trades(self, client):
        d = _run(client, "BOTH", "BOTH")
        assert len(d.get("chart_events", [])) == len(d.get("trades", []))


# ── Response fields ──────────────────────────────────────────────────────────


class TestResponseFields:
    def test_level_source_in_response(self, client):
        d = _run(client, "LONG", "BOTH")
        assert d["level_source"] == "BOTH"

    def test_direction_in_response(self, client):
        d = _run(client, "BOTH", "ORB_HIGH")
        assert d["direction"] == "BOTH"


# ── UI static tests ─────────────────────────────────────────────────────────


class TestUI:
    @pytest.fixture
    def html(self):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent.parent / "lab" / "index.html").read_text()

    def test_level_source_has_both_option(self, html):
        assert 'value="BOTH"' in html
        # Specifically in the level source select
        idx = html.index('id="pLevelSource"')
        block = html[idx:idx + 300]
        assert "BOTH" in block

    def test_level_source_in_payload(self, html):
        assert "pLevelSource" in html
        # The payload sends level_source
        assert "level_source:" in html
