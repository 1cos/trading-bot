"""Tests for canonical build_trade_dataset port.

Mirrors estrategie/test_bdrr_trade_dataset.js (170 checks).
"""

import copy
import os
import re

import pytest

from trading_lab.trade_dataset import (
    build_trade_dataset,
    DATASET_SCHEMA_VERSION,
)
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
}

BASE_CONFIG = {
    "tick_size": TICK_SIZE,
    "engine_version": "bdrr_v1.0",
    "exit_target_r": 2,
}

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _ms(date_str, time_str):
    from datetime import datetime
    return int(datetime.fromisoformat(
        f"{date_str}T{time_str}:00-04:00"
    ).timestamp() * 1000)


def _mc(date_str, time_str, o, h, l, c):
    return {
        "time_ms": _ms(date_str, time_str),
        "open": o, "high": h, "low": l, "close": c,
    }


def _no_break(date_str, symbol="TEST"):
    return {
        "symbol": symbol, "date": date_str,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": _ms(date_str, "09:30"),
        "session_close_utc_ms": _ms(date_str, "16:00"),
        "timeframe": "5m",
        "candles": [
            _mc(date_str, "09:30", 99.50, 100.00, 99.30, 99.80),
            _mc(date_str, "09:35", 99.70, 99.90, 99.60, 99.75),
            _mc(date_str, "09:40", 99.75, 99.95, 99.65, 99.70),
            _mc(date_str, "09:45", 99.70, 99.85, 99.55, 99.60),
            _mc(date_str, "09:50", 99.55, 99.75, 99.40, 99.50),
        ],
    }


def _stopped(date_str, symbol="TEST"):
    return {
        "symbol": symbol, "date": date_str,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": _ms(date_str, "09:30"),
        "session_close_utc_ms": _ms(date_str, "16:00"),
        "timeframe": "5m",
        "candles": [
            _mc(date_str, "09:30", 99.50, 100.00, 99.30, 99.80),
            _mc(date_str, "09:35", 99.90, 100.50, 99.80, 100.20),
            _mc(date_str, "09:40", 100.25, 100.60, 100.10, 100.40),
            _mc(date_str, "09:45", 100.10, 100.50, 99.70, 100.40),
            _mc(date_str, "09:50", 100.30, 100.35, 99.60, 99.65),
        ],
    }


def _target_hit(date_str, symbol="TEST"):
    return {
        "symbol": symbol, "date": date_str,
        "market_timezone": "America/New_York",
        "session_open_utc_ms": _ms(date_str, "09:30"),
        "session_close_utc_ms": _ms(date_str, "16:00"),
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


def _load_real_sessions(symbol):
    csv_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "dati", f"{symbol}_5m.csv"
    )
    with open(csv_path) as f:
        content = f.read()
    candles = parse_candles_from_csv(content)
    groups = split_into_sessions(candles, "America/New_York")
    return [
        {
            "symbol": symbol, "date": g["date"],
            "market_timezone": "America/New_York",
            "session_open_utc_ms": g["candles"][0]["time_ms"],
            "session_close_utc_ms": g["candles"][-1]["time_ms"],
            "timeframe": "5m", "candles": g["candles"],
        }
        for g in groups
    ]


# ── TD1: Empty dataset ──────────────────────────────────────────────────────

class TestTD1Empty:
    def test_empty(self):
        ds = build_trade_dataset([])
        assert ds["schema_version"] == DATASET_SCHEMA_VERSION
        assert isinstance(ds["metadata"], dict)
        assert isinstance(ds["records"], list)
        assert isinstance(ds["trades"], list)
        assert len(ds["records"]) == 0
        assert len(ds["trades"]) == 0
        m = ds["metadata"]
        assert m["session_count"] == 0
        assert m["trade_count"] == 0
        assert m["symbol"] is None
        assert m["preset_id"] is None
        assert m["engine_version"] is None
        assert m["exit_target_r"] is None
        assert m["date_range"]["first"] is None
        assert m["date_range"]["last"] is None
        assert HEX64_RE.match(m["dataset_id"])
        assert isinstance(m["generated_at"], str)


# ── TD2: Single trade (STOPPED) ─────────────────────────────────────────────

class TestTD2SingleStopped:
    def test_single_stopped(self):
        results = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        assert len(ds["records"]) == 1
        assert len(ds["trades"]) == 1
        assert ds["metadata"]["session_count"] == 1
        assert ds["metadata"]["trade_count"] == 1
        assert str(ds["records"][0]["outcome"]) == "STOPPED"
        assert str(ds["trades"][0]["outcome"]) == "STOPPED"
        assert ds["trades"][0] is ds["records"][0]


# ── TD3: Multiple records — records vs trades ────────────────────────────────

class TestTD3RecordsVsTrades:
    def test_separation(self):
        sessions = [
            _no_break("2026-07-01"), _stopped("2026-07-02"),
            _target_hit("2026-07-03"), _no_break("2026-07-07"),
        ]
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        assert len(ds["records"]) == 4
        assert len(ds["trades"]) == 2
        assert ds["metadata"]["session_count"] == 4
        assert ds["metadata"]["trade_count"] == 2
        assert str(ds["records"][0]["outcome"]) == "NO_VALID_SETUP"
        assert str(ds["records"][1]["outcome"]) == "STOPPED"
        assert str(ds["records"][2]["outcome"]) == "TARGET_HIT"
        assert str(ds["records"][3]["outcome"]) == "NO_VALID_SETUP"
        assert ds["trades"][0] is ds["records"][1]
        assert ds["trades"][1] is ds["records"][2]


# ── TD4: Chronological ordering preserved ────────────────────────────────────

class TestTD4Chronological:
    def test_order_preserved(self):
        sessions = [
            _no_break("2026-06-01"), _stopped("2026-06-02"),
            _target_hit("2026-06-03"),
        ]
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        dates = [r["session_date"] for r in ds["records"]]
        assert dates == sorted(dates)


# ── TD5: Duplicate run_record_id rejected ────────────────────────────────────

class TestTD5DuplicateRunId:
    def test_duplicate_rejected(self):
        results = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)
        with pytest.raises(ValueError):
            build_trade_dataset([results[0], results[0]])


# ── TD5b: Duplicate candidate_id rejected ────────────────────────────────────

class TestTD5bDuplicateCandidateId:
    def test_duplicate_rejected(self):
        r1 = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)[0]
        r2 = run_bdrr_strategy([_target_hit("2026-07-03")], FROZEN_PRESET, BASE_CONFIG)[0]
        r2_mod = {**r2, "candidate_id": r1["candidate_id"]}
        with pytest.raises(ValueError):
            build_trade_dataset([r1, r2_mod])


# ── TD7: Metadata fields ────────────────────────────────────────────────────

class TestTD7Metadata:
    def test_metadata(self):
        sessions = [
            _no_break("2026-06-01"), _stopped("2026-06-05"),
            _target_hit("2026-06-10"),
        ]
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        m = ds["metadata"]
        assert m["schema_version"] == DATASET_SCHEMA_VERSION
        assert HEX64_RE.match(m["dataset_id"])
        assert m["preset_id"] == "bdrr_v1_initial"
        assert m["symbol"] == "TEST"
        assert m["exit_target_r"] == 2
        assert m["engine_version"] == "bdrr_v1.0"
        assert m["session_count"] == 3
        assert m["trade_count"] == 2
        assert m["date_range"]["first"] == "2026-06-01"
        assert m["date_range"]["last"] == "2026-06-10"
        assert isinstance(m["generated_at"], str)


# ── TD8: Schema validation ──────────────────────────────────────────────────

class TestTD8SchemaValidation:
    def _good(self):
        return run_bdrr_strategy(
            [_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG
        )[0]

    def _del(self, key):
        g = self._good()
        del g[key]
        return g

    def test_missing_field(self):
        with pytest.raises(ValueError):
            build_trade_dataset([self._del("run_record_id")])

    def test_bad_run_record_id(self):
        with pytest.raises(ValueError):
            build_trade_dataset([{**self._good(), "run_record_id": "not-uuid"}])

    def test_empty_symbol(self):
        with pytest.raises(ValueError):
            build_trade_dataset([{**self._good(), "symbol": ""}])

    def test_bad_session_date(self):
        with pytest.raises(ValueError):
            build_trade_dataset([{**self._good(), "session_date": "2026/07/02"}])

    def test_bad_exit_target_r(self):
        with pytest.raises(ValueError):
            build_trade_dataset([{**self._good(), "exit_target_r": 5}])

    def test_bad_detection_status(self):
        with pytest.raises(ValueError):
            build_trade_dataset([{**self._good(), "detection_status": "MAYBE"}])

    def test_failed_rules_not_array(self):
        with pytest.raises(ValueError):
            build_trade_dataset([{**self._good(), "failed_rules": None}])

    def test_bad_timestamp(self):
        with pytest.raises(ValueError):
            build_trade_dataset([{**self._good(), "entry_timestamp": "not-a-date"}])

    def test_bad_candidate_id(self):
        with pytest.raises(ValueError):
            build_trade_dataset([{**self._good(), "candidate_id": "bad-uuid"}])

    def test_null_timestamp_accepted(self):
        ds = build_trade_dataset([{**self._good(), "entry_timestamp": None}])
        assert len(ds["records"]) == 1

    def test_valid_accepted(self):
        ds = build_trade_dataset([self._good()])
        assert len(ds["records"]) == 1


# ── TD9: Invalid input type rejection ────────────────────────────────────────

class TestTD9InvalidInput:
    def test_none(self):
        with pytest.raises(TypeError):
            build_trade_dataset(None)

    def test_string(self):
        with pytest.raises(TypeError):
            build_trade_dataset("string")

    def test_number(self):
        with pytest.raises(TypeError):
            build_trade_dataset(42)

    def test_dict(self):
        with pytest.raises(TypeError):
            build_trade_dataset({})

    def test_none_element(self):
        with pytest.raises(ValueError):
            build_trade_dataset([None])

    def test_string_element(self):
        with pytest.raises(ValueError):
            build_trade_dataset(["str"])

    def test_number_element(self):
        with pytest.raises(ValueError):
            build_trade_dataset([42])

    def test_empty_object_element(self):
        with pytest.raises(ValueError):
            build_trade_dataset([{}])


# ── TD10: SPY integration ───────────────────────────────────────────────────

class TestTD10SPYIntegration:
    def test_spy_batch(self):
        sessions = _load_real_sessions("SPY")
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        assert ds["schema_version"] == DATASET_SCHEMA_VERSION
        assert ds["metadata"]["session_count"] == len(sessions)
        assert len(ds["records"]) == len(sessions)
        assert ds["metadata"]["symbol"] == "SPY"
        assert ds["metadata"]["preset_id"] == "bdrr_v1_initial"
        assert ds["metadata"]["engine_version"] == "bdrr_v1.0"
        assert ds["metadata"]["exit_target_r"] == 2
        assert ds["metadata"]["trade_count"] == 3
        assert len(ds["trades"]) == 3

        # Outcome breakdown
        outcomes = {}
        for r in ds["records"]:
            o = str(r["outcome"])
            outcomes[o] = outcomes.get(o, 0) + 1
        assert outcomes.get("NO_VALID_SETUP") == 57
        assert outcomes.get("STOPPED") == 2
        assert outcomes.get("TARGET_HIT") == 1

        # No NO_VALID_SETUP in trades
        assert all(str(r["outcome"]) != "NO_VALID_SETUP" for r in ds["trades"])


# ── TD10b: QQQ integration ──────────────────────────────────────────────────

class TestTD10bQQQIntegration:
    def test_qqq_batch(self):
        sessions = _load_real_sessions("QQQ")
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        assert ds["metadata"]["session_count"] == len(sessions)
        assert ds["metadata"]["symbol"] == "QQQ"
        assert ds["metadata"]["trade_count"] == 4
        assert len(ds["trades"]) == 4


# ── TD11: schema_version constant ────────────────────────────────────────────

class TestTD11SchemaVersion:
    def test_constant(self):
        assert DATASET_SCHEMA_VERSION == "TradeDataset/v1"

    def test_on_empty(self):
        ds = build_trade_dataset([])
        assert ds["schema_version"] == "TradeDataset/v1"


# ── TD12: date_range ─────────────────────────────────────────────────────────

class TestTD12DateRange:
    def test_date_range(self):
        sessions = [
            _no_break("2026-05-01"), _stopped("2026-05-15"),
            _target_hit("2026-05-31"),
        ]
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        assert ds["metadata"]["date_range"]["first"] == "2026-05-01"
        assert ds["metadata"]["date_range"]["last"] == "2026-05-31"


# ── TD13: trade_count ────────────────────────────────────────────────────────

class TestTD13TradeCount:
    def test_trade_count(self):
        sessions = [
            _no_break("2026-04-01"), _no_break("2026-04-02"),
            _no_break("2026-04-03"), _stopped("2026-04-04"),
            _target_hit("2026-04-05"),
        ]
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        assert ds["metadata"]["session_count"] == 5
        assert ds["metadata"]["trade_count"] == 2
        assert len(ds["trades"]) == 2
        assert ds["metadata"]["trade_count"] == len(ds["trades"])
        assert all(r["candidate_id"] is not None for r in ds["trades"])


# ── TD15: Object identity preserved ──────────────────────────────────────────

class TestTD15Identity:
    def test_identity(self):
        results = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)
        original = results[0]
        ds = build_trade_dataset(results)
        assert ds["records"][0] is original
        assert ds["trades"][0] is original


# ── TD16: Multiple null candidate_ids ────────────────────────────────────────

class TestTD16NullCandidateIds:
    def test_multiple_nulls_allowed(self):
        sessions = [_no_break(f"2026-04-0{i+1}") for i in range(5)]
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        assert len(ds["records"]) == 5
        assert len(ds["trades"]) == 0
        assert ds["metadata"]["trade_count"] == 0


# ── TD17: Out-of-order rejected ──────────────────────────────────────────────

class TestTD17OutOfOrder:
    def test_rejected(self):
        r1 = run_bdrr_strategy([_stopped("2026-07-03")], FROZEN_PRESET, BASE_CONFIG)[0]
        r2 = run_bdrr_strategy([_no_break("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)[0]
        with pytest.raises(ValueError):
            build_trade_dataset([r1, r2])


# ── TD18: PIPELINE_FAILURE rejected ─────────────────────────────────────────

class TestTD18PipelineFailure:
    def test_rejected(self):
        bad = {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": 0, "session_close_utc_ms": 0,
            "timeframe": "5m", "candles": [],
        }
        results = run_bdrr_strategy([bad], FROZEN_PRESET, BASE_CONFIG)
        assert str(results[0]["outcome"]) == "PIPELINE_FAILURE"
        assert results[0]["detection_result"] is None
        with pytest.raises(ValueError):
            build_trade_dataset(results)


# ── TD19: Invalid outcome rejected ──────────────────────────────────────────

class TestTD19InvalidOutcome:
    def test_rejected(self):
        r = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)[0]
        with pytest.raises(ValueError):
            build_trade_dataset([{**r, "outcome": "INVALID_VALUE"}])


# ── TD20: Invalid detection_status rejected ──────────────────────────────────

class TestTD20InvalidDetectionStatus:
    def test_rejected(self):
        r = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)[0]
        with pytest.raises(ValueError):
            build_trade_dataset([{**r, "detection_status": "PENDING"}])


# ── TD21: Deterministic dataset_id ───────────────────────────────────────────

class TestTD21DeterministicId:
    def test_same_input_same_id(self):
        sessions = [
            _no_break("2026-06-01"), _stopped("2026-06-02"),
            _target_hit("2026-06-03"),
        ]
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds1 = build_trade_dataset(results)
        ds2 = build_trade_dataset(results)
        assert ds1["metadata"]["dataset_id"] == ds2["metadata"]["dataset_id"]
        assert HEX64_RE.match(ds1["metadata"]["dataset_id"])
        assert len(ds1["metadata"]["dataset_id"]) == 64


# ── TD22: Different content → different dataset_id ───────────────────────────

class TestTD22DifferentContent:
    def test_different_outcome_different_id(self):
        results = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)
        original = results[0]
        ds1 = build_trade_dataset(results)
        modified = {**original, "outcome": "NO_VALID_SETUP"}
        ds2 = build_trade_dataset([modified])
        assert ds1["metadata"]["dataset_id"] != ds2["metadata"]["dataset_id"]


# ── TD22d: generated_at does not affect dataset_id ───────────────────────────

class TestTD22dGeneratedAt:
    def test_stable_id(self):
        results = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)
        ds1 = build_trade_dataset(results)
        ds2 = build_trade_dataset(results)
        assert ds1["metadata"]["dataset_id"] == ds2["metadata"]["dataset_id"]
        assert isinstance(ds1["metadata"]["generated_at"], str)


# ── TD23: Out-of-order rejected, correct order accepted ─────────────────────

class TestTD23OrderEnforced:
    def test_wrong_order_rejected(self):
        r1 = run_bdrr_strategy([_stopped("2026-07-03")], FROZEN_PRESET, BASE_CONFIG)[0]
        r2 = run_bdrr_strategy([_no_break("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)[0]
        with pytest.raises(ValueError):
            build_trade_dataset([r1, r2])

    def test_correct_order_accepted(self):
        r1 = run_bdrr_strategy([_stopped("2026-07-03")], FROZEN_PRESET, BASE_CONFIG)[0]
        r2 = run_bdrr_strategy([_no_break("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)[0]
        ds = build_trade_dataset([r2, r1])
        assert ds["records"][0]["session_date"] == "2026-07-02"
        assert ds["records"][1]["session_date"] == "2026-07-03"


# ── TD24: NO_VALID_SETUP in records but not trades ───────────────────────────

class TestTD24NoValidSetupFiltering:
    def test_filtering(self):
        sessions = [
            _no_break("2026-08-01"), _no_break("2026-08-04"),
            _stopped("2026-08-05"), _no_break("2026-08-06"),
        ]
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        assert len(ds["records"]) == 4
        assert len(ds["trades"]) == 1
        no_valid_trades = [r for r in ds["trades"] if str(r["outcome"]) == "NO_VALID_SETUP"]
        no_valid_records = [r for r in ds["records"] if str(r["outcome"]) == "NO_VALID_SETUP"]
        assert len(no_valid_trades) == 0
        assert len(no_valid_records) == 3
        assert all(r["candidate_id"] is None for r in no_valid_records)


# ── TD25: Mixed symbol rejected ─────────────────────────────────────────────

class TestTD25MixedSymbol:
    def test_rejected(self):
        r1 = run_bdrr_strategy([_stopped("2026-07-02", "SPY")], FROZEN_PRESET, BASE_CONFIG)[0]
        r2 = run_bdrr_strategy([_no_break("2026-07-03", "QQQ")], FROZEN_PRESET, BASE_CONFIG)[0]
        with pytest.raises(ValueError):
            build_trade_dataset([r1, r2])


# ── TD26: Mixed preset_id rejected ──────────────────────────────────────────

class TestTD26MixedPreset:
    def test_rejected(self):
        preset_b = {**FROZEN_PRESET, "preset_id": "bdrr_v2_alt"}
        r1 = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)[0]
        r2 = run_bdrr_strategy([_no_break("2026-07-03")], preset_b, BASE_CONFIG)[0]
        with pytest.raises(ValueError):
            build_trade_dataset([r1, r2])


# ── TD27: Mixed exit_target_r rejected ───────────────────────────────────────

class TestTD27MixedExitR:
    def test_rejected(self):
        config_r3 = {**BASE_CONFIG, "exit_target_r": 3}
        r1 = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)[0]
        r2 = run_bdrr_strategy([_no_break("2026-07-03")], FROZEN_PRESET, config_r3)[0]
        with pytest.raises(ValueError):
            build_trade_dataset([r1, r2])


# ── TD28: Mixed engine_version rejected ──────────────────────────────────────

class TestTD28MixedEngine:
    def test_rejected(self):
        config_v2 = {**BASE_CONFIG, "engine_version": "bdrr_v2.0"}
        r1 = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)[0]
        r2 = run_bdrr_strategy([_no_break("2026-07-03")], FROZEN_PRESET, config_v2)[0]
        with pytest.raises(ValueError):
            build_trade_dataset([r1, r2])


# ── TD29: Null detection_result rejected ─────────────────────────────────────

class TestTD29NullDetectionResult:
    def test_rejected(self):
        results = run_bdrr_strategy([_stopped("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)
        modified = {**results[0], "detection_result": None}
        with pytest.raises(ValueError):
            build_trade_dataset([modified])


# ── TD31: Mixed engine versions rejected ─────────────────────────────────────

class TestTD31MixedEngineVersions:
    def test_rejected(self):
        config_v2 = {**BASE_CONFIG, "engine_version": "bdrr_v2.0"}
        r1 = run_bdrr_strategy([_no_break("2026-07-02")], FROZEN_PRESET, BASE_CONFIG)[0]
        r2 = run_bdrr_strategy([_stopped("2026-07-03")], FROZEN_PRESET, config_v2)[0]
        with pytest.raises(ValueError):
            build_trade_dataset([r1, r2])


# ── TD32: Homogeneous engine versions accepted ───────────────────────────────

class TestTD32HomogeneousEngine:
    def test_accepted(self):
        sessions = [
            _no_break("2026-06-01"), _stopped("2026-06-02"),
            _target_hit("2026-06-03"), _no_break("2026-06-04"),
        ]
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        ds = build_trade_dataset(results)
        assert ds["metadata"]["engine_version"] == "bdrr_v1.0"
