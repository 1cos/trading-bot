"""Tests for Trade Dataset v2 compatibility — Rational exit_target_r.

Covers:
    - v1 records continue to work
    - v2 records with Rational exit_target_r accepted
    - v2 validation: zero, negative Rational rejected
    - Homogeneity: all-v2 Rational records accepted
    - Mixed v1/v2 rejected (different types)
    - Serialization: Rational survives to_dict in dataset
    - Dataset ID is deterministic for Rational
"""

import copy
import re

import pytest

from trading_lab.contracts.primitives import Rational
from trading_lab.trade_dataset import build_trade_dataset, DATASET_SCHEMA_VERSION
from trading_lab.strategy_runner import Outcome

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _record(
    date="2026-07-01",
    exit_target_r=2,
    outcome=Outcome.NO_VALID_SETUP,
    realized_r=None,
    candidate_id=None,
    run_id_suffix="0001",
    detection_status="INVALID",
):
    """Build a minimal valid runner result record."""
    import uuid

    rid = f"aaaaaaaa-0000-4000-8000-00000000{run_id_suffix}"
    return {
        "run_record_id": rid,
        "symbol": "TEST",
        "session_date": date,
        "preset_id": "test_preset",
        "exit_target_r": exit_target_r,
        "detection_status": detection_status,
        "failure_stage": None,
        "failed_rules": [],
        "detection_result_id": None,
        "candidate_id": candidate_id,
        "confirmation_timestamp": None,
        "entry_timestamp": None,
        "first_evaluation_timestamp": None,
        "entry_price_ticks": None,
        "stop_price_ticks": None,
        "r2_price_ticks": None,
        "r3_price_ticks": None,
        "r4_price_ticks": None,
        "outcome": outcome,
        "realized_r": realized_r,
        "highest_target_achieved": None,
        "exit_timestamp": None,
        "exit_price_ticks": None,
        "detection_result": {
            "schema_version": "DetectionResult/v1",
            "engine_version": "1.0.0",
            "status": "INVALID",
        },
        "trade_plan": None,
        "trade_outcome": None,
    }


# ── v1 unchanged ─────────────────────────────────────────────────────────────


class TestV1Unchanged:
    def test_v1_int_2_accepted(self):
        ds = build_trade_dataset([_record(exit_target_r=2)])
        assert ds["schema_version"] == DATASET_SCHEMA_VERSION
        assert len(ds["records"]) == 1

    def test_v1_int_3_accepted(self):
        ds = build_trade_dataset([_record(exit_target_r=3)])
        assert len(ds["records"]) == 1

    def test_v1_int_4_accepted(self):
        ds = build_trade_dataset([_record(exit_target_r=4)])
        assert len(ds["records"]) == 1

    def test_v1_int_5_rejected(self):
        with pytest.raises(ValueError, match="exit_target_r"):
            build_trade_dataset([_record(exit_target_r=5)])

    def test_v1_float_rejected(self):
        with pytest.raises(ValueError, match="exit_target_r"):
            build_trade_dataset([_record(exit_target_r=2.5)])


# ── v2 Rational accepted ────────────────────────────────────────────────────


class TestV2RationalAccepted:
    def test_rational_2(self):
        ds = build_trade_dataset([_record(exit_target_r=Rational(2, 1))])
        assert len(ds["records"]) == 1

    def test_rational_2_1(self):
        ds = build_trade_dataset([_record(exit_target_r=Rational(21, 10))])
        assert len(ds["records"]) == 1

    def test_rational_2_25(self):
        ds = build_trade_dataset([_record(exit_target_r=Rational(9, 4))])
        assert len(ds["records"]) == 1

    def test_rational_2_5(self):
        ds = build_trade_dataset([_record(exit_target_r=Rational(5, 2))])
        assert len(ds["records"]) == 1

    def test_rational_3_75(self):
        ds = build_trade_dataset([_record(exit_target_r=Rational(15, 4))])
        assert len(ds["records"]) == 1


# ── v2 validation ───────────────────────────────────────────────────────────


class TestV2Validation:
    def test_rational_zero_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            build_trade_dataset([_record(exit_target_r=Rational(0, 1))])

    def test_rational_negative_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            build_trade_dataset([_record(exit_target_r=Rational(-2, 1))])


# ── Homogeneity ──────────────────────────────────────────────────────────────


class TestHomogeneity:
    def test_all_v2_same_rational(self):
        """Multiple records with same Rational exit_target_r pass homogeneity."""
        r = Rational(5, 2)
        recs = [
            _record(date="2026-07-01", exit_target_r=r, run_id_suffix="0001"),
            _record(date="2026-07-02", exit_target_r=r, run_id_suffix="0002"),
            _record(date="2026-07-03", exit_target_r=r, run_id_suffix="0003"),
        ]
        ds = build_trade_dataset(recs)
        assert len(ds["records"]) == 3
        assert ds["metadata"]["exit_target_r"] == r

    def test_different_rationals_rejected(self):
        """Records with different Rational values fail homogeneity."""
        recs = [
            _record(date="2026-07-01", exit_target_r=Rational(5, 2), run_id_suffix="0001"),
            _record(date="2026-07-02", exit_target_r=Rational(9, 4), run_id_suffix="0002"),
        ]
        with pytest.raises(ValueError, match="homogeneity"):
            build_trade_dataset(recs)

    def test_mixed_int_and_rational_rejected(self):
        """v1 int and v2 Rational in same dataset fail homogeneity."""
        recs = [
            _record(date="2026-07-01", exit_target_r=2, run_id_suffix="0001"),
            _record(date="2026-07-02", exit_target_r=Rational(2, 1), run_id_suffix="0002"),
        ]
        with pytest.raises(ValueError, match="homogeneity"):
            build_trade_dataset(recs)


# ── Dataset ID ───────────────────────────────────────────────────────────────


class TestDatasetId:
    def test_deterministic_for_rational(self):
        """Same Rational records produce same dataset ID."""
        r = Rational(5, 2)
        recs = [_record(exit_target_r=r)]
        ds1 = build_trade_dataset(recs)
        ds2 = build_trade_dataset(recs)
        assert ds1["metadata"]["dataset_id"] == ds2["metadata"]["dataset_id"]
        assert HEX64_RE.match(ds1["metadata"]["dataset_id"])

    def test_different_rational_different_id(self):
        """Different Rational values produce different dataset IDs."""
        recs1 = [_record(exit_target_r=Rational(5, 2))]
        recs2 = [_record(exit_target_r=Rational(9, 4))]
        ds1 = build_trade_dataset(recs1)
        ds2 = build_trade_dataset(recs2)
        assert ds1["metadata"]["dataset_id"] != ds2["metadata"]["dataset_id"]

    def test_v1_and_v2_different_id(self):
        """int 2 and Rational(2,1) produce different dataset IDs
        (different header serialization)."""
        recs1 = [_record(exit_target_r=2)]
        recs2 = [_record(exit_target_r=Rational(2, 1))]
        ds1 = build_trade_dataset(recs1)
        ds2 = build_trade_dataset(recs2)
        # Different because header uses "2" vs "2/1"
        assert ds1["metadata"]["dataset_id"] != ds2["metadata"]["dataset_id"]


# ── Serialization ────────────────────────────────────────────────────────────


class TestSerialization:
    def test_rational_in_metadata(self):
        """Metadata preserves Rational without conversion."""
        r = Rational(5, 2)
        ds = build_trade_dataset([_record(exit_target_r=r)])
        assert ds["metadata"]["exit_target_r"] == r
        assert isinstance(ds["metadata"]["exit_target_r"], Rational)

    def test_rational_realized_r_preserved(self):
        """realized_r as Rational passes through to records."""
        r = Rational(5, 2)
        realized = Rational(133, 53)
        rec = _record(
            exit_target_r=r,
            outcome=Outcome.TARGET_HIT,
            realized_r=realized,
            detection_status="VALID",
            candidate_id="bbbbbbbb-0000-4000-8000-000000000001",
        )
        ds = build_trade_dataset([rec])
        assert ds["records"][0]["realized_r"] == realized
        assert isinstance(ds["records"][0]["realized_r"], Rational)
