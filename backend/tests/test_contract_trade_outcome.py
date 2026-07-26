"""Tests for canonical TradeOutcome/v1 contract type.

Covers every authoritative outcome variant and contract rule.
"""

import pytest

from trading_lab.contracts.enums import Direction
from trading_lab.contracts.trade_outcome import TradeOutcome, TradeOutcomeStatus
from trading_lab.contracts.trade_plan import EntryModel


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK_SIZE = "0.01"


def _target_hit_kwargs() -> dict:
    """CC, 2R target hit."""
    return dict(
        schema_version="TradeOutcome/v1",
        direction=Direction.LONG,
        entry_model=EntryModel.CONFIRMATION_CLOSE,
        entry_price_ticks=10120,
        stop_price_ticks=10000,
        tick_size=TICK_SIZE,
        selected_exit_target_r=2,
        selected_exit_target_label="2R",
        entry_triggered=True,
        entry_bar_utc_ms=1748264400000,
        bosb_entry_bar_index=None,
        first_eval_bar_index=0,
        first_eval_bar_utc_ms=1748264700000,
        outcome=TradeOutcomeStatus.TARGET_HIT,
        exit_bar_index=3,
        exit_bar_utc_ms=1748265600000,
        exit_price_ticks=10360,
        exit_target_label="2R",
        exit_target_r=2,
        highest_target_achieved="2R",
        highest_target_r=2,
        realized_r=2,
        r2_price_ticks=10360,
        r3_price_ticks=10480,
        r4_price_ticks=10600,
    )


def _stopped_kwargs() -> dict:
    kw = _target_hit_kwargs()
    kw.update(
        outcome=TradeOutcomeStatus.STOPPED,
        exit_bar_index=1,
        exit_bar_utc_ms=1748265000000,
        exit_price_ticks=10000,
        exit_target_label=None,
        exit_target_r=None,
        highest_target_achieved=None,
        highest_target_r=None,
        realized_r=-1,
    )
    return kw


def _open_kwargs() -> dict:
    kw = _target_hit_kwargs()
    kw.update(
        outcome=TradeOutcomeStatus.OPEN,
        exit_bar_index=None,
        exit_bar_utc_ms=None,
        exit_price_ticks=None,
        exit_target_label=None,
        exit_target_r=None,
        highest_target_achieved=None,
        highest_target_r=None,
        realized_r=None,
    )
    return kw


def _ambiguous_kwargs() -> dict:
    kw = _target_hit_kwargs()
    kw.update(
        outcome=TradeOutcomeStatus.AMBIGUOUS,
        exit_bar_index=0,
        exit_bar_utc_ms=1748264700000,
        exit_price_ticks=None,
        exit_target_label=None,
        exit_target_r=None,
        highest_target_achieved=None,
        highest_target_r=None,
        realized_r=None,
    )
    return kw


def _entry_not_triggered_kwargs() -> dict:
    kw = _target_hit_kwargs()
    kw.update(
        entry_model=EntryModel.BREAK_OF_SIGNAL_BAR,
        entry_triggered=False,
        entry_bar_utc_ms=None,
        bosb_entry_bar_index=None,
        first_eval_bar_index=None,
        first_eval_bar_utc_ms=None,
        outcome=TradeOutcomeStatus.ENTRY_NOT_TRIGGERED,
        exit_bar_index=None,
        exit_bar_utc_ms=None,
        exit_price_ticks=None,
        exit_target_label=None,
        exit_target_r=None,
        highest_target_achieved=None,
        highest_target_r=None,
        realized_r=None,
    )
    return kw


def make_to(**overrides) -> TradeOutcome:
    kw = _target_hit_kwargs()
    kw.update(overrides)
    return TradeOutcome(**kw)


# ═══════════════════════════════════════════════════════════════════════════════
# Valid construction — all outcome variants
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidConstruction:
    def test_target_hit(self):
        to = TradeOutcome(**_target_hit_kwargs())
        assert to.outcome == TradeOutcomeStatus.TARGET_HIT
        assert to.realized_r == 2
        assert to.exit_price_ticks == 10360

    def test_stopped(self):
        to = TradeOutcome(**_stopped_kwargs())
        assert to.outcome == TradeOutcomeStatus.STOPPED
        assert to.realized_r == -1
        assert to.exit_price_ticks == 10000

    def test_open(self):
        to = TradeOutcome(**_open_kwargs())
        assert to.outcome == TradeOutcomeStatus.OPEN
        assert to.realized_r is None
        assert to.exit_bar_index is None

    def test_ambiguous(self):
        to = TradeOutcome(**_ambiguous_kwargs())
        assert to.outcome == TradeOutcomeStatus.AMBIGUOUS
        assert to.realized_r is None
        assert to.exit_price_ticks is None

    def test_entry_not_triggered(self):
        to = TradeOutcome(**_entry_not_triggered_kwargs())
        assert to.outcome == TradeOutcomeStatus.ENTRY_NOT_TRIGGERED
        assert to.entry_triggered is False
        assert to.realized_r is None

    def test_bosb_with_entry_bar_index(self):
        to = make_to(
            entry_model=EntryModel.BREAK_OF_SIGNAL_BAR,
            bosb_entry_bar_index=1,
        )
        assert to.bosb_entry_bar_index == 1

    def test_3r_target(self):
        to = make_to(
            selected_exit_target_r=3,
            selected_exit_target_label="3R",
            realized_r=3,
            exit_target_r=3,
            exit_target_label="3R",
        )
        assert to.selected_exit_target_r == 3
        assert to.selected_exit_target_label == "3R"

    def test_4r_target(self):
        to = make_to(
            selected_exit_target_r=4,
            selected_exit_target_label="4R",
            realized_r=4,
            exit_target_r=4,
            exit_target_label="4R",
        )
        assert to.selected_exit_target_r == 4


# ═══════════════════════════════════════════════════════════════════════════════
# Field preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestFieldPreservation:
    def test_all_fields(self):
        to = make_to()
        assert to.schema_version == "TradeOutcome/v1"
        assert to.direction == Direction.LONG
        assert to.entry_model == EntryModel.CONFIRMATION_CLOSE
        assert to.entry_price_ticks == 10120
        assert to.stop_price_ticks == 10000
        assert to.tick_size == TICK_SIZE
        assert to.r2_price_ticks == 10360
        assert to.r3_price_ticks == 10480
        assert to.r4_price_ticks == 10600


# ═══════════════════════════════════════════════════════════════════════════════
# TradeOutcomeStatus enum
# ═══════════════════════════════════════════════════════════════════════════════


class TestTradeOutcomeStatus:
    def test_all_values(self):
        expected = {
            "TARGET_HIT", "STOPPED", "AMBIGUOUS",
            "OPEN", "ENTRY_NOT_TRIGGERED",
        }
        assert {m.value for m in TradeOutcomeStatus} == expected

    def test_member_count(self):
        assert len(TradeOutcomeStatus) == 5

    def test_string_comparison(self):
        assert TradeOutcomeStatus.STOPPED == "STOPPED"

    def test_invalid(self):
        with pytest.raises(ValueError):
            TradeOutcomeStatus("FILLED")


# ═══════════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_cannot_set_outcome(self):
        to = make_to()
        with pytest.raises(AttributeError):
            to.outcome = TradeOutcomeStatus.STOPPED  # type: ignore[misc]

    def test_cannot_set_realized_r(self):
        to = make_to()
        with pytest.raises(AttributeError):
            to.realized_r = -1  # type: ignore[misc]

    def test_cannot_set_entry_price(self):
        to = make_to()
        with pytest.raises(AttributeError):
            to.entry_price_ticks = 0  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic equality
# ═══════════════════════════════════════════════════════════════════════════════


class TestEquality:
    def test_equal(self):
        a = make_to()
        b = make_to()
        assert a == b

    def test_not_equal(self):
        a = make_to()
        b = TradeOutcome(**_stopped_kwargs())
        assert a != b

    def test_hash_equal(self):
        a = make_to()
        b = make_to()
        assert hash(a) == hash(b)


# ═══════════════════════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialization:
    def test_all_keys_present(self):
        to = make_to()
        d = to.to_dict()
        expected = {
            "schema_version", "direction", "entry_model",
            "entry_price_ticks", "stop_price_ticks", "tick_size",
            "selected_exit_target_r", "selected_exit_target_label",
            "entry_triggered", "entry_bar_utc_ms", "bosb_entry_bar_index",
            "first_eval_bar_index", "first_eval_bar_utc_ms",
            "outcome", "exit_bar_index", "exit_bar_utc_ms",
            "exit_price_ticks", "exit_target_label", "exit_target_r",
            "highest_target_achieved", "highest_target_r",
            "realized_r",
            "r2_price_ticks", "r3_price_ticks", "r4_price_ticks",
        }
        assert set(d.keys()) == expected

    def test_key_count(self):
        to = make_to()
        assert len(to.to_dict()) == 25

    def test_schema_version(self):
        assert make_to().to_dict()["schema_version"] == "TradeOutcome/v1"

    def test_direction_string(self):
        d = make_to().to_dict()
        assert d["direction"] == "LONG"
        assert isinstance(d["direction"], str)

    def test_entry_model_string(self):
        d = make_to().to_dict()
        assert d["entry_model"] == "CONFIRMATION_CLOSE"
        assert isinstance(d["entry_model"], str)

    def test_outcome_string(self):
        d = make_to().to_dict()
        assert d["outcome"] == "TARGET_HIT"
        assert isinstance(d["outcome"], str)

    def test_entry_triggered_bool(self):
        d = make_to().to_dict()
        assert d["entry_triggered"] is True
        assert isinstance(d["entry_triggered"], bool)

    def test_null_fields_present(self):
        to = TradeOutcome(**_open_kwargs())
        d = to.to_dict()
        assert "exit_bar_index" in d and d["exit_bar_index"] is None
        assert "exit_price_ticks" in d and d["exit_price_ticks"] is None
        assert "realized_r" in d and d["realized_r"] is None

    def test_tick_size_string(self):
        d = make_to().to_dict()
        assert isinstance(d["tick_size"], str)
        assert d["tick_size"] == TICK_SIZE

    def test_integer_fields(self):
        d = make_to().to_dict()
        assert isinstance(d["entry_price_ticks"], int)
        assert isinstance(d["r2_price_ticks"], int)

    def test_full_target_hit_shape(self):
        to = make_to()
        d = to.to_dict()
        assert d == {
            "schema_version": "TradeOutcome/v1",
            "direction": "LONG",
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_price_ticks": 10120,
            "stop_price_ticks": 10000,
            "tick_size": TICK_SIZE,
            "selected_exit_target_r": 2,
            "selected_exit_target_label": "2R",
            "entry_triggered": True,
            "entry_bar_utc_ms": 1748264400000,
            "bosb_entry_bar_index": None,
            "first_eval_bar_index": 0,
            "first_eval_bar_utc_ms": 1748264700000,
            "outcome": "TARGET_HIT",
            "exit_bar_index": 3,
            "exit_bar_utc_ms": 1748265600000,
            "exit_price_ticks": 10360,
            "exit_target_label": "2R",
            "exit_target_r": 2,
            "highest_target_achieved": "2R",
            "highest_target_r": 2,
            "realized_r": 2,
            "r2_price_ticks": 10360,
            "r3_price_ticks": 10480,
            "r4_price_ticks": 10600,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid schema_version
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidSchemaVersion:
    def test_wrong(self):
        with pytest.raises(ValueError, match="TradeOutcome/v1"):
            make_to(schema_version="TradeOutcome/v2")

    def test_none(self):
        with pytest.raises(ValueError, match="TradeOutcome/v1"):
            make_to(schema_version=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid enums
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidEnums:
    def test_direction_string(self):
        with pytest.raises(TypeError, match="Direction"):
            make_to(direction="LONG")

    def test_entry_model_string(self):
        with pytest.raises(TypeError, match="EntryModel"):
            make_to(entry_model="CONFIRMATION_CLOSE")

    def test_outcome_string(self):
        with pytest.raises(TypeError, match="TradeOutcomeStatus"):
            make_to(outcome="TARGET_HIT")


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid exit target R
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidExitTargetR:
    def test_value_1(self):
        with pytest.raises(ValueError, match="2, 3, or 4"):
            make_to(selected_exit_target_r=1)

    def test_value_5(self):
        with pytest.raises(ValueError, match="2, 3, or 4"):
            make_to(selected_exit_target_r=5)

    def test_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_to(selected_exit_target_r=True)

    def test_invalid_label(self):
        with pytest.raises(ValueError, match="'2R', '3R', or '4R'"):
            make_to(selected_exit_target_label="5R")


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid integer fields (boolean rejection)
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidIntegers:
    def test_entry_price_ticks_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_to(entry_price_ticks=True)

    def test_stop_price_ticks_float(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_to(stop_price_ticks=10000.0)

    def test_r2_price_ticks_str(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_to(r2_price_ticks="10360")

    def test_exit_bar_index_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_to(exit_bar_index=True)

    def test_realized_r_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_to(realized_r=True)

    def test_entry_bar_utc_ms_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_to(entry_bar_utc_ms=False)

    def test_highest_target_r_float(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_to(highest_target_r=2.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid entry_triggered
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidEntryTriggered:
    def test_int(self):
        with pytest.raises(TypeError, match="must be a bool"):
            make_to(entry_triggered=1)

    def test_none(self):
        with pytest.raises(TypeError, match="must be a bool"):
            make_to(entry_triggered=None)

    def test_string(self):
        with pytest.raises(TypeError, match="must be a bool"):
            make_to(entry_triggered="true")


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid tick_size
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidTickSize:
    def test_numeric(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_to(tick_size=0.01)

    def test_none(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_to(tick_size=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Unexpected fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnexpectedFields:
    def test_extra_field(self):
        kw = _target_hit_kwargs()
        kw["mfe_ticks"] = 100
        with pytest.raises(TypeError):
            TradeOutcome(**kw)


# ═══════════════════════════════════════════════════════════════════════════════
# Package export
# ═══════════════════════════════════════════════════════════════════════════════


class TestPackageExport:
    def test_import_trade_outcome(self):
        from trading_lab.contracts import TradeOutcome as TO
        assert TO is TradeOutcome

    def test_import_trade_outcome_status(self):
        from trading_lab.contracts import TradeOutcomeStatus as TOS
        assert TOS is TradeOutcomeStatus
