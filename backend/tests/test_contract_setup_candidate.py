"""Tests for canonical SetupCandidate/v1 contract type (§3.5)."""

import pytest

from trading_lab.contracts.setup_candidate import SetupCandidate
from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.trade_plan import TradePlan, EntryModel
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.contracts.distances import AbsoluteTickDistance
from trading_lab.contracts.enums import DetectionStatus, FailedStage

# Import fixtures from existing detection result tests
from test_contract_detection_result import (
    _valid_kwargs as _dr_valid_kwargs,
    _invalid_kwargs as _dr_invalid_kwargs,
    _pt,
)

TICK_SIZE = "0.01"


def _valid_detection_result():
    return DetectionResult(**_dr_valid_kwargs())


def _valid_trade_plan():
    """TradePlan matching the detection result fixture."""
    dr = _valid_detection_result()
    entry_ticks = dr.confirmation_bar.close.ticks
    stop_ticks = dr.confirmation_bar.low.ticks
    risk = abs(entry_ticks - stop_ticks)
    return TradePlan(
        schema_version="TradePlan/v1",
        entry_model=EntryModel.CONFIRMATION_CLOSE,
        entry_buffer_ticks=0,
        stop_buffer_ticks=0,
        tick_size=TICK_SIZE,
        entry_price=PriceTicks(ticks=entry_ticks, tick_size=TICK_SIZE),
        stop_price=PriceTicks(ticks=stop_ticks, tick_size=TICK_SIZE),
        risk=AbsoluteTickDistance(ticks=risk, tick_size=TICK_SIZE),
        r2_price=PriceTicks(ticks=entry_ticks + 2 * risk, tick_size=TICK_SIZE),
        r3_price=PriceTicks(ticks=entry_ticks + 3 * risk, tick_size=TICK_SIZE),
        r4_price=PriceTicks(ticks=entry_ticks + 4 * risk, tick_size=TICK_SIZE),
    )


def _valid_kwargs():
    return dict(
        schema_version="SetupCandidate/v1",
        candidate_id="11111111-2222-4333-9444-555555555555",
        composed_at="2026-01-02T14:01:00.000Z",
        detection_result=_valid_detection_result(),
        trade_plan=_valid_trade_plan(),
    )


class TestValidConstruction:
    def test_all_fields(self):
        sc = SetupCandidate(**_valid_kwargs())
        assert sc.schema_version == "SetupCandidate/v1"
        assert sc.candidate_id == "11111111-2222-4333-9444-555555555555"
        assert sc.composed_at == "2026-01-02T14:01:00.000Z"
        assert isinstance(sc.detection_result, DetectionResult)
        assert isinstance(sc.trade_plan, TradePlan)


class TestSchemaVersion:
    def test_wrong_schema(self):
        kw = _valid_kwargs()
        kw["schema_version"] = "SetupCandidate/v2"
        with pytest.raises(ValueError, match="SetupCandidate/v1"):
            SetupCandidate(**kw)


class TestInvariantC01:
    def test_invalid_detection_rejected(self):
        kw = _valid_kwargs()
        invalid_dr = DetectionResult(**_dr_invalid_kwargs())
        kw["detection_result"] = invalid_dr
        with pytest.raises(ValueError, match="status must be VALID"):
            SetupCandidate(**kw)


class TestInvariantC06:
    def test_candidate_id_equals_result_id_rejected(self):
        kw = _valid_kwargs()
        kw["candidate_id"] = kw["detection_result"].result_id
        with pytest.raises(ValueError, match="INV-C-06"):
            SetupCandidate(**kw)


class TestTypeValidation:
    def test_detection_result_not_dataclass(self):
        kw = _valid_kwargs()
        kw["detection_result"] = {"status": "VALID"}
        with pytest.raises(TypeError, match="DetectionResult"):
            SetupCandidate(**kw)

    def test_trade_plan_not_dataclass(self):
        kw = _valid_kwargs()
        kw["trade_plan"] = {"schema_version": "TradePlan/v1"}
        with pytest.raises(TypeError, match="TradePlan"):
            SetupCandidate(**kw)

    def test_empty_candidate_id(self):
        kw = _valid_kwargs()
        kw["candidate_id"] = ""
        with pytest.raises(ValueError, match="non-empty"):
            SetupCandidate(**kw)


class TestImmutability:
    def test_frozen(self):
        sc = SetupCandidate(**_valid_kwargs())
        with pytest.raises(AttributeError):
            sc.candidate_id = "new-id"


class TestToDict:
    def test_shape(self):
        sc = SetupCandidate(**_valid_kwargs())
        d = sc.to_dict()
        assert d["schema_version"] == "SetupCandidate/v1"
        assert d["candidate_id"] == "11111111-2222-4333-9444-555555555555"
        assert d["composed_at"] == "2026-01-02T14:01:00.000Z"
        assert isinstance(d["detection_result"], dict)
        assert isinstance(d["trade_plan"], dict)
        assert d["detection_result"]["schema_version"] == "DetectionResult/v1"
        assert d["trade_plan"]["schema_version"] == "TradePlan/v1"
