"""Tests for the deterministic historical research batch runner."""

import os
import re
import uuid

import pytest

from trading_lab.research_batch_runner import (
    build_research_dataset_from_csv,
    research_csv_from_csv,
    RESEARCH_ID_NAMESPACE,
)
from trading_lab.research_dataset import (
    FROZEN_COLUMNS,
    RESEARCH_DATASET_SCHEMA_VERSION,
    ResearchDatasetValidationError,
    serialize_research_csv,
)
from trading_lab.strategy_runner import run_bdrr_strategy


# ── Fixtures ──────────────────────────────────────────────────────────────────

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
}

BASE_CONFIG = {
    "tick_size": 0.01,
    "engine_version": "bdrr_v1.0",
    "exit_target_r": 2,
}

SOURCE_ID = "batch-test-001"
COMMIT = "abc123"

UUID_V5_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _load_csv(symbol):
    csv_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "dati", f"{symbol}_5m.csv"
    )
    with open(csv_path) as f:
        return f.read()


def _run_spy(**overrides):
    kw = dict(
        csv_text=_load_csv("SPY"), symbol="SPY",
        preset=FROZEN_PRESET, config=BASE_CONFIG,
        source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
    )
    kw.update(overrides)
    return build_research_dataset_from_csv(**kw)


def _run_qqq():
    return build_research_dataset_from_csv(
        csv_text=_load_csv("QQQ"), symbol="QQQ",
        preset=FROZEN_PRESET, config=BASE_CONFIG,
        source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
    )


# ── Input validation ─────────────────────────────────────────────────────────

class TestInputValidation:
    def test_empty_csv(self):
        with pytest.raises(ValueError, match="csv_text"):
            build_research_dataset_from_csv(
                csv_text="", symbol="SPY", preset=FROZEN_PRESET,
                config=BASE_CONFIG, source_dataset_id=SOURCE_ID,
                code_commit_hash=COMMIT,
            )

    def test_whitespace_only_csv(self):
        with pytest.raises(ValueError, match="csv_text"):
            build_research_dataset_from_csv(
                csv_text="   \n  ", symbol="SPY", preset=FROZEN_PRESET,
                config=BASE_CONFIG, source_dataset_id=SOURCE_ID,
                code_commit_hash=COMMIT,
            )

    def test_empty_symbol(self):
        with pytest.raises(ValueError, match="symbol"):
            build_research_dataset_from_csv(
                csv_text=_load_csv("SPY"), symbol="", preset=FROZEN_PRESET,
                config=BASE_CONFIG, source_dataset_id=SOURCE_ID,
                code_commit_hash=COMMIT,
            )

    def test_empty_source_dataset_id(self):
        with pytest.raises((ValueError, ResearchDatasetValidationError)):
            build_research_dataset_from_csv(
                csv_text=_load_csv("SPY"), symbol="SPY", preset=FROZEN_PRESET,
                config=BASE_CONFIG, source_dataset_id="",
                code_commit_hash=COMMIT,
            )

    def test_empty_code_commit_hash(self):
        with pytest.raises((ValueError, ResearchDatasetValidationError)):
            build_research_dataset_from_csv(
                csv_text=_load_csv("SPY"), symbol="SPY", preset=FROZEN_PRESET,
                config=BASE_CONFIG, source_dataset_id=SOURCE_ID,
                code_commit_hash="",
            )


# ── Full determinism: rows ───────────────────────────────────────────────────

class TestRowDeterminism:
    def test_identical_rows(self):
        """Two independent runs produce exactly equal rows including IDs."""
        rows1 = _run_spy()
        rows2 = _run_spy()
        assert len(rows1) == len(rows2)
        for r1, r2 in zip(rows1, rows2):
            for col in FROZEN_COLUMNS:
                assert r1[col] == r2[col], f"mismatch on {col}"

    def test_candidate_id_equal(self):
        rows1 = _run_spy()
        rows2 = _run_spy()
        for r1, r2 in zip(rows1, rows2):
            assert r1["candidate_id"] == r2["candidate_id"]

    def test_result_id_equal(self):
        rows1 = _run_spy()
        rows2 = _run_spy()
        for r1, r2 in zip(rows1, rows2):
            assert r1["result_id"] == r2["result_id"]


# ── Full determinism: CSV ────────────────────────────────────────────────────

class TestCSVDeterminism:
    def test_byte_for_byte_csv(self):
        """Two independent full pipeline runs produce identical CSV."""
        csv1 = research_csv_from_csv(
            csv_text=_load_csv("SPY"), symbol="SPY",
            preset=FROZEN_PRESET, config=BASE_CONFIG,
            source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        csv2 = research_csv_from_csv(
            csv_text=_load_csv("SPY"), symbol="SPY",
            preset=FROZEN_PRESET, config=BASE_CONFIG,
            source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert csv1 == csv2


# ── UUID v5 format ───────────────────────────────────────────────────────────

class TestUUIDFormat:
    def test_result_id_is_uuid_v5(self):
        rows = _run_spy()
        for r in rows:
            assert UUID_V5_RE.match(r["result_id"]), r["result_id"]
            assert uuid.UUID(r["result_id"]).version == 5

    def test_candidate_id_is_uuid_v5(self):
        rows = _run_spy()
        for r in rows:
            assert UUID_V5_RE.match(r["candidate_id"]), r["candidate_id"]
            assert uuid.UUID(r["candidate_id"]).version == 5


# ── Frozen test vectors ──────────────────────────────────────────────────────

class TestFrozenVectors:
    def test_spy_may26_result_id(self):
        rows = _run_spy()
        may26 = [r for r in rows if r["session_date"] == "2026-05-26"][0]
        assert may26["result_id"] == "798da418-b84e-5437-8c82-fc3409444b19"

    def test_spy_may26_candidate_id(self):
        rows = _run_spy()
        may26 = [r for r in rows if r["session_date"] == "2026-05-26"][0]
        assert may26["candidate_id"] == "75fc48a0-e077-5126-8af6-1ad8337199c8"


# ── Identity independence from provenance ────────────────────────────────────

class TestProvenanceIndependence:
    def test_different_source_dataset_id_same_ids(self):
        rows1 = _run_spy(source_dataset_id="source-A")
        rows2 = _run_spy(source_dataset_id="source-B")
        for r1, r2 in zip(rows1, rows2):
            assert r1["result_id"] == r2["result_id"]
            assert r1["candidate_id"] == r2["candidate_id"]

    def test_different_code_commit_hash_same_ids(self):
        rows1 = _run_spy(code_commit_hash="aaa111")
        rows2 = _run_spy(code_commit_hash="bbb222")
        for r1, r2 in zip(rows1, rows2):
            assert r1["result_id"] == r2["result_id"]
            assert r1["candidate_id"] == r2["candidate_id"]


# ── Semantic differentiation ─────────────────────────────────────────────────

class TestSemanticDifferentiation:
    def test_different_symbol_different_result_id(self):
        spy = _run_spy()
        qqq = _run_qqq()
        spy_ids = {r["result_id"] for r in spy}
        qqq_ids = {r["result_id"] for r in qqq}
        assert spy_ids.isdisjoint(qqq_ids)

    def test_different_exit_target_changes_candidate_not_result(self):
        rows_r2 = _run_spy(config={**BASE_CONFIG, "exit_target_r": 2})
        rows_r3 = _run_spy(config={**BASE_CONFIG, "exit_target_r": 3})
        # result_id should be same (detection identity unchanged)
        for r2, r3 in zip(rows_r2, rows_r3):
            assert r2["result_id"] == r3["result_id"]
        # candidate_id should differ (trade plan identity changed)
        for r2, r3 in zip(rows_r2, rows_r3):
            assert r2["candidate_id"] != r3["candidate_id"]


# ── No collisions ────────────────────────────────────────────────────────────

class TestNoCollisions:
    def test_all_spy_qqq_ids_unique(self):
        spy = _run_spy()
        qqq = _run_qqq()
        all_result_ids = [r["result_id"] for r in spy] + [r["result_id"] for r in qqq]
        all_candidate_ids = [r["candidate_id"] for r in spy] + [r["candidate_id"] for r in qqq]
        assert len(set(all_result_ids)) == len(all_result_ids)
        assert len(set(all_candidate_ids)) == len(all_candidate_ids)


# ── Production default unchanged ─────────────────────────────────────────────

class TestProductionDefault:
    def test_production_runner_still_random_uuid4(self):
        """Normal Strategy Runner without injection still uses random UUIDs."""
        from trading_lab.csv_parser import parse_candles_from_csv
        from trading_lab.session_split import split_into_sessions

        with open(os.path.join(os.path.dirname(__file__),
                  "..", "..", "dati", "SPY_5m.csv")) as f:
            csv_text = f.read()
        candles = parse_candles_from_csv(csv_text)
        groups = split_into_sessions(candles, "America/New_York")
        sessions = [{
            "symbol": "SPY", "date": g["date"],
            "market_timezone": "America/New_York",
            "session_open_utc_ms": g["candles"][0]["time_ms"],
            "session_close_utc_ms": g["candles"][-1]["time_ms"],
            "timeframe": "5m", "candles": g["candles"],
        } for g in groups]

        r1 = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        r2 = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)

        v1 = [r for r in r1 if r["detection_status"] == "VALID"]
        v2 = [r for r in r2 if r["detection_status"] == "VALID"]
        # Production IDs should differ (random)
        assert v1[0]["candidate_id"] != v2[0]["candidate_id"]
        # And should be v4
        u = uuid.UUID(v1[0]["candidate_id"])
        assert u.version == 4


# ── Non-exported nondeterministic fields ─────────────────────────────────────

class TestNonExportedFields:
    def test_run_record_id_not_in_csv(self):
        assert "run_record_id" not in FROZEN_COLUMNS

    def test_produced_at_not_in_csv(self):
        assert "produced_at" not in FROZEN_COLUMNS

    def test_no_timestamps_in_research_csv(self):
        """Verify no nondeterministic timestamps appear in CSV output."""
        csv1 = research_csv_from_csv(
            csv_text=_load_csv("SPY"), symbol="SPY",
            preset=FROZEN_PRESET, config=BASE_CONFIG,
            source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        csv2 = research_csv_from_csv(
            csv_text=_load_csv("SPY"), symbol="SPY",
            preset=FROZEN_PRESET, config=BASE_CONFIG,
            source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert csv1 == csv2


# ── Integration ──────────────────────────────────────────────────────────────

class TestIntegration:
    def test_spy_row_count(self):
        rows = _run_spy()
        assert len(rows) == 3

    def test_qqq_row_count(self):
        rows = _run_qqq()
        assert len(rows) == 2  # sequence_validator invalidates 2 former false positives

    def test_all_columns_present(self):
        rows = _run_spy()
        for row in rows:
            assert set(row.keys()) == set(FROZEN_COLUMNS)

    def test_csv_wrapper(self):
        csv_out = research_csv_from_csv(
            csv_text=_load_csv("SPY"), symbol="SPY",
            preset=FROZEN_PRESET, config=BASE_CONFIG,
            source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert isinstance(csv_out, str)
        assert csv_out.endswith("\n")
        lines = csv_out.strip().split("\n")
        assert len(lines) == 4  # header + 3 data


# ── Error propagation ────────────────────────────────────────────────────────

class TestErrorPropagation:
    def test_bad_config_propagates(self):
        with pytest.raises(TypeError):
            build_research_dataset_from_csv(
                csv_text=_load_csv("SPY"), symbol="SPY",
                preset=FROZEN_PRESET,
                config={**BASE_CONFIG, "exit_target_r": 99},
                source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )


# ── No mutation ──────────────────────────────────────────────────────────────

class TestNoMutation:
    def test_preset_not_mutated(self):
        preset = {**FROZEN_PRESET}
        config = {**BASE_CONFIG}
        preset_copy = dict(preset)
        config_copy = dict(config)
        build_research_dataset_from_csv(
            csv_text=_load_csv("SPY"), symbol="SPY",
            preset=preset, config=config,
            source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert preset == preset_copy
        assert config == config_copy
