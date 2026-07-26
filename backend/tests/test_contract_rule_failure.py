"""Tests for canonical RuleFailure and RejectionAttempt contract types."""

import pytest

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.enums import Operator, Stage, ValueType
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.contracts.rule_failure import RejectionAttempt, RuleFailure


TICK_SIZE = "0.01"
PT = PriceTicks(ticks=52500, tick_size=TICK_SIZE)
BAR_MS = 1748264400000


def make_bar():
    return Bar(
        bar_utc_ms=BAR_MS, open=PT, high=PT, low=PT, close=PT, volume=None,
    )


def make_rule_failure(**overrides):
    defaults = dict(
        rule_id="REJECTION_WICK_RATIO_TOO_LOW",
        stage=Stage.REJECTION_CANDLE,
        value_type=ValueType.BOOLEAN,
        actual_value=None,
        operator=None,
        required_value=None,
        unit=None,
        message="REJECTION_WICK_RATIO_TOO_LOW",
    )
    defaults.update(overrides)
    return RuleFailure(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# RuleFailure
# ═══════════════════════════════════════════════════════════════════════════════


class TestRuleFailureConstruction:
    def test_minimal(self):
        rf = make_rule_failure()
        assert rf.rule_id == "REJECTION_WICK_RATIO_TOO_LOW"
        assert rf.stage == Stage.REJECTION_CANDLE
        assert rf.value_type == ValueType.BOOLEAN
        assert rf.actual_value is None
        assert rf.operator is None
        assert rf.required_value is None
        assert rf.unit is None
        assert rf.message == "REJECTION_WICK_RATIO_TOO_LOW"

    def test_fully_populated(self):
        rf = RuleFailure(
            rule_id="BODY_RATIO_TOO_HIGH",
            stage=Stage.REJECTION_CANDLE,
            value_type=ValueType.DECIMAL,
            actual_value="0.55",
            operator=Operator.LTE,
            required_value="0.40",
            unit="ratio",
            message="body ratio 0.55 exceeds max 0.40",
        )
        assert rf.actual_value == "0.55"
        assert rf.operator == Operator.LTE
        assert rf.required_value == "0.40"
        assert rf.unit == "ratio"


class TestRuleFailureImmutability:
    def test_cannot_set_rule_id(self):
        rf = make_rule_failure()
        with pytest.raises(AttributeError):
            rf.rule_id = "OTHER"  # type: ignore[misc]

    def test_cannot_set_stage(self):
        rf = make_rule_failure()
        with pytest.raises(AttributeError):
            rf.stage = Stage.BREAK  # type: ignore[misc]


class TestRuleFailureSerialization:
    def test_minimal_shape(self):
        rf = make_rule_failure()
        d = rf.to_dict()
        assert d == {
            "rule_id": "REJECTION_WICK_RATIO_TOO_LOW",
            "stage": "REJECTION_CANDLE",
            "value_type": "BOOLEAN",
            "actual_value": None,
            "operator": None,
            "required_value": None,
            "unit": None,
            "message": "REJECTION_WICK_RATIO_TOO_LOW",
        }

    def test_with_operator(self):
        rf = make_rule_failure(operator=Operator.GTE, actual_value="0.30")
        d = rf.to_dict()
        assert d["operator"] == "GTE"
        assert d["actual_value"] == "0.30"

    def test_keys_exact(self):
        rf = make_rule_failure()
        assert set(rf.to_dict().keys()) == {
            "rule_id", "stage", "value_type", "actual_value",
            "operator", "required_value", "unit", "message",
        }

    def test_enum_values_are_strings(self):
        rf = make_rule_failure()
        d = rf.to_dict()
        assert isinstance(d["stage"], str)
        assert isinstance(d["value_type"], str)


class TestRuleFailureEquality:
    def test_equal(self):
        a = make_rule_failure()
        b = make_rule_failure()
        assert a == b

    def test_not_equal(self):
        a = make_rule_failure(rule_id="A")
        b = make_rule_failure(rule_id="B")
        assert a != b

    def test_hash_equal(self):
        a = make_rule_failure()
        b = make_rule_failure()
        assert hash(a) == hash(b)


class TestRuleFailureInvalid:
    def test_empty_rule_id(self):
        with pytest.raises(ValueError, match="non-empty"):
            make_rule_failure(rule_id="")

    def test_none_rule_id(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_rule_failure(rule_id=None)

    def test_invalid_stage_string(self):
        with pytest.raises(TypeError, match="Stage enum member"):
            make_rule_failure(stage="REJECTION_CANDLE")

    def test_invalid_value_type_string(self):
        with pytest.raises(TypeError, match="ValueType enum member"):
            make_rule_failure(value_type="BOOLEAN")

    def test_invalid_operator_string(self):
        with pytest.raises(TypeError, match="Operator enum member"):
            make_rule_failure(operator="GT")

    def test_actual_value_int(self):
        with pytest.raises(TypeError, match="must be a str or None"):
            make_rule_failure(actual_value=42)

    def test_empty_message(self):
        with pytest.raises(ValueError, match="non-empty"):
            make_rule_failure(message="")

    def test_none_message(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_rule_failure(message=None)


# ═══════════════════════════════════════════════════════════════════════════════
# RejectionAttempt
# ═══════════════════════════════════════════════════════════════════════════════


class TestRejectionAttemptConstruction:
    def test_with_rules(self):
        bar = make_bar()
        rf = make_rule_failure()
        ra = RejectionAttempt(bar=bar, failed_rules=(rf,))
        assert ra.bar is bar
        assert len(ra.failed_rules) == 1
        assert ra.failed_rules[0] is rf

    def test_empty_rules(self):
        bar = make_bar()
        ra = RejectionAttempt(bar=bar, failed_rules=())
        assert len(ra.failed_rules) == 0

    def test_multiple_rules(self):
        bar = make_bar()
        rf1 = make_rule_failure(rule_id="RULE_A")
        rf2 = make_rule_failure(rule_id="RULE_B")
        ra = RejectionAttempt(bar=bar, failed_rules=(rf1, rf2))
        assert len(ra.failed_rules) == 2


class TestRejectionAttemptImmutability:
    def test_cannot_set_bar(self):
        ra = RejectionAttempt(bar=make_bar(), failed_rules=())
        with pytest.raises(AttributeError):
            ra.bar = make_bar()  # type: ignore[misc]

    def test_cannot_set_failed_rules(self):
        ra = RejectionAttempt(bar=make_bar(), failed_rules=())
        with pytest.raises(AttributeError):
            ra.failed_rules = ()  # type: ignore[misc]


class TestRejectionAttemptSerialization:
    def test_shape(self):
        bar = make_bar()
        rf = make_rule_failure()
        ra = RejectionAttempt(bar=bar, failed_rules=(rf,))
        d = ra.to_dict()
        assert set(d.keys()) == {"bar", "failed_rules"}
        assert isinstance(d["bar"], dict)
        assert isinstance(d["failed_rules"], list)
        assert len(d["failed_rules"]) == 1
        assert d["failed_rules"][0]["rule_id"] == "REJECTION_WICK_RATIO_TOO_LOW"

    def test_nested_bar_serialization(self):
        ra = RejectionAttempt(bar=make_bar(), failed_rules=())
        d = ra.to_dict()
        assert "bar_utc_ms" in d["bar"]
        assert "open" in d["bar"]

    def test_empty_rules_serialization(self):
        ra = RejectionAttempt(bar=make_bar(), failed_rules=())
        assert ra.to_dict()["failed_rules"] == []


class TestRejectionAttemptEquality:
    def test_equal(self):
        bar = make_bar()
        rf = make_rule_failure()
        a = RejectionAttempt(bar=bar, failed_rules=(rf,))
        b = RejectionAttempt(bar=bar, failed_rules=(rf,))
        assert a == b

    def test_hash_equal(self):
        bar = make_bar()
        a = RejectionAttempt(bar=bar, failed_rules=())
        b = RejectionAttempt(bar=bar, failed_rules=())
        assert hash(a) == hash(b)


class TestRejectionAttemptInvalid:
    def test_bar_none(self):
        with pytest.raises(TypeError, match="Bar instance"):
            RejectionAttempt(bar=None, failed_rules=())

    def test_bar_dict(self):
        with pytest.raises(TypeError, match="Bar instance"):
            RejectionAttempt(bar={"bar_utc_ms": 0}, failed_rules=())

    def test_failed_rules_list(self):
        with pytest.raises(TypeError, match="must be a tuple"):
            RejectionAttempt(bar=make_bar(), failed_rules=[])

    def test_failed_rules_contains_non_rule(self):
        with pytest.raises(TypeError, match="RuleFailure instance"):
            RejectionAttempt(bar=make_bar(), failed_rules=("not_a_rule",))


class TestRuleFailurePackageExport:
    def test_import_rule_failure(self):
        from trading_lab.contracts import RuleFailure as RF
        assert RF is RuleFailure

    def test_import_rejection_attempt(self):
        from trading_lab.contracts import RejectionAttempt as RA
        assert RA is RejectionAttempt
