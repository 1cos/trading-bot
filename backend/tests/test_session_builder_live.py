"""Tests for LiveSessionBuilder — live 1m session accumulator.

Verifies:
  1. Empty builder state.
  2. First bar creates a session.
  3. Multiple bars accumulate correctly.
  4. Chronological order is maintained.
  5. Duplicate timestamps are deterministic.
  6. New session/day resets correctly.
  7. Out-of-order bars are rejected.
  8. Session is structurally consumable by build_session_context.
"""

import pytest

from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.session_context import build_session_context


# ── Candle factory ───────────────────────────────────────────────────────────

# 2026-08-11 EDT (UTC-4): 09:30 ET = 13:30 UTC = epoch ms
# datetime(2026, 8, 11, 13, 30, 0, tzinfo=timezone.utc).timestamp() * 1000
MS_0930 = 1786451400000  # 09:30
MS_0931 = MS_0930 + 60_000  # 09:31
MS_0932 = MS_0930 + 120_000  # 09:32
MS_0933 = MS_0930 + 180_000  # 09:33
MS_0934 = MS_0930 + 240_000  # 09:34

# Next day: 2026-08-12 09:30
MS_NEXT_DAY_0930 = MS_0930 + 86_400_000


def _bar(time_ms, open_=100.0, high=101.0, low=99.0, close=100.5, volume=1000):
    """Create a synthetic candle dict."""
    return {
        "time_ms": time_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


# ── Engine config for build_session_context compatibility ────────────────────

ENGINE_CONFIG = {
    "timeframe_minutes": 1,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "tick_size": 0.01,
}


# ── Test 1: Empty builder ────────────────────────────────────────────────────

class TestEmptyBuilder:
    def test_no_bars_returns_none(self):
        b = LiveSessionBuilder("SPY")
        assert b.current_session() is None

    def test_bar_count_zero(self):
        b = LiveSessionBuilder("SPY")
        assert b.bar_count == 0

    def test_current_date_none(self):
        b = LiveSessionBuilder("SPY")
        assert b.current_date is None


# ── Test 2: First bar ───────────────────────────────────────────────────────

class TestFirstBar:
    def test_creates_session(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930))
        sess = b.current_session()
        assert sess is not None
        assert sess["symbol"] == "SPY"
        assert sess["timeframe"] == "1m"
        assert sess["market_timezone"] == "America/New_York"
        assert len(sess["candles"]) == 1

    def test_session_date(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930))
        assert b.current_date == "2026-08-11"

    def test_session_timestamps(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930))
        sess = b.current_session()
        assert sess["session_open_utc_ms"] == MS_0930
        assert sess["session_close_utc_ms"] == MS_0930


# ── Test 3: Multiple bars accumulate ─────────────────────────────────────────

class TestMultipleBars:
    def test_accumulation(self):
        b = LiveSessionBuilder("SPY")
        for ms in [MS_0930, MS_0931, MS_0932, MS_0933, MS_0934]:
            b.add_bar(_bar(ms, open_=100.0 + (ms - MS_0930) / 60_000))
        assert b.bar_count == 5
        sess = b.current_session()
        assert len(sess["candles"]) == 5
        assert sess["session_open_utc_ms"] == MS_0930
        assert sess["session_close_utc_ms"] == MS_0934

    def test_bar_count_increments(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930))
        assert b.bar_count == 1
        b.add_bar(_bar(MS_0931))
        assert b.bar_count == 2


# ── Test 4: Chronological order ──────────────────────────────────────────────

class TestChronological:
    def test_bars_in_order(self):
        b = LiveSessionBuilder("SPY")
        for ms in [MS_0930, MS_0931, MS_0932]:
            b.add_bar(_bar(ms))
        sess = b.current_session()
        timestamps = [c["time_ms"] for c in sess["candles"]]
        assert timestamps == [MS_0930, MS_0931, MS_0932]


# ── Test 5: Duplicate timestamp ──────────────────────────────────────────────

class TestDuplicate:
    def test_identical_bar_ignored(self):
        b = LiveSessionBuilder("SPY")
        bar = _bar(MS_0930)
        b.add_bar(bar)
        b.add_bar(bar)  # same object, identical
        assert b.bar_count == 1

    def test_identical_values_ignored(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5))
        b.add_bar(_bar(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5))
        assert b.bar_count == 1

    def test_changed_values_replaced(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930, close=100.5))
        b.add_bar(_bar(MS_0930, close=101.0))  # updated close
        assert b.bar_count == 1
        sess = b.current_session()
        assert sess["candles"][0]["close"] == 101.0

    def test_replace_preserves_position(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930, close=100.0))
        b.add_bar(_bar(MS_0931, close=101.0))
        b.add_bar(_bar(MS_0930, close=99.5))  # replace first bar
        sess = b.current_session()
        assert sess["candles"][0]["close"] == 99.5
        assert sess["candles"][1]["close"] == 101.0
        assert b.bar_count == 2


# ── Test 6: New session rollover ─────────────────────────────────────────────

class TestSessionRollover:
    def test_new_day_resets(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930))
        b.add_bar(_bar(MS_0931))
        assert b.bar_count == 2

        # Next trading day
        b.add_bar(_bar(MS_NEXT_DAY_0930))
        assert b.bar_count == 1
        assert b.current_date == "2026-08-12"

    def test_old_session_gone_after_rollover(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930))
        b.add_bar(_bar(MS_NEXT_DAY_0930))
        sess = b.current_session()
        assert len(sess["candles"]) == 1
        assert sess["candles"][0]["time_ms"] == MS_NEXT_DAY_0930

    def test_explicit_reset(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930))
        b.reset()
        assert b.current_session() is None
        assert b.bar_count == 0


# ── Test 7: Out-of-order rejection ───────────────────────────────────────────

class TestOutOfOrder:
    def test_older_bar_rejected(self):
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0931))
        with pytest.raises(ValueError, match="Out-of-order"):
            b.add_bar(_bar(MS_0930))

    def test_equal_timestamp_not_rejected(self):
        """Same timestamp is a duplicate update, not out-of-order."""
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930, close=100.0))
        b.add_bar(_bar(MS_0930, close=100.5))  # should not raise
        assert b.bar_count == 1


# ── Test 8: Structural compatibility with build_session_context ──────────────

class TestPipelineCompatibility:
    def test_session_consumable_by_build_session_context(self):
        """Prove the session dict produced by LiveSessionBuilder
        is accepted by the real build_session_context function."""
        b = LiveSessionBuilder("SPY")
        for i in range(10):
            ms = MS_0930 + i * 60_000
            b.add_bar(_bar(ms, open_=100.0 + i * 0.1))

        sess = b.current_session()
        assert sess is not None

        # Feed candles into the real pipeline consumer
        sc = build_session_context(sess["candles"], ENGINE_CONFIG)
        assert sc["status"] == "OK"
        assert sc["date"] == "2026-08-11"
        assert sc["candle_count"] == 10
        assert sc["timezone"] == "America/New_York"

    def test_session_has_all_required_fields(self):
        """Verify all fields read by _process_one_session exist."""
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930))
        sess = b.current_session()

        required = [
            "symbol", "date", "market_timezone",
            "session_open_utc_ms", "session_close_utc_ms",
            "timeframe", "candles",
        ]
        for field in required:
            assert field in sess, f"missing field: {field}"

    def test_defensive_copy(self):
        """current_session() returns a copy; mutating it doesn't
        affect internal state."""
        b = LiveSessionBuilder("SPY")
        b.add_bar(_bar(MS_0930))
        sess1 = b.current_session()
        sess1["candles"].append(_bar(MS_0931))  # mutate the copy
        sess2 = b.current_session()
        assert len(sess2["candles"]) == 1  # internal unchanged


# ── Test: Input validation ───────────────────────────────────────────────────

class TestValidation:
    def test_not_a_dict(self):
        b = LiveSessionBuilder("SPY")
        with pytest.raises(TypeError):
            b.add_bar("not a bar")

    def test_missing_time_ms(self):
        b = LiveSessionBuilder("SPY")
        with pytest.raises(ValueError, match="time_ms"):
            b.add_bar({"open": 1, "high": 2, "low": 0.5, "close": 1.5})

    def test_missing_ohlc(self):
        b = LiveSessionBuilder("SPY")
        with pytest.raises(ValueError, match="open"):
            b.add_bar({"time_ms": MS_0930, "high": 2, "low": 0.5, "close": 1.5})
