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
    def test_effective_rr_from_backend(self, html):
        """The effective RR should come from E.effective_target_r."""
        assert 'E.effective_target_r' in html

    def test_requested_rr_from_backend(self, html):
        """The requested RR should come from E.requested_target_r."""
        assert 'E.requested_target_r' in html

    def test_no_realized_r_as_effective(self, html):
        """realized_r must not be used as effective target label."""
        # The old pattern: effRRLabel from realized_r
        assert 'realized_r' not in html.split('effRRLabel')[0].split('\n')[-1] \
            if 'effRRLabel' in html else True


class TestNoClientSideTargetCalc:
    def test_no_math_round_for_target(self, html):
        """UI must not use Math.round to compute target."""
        # Math.round should not appear near target/offset/risk computation
        assert 'Math.round(risk' not in html

    def test_target_from_backend(self, html):
        """Target ticks should come from E.target_price_ticks."""
        assert 'E.target_price_ticks' in html

    def test_no_numerator_denominator_division(self, html):
        """UI must not divide numerator/denominator to compute target."""
        assert 'cfgEtr.numerator/cfgEtr.denominator' not in html


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

    def test_v2_trade_has_target_fields(self, client):
        """v2 trades must have requested/effective target R and target_price_ticks."""
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "config": {"exit_target_r": "2.5"},
        })
        data = resp.get_json()
        for t in data.get("trades", []):
            assert "target_price_ticks" in t
            assert "requested_target_r" in t
            assert "effective_target_r" in t
            assert "target_label" in t
            # requested is a Rational dict
            rtr = t["requested_target_r"]
            assert isinstance(rtr, dict)
            assert rtr["decimal"] == "2.5"
            # effective is a Rational dict
            etr = t["effective_target_r"]
            assert isinstance(etr, dict)
            assert "decimal" in etr
            # target_price_ticks is int
            assert isinstance(t["target_price_ticks"], int)

    def test_v2_chart_events_have_target(self, client):
        """v2 chart events must have target_price_ticks and target_label."""
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "config": {"exit_target_r": "2.5"},
        })
        data = resp.get_json()
        for ev in data.get("chart_events", []):
            assert "target_price_ticks" in ev
            assert "target_label" in ev
            assert ev["target_label"] == "2.5R"

    def test_losing_trade_has_positive_effective_r(self, client):
        """A stopped trade must still have a positive effective_target_r."""
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "config": {"exit_target_r": "2.5"},
        })
        data = resp.get_json()
        for t in data.get("trades", []):
            if t["outcome"] == "STOPPED":
                etr = t["effective_target_r"]
                assert isinstance(etr, dict)
                assert etr["numerator"] > 0
                # realized_r is negative but effective_target_r is positive
                assert t["realized_r"] < 0

# ── Dark theme tests ─────────────────────────────────────────────────────────


class TestDarkTheme:
    def test_dark_background(self, html):
        """Root CSS should use dark background."""
        assert "--bg:#0f0f0f" in html or "--bg: #0f0f0f" in html

    def test_light_text(self, html):
        """Root CSS should use light text color."""
        assert "--text:#f0f0f0" in html or "--text: #f0f0f0" in html

    def test_dark_surface(self, html):
        """Surface should be dark."""
        assert "--surface:#1a1a1a" in html or "--surface: #1a1a1a" in html

    def test_no_white_chart_background(self, html):
        """Chart backgrounds should not be white."""
        assert 'color:"#fff"' not in html

    def test_chart_has_dark_bg(self, html):
        """Chart should use dark background color."""
        assert 'color:"#1a1a1a"' in html


class TestFunctionalIdsPreserved:
    def test_critical_ids(self, html):
        """All functional HTML IDs must still be present."""
        for id_name in [
            "pExitTargetR", "pSymbol", "pDirection", "pLevelSource",
            "pOrbDuration", "pConsecOrbCloses", "pEntryBuffer",
            "pStopBuffer", "pTickSize", "pStartDate", "pEndDate",
            "pTimeframe", "btnRun", "runStatus", "metricsGrid",
            "equityChartWrap", "tradeTableWrap", "tradeChart",
            "chartOrbZone", "chartExplain", "chartTitle",
        ]:
            assert f'id="{id_name}"' in html, f"Missing ID: {id_name}"

    def test_rr_decimal_input_preserved(self, html):
        """The RR decimal input must still exist."""
        assert 'type="number"' in html
        assert 'id="pExitTargetR"' in html
        assert 'step="0.1"' in html

    def test_no_old_strategy_scripts(self, html):
        """Old dashboard strategy functions must not be present."""
        for fn in ["strategyPDHPDL", "strategyORB", "strategyOB",
                    "strategyCombined", "checkWick", "checkEng"]:
            assert fn not in html, f"Old strategy function found: {fn}"
