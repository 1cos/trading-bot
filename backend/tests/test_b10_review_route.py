"""Tests for B10.2 — B10 review route in Trading Lab server.

Verifies the /b10-review route serves the review page and that
existing Lab routes remain functional.
"""

import pytest


@pytest.fixture
def client():
    from trading_lab.backtest_server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestB10ReviewRoute:

    def test_b10_review_returns_200(self, client):
        """Test 1: GET /b10-review returns HTTP 200."""
        resp = client.get("/b10-review")
        assert resp.status_code == 200

    def test_b10_review_serves_correct_html(self, client):
        """Test 2: Response contains the B10 review page."""
        resp = client.get("/b10-review")
        html = resp.data.decode()
        assert "B10 Trade Space Grade Review" in html
        assert "lightweight-charts" in html

    def test_mandatory_cases_present(self, client):
        """Test 3: All 9 mandatory case identifiers present."""
        resp = client.get("/b10-review")
        html = resp.data.decode()
        mandatory = [
            ("NVDA", "2025-11-25"), ("NVDA", "2025-12-19"),
            ("GOOGL", "2025-12-26"), ("AAPL", "2025-11-28"),
            ("SPY", "2025-12-02"), ("QQQ", "2025-12-02"),
            ("SPY", "2026-08-06"), ("SPY", "2026-02-10"),
            ("QQQ", "2025-11-25"),
        ]
        for sym, date in mandatory:
            assert sym in html, f"{sym} missing"
            assert date in html, f"{date} missing"

    def test_at_least_20_cases(self, client):
        """Test 4: At least 20 cases embedded."""
        import json
        resp = client.get("/b10-review")
        html = resp.data.decode()
        start = html.index("const CASES = ") + len("const CASES = ")
        end = html.index(";", start)
        cases = json.loads(html[start:end])
        assert len(cases) >= 20

    def test_spy_20260806_is_b_plus(self, client):
        """Test 5: SPY 2026-08-06 grade is B_PLUS."""
        resp = client.get("/b10-review")
        html = resp.data.decode()
        assert '"B_PLUS"' in html

    def test_all_three_grades_present(self, client):
        """Test 6: A, B_PLUS, B all present."""
        import json
        resp = client.get("/b10-review")
        html = resp.data.decode()
        start = html.index("const CASES = ") + len("const CASES = ")
        end = html.index(";", start)
        cases = json.loads(html[start:end])
        grades = set(c["grade"] for c in cases)
        assert "A" in grades
        assert "B_PLUS" in grades
        assert "B" in grades

    def test_both_directions_present(self, client):
        """Test 7: LONG and SHORT both present."""
        import json
        resp = client.get("/b10-review")
        html = resp.data.decode()
        start = html.index("const CASES = ") + len("const CASES = ")
        end = html.index(";", start)
        cases = json.loads(html[start:end])
        dirs = set(c["direction"] for c in cases)
        assert "LONG" in dirs
        assert "SHORT" in dirs

    def test_both_outcomes_present(self, client):
        """Test 8: TARGET_HIT and STOPPED both present."""
        import json
        resp = client.get("/b10-review")
        html = resp.data.decode()
        start = html.index("const CASES = ") + len("const CASES = ")
        end = html.index(";", start)
        cases = json.loads(html[start:end])
        outs = set(c["outcome"] for c in cases)
        assert "TARGET_HIT" in outs
        assert "STOPPED" in outs

    def test_main_lab_still_works(self, client):
        """Test 9: Main Lab route returns 200."""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_api_symbols_still_works(self, client):
        """Test 10: /api/symbols returns 200."""
        resp = client.get("/api/symbols")
        assert resp.status_code == 200
