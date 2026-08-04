"""Tests for IBKR canonical source policy, dedup, and RTH session counting.

Covers:
  1.  Identical duplicates removed
  2.  Discordant duplicates produce error
  3.  SPY IBKR → 172 RTH sessions
  4.  QQQ IBKR → 172 RTH sessions
  5.  Dates without RTH bars are not counted
  6.  Timeframe 1m uses IBKR file
  7.  Timeframe 5m uses IBKR file (aggregated)
  8.  Legacy SPY_5m.csv is not used for IBKR equity
  9.  Aggregation OHLCV correct on sample window
  10. Aggregation does not cross session boundary
  11. Dedup is idempotent
  12. MES/MNQ excluded from IBKR equity set
  13. Raw files not modified during tests
  14. Regression baseline preserved (run separately)
"""

import csv
import hashlib
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trading_lab.timeframe_aggregation import (
    IBKR_EQUITY_SYMBOLS,
    DuplicateConflictError,
    aggregate_candles,
    dedup_candles,
    filter_rth_sessions,
    is_ibkr_equity,
    load_candles_for_timeframe,
    parse_csv_candles,
    split_into_sessions,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATI_DIR = REPO_ROOT / "dati"


def _file_hash(path):
    """SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 1. Identical duplicates removed ──────────────────────────────────────────

class TestDedup:
    def test_identical_duplicates_removed(self):
        candles = [
            {"time_ms": 1000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100},
            {"time_ms": 1000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100},
            {"time_ms": 2000, "open": 1.1, "high": 2.1, "low": 0.6, "close": 1.6, "volume": 200},
        ]
        result, removed = dedup_candles(candles)
        assert len(result) == 2
        assert removed == 1

    # ── 2. Discordant duplicates produce error ───────────────────────────────

    def test_conflicting_duplicates_raise_error(self):
        candles = [
            {"time_ms": 1000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100},
            {"time_ms": 1000, "open": 1.1, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100},
        ]
        with pytest.raises(DuplicateConflictError):
            dedup_candles(candles)

    # ── 11. Dedup idempotent ─────────────────────────────────────────────────

    def test_dedup_idempotent(self):
        candles = [
            {"time_ms": 1000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100},
            {"time_ms": 1000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100},
            {"time_ms": 2000, "open": 1.1, "high": 2.1, "low": 0.6, "close": 1.6, "volume": 200},
        ]
        first, rem1 = dedup_candles(candles)
        second, rem2 = dedup_candles(first)
        assert first == second
        assert rem2 == 0


# ── 3/4. SPY and QQQ session counts ─────────────────────────────────────────

@pytest.mark.skipif(
    not (DATI_DIR / "1m" / "SPY_1m.csv").exists(),
    reason="IBKR 1m data not available",
)
class TestIBKRSessionCounts:
    def test_spy_172_rth_sessions(self):
        result = load_candles_for_timeframe(DATI_DIR, "SPY", 1)
        assert result["session_count"] == 172, f"SPY got {result['session_count']}"

    def test_qqq_172_rth_sessions(self):
        result = load_candles_for_timeframe(DATI_DIR, "QQQ", 1)
        assert result["session_count"] == 172, f"QQQ got {result['session_count']}"

    def test_spy_5m_172_rth_sessions(self):
        result = load_candles_for_timeframe(DATI_DIR, "SPY", 5)
        assert result["session_count"] == 172, f"SPY 5m got {result['session_count']}"

    def test_qqq_5m_172_rth_sessions(self):
        result = load_candles_for_timeframe(DATI_DIR, "QQQ", 5)
        assert result["session_count"] == 172, f"QQQ 5m got {result['session_count']}"


# ── 5. Dates without RTH bars not counted ────────────────────────────────────

class TestRTHFilter:
    def test_non_rth_dates_excluded(self):
        et = ZoneInfo("America/New_York")
        # Create candles: one date with only pre-market, one with RTH
        pre_market_dt = datetime(2026, 1, 1, 7, 0, tzinfo=et)  # 07:00 ET, no RTH
        rth_dt = datetime(2026, 1, 2, 10, 0, tzinfo=et)  # 10:00 ET, RTH

        candles_by_date = {
            "2026-01-01": [{"time_ms": int(pre_market_dt.timestamp() * 1000),
                            "open": 1, "high": 2, "low": 0.5, "close": 1.5}],
            "2026-01-02": [{"time_ms": int(rth_dt.timestamp() * 1000),
                            "open": 1, "high": 2, "low": 0.5, "close": 1.5}],
        }
        filtered = filter_rth_sessions(candles_by_date)
        assert "2026-01-01" not in filtered
        assert "2026-01-02" in filtered


# ── 6/7/8. Source selection ──────────────────────────────────────────────────

@pytest.mark.skipif(
    not (DATI_DIR / "1m" / "SPY_1m.csv").exists(),
    reason="IBKR 1m data not available",
)
class TestSourceSelection:
    def test_1m_uses_ibkr_file(self):
        result = load_candles_for_timeframe(DATI_DIR, "SPY", 1)
        assert result["source_timeframe"] == "1m"
        assert result["provider"] == "IBKR"
        assert "1m" in result["source_file"]
        assert result["aggregation_method"] == "none"

    def test_5m_uses_ibkr_aggregated(self):
        result = load_candles_for_timeframe(DATI_DIR, "SPY", 5)
        assert result["source_timeframe"] == "1m"
        assert result["provider"] == "IBKR"
        assert "1m" in result["source_file"]
        assert result["aggregation_method"] == "1m_to_5m"

    def test_legacy_5m_not_used_for_ibkr(self):
        """Even though dati/SPY_5m.csv exists, IBKR equity must use 1m."""
        legacy = DATI_DIR / "SPY_5m.csv"
        if not legacy.exists():
            pytest.skip("Legacy SPY_5m.csv not present")
        result = load_candles_for_timeframe(DATI_DIR, "SPY", 5)
        assert result["source_file"] != str(legacy)
        # Must come from 1m data (172 sessions), not legacy (60 sessions)
        assert result["session_count"] == 172

    def test_duplicate_rows_removed_reported(self):
        result = load_candles_for_timeframe(DATI_DIR, "SPY", 1)
        assert "duplicate_rows_removed" in result
        assert result["duplicate_rows_removed"] > 0  # SPY has 9 dup dates


# ── 9. Aggregation OHLCV correct ────────────────────────────────────────────

@pytest.mark.skipif(
    not (DATI_DIR / "1m" / "SPY_1m.csv").exists(),
    reason="IBKR 1m data not available",
)
class TestAggregationCorrectness:
    def test_5m_ohlcv_from_five_1m_bars(self):
        """Verify a 5m bar matches O/H/L/C/V of its constituent 1m bars."""
        result_1m = load_candles_for_timeframe(DATI_DIR, "SPY", 1)
        result_5m = load_candles_for_timeframe(DATI_DIR, "SPY", 5)

        # Pick a complete session
        test_date = result_1m["dates"][5]  # skip first few, take a solid one
        candles_1m = result_1m["candles_by_date"][test_date]
        candles_5m = result_5m["candles_by_date"][test_date]

        et = ZoneInfo("America/New_York")
        # Find RTH bars at 09:30-09:34 in the 1m data
        rth_1m = []
        for c in candles_1m:
            dt = datetime.fromtimestamp(c["time_ms"] / 1000, tz=et)
            if dt.hour == 9 and 30 <= dt.minute <= 34:
                rth_1m.append(c)

        assert len(rth_1m) == 5, f"Expected 5 bars at 09:30-09:34, got {len(rth_1m)}"

        # Find the corresponding 5m bar (09:30)
        bar_5m = None
        for c in candles_5m:
            dt = datetime.fromtimestamp(c["time_ms"] / 1000, tz=et)
            if dt.hour == 9 and dt.minute == 30:
                bar_5m = c
                break

        assert bar_5m is not None, "No 5m bar at 09:30"

        # Verify aggregation rules
        assert bar_5m["open"] == rth_1m[0]["open"], "open != first bar's open"
        assert bar_5m["close"] == rth_1m[4]["close"], "close != last bar's close"
        assert bar_5m["high"] == max(c["high"] for c in rth_1m), "high != max(highs)"
        assert bar_5m["low"] == min(c["low"] for c in rth_1m), "low != min(lows)"
        assert bar_5m["volume"] == sum(c.get("volume", 0) for c in rth_1m), "volume != sum"


# ── 10. Aggregation does not cross session boundary ─────────────────────────

class TestAggregationBoundary:
    def test_no_cross_session_aggregation(self):
        """Bars from different sessions must not be aggregated together."""
        et = ZoneInfo("America/New_York")
        # Create 2 bars from different dates
        d1 = datetime(2026, 1, 5, 15, 55, tzinfo=et)
        d2 = datetime(2026, 1, 6, 9, 30, tzinfo=et)
        candles = [
            {"time_ms": int(d1.timestamp() * 1000), "open": 100, "high": 101,
             "low": 99, "close": 100.5, "volume": 1000},
            {"time_ms": int(d2.timestamp() * 1000), "open": 101, "high": 102,
             "low": 100, "close": 101.5, "volume": 2000},
        ]
        sessions = split_into_sessions(candles)
        assert len(sessions) == 2

        # Aggregate each session separately (as the real code does)
        all_5m = []
        for date_key in sorted(sessions.keys()):
            agg = aggregate_candles(sessions[date_key], 5)
            all_5m.extend(agg)

        # Should produce 2 separate bars, not 1 merged bar
        assert len(all_5m) == 2


# ── 12. MES/MNQ excluded ────────────────────────────────────────────────────

class TestFuturesExcluded:
    def test_mes_not_ibkr_equity(self):
        assert not is_ibkr_equity("MES")

    def test_mnq_not_ibkr_equity(self):
        assert not is_ibkr_equity("MNQ")

    def test_ibkr_set_has_exactly_10(self):
        assert len(IBKR_EQUITY_SYMBOLS) == 10

    @pytest.mark.skipif(
        not (DATI_DIR / "1m" / "MES_1m.csv").exists(),
        reason="MES 1m data not available",
    )
    def test_mes_provider_not_ibkr(self):
        result = load_candles_for_timeframe(DATI_DIR, "MES", 1)
        assert result.get("provider") != "IBKR"


# ── 13. Raw files not modified ───────────────────────────────────────────────

@pytest.mark.skipif(
    not (DATI_DIR / "1m" / "SPY_1m.csv").exists(),
    reason="IBKR 1m data not available",
)
class TestRawFilesPreserved:
    def test_loading_does_not_modify_raw_file(self):
        path = DATI_DIR / "1m" / "SPY_1m.csv"
        hash_before = _file_hash(path)

        # Load at both timeframes
        load_candles_for_timeframe(DATI_DIR, "SPY", 1)
        load_candles_for_timeframe(DATI_DIR, "SPY", 5)

        hash_after = _file_hash(path)
        assert hash_before == hash_after, "Raw IBKR file was modified!"
