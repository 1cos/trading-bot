"""Tests for evaluate_trade_outcome_v2 — Rational R/R evaluator.

Covers:
    - LONG: target hit at 2R, 2.5R, 2.25R not reached, stopped
    - SHORT: target hit at 2.5R, stopped, symmetry with LONG
    - Intrabar: target + stop same bar → AMBIGUOUS
    - Validation: zero, negative, float rejected; Rational accepted
    - Rounding: below half, above half, exactly half, integer offset
    - Precision: no float used, serialization round-trip
    - Compatibility: same trade at 2R → v1 and v2 same economic outcome
"""

import copy

import pytest

from trading_lab.contracts.primitives import Rational
from trading_lab.contracts.trade_outcome import TradeOutcomeStatus
from trading_lab.contracts.trade_outcome_v2 import TradeOutcomeV2, rational_to_label
from trading_lab.trade_outcome_evaluator import (
    evaluate_trade_outcome,
    evaluate_trade_outcome_v2,
    _round_offset_ticks,
    TradeOutcomeConfig,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK = 0.01
TICK_STR = "0.01"
CONF_BAR_UTC_MS = 1000


def _pt(ticks, ts=TICK):
    return {"ticks": ticks, "tick_size": ts}


def _dr(**overrides):
    base = {
        "schema_version": "DetectionResult/v1",
        "result_id": "aaaaaaaa-0000-4000-8000-000000000001",
        "produced_at": "2026-07-01T10:00:00.000Z",
        "status": "VALID",
        "failed_stage": None,
        "session": {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": 0, "session_close_utc_ms": 99999,
            "timeframe_seconds": 300,
        },
        "preset_id": "test",
        "engine_version": "1.0.0",
        "direction": "LONG",
        "confirmation_bar": {
            "bar_utc_ms": CONF_BAR_UTC_MS,
            "open": _pt(10050), "high": _pt(10090),
            "low": _pt(10000), "close": _pt(10070),
            "volume": None,
        },
        "displacement_window": [],
        "retest_window": [],
        "failed_retests": [],
        "failed_retest_count": 0,
    }
    base.update(overrides)
    return base


def _tp(entry=10100, stop=10000, **overrides):
    """TradePlan dict with configurable entry/stop."""
    r = abs(entry - stop)
    base = {
        "schema_version": "TradePlan/v1",
        "entry_model": "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0,
        "tick_size": TICK,
        "entry_price": _pt(entry),
        "stop_price": _pt(stop),
        "risk": _pt(r),
        "r2_price": _pt(entry + 2 * r),
        "r3_price": _pt(entry + 3 * r),
        "r4_price": _pt(entry + 4 * r),
    }
    base.update(overrides)
    return base


def _tp_short(entry=9900, stop=10000, **overrides):
    """TradePlan dict for SHORT."""
    r = abs(entry - stop)
    base = {
        "schema_version": "TradePlan/v1",
        "entry_model": "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0,
        "tick_size": TICK,
        "entry_price": _pt(entry),
        "stop_price": _pt(stop),
        "risk": _pt(r),
        "r2_price": _pt(entry - 2 * r),
        "r3_price": _pt(entry - 3 * r),
        "r4_price": _pt(entry - 4 * r),
    }
    base.update(overrides)
    return base


_next_ms = [2000]


def _reset_ms(v=2000):
    _next_ms[0] = v


def _bar(hi, lo, utc_ms=None):
    if utc_ms is None:
        utc_ms = _next_ms[0]
        _next_ms[0] += 300000
    mid = round((hi + lo) / 2)
    return {
        "bar_utc_ms": utc_ms,
        "open": _pt(mid), "high": _pt(hi),
        "low": _pt(lo), "close": _pt(mid),
        "volume": None,
    }


def _cfg_v2(direction="LONG", r_num=2, r_den=1):
    return {"direction": direction, "exit_target_r": Rational(r_num, r_den)}


# ── _round_offset_ticks unit tests ───────────────────────────────────────────


class TestRoundOffsetTicks:
    def test_below_half_tick(self):
        """53 × 21/10 = 111.3 → 111"""
        assert _round_offset_ticks(53, Rational(21, 10)) == 111

    def test_above_half_tick(self):
        """53 × 23/10 = 121.9 → 122"""
        assert _round_offset_ticks(53, Rational(23, 10)) == 122

    def test_exactly_half_tick(self):
        """53 × 5/2 = 132.5 → 133 (rounds away from entry)"""
        assert _round_offset_ticks(53, Rational(5, 2)) == 133

    def test_integer_offset(self):
        """120 × 9/4 = 270.0 → 270"""
        assert _round_offset_ticks(120, Rational(9, 4)) == 270

    def test_integer_r(self):
        """100 × 2/1 = 200 → 200"""
        assert _round_offset_ticks(100, Rational(2, 1)) == 200

    def test_another_half(self):
        """47 × 5/2 = 117.5 → 118"""
        assert _round_offset_ticks(47, Rational(5, 2)) == 118


# ── LONG: target hit ─────────────────────────────────────────────────────────


class TestLongTargetHit:
    def test_2r_target_hit(self):
        """LONG, 2R, entry=10100, stop=10000, risk=100, target=10300."""
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(),
            [_bar(10350, 10150)],  # hi >= 10300
            _cfg_v2(r_num=2, r_den=1),
        )
        assert r["status"] == "OK"
        o = r["outcome"]
        assert isinstance(o, TradeOutcomeV2)
        assert o.schema_version == "TradeOutcome/v2"
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_price_ticks == 10300
        assert o.selected_exit_target_r == Rational(2, 1)
        assert o.selected_exit_target_label == "2R"
        # For integer R, effective == selected
        assert o.realized_r == Rational(200, 100)  # 2/1 reduced? No: Rational stores as-is
        assert o.realized_r == Rational(200, 100)

    def test_2_5r_target_hit(self):
        """LONG, 2.5R, entry=10100, stop=10000, risk=100, target=10350."""
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(),
            [_bar(10400, 10200)],  # hi >= 10350
            _cfg_v2(r_num=5, r_den=2),
        )
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_price_ticks == 10350
        assert o.selected_exit_target_r == Rational(5, 2)
        assert o.selected_exit_target_label == "2.5R"
        assert o.realized_r == Rational(250, 100)

    def test_2_25r_not_reached(self):
        """LONG, 2.25R, entry=10100, stop=10000, risk=100, target=10325.
        Price doesn't reach target → OPEN."""
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(),
            [_bar(10300, 10150)],  # hi=10300 < 10325
            _cfg_v2(r_num=9, r_den=4),
        )
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.OPEN
        assert o.exit_price_ticks is None
        assert o.realized_r is None

    def test_stopped(self):
        """LONG stop hit."""
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(),
            [_bar(10150, 9950)],  # lo <= 10000
            _cfg_v2(r_num=5, r_den=2),
        )
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.STOPPED
        assert o.exit_price_ticks == 10000
        assert o.realized_r == Rational(-1, 1)

    def test_ambiguous_same_bar(self):
        """LONG, target and stop hit on same bar → AMBIGUOUS."""
        _reset_ms()
        # risk=100, 2R target at 10300
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(),
            [_bar(10350, 9900)],  # hi >= 10300 AND lo <= 10000
            _cfg_v2(r_num=2, r_den=1),
        )
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.AMBIGUOUS
        assert o.exit_price_ticks is None


# ── SHORT: target hit ────────────────────────────────────────────────────────


class TestShortTargetHit:
    def test_2_5r_target_hit(self):
        """SHORT, 2.5R, entry=9900, stop=10000, risk=100, target=9650."""
        _reset_ms()
        dr = _dr(direction="SHORT")
        r = evaluate_trade_outcome_v2(
            dr, _tp_short(),
            [_bar(9800, 9600)],  # lo <= 9650
            _cfg_v2("SHORT", r_num=5, r_den=2),
        )
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_price_ticks == 9650
        assert o.selected_exit_target_r == Rational(5, 2)
        assert o.direction.value == "SHORT"
        assert o.realized_r == Rational(250, 100)

    def test_stopped(self):
        """SHORT stop hit."""
        _reset_ms()
        dr = _dr(direction="SHORT")
        r = evaluate_trade_outcome_v2(
            dr, _tp_short(),
            [_bar(10050, 9850)],  # hi >= 10000
            _cfg_v2("SHORT", r_num=5, r_den=2),
        )
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.STOPPED
        assert o.exit_price_ticks == 10000
        assert o.realized_r == Rational(-1, 1)

    def test_symmetry_with_long(self):
        """Same risk, same R → absolute offset distance is identical."""
        risk = 100
        r = Rational(5, 2)
        offset = _round_offset_ticks(risk, r)

        long_target = 10100 + offset
        short_target = 9900 - offset

        # Absolute distances from entry are identical
        assert long_target - 10100 == 9900 - short_target == 250


# ── Rounding tests ───────────────────────────────────────────────────────────


class TestRounding:
    def test_rounding_below_half(self):
        """risk=53, RR=21/10, offset=111.3 → 111. LONG target=10153+111=10264."""
        _reset_ms()
        tp = _tp(entry=10153, stop=10100)  # risk=53
        r = evaluate_trade_outcome_v2(
            _dr(), tp,
            [_bar(10264, 10200)],  # hi=10264 == target
            _cfg_v2(r_num=21, r_den=10),
        )
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_price_ticks == 10264
        # effective_r = 111/53
        assert o.realized_r == Rational(111, 53)

    def test_rounding_above_half(self):
        """risk=53, RR=23/10, offset=121.9 → 122. Target=10153+122=10275."""
        _reset_ms()
        tp = _tp(entry=10153, stop=10100)  # risk=53
        target = 10153 + 122
        r = evaluate_trade_outcome_v2(
            _dr(), tp,
            [_bar(target, 10200)],
            _cfg_v2(r_num=23, r_den=10),
        )
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_price_ticks == target
        assert o.realized_r == Rational(122, 53)

    def test_rounding_exactly_half(self):
        """risk=53, RR=5/2, offset=132.5 → 133. Target=10153+133=10286."""
        _reset_ms()
        tp = _tp(entry=10153, stop=10100)  # risk=53
        target = 10153 + 133
        r = evaluate_trade_outcome_v2(
            _dr(), tp,
            [_bar(target, 10200)],
            _cfg_v2(r_num=5, r_den=2),
        )
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_price_ticks == target
        assert o.realized_r == Rational(133, 53)

    def test_integer_offset(self):
        """risk=120, RR=9/4, offset=270 (exact). Target=10220+270=10490."""
        _reset_ms()
        tp = _tp(entry=10220, stop=10100)  # risk=120
        target = 10220 + 270
        r = evaluate_trade_outcome_v2(
            _dr(), tp,
            [_bar(target, 10300)],
            _cfg_v2(r_num=9, r_den=4),
        )
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_price_ticks == target
        assert o.realized_r == Rational(270, 120)

    def test_effective_rr_for_53_times_2_5(self):
        """Explicit: risk=53, RR requested=5/2, effective=133/53."""
        _reset_ms()
        tp = _tp(entry=10153, stop=10100)
        r = evaluate_trade_outcome_v2(
            _dr(), tp,
            [_bar(10153 + 133, 10200)],
            _cfg_v2(r_num=5, r_den=2),
        )
        o = r["outcome"]
        assert o.selected_exit_target_r == Rational(5, 2)
        assert o.realized_r == Rational(133, 53)
        # They're numerically close but not equal
        selected_dec = o.selected_exit_target_r.as_decimal()
        realized_dec = o.realized_r.as_decimal()
        assert selected_dec != realized_dec  # 2.5 vs 2.50943...


# ── Validation ───────────────────────────────────────────────────────────────


class TestValidation:
    def test_zero_rejected(self):
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(), [_bar(10200, 10050)],
            {"direction": "LONG", "exit_target_r": Rational(0, 1)},
        )
        assert r["status"] == "FAILED"
        assert "positive" in r["reason"]

    def test_negative_rejected(self):
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(), [_bar(10200, 10050)],
            {"direction": "LONG", "exit_target_r": Rational(-2, 1)},
        )
        assert r["status"] == "FAILED"
        assert "positive" in r["reason"]

    def test_float_rejected(self):
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(), [_bar(10200, 10050)],
            {"direction": "LONG", "exit_target_r": 2.5},
        )
        assert r["status"] == "FAILED"
        assert "Rational" in r["reason"]

    def test_int_rejected(self):
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(), [_bar(10200, 10050)],
            {"direction": "LONG", "exit_target_r": 2},
        )
        assert r["status"] == "FAILED"
        assert "Rational" in r["reason"]

    def test_rational_accepted(self):
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(), [_bar(10200, 10050)],
            _cfg_v2(r_num=21, r_den=10),
        )
        assert r["status"] == "OK"


# ── Precision and serialization ──────────────────────────────────────────────


class TestPrecision:
    def test_no_float_in_outcome(self):
        """All R fields must be Rational, never float."""
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(),
            [_bar(10350, 10150)],
            _cfg_v2(r_num=5, r_den=2),
        )
        o = r["outcome"]
        assert isinstance(o.selected_exit_target_r, Rational)
        assert isinstance(o.realized_r, Rational)
        assert isinstance(o.exit_target_r, Rational)
        assert isinstance(o.highest_target_r, Rational)

    def test_serialization_round_trip(self):
        """Serialize and reconstruct Rational without precision loss."""
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(),
            [_bar(10350, 10150)],
            _cfg_v2(r_num=5, r_den=2),
        )
        o = r["outcome"]
        d = o.to_dict()

        # Reconstruct Rationals from serialized form
        sel_r = Rational(
            d["selected_exit_target_r"]["numerator"],
            d["selected_exit_target_r"]["denominator"],
        )
        real_r = Rational(
            d["realized_r"]["numerator"],
            d["realized_r"]["denominator"],
        )
        assert sel_r == o.selected_exit_target_r
        assert real_r == o.realized_r
        assert d["schema_version"] == "TradeOutcome/v2"

    def test_label_matches_selected(self):
        """Label for selected R is canonical."""
        _reset_ms()
        r = evaluate_trade_outcome_v2(
            _dr(), _tp(),
            [_bar(10400, 10200)],
            _cfg_v2(r_num=15, r_den=4),  # 3.75R
        )
        o = r["outcome"]
        assert o.selected_exit_target_label == "3.75R"


# ── v1/v2 compatibility ─────────────────────────────────────────────────────


class TestV1V2Compatibility:
    def test_same_2r_outcome(self):
        """Same trade at 2R: v1 and v2 produce the same economic outcome."""
        _reset_ms()
        bars = [_bar(10350, 10150)]  # TARGET_HIT for both

        r1 = evaluate_trade_outcome(
            _dr(), _tp(), bars,
            TradeOutcomeConfig(direction="LONG", exit_target_r=2),
        )
        _reset_ms()
        r2 = evaluate_trade_outcome_v2(
            _dr(), _tp(), bars,
            _cfg_v2(r_num=2, r_den=1),
        )

        o1 = r1["outcome"]
        o2 = r2["outcome"]

        # Same economic outcome
        assert str(o1.outcome) == str(o2.outcome) == "TARGET_HIT"
        assert o1.exit_price_ticks == o2.exit_price_ticks == 10300
        assert o1.entry_price_ticks == o2.entry_price_ticks
        assert o1.stop_price_ticks == o2.stop_price_ticks

        # Schema differs
        assert o1.schema_version == "TradeOutcome/v1"
        assert o2.schema_version == "TradeOutcome/v2"

        # Types differ as expected
        assert isinstance(o1.selected_exit_target_r, int)
        assert isinstance(o2.selected_exit_target_r, Rational)

    def test_same_2r_stop(self):
        """Same stop at 2R: v1 and v2 produce the same result."""
        _reset_ms()
        bars = [_bar(10150, 9900)]  # STOPPED for both

        r1 = evaluate_trade_outcome(
            _dr(), _tp(), bars,
            TradeOutcomeConfig(direction="LONG", exit_target_r=2),
        )
        _reset_ms()
        r2 = evaluate_trade_outcome_v2(
            _dr(), _tp(), bars,
            _cfg_v2(r_num=2, r_den=1),
        )

        o1 = r1["outcome"]
        o2 = r2["outcome"]

        assert str(o1.outcome) == str(o2.outcome) == "STOPPED"
        assert o1.exit_price_ticks == o2.exit_price_ticks == 10000
