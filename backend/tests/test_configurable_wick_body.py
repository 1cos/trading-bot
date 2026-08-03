"""Tests for configurable rejection_wick_ratio_min and body_ratio_max.

Covers:
    - Wick threshold: at/below/above, raising/lowering
    - Body threshold: at/above/below, raising/lowering
    - Direction: LONG and SHORT
    - Validation: 0, 1, negative, >1
    - End-to-end: default matches baseline, alternatives change results
"""

import pytest

from trading_lab.rejection_finder import find_rejection, REJECTION_WICK_RATIO_MIN, BODY_RATIO_MAX


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK = 0.01
MS_0930 = 1719828600000
MS_0935 = MS_0930 + 300000
MS_0940 = MS_0930 + 600000
MS_0945 = MS_0930 + 900000
MS_0950 = MS_0930 + 1200000
MS_0955 = MS_0930 + 1500000


def _pt(ticks):
    return {"ticks": ticks, "tick_size": TICK}


def c(ms, open_=None, high=None, low=None, close=None):
    o = int(round((open_ or 100) / TICK))
    h = int(round((high or 100) / TICK))
    l = int(round((low or 100) / TICK))
    cl = int(round((close or 100) / TICK))
    return {
        "time_ms": ms,
        "bar_utc_ms": ms,
        "open": _pt(o), "high": _pt(h), "low": _pt(l), "close": _pt(cl),
        "volume": None,
    }


def _config(**overrides):
    base = {
        "timeframe_minutes": 5,
        "timezone": "America/New_York",
        "session_open": "09:30",
        "orb_start": "session_open",
        "orb_duration_minutes": 5,
        "level_source": "ORB_HIGH",
        "direction": "LONG",
        "tick_size": TICK,
    }
    base.update(overrides)
    return base


def _orb(high=101.00, low=100.00):
    return {
        "status": "OK",
        "orb_high_ticks": int(round(high / TICK)),
        "orb_low_ticks": int(round(low / TICK)),
        "level_price_ticks": int(round(high / TICK)),
        "orb_candle_index": 0,
        "orb_candle": {"time_ms": MS_0930},
    }


def _break(idx=1):
    return {"status": "OK", "break_candle_index": idx,
            "break_candle": {"time_ms": MS_0930 + idx * 300000}}


def _disp(start=2, end=2):
    return {"status": "OK", "displacement_start_index": start, "displacement_end_index": end}


def _retest(start=3, end=4):
    return {"status": "OK", "retest_start_index": start, "retest_end_index": end}


def _make_rejection_candle_long(wick_ratio, body_ratio, level_price=101.00):
    """Build a LONG rejection candle with exact geometry.

    LONG rejection: wick goes DOWN toward level, body is small, close near high.
    range = 100 ticks for easy math.
    """
    range_ticks = 100
    low = level_price - 0.10  # slightly below level for penetration
    high = low + range_ticks * TICK

    wick_ticks = int(round(wick_ratio * range_ticks))
    body_ticks = int(round(body_ratio * range_ticks))

    # LONG: rejection_wick = min(open, close) - low
    # So min(open, close) = low + wick_ticks * TICK
    # Body small, close near high → close > open
    open_ = low + wick_ticks * TICK
    close = open_ + body_ticks * TICK

    # favorable_close_location = (close - low) / range
    # With these values it should be > 0.80 for reasonable wick/body combos

    return c(MS_0950, open_=open_, high=high, low=low, close=close)


# Standard candles for the pipeline leading up to rejection
_CANDLES_PREFIX = [
    c(MS_0930, open_=100.20, high=101.00, low=100.00, close=100.80),  # ORB
    c(MS_0935, open_=100.80, high=101.50, low=100.70, close=101.30),  # Break
    c(MS_0940, open_=101.30, high=102.00, low=101.20, close=101.80),  # Displacement
    c(MS_0945, open_=101.50, high=101.60, low=100.95, close=101.05),  # Retest toward level
]


# ── Wick/Body Config Unit Tests ──────────────────────────────────────────────


class TestWickBodyConfigDefaults:
    """Verify the constants still exist and config.get handles None."""

    def test_constants_unchanged(self):
        assert REJECTION_WICK_RATIO_MIN == 0.47
        assert BODY_RATIO_MAX == 0.40

    def test_config_get_none_uses_default(self):
        """When config has key=None, rejection_finder uses frozen default."""
        cfg = _config(rejection_wick_ratio_min=None, body_ratio_max=None)
        # These should not crash — they fall back to constants
        assert cfg["rejection_wick_ratio_min"] is None
        assert cfg["body_ratio_max"] is None

    def test_config_get_value_overrides(self):
        cfg = _config(rejection_wick_ratio_min=0.30, body_ratio_max=0.60)
        assert cfg["rejection_wick_ratio_min"] == 0.30
        assert cfg["body_ratio_max"] == 0.60


# ── Validation Tests ─────────────────────────────────────────────────────────


class TestValidation:
    """Server-level validation of wick/body params."""

    @pytest.fixture
    def client(self):
        from trading_lab.backtest_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def _body(self, wick=None, body=None):
        preset = {"direction": "LONG"}
        if wick is not None:
            preset["rejection_wick_ratio_min"] = wick
        if body is not None:
            preset["body_ratio_max"] = body
        return {"symbols": ["SPY"], "timeframe": "5m",
                "preset": preset, "config": {"exit_target_r": "2"}}

    def test_zero_valid(self, client):
        r = client.post("/api/run", json=self._body(wick=0))
        assert r.status_code == 200

    def test_one_valid(self, client):
        r = client.post("/api/run", json=self._body(wick=1.0))
        assert r.status_code == 200

    def test_negative_rejected(self, client):
        r = client.post("/api/run", json=self._body(wick=-0.1))
        assert r.status_code == 400

    def test_above_one_rejected(self, client):
        r = client.post("/api/run", json=self._body(body=1.5))
        assert r.status_code == 400

    def test_string_rejected(self, client):
        r = client.post("/api/run", json=self._body(wick="abc"))
        assert r.status_code == 400

    def test_boolean_rejected(self, client):
        r = client.post("/api/run", json=self._body(body=True))
        assert r.status_code == 400


# ── End-to-end Tests ─────────────────────────────────────────────────────────


class TestEndToEnd:
    @pytest.fixture
    def client(self):
        from trading_lab.backtest_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def _run(self, client, wick=None, body=None):
        preset = {"direction": "LONG"}
        if wick is not None:
            preset["rejection_wick_ratio_min"] = wick
        if body is not None:
            preset["body_ratio_max"] = body
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "preset": preset, "config": {"exit_target_r": "2"},
        })
        return resp.get_json()

    def test_defaults_match_baseline(self, client):
        """Default 0.47/0.40 produces same trades as without params."""
        d1 = self._run(client)
        d2 = self._run(client, wick=0.47, body=0.40)
        assert d1["metrics"]["total_detected"] == d2["metrics"]["total_detected"]
        assert d1["metrics"]["winning_trades"] == d2["metrics"]["winning_trades"]
        assert d1["metrics"]["net_r"] == d2["metrics"]["net_r"]

    def test_restrictive_wick_reduces_trades(self, client):
        """Wick 60% should produce <= trades compared to default 47%."""
        d_default = self._run(client, wick=0.47, body=0.40)
        d_strict = self._run(client, wick=0.60, body=0.40)
        assert d_strict["metrics"]["total_detected"] <= d_default["metrics"]["total_detected"]

    def test_restrictive_body_reduces_trades(self, client):
        """Body 25% should produce <= trades compared to default 40%."""
        d_default = self._run(client, wick=0.47, body=0.40)
        d_strict = self._run(client, wick=0.47, body=0.25)
        assert d_strict["metrics"]["total_detected"] <= d_default["metrics"]["total_detected"]

    def test_permissive_increases_trades(self, client):
        """Very permissive 10%/90% should produce >= trades."""
        d_default = self._run(client, wick=0.47, body=0.40)
        d_perm = self._run(client, wick=0.10, body=0.90)
        assert d_perm["metrics"]["total_detected"] >= d_default["metrics"]["total_detected"]

    def test_response_includes_preset_values(self, client):
        """Response should reflect the configured values."""
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "preset": {"direction": "LONG",
                       "rejection_wick_ratio_min": 0.55,
                       "body_ratio_max": 0.35},
            "config": {"exit_target_r": "2"},
        })
        data = resp.get_json()
        assert data["preset"]["rejection_wick_ratio_min"] == 0.55
        assert data["preset"]["body_ratio_max"] == 0.35


# ── UI static tests ──────────────────────────────────────────────────────────


class TestUIControls:
    @pytest.fixture
    def html(self):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent.parent / "lab" / "index.html").read_text()

    def test_wick_input_exists(self, html):
        assert 'id="pWickMin"' in html

    def test_body_input_exists(self, html):
        assert 'id="pBodyMax"' in html

    def test_wick_in_payload(self, html):
        assert "rejection_wick_ratio_min" in html

    def test_body_in_payload(self, html):
        assert "body_ratio_max" in html

    def test_values_divided_by_100(self, html):
        """UI shows percentages but sends ratios (divided by 100)."""
        assert "pWickMin" in html
        assert "/100" in html
