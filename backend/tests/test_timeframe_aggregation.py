"""Tests for timeframe aggregation module."""

import pytest
from trading_lab.timeframe_aggregation import aggregate_candles, available_timeframes


def _c(minute, o=100.0, h=101.0, l=99.0, c=100.5, v=100):
    """Create a 1-minute candle at a specific minute of the day (using a fixed date)."""
    # 2026-07-01 at the given minute (ET → UTC: +4 hours)
    from datetime import datetime
    hour = minute // 60
    mins = minute % 60
    dt = datetime(2026, 7, 1, hour + 4, mins)  # UTC
    return {"time_ms": int(dt.timestamp() * 1000),
            "open": o, "high": h, "low": l, "close": c, "volume": v}


class TestAggregateCandles:
    def test_1m_passthrough(self):
        candles = [_c(570), _c(571), _c(572)]
        result = aggregate_candles(candles, 1)
        assert len(result) == 3

    def test_2m_aggregation(self):
        candles = [
            _c(570, o=100, h=102, l=99, c=101, v=50),
            _c(571, o=101, h=103, l=100, c=102, v=60),
            _c(572, o=102, h=104, l=98, c=103, v=70),
            _c(573, o=103, h=105, l=97, c=104, v=80),
        ]
        result = aggregate_candles(candles, 2)
        assert len(result) == 2
        # First 2m bar
        assert result[0]["open"] == 100      # first bar's open
        assert result[0]["high"] == 103      # max high
        assert result[0]["low"] == 99        # min low
        assert result[0]["close"] == 102     # last bar's close
        assert result[0]["volume"] == 110    # sum volumes

    def test_5m_aggregation(self):
        candles = [_c(570 + i, h=100+i, l=90-i) for i in range(10)]
        result = aggregate_candles(candles, 5)
        assert len(result) == 2

    def test_3m_aggregation(self):
        candles = [_c(570 + i) for i in range(9)]
        result = aggregate_candles(candles, 3)
        assert len(result) == 3

    def test_preserves_ohlc_rules(self):
        candles = [
            _c(570, o=100, h=110, l=95, c=105, v=10),
            _c(571, o=105, h=115, l=90, c=108, v=20),
            _c(572, o=108, h=112, l=92, c=100, v=30),
        ]
        result = aggregate_candles(candles, 3)
        assert len(result) == 1
        bar = result[0]
        assert bar["open"] == 100    # first open
        assert bar["high"] == 115    # max high
        assert bar["low"] == 90      # min low
        assert bar["close"] == 100   # last close
        assert bar["volume"] == 60   # sum

    def test_empty_input(self):
        assert aggregate_candles([], 5) == []

    def test_invalid_timeframe(self):
        with pytest.raises(ValueError):
            aggregate_candles([_c(570)], 7)


class TestAvailableTimeframes:
    def test_5m_only(self, tmp_path):
        (tmp_path / "SPY_5m.csv").touch()
        tfs = available_timeframes(tmp_path, "SPY")
        assert any(t["value"] == "5m" and t["available"] for t in tfs)
        assert any(t["value"] == "1m" and not t["available"] for t in tfs)

    def test_1m_enables_all(self, tmp_path):
        (tmp_path / "SPY_1m.csv").touch()
        tfs = available_timeframes(tmp_path, "SPY")
        assert all(t["available"] for t in tfs if t["value"] in ("1m", "2m", "3m"))
