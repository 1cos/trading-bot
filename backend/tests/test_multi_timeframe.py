"""Tests for multi-timeframe runner and ORB override.

Covers:
  1. ORB equality across all timeframes (same 1m source → same ORB H/L)
  2. No pre-09:35 contamination in detector input
  3. First detector candle begins at 09:35
  4. Backward compatibility: no-override path identical to previous output
  5. Invalid ORB override rejected with clear error
  6. Incomplete 1m ORB data rejected
  7. Session boundary isolation
  8. Existing tests unaffected
"""

import csv
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading_lab.strategy_runner import run_bdrr_strategy
from trading_lab.timeframe_aggregation import aggregate_post_orb
from trading_lab.multi_timeframe_runner import run_multi_timeframe


ET = ZoneInfo("America/New_York")
VOLATILE_KEYS = frozenset({
    "run_record_id", "candidate_id", "detection_result_id",
    "produced_at", "result_id",
})


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items()
                if k not in VOLATILE_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_strip_volatile(x) for x in obj]
    if hasattr(obj, "to_dict"):
        return _strip_volatile(obj.to_dict())
    return obj


# ── Fixtures: build synthetic 1-minute session ─────────────────────────────

def _1m_candle(hour, minute, o, h, l, c, v=100):
    """Build a 1m candle at a given ET time on 2026-07-01."""
    dt = datetime(2026, 7, 1, hour, minute, tzinfo=ET)
    utc_ms = int(dt.astimezone(timezone.utc).timestamp() * 1000)
    return {"time_ms": utc_ms, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _build_1m_session():
    """Build a full 1m session with a clear LONG setup.

    ORB (09:30–09:34): high=101.50, low=99.00
    Post-ORB: break above 101.50, displacement, retest, confirm.
    """
    candles = []
    # ORB bars: 09:30–09:34 (5 bars)
    candles.append(_1m_candle(9, 30, 100.0, 101.0, 99.0, 100.5))
    candles.append(_1m_candle(9, 31, 100.5, 101.2, 100.0, 100.8))
    candles.append(_1m_candle(9, 32, 100.8, 101.5, 100.5, 101.0))
    candles.append(_1m_candle(9, 33, 101.0, 101.3, 100.3, 100.6))
    candles.append(_1m_candle(9, 34, 100.6, 101.0, 100.0, 100.4))
    # Post-ORB: 09:35+ (break, displacement, retest, confirm)
    candles.append(_1m_candle(9, 35, 100.5, 102.0, 100.3, 101.80))  # break
    candles.append(_1m_candle(9, 36, 101.8, 102.5, 101.6, 102.20))  # disp
    candles.append(_1m_candle(9, 37, 102.2, 102.8, 101.5, 102.50))  # disp
    candles.append(_1m_candle(9, 38, 102.3, 102.6, 101.3, 101.60))  # retest contact
    # Confirmation: wick to 101.2 (below 101.5), close at 102.0
    candles.append(_1m_candle(9, 39, 101.55, 102.1, 101.2, 102.00))  # confirm attempt
    candles.append(_1m_candle(9, 40, 102.0, 103.0, 101.9, 102.80))
    candles.append(_1m_candle(9, 41, 102.8, 103.5, 102.5, 103.20))
    candles.append(_1m_candle(9, 42, 103.2, 104.0, 103.0, 103.80))
    candles.append(_1m_candle(9, 43, 103.8, 104.5, 103.5, 104.20))
    candles.append(_1m_candle(9, 44, 104.2, 105.0, 104.0, 104.80))
    return candles


# ── Test 1: ORB equality across timeframes ──────────────────────────────────


class TestOrbEquality:
    def test_all_timeframes_same_orb(self):
        candles = _build_1m_session()
        orb_values = {}
        for tf in [1, 2, 3, 5, 10]:
            orb_summary, post = aggregate_post_orb(candles, tf)
            orb_values[tf] = (orb_summary["high"], orb_summary["low"])

        # All must be identical
        ref = orb_values[1]
        for tf, (h, l) in orb_values.items():
            assert h == ref[0], f"{tf}m ORB high {h} != {ref[0]}"
            assert l == ref[1], f"{tf}m ORB low {l} != {ref[1]}"

    def test_orb_values_correct(self):
        candles = _build_1m_session()
        orb_summary, _ = aggregate_post_orb(candles, 5)
        assert orb_summary["high"] == 101.5
        assert orb_summary["low"] == 99.0
        assert orb_summary["open"] == 100.0  # first bar open
        assert orb_summary["close"] == 100.4  # last bar close


# ── Test 2: No pre-09:35 contamination ──────────────────────────────────────


class TestNoPreOrbContamination:
    def test_no_pre_0935_in_post_candles(self):
        candles = _build_1m_session()
        for tf in [1, 2, 3, 5, 10]:
            orb_summary, post = aggregate_post_orb(candles, tf)
            for c in post:
                dt = datetime.fromtimestamp(
                    c["time_ms"] / 1000, tz=timezone.utc
                ).astimezone(ET)
                minute_of_day = dt.hour * 60 + dt.minute
                assert minute_of_day >= 575, (
                    f"{tf}m: post-ORB candle at {dt.strftime('%H:%M')} "
                    f"is before 09:35"
                )


# ── Test 3: First detector candle at 09:35 ──────────────────────────────────


class TestFirstCandleAt0935:
    def test_first_post_orb_at_0935(self):
        candles = _build_1m_session()
        for tf in [1, 2, 3, 5, 10]:
            orb_summary, post = aggregate_post_orb(candles, tf)
            assert len(post) > 0, f"{tf}m: no post-ORB candles"
            dt = datetime.fromtimestamp(
                post[0]["time_ms"] / 1000, tz=timezone.utc
            ).astimezone(ET)
            assert dt.strftime("%H:%M") == "09:35", (
                f"{tf}m: first post-ORB at {dt.strftime('%H:%M')}, not 09:35"
            )


# ── Test 4: Backward compatibility ──────────────────────────────────────────


class TestBackwardCompatibility:
    """Verify no-override 5m path produces identical results."""

    def _load_spy_sessions(self, dates):
        sm = {}
        dati_dir = Path(__file__).resolve().parent.parent.parent / "dati"
        with open(dati_dir / "SPY_5m.csv") as f:
            for i, row in enumerate(csv.reader(f)):
                if i < 3:
                    continue
                if not row[0].strip():
                    continue
                d = row[0][:10]
                if d not in dates:
                    continue
                if d not in sm:
                    sm[d] = []
                dt = datetime.fromisoformat(row[0])
                sm[d].append({
                    "time_ms": int(dt.timestamp() * 1000),
                    "open": float(row[4]), "high": float(row[2]),
                    "low": float(row[3]), "close": float(row[1]),
                    "volume": int(float(row[5])),
                })
        return sm

    def test_5m_no_override_identical(self):
        dates = {"2026-05-26", "2026-07-06", "2026-04-29", "2026-06-08"}
        sm = self._load_spy_sessions(dates)
        sessions = [
            {"symbol": "SPY", "date": d, "market_timezone": "America/New_York",
             "session_open_utc_ms": sm[d][0]["time_ms"],
             "session_close_utc_ms": sm[d][-1]["time_ms"],
             "timeframe": "5m", "candles": sm[d]}
            for d in sorted(dates)
        ]
        preset = {
            "preset_id": "frozen_default", "timeframe_minutes": 5,
            "timezone": "America/New_York", "session_open": "09:30",
            "orb_start": "session_open", "orb_duration_minutes": 5,
            "level_source": "ORB_HIGH", "direction": "LONG",
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
            "min_displacement_ticks": None, "min_penetration_ticks": None,
            "min_close_beyond_level_ticks": None,
            "consecutive_orb_closes": 2,
            "min_displacement_bars": 1,
            "confirmation_wick_penetration_pct_min": 0,
        }
        config = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0"}

        results = run_bdrr_strategy(sessions, preset, config)

        # Must produce same deterministic results (sorted by date)
        assert len(results) == 4
        by_date = {r["session_date"]: r for r in results}
        assert by_date["2026-04-29"]["detection_status"] == "INVALID"
        assert by_date["2026-05-26"]["detection_status"] == "VALID"
        assert by_date["2026-06-08"]["detection_status"] == "VALID"
        assert by_date["2026-07-06"]["detection_status"] == "VALID"
        assert str(by_date["2026-05-26"]["outcome"]) == "STOPPED"
        assert str(by_date["2026-07-06"]["outcome"]) == "TARGET_HIT"


# ── Test 5: Invalid override rejected ────────────────────────────────────────


class TestInvalidOverride:
    def test_missing_orb_high(self):
        candles = _build_1m_session()
        session = {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": candles[0]["time_ms"],
            "session_close_utc_ms": candles[-1]["time_ms"],
            "timeframe": "5m", "candles": candles,
            "_orb_override": {
                # Missing orb_high
                "orb_low": 99.0,
                "orb_candle": candles[0],
                "orb_candle_index": 0,
            },
        }
        preset = {
            "preset_id": "test", "timeframe_minutes": 5,
            "timezone": "America/New_York", "session_open": "09:30",
            "orb_start": "session_open", "orb_duration_minutes": 5,
            "level_source": "ORB_HIGH", "direction": "LONG",
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
            "min_displacement_ticks": None, "min_penetration_ticks": None,
            "min_close_beyond_level_ticks": None,
            "consecutive_orb_closes": 2,
        }
        config = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0"}
        results = run_bdrr_strategy([session], preset, config)
        assert results[0]["detection_status"] == "INVALID"

    def test_missing_orb_candle(self):
        session = {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": 1000000,
            "session_close_utc_ms": 2000000,
            "timeframe": "5m",
            "candles": [_1m_candle(9, 30, 100, 101, 99, 100)],
            "_orb_override": {
                "orb_high": 101.0, "orb_low": 99.0,
                "orb_candle_index": 0,
                # Missing orb_candle
            },
        }
        preset = {
            "preset_id": "test", "timeframe_minutes": 5,
            "timezone": "America/New_York", "session_open": "09:30",
            "orb_start": "session_open", "orb_duration_minutes": 5,
            "level_source": "ORB_HIGH", "direction": "LONG",
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
            "min_displacement_ticks": None, "min_penetration_ticks": None,
            "min_close_beyond_level_ticks": None, "consecutive_orb_closes": 2,
        }
        config = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0"}
        results = run_bdrr_strategy([session], preset, config)
        assert results[0]["detection_status"] == "INVALID"


# ── Test 6: Incomplete 1m ORB data rejected ──────────────────────────────────


class TestIncomplete1mOrb:
    def test_only_3_orb_bars_raises(self):
        # Only 3 bars in ORB window
        candles = [
            _1m_candle(9, 30, 100, 101, 99, 100),
            _1m_candle(9, 31, 100, 101, 99, 100),
            _1m_candle(9, 32, 100, 101, 99, 100),
            _1m_candle(9, 35, 100, 102, 99, 101),
        ]
        with pytest.raises(ValueError, match="Expected exactly 5 ORB bars"):
            aggregate_post_orb(candles, 5)

    def test_empty_session_skipped(self):
        result = run_multi_timeframe({}, "TEST", 5)
        assert result == []


# ── Test 7: Session boundary isolation ───────────────────────────────────────


class TestSessionBoundary:
    def test_different_dates_independent(self):
        c1 = _build_1m_session()
        # Second session: same candles but different date
        c2 = []
        for c in c1:
            c2_bar = dict(c)
            c2_bar["time_ms"] += 86400 * 1000  # next day
            c2.append(c2_bar)

        by_date = {"2026-07-01": c1, "2026-07-02": c2}
        results = run_multi_timeframe(by_date, "TEST", 5, "LONG")
        # Each session produces an independent result
        assert len(results) == 2
        dates = [r["session_date"] for r in results]
        assert "2026-07-01" in dates
        assert "2026-07-02" in dates


# ── Test 8: Multi-timeframe runner integration ───────────────────────────────


class TestMultiTimeframeRunner:
    def test_runs_at_1m(self):
        by_date = {"2026-07-01": _build_1m_session()}
        results = run_multi_timeframe(by_date, "TEST", 1, "LONG")
        assert len(results) == 1

    def test_runs_at_2m(self):
        by_date = {"2026-07-01": _build_1m_session()}
        results = run_multi_timeframe(by_date, "TEST", 2, "LONG")
        assert len(results) == 1

    def test_runs_at_5m(self):
        by_date = {"2026-07-01": _build_1m_session()}
        results = run_multi_timeframe(by_date, "TEST", 5, "LONG")
        assert len(results) == 1

    def test_runs_at_10m(self):
        by_date = {"2026-07-01": _build_1m_session()}
        results = run_multi_timeframe(by_date, "TEST", 10, "LONG")
        assert len(results) == 1

    def test_invalid_timeframe(self):
        with pytest.raises(ValueError):
            run_multi_timeframe({}, "TEST", 7, "LONG")
