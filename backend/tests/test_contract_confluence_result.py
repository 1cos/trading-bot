"""Tests for canonical ConfluenceResult contract type (§3.6)."""

import pytest

from trading_lab.contracts.enums import (
    ConfluenceStatus,
    EvaluationStatus,
)
from trading_lab.contracts.confluence_result import ConfluenceResult


def _scored_kwargs():
    return dict(
        evaluation_status=EvaluationStatus.SCORED,
        score="0.72",
        confluence_status=ConfluenceStatus.CONFIRMING,
        data_source="PDH_PDL_v1",
        data_timestamp_utc_ms=1700000000000,
        input_fields=("pdh_price", "pdl_price"),
    )


def _unavailable_kwargs():
    return dict(
        evaluation_status=EvaluationStatus.DATA_UNAVAILABLE,
        score=None,
        confluence_status=ConfluenceStatus.DATA_UNAVAILABLE,
        data_source=None,
        data_timestamp_utc_ms=None,
        input_fields=(),
    )


class TestValidConstruction:
    def test_scored(self):
        cr = ConfluenceResult(**_scored_kwargs())
        assert cr.evaluation_status == EvaluationStatus.SCORED
        assert cr.score == "0.72"
        assert cr.confluence_status == ConfluenceStatus.CONFIRMING
        assert cr.data_source == "PDH_PDL_v1"
        assert cr.data_timestamp_utc_ms == 1700000000000
        assert cr.input_fields == ("pdh_price", "pdl_price")

    def test_data_unavailable(self):
        cr = ConfluenceResult(**_unavailable_kwargs())
        assert cr.evaluation_status == EvaluationStatus.DATA_UNAVAILABLE
        assert cr.score is None

    def test_error(self):
        cr = ConfluenceResult(
            evaluation_status=EvaluationStatus.ERROR,
            score=None,
            confluence_status=ConfluenceStatus.DATA_UNAVAILABLE,
            data_source=None,
            data_timestamp_utc_ms=None,
            input_fields=(),
        )
        assert cr.evaluation_status == EvaluationStatus.ERROR


class TestEvaluationStatusRestriction:
    def test_not_run_rejected(self):
        kw = _scored_kwargs()
        kw["evaluation_status"] = EvaluationStatus.NOT_RUN
        kw["score"] = None
        with pytest.raises(ValueError, match="SCORED, DATA_UNAVAILABLE, or ERROR"):
            ConfluenceResult(**kw)


class TestScoredConstraints:
    def test_scored_requires_non_null_score(self):
        kw = _scored_kwargs()
        kw["score"] = None
        with pytest.raises(ValueError, match="score must be non-null"):
            ConfluenceResult(**kw)

    def test_non_scored_rejects_score(self):
        kw = _unavailable_kwargs()
        kw["score"] = "0.5"
        with pytest.raises(ValueError, match="score must be null"):
            ConfluenceResult(**kw)


class TestTypeValidation:
    def test_bad_confluence_status_type(self):
        kw = _scored_kwargs()
        kw["confluence_status"] = "CONFIRMING"
        with pytest.raises(TypeError, match="ConfluenceStatus"):
            ConfluenceResult(**kw)

    def test_data_timestamp_bool(self):
        kw = _scored_kwargs()
        kw["data_timestamp_utc_ms"] = True
        with pytest.raises(TypeError, match="int, got bool"):
            ConfluenceResult(**kw)

    def test_input_fields_not_tuple(self):
        kw = _scored_kwargs()
        kw["input_fields"] = ["a"]
        with pytest.raises(TypeError, match="tuple"):
            ConfluenceResult(**kw)

    def test_invalid_score_decimal(self):
        kw = _scored_kwargs()
        kw["score"] = "not_decimal"
        with pytest.raises(ValueError, match="not a valid Decimal"):
            ConfluenceResult(**kw)


class TestImmutability:
    def test_frozen(self):
        cr = ConfluenceResult(**_scored_kwargs())
        with pytest.raises(AttributeError):
            cr.score = "0.99"


class TestToDict:
    def test_shape(self):
        cr = ConfluenceResult(**_scored_kwargs())
        d = cr.to_dict()
        assert d == {
            "evaluation_status": "SCORED",
            "score": "0.72",
            "confluence_status": "CONFIRMING",
            "data_source": "PDH_PDL_v1",
            "data_timestamp_utc_ms": 1700000000000,
            "input_fields": ["pdh_price", "pdl_price"],
        }
        assert isinstance(d["input_fields"], list)


class TestINVS08StatusConsistency:
    def test_data_unavailable_with_data_unavailable_status(self):
        cr = ConfluenceResult(**_unavailable_kwargs())
        assert cr.confluence_status == ConfluenceStatus.DATA_UNAVAILABLE

    def test_data_unavailable_with_confirming_rejected(self):
        kw = _unavailable_kwargs()
        kw["confluence_status"] = ConfluenceStatus.CONFIRMING
        with pytest.raises(ValueError, match="INV-S-08"):
            ConfluenceResult(**kw)

    def test_data_unavailable_with_neutral_rejected(self):
        kw = _unavailable_kwargs()
        kw["confluence_status"] = ConfluenceStatus.NEUTRAL
        with pytest.raises(ValueError, match="INV-S-08"):
            ConfluenceResult(**kw)

    def test_data_unavailable_with_conflicting_rejected(self):
        kw = _unavailable_kwargs()
        kw["confluence_status"] = ConfluenceStatus.CONFLICTING
        with pytest.raises(ValueError, match="INV-S-08"):
            ConfluenceResult(**kw)

    def test_error_with_data_unavailable_status(self):
        cr = ConfluenceResult(
            evaluation_status=EvaluationStatus.ERROR,
            score=None,
            confluence_status=ConfluenceStatus.DATA_UNAVAILABLE,
            data_source=None,
            data_timestamp_utc_ms=None,
            input_fields=(),
        )
        assert cr.confluence_status == ConfluenceStatus.DATA_UNAVAILABLE

    def test_error_with_confirming_rejected(self):
        with pytest.raises(ValueError, match="INV-S-08"):
            ConfluenceResult(
                evaluation_status=EvaluationStatus.ERROR,
                score=None,
                confluence_status=ConfluenceStatus.CONFIRMING,
                data_source=None,
                data_timestamp_utc_ms=None,
                input_fields=(),
            )

    def test_non_scored_with_non_null_score_rejected(self):
        kw = _unavailable_kwargs()
        kw["score"] = "0.5"
        with pytest.raises(ValueError, match="score must be null"):
            ConfluenceResult(**kw)

    def test_not_run_still_rejected(self):
        with pytest.raises(ValueError, match="SCORED, DATA_UNAVAILABLE, or ERROR"):
            ConfluenceResult(
                evaluation_status=EvaluationStatus.NOT_RUN,
                score=None,
                confluence_status=ConfluenceStatus.DATA_UNAVAILABLE,
                data_source=None,
                data_timestamp_utc_ms=None,
                input_fields=(),
            )
