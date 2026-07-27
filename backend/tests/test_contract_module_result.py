"""Tests for canonical ModuleResult contract type (§3.2)."""

import pytest

from trading_lab.contracts.enums import EvaluationStatus
from trading_lab.contracts.module_result import ModuleResult


def _scored_kwargs():
    return dict(
        enabled=True,
        evaluation_status=EvaluationStatus.SCORED,
        score="0.85",
        weight="1.0",
        weighted_score="0.85",
        input_fields=("orb_range_ticks", "displacement_ticks"),
        error_detail=None,
        notes="Displacement quality high",
    )


def _not_run_kwargs():
    return dict(
        enabled=False,
        evaluation_status=EvaluationStatus.NOT_RUN,
        score=None,
        weight="1.0",
        weighted_score=None,
        input_fields=(),
        error_detail=None,
        notes="Module disabled",
    )


def _error_kwargs():
    return dict(
        enabled=True,
        evaluation_status=EvaluationStatus.ERROR,
        score=None,
        weight="0.5",
        weighted_score=None,
        input_fields=("volume_profile",),
        error_detail="Data feed timeout",
        notes="Failed to compute",
    )


def _data_unavailable_kwargs():
    return dict(
        enabled=True,
        evaluation_status=EvaluationStatus.DATA_UNAVAILABLE,
        score=None,
        weight="0.3",
        weighted_score=None,
        input_fields=("order_flow",),
        error_detail=None,
        notes="No L2 data",
    )


class TestValidConstruction:
    def test_scored(self):
        mr = ModuleResult(**_scored_kwargs())
        assert mr.enabled is True
        assert mr.evaluation_status == EvaluationStatus.SCORED
        assert mr.score == "0.85"
        assert mr.weight == "1.0"
        assert mr.weighted_score == "0.85"
        assert mr.input_fields == ("orb_range_ticks", "displacement_ticks")
        assert mr.error_detail is None
        assert mr.notes == "Displacement quality high"

    def test_not_run(self):
        mr = ModuleResult(**_not_run_kwargs())
        assert mr.enabled is False
        assert mr.evaluation_status == EvaluationStatus.NOT_RUN
        assert mr.score is None
        assert mr.weighted_score is None

    def test_error(self):
        mr = ModuleResult(**_error_kwargs())
        assert mr.evaluation_status == EvaluationStatus.ERROR
        assert mr.error_detail == "Data feed timeout"
        assert mr.score is None

    def test_data_unavailable(self):
        mr = ModuleResult(**_data_unavailable_kwargs())
        assert mr.evaluation_status == EvaluationStatus.DATA_UNAVAILABLE
        assert mr.score is None
        assert mr.error_detail is None


class TestScoredConstraints:
    def test_scored_requires_non_null_score(self):
        kw = _scored_kwargs()
        kw["score"] = None
        with pytest.raises(ValueError, match="score must be non-null"):
            ModuleResult(**kw)

    def test_scored_requires_non_null_weighted_score(self):
        kw = _scored_kwargs()
        kw["weighted_score"] = None
        with pytest.raises(ValueError, match="weighted_score must be non-null"):
            ModuleResult(**kw)

    def test_not_scored_rejects_non_null_score(self):
        kw = _not_run_kwargs()
        kw["score"] = "0.5"
        with pytest.raises(ValueError, match="score must be null"):
            ModuleResult(**kw)

    def test_not_scored_rejects_non_null_weighted_score(self):
        kw = _not_run_kwargs()
        kw["weighted_score"] = "0.5"
        with pytest.raises(ValueError, match="weighted_score must be null"):
            ModuleResult(**kw)


class TestErrorConstraints:
    def test_error_requires_error_detail(self):
        kw = _error_kwargs()
        kw["error_detail"] = None
        with pytest.raises(ValueError, match="error_detail must be non-null"):
            ModuleResult(**kw)

    def test_non_error_rejects_error_detail(self):
        kw = _scored_kwargs()
        kw["error_detail"] = "should not be here"
        with pytest.raises(ValueError, match="error_detail must be null"):
            ModuleResult(**kw)


class TestTypeValidation:
    def test_enabled_not_bool(self):
        kw = _scored_kwargs()
        kw["enabled"] = 1
        with pytest.raises(TypeError, match="enabled must be a bool"):
            ModuleResult(**kw)

    def test_bad_evaluation_status(self):
        kw = _scored_kwargs()
        kw["evaluation_status"] = "SCORED"
        with pytest.raises(TypeError):
            ModuleResult(**kw)

    def test_weight_not_decimal_str(self):
        kw = _scored_kwargs()
        kw["weight"] = 1.0
        with pytest.raises(TypeError, match="weight must be a Decimal"):
            ModuleResult(**kw)

    def test_invalid_decimal(self):
        kw = _scored_kwargs()
        kw["score"] = "not_a_number"
        with pytest.raises(ValueError, match="not a valid Decimal"):
            ModuleResult(**kw)

    def test_input_fields_not_tuple(self):
        kw = _scored_kwargs()
        kw["input_fields"] = ["a", "b"]
        with pytest.raises(TypeError, match="input_fields must be a tuple"):
            ModuleResult(**kw)

    def test_input_fields_non_str_element(self):
        kw = _scored_kwargs()
        kw["input_fields"] = ("a", 42)
        with pytest.raises(TypeError, match="input_fields\\[1\\] must be a str"):
            ModuleResult(**kw)

    def test_notes_not_str(self):
        kw = _scored_kwargs()
        kw["notes"] = 123
        with pytest.raises(TypeError, match="notes must be a str"):
            ModuleResult(**kw)


class TestImmutability:
    def test_frozen(self):
        mr = ModuleResult(**_scored_kwargs())
        with pytest.raises(AttributeError):
            mr.score = "0.99"


class TestToDict:
    def test_scored_shape(self):
        mr = ModuleResult(**_scored_kwargs())
        d = mr.to_dict()
        assert d == {
            "enabled": True,
            "evaluation_status": "SCORED",
            "score": "0.85",
            "weight": "1.0",
            "weighted_score": "0.85",
            "input_fields": ["orb_range_ticks", "displacement_ticks"],
            "error_detail": None,
            "notes": "Displacement quality high",
        }

    def test_input_fields_is_list(self):
        mr = ModuleResult(**_scored_kwargs())
        d = mr.to_dict()
        assert isinstance(d["input_fields"], list)


class TestScoreRange:
    def test_score_zero(self):
        kw = _scored_kwargs()
        kw["score"] = "0"
        kw["weighted_score"] = "0"
        ModuleResult(**kw)

    def test_score_zero_point_zero(self):
        kw = _scored_kwargs()
        kw["score"] = "0.0"
        kw["weighted_score"] = "0.0"
        ModuleResult(**kw)

    def test_score_one(self):
        kw = _scored_kwargs()
        kw["score"] = "1"
        kw["weighted_score"] = "1.0"
        ModuleResult(**kw)

    def test_score_one_point_zero(self):
        kw = _scored_kwargs()
        kw["score"] = "1.0"
        kw["weighted_score"] = "1.00"
        ModuleResult(**kw)

    def test_score_between_boundaries(self):
        kw = _scored_kwargs()
        kw["score"] = "0.50"
        kw["weight"] = "2.0"
        kw["weighted_score"] = "1.00"
        ModuleResult(**kw)

    def test_negative_score_rejected(self):
        kw = _scored_kwargs()
        kw["score"] = "-0.01"
        kw["weighted_score"] = "-0.01"
        with pytest.raises(ValueError, match="score must be in"):
            ModuleResult(**kw)

    def test_score_above_one_rejected(self):
        kw = _scored_kwargs()
        kw["score"] = "1.01"
        kw["weighted_score"] = "1.01"
        with pytest.raises(ValueError, match="score must be in"):
            ModuleResult(**kw)


class TestWeightedScoreConsistency:
    def test_exact_multiplication(self):
        kw = _scored_kwargs()
        kw["score"] = "0.5"
        kw["weight"] = "2"
        kw["weighted_score"] = "1.0"
        ModuleResult(**kw)

    def test_different_decimal_formatting(self):
        kw = _scored_kwargs()
        kw["score"] = "0.50"
        kw["weight"] = "2.0"
        kw["weighted_score"] = "1.000"
        ModuleResult(**kw)

    def test_incorrect_weighted_score_rejected(self):
        kw = _scored_kwargs()
        kw["score"] = "0.5"
        kw["weight"] = "2"
        kw["weighted_score"] = "0.99"
        with pytest.raises(ValueError, match="weighted_score must equal"):
            ModuleResult(**kw)

    def test_zero_score_zero_weighted(self):
        kw = _scored_kwargs()
        kw["score"] = "0"
        kw["weight"] = "1.5"
        kw["weighted_score"] = "0"
        ModuleResult(**kw)

    def test_malformed_weighted_score_rejected(self):
        kw = _scored_kwargs()
        kw["weighted_score"] = "abc"
        with pytest.raises(ValueError, match="not a valid Decimal"):
            ModuleResult(**kw)
