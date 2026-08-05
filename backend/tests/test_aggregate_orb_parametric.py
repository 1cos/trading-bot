"""Tests for aggregate_post_orb — parametric ORB window."""

from datetime import datetime, timezone as dt_tz
from zoneinfo import ZoneInfo

import pytest

from trading_lab.timeframe_aggregation import aggregate_post_orb


ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")


def _bar(dt_aware, o=100, h=101, lo=99, c=100.5, v=10):
    return {
        "time_ms": int(dt_aware.timestamp() * 1000),
        "open": o, "high": h, "low": lo, "close": c, "volume": v,
    }


def _equity_session(date=(2026, 7, 29)):
    """Build a standard equity session: 09:30–09:40 ET (ORB + 5 post-ORB)."""
    bars = []
    for m in range(30, 41):
        dt = datetime(*date, 9, m, tzinfo=ET)
        bars.append(_bar(dt, o=100+m-30, h=102+m-30, lo=99+m-30, c=101+m-30, v=10*(m-29)))
    return bars


def _futures_session(date=(2026, 7, 29)):
    """Build a futures session: 08:30–08:40 CT (ORB + 5 post-ORB)."""
    bars = []
    for m in range(30, 41):
        dt = datetime(*date, 8, m, tzinfo=CT)
        bars.append(_bar(dt, o=5600+m-30, h=5602+m-30, lo=5599+m-30, c=5601+m-30, v=100*(m-29)))
    return bars


# ── 1. equity ORB 09:30–09:34 ───────────────────────────────────────────────

class TestEquityORB:
    def test_equity_orb_default(self):
        orb, post = aggregate_post_orb(_equity_session(), 1)
        assert orb is not None
        assert len(post) == 6  # 09:35–09:40

    def test_equity_orb_explicit(self):
        orb, post = aggregate_post_orb(
            _equity_session(), 1, "America/New_York",
            orb_open="09:30", orb_close="09:34",
        )
        assert orb is not None


# ── 2. futures ORB 08:30–08:34 ──────────────────────────────────────────────

class TestFuturesORB:
    def test_futures_orb(self):
        orb, post = aggregate_post_orb(
            _futures_session(), 1, "America/Chicago",
            orb_open="08:30", orb_close="08:34",
        )
        assert orb is not None
        assert len(post) == 6  # 08:35–08:40


# ── 3. prima barra post-ORB equity = 09:35 ──────────────────────────────────

class TestPostORBStart:
    def test_equity_post_orb_starts_0935(self):
        _, post = aggregate_post_orb(_equity_session(), 1)
        first_dt = datetime.fromtimestamp(
            post[0]["time_ms"] / 1000, tz=dt_tz.utc
        ).astimezone(ET)
        assert first_dt.hour == 9
        assert first_dt.minute == 35

    # ── 4. prima barra post-ORB futures = 08:35 ─────────────────────────
    def test_futures_post_orb_starts_0835(self):
        _, post = aggregate_post_orb(
            _futures_session(), 1, "America/Chicago",
            orb_open="08:30", orb_close="08:34",
        )
        first_dt = datetime.fromtimestamp(
            post[0]["time_ms"] / 1000, tz=dt_tz.utc
        ).astimezone(CT)
        assert first_dt.hour == 8
        assert first_dt.minute == 35


# ── 5. open dalla prima barra ────────────────────────────────────────────────

class TestORBSummaryValues:
    def test_open_from_first_bar(self):
        bars = _equity_session()
        orb, _ = aggregate_post_orb(bars, 1)
        assert orb["open"] == bars[0]["open"]

    # ── 6. high massimo ─────────────────────────────────────────────────
    def test_high_is_max(self):
        bars = _equity_session()
        orb, _ = aggregate_post_orb(bars, 1)
        expected = max(b["high"] for b in bars[:5])
        assert orb["high"] == expected

    # ── 7. low minimo ───────────────────────────────────────────────────
    def test_low_is_min(self):
        bars = _equity_session()
        orb, _ = aggregate_post_orb(bars, 1)
        expected = min(b["low"] for b in bars[:5])
        assert orb["low"] == expected

    # ── 8. close dall'ultima barra ──────────────────────────────────────
    def test_close_from_last_bar(self):
        bars = _equity_session()
        orb, _ = aggregate_post_orb(bars, 1)
        assert orb["close"] == bars[4]["close"]

    # ── 9. volume somma ─────────────────────────────────────────────────
    def test_volume_is_sum(self):
        bars = _equity_session()
        orb, _ = aggregate_post_orb(bars, 1)
        expected = sum(b["volume"] for b in bars[:5])
        assert orb["volume"] == expected

    def test_futures_ohlcv(self):
        bars = _futures_session()
        orb, _ = aggregate_post_orb(
            bars, 1, "America/Chicago",
            orb_open="08:30", orb_close="08:34",
        )
        assert orb["open"] == bars[0]["open"]
        assert orb["high"] == max(b["high"] for b in bars[:5])
        assert orb["low"] == min(b["low"] for b in bars[:5])
        assert orb["close"] == bars[4]["close"]
        assert orb["volume"] == sum(b["volume"] for b in bars[:5])


# ── 10. barre precedenti all'ORB escluse ────────────────────────────────────

class TestPreORBExcluded:
    def test_pre_orb_bars_ignored(self):
        """Bars before ORB window should not appear in ORB or post-ORB."""
        pre_bar = _bar(datetime(2026, 7, 29, 9, 15, tzinfo=ET))
        session = [pre_bar] + _equity_session()
        orb, post = aggregate_post_orb(session, 1)
        # ORB should still be 5 bars (09:30–09:34)
        assert orb["open"] == _equity_session()[0]["open"]
        # Post should still be 6 bars (09:35–09:40)
        assert len(post) == 6

    def test_globex_bars_excluded_futures(self):
        """Overnight Globex bars before 08:30 CT are excluded."""
        globex = _bar(datetime(2026, 7, 29, 3, 0, tzinfo=CT))
        session = [globex] + _futures_session()
        orb, post = aggregate_post_orb(
            session, 1, "America/Chicago",
            orb_open="08:30", orb_close="08:34",
        )
        assert orb["open"] == _futures_session()[0]["open"]
        assert len(post) == 6


# ── 11. comportamento default equity invariato ──────────────────────────────

class TestDefaultEquityUnchanged:
    def test_default_no_kwargs(self):
        """Calling with no kwargs matches old hardcoded behavior."""
        bars = _equity_session()
        orb, post = aggregate_post_orb(bars, 1)
        assert orb["open"] == bars[0]["open"]
        assert len(post) == 6

    def test_default_with_tz_only(self):
        """Calling with just timezone_str matches old behavior."""
        bars = _equity_session()
        orb, post = aggregate_post_orb(bars, 1, "America/New_York")
        assert orb["open"] == bars[0]["open"]

    def test_missing_orb_bars_raises(self):
        """Missing ORB bars still raise ValueError."""
        bars = _equity_session()[3:]  # skip first 3 ORB bars
        with pytest.raises(ValueError, match="Expected exactly 5"):
            aggregate_post_orb(bars, 1)


# ── 12. orari invalidi rifiutati ─────────────────────────────────────────────

class TestInvalidTimes:
    def test_invalid_orb_open(self):
        with pytest.raises(ValueError, match="Invalid orb_open"):
            aggregate_post_orb([], 1, orb_open="9:30", orb_close="09:34")

    def test_invalid_orb_close(self):
        with pytest.raises(ValueError, match="Invalid orb_close"):
            aggregate_post_orb([], 1, orb_open="09:30", orb_close="934")

    def test_invalid_orb_open_letters(self):
        with pytest.raises(ValueError, match="Invalid orb_open"):
            aggregate_post_orb([], 1, orb_open="ab:cd", orb_close="09:34")
