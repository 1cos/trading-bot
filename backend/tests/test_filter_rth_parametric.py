"""Tests for filter_rth_sessions — parametric timezone/session window."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from trading_lab.timeframe_aggregation import filter_rth_sessions


ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")


def _candle_at(dt_aware) -> dict:
    """Build a minimal candle dict from an aware datetime."""
    return {
        "time_ms": int(dt_aware.timestamp() * 1000),
        "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1,
    }


def _session(date_str: str, candles: list[dict]) -> dict[str, list[dict]]:
    return {date_str: candles}


# ── 1. equity: include 09:30 ────────────────────────────────────────────────

class TestEquityIncludes:
    def test_includes_0930(self):
        dt = datetime(2026, 7, 29, 9, 30, tzinfo=ET)
        result = filter_rth_sessions(_session("2026-07-29", [_candle_at(dt)]))
        assert "2026-07-29" in result

    # ── 2. equity: include 15:59 ────────────────────────────────────────
    def test_includes_1559(self):
        dt = datetime(2026, 7, 29, 15, 59, tzinfo=ET)
        result = filter_rth_sessions(_session("2026-07-29", [_candle_at(dt)]))
        assert "2026-07-29" in result


# ── 3. equity: esclude 09:29 ────────────────────────────────────────────────

class TestEquityExcludes:
    def test_excludes_0929(self):
        dt = datetime(2026, 7, 29, 9, 29, tzinfo=ET)
        result = filter_rth_sessions(_session("2026-07-29", [_candle_at(dt)]))
        assert "2026-07-29" not in result

    # ── 4. equity: esclude 16:00 ────────────────────────────────────────
    def test_excludes_1600(self):
        dt = datetime(2026, 7, 29, 16, 0, tzinfo=ET)
        result = filter_rth_sessions(_session("2026-07-29", [_candle_at(dt)]))
        assert "2026-07-29" not in result


# ── 5. futures: include 08:30 CT ────────────────────────────────────────────

class TestFuturesIncludes:
    FUT_KWARGS = dict(
        timezone_str="America/Chicago",
        session_open="08:30",
        session_close="15:00",
    )

    def test_includes_0830_ct(self):
        dt = datetime(2026, 7, 29, 8, 30, tzinfo=CT)
        result = filter_rth_sessions(
            _session("2026-07-29", [_candle_at(dt)]),
            **self.FUT_KWARGS,
        )
        assert "2026-07-29" in result

    # ── 6. futures: include 14:59 CT ────────────────────────────────────
    def test_includes_1459_ct(self):
        dt = datetime(2026, 7, 29, 14, 59, tzinfo=CT)
        result = filter_rth_sessions(
            _session("2026-07-29", [_candle_at(dt)]),
            **self.FUT_KWARGS,
        )
        assert "2026-07-29" in result


# ── 7. futures: esclude 08:29 CT ────────────────────────────────────────────

class TestFuturesExcludes:
    FUT_KWARGS = dict(
        timezone_str="America/Chicago",
        session_open="08:30",
        session_close="15:00",
    )

    def test_excludes_0829_ct(self):
        dt = datetime(2026, 7, 29, 8, 29, tzinfo=CT)
        result = filter_rth_sessions(
            _session("2026-07-29", [_candle_at(dt)]),
            **self.FUT_KWARGS,
        )
        assert "2026-07-29" not in result

    # ── 8. futures: esclude 15:00 CT ────────────────────────────────────
    def test_excludes_1500_ct(self):
        dt = datetime(2026, 7, 29, 15, 0, tzinfo=CT)
        result = filter_rth_sessions(
            _session("2026-07-29", [_candle_at(dt)]),
            **self.FUT_KWARGS,
        )
        assert "2026-07-29" not in result


# ── 9. timezone conversion corretta ─────────────────────────────────────────

class TestTimezoneConversion:
    def test_same_instant_different_tz(self):
        """08:30 CT = 09:30 ET. The bar should be included by BOTH filters."""
        dt_ct = datetime(2026, 7, 29, 8, 30, tzinfo=CT)
        dt_et = datetime(2026, 7, 29, 9, 30, tzinfo=ET)
        # Same instant
        assert abs(dt_ct.timestamp() - dt_et.timestamp()) < 1

        candle = _candle_at(dt_ct)

        # Equity filter (ET) should include — it's 09:30 ET
        equity = filter_rth_sessions(_session("2026-07-29", [candle]))
        assert "2026-07-29" in equity

        # Futures filter (CT) should include — it's 08:30 CT
        futures = filter_rth_sessions(
            _session("2026-07-29", [candle]),
            timezone_str="America/Chicago",
            session_open="08:30",
            session_close="15:00",
        )
        assert "2026-07-29" in futures

    def test_bar_in_futures_not_equity(self):
        """08:00 CT = 09:00 ET. Inside neither equity nor futures session."""
        dt = datetime(2026, 7, 29, 8, 0, tzinfo=CT)
        candle = _candle_at(dt)

        equity = filter_rth_sessions(_session("2026-07-29", [candle]))
        assert "2026-07-29" not in equity

        futures = filter_rth_sessions(
            _session("2026-07-29", [candle]),
            timezone_str="America/Chicago",
            session_open="08:30",
            session_close="15:00",
        )
        assert "2026-07-29" not in futures


# ── 10. sessione con sole barre Globex esclusa ──────────────────────────────

class TestGlobexOnlyExcluded:
    def test_globex_only_excluded_equity(self):
        """A date with only pre-market bars should be excluded."""
        candles = [
            _candle_at(datetime(2026, 7, 29, 4, 0, tzinfo=ET)),
            _candle_at(datetime(2026, 7, 29, 7, 30, tzinfo=ET)),
        ]
        result = filter_rth_sessions(_session("2026-07-29", candles))
        assert "2026-07-29" not in result

    def test_globex_only_excluded_futures(self):
        """A date with only overnight Globex bars (17:00–07:00 CT) excluded."""
        candles = [
            _candle_at(datetime(2026, 7, 29, 17, 0, tzinfo=CT)),
            _candle_at(datetime(2026, 7, 29, 23, 0, tzinfo=CT)),
            _candle_at(datetime(2026, 7, 30, 3, 0, tzinfo=CT)),
        ]
        result = filter_rth_sessions(
            _session("2026-07-29", candles),
            timezone_str="America/Chicago",
            session_open="08:30",
            session_close="15:00",
        )
        assert "2026-07-29" not in result


# ── 11. default equity invariato ─────────────────────────────────────────────

class TestDefaultEquityUnchanged:
    def test_no_args_matches_equity(self):
        """Calling with no kwargs behaves exactly like the old hardcoded version."""
        candles = [
            _candle_at(datetime(2026, 7, 29, 4, 0, tzinfo=ET)),   # pre-market
            _candle_at(datetime(2026, 7, 29, 9, 30, tzinfo=ET)),  # RTH open
            _candle_at(datetime(2026, 7, 29, 12, 0, tzinfo=ET)),  # mid-day
            _candle_at(datetime(2026, 7, 29, 15, 59, tzinfo=ET)), # last RTH
            _candle_at(datetime(2026, 7, 29, 18, 0, tzinfo=ET)),  # after-hours
        ]
        result = filter_rth_sessions(_session("2026-07-29", candles))
        assert "2026-07-29" in result

    def test_no_args_excludes_after_hours_only(self):
        """Date with only after-hours bars excluded by default."""
        candles = [
            _candle_at(datetime(2026, 7, 29, 18, 0, tzinfo=ET)),
            _candle_at(datetime(2026, 7, 29, 19, 0, tzinfo=ET)),
        ]
        result = filter_rth_sessions(_session("2026-07-29", candles))
        assert "2026-07-29" not in result

    def test_boundary_1559_included_by_default(self):
        """15:59 ET is included by default (close=16:00 exclusive)."""
        dt = datetime(2026, 7, 29, 15, 59, tzinfo=ET)
        result = filter_rth_sessions(_session("2026-07-29", [_candle_at(dt)]))
        assert "2026-07-29" in result

    def test_boundary_1600_excluded_by_default(self):
        """16:00 ET is excluded by default (close=16:00 exclusive)."""
        dt = datetime(2026, 7, 29, 16, 0, tzinfo=ET)
        result = filter_rth_sessions(_session("2026-07-29", [_candle_at(dt)]))
        assert "2026-07-29" not in result
