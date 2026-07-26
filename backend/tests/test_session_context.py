"""Tests for canonical buildSessionContext port.

Parity vectors verified against bdrr_engine.js buildSessionContext
via Node.js on dati/SPY_5m.csv sessions.
"""

import copy

import pytest

from trading_lab.session_context import build_session_context


# ── Config fixture ────────────────────────────────────────────────────────────

CONFIG = {
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "tick_size": 0.01,
}

# 2026-07-01 EDT: 09:30 ET = 13:30 UTC = epoch ms 1782912600000
MS_0930 = 1782912600000
MS_0935 = 1782912900000
MS_0940 = 1782913200000
MS_1555 = 1782935700000


def candle(time_ms, close=100.0):
    return {
        "time_ms": time_ms,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Valid construction
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidConstruction:
    def test_single_candle(self):
        sc = build_session_context([candle(MS_0930)], CONFIG)
        assert sc["status"] == "OK"
        assert sc["date"] == "2026-07-01"
        assert sc["timezone"] == "America/New_York"
        assert sc["session_open"] == "09:30"
        assert sc["candle_count"] == 1

    def test_multiple_candles(self):
        candles = [candle(MS_0930), candle(MS_0935), candle(MS_0940)]
        sc = build_session_context(candles, CONFIG)
        assert sc["status"] == "OK"
        assert sc["candle_count"] == 3

    def test_full_session(self):
        candles = [candle(MS_0930 + i * 300000) for i in range(78)]
        sc = build_session_context(candles, CONFIG)
        assert sc["status"] == "OK"
        assert sc["candle_count"] == 78


class TestOutputFields:
    def test_all_ok_fields(self):
        sc = build_session_context([candle(MS_0930)], CONFIG)
        assert set(sc.keys()) == {
            "status", "date", "timezone", "session_open",
            "candles", "candle_count",
        }

    def test_all_failed_fields(self):
        sc = build_session_context([], CONFIG)
        assert set(sc.keys()) == {"status", "failed_stage", "reason"}


# ═══════════════════════════════════════════════════════════════════════════════
# Sorting behavior
# ═══════════════════════════════════════════════════════════════════════════════


class TestSorting:
    def test_already_sorted(self):
        candles = [candle(MS_0930), candle(MS_0935), candle(MS_0940)]
        sc = build_session_context(candles, CONFIG)
        times = [c["time_ms"] for c in sc["candles"]]
        assert times == sorted(times)

    def test_reverse_input_sorted(self):
        candles = [candle(MS_0940), candle(MS_0935), candle(MS_0930)]
        sc = build_session_context(candles, CONFIG)
        times = [c["time_ms"] for c in sc["candles"]]
        assert times == [MS_0930, MS_0935, MS_0940]

    def test_random_order_sorted(self):
        candles = [candle(MS_0935), candle(MS_0940), candle(MS_0930)]
        sc = build_session_context(candles, CONFIG)
        times = [c["time_ms"] for c in sc["candles"]]
        assert times == [MS_0930, MS_0935, MS_0940]


# ═══════════════════════════════════════════════════════════════════════════════
# Identity and mutation
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdentityAndMutation:
    def test_original_list_not_mutated(self):
        candles = [candle(MS_0935), candle(MS_0930)]
        original_order = [c["time_ms"] for c in candles]
        build_session_context(candles, CONFIG)
        assert [c["time_ms"] for c in candles] == original_order

    def test_candle_identity_preserved(self):
        """Sorted output contains the same dict objects as input."""
        c1 = candle(MS_0930)
        c2 = candle(MS_0935)
        sc = build_session_context([c2, c1], CONFIG)
        assert sc["candles"][0] is c1  # c1 has earlier time
        assert sc["candles"][1] is c2

    def test_defensive_copy(self):
        """Output list is not the same object as input list."""
        candles = [candle(MS_0930)]
        sc = build_session_context(candles, CONFIG)
        assert sc["candles"] is not candles


# ═══════════════════════════════════════════════════════════════════════════════
# Duplicate timestamps
# ═══════════════════════════════════════════════════════════════════════════════


class TestDuplicates:
    def test_duplicates_preserved(self):
        c1 = candle(MS_0930, close=100.0)
        c2 = candle(MS_0930, close=200.0)
        sc = build_session_context([c1, c2], CONFIG)
        assert sc["status"] == "OK"
        assert sc["candle_count"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Empty input
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmptyInput:
    def test_empty_list(self):
        sc = build_session_context([], CONFIG)
        assert sc["status"] == "FAILED"
        assert sc["failed_stage"] == "INVALID_SESSION_INPUT"
        assert "no candles" in sc["reason"]


# ═══════════════════════════════════════════════════════════════════════════════
# Multiple dates
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultipleDates:
    def test_two_different_et_dates(self):
        # 2026-07-01 and 2026-07-02 (24 hours apart)
        c1 = candle(MS_0930)
        c2 = candle(MS_0930 + 86400000)
        sc = build_session_context([c1, c2], CONFIG)
        assert sc["status"] == "FAILED"
        assert sc["failed_stage"] == "INVALID_SESSION_INPUT"
        assert "multiple ET calendar dates" in sc["reason"]
        assert "2026-07-01" in sc["reason"]
        assert "2026-07-02" in sc["reason"]

    def test_utc_midnight_same_et_date(self):
        """UTC midnight 2026-07-02 00:00 = ET 2026-07-01 20:00 → same date."""
        # 2026-07-01 09:30 ET and 2026-07-01 20:00 ET
        c1 = candle(MS_0930)
        ms_2000 = MS_0930 + 37800000  # +10.5 hours = 20:00 ET
        c2 = candle(ms_2000)
        sc = build_session_context([c1, c2], CONFIG)
        assert sc["status"] == "OK"
        assert sc["date"] == "2026-07-01"


# ═══════════════════════════════════════════════════════════════════════════════
# Timezone
# ═══════════════════════════════════════════════════════════════════════════════


class TestTimezone:
    def test_timezone_propagated(self):
        sc = build_session_context([candle(MS_0930)], CONFIG)
        assert sc["timezone"] == "America/New_York"

    def test_edt_date(self):
        """EDT: 2026-07-01 13:30 UTC = 2026-07-01 09:30 EDT."""
        sc = build_session_context([candle(MS_0930)], CONFIG)
        assert sc["date"] == "2026-07-01"

    def test_est_date(self):
        """EST: 2026-01-05 14:30 UTC = 2026-01-05 09:30 EST."""
        ms = 1767623400000
        config = {**CONFIG}
        sc = build_session_context([candle(ms)], config)
        assert sc["date"] == "2026-01-05"


# ═══════════════════════════════════════════════════════════════════════════════
# Config validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigValidation:
    def test_config_not_dict(self):
        with pytest.raises(TypeError, match="must be a dict"):
            build_session_context([candle(MS_0930)], "config")

    def test_config_none(self):
        with pytest.raises(TypeError, match="must be a dict"):
            build_session_context([candle(MS_0930)], None)

    def test_missing_timezone(self):
        cfg = {k: v for k, v in CONFIG.items() if k != "timezone"}
        with pytest.raises(TypeError, match="timezone"):
            build_session_context([candle(MS_0930)], cfg)

    def test_missing_session_open(self):
        cfg = {k: v for k, v in CONFIG.items() if k != "session_open"}
        with pytest.raises(TypeError, match="session_open"):
            build_session_context([candle(MS_0930)], cfg)

    def test_missing_tick_size(self):
        cfg = {k: v for k, v in CONFIG.items() if k != "tick_size"}
        with pytest.raises(TypeError, match="tick_size"):
            build_session_context([candle(MS_0930)], cfg)

    def test_missing_direction(self):
        cfg = {k: v for k, v in CONFIG.items() if k != "direction"}
        with pytest.raises(TypeError, match="direction"):
            build_session_context([candle(MS_0930)], cfg)


# ═══════════════════════════════════════════════════════════════════════════════
# Input type validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestInputValidation:
    def test_candles_not_list(self):
        with pytest.raises(TypeError, match="must be a list"):
            build_session_context((), CONFIG)

    def test_candles_tuple(self):
        with pytest.raises(TypeError, match="must be a list"):
            build_session_context((candle(MS_0930),), CONFIG)


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministic:
    def test_repeated_calls(self):
        candles = [candle(MS_0935), candle(MS_0930)]
        results = [build_session_context(candles, CONFIG) for _ in range(10)]
        for r in results:
            assert r["date"] == "2026-07-01"
            assert r["candle_count"] == 2
            assert r["candles"][0]["time_ms"] == MS_0930


# ═══════════════════════════════════════════════════════════════════════════════
# No ORB or detection fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoDetection:
    def test_no_orb_fields(self):
        sc = build_session_context([candle(MS_0930)], CONFIG)
        assert "orb_high" not in sc
        assert "orb_low" not in sc
        assert "level_price" not in sc
        assert "orb_candle_index" not in sc


# ═══════════════════════════════════════════════════════════════════════════════
# Real CSV parity
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealCSVParity:
    """Parity against JS buildSessionContext on SPY_5m.csv 2026-04-24.

    JS results:
      status: OK
      date: 2026-04-24
      candle_count: 78
      first time_ms: 1777037400000
      last time_ms: 1777060500000
    """

    @pytest.fixture()
    def spy_first_session(self):
        import os
        from trading_lab.csv_parser import parse_candles_from_csv
        from trading_lab.session_split import split_into_sessions

        csv_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "dati", "SPY_5m.csv"
        )
        if not os.path.exists(csv_path):
            pytest.skip("SPY_5m.csv not available")
        with open(csv_path) as f:
            candles = parse_candles_from_csv(f.read())
        sessions = split_into_sessions(candles, "America/New_York")
        return sessions[0]["candles"]

    def test_status(self, spy_first_session):
        sc = build_session_context(spy_first_session, CONFIG)
        assert sc["status"] == "OK"

    def test_date(self, spy_first_session):
        sc = build_session_context(spy_first_session, CONFIG)
        assert sc["date"] == "2026-04-24"

    def test_candle_count(self, spy_first_session):
        sc = build_session_context(spy_first_session, CONFIG)
        assert sc["candle_count"] == 78

    def test_first_timestamp(self, spy_first_session):
        sc = build_session_context(spy_first_session, CONFIG)
        assert sc["candles"][0]["time_ms"] == 1777037400000

    def test_last_timestamp(self, spy_first_session):
        sc = build_session_context(spy_first_session, CONFIG)
        assert sc["candles"][-1]["time_ms"] == 1777060500000

    def test_sorted(self, spy_first_session):
        sc = build_session_context(spy_first_session, CONFIG)
        times = [c["time_ms"] for c in sc["candles"]]
        assert times == sorted(times)

    def test_session_open_propagated(self, spy_first_session):
        sc = build_session_context(spy_first_session, CONFIG)
        assert sc["session_open"] == "09:30"
