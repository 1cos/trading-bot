"""Tests for backtest_server v2 — Rational R/R parsing, metrics, JSON.

Covers:
    - parse_exit_target_r: valid/invalid inputs
    - _compute_metrics with Rational realized_r
    - /api/run v1 and v2 dispatch
    - JSON serialization of Rational/Decimal
    - v1/v2 economic compatibility
"""

import json
from decimal import Decimal

import pytest

from trading_lab.contracts.primitives import Rational
from trading_lab.backtest_server import (
    parse_exit_target_r,
    _compute_metrics,
    _rational_to_number,
    _RationalEncoder,
    app,
)
from trading_lab.strategy_runner import Outcome


# ── parse_exit_target_r ──────────────────────────────────────────────────────


class TestParseExitTargetR:
    # v1 integers
    def test_int_2(self):
        val, is_v2 = parse_exit_target_r(2)
        assert val == 2 and not is_v2

    def test_int_3(self):
        val, is_v2 = parse_exit_target_r(3)
        assert val == 3 and not is_v2

    def test_int_4(self):
        val, is_v2 = parse_exit_target_r(4)
        assert val == 4 and not is_v2

    # v2 strings
    def test_string_2(self):
        val, is_v2 = parse_exit_target_r("2")
        assert isinstance(val, Rational) and is_v2
        assert val.as_decimal() == Decimal("2")

    def test_string_2_1(self):
        val, is_v2 = parse_exit_target_r("2.1")
        assert isinstance(val, Rational) and is_v2
        assert val.as_decimal() == Decimal("2.1")

    def test_string_2_25(self):
        val, is_v2 = parse_exit_target_r("2.25")
        assert isinstance(val, Rational) and is_v2
        assert val.as_decimal() == Decimal("2.25")

    def test_string_2_5(self):
        val, is_v2 = parse_exit_target_r("2.5")
        assert isinstance(val, Rational) and is_v2
        assert val.as_decimal() == Decimal("2.5")

    def test_string_3_75(self):
        val, is_v2 = parse_exit_target_r("3.75")
        assert isinstance(val, Rational) and is_v2
        assert val.as_decimal() == Decimal("3.75")

    # Invalid
    def test_empty_string(self):
        with pytest.raises(ValueError, match="empty"):
            parse_exit_target_r("")

    def test_zero_string(self):
        with pytest.raises(ValueError, match="positive"):
            parse_exit_target_r("0")

    def test_negative_string(self):
        with pytest.raises(ValueError, match="positive"):
            parse_exit_target_r("-2.5")

    def test_nan_string(self):
        with pytest.raises(ValueError, match="finite"):
            parse_exit_target_r("NaN")

    def test_infinity_string(self):
        with pytest.raises(ValueError, match="finite"):
            parse_exit_target_r("Infinity")

    def test_bool_rejected(self):
        with pytest.raises(ValueError, match="bool"):
            parse_exit_target_r(True)

    def test_list_rejected(self):
        with pytest.raises(ValueError, match="list"):
            parse_exit_target_r([2])

    def test_dict_rejected(self):
        with pytest.raises(ValueError, match="dict"):
            parse_exit_target_r({"r": 2})

    def test_float_rejected(self):
        with pytest.raises(ValueError, match="got"):
            parse_exit_target_r(2.5)

    def test_int_5_rejected(self):
        with pytest.raises(ValueError, match="got"):
            parse_exit_target_r(5)

    def test_garbage_string(self):
        with pytest.raises(ValueError, match="not a valid"):
            parse_exit_target_r("abc")

    def test_no_float_in_result(self):
        """Parser must not use float internally."""
        val, _ = parse_exit_target_r("2.1")
        assert isinstance(val, Rational)
        assert isinstance(val.numerator, int)
        assert isinstance(val.denominator, int)


# ── _rational_to_number ─────────────────────────────────────────────────────


class TestRationalToNumber:
    def test_rational(self):
        d = _rational_to_number(Rational(5, 2))
        assert d == Decimal("2.5")

    def test_int(self):
        d = _rational_to_number(2)
        assert d == Decimal("2")

    def test_negative_rational(self):
        d = _rational_to_number(Rational(-1, 1))
        assert d == Decimal("-1")


# ── _compute_metrics with Rational ──────────────────────────────────────────


def _result(outcome, realized_r):
    return {
        "detection_status": "VALID",
        "outcome": outcome,
        "realized_r": realized_r,
    }


class TestComputeMetricsRational:
    def test_metrics_with_rational_values(self):
        results = [
            _result(Outcome.TARGET_HIT, Rational(5, 2)),    # +2.5R
            _result(Outcome.STOPPED, Rational(-1, 1)),       # -1R
            _result(Outcome.TARGET_HIT, Rational(133, 53)),  # ~2.509R
        ]
        m = _compute_metrics(results)
        assert m["total_detected"] == 3
        assert m["winning_trades"] == 2
        assert m["losing_trades"] == 1

        # net_r should be positive: 2.5 - 1 + 2.509... ≈ 4.009
        net_r_dec = m["net_r"]
        assert isinstance(net_r_dec, Decimal)
        assert net_r_dec > Decimal("4")

        # equity_curve exists and has 3 entries
        assert len(m["equity_curve"]) == 3

    def test_metrics_with_int_values(self):
        """v1 int realized_r still works."""
        results = [
            _result(Outcome.TARGET_HIT, 2),
            _result(Outcome.STOPPED, -1),
        ]
        m = _compute_metrics(results)
        assert m["total_detected"] == 2
        net_r_dec = m["net_r"]
        assert isinstance(net_r_dec, Decimal)
        assert net_r_dec == Decimal("1")

    def test_empty_results(self):
        m = _compute_metrics([])
        assert m["total_detected"] == 0
        assert m["net_r"] == 0


# ── JSON serialization ──────────────────────────────────────────────────────


class TestJSONSerialization:
    def test_rational_encoder(self):
        data = {"r": Rational(5, 2), "d": Decimal("3.14")}
        result = json.dumps(data, cls=_RationalEncoder)
        parsed = json.loads(result)
        assert parsed["r"] == 2.5
        assert abs(parsed["d"] - 3.14) < 0.001

    def test_metrics_serializable(self):
        """Full metrics dict can be serialized to JSON."""
        results = [
            _result(Outcome.TARGET_HIT, Rational(5, 2)),
            _result(Outcome.STOPPED, Rational(-1, 1)),
        ]
        m = _compute_metrics(results)
        # Should not raise
        result = json.dumps(m, cls=_RationalEncoder)
        parsed = json.loads(result)
        assert isinstance(parsed["net_r"], (int, float))
        assert isinstance(parsed["win_rate"], (int, float))

    def test_flask_app_serializes_rational(self):
        """Flask app with custom JSON provider can serialize Rational."""
        with app.app_context():
            from flask import json as flask_json
            data = {"value": Rational(9, 4)}
            result = flask_json.dumps(data)
            parsed = json.loads(result)
            assert parsed["value"] == 2.25


# ── /api/run endpoint ───────────────────────────────────────────────────────


class TestApiRunEndpoint:
    @pytest.fixture
    def client(self):
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def _run_body(self, exit_target_r=2, symbol="SPY"):
        return {
            "symbols": [symbol],
            "timeframe": "5m",
            "config": {"exit_target_r": exit_target_r},
        }

    def test_v1_endpoint(self, client):
        resp = client.post("/api/run",
                           json=self._run_body(exit_target_r=2))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "metrics" in data
        assert "error" not in data

    def test_v2_endpoint_string(self, client):
        resp = client.post("/api/run",
                           json=self._run_body(exit_target_r="2.5"))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "metrics" in data
        assert "error" not in data

    def test_v2_endpoint_2_25(self, client):
        resp = client.post("/api/run",
                           json=self._run_body(exit_target_r="2.25"))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" not in data

    def test_invalid_rr_rejected(self, client):
        resp = client.post("/api/run",
                           json=self._run_body(exit_target_r="abc"))
        assert resp.status_code == 400

    def test_zero_rr_rejected(self, client):
        resp = client.post("/api/run",
                           json=self._run_body(exit_target_r="0"))
        assert resp.status_code == 400

    def test_v1_v2_same_trade_count(self, client):
        """v1 at 2R and v2 at '2' produce same number of trades."""
        r1 = client.post("/api/run",
                         json=self._run_body(exit_target_r=2))
        r2 = client.post("/api/run",
                         json=self._run_body(exit_target_r="2"))
        d1 = r1.get_json()
        d2 = r2.get_json()
        assert d1["metrics"]["total_detected"] == d2["metrics"]["total_detected"]

    def test_v1_v2_same_win_count(self, client):
        """v1 at 2R and v2 at '2' produce same number of wins."""
        r1 = client.post("/api/run",
                         json=self._run_body(exit_target_r=2))
        r2 = client.post("/api/run",
                         json=self._run_body(exit_target_r="2"))
        d1 = r1.get_json()
        d2 = r2.get_json()
        assert d1["metrics"]["winning_trades"] == d2["metrics"]["winning_trades"]
        assert d1["metrics"]["losing_trades"] == d2["metrics"]["losing_trades"]
