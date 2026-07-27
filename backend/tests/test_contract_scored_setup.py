"""Tests for canonical ScoredSetup/v1 contract type (§3.6)."""

import pytest

from trading_lab.contracts.enums import (
    ConfluenceStatus,
    EvaluationStatus,
    QualityGrade,
)
from trading_lab.contracts.confluence_result import ConfluenceResult
from trading_lab.contracts.module_result import ModuleResult
from trading_lab.contracts.scored_setup import ScoredSetup

# Re-use SetupCandidate fixture from its test module
from test_contract_setup_candidate import _valid_kwargs as _sc_kwargs
from test_contract_setup_candidate import SetupCandidate


def _core_module():
    return ModuleResult(
        enabled=True,
        evaluation_status=EvaluationStatus.SCORED,
        score="0.80",
        weight="1.0",
        weighted_score="0.80",
        input_fields=("displacement_ticks",),
        error_detail=None,
        notes="Good displacement",
    )


def _contextual_module():
    return ModuleResult(
        enabled=True,
        evaluation_status=EvaluationStatus.SCORED,
        score="0.60",
        weight="0.5",
        weighted_score="0.30",
        input_fields=("volume",),
        error_detail=None,
        notes="Average volume",
    )


def _confluence():
    return ConfluenceResult(
        evaluation_status=EvaluationStatus.SCORED,
        score="0.72",
        confluence_status=ConfluenceStatus.CONFIRMING,
        data_source="PDH_PDL_v1",
        data_timestamp_utc_ms=1700000000000,
        input_fields=("pdh_price",),
    )


def _valid_kwargs():
    return dict(
        schema_version="ScoredSetup/v1",
        scored_id="22222222-3333-4444-9555-666666666666",
        scored_at="2026-01-02T14:02:00.000Z",
        scorer_version="scorer_v0.1",
        setup=SetupCandidate(**_sc_kwargs()),
        core_quality_score="0.80",
        core_quality_grade=QualityGrade.A,
        core_grade_thresholds={
            "A_PLUS": "0.90", "A": "0.75", "B": "0.55",
            "C": "0.35", "D": "0.00",
        },
        core_module_scores={"displacement_quality": _core_module()},
        core_weights_snapshot={"displacement_quality": "1.0"},
        contextual_module_results={"volume_profile": _contextual_module()},
        contextual_weights_snapshot={"volume_profile": "0.5"},
        confluence_result=_confluence(),
        module_features={"disp_ticks_raw": 50},
    )


class TestValidConstruction:
    def test_all_fields(self):
        ss = ScoredSetup(**_valid_kwargs())
        assert ss.schema_version == "ScoredSetup/v1"
        assert ss.scored_id == "22222222-3333-4444-9555-666666666666"
        assert ss.scorer_version == "scorer_v0.1"
        assert ss.core_quality_score == "0.80"
        assert ss.core_quality_grade == QualityGrade.A
        assert isinstance(ss.setup, SetupCandidate)
        assert isinstance(ss.confluence_result, ConfluenceResult)

    def test_null_confluence(self):
        kw = _valid_kwargs()
        kw["confluence_result"] = None
        ss = ScoredSetup(**kw)
        assert ss.confluence_result is None

    def test_null_score_and_grade(self):
        kw = _valid_kwargs()
        kw["core_quality_score"] = None
        kw["core_quality_grade"] = None
        ss = ScoredSetup(**kw)
        assert ss.core_quality_score is None
        assert ss.core_quality_grade is None


class TestSchemaVersion:
    def test_wrong_schema(self):
        kw = _valid_kwargs()
        kw["schema_version"] = "ScoredSetup/v2"
        with pytest.raises(ValueError, match="ScoredSetup/v1"):
            ScoredSetup(**kw)


class TestScoreGradeConsistency:
    def test_score_non_null_grade_null(self):
        kw = _valid_kwargs()
        kw["core_quality_grade"] = None
        with pytest.raises(ValueError, match="core_quality_grade must be non-null"):
            ScoredSetup(**kw)

    def test_score_null_grade_non_null(self):
        kw = _valid_kwargs()
        kw["core_quality_score"] = None
        with pytest.raises(ValueError, match="core_quality_grade must be null"):
            ScoredSetup(**kw)


class TestWeightsSnapshotParity:
    def test_core_weights_missing_key(self):
        kw = _valid_kwargs()
        kw["core_weights_snapshot"] = {}
        with pytest.raises(ValueError, match="keys must match"):
            ScoredSetup(**kw)

    def test_core_weights_extra_key(self):
        kw = _valid_kwargs()
        kw["core_weights_snapshot"]["extra"] = "1.0"
        with pytest.raises(ValueError, match="keys must match"):
            ScoredSetup(**kw)

    def test_contextual_weights_mismatch(self):
        kw = _valid_kwargs()
        kw["contextual_weights_snapshot"] = {"wrong_key": "0.5"}
        with pytest.raises(ValueError, match="keys must match"):
            ScoredSetup(**kw)


class TestTypeValidation:
    def test_setup_not_candidate(self):
        kw = _valid_kwargs()
        kw["setup"] = {"schema_version": "SetupCandidate/v1"}
        with pytest.raises(TypeError, match="SetupCandidate"):
            ScoredSetup(**kw)

    def test_core_module_not_module_result(self):
        kw = _valid_kwargs()
        kw["core_module_scores"] = {"m": "not a module result"}
        with pytest.raises(TypeError, match="ModuleResult"):
            ScoredSetup(**kw)

    def test_bad_grade_type(self):
        kw = _valid_kwargs()
        kw["core_quality_grade"] = "A"
        with pytest.raises(TypeError, match="QualityGrade"):
            ScoredSetup(**kw)

    def test_empty_scored_id(self):
        kw = _valid_kwargs()
        kw["scored_id"] = ""
        with pytest.raises(ValueError, match="non-empty"):
            ScoredSetup(**kw)

    def test_empty_scorer_version(self):
        kw = _valid_kwargs()
        kw["scorer_version"] = ""
        with pytest.raises(ValueError, match="non-empty"):
            ScoredSetup(**kw)

    def test_confluence_wrong_type(self):
        kw = _valid_kwargs()
        kw["confluence_result"] = {"score": "0.5"}
        with pytest.raises(TypeError, match="ConfluenceResult"):
            ScoredSetup(**kw)


class TestImmutability:
    def test_frozen(self):
        ss = ScoredSetup(**_valid_kwargs())
        with pytest.raises(AttributeError):
            ss.scored_id = "new-id"


class TestToDict:
    def test_shape(self):
        ss = ScoredSetup(**_valid_kwargs())
        d = ss.to_dict()
        assert d["schema_version"] == "ScoredSetup/v1"
        assert d["scored_id"] == "22222222-3333-4444-9555-666666666666"
        assert d["scorer_version"] == "scorer_v0.1"
        assert d["core_quality_score"] == "0.80"
        assert d["core_quality_grade"] == "A"
        assert isinstance(d["setup"], dict)
        assert isinstance(d["core_module_scores"]["displacement_quality"], dict)
        assert isinstance(d["confluence_result"], dict)
        assert isinstance(d["module_features"], dict)

    def test_null_confluence_in_dict(self):
        kw = _valid_kwargs()
        kw["confluence_result"] = None
        ss = ScoredSetup(**kw)
        d = ss.to_dict()
        assert d["confluence_result"] is None

    def test_null_grade_in_dict(self):
        kw = _valid_kwargs()
        kw["core_quality_score"] = None
        kw["core_quality_grade"] = None
        ss = ScoredSetup(**kw)
        d = ss.to_dict()
        assert d["core_quality_score"] is None
        assert d["core_quality_grade"] is None
