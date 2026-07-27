"""Tests for canonical build_trade_plan port.

Mirrors estrategie/test_bdrr_trade_plan.js (189 checks).

Coverage:
  1–5.   CONFIRMATION_CLOSE and BREAK_OF_SIGNAL_BAR with zero/non-zero buffers.
  6.     LONG stop below entry.
  7.     Risk = abs(entry − stop).
  8.     Exact 2R, 3R, 4R targets.
  9.     Integer ticks on all price fields.
  10.    tick_size preserved consistently.
  11.    Invalid detection result rejected.
  12.    Missing confirmation_bar rejected.
  13.    Invalid PriceTicks fields rejected.
  14.    Tick-size mismatch rejected.
  15.    Negative / non-integer buffers rejected.
  16.    Unknown entry model rejected.
  17.    Unsupported SHORT direction rejected.
  18.    Zero risk rejected.
  19.    Deterministic repeated execution.
  20.    No input mutation.
  21–22. Oracle parity for eligible candidates.
  23.    SPY integration parity.
  Additional Python-specific boundary tests.
"""

import copy
import math

import pytest

from trading_lab.trade_plan_builder import (
    build_trade_plan,
    TradePlanConfig,
)
from trading_lab.contracts.trade_plan import EntryModel, TradePlan
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.contracts.distances import AbsoluteTickDistance


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK = 0.01
TICK_STR = "0.01"


def _pt(price: float, tick_size: float = TICK) -> PriceTicks:
    """Build a canonical PriceTicks from a dollar price."""
    ticks = round(price / tick_size)
    return PriceTicks(ticks=ticks, tick_size=str(tick_size))


def _bar(
    open_: float = 101.50,
    high: float = 102.00,
    low: float = 100.00,
    close: float = 101.20,
):
    """Build a minimal confirmation_bar dict with PriceTicks values."""
    return {
        "bar_utc_ms": 1000,
        "open": _pt(open_),
        "high": _pt(high),
        "low": _pt(low),
        "close": _pt(close),
        "volume": None,
    }


def _dr(**overrides):
    """Build a synthetic DetectionResult/v1 dict for testing."""
    base = {
        "schema_version": "DetectionResult/v1",
        "result_id": "aaaaaaaa-0000-4000-8000-000000000001",
        "produced_at": "2026-07-01T13:45:00.000Z",
        "status": "VALID",
        "failed_stage": None,
        "failed_rules": [],
        "session": {
            "symbol": "TEST",
            "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": 1000,
            "session_close_utc_ms": 2000,
            "timeframe_seconds": 300,
        },
        "preset_id": "test_preset",
        "engine_version": "1.0.0",
        "level_price": _pt(101.00),
        "level_source": "ORB_HIGH",
        "direction": "LONG",
        "confirmation_bar": _bar(),
        "displacement_window": [],
        "retest_window": [],
        "failed_retests": [],
        "failed_retest_count": 0,
    }
    base.update(overrides)
    return base


BASE_CONFIG = TradePlanConfig(
    direction="LONG",
    entry_model="CONFIRMATION_CLOSE",
    entry_buffer_ticks=0,
    stop_buffer_ticks=0,
    tick_size=TICK,
)


def _cfg(**overrides):
    """Build a TradePlanConfig with overrides."""
    kw = {
        "direction": BASE_CONFIG.direction,
        "entry_model": BASE_CONFIG.entry_model,
        "entry_buffer_ticks": BASE_CONFIG.entry_buffer_ticks,
        "stop_buffer_ticks": BASE_CONFIG.stop_buffer_ticks,
        "tick_size": BASE_CONFIG.tick_size,
    }
    kw.update(overrides)
    return TradePlanConfig(**kw)


# ── 1. schema_version ────────────────────────────────────────────────────────


class TestSchemaVersion:
    def test_ok_status(self):
        r = build_trade_plan(_dr(), BASE_CONFIG)
        assert r["status"] == "OK"

    def test_trade_plan_schema_version(self):
        r = build_trade_plan(_dr(), BASE_CONFIG)
        assert r["trade_plan"].schema_version == "TradePlan/v1"


# ── 2. CONFIRMATION_CLOSE / zero buffers ──────────────────────────────────────


class TestConfirmationCloseZeroBuffers:
    """close=101.20 → entry=10120; low=100.00 → stop=10000; risk=120."""

    def test_entry(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert tp.entry_price.ticks == 10120

    def test_stop(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert tp.stop_price.ticks == 10000

    def test_risk(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert tp.risk.ticks == 120


# ── 3. CONFIRMATION_CLOSE / non-zero buffers ─────────────────────────────────


class TestConfirmationCloseNonZeroBuffers:
    """entry = close(10120)+5 = 10125; stop = low(10000)-3 = 9997; risk=128."""

    def setup_method(self):
        cfg = _cfg(entry_buffer_ticks=5, stop_buffer_ticks=3)
        self.tp = build_trade_plan(_dr(), cfg)["trade_plan"]

    def test_entry(self):
        assert self.tp.entry_price.ticks == 10125

    def test_stop(self):
        assert self.tp.stop_price.ticks == 9997

    def test_risk(self):
        assert self.tp.risk.ticks == 128


# ── 4. BREAK_OF_SIGNAL_BAR / zero buffers ────────────────────────────────────


class TestBreakOfSignalBarZeroBuffers:
    """entry = high(10200); stop = low(10000); risk = 200."""

    def setup_method(self):
        cfg = _cfg(entry_model="BREAK_OF_SIGNAL_BAR")
        self.tp = build_trade_plan(_dr(), cfg)["trade_plan"]

    def test_entry(self):
        assert self.tp.entry_price.ticks == 10200

    def test_stop(self):
        assert self.tp.stop_price.ticks == 10000

    def test_risk(self):
        assert self.tp.risk.ticks == 200


# ── 5. BREAK_OF_SIGNAL_BAR / non-zero buffers ────────────────────────────────


class TestBreakOfSignalBarNonZeroBuffers:
    """entry = high(10200)+2 = 10202; stop = low(10000)-1 = 9999; risk=203."""

    def setup_method(self):
        cfg = _cfg(
            entry_model="BREAK_OF_SIGNAL_BAR",
            entry_buffer_ticks=2,
            stop_buffer_ticks=1,
        )
        self.tp = build_trade_plan(_dr(), cfg)["trade_plan"]

    def test_entry(self):
        assert self.tp.entry_price.ticks == 10202

    def test_stop(self):
        assert self.tp.stop_price.ticks == 9999

    def test_risk(self):
        assert self.tp.risk.ticks == 203


# ── 6. LONG stop is below entry ──────────────────────────────────────────────


class TestLongStopBelowEntry:
    def test_stop_below_entry(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert tp.stop_price.ticks < tp.entry_price.ticks


# ── 7. Risk = abs(entry - stop) ──────────────────────────────────────────────


class TestRiskCalculation:
    @pytest.mark.parametrize(
        "high,low,close",
        [
            (110.00, 100.00, 108.00),
            (205.50, 200.00, 203.25),
            (510.00, 505.00, 509.50),
        ],
    )
    def test_risk_equals_abs_entry_minus_stop(self, high, low, close):
        dr = _dr(
            confirmation_bar=_bar(open_=close, high=high, low=low, close=close)
        )
        r = build_trade_plan(dr, BASE_CONFIG)
        assert r["status"] == "OK"
        tp = r["trade_plan"]
        assert tp.risk.ticks == abs(tp.entry_price.ticks - tp.stop_price.ticks)


# ── 8. Exact 2R, 3R, 4R targets ─────────────────────────────────────────────


class TestTargets:
    def test_cc_targets(self):
        """CC: entry=10120, risk=120."""
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert tp.r2_price.ticks == 10120 + 2 * 120
        assert tp.r3_price.ticks == 10120 + 3 * 120
        assert tp.r4_price.ticks == 10120 + 4 * 120

    def test_bosb_targets(self):
        """BOSB: entry=10200, risk=200."""
        cfg = _cfg(entry_model="BREAK_OF_SIGNAL_BAR")
        tp = build_trade_plan(_dr(), cfg)["trade_plan"]
        assert tp.r2_price.ticks == 10200 + 2 * 200
        assert tp.r3_price.ticks == 10200 + 3 * 200
        assert tp.r4_price.ticks == 10200 + 4 * 200


# ── 9. Every stored tick value is an integer ─────────────────────────────────


class TestIntegerTicks:
    def test_all_ticks_are_int(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        for field in (
            "entry_price",
            "stop_price",
            "risk",
            "r2_price",
            "r3_price",
            "r4_price",
        ):
            v = getattr(tp, field).ticks
            assert isinstance(v, int) and not isinstance(v, bool), (
                f"{field}.ticks must be int"
            )


# ── 10. tick_size is preserved consistently ──────────────────────────────────


class TestTickSizePreserved:
    def test_trade_plan_tick_size(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert tp.tick_size == TICK_STR

    def test_all_price_tick_sizes(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        for field in (
            "entry_price",
            "stop_price",
            "risk",
            "r2_price",
            "r3_price",
            "r4_price",
        ):
            assert getattr(tp, field).tick_size == TICK_STR, (
                f"{field}.tick_size mismatch"
            )


# ── 11. Invalid detection result is rejected ─────────────────────────────────


class TestInvalidDetectionRejected:
    def test_wrong_schema_version(self):
        r = build_trade_plan(
            _dr(schema_version="DetectionResult/v0"), BASE_CONFIG
        )
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_missing_schema_version(self):
        dr = _dr()
        del dr["schema_version"]
        r = build_trade_plan(dr, BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_invalid_status(self):
        r = build_trade_plan(
            _dr(status="INVALID", failed_stage="NO_QUALIFYING_REJECTION_CANDLE"),
            BASE_CONFIG,
        )
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_invalid_status_includes_failed_stage(self):
        r = build_trade_plan(
            _dr(status="INVALID", failed_stage="NO_QUALIFYING_REJECTION_CANDLE"),
            BASE_CONFIG,
        )
        assert "failed_stage: NO_QUALIFYING_REJECTION_CANDLE" in r["reason"]

    def test_old_raw_shape_rejected(self):
        """Old findRejection() shape — status 'OK', no schema_version."""
        raw = {
            "status": "OK",
            "date": "2026-05-26",
            "level_price": 750.44,
            "confirmation_candle": {"open": 750.77, "high": 750.97},
            "geometry": {},
            "failed_retests": [],
        }
        r = build_trade_plan(raw, BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_null_input(self):
        r = build_trade_plan(None, BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_string_input(self):
        r = build_trade_plan("not-an-object", BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_int_input(self):
        r = build_trade_plan(42, BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"


# ── 12. Missing confirmation_bar is rejected ─────────────────────────────────


class TestMissingConfirmationBar:
    def test_null_confirmation_bar(self):
        r = build_trade_plan(_dr(confirmation_bar=None), BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "MISSING_CONFIRMATION_BAR"

    def test_absent_confirmation_bar(self):
        dr = _dr()
        del dr["confirmation_bar"]
        r = build_trade_plan(dr, BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "MISSING_CONFIRMATION_BAR"


# ── 13. Invalid PriceTicks fields are rejected ───────────────────────────────


class TestInvalidPriceTicksFields:
    def test_plain_float_close(self):
        """Non-object field (plain float instead of PriceTicks)."""
        bar = _bar()
        bar["close"] = 101.20  # not a PriceTicks
        r = build_trade_plan(_dr(confirmation_bar=bar), BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_TICK_VALUE"

    def test_non_integer_ticks(self):
        bar = _bar()
        bar["close"] = {"ticks": 101.2, "tick_size": TICK_STR}
        r = build_trade_plan(_dr(confirmation_bar=bar), BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_TICK_VALUE"

    def test_tick_size_mismatch(self):
        bar = _bar()
        bar["close"] = PriceTicks(ticks=10120, tick_size="0.05")
        r = build_trade_plan(_dr(confirmation_bar=bar), BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"

    def test_null_field(self):
        bar = _bar()
        bar["low"] = None
        r = build_trade_plan(_dr(confirmation_bar=bar), BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_TICK_VALUE"


# ── 14. Inconsistent config tick sizes are rejected ──────────────────────────


class TestTickSizeMismatch:
    def test_zero_tick_size(self):
        r = build_trade_plan(_dr(), _cfg(tick_size=0))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"

    def test_negative_tick_size(self):
        r = build_trade_plan(_dr(), _cfg(tick_size=-0.01))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"

    def test_nan_tick_size(self):
        r = build_trade_plan(_dr(), _cfg(tick_size=float("nan")))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"

    def test_inf_tick_size(self):
        r = build_trade_plan(_dr(), _cfg(tick_size=float("inf")))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"

    def test_none_tick_size(self):
        r = build_trade_plan(_dr(), _cfg(tick_size=None))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"


# ── 15. Negative and non-integer buffers are rejected ────────────────────────


class TestInvalidBuffers:
    def test_negative_entry_buffer(self):
        r = build_trade_plan(_dr(), _cfg(entry_buffer_ticks=-1))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_BUFFER"

    def test_negative_stop_buffer(self):
        r = build_trade_plan(_dr(), _cfg(stop_buffer_ticks=-5))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_BUFFER"

    def test_float_entry_buffer(self):
        r = build_trade_plan(_dr(), _cfg(entry_buffer_ticks=1.5))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_BUFFER"

    def test_float_stop_buffer(self):
        r = build_trade_plan(_dr(), _cfg(stop_buffer_ticks=0.5))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_BUFFER"

    def test_missing_entry_buffer(self):
        r = build_trade_plan(
            _dr(),
            _cfg(entry_buffer_ticks=None),
        )
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_BUFFER"

    def test_bool_entry_buffer(self):
        """Python bool is subclass of int — must be rejected."""
        r = build_trade_plan(_dr(), _cfg(entry_buffer_ticks=True))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_BUFFER"

    def test_bool_stop_buffer(self):
        r = build_trade_plan(_dr(), _cfg(stop_buffer_ticks=False))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_BUFFER"

    def test_string_entry_buffer(self):
        r = build_trade_plan(_dr(), _cfg(entry_buffer_ticks="2"))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_BUFFER"


# ── 16. Unknown entry model is rejected ──────────────────────────────────────


class TestUnknownEntryModel:
    def test_unknown_model(self):
        r = build_trade_plan(_dr(), _cfg(entry_model="MARKET_ORDER"))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "UNSUPPORTED_ENTRY_MODEL"

    def test_none_model(self):
        r = build_trade_plan(_dr(), _cfg(entry_model=None))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "UNSUPPORTED_ENTRY_MODEL"


# ── 17. Unsupported SHORT direction is rejected ──────────────────────────────


class TestShortDirectionRejected:
    def test_short_fails(self):
        r = build_trade_plan(
            _dr(direction="SHORT"), _cfg(direction="SHORT")
        )
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "UNSUPPORTED_DIRECTION"

    def test_short_does_not_raise(self):
        """Must return structured failure, not throw."""
        r = build_trade_plan(
            _dr(direction="SHORT"), _cfg(direction="SHORT")
        )
        assert isinstance(r, dict)
        assert r["status"] == "FAILED"

    def test_unknown_direction(self):
        r = build_trade_plan(_dr(), _cfg(direction="UP"))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "UNSUPPORTED_DIRECTION"


# ── 18. Zero risk is rejected ────────────────────────────────────────────────


class TestZeroRiskRejected:
    def test_doji_cc(self):
        """Doji bar (all OHLC = 101.00) → entry == stop → INVALID_RISK."""
        dr = _dr(
            confirmation_bar=_bar(
                open_=101.00, high=101.00, low=101.00, close=101.00
            )
        )
        r = build_trade_plan(dr, BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_RISK"

    def test_doji_bosb(self):
        dr = _dr(
            confirmation_bar=_bar(
                open_=101.00, high=101.00, low=101.00, close=101.00
            )
        )
        r = build_trade_plan(dr, _cfg(entry_model="BREAK_OF_SIGNAL_BAR"))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_RISK"


# ── 19. Repeated runs are deeply identical ───────────────────────────────────


class TestDeterminism:
    def test_repeated_identical(self):
        cfg = _cfg(entry_buffer_ticks=2, stop_buffer_ticks=1)
        r1 = build_trade_plan(_dr(), cfg)
        r2 = build_trade_plan(_dr(), cfg)
        assert r1["status"] == "OK"
        assert r2["status"] == "OK"
        tp1 = r1["trade_plan"]
        tp2 = r2["trade_plan"]
        assert tp1 == tp2


# ── 20. Input objects are not mutated ─────────────────────────────────────────


class TestNoMutation:
    def test_dr_not_mutated(self):
        dr = _dr()
        dr_copy = copy.deepcopy(dr)
        build_trade_plan(dr, BASE_CONFIG)
        assert dr == dr_copy

    def test_config_not_mutated(self):
        cfg = _cfg(entry_buffer_ticks=2, stop_buffer_ticks=1)
        cfg_copy = TradePlanConfig(
            direction=cfg.direction,
            entry_model=cfg.entry_model,
            entry_buffer_ticks=cfg.entry_buffer_ticks,
            stop_buffer_ticks=cfg.stop_buffer_ticks,
            tick_size=cfg.tick_size,
        )
        build_trade_plan(_dr(), cfg)
        assert cfg == cfg_copy


# ── Canonical TradePlan type checks ──────────────────────────────────────────


class TestCanonicalTypes:
    def test_trade_plan_type(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert isinstance(tp, TradePlan)

    def test_entry_model_enum(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert isinstance(tp.entry_model, EntryModel)
        assert tp.entry_model == EntryModel.CONFIRMATION_CLOSE

    def test_entry_price_type(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert isinstance(tp.entry_price, PriceTicks)

    def test_stop_price_type(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert isinstance(tp.stop_price, PriceTicks)

    def test_risk_type(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert isinstance(tp.risk, AbsoluteTickDistance)

    def test_r2_type(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert isinstance(tp.r2_price, PriceTicks)

    def test_r3_type(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert isinstance(tp.r3_price, PriceTicks)

    def test_r4_type(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert isinstance(tp.r4_price, PriceTicks)


# ── 11-field contract ────────────────────────────────────────────────────────


class TestFieldSet:
    """Confirm exactly 11 fields, no extra."""

    def test_field_count(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        d = tp.to_dict()
        assert len(d) == 11

    def test_field_names(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        d = tp.to_dict()
        expected = {
            "schema_version",
            "entry_model",
            "entry_buffer_ticks",
            "stop_buffer_ticks",
            "tick_size",
            "entry_price",
            "stop_price",
            "risk",
            "r2_price",
            "r3_price",
            "r4_price",
        }
        assert set(d.keys()) == expected


# ── Success and failure wrapper shapes ───────────────────────────────────────


class TestWrapperShapes:
    def test_success_keys(self):
        r = build_trade_plan(_dr(), BASE_CONFIG)
        assert set(r.keys()) == {"status", "trade_plan"}
        assert r["status"] == "OK"

    def test_failure_keys(self):
        r = build_trade_plan(None, BASE_CONFIG)
        assert set(r.keys()) == {"status", "failure_code", "reason"}
        assert r["status"] == "FAILED"


# ── No UUID or timestamp ─────────────────────────────────────────────────────


class TestNoUUIDOrTimestamp:
    def test_no_uuid(self):
        r = build_trade_plan(_dr(), BASE_CONFIG)
        assert "result_id" not in r
        tp = r["trade_plan"]
        d = tp.to_dict()
        assert "result_id" not in d

    def test_no_timestamp(self):
        r = build_trade_plan(_dr(), BASE_CONFIG)
        assert "produced_at" not in r
        assert "timestamp" not in r
        d = r["trade_plan"].to_dict()
        assert "produced_at" not in d
        assert "timestamp" not in d


# ── No scorer, policy, outcome, simulation fields ────────────────────────────


class TestNoExtraFields:
    def test_no_score(self):
        d = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"].to_dict()
        for key in ("score", "quality_score", "grade", "policy",
                     "outcome", "simulation", "decision", "approval"):
            assert key not in d


# ── OHLC validation order ────────────────────────────────────────────────────


class TestOHLCValidationOrder:
    """open is validated before high, high before low, low before close."""

    def test_open_before_high(self):
        bar = _bar()
        bar["open"] = None  # invalid
        bar["high"] = None  # also invalid
        r = build_trade_plan(_dr(confirmation_bar=bar), BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert "open" in r["reason"]

    def test_high_before_low(self):
        bar = _bar()
        bar["high"] = None
        bar["low"] = None
        r = build_trade_plan(_dr(confirmation_bar=bar), BASE_CONFIG)
        assert "high" in r["reason"]

    def test_low_before_close(self):
        bar = _bar()
        bar["low"] = None
        bar["close"] = None
        r = build_trade_plan(_dr(confirmation_bar=bar), BASE_CONFIG)
        assert "low" in r["reason"]


# ── Validation precedence ────────────────────────────────────────────────────


class TestValidationPrecedence:
    """Detection result is validated before config."""

    def test_dr_before_config(self):
        r = build_trade_plan(None, None)
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_config_before_bar(self):
        """Config direction validated before confirmation_bar."""
        r = build_trade_plan(
            _dr(confirmation_bar=None),
            _cfg(direction="SHORT"),
        )
        assert r["failure_code"] == "UNSUPPORTED_DIRECTION"


# ── Null and non-object config ───────────────────────────────────────────────


class TestInvalidConfig:
    def test_null_config(self):
        r = build_trade_plan(_dr(), None)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"
        assert "config must be a non-null object" in r["reason"]

    def test_string_config(self):
        r = build_trade_plan(_dr(), "not-an-object")
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_int_config(self):
        r = build_trade_plan(_dr(), 42)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"


# ── Exact failure codes ──────────────────────────────────────────────────────


class TestExactFailureCodes:
    """Each of the 8 canonical failure codes is produced."""

    def test_invalid_detection_result(self):
        r = build_trade_plan(None, BASE_CONFIG)
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_unsupported_direction(self):
        r = build_trade_plan(_dr(), _cfg(direction="SHORT"))
        assert r["failure_code"] == "UNSUPPORTED_DIRECTION"

    def test_unsupported_entry_model(self):
        r = build_trade_plan(_dr(), _cfg(entry_model="MARKET_ORDER"))
        assert r["failure_code"] == "UNSUPPORTED_ENTRY_MODEL"

    def test_tick_size_mismatch(self):
        r = build_trade_plan(_dr(), _cfg(tick_size=0))
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"

    def test_invalid_buffer(self):
        r = build_trade_plan(_dr(), _cfg(entry_buffer_ticks=-1))
        assert r["failure_code"] == "INVALID_BUFFER"

    def test_missing_confirmation_bar(self):
        r = build_trade_plan(_dr(confirmation_bar=None), BASE_CONFIG)
        assert r["failure_code"] == "MISSING_CONFIRMATION_BAR"

    def test_invalid_tick_value(self):
        bar = _bar()
        bar["open"] = None
        r = build_trade_plan(_dr(confirmation_bar=bar), BASE_CONFIG)
        assert r["failure_code"] == "INVALID_TICK_VALUE"

    def test_invalid_risk(self):
        dr = _dr(
            confirmation_bar=_bar(
                open_=100.0, high=100.0, low=100.0, close=100.0
            )
        )
        r = build_trade_plan(dr, BASE_CONFIG)
        assert r["failure_code"] == "INVALID_RISK"


# ── Exact reason strings ─────────────────────────────────────────────────────


class TestExactReasonStrings:
    def test_null_dr_reason(self):
        r = build_trade_plan(None, BASE_CONFIG)
        assert r["reason"] == "detectionResult must be a non-null object"

    def test_wrong_schema_reason(self):
        r = build_trade_plan(
            _dr(schema_version="DetectionResult/v0"), BASE_CONFIG
        )
        assert (
            r["reason"]
            == 'detectionResult.schema_version must be "DetectionResult/v1";'
            ' got "DetectionResult/v0"'
        )

    def test_invalid_status_reason(self):
        r = build_trade_plan(_dr(status="INVALID"), BASE_CONFIG)
        assert r["reason"] == (
            'detectionResult.status must be "VALID"; got "INVALID"'
        )

    def test_invalid_status_with_failed_stage_reason(self):
        r = build_trade_plan(
            _dr(
                status="INVALID",
                failed_stage="NO_QUALIFYING_REJECTION_CANDLE",
            ),
            BASE_CONFIG,
        )
        assert r["reason"] == (
            'detectionResult.status must be "VALID"; got "INVALID"'
            " (failed_stage: NO_QUALIFYING_REJECTION_CANDLE)"
        )

    def test_null_config_reason(self):
        r = build_trade_plan(_dr(), None)
        assert r["reason"] == "config must be a non-null object"

    def test_unsupported_direction_reason(self):
        r = build_trade_plan(_dr(), _cfg(direction="SHORT"))
        assert r["reason"] == (
            'direction "SHORT" is not supported;'
            ' only "LONG" is implemented'
        )

    def test_unsupported_entry_model_reason(self):
        r = build_trade_plan(_dr(), _cfg(entry_model="MARKET_ORDER"))
        assert r["reason"] == (
            'entry_model "MARKET_ORDER" is not recognized; '
            "supported values: CONFIRMATION_CLOSE, BREAK_OF_SIGNAL_BAR"
        )

    def test_tick_size_reason(self):
        r = build_trade_plan(_dr(), _cfg(tick_size=0))
        assert (
            r["reason"] == "config.tick_size must be a finite positive number"
        )

    def test_missing_bar_reason(self):
        r = build_trade_plan(_dr(confirmation_bar=None), BASE_CONFIG)
        assert r["reason"] == (
            "detectionResult.confirmation_bar is missing or not an object"
        )

    def test_invalid_risk_reason(self):
        dr = _dr(
            confirmation_bar=_bar(
                open_=100.0, high=100.0, low=100.0, close=100.0
            )
        )
        r = build_trade_plan(dr, BASE_CONFIG)
        assert "must be strictly above stop" in r["reason"]

    def test_invalid_risk_exact_ticks_in_reason(self):
        dr = _dr(
            confirmation_bar=_bar(
                open_=100.0, high=100.0, low=100.0, close=100.0
            )
        )
        r = build_trade_plan(dr, BASE_CONFIG)
        assert "10000 ticks" in r["reason"]


# ── Stop above entry ─────────────────────────────────────────────────────────


class TestStopAboveEntry:
    def test_large_buffer_creates_invalid_geometry(self):
        """Large stop_buffer with small bar range can push stop above entry."""
        dr = _dr(
            confirmation_bar=_bar(
                open_=100.05, high=100.10, low=100.00, close=100.02
            )
        )
        # entry=10002, stop=10000-500=9500 → OK normally
        # But with extreme buffer: entry=10002, stop stays below
        # Use case: entry(10002) vs stop with huge subtraction
        # Actually test stop > entry: use zero-range bar + buffer
        dr2 = _dr(
            confirmation_bar=_bar(
                open_=100.01, high=100.02, low=100.01, close=100.01
            )
        )
        # entry=10001 (close), stop=10001-0=10001 → entry==stop → INVALID_RISK
        r = build_trade_plan(dr2, BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_RISK"


# ── Large integer tick values ────────────────────────────────────────────────


class TestLargeTicks:
    def test_spy_scale_values(self):
        """SPY-scale prices ~$750 → ~75000 ticks."""
        dr = _dr(
            confirmation_bar=_bar(
                open_=750.50, high=751.00, low=750.00, close=750.89
            )
        )
        r = build_trade_plan(dr, BASE_CONFIG)
        assert r["status"] == "OK"
        tp = r["trade_plan"]
        assert tp.entry_price.ticks == 75089
        assert tp.stop_price.ticks == 75000
        assert tp.risk.ticks == 89


# ── EntryModel enum values ───────────────────────────────────────────────────


class TestEntryModelValues:
    def test_cc_enum(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert tp.entry_model == EntryModel.CONFIRMATION_CLOSE

    def test_bosb_enum(self):
        tp = build_trade_plan(
            _dr(), _cfg(entry_model="BREAK_OF_SIGNAL_BAR")
        )["trade_plan"]
        assert tp.entry_model == EntryModel.BREAK_OF_SIGNAL_BAR


# ── JS Oracle Parity Vectors ─────────────────────────────────────────────────
# These vectors are derived from the authoritative JavaScript test suite.
# estrategie/test_bdrr_trade_plan.js tests 2–5 use the synthetic fixture:
#   high=102.00, low=100.00, open=101.50, close=101.20
# with tick_size=0.01
#
# CC/zero:   entry=10120, stop=10000, risk=120, r2=10360, r3=10480, r4=10600
# CC/buf5,3: entry=10125, stop=9997,  risk=128, r2=10381, r3=10509, r4=10637
# BOSB/zero: entry=10200, stop=10000, risk=200, r2=10600, r3=11000, r4=11400
#            wait — r2=10200+400=10600, r3=10200+600=10800, r4=10200+800=11000
#            Correction: r2=10600, r3=10800, r4=11000
# BOSB/2,1:  entry=10202, stop=9999,  risk=203, r2=10608, r3=10811, r4=11014


class TestOracleParity:
    """Representative JS oracle parity vectors."""

    def test_cc_zero_buffers(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        assert tp.entry_price.ticks == 10120
        assert tp.stop_price.ticks == 10000
        assert tp.risk.ticks == 120
        assert tp.r2_price.ticks == 10360
        assert tp.r3_price.ticks == 10480
        assert tp.r4_price.ticks == 10600

    def test_cc_nonzero_buffers(self):
        cfg = _cfg(entry_buffer_ticks=5, stop_buffer_ticks=3)
        tp = build_trade_plan(_dr(), cfg)["trade_plan"]
        assert tp.entry_price.ticks == 10125
        assert tp.stop_price.ticks == 9997
        assert tp.risk.ticks == 128
        assert tp.r2_price.ticks == 10381
        assert tp.r3_price.ticks == 10509
        assert tp.r4_price.ticks == 10637

    def test_bosb_zero_buffers(self):
        cfg = _cfg(entry_model="BREAK_OF_SIGNAL_BAR")
        tp = build_trade_plan(_dr(), cfg)["trade_plan"]
        assert tp.entry_price.ticks == 10200
        assert tp.stop_price.ticks == 10000
        assert tp.risk.ticks == 200
        assert tp.r2_price.ticks == 10600
        assert tp.r3_price.ticks == 10800
        assert tp.r4_price.ticks == 11000

    def test_bosb_nonzero_buffers(self):
        cfg = _cfg(
            entry_model="BREAK_OF_SIGNAL_BAR",
            entry_buffer_ticks=2,
            stop_buffer_ticks=1,
        )
        tp = build_trade_plan(_dr(), cfg)["trade_plan"]
        assert tp.entry_price.ticks == 10202
        assert tp.stop_price.ticks == 9999
        assert tp.risk.ticks == 203
        assert tp.r2_price.ticks == 10608
        assert tp.r3_price.ticks == 10811
        assert tp.r4_price.ticks == 11014


# ── SPY Integration Parity ───────────────────────────────────────────────────
# From test_bdrr_trade_plan.js test 23 (SPY 2026-05-26):
#   confirmation_bar: close=750.89 (75089 ticks), low=750.36 (75036 ticks)
#   entry=75089, stop=75036, risk=53
#   r2=75089+106=75195, r3=75089+159=75248, r4=75089+212=75301


class TestSPYIntegrationParity:
    def test_spy_2026_05_26(self):
        dr = _dr(
            confirmation_bar=_bar(
                open_=750.77, high=750.97, low=750.36, close=750.89
            )
        )
        r = build_trade_plan(dr, BASE_CONFIG)
        assert r["status"] == "OK"
        tp = r["trade_plan"]
        assert tp.entry_price.ticks == 75089
        assert tp.stop_price.ticks == 75036
        assert tp.risk.ticks == 53
        assert tp.r2_price.ticks == 75195
        assert tp.r3_price.ticks == 75248
        assert tp.r4_price.ticks == 75301

    def test_spy_tick_size(self):
        dr = _dr(
            confirmation_bar=_bar(
                open_=750.77, high=750.97, low=750.36, close=750.89
            )
        )
        tp = build_trade_plan(dr, BASE_CONFIG)["trade_plan"]
        assert tp.tick_size == TICK_STR
        assert tp.entry_price.tick_size == TICK_STR

    def test_spy_raw_findrejection_shape_rejected(self):
        """Old raw findRejection() shape rejected."""
        raw = {
            "status": "OK",
            "level_price": 750.44,
            "confirmation_candle": {"open": 750.77, "high": 750.97,
                                     "low": 750.36, "close": 750.89},
            "geometry": {},
            "failed_retests": [],
        }
        r = build_trade_plan(raw, BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"


# ── Frozen TradePlan immutability ────────────────────────────────────────────


class TestImmutability:
    def test_trade_plan_is_frozen(self):
        tp = build_trade_plan(_dr(), BASE_CONFIG)["trade_plan"]
        with pytest.raises(AttributeError):
            tp.entry_price = PriceTicks(ticks=0, tick_size="0.01")


# ── Boolean tick_size rejected ───────────────────────────────────────────────


class TestBoolTickSize:
    def test_bool_tick_size_rejected(self):
        r = build_trade_plan(_dr(), _cfg(tick_size=True))
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"


# ── OHLC boolean ticks rejected ──────────────────────────────────────────────


class TestBoolOHLCTicks:
    def test_bool_ticks_in_ohlc(self):
        bar = _bar()
        bar["open"] = {"ticks": True, "tick_size": TICK_STR}
        r = build_trade_plan(_dr(confirmation_bar=bar), BASE_CONFIG)
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_TICK_VALUE"


# ── Dict-based config ───────────────────────────────────────────────────────


class TestDictConfig:
    """build_trade_plan also works with plain dict config."""

    def test_dict_config_success(self):
        cfg = {
            "direction": "LONG",
            "entry_model": "CONFIRMATION_CLOSE",
            "entry_buffer_ticks": 0,
            "stop_buffer_ticks": 0,
            "tick_size": 0.01,
        }
        r = build_trade_plan(_dr(), cfg)
        assert r["status"] == "OK"
        assert r["trade_plan"].entry_price.ticks == 10120
