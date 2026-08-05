"""Tests for /api/symbols with market_config and futures staging integration.

Tests _available_symbols() and _load_futures_staging() directly for speed.
"""

import json
from pathlib import Path

import pytest

from trading_lab.backtest_server import _available_symbols, _load_futures_staging

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Cache symbols once per session for speed
_SYMBOLS = None

def _get_symbols():
    global _SYMBOLS
    if _SYMBOLS is None:
        _SYMBOLS = _available_symbols()
    return _SYMBOLS


def _find(sym):
    return next((s for s in _get_symbols() if s["symbol"] == sym), None)


# ── 1. SPY metadata invariati ────────────────────────────────────────────────

class TestSPYMetadata:
    def test_spy_present(self):
        assert _find("SPY") is not None

    def test_spy_has_earliest(self):
        assert "earliest" in _find("SPY")

    def test_spy_has_latest(self):
        assert "latest" in _find("SPY")

    def test_spy_has_session_count(self):
        assert _find("SPY")["session_count"] > 0

    def test_spy_has_timeframes(self):
        assert "timeframes" in _find("SPY")

    def test_spy_asset_class(self):
        assert _find("SPY")["asset_class"] == "EQUITY"

    def test_spy_timezone(self):
        assert _find("SPY")["timezone"] == "America/New_York"

    def test_spy_tick_size(self):
        assert _find("SPY")["tick_size"] == 0.01

    def test_spy_point_value(self):
        assert _find("SPY")["point_value"] == 1.0

    def test_spy_not_futures(self):
        assert _find("SPY").get("instrument_type") != "CONTINUOUS_FUTURE"


# ── 2. NVDA metadata invariati ──────────────────────────────────────────────

class TestNVDAMetadata:
    def test_nvda_present(self):
        assert _find("NVDA") is not None

    def test_nvda_asset_class(self):
        assert _find("NVDA")["asset_class"] == "EQUITY"

    def test_nvda_timezone(self):
        assert _find("NVDA")["timezone"] == "America/New_York"

    def test_nvda_tick_size(self):
        assert _find("NVDA")["tick_size"] == 0.01

    def test_nvda_has_timeframes(self):
        assert "timeframes" in _find("NVDA")


# ── 3. MES appare con staging valido ────────────────────────────────────────

class TestMESStaging:
    def test_mes_present(self):
        staging = REPO_ROOT / "backend" / "runtime" / "futures_download_test" / "MES"
        csv_ok = (staging / "MES_contfut_1m_staging.csv").exists()
        meta_ok = (staging / "MES_contfut_staging_meta.json").exists()
        if csv_ok and meta_ok:
            assert _find("MES") is not None
        else:
            pytest.skip("MES staging not present")

    def test_mes_instrument_type(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["instrument_type"] == "CONTINUOUS_FUTURE"

    def test_mes_asset_class(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["asset_class"] == "FUTURE"


# ── 4. MNQ appare con staging valido ────────────────────────────────────────

class TestMNQStaging:
    def test_mnq_present(self):
        staging = REPO_ROOT / "backend" / "runtime" / "futures_download_test" / "MNQ"
        csv_ok = (staging / "MNQ_contfut_1m_staging.csv").exists()
        meta_ok = (staging / "MNQ_contfut_staging_meta.json").exists()
        if csv_ok and meta_ok:
            assert _find("MNQ") is not None
        else:
            pytest.skip("MNQ staging not present")

    def test_mnq_instrument_type(self):
        mnq = _find("MNQ")
        if mnq is None:
            pytest.skip("MNQ not available")
        assert mnq["instrument_type"] == "CONTINUOUS_FUTURE"


# ── 5. MES/MNQ non usano Yahoo ──────────────────────────────────────────────

class TestNoYahoo:
    def test_mes_source_not_yahoo(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        src = mes.get("source_file", "")
        assert "Yahoo" not in src
        assert "dati/1m/MES" not in src
        assert "futures_download_test" in src

    def test_mnq_source_not_yahoo(self):
        mnq = _find("MNQ")
        if mnq is None:
            pytest.skip("MNQ not available")
        src = mnq.get("source_file", "")
        assert "Yahoo" not in src
        assert "dati/1m/MNQ" not in src


# ── 6. conId corretto ───────────────────────────────────────────────────────

class TestConId:
    def test_mes_conid(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["conId"] == 793356217

    def test_mnq_conid(self):
        mnq = _find("MNQ")
        if mnq is None:
            pytest.skip("MNQ not available")
        assert mnq["conId"] == 793356225


# ── 7. timezone America/Chicago ─────────────────────────────────────────────

class TestFuturesTimezone:
    def test_mes_timezone(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["timezone"] == "America/Chicago"

    def test_mnq_timezone(self):
        mnq = _find("MNQ")
        if mnq is None:
            pytest.skip("MNQ not available")
        assert mnq["timezone"] == "America/Chicago"


# ── 8. tick_size 0.25 ───────────────────────────────────────────────────────

class TestFuturesTickSize:
    def test_mes_tick_size(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["tick_size"] == 0.25

    def test_mnq_tick_size(self):
        mnq = _find("MNQ")
        if mnq is None:
            pytest.skip("MNQ not available")
        assert mnq["tick_size"] == 0.25


# ── 9. tradable false ───────────────────────────────────────────────────────

class TestTradable:
    def test_mes_not_tradable(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["tradable"] is False

    def test_mes_historical_only(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["historical_only"] is True

    def test_mnq_not_tradable(self):
        mnq = _find("MNQ")
        if mnq is None:
            pytest.skip("MNQ not available")
        assert mnq["tradable"] is False


# ── 10. staging mancante → simbolo non disponibile ──────────────────────────

class TestMissingStaging:
    def test_missing_returns_none(self):
        assert _load_futures_staging("NONEXISTENT") is None

    def test_staging_mes_valid(self):
        result = _load_futures_staging("MES")
        staging = REPO_ROOT / "backend" / "runtime" / "futures_download_test" / "MES"
        if (staging / "MES_contfut_1m_staging.csv").exists():
            assert result is not None
            assert result["conId"] == 793356217
        else:
            assert result is None


# ── Equity without manifest still works ──────────────────────────────────────

class TestEquityWithoutManifest:
    def test_non_manifest_equity_present(self):
        """AAPL/TSLA not in market_manifest.json but have CSVs."""
        aapl = _find("AAPL")
        tsla = _find("TSLA")
        assert aapl is not None or tsla is not None

    def test_non_manifest_has_dates(self):
        aapl = _find("AAPL")
        if aapl is None:
            pytest.skip("AAPL not present")
        assert "earliest" in aapl
        assert "latest" in aapl


# ── Response format completeness ─────────────────────────────────────────────

class TestFuturesResponseFormat:
    def test_mes_has_all_fields(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        required = [
            "asset_class", "provider", "sec_type", "exchange", "currency",
            "timezone", "session_open", "session_close", "orb_open", "orb_close",
            "tick_size", "point_value", "price_scale",
            "instrument_type", "tradable", "historical_only",
            "conId", "source_file", "earliest", "latest",
        ]
        for field in required:
            assert field in mes, f"MES missing: {field}"

    def test_mes_session_open(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["session_open"] == "08:30"

    def test_mes_session_close(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["session_close"] == "15:00"

    def test_mes_orb_open(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["orb_open"] == "08:30"

    def test_mes_point_value(self):
        mes = _find("MES")
        if mes is None:
            pytest.skip("MES not available")
        assert mes["point_value"] == 5.0

    def test_mnq_point_value(self):
        mnq = _find("MNQ")
        if mnq is None:
            pytest.skip("MNQ not available")
        assert mnq["point_value"] == 2.0
