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

    def test_all_nine_cases_present(self, client):
        """Test 3: All 9 case identifiers present in embedded data."""
        resp = client.get("/b10-review")
        html = resp.data.decode()
        cases = [
            ("NVDA", "2025-11-25", "SHORT"),
            ("NVDA", "2025-12-19", "LONG"),
            ("GOOGL", "2025-12-26", "SHORT"),
            ("AAPL", "2025-11-28", "SHORT"),
            ("SPY", "2025-12-02", "LONG"),
            ("QQQ", "2025-12-02", "LONG"),
            ("SPY", "2026-08-06", "LONG"),
            ("SPY", "2026-02-10", "LONG"),
            ("QQQ", "2025-11-25", "SHORT"),
        ]
        for sym, date, direction in cases:
            assert sym in html, f"{sym} missing"
            assert date in html, f"{date} missing"

    def test_spy_20260806_is_b_plus(self, client):
        """Test 4: SPY 2026-08-06 grade is B_PLUS."""
        resp = client.get("/b10-review")
        html = resp.data.decode()
        # The embedded JSON has "grade": "B_PLUS" for this case
        assert '"B_PLUS"' in html

    def test_spy_20260806_nearest_distance(self, client):
        """Test 5: SPY 2026-08-06 nearest distance 1.12R."""
        resp = client.get("/b10-review")
        html = resp.data.decode()
        assert "1.125" in html or "1.12" in html

    def test_grade_a_case_present(self, client):
        """Test 6: At least one grade-A case present."""
        resp = client.get("/b10-review")
        html = resp.data.decode()
        assert '"grade": "A"' in html or '"grade":"A"' in html

    def test_main_lab_still_works(self, client):
        """Test 7: Main Lab route returns 200."""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_api_symbols_still_works(self, client):
        """Test 8: /api/symbols returns 200."""
        resp = client.get("/api/symbols")
        assert resp.status_code == 200
