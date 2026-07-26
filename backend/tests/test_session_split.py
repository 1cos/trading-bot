"""Tests for canonical session splitting.

Parity vectors verified by running splitIntoSessions in
estrategie/bdrr_strategy_runner.js via Node.js on dati/SPY_5m.csv.
"""

import pytest

from trading_lab.session_split import split_into_sessions


# ── Helpers ───────────────────────────────────────────────────────────────────

TZ = "America/New_York"


def candle(time_ms, close=100.0):
    """Minimal raw candle dict matching parse_candles_from_csv output."""
    return {
        "time_ms": time_ms,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
    }


# Known epoch ms values:
#   2026-04-24 09:30 EDT → UTC 13:30 → 1777037400000
#   2026-04-24 09:35 EDT → UTC 13:35 → 1777037700000
#   2026-04-24 15:55 EDT → UTC 19:55 → 1777060500000
#   2026-04-27 09:30 EDT → UTC 13:30 → 1777296600000
MS_0930_APR24 = 1777037400000
MS_0935_APR24 = 1777037700000
MS_1555_APR24 = 1777060500000
MS_0930_APR27 = 1777296600000


# ═══════════════════════════════════════════════════════════════════════════════
# Empty input
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmptyInput:
    def test_empty_list(self):
        assert split_into_sessions([], TZ) == []

    def test_returns_list(self):
        result = split_into_sessions([], TZ)
        assert isinstance(result, list)


# ═══════════════════════════════════════════════════════════════════════════════
# Single candle
# ═══════════════════════════════════════════════════════════════════════════════


class TestSingleCandle:
    def test_one_candle(self):
        c = candle(MS_0930_APR24)
        result = split_into_sessions([c], TZ)
        assert len(result) == 1
        assert result[0]["date"] == "2026-04-24"
        assert result[0]["candles"] == [c]

    def test_identity_preserved(self):
        c = candle(MS_0930_APR24)
        result = split_into_sessions([c], TZ)
        assert result[0]["candles"][0] is c


# ═══════════════════════════════════════════════════════════════════════════════
# Same date grouping
# ═══════════════════════════════════════════════════════════════════════════════


class TestSameDateGrouping:
    def test_two_candles_same_date(self):
        c1 = candle(MS_0930_APR24)
        c2 = candle(MS_0935_APR24)
        result = split_into_sessions([c1, c2], TZ)
        assert len(result) == 1
        assert result[0]["date"] == "2026-04-24"
        assert len(result[0]["candles"]) == 2

    def test_three_candles_same_date(self):
        c1 = candle(MS_0930_APR24)
        c2 = candle(MS_0935_APR24)
        c3 = candle(MS_1555_APR24)
        result = split_into_sessions([c1, c2, c3], TZ)
        assert len(result) == 1
        assert len(result[0]["candles"]) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# Multiple dates
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultipleDates:
    def test_two_dates(self):
        c1 = candle(MS_0930_APR24)
        c2 = candle(MS_0930_APR27)
        result = split_into_sessions([c1, c2], TZ)
        assert len(result) == 2
        assert result[0]["date"] == "2026-04-24"
        assert result[1]["date"] == "2026-04-27"

    def test_sorted_by_date_key(self):
        """Even if input is reverse-ordered, output is sorted by date."""
        c1 = candle(MS_0930_APR27)
        c2 = candle(MS_0930_APR24)
        result = split_into_sessions([c1, c2], TZ)
        assert result[0]["date"] == "2026-04-24"
        assert result[1]["date"] == "2026-04-27"


# ═══════════════════════════════════════════════════════════════════════════════
# Candle order within sessions
# ═══════════════════════════════════════════════════════════════════════════════


class TestCandleOrder:
    def test_insertion_order_preserved(self):
        """Candles within a session keep their input order."""
        c1 = candle(MS_0935_APR24, close=200.0)
        c2 = candle(MS_0930_APR24, close=100.0)
        result = split_into_sessions([c1, c2], TZ)
        # c1 was inserted first even though it has a later timestamp
        assert result[0]["candles"][0] is c1
        assert result[0]["candles"][1] is c2


# ═══════════════════════════════════════════════════════════════════════════════
# Duplicate timestamps
# ═══════════════════════════════════════════════════════════════════════════════


class TestDuplicates:
    def test_duplicates_not_removed(self):
        c1 = candle(MS_0930_APR24, close=100.0)
        c2 = candle(MS_0930_APR24, close=200.0)
        result = split_into_sessions([c1, c2], TZ)
        assert len(result[0]["candles"]) == 2

    def test_duplicate_identity(self):
        c1 = candle(MS_0930_APR24, close=100.0)
        c2 = candle(MS_0930_APR24, close=200.0)
        result = split_into_sessions([c1, c2], TZ)
        assert result[0]["candles"][0] is c1
        assert result[0]["candles"][1] is c2


# ═══════════════════════════════════════════════════════════════════════════════
# UTC vs ET date boundary
# ═══════════════════════════════════════════════════════════════════════════════


class TestDateBoundary:
    def test_utc_midnight_is_previous_et_date(self):
        """UTC 2026-04-25 00:00 = ET 2026-04-24 20:00 → key is 2026-04-24."""
        # 2026-04-25 00:00 UTC = epoch ms for that time
        ms = 1777075200000  # 2026-04-25T00:00:00Z
        result = split_into_sessions([candle(ms)], TZ)
        assert result[0]["date"] == "2026-04-24"

    def test_utc_04_00_is_next_et_date(self):
        """UTC 2026-04-25 04:00 = ET 2026-04-25 00:00 → key is 2026-04-25."""
        ms = 1777089600000  # 2026-04-25T04:00:00Z
        result = split_into_sessions([candle(ms)], TZ)
        assert result[0]["date"] == "2026-04-25"


# ═══════════════════════════════════════════════════════════════════════════════
# EST vs EDT (DST transitions)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDST:
    def test_edt_period(self):
        """During EDT (UTC-4), 13:30 UTC = 09:30 ET."""
        result = split_into_sessions([candle(MS_0930_APR24)], TZ)
        assert result[0]["date"] == "2026-04-24"

    def test_est_period(self):
        """During EST (UTC-5), 14:30 UTC = 09:30 ET.
        2026-01-05 14:30 UTC = 2026-01-05 09:30 EST."""
        ms = 1767623400000  # 2026-01-05T14:30:00Z
        result = split_into_sessions([candle(ms)], TZ)
        assert result[0]["date"] == "2026-01-05"

    def test_spring_forward(self):
        """2026 spring forward: March 8, 2:00 AM ET → 3:00 AM ET.
        Just before: 2026-03-08 06:59 UTC = 2026-03-08 01:59 EST → date 2026-03-08.
        Just after:  2026-03-08 07:01 UTC = 2026-03-08 03:01 EDT → date 2026-03-08."""
        ms_before = 1772953140000  # 2026-03-08T06:59:00Z
        ms_after = 1772953260000   # 2026-03-08T07:01:00Z
        result = split_into_sessions(
            [candle(ms_before), candle(ms_after)], TZ
        )
        assert len(result) == 1
        assert result[0]["date"] == "2026-03-08"

    def test_fall_back(self):
        """2026 fall back: November 1, 2:00 AM EDT → 1:00 AM EST.
        Before: 2026-11-01 05:30 UTC = 2026-11-01 01:30 EDT → date 2026-11-01.
        After:  2026-11-01 06:30 UTC = 2026-11-01 01:30 EST → date 2026-11-01."""
        ms_before = 1793511000000  # 2026-11-01T05:30:00Z
        ms_after = 1793514600000   # 2026-11-01T06:30:00Z
        result = split_into_sessions(
            [candle(ms_before), candle(ms_after)], TZ
        )
        # Both map to 2026-11-01 in ET
        assert len(result) == 1
        assert result[0]["date"] == "2026-11-01"


# ═══════════════════════════════════════════════════════════════════════════════
# Timezone parameter
# ═══════════════════════════════════════════════════════════════════════════════


class TestTimezoneParam:
    def test_explicit_timezone(self):
        result = split_into_sessions(
            [candle(MS_0930_APR24)], "America/New_York"
        )
        assert result[0]["date"] == "2026-04-24"

    def test_utc_timezone(self):
        """UTC grouping: 2026-04-24 13:30 UTC → date 2026-04-24."""
        result = split_into_sessions(
            [candle(MS_0930_APR24)], "UTC"
        )
        assert result[0]["date"] == "2026-04-24"

    def test_different_timezone_changes_date(self):
        """Tokyo (UTC+9): 2026-04-24 13:30 UTC = 2026-04-24 22:30 JST."""
        result = split_into_sessions(
            [candle(MS_0930_APR24)], "Asia/Tokyo"
        )
        assert result[0]["date"] == "2026-04-24"

        """But 2026-04-24 20:00 UTC = 2026-04-25 05:00 JST → next day."""
        ms_late = 1777060800000  # 2026-04-24T20:00:00Z
        result2 = split_into_sessions(
            [candle(ms_late)], "Asia/Tokyo"
        )
        assert result2[0]["date"] == "2026-04-25"

    def test_invalid_timezone(self):
        with pytest.raises(KeyError):
            split_into_sessions([candle(MS_0930_APR24)], "Invalid/Zone")


# ═══════════════════════════════════════════════════════════════════════════════
# Input validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestInputValidation:
    def test_candles_not_list(self):
        with pytest.raises(TypeError, match="must be a list"):
            split_into_sessions((), TZ)

    def test_timezone_not_string(self):
        with pytest.raises(TypeError, match="must be a str"):
            split_into_sessions([], 123)


# ═══════════════════════════════════════════════════════════════════════════════
# Raw candle values preserved
# ═══════════════════════════════════════════════════════════════════════════════


class TestValuePreservation:
    def test_all_fields_preserved(self):
        c = candle(MS_0930_APR24, close=525.50)
        result = split_into_sessions([c], TZ)
        out = result[0]["candles"][0]
        assert out["time_ms"] == MS_0930_APR24
        assert out["open"] == 525.50
        assert out["high"] == 526.50
        assert out["low"] == 524.50
        assert out["close"] == 525.50


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministic:
    def test_repeated_calls(self):
        c1 = candle(MS_0930_APR24)
        c2 = candle(MS_0930_APR27)
        results = [split_into_sessions([c1, c2], TZ) for _ in range(10)]
        for r in results:
            assert len(r) == 2
            assert r[0]["date"] == "2026-04-24"
            assert r[1]["date"] == "2026-04-27"


# ═══════════════════════════════════════════════════════════════════════════════
# Output shape
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutputShape:
    def test_keys(self):
        result = split_into_sessions([candle(MS_0930_APR24)], TZ)
        assert set(result[0].keys()) == {"date", "candles"}

    def test_date_is_string(self):
        result = split_into_sessions([candle(MS_0930_APR24)], TZ)
        assert isinstance(result[0]["date"], str)

    def test_candles_is_list(self):
        result = split_into_sessions([candle(MS_0930_APR24)], TZ)
        assert isinstance(result[0]["candles"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# Real CSV parity (SPY_5m.csv)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealCSVParity:
    """Parity against JS splitIntoSessions on dati/SPY_5m.csv.

    JS results: 60 sessions, each with 78 candles, keys from
    2026-04-24 through 2026-07-21.
    """

    @pytest.fixture()
    def spy_sessions(self):
        import os
        from trading_lab.csv_parser import parse_candles_from_csv

        csv_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "dati", "SPY_5m.csv"
        )
        if not os.path.exists(csv_path):
            pytest.skip("SPY_5m.csv not available")
        with open(csv_path) as f:
            candles = parse_candles_from_csv(f.read())
        return split_into_sessions(candles, "America/New_York")

    def test_session_count(self, spy_sessions):
        assert len(spy_sessions) == 60

    def test_total_candles(self, spy_sessions):
        total = sum(len(s["candles"]) for s in spy_sessions)
        assert total == 4680

    def test_all_sessions_78_candles(self, spy_sessions):
        for s in spy_sessions:
            assert len(s["candles"]) == 78, f"{s['date']} has {len(s['candles'])}"

    def test_first_session(self, spy_sessions):
        s = spy_sessions[0]
        assert s["date"] == "2026-04-24"
        assert s["candles"][0]["time_ms"] == 1777037400000
        assert s["candles"][-1]["time_ms"] == 1777060500000

    def test_last_session(self, spy_sessions):
        s = spy_sessions[-1]
        assert s["date"] == "2026-07-21"
        assert s["candles"][0]["time_ms"] == 1784640600000
        assert s["candles"][-1]["time_ms"] == 1784663700000

    def test_session_keys_sorted(self, spy_sessions):
        keys = [s["date"] for s in spy_sessions]
        assert keys == sorted(keys)

    def test_identity_preserved(self, spy_sessions):
        """Session candles are the same dict objects, not copies."""
        from trading_lab.csv_parser import parse_candles_from_csv
        import os
        csv_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "dati", "SPY_5m.csv"
        )
        with open(csv_path) as f:
            candles = parse_candles_from_csv(f.read())
        sessions = split_into_sessions(candles, "America/New_York")
        assert sessions[0]["candles"][0] is candles[0]
