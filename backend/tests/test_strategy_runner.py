"""Tests for canonical run_bdrr_strategy port.

Mirrors estrategie/test_bdrr_strategy_runner.js (103 checks).
"""

import copy
import os
import re

import pytest

from trading_lab.strategy_runner import run_bdrr_strategy, Outcome
from trading_lab.csv_parser import parse_candles_from_csv
from trading_lab.session_split import split_into_sessions


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK_SIZE = 0.01

FROZEN_PRESET = {
    "preset_id": "bdrr_v1_initial",
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "entry_model": "CONFIRMATION_CLOSE",
    "entry_buffer_ticks": 0,
    "stop_buffer_ticks": 0,
    "min_displacement_ticks": None,
    "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": 1,
    "min_displacement_bars": 1,
    "confirmation_wick_penetration_pct_min": 0,
}

BASE_CONFIG = {
    "tick_size": TICK_SIZE,
    "engine_version": "bdrr_v1.0",
    "exit_target_r": 2,
}


def _make_date_ms(date_str, time_str):
    """Get epoch ms for ET time matching JS: new Date(d+'T'+t+':00-04:00')."""
    from datetime import datetime, timezone, timedelta
    iso = f"{date_str}T{time_str}:00-04:00"
    dt = datetime.fromisoformat(iso)
    return int(dt.timestamp() * 1000)


def _mc(date_str, time_str, open_, high, low, close):
    return {
        "time_ms": _make_date_ms(date_str, time_str),
        "open": open_, "high": high, "low": low, "close": close,
    }


def _build_no_break_session(date_str):
    return {
        "symbol": "TEST", "date": date_str,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": _make_date_ms(date_str, "09:30"),
        "session_close_utc_ms": _make_date_ms(date_str, "16:00"),
        "timeframe": "5m",
        "candles": [
            _mc(date_str, "09:30", 99.50, 100.00, 99.30, 99.80),
            _mc(date_str, "09:35", 99.70, 99.90, 99.60, 99.75),
            _mc(date_str, "09:40", 99.75, 99.95, 99.65, 99.70),
            _mc(date_str, "09:45", 99.70, 99.85, 99.55, 99.60),
            _mc(date_str, "09:50", 99.55, 99.75, 99.40, 99.50),
        ],
    }


def _build_stopped_session(date_str):
    return {
        "symbol": "TEST", "date": date_str,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": _make_date_ms(date_str, "09:30"),
        "session_close_utc_ms": _make_date_ms(date_str, "16:00"),
        "timeframe": "5m",
        "candles": [
            _mc(date_str, "09:30", 99.50, 100.00, 99.30, 99.80),
            _mc(date_str, "09:35", 99.90, 100.50, 99.80, 100.20),
            _mc(date_str, "09:40", 100.25, 100.60, 100.10, 100.40),
            _mc(date_str, "09:45", 100.10, 100.50, 99.70, 100.40),
            _mc(date_str, "09:50", 100.30, 100.35, 99.60, 99.65),
        ],
    }


def _build_target_hit_session(date_str):
    return {
        "symbol": "TEST", "date": date_str,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": _make_date_ms(date_str, "09:30"),
        "session_close_utc_ms": _make_date_ms(date_str, "16:00"),
        "timeframe": "5m",
        "candles": [
            _mc(date_str, "09:30", 99.50, 100.00, 99.30, 99.80),
            _mc(date_str, "09:35", 99.90, 100.50, 99.80, 100.20),
            _mc(date_str, "09:40", 100.25, 100.60, 100.10, 100.40),
            _mc(date_str, "09:45", 100.10, 100.50, 99.70, 100.40),
            _mc(date_str, "09:50", 100.50, 101.00, 100.30, 100.90),
            _mc(date_str, "09:55", 100.90, 101.50, 100.80, 101.30),
            _mc(date_str, "10:00", 101.30, 101.90, 101.20, 101.70),
        ],
    }


def _build_multi_target_session(date_str):
    return {
        "symbol": "TEST", "date": date_str,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": _make_date_ms(date_str, "09:30"),
        "session_close_utc_ms": _make_date_ms(date_str, "16:00"),
        "timeframe": "5m",
        "candles": [
            _mc(date_str, "09:30", 99.50, 100.00, 99.30, 99.80),
            _mc(date_str, "09:35", 99.90, 100.50, 99.80, 100.20),
            _mc(date_str, "09:40", 100.25, 100.60, 100.10, 100.40),
            _mc(date_str, "09:45", 100.10, 100.50, 99.70, 100.40),
            _mc(date_str, "09:50", 100.50, 101.00, 100.30, 100.90),
            _mc(date_str, "09:55", 100.90, 101.90, 100.80, 101.50),
            _mc(date_str, "10:00", 101.50, 102.60, 101.40, 102.40),
            _mc(date_str, "10:05", 102.20, 102.30, 99.50, 99.55),
        ],
    }


def _build_entry_not_triggered_session(date_str):
    return {
        "symbol": "TEST", "date": date_str,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": _make_date_ms(date_str, "09:30"),
        "session_close_utc_ms": _make_date_ms(date_str, "16:00"),
        "timeframe": "5m",
        "candles": [
            _mc(date_str, "09:30", 99.50, 100.00, 99.30, 99.80),
            _mc(date_str, "09:35", 99.90, 100.50, 99.80, 100.20),
            _mc(date_str, "09:40", 100.25, 100.60, 100.10, 100.40),
            _mc(date_str, "09:45", 100.10, 100.50, 99.70, 100.40),
            _mc(date_str, "09:50", 100.30, 100.45, 100.10, 100.20),
            _mc(date_str, "09:55", 100.15, 100.40, 100.00, 100.10),
        ],
    }


def _build_ambiguous_session(date_str):
    return {
        "symbol": "TEST", "date": date_str,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": _make_date_ms(date_str, "09:30"),
        "session_close_utc_ms": _make_date_ms(date_str, "16:00"),
        "timeframe": "5m",
        "candles": [
            _mc(date_str, "09:30", 99.50, 100.00, 99.30, 99.80),
            _mc(date_str, "09:35", 99.90, 100.50, 99.80, 100.20),
            _mc(date_str, "09:40", 100.25, 100.60, 100.10, 100.40),
            _mc(date_str, "09:45", 100.10, 100.50, 99.70, 100.40),
            _mc(date_str, "09:50", 100.50, 101.90, 99.50, 101.00),
        ],
    }


def _build_open_session(date_str):
    return {
        "symbol": "TEST", "date": date_str,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": _make_date_ms(date_str, "09:30"),
        "session_close_utc_ms": _make_date_ms(date_str, "16:00"),
        "timeframe": "5m",
        "candles": [
            _mc(date_str, "09:30", 99.50, 100.00, 99.30, 99.80),
            _mc(date_str, "09:35", 99.90, 100.50, 99.80, 100.20),
            _mc(date_str, "09:40", 100.25, 100.60, 100.10, 100.40),
            _mc(date_str, "09:45", 100.10, 100.50, 99.70, 100.40),
            _mc(date_str, "09:50", 100.50, 101.00, 100.00, 100.80),
            _mc(date_str, "09:55", 100.80, 101.50, 100.20, 101.30),
        ],
    }


UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

RECORD_FIELDS = [
    "run_record_id", "symbol", "session_date", "preset_id", "exit_target_r",
    "detection_status", "failure_stage", "failed_rules", "detection_result_id",
    "candidate_id", "confirmation_timestamp", "entry_timestamp",
    "first_evaluation_timestamp", "entry_price_ticks", "stop_price_ticks",
    "r2_price_ticks", "r3_price_ticks", "r4_price_ticks",
    "outcome", "realized_r", "highest_target_achieved",
    "exit_timestamp", "exit_price_ticks",
    "detection_result", "trade_plan", "trade_outcome",
]


# ── Helper to load real sessions ─────────────────────────────────────────────

def _load_real_sessions(symbol):
    csv_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "dati", f"{symbol}_5m.csv"
    )
    with open(csv_path, "r") as f:
        csv_content = f.read()
    candles = parse_candles_from_csv(csv_content)
    groups = split_into_sessions(candles, "America/New_York")
    sessions = []
    for g in groups:
        c = g["candles"]
        sessions.append({
            "symbol": symbol,
            "date": g["date"],
            "market_timezone": "America/New_York",
            "session_open_utc_ms": c[0]["time_ms"],
            "session_close_utc_ms": c[-1]["time_ms"],
            "timeframe": "5m",
            "candles": c,
        })
    return sessions


# ── T1: No valid setup ──────────────────────────────────────────────────────

class TestT1NoValidSetup:
    def test_outcome(self):
        r = run_bdrr_strategy(
            [_build_no_break_session("2026-07-01")], FROZEN_PRESET, BASE_CONFIG
        )
        assert len(r) == 1
        assert r[0]["outcome"] == Outcome.NO_VALID_SETUP

    def test_detection_status(self):
        r = run_bdrr_strategy(
            [_build_no_break_session("2026-07-01")], FROZEN_PRESET, BASE_CONFIG
        )
        assert r[0]["detection_status"] == "INVALID"

    def test_trade_plan_null(self):
        r = run_bdrr_strategy(
            [_build_no_break_session("2026-07-01")], FROZEN_PRESET, BASE_CONFIG
        )
        assert r[0]["trade_plan"] is None
        assert r[0]["trade_outcome"] is None


# ── T2: Stopped trade ──────────────────────────────────────────────────────

class TestT2Stopped:
    def test_outcome(self):
        r = run_bdrr_strategy(
            [_build_stopped_session("2026-07-02")], FROZEN_PRESET, BASE_CONFIG
        )
        assert r[0]["outcome"] == Outcome.STOPPED
        assert r[0]["detection_status"] == "VALID"
        assert r[0]["realized_r"] == -1

    def test_populated(self):
        r = run_bdrr_strategy(
            [_build_stopped_session("2026-07-02")], FROZEN_PRESET, BASE_CONFIG
        )
        assert r[0]["trade_plan"] is not None
        assert r[0]["trade_outcome"] is not None

    def test_exit_price_equals_stop(self):
        r = run_bdrr_strategy(
            [_build_stopped_session("2026-07-02")], FROZEN_PRESET, BASE_CONFIG
        )
        assert r[0]["exit_price_ticks"] == r[0]["stop_price_ticks"]


# ── T3: Target hit ──────────────────────────────────────────────────────────

class TestT3TargetHit:
    def test_outcome(self):
        r = run_bdrr_strategy(
            [_build_target_hit_session("2026-07-03")], FROZEN_PRESET, BASE_CONFIG
        )
        assert r[0]["outcome"] == Outcome.TARGET_HIT
        assert r[0]["realized_r"] == 2

    def test_exit_price_equals_r2(self):
        r = run_bdrr_strategy(
            [_build_target_hit_session("2026-07-03")], FROZEN_PRESET, BASE_CONFIG
        )
        assert r[0]["exit_price_ticks"] == r[0]["r2_price_ticks"]


# ── T4: Multi-target different R outcomes ────────────────────────────────────

class TestT4MultiTargetR:
    def test_r2_target_hit(self):
        s = _build_multi_target_session("2026-07-04")
        r = run_bdrr_strategy([s], FROZEN_PRESET, {**BASE_CONFIG, "exit_target_r": 2})
        assert r[0]["outcome"] == Outcome.TARGET_HIT
        assert r[0]["realized_r"] == 2

    def test_r3_target_hit(self):
        s = _build_multi_target_session("2026-07-04")
        r = run_bdrr_strategy([s], FROZEN_PRESET, {**BASE_CONFIG, "exit_target_r": 3})
        assert r[0]["outcome"] == Outcome.TARGET_HIT
        assert r[0]["realized_r"] == 3

    def test_r4_stopped(self):
        s = _build_multi_target_session("2026-07-04")
        r = run_bdrr_strategy([s], FROZEN_PRESET, {**BASE_CONFIG, "exit_target_r": 4})
        assert r[0]["outcome"] == Outcome.STOPPED
        assert r[0]["realized_r"] == -1


# ── T5: Entry not triggered ─────────────────────────────────────────────────

class TestT5EntryNotTriggered:
    def test_outcome(self):
        preset = {**FROZEN_PRESET, "entry_model": "BREAK_OF_SIGNAL_BAR"}
        r = run_bdrr_strategy(
            [_build_entry_not_triggered_session("2026-07-05")], preset, BASE_CONFIG
        )
        assert r[0]["outcome"] == Outcome.ENTRY_NOT_TRIGGERED
        assert r[0]["detection_status"] == "VALID"
        assert r[0]["trade_plan"] is not None


# ── T6: Ambiguous terminal bar ──────────────────────────────────────────────

class TestT6Ambiguous:
    def test_outcome(self):
        r = run_bdrr_strategy(
            [_build_ambiguous_session("2026-07-06")], FROZEN_PRESET, BASE_CONFIG
        )
        assert r[0]["outcome"] == Outcome.AMBIGUOUS
        assert r[0]["realized_r"] is None


# ── T7: Open trade ──────────────────────────────────────────────────────────

class TestT7Open:
    def test_outcome(self):
        r = run_bdrr_strategy(
            [_build_open_session("2026-07-07")], FROZEN_PRESET, BASE_CONFIG
        )
        assert r[0]["outcome"] == Outcome.SESSION_CLOSE
        assert r[0]["exit_timestamp"] is not None  # forced close on last bar
        assert r[0]["exit_price_ticks"] is not None
        assert r[0]["realized_r"] is not None


# ── T8: Chronological ordering ──────────────────────────────────────────────

class TestT8Chronological:
    def test_ordering(self):
        r = run_bdrr_strategy([
            _build_no_break_session("2026-07-01"),
            _build_stopped_session("2026-07-02"),
            _build_target_hit_session("2026-07-03"),
        ], FROZEN_PRESET, BASE_CONFIG)
        assert len(r) == 3
        assert r[0]["session_date"] == "2026-07-01"
        assert r[1]["session_date"] == "2026-07-02"
        assert r[2]["session_date"] == "2026-07-03"
        assert r[0]["outcome"] == Outcome.NO_VALID_SETUP
        assert r[1]["outcome"] == Outcome.STOPPED
        assert r[2]["outcome"] == Outcome.TARGET_HIT


# ── T9: Failed session isolation ────────────────────────────────────────────

class TestT9FailedSessionIsolation:
    def test_isolation(self):
        bad = {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": 0, "session_close_utc_ms": 0,
            "timeframe": "5m", "candles": [],
        }
        r = run_bdrr_strategy(
            [bad, _build_target_hit_session("2026-07-02")],
            FROZEN_PRESET, BASE_CONFIG,
        )
        assert len(r) == 2
        assert r[0]["outcome"] == Outcome.PIPELINE_FAILURE
        assert r[1]["outcome"] == Outcome.TARGET_HIT


# ── T10: Invalid config rejection ───────────────────────────────────────────

class TestT10InvalidConfig:
    def test_bad_exit_target_r(self):
        with pytest.raises(TypeError):
            run_bdrr_strategy([], FROZEN_PRESET, {**BASE_CONFIG, "exit_target_r": 5})

    def test_bad_tick_size(self):
        with pytest.raises(TypeError):
            run_bdrr_strategy([], FROZEN_PRESET, {**BASE_CONFIG, "tick_size": 0})

    def test_empty_engine_version(self):
        with pytest.raises(TypeError):
            run_bdrr_strategy([], FROZEN_PRESET, {**BASE_CONFIG, "engine_version": ""})

    def test_non_list_sessions(self):
        with pytest.raises(TypeError):
            run_bdrr_strategy("not-a-list", FROZEN_PRESET, BASE_CONFIG)

    def test_non_dict_preset(self):
        with pytest.raises(TypeError):
            run_bdrr_strategy([], None, BASE_CONFIG)

    def test_non_dict_config(self):
        with pytest.raises(TypeError):
            run_bdrr_strategy([], FROZEN_PRESET, None)


# ── T11: Input immutability ─────────────────────────────────────────────────

class TestT11InputImmutability:
    def test_not_mutated(self):
        session = _build_stopped_session("2026-07-02")
        candle_count = len(session["candles"])
        preset = {**FROZEN_PRESET}
        config = {**BASE_CONFIG}
        preset_copy = {**preset}
        config_copy = {**config}

        run_bdrr_strategy([session], preset, config)

        assert preset == preset_copy
        assert config == config_copy
        assert len(session["candles"]) == candle_count


# ── T13: Unique IDs ─────────────────────────────────────────────────────────

class TestT13UniqueIDs:
    def test_unique_uuids(self):
        r = run_bdrr_strategy([
            _build_stopped_session("2026-07-02"),
            _build_target_hit_session("2026-07-03"),
        ], FROZEN_PRESET, BASE_CONFIG)

        ids = set()
        for rec in r:
            rid = rec["run_record_id"]
            assert UUID_RE.match(rid), f"run_record_id must be UUID v4: {rid}"
            assert rid not in ids
            ids.add(rid)
            if rec.get("detection_result_id"):
                assert rec["detection_result_id"] not in ids
                ids.add(rec["detection_result_id"])
            if rec.get("candidate_id"):
                assert rec["candidate_id"] not in ids
                ids.add(rec["candidate_id"])


# ── T14: SPY 2026-05-26 frozen result ───────────────────────────────────────

class TestT14SPY20260526:
    def test_frozen_result(self):
        sessions = _load_real_sessions("SPY")
        may26 = [s for s in sessions if s["date"] == "2026-05-26"]
        assert len(may26) == 1

        r = run_bdrr_strategy(may26, FROZEN_PRESET, BASE_CONFIG)
        assert len(r) == 1
        rec = r[0]
        assert rec["detection_status"] == "VALID"
        assert rec["outcome"] == Outcome.STOPPED
        assert rec["stop_price_ticks"] == 75036
        assert rec["exit_price_ticks"] == 75036
        assert rec["realized_r"] == -1
        assert rec["detection_result"] is not None
        assert rec["trade_plan"] is not None
        assert rec["trade_outcome"] is not None


# ── T15: SPY batch oracle ────────────────────────────────────────────────────

class TestT15SPYBatch:
    def test_spy_batch(self):
        sessions = _load_real_sessions("SPY")
        assert len(sessions) == 60

        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        assert len(results) == len(sessions)

        valid = [r for r in results if r["detection_status"] == "VALID"]
        valid_dates = sorted(r["session_date"] for r in valid)

        assert len(valid) == 3
        assert set(valid_dates) == {"2026-05-26", "2026-06-08", "2026-07-06"}


# ── T16: QQQ batch oracle ───────────────────────────────────────────────────

class TestT16QQQBatch:
    def test_qqq_batch(self):
        sessions = _load_real_sessions("QQQ")
        assert len(sessions) == 60

        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        assert len(results) == len(sessions)

        valid = [r for r in results if r["detection_status"] == "VALID"]
        valid_dates = sorted(r["session_date"] for r in valid)

        assert len(valid) == 2  # sequence_validator invalidates 2 former false positives
        assert set(valid_dates) == {
            "2026-05-06", "2026-05-13",
        }


# ── T17: Result record schema ───────────────────────────────────────────────

class TestT17RecordSchema:
    def test_all_fields_present(self):
        r = run_bdrr_strategy(
            [_build_stopped_session("2026-07-02")], FROZEN_PRESET, BASE_CONFIG
        )
        for f in RECORD_FIELDS:
            assert f in r[0], f"missing field: {f}"

    def test_field_count(self):
        r = run_bdrr_strategy(
            [_build_stopped_session("2026-07-02")], FROZEN_PRESET, BASE_CONFIG
        )
        assert len(r[0]) == len(RECORD_FIELDS)


# ── Empty sessions list ─────────────────────────────────────────────────────

class TestEmptySessions:
    def test_empty_list(self):
        r = run_bdrr_strategy([], FROZEN_PRESET, BASE_CONFIG)
        assert r == []


# ── Determinism ─────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_outcomes(self):
        s = _build_stopped_session("2026-07-02")
        r1 = run_bdrr_strategy([s], FROZEN_PRESET, BASE_CONFIG)
        r2 = run_bdrr_strategy([s], FROZEN_PRESET, BASE_CONFIG)
        # Outcomes are deterministic (UUIDs differ but outcomes match)
        assert r1[0]["outcome"] == r2[0]["outcome"]
        assert r1[0]["realized_r"] == r2[0]["realized_r"]
        assert r1[0]["exit_price_ticks"] == r2[0]["exit_price_ticks"]
        assert r1[0]["stop_price_ticks"] == r2[0]["stop_price_ticks"]


# ── Outcome enum values ─────────────────────────────────────────────────────

class TestOutcomeEnum:
    def test_all_values(self):
        assert str(Outcome.NO_VALID_SETUP) == "NO_VALID_SETUP"
        assert str(Outcome.PIPELINE_FAILURE) == "PIPELINE_FAILURE"
        assert str(Outcome.STOPPED) == "STOPPED"
        assert str(Outcome.TARGET_HIT) == "TARGET_HIT"
        assert str(Outcome.AMBIGUOUS) == "AMBIGUOUS"
        assert str(Outcome.OPEN) == "OPEN"
        assert str(Outcome.ENTRY_NOT_TRIGGERED) == "ENTRY_NOT_TRIGGERED"
