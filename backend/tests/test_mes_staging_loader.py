"""Tests for MES staging integration in backtest_server."""

from pathlib import Path

import pytest

from trading_lab.backtest_server import (
    _load_futures_candles,
    _load_futures_staging,
    _FUTURES_STAGING_ENABLED,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STAGING_MES = REPO_ROOT / "backend" / "runtime" / "futures_download_test" / "MES"


def _has_mes_staging():
    return (
        (STAGING_MES / "MES_contfut_1m_staging.csv").exists()
        and (STAGING_MES / "MES_contfut_staging_meta.json").exists()
    )


@pytest.fixture
def mes_1m():
    if not _has_mes_staging():
        pytest.skip("MES staging not present")
    data = _load_futures_candles("MES", 1)
    assert data is not None
    return data


@pytest.fixture
def mes_5m():
    if not _has_mes_staging():
        pytest.skip("MES staging not present")
    data = _load_futures_candles("MES", 5)
    assert data is not None
    return data


# ── 1. MES risolve il file staging corretto ─────────────────────────────────

class TestMESResolvesStaging:
    def test_source_file_is_staging(self, mes_1m):
        assert "futures_download_test" in mes_1m["source_file"]
        assert "MES_contfut_1m_staging.csv" in mes_1m["source_file"]

    def test_source_is_not_dati(self, mes_1m):
        assert "dati/1m/" not in mes_1m["source_file"]
        assert "dati/MES" not in mes_1m["source_file"]


# ── 2. non usa i vecchi file Yahoo ──────────────────────────────────────────

class TestNoYahoo:
    def test_provider_is_ibkr(self, mes_1m):
        assert mes_1m["provider"] == "IBKR"

    def test_source_not_yahoo(self, mes_1m):
        assert "Yahoo" not in mes_1m["source_file"]


# ── 3. timezone = America/Chicago ────────────────────────────────────────────

class TestTimezone:
    def test_timezone(self, mes_1m):
        assert mes_1m["timezone"] == "America/Chicago"


# ── 4. tick_size = 0.25 ─────────────────────────────────────────────────────

class TestTickSize:
    def test_tick_size(self, mes_1m):
        assert mes_1m["tick_size"] == 0.25


# ── 5. sessione = 08:30–15:00 ───────────────────────────────────────────────

class TestSessionWindow:
    def test_session_open(self, mes_1m):
        assert mes_1m["session_open"] == "08:30"

    def test_session_close(self, mes_1m):
        assert mes_1m["session_close"] == "15:00"


# ── 6. ORB = 08:30–08:34 ────────────────────────────────────────────────────

class TestORBWindow:
    def test_orb_open(self, mes_1m):
        assert mes_1m["orb_open"] == "08:30"

    def test_orb_close(self, mes_1m):
        assert mes_1m["orb_close"] == "08:34"


# ── 7. MES 1m produce 4 sessioni ────────────────────────────────────────────

class TestMES1m:
    def test_session_count(self, mes_1m):
        # 5 days of data but only ~4 trading sessions with RTH bars
        assert mes_1m["session_count"] >= 4

    def test_has_candles(self, mes_1m):
        for date in mes_1m["dates"]:
            assert len(mes_1m["candles_by_date"][date]) > 0

    def test_source_timeframe(self, mes_1m):
        assert mes_1m["source_timeframe"] == "1m"

    def test_aggregation_none(self, mes_1m):
        assert mes_1m["aggregation_method"] == "none"


# ── 8. MES 5m produce 4 sessioni ────────────────────────────────────────────

class TestMES5m:
    def test_session_count(self, mes_5m):
        assert mes_5m["session_count"] >= 4

    def test_aggregation_method(self, mes_5m):
        assert mes_5m["aggregation_method"] == "1m_to_5m"

    def test_fewer_bars_than_1m(self, mes_1m, mes_5m):
        total_1m = sum(len(v) for v in mes_1m["candles_by_date"].values())
        total_5m = sum(len(v) for v in mes_5m["candles_by_date"].values())
        assert total_5m < total_1m


# ── 9. Globex escluso ───────────────────────────────────────────────────────

class TestGlobexExcluded:
    def test_no_overnight_bars(self, mes_1m):
        """All bars should be within 08:30–14:59 CT."""
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        ct = ZoneInfo("America/Chicago")
        for date, candles in mes_1m["candles_by_date"].items():
            for c in candles:
                dt = datetime.fromtimestamp(c["time_ms"] / 1000, tz=timezone.utc).astimezone(ct)
                minute = dt.hour * 60 + dt.minute
                assert 510 <= minute < 900, (
                    f"Bar at {dt.strftime('%H:%M')} CT outside 08:30–14:59"
                )


# ── 10. provenance corretta ─────────────────────────────────────────────────

class TestProvenance:
    def test_instrument_type(self, mes_1m):
        assert mes_1m["instrument_type"] == "CONTINUOUS_FUTURE"

    def test_provider(self, mes_1m):
        assert mes_1m["provider"] == "IBKR"

    def test_selected_timeframe_1m(self, mes_1m):
        assert mes_1m["selected_timeframe"] == "1m"

    def test_selected_timeframe_5m(self, mes_5m):
        assert mes_5m["selected_timeframe"] == "5m"


# ── 11. MNQ resta invariato/disabilitato ────────────────────────────────────

class TestMNQDisabled:
    def test_mnq_not_in_enabled(self):
        assert "MNQ" not in _FUTURES_STAGING_ENABLED

    def test_mnq_returns_none(self):
        result = _load_futures_candles("MNQ", 1)
        assert result is None

    def test_equity_not_affected(self):
        """SPY should not go through futures loader."""
        result = _load_futures_candles("SPY", 1)
        assert result is None


# ── Edge cases ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_nonexistent_symbol(self):
        result = _load_futures_candles("FAKE", 1)
        assert result is None

    def test_mes_dates_are_weekdays(self, mes_1m):
        """All session dates should be weekdays."""
        from datetime import datetime as dt
        for date in mes_1m["dates"]:
            d = dt.strptime(date, "%Y-%m-%d")
            assert d.weekday() < 5, f"{date} is a weekend"
