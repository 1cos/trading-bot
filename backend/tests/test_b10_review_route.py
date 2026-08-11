"""Tests for B10 review route — annotation workspace.

Verifies the /b10-review route serves the annotation workspace and that
existing Lab routes remain functional.
"""

import json
import pytest


@pytest.fixture
def client():
    from trading_lab.backtest_server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _get_cases(client):
    """Extract embedded cases from the review page."""
    resp = client.get("/b10-review")
    html = resp.data.decode()
    start = html.index("const CASES = ") + len("const CASES = ")
    end = html.index(";", start)
    return json.loads(html[start:end]), html


class TestB10ReviewRoute:

    def test_b10_review_returns_200(self, client):
        resp = client.get("/b10-review")
        assert resp.status_code == 200

    def test_annotation_mode_default_on(self, client):
        """Annotation mode is ON by default."""
        resp = client.get("/b10-review")
        html = resp.data.decode()
        assert "annotationMode = true" in html

    def test_future_hidden_by_default(self, client):
        """Future candles hidden by default."""
        resp = client.get("/b10-review")
        html = resp.data.decode()
        assert "showFuture = false" in html

    def test_bot_walls_hidden_by_default(self, client):
        """Bot walls hidden by default."""
        resp = client.get("/b10-review")
        html = resp.data.decode()
        assert "showBotWalls = false" in html

    def test_spy_20260318_present(self, client):
        """SPY 2026-03-18 SHORT exists."""
        cases, _ = _get_cases(client)
        assert any(c["symbol"] == "SPY" and c["date"] == "2026-03-18" for c in cases)

    def test_spy_20260806_present(self, client):
        """SPY 2026-08-06 LONG exists."""
        cases, _ = _get_cases(client)
        spy = [c for c in cases if c["symbol"] == "SPY" and c["date"] == "2026-08-06"]
        assert len(spy) == 1
        assert spy[0]["grade"] == "B_PLUS"

    def test_grade_a_present(self, client):
        """At least one grade-A case."""
        cases, _ = _get_cases(client)
        assert any(c["grade"] == "A" for c in cases)

    def test_manual_zone_features_present(self, client):
        """Annotation workspace features exist in HTML."""
        _, html = _get_cases(client)
        assert "ADD ZONE" in html
        assert "EXPORT" in html
        assert "manual_zones" in html

    def test_main_lab_still_works(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_api_symbols_still_works(self, client):
        resp = client.get("/api/symbols")
        assert resp.status_code == 200
