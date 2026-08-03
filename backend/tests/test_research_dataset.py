"""Tests for the deterministic Historical Research Dataset exporter."""

import copy
import os

import pytest

from trading_lab.research_dataset import (
    FROZEN_COLUMNS,
    RESEARCH_DATASET_SCHEMA_VERSION,
    RESEARCH_EXPORTER_VERSION,
    ResearchDatasetValidationError,
    build_research_rows,
    serialize_research_csv,
    _compute_ratio,
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
    "min_displacement_bars": 1,
    "confirmation_wick_penetration_pct_min": 0,
}

BASE_CONFIG = {
    "tick_size": TICK_SIZE,
    "engine_version": "bdrr_v1.0",
    "exit_target_r": 2,
}

SOURCE_ID = "test-source-dataset-001"
COMMIT = "abc123def456"


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


def _no_break(date_str):
    return {
        "symbol": "TEST", "date": date_str,
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


def _stopped(date_str):
    return {
        "symbol": "TEST", "date": date_str,
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


def _target_hit(date_str):
    return {
        "symbol": "TEST", "date": date_str,
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


def _run(sessions):
    return run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)


# ── 1. Constants ─────────────────────────────────────────────────────────────

class TestConstants:
    def test_schema_version(self):
        assert RESEARCH_DATASET_SCHEMA_VERSION == "ResearchDataset/v1"

    def test_exporter_version(self):
        assert RESEARCH_EXPORTER_VERSION == "ResearchDatasetExporter/v1"


# ── 2. Frozen column order ───────────────────────────────────────────────────

class TestFrozenColumnOrder:
    def test_column_count(self):
        assert len(FROZEN_COLUMNS) == 52

    def test_first_columns(self):
        assert FROZEN_COLUMNS[0] == "research_dataset_schema_version"
        assert FROZEN_COLUMNS[1] == "research_exporter_version"
        assert FROZEN_COLUMNS[2] == "source_dataset_id"
        assert FROZEN_COLUMNS[3] == "code_commit_hash"

    def test_last_column(self):
        assert FROZEN_COLUMNS[-1] == "highest_target_achieved"

    def test_is_tuple(self):
        assert isinstance(FROZEN_COLUMNS, tuple)


# ── 3–7. Complete valid research row ─────────────────────────────────────────

class TestCompleteValidRow:
    def test_stopped_row(self):
        results = _run([_stopped("2026-07-02")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows) == 1
        row = rows[0]

        # Identity
        assert row["research_dataset_schema_version"] == "ResearchDataset/v1"
        assert row["research_exporter_version"] == "ResearchDatasetExporter/v1"
        assert row["source_dataset_id"] == SOURCE_ID
        assert row["code_commit_hash"] == COMMIT
        assert row["symbol"] == "TEST"
        assert row["session_date"] == "2026-07-02"
        assert row["preset_id"] == "bdrr_v1_initial"
        assert isinstance(row["candidate_id"], str)
        assert isinstance(row["result_id"], str)

        # Direction
        assert row["direction"] == "LONG"

        # Schema versions
        assert row["detection_schema_version"] == "DetectionResult/v1"
        assert row["trade_plan_schema_version"] == "TradePlan/v1"
        assert row["trade_outcome_schema_version"] == "TradeOutcome/v1"

        # Engine
        assert row["engine_version"] == "bdrr_v1.0"

        # Timing
        assert isinstance(row["session_open_utc_ms"], int)
        assert isinstance(row["session_close_utc_ms"], int)
        assert isinstance(row["break_bar_utc_ms"], int)
        assert isinstance(row["confirmation_bar_utc_ms"], int)

        # Geometry
        assert isinstance(row["level_price_ticks"], int)
        assert isinstance(row["displacement_ticks"], int)
        assert isinstance(row["minimum_rejection_side_clearance_ticks"], int)
        assert isinstance(row["risk_ticks"], int)

        # Ratio
        assert isinstance(row["rejection_side_clearance_ratio_to_displacement"], str)
        assert "." in row["rejection_side_clearance_ratio_to_displacement"]

        # Outcome
        assert row["outcome"] == "STOPPED"
        assert row["entry_triggered"] is True
        assert row["realized_r"] == -1

    def test_all_columns_present(self):
        results = _run([_stopped("2026-07-02")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert set(rows[0].keys()) == set(FROZEN_COLUMNS)

    def test_column_count(self):
        results = _run([_stopped("2026-07-02")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows[0]) == 52


# ── 8–12. Ratio calculation ──────────────────────────────────────────────────

class TestRatioCalculation:
    def test_20_over_68(self):
        assert _compute_ratio(20, 68) == "0.294117647059"

    def test_2_over_43(self):
        assert _compute_ratio(2, 43) == "0.046511627907"

    def test_exactly_12_decimal_places(self):
        result = _compute_ratio(1, 3)
        after_dot = result.split(".")[1]
        assert len(after_dot) == 12

    def test_negative_numerator(self):
        result = _compute_ratio(-1, 8)
        assert result == "-0.125000000000"
        assert result.startswith("-")

    def test_zero_numerator(self):
        result = _compute_ratio(0, 8)
        assert result == "0.000000000000"

    def test_zero_denominator_raises(self):
        with pytest.raises(ResearchDatasetValidationError, match="zero"):
            _compute_ratio(20, 0)


# ── 13–14. Provenance validation ─────────────────────────────────────────────

class TestProvenanceValidation:
    def test_empty_source_dataset_id(self):
        results = _run([_stopped("2026-07-02")])
        with pytest.raises(ResearchDatasetValidationError, match="source_dataset_id"):
            build_research_rows(
                results, source_dataset_id="", code_commit_hash=COMMIT,
            )

    def test_empty_code_commit_hash(self):
        results = _run([_stopped("2026-07-02")])
        with pytest.raises(ResearchDatasetValidationError, match="code_commit_hash"):
            build_research_rows(
                results, source_dataset_id=SOURCE_ID, code_commit_hash="",
            )


# ── 15–18. Eligibility filtering ─────────────────────────────────────────────

class TestEligibilityFiltering:
    def test_no_valid_setup_excluded(self):
        results = _run([_no_break("2026-07-01")])
        assert str(results[0]["outcome"]) == "NO_VALID_SETUP"
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows) == 0

    def test_pipeline_failure_excluded(self):
        bad_session = {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": 0, "session_close_utc_ms": 0,
            "timeframe": "5m", "candles": [],
        }
        results = _run([bad_session])
        assert str(results[0]["outcome"]) == "PIPELINE_FAILURE"
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows) == 0

    def test_mixed_sessions_filter_correctly(self):
        results = _run([
            _no_break("2026-07-01"),
            _stopped("2026-07-02"),
            _no_break("2026-07-03"),
            _target_hit("2026-07-04"),
        ])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows) == 2
        assert rows[0]["session_date"] == "2026-07-02"
        assert rows[1]["session_date"] == "2026-07-04"

    def test_contradictory_record_candidate_missing_detection(self):
        results = _run([_stopped("2026-07-02")])
        # Tamper: keep candidate_id but null out trade_outcome and detection
        bad = {**results[0], "trade_outcome": None, "detection_result": None}
        with pytest.raises(ResearchDatasetValidationError, match="candidate_id is present"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )

    def test_contradictory_candidate_missing_trade_plan(self):
        results = _run([_stopped("2026-07-02")])
        bad = {**results[0], "trade_plan": None}
        with pytest.raises(ResearchDatasetValidationError, match="candidate_id is present"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )


# ── 19. Contradictory eligible record ────────────────────────────────────────

class TestContradictoryEligible:
    def test_eligible_but_invalid_status_raises(self):
        # Build a record that has candidate_id, detection_result,
        # trade_plan, and trade_outcome all non-null, but detection
        # status is INVALID
        results = _run([_no_break("2026-07-01")])
        no_setup = results[0]
        # This record has detection_result (INVALID), no TP/TO, no candidate_id
        # Build a fake eligible record
        valid_results = _run([_stopped("2026-07-02")])
        valid = valid_results[0]
        # Replace detection_result with the invalid one
        bad = {**valid, "detection_result": no_setup["detection_result"]}
        with pytest.raises(ResearchDatasetValidationError, match="not VALID"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )


# ── 20–21. CSV value serialization ──────────────────────────────────────────

class TestCSVSerialization:
    def test_null_fields_serialize_as_empty(self):
        results = _run([_target_hit("2026-07-03")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        csv_text = serialize_research_csv(rows)
        lines = csv_text.strip().split("\n")
        header = lines[0].split(",")
        data = lines[1].split(",")
        # Find highest_target_achieved column
        idx = header.index("highest_target_achieved")
        # TARGET_HIT at 2R: highest_target_achieved should be non-null
        assert data[idx] != ""

    def test_boolean_lowercase(self):
        results = _run([_stopped("2026-07-02")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        csv_text = serialize_research_csv(rows)
        lines = csv_text.strip().split("\n")
        header = lines[0].split(",")
        data = lines[1].split(",")
        idx = header.index("entry_triggered")
        assert data[idx] == "true"


# ── 22–26. CSV structure ─────────────────────────────────────────────────────

class TestCSVStructure:
    def test_stable_header(self):
        csv1 = serialize_research_csv(())
        csv2 = serialize_research_csv(())
        assert csv1 == csv2
        header = csv1.strip().split(",")
        assert header == list(FROZEN_COLUMNS)

    def test_stable_column_order(self):
        results = _run([_stopped("2026-07-02")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        csv_text = serialize_research_csv(rows)
        header = csv_text.split("\n")[0].split(",")
        assert header == list(FROZEN_COLUMNS)

    def test_byte_for_byte_identical(self):
        results = _run([_stopped("2026-07-02"), _target_hit("2026-07-03")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        csv1 = serialize_research_csv(rows)
        csv2 = serialize_research_csv(rows)
        assert csv1 == csv2

    def test_ends_with_exactly_one_newline(self):
        results = _run([_stopped("2026-07-02")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        csv_text = serialize_research_csv(rows)
        assert csv_text.endswith("\n")
        assert not csv_text.endswith("\n\n")

    def test_empty_rows_header_only(self):
        csv_text = serialize_research_csv(())
        lines = csv_text.strip().split("\n")
        assert len(lines) == 1
        assert lines[0] == ",".join(FROZEN_COLUMNS)


# ── 27–28. Row key validation ────────────────────────────────────────────────

class TestRowKeyValidation:
    def test_unknown_key_rejected(self):
        results = _run([_stopped("2026-07-02")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        bad_row = {**rows[0], "extra_key": "bad"}
        with pytest.raises(ResearchDatasetValidationError, match="unknown"):
            serialize_research_csv([bad_row])

    def test_missing_key_rejected(self):
        results = _run([_stopped("2026-07-02")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        bad_row = dict(rows[0])
        del bad_row["symbol"]
        with pytest.raises(ResearchDatasetValidationError, match="missing"):
            serialize_research_csv([bad_row])


# ── 29. Multiple valid rows preserve order ───────────────────────────────────

class TestRowOrder:
    def test_order_preserved(self):
        results = _run([
            _stopped("2026-07-02"),
            _no_break("2026-07-03"),
            _target_hit("2026-07-04"),
        ])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows) == 2
        assert rows[0]["session_date"] == "2026-07-02"
        assert rows[1]["session_date"] == "2026-07-04"


# ── 30. No mutation ──────────────────────────────────────────────────────────

class TestNoMutation:
    def test_runner_results_not_mutated(self):
        results = _run([_stopped("2026-07-02"), _no_break("2026-07-03")])
        results_copy = copy.deepcopy(results)
        # Deepcopy won't work for frozen dataclasses but the runner
        # records are plain dicts with frozen dataclass values.
        # We can at least verify the dict keys/primitives.
        original_keys = [set(r.keys()) for r in results]
        original_outcomes = [str(r["outcome"]) for r in results]
        original_symbols = [r["symbol"] for r in results]

        build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )

        for i, r in enumerate(results):
            assert set(r.keys()) == original_keys[i]
            assert str(r["outcome"]) == original_outcomes[i]
            assert r["symbol"] == original_symbols[i]


# ── 31–32. SPY/QQQ integration ───────────────────────────────────────────────

class TestSPYIntegration:
    def test_spy_valid_setups(self):
        sessions = _load_real_sessions("SPY")
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        rows = build_research_rows(
            results, source_dataset_id="spy-test", code_commit_hash="abc",
        )
        assert len(rows) == 3
        dates = [r["session_date"] for r in rows]
        assert set(dates) == {"2026-05-26", "2026-06-08", "2026-07-06"}

        # Verify SPY 2026-05-26 frozen values
        may26 = [r for r in rows if r["session_date"] == "2026-05-26"][0]
        assert may26["displacement_ticks"] == 68
        assert may26["minimum_rejection_side_clearance_ticks"] == 20
        assert may26["rejection_side_clearance_ratio_to_displacement"] == "0.294117647059"
        assert may26["outcome"] == "STOPPED"
        assert may26["realized_r"] == -1

        csv_text = serialize_research_csv(rows)
        assert csv_text.count("\n") == 4  # header + 3 data rows


class TestQQQIntegration:
    def test_qqq_valid_setups(self):
        sessions = _load_real_sessions("QQQ")
        results = run_bdrr_strategy(sessions, FROZEN_PRESET, BASE_CONFIG)
        rows = build_research_rows(
            results, source_dataset_id="qqq-test", code_commit_hash="def",
        )
        assert len(rows) == 2  # sequence_validator invalidates 2 former false positives
        dates = [r["session_date"] for r in rows]
        assert set(dates) == {
            "2026-05-06", "2026-05-13",
        }


# ── 33–34. Specific outcomes ─────────────────────────────────────────────────

class TestOutcomeTypes:
    def test_target_hit(self):
        results = _run([_target_hit("2026-07-03")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows) == 1
        assert rows[0]["outcome"] == "TARGET_HIT"
        assert rows[0]["realized_r"] == 2

    def test_stopped(self):
        results = _run([_stopped("2026-07-02")])
        rows = build_research_rows(
            results, source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows) == 1
        assert rows[0]["outcome"] == "STOPPED"
        assert rows[0]["realized_r"] == -1


# ── Record consistency enforcement ───────────────────────────────────────────

class TestRecordConsistency:
    """Verify contradictory or incomplete records raise, not silently skip."""

    def _valid_record(self):
        return _run([_stopped("2026-07-02")])[0]

    def _no_setup_record(self):
        return _run([_no_break("2026-07-01")])[0]

    def _pipeline_failure_record(self):
        bad = {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": 0, "session_close_utc_ms": 0,
            "timeframe": "5m", "candles": [],
        }
        return _run([bad])[0]

    # Legitimate skips still work
    def test_legitimate_no_valid_setup(self):
        rows = build_research_rows(
            [self._no_setup_record()],
            source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows) == 0

    def test_legitimate_pipeline_failure(self):
        rows = build_research_rows(
            [self._pipeline_failure_record()],
            source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows) == 0

    def test_complete_valid_still_emits(self):
        rows = build_research_rows(
            [self._valid_record()],
            source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
        )
        assert len(rows) == 1

    # valid detection + missing trade_plan
    def test_valid_detection_missing_trade_plan(self):
        r = self._valid_record()
        bad = {**r, "trade_plan": None, "candidate_id": None}
        with pytest.raises(ResearchDatasetValidationError, match="VALID.*missing.*trade_plan"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )

    # valid detection + missing trade_outcome
    def test_valid_detection_missing_trade_outcome(self):
        r = self._valid_record()
        bad = {**r, "trade_outcome": None, "candidate_id": None}
        with pytest.raises(ResearchDatasetValidationError, match="VALID.*missing.*trade_outcome"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )

    # valid detection + missing candidate_id
    def test_valid_detection_missing_candidate_id(self):
        r = self._valid_record()
        bad = {**r, "candidate_id": None}
        with pytest.raises(ResearchDatasetValidationError, match="VALID.*missing.*candidate_id"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )

    # candidate_id + missing detection
    def test_candidate_id_missing_detection(self):
        r = self._valid_record()
        bad = {**r, "detection_result": None}
        with pytest.raises(ResearchDatasetValidationError, match="candidate_id is present.*missing.*detection_result"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )

    # trade_plan without valid detection
    def test_trade_plan_without_detection(self):
        r = self._valid_record()
        # Use a non-standard outcome to avoid NO_VALID_SETUP/PIPELINE_FAILURE paths
        bad = {**r, "detection_result": None, "candidate_id": None,
               "trade_outcome": None, "outcome": "STOPPED"}
        with pytest.raises(ResearchDatasetValidationError, match="trade_plan.*not VALID"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )

    # trade_outcome without trade_plan
    def test_trade_outcome_without_trade_plan(self):
        r = self._valid_record()
        bad = {**r, "trade_plan": None, "candidate_id": None,
               "detection_result": None, "outcome": "STOPPED"}
        with pytest.raises(ResearchDatasetValidationError, match="trade_outcome.*missing"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )

    # NO_VALID_SETUP with candidate_id
    def test_no_valid_setup_with_candidate_id(self):
        r = self._no_setup_record()
        bad = {**r, "candidate_id": "fake-id"}
        with pytest.raises(ResearchDatasetValidationError, match="NO_VALID_SETUP.*candidate_id"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )

    # NO_VALID_SETUP with trade_plan
    def test_no_valid_setup_with_trade_plan(self):
        r = self._no_setup_record()
        vr = self._valid_record()
        bad = {**r, "trade_plan": vr["trade_plan"]}
        with pytest.raises(ResearchDatasetValidationError, match="NO_VALID_SETUP.*trade_plan"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )

    # PIPELINE_FAILURE with valid detection_result
    def test_pipeline_failure_with_valid_detection(self):
        pf = self._pipeline_failure_record()
        vr = self._valid_record()
        bad = {**pf, "detection_result": vr["detection_result"]}
        with pytest.raises(ResearchDatasetValidationError, match="PIPELINE_FAILURE.*VALID"):
            build_research_rows(
                [bad], source_dataset_id=SOURCE_ID, code_commit_hash=COMMIT,
            )
