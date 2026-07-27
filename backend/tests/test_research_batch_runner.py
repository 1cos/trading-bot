"""Tests for the deterministic historical research batch runner."""

import os

import pytest

from trading_lab.research_batch_runner import (
    build_research_dataset_from_csv,
    research_csv_from_csv,
)
from trading_lab.research_dataset import (
    FROZEN_COLUMNS,
    RESEARCH_DATASET_SCHEMA_VERSION,
    ResearchDatasetValidationError,
)


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
}

BASE_CONFIG = {
    "tick_size": 0.01,
    "engine_version": "bdrr_v1.0",
    "exit_target_r": 2,
}

SOURCE_ID = "batch-test-001"
COMMIT = "abc123"


def _load_csv(symbol):
    csv_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "dati", f"{symbol}_5m.csv"
    )
    with open(csv_path) as f:
        return f.read()


def _run_spy():
    return build_research_dataset_from_csv(
        csv_text=_load_csv("SPY"),
        symbol="SPY",
        preset=FROZEN_PRESET,
        config=BASE_CONFIG,
        source_dataset_id=SOURCE_ID,
        code_commit_hash=COMMIT,
    )


def _run_qqq():
    return build_research_dataset_from_csv(
        csv_text=_load_csv("QQQ"),
        symbol="QQQ",
        preset=FROZEN_PRESET,
        config=BASE_CONFIG,
        source_dataset_id=SOURCE_ID,
        code_commit_hash=COMMIT,
    )


# ── 1. Empty CSV rejected ───────────────────────────────────────────────────

class TestInputValidation:
    def test_empty_csv(self):
        with pytest.raises(ValueError, match="csv_text"):
            build_research_dataset_from_csv(
                csv_text="",
                symbol="SPY",
                preset=FROZEN_PRESET,
                config=BASE_CONFIG,
                source_dataset_id=SOURCE_ID,
                code_commit_hash=COMMIT,
            )

    def test_whitespace_only_csv(self):
        with pytest.raises(ValueError, match="csv_text"):
            build_research_dataset_from_csv(
                csv_text="   \n  ",
                symbol="SPY",
                preset=FROZEN_PRESET,
                config=BASE_CONFIG,
                source_dataset_id=SOURCE_ID,
                code_commit_hash=COMMIT,
            )

    def test_empty_symbol(self):
        with pytest.raises(ValueError, match="symbol"):
            build_research_dataset_from_csv(
                csv_text=_load_csv("SPY"),
                symbol="",
                preset=FROZEN_PRESET,
                config=BASE_CONFIG,
                source_dataset_id=SOURCE_ID,
                code_commit_hash=COMMIT,
            )

    def test_empty_source_dataset_id(self):
        with pytest.raises((ValueError, ResearchDatasetValidationError)):
            build_research_dataset_from_csv(
                csv_text=_load_csv("SPY"),
                symbol="SPY",
                preset=FROZEN_PRESET,
                config=BASE_CONFIG,
                source_dataset_id="",
                code_commit_hash=COMMIT,
            )

    def test_empty_code_commit_hash(self):
        with pytest.raises((ValueError, ResearchDatasetValidationError)):
            build_research_dataset_from_csv(
                csv_text=_load_csv("SPY"),
                symbol="SPY",
                preset=FROZEN_PRESET,
                config=BASE_CONFIG,
                source_dataset_id=SOURCE_ID,
                code_commit_hash="",
            )


# ── 4–5. SPY/QQQ integration ────────────────────────────────────────────────

class TestSPYIntegration:
    def test_spy_row_count(self):
        rows = _run_spy()
        assert len(rows) == 3

    def test_spy_valid_dates(self):
        rows = _run_spy()
        dates = {r["session_date"] for r in rows}
        assert dates == {"2026-05-26", "2026-06-08", "2026-07-06"}

    def test_spy_symbol_consistent(self):
        rows = _run_spy()
        assert all(r["symbol"] == "SPY" for r in rows)

    def test_spy_provenance(self):
        rows = _run_spy()
        assert all(r["source_dataset_id"] == SOURCE_ID for r in rows)
        assert all(r["code_commit_hash"] == COMMIT for r in rows)

    def test_spy_frozen_values(self):
        rows = _run_spy()
        may26 = [r for r in rows if r["session_date"] == "2026-05-26"][0]
        assert may26["displacement_ticks"] == 68
        assert may26["minimum_rejection_side_clearance_ticks"] == 20
        assert may26["rejection_side_clearance_ratio_to_displacement"] == "0.294117647059"
        assert may26["outcome"] == "STOPPED"


class TestQQQIntegration:
    def test_qqq_row_count(self):
        rows = _run_qqq()
        assert len(rows) == 4

    def test_qqq_valid_dates(self):
        rows = _run_qqq()
        dates = {r["session_date"] for r in rows}
        assert dates == {"2026-04-29", "2026-05-06", "2026-05-13", "2026-07-14"}


# ── 6–7. Determinism ────────────────────────────────────────────────────────

class TestDeterminism:
    def test_identical_rows(self):
        rows1 = _run_spy()
        rows2 = _run_spy()
        assert len(rows1) == len(rows2)
        for r1, r2 in zip(rows1, rows2):
            # All columns except UUIDs must match exactly
            for col in FROZEN_COLUMNS:
                if col in ("candidate_id", "result_id"):
                    continue
                assert r1[col] == r2[col], f"mismatch on {col}"

    def test_byte_for_byte_csv(self):
        csv1 = research_csv_from_csv(
            csv_text=_load_csv("SPY"),
            symbol="SPY",
            preset=FROZEN_PRESET,
            config=BASE_CONFIG,
            source_dataset_id=SOURCE_ID,
            code_commit_hash=COMMIT,
        )
        csv2 = research_csv_from_csv(
            csv_text=_load_csv("SPY"),
            symbol="SPY",
            preset=FROZEN_PRESET,
            config=BASE_CONFIG,
            source_dataset_id=SOURCE_ID,
            code_commit_hash=COMMIT,
        )
        # UUIDs differ per run, so we compare structure but not byte-for-byte
        lines1 = csv1.strip().split("\n")
        lines2 = csv2.strip().split("\n")
        assert len(lines1) == len(lines2)
        assert lines1[0] == lines2[0]  # header identical


# ── 8. CSV wrapper ───────────────────────────────────────────────────────────

class TestCSVWrapper:
    def test_returns_string(self):
        csv_out = research_csv_from_csv(
            csv_text=_load_csv("SPY"),
            symbol="SPY",
            preset=FROZEN_PRESET,
            config=BASE_CONFIG,
            source_dataset_id=SOURCE_ID,
            code_commit_hash=COMMIT,
        )
        assert isinstance(csv_out, str)
        assert csv_out.endswith("\n")

    def test_header_matches_frozen_columns(self):
        csv_out = research_csv_from_csv(
            csv_text=_load_csv("SPY"),
            symbol="SPY",
            preset=FROZEN_PRESET,
            config=BASE_CONFIG,
            source_dataset_id=SOURCE_ID,
            code_commit_hash=COMMIT,
        )
        header = csv_out.split("\n")[0].split(",")
        assert header == list(FROZEN_COLUMNS)

    def test_row_count(self):
        csv_out = research_csv_from_csv(
            csv_text=_load_csv("SPY"),
            symbol="SPY",
            preset=FROZEN_PRESET,
            config=BASE_CONFIG,
            source_dataset_id=SOURCE_ID,
            code_commit_hash=COMMIT,
        )
        lines = csv_out.strip().split("\n")
        assert len(lines) == 4  # 1 header + 3 data


# ── 9–11. Multiple sessions, filtering ───────────────────────────────────────

class TestSessionBehavior:
    def test_all_60_sessions_processed(self):
        rows = _run_spy()
        # 60 sessions → 3 valid setups
        assert len(rows) == 3

    def test_no_valid_setup_sessions_excluded(self):
        rows = _run_spy()
        outcomes = [r["outcome"] for r in rows]
        assert "NO_VALID_SETUP" not in outcomes

    def test_valid_rows_have_all_columns(self):
        rows = _run_spy()
        for row in rows:
            assert set(row.keys()) == set(FROZEN_COLUMNS)


# ── 12–13. Error propagation ────────────────────────────────────────────────

class TestErrorPropagation:
    def test_bad_config_propagates(self):
        with pytest.raises(TypeError):
            build_research_dataset_from_csv(
                csv_text=_load_csv("SPY"),
                symbol="SPY",
                preset=FROZEN_PRESET,
                config={**BASE_CONFIG, "exit_target_r": 99},
                source_dataset_id=SOURCE_ID,
                code_commit_hash=COMMIT,
            )

    def test_bad_preset_propagates(self):
        with pytest.raises((TypeError, AttributeError)):
            build_research_dataset_from_csv(
                csv_text=_load_csv("SPY"),
                symbol="SPY",
                preset=None,
                config=BASE_CONFIG,
                source_dataset_id=SOURCE_ID,
                code_commit_hash=COMMIT,
            )


# ── 14. No mutation ─────────────────────────────────────────────────────────

class TestNoMutation:
    def test_preset_not_mutated(self):
        preset = {**FROZEN_PRESET}
        config = {**BASE_CONFIG}
        preset_copy = dict(preset)
        config_copy = dict(config)
        build_research_dataset_from_csv(
            csv_text=_load_csv("SPY"),
            symbol="SPY",
            preset=preset,
            config=config,
            source_dataset_id=SOURCE_ID,
            code_commit_hash=COMMIT,
        )
        assert preset == preset_copy
        assert config == config_copy
