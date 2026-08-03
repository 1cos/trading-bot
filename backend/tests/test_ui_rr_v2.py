"""Static validation tests for lab/index.html v2 Risk/Reward UI.

Verifies the HTML source doesn't contain v1-only patterns and
meets the v2 requirements without running a browser.
"""

import re
from pathlib import Path

import pytest

LAB_HTML = Path(__file__).resolve().parent.parent.parent / "lab" / "index.html"


@pytest.fixture
def html():
    return LAB_HTML.read_text()


class TestRRControlNotSelect:
    def test_no_select_for_exit_target(self, html):
        """The RR control must be an input, not a select."""
        # Should not have <select> with id pExitTargetR
        assert '<select' not in html.split('pExitTargetR')[0].split('\n')[-1]

    def test_input_type_number(self, html):
        """RR control must be type=number."""
        match = re.search(r'id="pExitTargetR"', html)
        assert match
        # Find the containing tag
        start = html.rfind('<', 0, match.start())
        tag = html[start:match.end() + 50]
        assert 'type="number"' in tag

    def test_step_0_1(self, html):
        """RR input must have step=0.1."""
        match = re.search(r'id="pExitTargetR"', html)
        start = html.rfind('<', 0, match.start())
        tag = html[start:match.end() + 80]
        assert 'step="0.1"' in tag


class TestPayloadSendsString:
    def test_no_parseint_for_exit_target(self, html):
        """exit_target_r must not use parseInt."""
        assert "parseInt" not in html.split("exit_target_r")[1].split("\n")[0]

    def test_sends_value_as_string(self, html):
        """exit_target_r payload should use .value (string), not parseInt."""
        # Find the config payload line
        lines = html.split('\n')
        for line in lines:
            if 'exit_target_r' in line and 'pExitTargetR' in line:
                assert 'parseInt' not in line
                assert '.value' in line
                break
        else:
            pytest.fail("exit_target_r payload line not found")


class TestTargetLabelNotHardcoded:
    def test_no_hardcoded_target_2r(self, html):
        """Chart must not have hardcoded 'Target 2R' or 'TP 2R'."""
        # Allow 'Target 2R' only in comments, not in actual createPriceLine
        lines = html.split('\n')
        for line in lines:
            if 'createPriceLine' in line and 'TP 2R' in line:
                pytest.fail(f"Found hardcoded 'TP 2R' in price line: {line.strip()}")

    def test_no_hardcoded_target_2r_in_cards(self, html):
        """Explain cards must not have hardcoded 'Target 2R'."""
        lines = html.split('\n')
        for line in lines:
            if '"Target 2R"' in line and 'ecards' in line:
                pytest.fail(f"Found hardcoded 'Target 2R' in explain cards: {line.strip()}")

    def test_dynamic_rr_label_in_chart(self, html):
        """Chart target line should reference rrLabel."""
        assert 'rrLabel' in html


class TestRRRequestedAndEffective:
    def test_effective_rr_shown(self, html):
        """The explain cards should show effective RR."""
        assert 'effRRLabel' in html

    def test_requested_rr_from_config(self, html):
        """The requested RR should come from config.exit_target_r.decimal."""
        assert 'cfgEtr.decimal' in html


class TestFrontendValidation:
    def test_empty_check(self, html):
        """UI should check for empty RR value."""
        assert 'cannot be empty' in html or 'empty' in html.lower()

    def test_zero_check(self, html):
        """UI should reject zero RR."""
        assert 'greater than zero' in html

    def test_nan_check(self, html):
        """UI should reject non-numeric RR."""
        assert 'isNaN' in html or 'valid number' in html


class TestErrorHandling:
    def test_error_from_backend(self, html):
        """UI should handle backend error response."""
        assert 'data.error' in html


class TestEndpointIntegration:
    """Test the actual server endpoint with v2 RR values."""

    @pytest.fixture
    def client(self):
        from trading_lab.backtest_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_v2_string_2_5(self, client):
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "config": {"exit_target_r": "2.5"},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" not in data
        etr = data["config"]["exit_target_r"]
        assert etr["decimal"] == "2.5"

    def test_v2_string_2_25(self, client):
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "config": {"exit_target_r": "2.25"},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" not in data

    def test_v2_string_3_1(self, client):
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "config": {"exit_target_r": "3.1"},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" not in data

    def test_v2_metrics_present(self, client):
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "config": {"exit_target_r": "2.5"},
        })
        data = resp.get_json()
        m = data["metrics"]
        assert "net_r" in m
        assert "win_rate" in m
        assert "equity_curve" in m

    def test_v2_chart_events_present(self, client):
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "config": {"exit_target_r": "2.5"},
        })
        data = resp.get_json()
        assert "chart_events" in data

    def test_invalid_rr_4xx(self, client):
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "config": {"exit_target_r": "abc"},
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data
