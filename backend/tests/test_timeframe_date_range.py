"""Tests for per-timeframe date ranges in /api/symbols and date-aware run.

Covers:
    1-3. SPY/QQQ 1m and 5m return correct earliest/latest
    4-5. Timeframe change → dates change (via available_timeframes)
    12-14. SPY/QQQ 1m/5m produce same results via test client
"""

import pytest

from trading_lab.timeframe_aggregation import available_timeframes


# ── 1-3: Per-timeframe date ranges ──────────────────────────────────────────


class TestPerTimeframeDateRanges:
    def test_spy_1m_has_own_range(self):
        tfs = available_timeframes("dati", "SPY")
        tf1m = next(t for t in tfs if t["value"] == "1m")
        assert tf1m["available"] is True
        assert tf1m["earliest_date"] is not None
        assert tf1m["latest_date"] is not None
        assert tf1m["session_count"] > 0

    def test_spy_5m_has_own_range(self):
        tfs = available_timeframes("dati", "SPY")
        tf5m = next(t for t in tfs if t["value"] == "5m")
        assert tf5m["available"] is True
        assert tf5m["earliest_date"] is not None
        assert tf5m["latest_date"] is not None
        assert tf5m["session_count"] > 0

    def test_spy_1m_5m_ranges_differ(self):
        tfs = available_timeframes("dati", "SPY")
        tf1m = next(t for t in tfs if t["value"] == "1m")
        tf5m = next(t for t in tfs if t["value"] == "5m")
        # The datasets have different date ranges
        assert tf1m["earliest_date"] != tf5m["earliest_date"] or \
               tf1m["latest_date"] != tf5m["latest_date"]

    def test_qqq_1m_has_own_range(self):
        tfs = available_timeframes("dati", "QQQ")
        tf1m = next(t for t in tfs if t["value"] == "1m")
        assert tf1m["available"] is True
        assert tf1m["earliest_date"] is not None

    def test_qqq_5m_has_own_range(self):
        tfs = available_timeframes("dati", "QQQ")
        tf5m = next(t for t in tfs if t["value"] == "5m")
        assert tf5m["available"] is True
        assert tf5m["earliest_date"] is not None

    def test_unavailable_timeframe_has_no_dates(self):
        """Unavailable timeframes should have None dates."""
        tfs = available_timeframes("dati", "SPY")
        unavail = [t for t in tfs if not t["available"]]
        # All entries now have these fields (even unavailable)
        for t in unavail:
            assert t["earliest_date"] is None
            assert t["latest_date"] is None
            assert t["session_count"] == 0


# ── 4-5: Date range per timeframe in /api/symbols ──────────────────────────


class TestSymbolsEndpointDateRanges:
    @pytest.fixture
    def client(self):
        from trading_lab.backtest_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_symbols_include_per_tf_dates(self, client):
        data = client.get("/api/symbols").get_json()
        spy = next(s for s in data if s["symbol"] == "SPY")
        tf1m = next(t for t in spy["timeframes"] if t["value"] == "1m")
        tf5m = next(t for t in spy["timeframes"] if t["value"] == "5m")
        assert "earliest_date" in tf1m
        assert "latest_date" in tf1m
        assert "earliest_date" in tf5m
        assert tf1m["earliest_date"] is not None
        assert tf5m["earliest_date"] is not None


# ── 12-14: 1m/5m produce results via test client ───────────────────────────


class TestRunWithCorrectDates:
    @pytest.fixture
    def client(self):
        from trading_lab.backtest_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def _run(self, client, sym, tf):
        tfs = available_timeframes("dati", sym)
        tf_info = next(t for t in tfs if t["value"] == tf)
        payload = {
            "symbols": [sym],
            "start_date": tf_info["earliest_date"],
            "end_date": tf_info["latest_date"],
            "timeframe": tf,
            "preset": {
                "direction": "BOTH", "level_source": "BOTH",
                "orb_duration_minutes": 5, "consecutive_orb_closes": 2,
                "entry_model": "CONFIRMATION_CLOSE",
                "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
                "rejection_wick_ratio_min": 0.47, "body_ratio_max": 0.40,
                "confirmation_wick_penetration_pct_min": 0,
            },
            "config": {"exit_target_r": "2", "tick_size": 0.01},
        }
        resp = client.post("/api/run", json=payload)
        return resp.get_json()

    def test_spy_1m_produces_trades(self, client):
        data = self._run(client, "SPY", "1m")
        assert "error" not in data
        assert data["total_sessions"] > 0
        assert data["metrics"]["total_detected"] >= 1

    def test_qqq_1m_produces_trades(self, client):
        data = self._run(client, "QQQ", "1m")
        assert "error" not in data
        assert data["total_sessions"] > 0
        assert data["metrics"]["total_detected"] >= 1

    def test_spy_5m_still_works(self, client):
        data = self._run(client, "SPY", "5m")
        assert "error" not in data
        assert data["total_sessions"] > 0
        assert data["metrics"]["total_detected"] >= 1

    def test_5m_dates_on_1m_produces_error(self, client):
        """Using 5m date range with 1m timeframe should find no sessions."""
        tfs = available_timeframes("dati", "SPY")
        tf5m = next(t for t in tfs if t["value"] == "5m")
        payload = {
            "symbols": ["SPY"],
            "start_date": tf5m["earliest_date"],
            "end_date": tf5m["latest_date"],
            "timeframe": "1m",
            "preset": {
                "direction": "BOTH", "level_source": "BOTH",
                "orb_duration_minutes": 5, "consecutive_orb_closes": 2,
                "entry_model": "CONFIRMATION_CLOSE",
                "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
            },
            "config": {"exit_target_r": "2", "tick_size": 0.01},
        }
        resp = client.post("/api/run", json=payload)
        data = resp.get_json()
        assert "error" in data
        assert "No sessions" in data["error"]
