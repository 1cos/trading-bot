"""Tests for parse_csv_candles — futures ISO format (time_utc / time_ct)."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading_lab.timeframe_aggregation import parse_csv_candles


CT = ZoneInfo("America/Chicago")
ET = ZoneInfo("America/New_York")


def _write_csv(tmp_path: Path, header: str, rows: list[str]) -> Path:
    p = tmp_path / "test.csv"
    p.write_text(header + "\n" + "\n".join(rows) + "\n")
    return p


# ── 1. header time_utc riconosciuto ─────────────────────────────────────────

class TestTimeUtcRecognized:
    def test_parses_time_utc_header(self, tmp_path):
        p = _write_csv(tmp_path, "time_utc,open,high,low,close,volume", [
            "2026-07-29T17:00:00-05:00,5600.25,5601.00,5599.75,5600.50,150",
        ])
        candles = parse_csv_candles(p)
        assert len(candles) == 1

    def test_parses_time_ct_header(self, tmp_path):
        p = _write_csv(tmp_path, "time_ct,open,high,low,close,volume", [
            "2026-07-29T17:00:00-05:00,5600.25,5601.00,5599.75,5600.50,150",
        ])
        candles = parse_csv_candles(p)
        assert len(candles) == 1


# ── 2. timestamp ISO con offset convertito correttamente ────────────────────

class TestTimestampConversion:
    def test_ct_offset_minus_5(self, tmp_path):
        """2026-07-29T17:00:00-05:00 = 2026-07-29T22:00:00 UTC"""
        p = _write_csv(tmp_path, "time_utc,open,high,low,close,volume", [
            "2026-07-29T17:00:00-05:00,100,101,99,100.5,50",
        ])
        candles = parse_csv_candles(p)
        expected_utc = datetime(2026, 7, 29, 22, 0, 0, tzinfo=timezone.utc)
        expected_ms = int(expected_utc.timestamp() * 1000)
        assert candles[0]["time_ms"] == expected_ms

    def test_utc_offset_zero(self, tmp_path):
        """2026-07-29T22:00:00+00:00 = same instant as above"""
        p = _write_csv(tmp_path, "time_utc,open,high,low,close,volume", [
            "2026-07-29T22:00:00+00:00,100,101,99,100.5,50",
        ])
        candles = parse_csv_candles(p)
        expected_utc = datetime(2026, 7, 29, 22, 0, 0, tzinfo=timezone.utc)
        expected_ms = int(expected_utc.timestamp() * 1000)
        assert candles[0]["time_ms"] == expected_ms

    def test_ct_and_utc_same_instant(self, tmp_path):
        """Both representations of the same instant produce the same time_ms."""
        p1 = _write_csv(tmp_path, "time_utc,open,high,low,close,volume", [
            "2026-07-29T17:00:00-05:00,100,101,99,100.5,50",
        ])
        candles1 = parse_csv_candles(p1)
        p2 = tmp_path / "test2.csv"
        p2.write_text("time_utc,open,high,low,close,volume\n"
                       "2026-07-29T22:00:00+00:00,100,101,99,100.5,50\n")
        candles2 = parse_csv_candles(p2)
        assert candles1[0]["time_ms"] == candles2[0]["time_ms"]


# ── 3. OHLCV corretti ──────────────────────────────────────────────────────

class TestOHLCV:
    def test_ohlcv_values(self, tmp_path):
        p = _write_csv(tmp_path, "time_utc,open,high,low,close,volume", [
            "2026-07-29T08:30:00-05:00,5600.25,5601.00,5599.75,5600.50,150",
        ])
        c = parse_csv_candles(p)[0]
        assert c["open"] == 5600.25
        assert c["high"] == 5601.00
        assert c["low"] == 5599.75
        assert c["close"] == 5600.50
        assert c["volume"] == 150

    def test_volume_missing_defaults_zero(self, tmp_path):
        p = _write_csv(tmp_path, "time_utc,open,high,low,close", [
            "2026-07-29T08:30:00-05:00,100,101,99,100.5",
        ])
        c = parse_csv_candles(p)[0]
        assert c["volume"] == 0


# ── 4. più righe restano ordinate ────────────────────────────────────────────

class TestOrdering:
    def test_multiple_rows_preserve_order(self, tmp_path):
        p = _write_csv(tmp_path, "time_utc,open,high,low,close,volume", [
            "2026-07-29T08:30:00-05:00,100,101,99,100.5,10",
            "2026-07-29T08:31:00-05:00,100.5,102,100,101,20",
            "2026-07-29T08:32:00-05:00,101,103,100.5,102,30",
        ])
        candles = parse_csv_candles(p)
        assert len(candles) == 3
        assert candles[0]["time_ms"] < candles[1]["time_ms"]
        assert candles[1]["time_ms"] < candles[2]["time_ms"]

    def test_time_ms_increments_by_60s(self, tmp_path):
        p = _write_csv(tmp_path, "time_utc,open,high,low,close,volume", [
            "2026-07-29T08:30:00-05:00,100,101,99,100.5,10",
            "2026-07-29T08:31:00-05:00,100.5,102,100,101,20",
        ])
        candles = parse_csv_candles(p)
        diff_ms = candles[1]["time_ms"] - candles[0]["time_ms"]
        assert diff_ms == 60_000


# ── 5. timestamp invalido rifiutato ─────────────────────────────────────────

class TestInvalidTimestamp:
    def test_garbage_timestamp_raises(self, tmp_path):
        p = _write_csv(tmp_path, "time_utc,open,high,low,close,volume", [
            "not-a-date,100,101,99,100.5,10",
        ])
        with pytest.raises(ValueError, match="Invalid timestamp"):
            parse_csv_candles(p)

    def test_naive_timestamp_raises(self, tmp_path):
        """Futures CSV must have explicit offset — naive timestamps are rejected."""
        p = _write_csv(tmp_path, "time_utc,open,high,low,close,volume", [
            "2026-07-29 17:00:00,100,101,99,100.5,10",
        ])
        with pytest.raises(ValueError, match="missing offset"):
            parse_csv_candles(p)


# ── 6. formato time_et invariato ─────────────────────────────────────────────

class TestTimeEtUnchanged:
    def test_time_et_still_works(self, tmp_path):
        p = _write_csv(tmp_path, "time_et,open,high,low,close,volume", [
            "2026-07-29 09:30:00,100,101,99,100.5,10",
            "2026-07-29 09:31:00,100.5,102,100,101,20",
        ])
        candles = parse_csv_candles(p)
        assert len(candles) == 2
        assert candles[0]["open"] == 100
        assert candles[1]["open"] == 100.5

    def test_time_et_interprets_as_eastern(self, tmp_path):
        """time_et timestamps are interpreted as America/New_York."""
        p = _write_csv(tmp_path, "time_et,open,high,low,close,volume", [
            "2026-07-29 09:30:00,100,101,99,100.5,10",
        ])
        candles = parse_csv_candles(p)
        # 09:30 EDT (UTC-4 in July) = 13:30 UTC
        expected = datetime(2026, 7, 29, 9, 30, tzinfo=ET)
        assert candles[0]["time_ms"] == int(expected.timestamp() * 1000)


# ── 7. formato TradingView invariato ─────────────────────────────────────────

class TestTradingViewUnchanged:
    def test_tradingview_format(self, tmp_path):
        """TradingView: 3 header rows, columns Price,Close,High,Low,Open,Volume."""
        content = (
            "Price,Close,High,Low,Open,Volume\n"
            "AAPL\n"
            "\n"
            "2026-07-29 09:30:00-04:00,150.5,151.0,149.5,150.0,1000\n"
        )
        p = tmp_path / "tv.csv"
        p.write_text(content)
        candles = parse_csv_candles(p)
        assert len(candles) == 1
        # TradingView: col order is Price,Close,High,Low,Open,Volume
        # So col[1]=Close, col[2]=High, col[3]=Low, col[4]=Open
        assert candles[0]["close"] == 150.5
        assert candles[0]["high"] == 151.0
        assert candles[0]["low"] == 149.5
        assert candles[0]["open"] == 150.0
        assert candles[0]["volume"] == 1000


# ── Real staging file (if available) ────────────────────────────────────────

class TestRealStagingFile:
    REPO_ROOT = Path(__file__).resolve().parent.parent.parent

    def test_parse_real_mes_staging(self):
        p = self.REPO_ROOT / "backend" / "runtime" / "futures_download_test" / "MES" / "MES_contfut_1m_staging.csv"
        if not p.exists():
            pytest.skip("MES staging not present")
        candles = parse_csv_candles(p)
        assert len(candles) > 5000
        # First bar should be 2026-07-29 17:00 CT = 22:00 UTC
        first = candles[0]
        dt = datetime.fromtimestamp(first["time_ms"] / 1000, tz=timezone.utc)
        assert dt.hour == 22
        assert dt.minute == 0
        # OHLCV should be reasonable futures prices
        assert first["open"] > 1000
        assert first["volume"] > 0

    def test_parse_real_mnq_staging(self):
        p = self.REPO_ROOT / "backend" / "runtime" / "futures_download_test" / "MNQ" / "MNQ_contfut_1m_staging.csv"
        if not p.exists():
            pytest.skip("MNQ staging not present")
        candles = parse_csv_candles(p)
        assert len(candles) > 5000
