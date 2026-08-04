"""Tests for canonical evaluate_trade_outcome port.

Mirrors estrategie/test_bdrr_trade_outcome.js (178 checks).
"""

import copy
import math

import pytest

from trading_lab.trade_outcome_evaluator import (
    evaluate_trade_outcome,
    TradeOutcomeConfig,
)
from trading_lab.contracts.trade_outcome import TradeOutcome, TradeOutcomeStatus
from trading_lab.contracts.trade_plan import EntryModel
from trading_lab.contracts.enums import Direction


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK = 0.01
TICK_STR = "0.01"
CONF_BAR_UTC_MS = 1000

CONFIG4 = TradeOutcomeConfig(direction="LONG", exit_target_r=4)
CONFIG3 = TradeOutcomeConfig(direction="LONG", exit_target_r=3)
CONFIG2 = TradeOutcomeConfig(direction="LONG", exit_target_r=2)


def _pt(ticks, ts=TICK):
    """PriceTicks-like dict."""
    return {"ticks": ticks, "tick_size": ts}


def _dr(**overrides):
    """Synthetic DetectionResult/v1 dict."""
    base = {
        "schema_version": "DetectionResult/v1",
        "result_id": "aaaaaaaa-0000-4000-8000-000000000001",
        "produced_at": "2026-07-01T10:00:00.000Z",
        "status": "VALID",
        "failed_stage": None,
        "session": {
            "symbol": "TEST",
            "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": 0,
            "session_close_utc_ms": 99999,
            "timeframe_seconds": 300,
        },
        "preset_id": "test",
        "engine_version": "1.0.0",
        "direction": "LONG",
        "confirmation_bar": {
            "bar_utc_ms": CONF_BAR_UTC_MS,
            "open": _pt(10050),
            "high": _pt(10090),
            "low": _pt(10000),
            "close": _pt(10070),
            "volume": None,
        },
        "displacement_window": [],
        "retest_window": [],
        "failed_retests": [],
        "failed_retest_count": 0,
    }
    base.update(overrides)
    return base


def _tp(**overrides):
    """TradePlan dict: entry=10100, stop=10000, risk=100, 2R=10300, 3R=10400, 4R=10500."""
    e, s, r = 10100, 10000, 100
    base = {
        "schema_version": "TradePlan/v1",
        "entry_model": "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0,
        "tick_size": TICK,
        "entry_price": _pt(e),
        "stop_price": _pt(s),
        "risk": _pt(r),
        "r2_price": _pt(e + 2 * r),
        "r3_price": _pt(e + 3 * r),
        "r4_price": _pt(e + 4 * r),
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
        "open": _pt(mid),
        "high": _pt(hi),
        "low": _pt(lo),
        "close": _pt(mid),
        "volume": None,
    }


# ── 1. Entry not triggered (BOSB) ────────────────────────────────────────────


class TestEntryNotTriggered:
    def test_bosb_no_trigger(self):
        _reset_ms()
        tp = _tp(entry_model="BREAK_OF_SIGNAL_BAR")
        bars = [_bar(10090, 10050), _bar(10095, 10055)]
        r = evaluate_trade_outcome(_dr(), tp, bars, CONFIG4)
        assert r["status"] == "OK"
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.ENTRY_NOT_TRIGGERED
        assert o.entry_triggered is False
        assert o.realized_r is None

    def test_bosb_empty_bars(self):
        tp = _tp(entry_model="BREAK_OF_SIGNAL_BAR")
        r = evaluate_trade_outcome(_dr(), tp, [], CONFIG4)
        assert r["outcome"].outcome == TradeOutcomeStatus.ENTRY_NOT_TRIGGERED


# ── 2. STOPPED before any target ─────────────────────────────────────────────


class TestStopped:
    def test_stopped_before_target(self):
        _reset_ms()
        bars = [_bar(10150, 10060), _bar(10120, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        assert r["status"] == "OK"
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.STOPPED
        assert o.exit_bar_index == 1
        assert o.exit_price_ticks == 10000
        assert o.realized_r == -1
        assert o.highest_target_achieved is None


# ── 3. OPEN ──────────────────────────────────────────────────────────────────


class TestOpen:
    def test_open_after_session(self):
        _reset_ms()
        bars = [_bar(10150, 10060), _bar(10200, 10100)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        assert r["outcome"].outcome == TradeOutcomeStatus.SESSION_CLOSE
        assert r["outcome"].realized_r is not None
        assert r["outcome"].exit_bar_index is not None

    def test_cc_empty_bars_open(self):
        r = evaluate_trade_outcome(_dr(), _tp(), [], CONFIG4)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.SESSION_CLOSE
        assert o.entry_triggered is True
        assert o.entry_bar_utc_ms == CONF_BAR_UTC_MS
        assert o.first_eval_bar_index is None
        assert o.realized_r is None  # no bars to close on


# ── 4. AMBIGUOUS ─────────────────────────────────────────────────────────────


class TestAmbiguous:
    def test_stop_and_terminal_4r(self):
        _reset_ms()
        bars = [_bar(10150, 10060), _bar(10520, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.AMBIGUOUS
        assert o.exit_price_ticks is None
        assert o.realized_r is None
        assert o.exit_target_label is None


# ── 5. Configurable exit target ──────────────────────────────────────────────


class TestConfigurableExitTarget:
    def test_r2_target_hit(self):
        _reset_ms()
        bars = [_bar(10150, 10060), _bar(10350, 10110)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG2)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_target_label == "2R"
        assert o.exit_target_r == 2
        assert o.realized_r == 2

    def test_r3_intermediate_open(self):
        _reset_ms()
        bars = [_bar(10150, 10060), _bar(10350, 10110)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG3)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.SESSION_CLOSE
        assert o.highest_target_achieved == "2R"
        assert o.realized_r is not None  # SESSION_CLOSE computes R

    def test_r4_intermediate_open(self):
        _reset_ms()
        bars = [_bar(10150, 10060), _bar(10350, 10110)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.SESSION_CLOSE
        assert o.highest_target_achieved == "2R"
        assert o.realized_r is not None


# ── 6. 2R then stopped ──────────────────────────────────────────────────────


class TestTwoRThenStopped:
    def test_r2_target_hit_then_stop(self):
        _reset_ms()
        bars = [_bar(10350, 10110), _bar(10120, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG2)
        assert r["outcome"].outcome == TradeOutcomeStatus.TARGET_HIT
        assert r["outcome"].exit_bar_index == 0
        assert r["outcome"].realized_r == 2

    def test_r3_intermediate_then_stop(self):
        _reset_ms()
        bars = [_bar(10350, 10110), _bar(10120, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG3)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.STOPPED
        assert o.exit_bar_index == 1
        assert o.highest_target_achieved == "2R"
        assert o.realized_r == -1

    def test_r4_intermediate_then_stop(self):
        _reset_ms()
        bars = [_bar(10350, 10110), _bar(10120, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.STOPPED
        assert o.highest_target_achieved == "2R"
        assert o.realized_r == -1


# ── 7–8. 3R and 4R terminal targets ─────────────────────────────────────────


class TestTerminalTargets:
    def test_3r_terminal(self):
        _reset_ms()
        bars = [_bar(10420, 10110)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG3)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_target_label == "3R"
        assert o.exit_target_r == 3
        assert o.realized_r == 3

    def test_4r_terminal(self):
        _reset_ms()
        bars = [_bar(10550, 10110)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_target_label == "4R"
        assert o.exit_target_r == 4
        assert o.realized_r == 4

    def test_multiple_targets_one_bar(self):
        """Bar crosses 2R, 3R, and 4R all at once."""
        _reset_ms()
        bars = [_bar(10550, 10110)]  # hi >= 4R=10500
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_target_label == "4R"
        assert o.highest_target_achieved == "4R"
        assert o.highest_target_r == 4


# ── 9. CC entry timestamp ────────────────────────────────────────────────────


class TestCCEntryTimestamp:
    def test_entry_bar_is_confirmation_bar(self):
        _reset_ms(9000)
        bars = [_bar(10150, 10060, 9000), _bar(10200, 10110, 12000)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        o = r["outcome"]
        assert o.entry_triggered is True
        assert o.entry_bar_utc_ms == CONF_BAR_UTC_MS
        assert o.first_eval_bar_index == 0
        assert o.first_eval_bar_utc_ms == 9000
        assert o.entry_bar_utc_ms != o.first_eval_bar_utc_ms


# ── 10. BOSB entry bar index ────────────────────────────────────────────────


class TestBOSBEntryBarIndex:
    def test_entry_on_second_bar(self):
        _reset_ms(5000)
        tp = _tp(entry_model="BREAK_OF_SIGNAL_BAR")
        bars = [_bar(10090, 10050, 5000), _bar(10120, 10060, 8000)]
        r = evaluate_trade_outcome(_dr(), tp, bars, CONFIG4)
        o = r["outcome"]
        assert o.entry_triggered is True
        assert o.entry_bar_utc_ms == 8000
        assert o.bosb_entry_bar_index == 1
        assert o.first_eval_bar_index == 1


# ── 12. Frozen ambiguity: terminal only ──────────────────────────────────────


class TestFrozenAmbiguity:
    def test_r4_stop_plus_2r_only_is_stopped(self):
        """exit_target_r=4, stop + 2R (not terminal) → STOPPED, 2R not credited."""
        _reset_ms()
        bars = [_bar(10320, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.STOPPED
        assert o.realized_r == -1
        assert o.highest_target_achieved is None
        assert o.exit_price_ticks == 10000

    def test_r4_2r_earlier_then_stop(self):
        """2R reached cleanly on bar[0], then stop on bar[1]."""
        _reset_ms()
        bars = [_bar(10350, 10110), _bar(10120, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.STOPPED
        assert o.highest_target_achieved == "2R"
        assert o.realized_r == -1

    def test_r4_stop_plus_4r_terminal(self):
        _reset_ms()
        bars = [_bar(10520, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        assert r["outcome"].outcome == TradeOutcomeStatus.AMBIGUOUS

    def test_r3_stop_plus_2r_only_is_stopped(self):
        _reset_ms()
        bars = [_bar(10320, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG3)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.STOPPED
        assert o.highest_target_achieved is None

    def test_r3_stop_plus_3r_terminal(self):
        _reset_ms()
        bars = [_bar(10420, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG3)
        assert r["outcome"].outcome == TradeOutcomeStatus.AMBIGUOUS

    def test_r2_stop_plus_2r_terminal(self):
        _reset_ms()
        bars = [_bar(10320, 9950)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG2)
        assert r["outcome"].outcome == TradeOutcomeStatus.AMBIGUOUS


# ── 13–18. Validation tests ─────────────────────────────────────────────────


class TestInvalidExitTargetR:
    @pytest.mark.parametrize("bad_val", [None, 0, 1, 5, "2", 2.5])
    def test_invalid_values(self, bad_val):
        r = evaluate_trade_outcome(
            _dr(), _tp(), [],
            TradeOutcomeConfig(direction="LONG", exit_target_r=bad_val),
        )
        assert r["status"] == "FAILED"
        assert r["failure_code"] == "INVALID_CONFIG"


class TestMissingDirection:
    def test_no_direction(self):
        r = evaluate_trade_outcome(
            _dr(), _tp(), [],
            {"exit_target_r": 2},
        )
        assert r["status"] == "FAILED"


class TestInvalidDetectionResult:
    def test_null(self):
        r = evaluate_trade_outcome(None, _tp(), [], CONFIG2)
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_wrong_schema(self):
        r = evaluate_trade_outcome(
            _dr(schema_version="DetectionResult/v0"), _tp(), [], CONFIG2
        )
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_invalid_status(self):
        r = evaluate_trade_outcome(
            _dr(status="INVALID", failed_stage="BREAK_NOT_FOUND"),
            _tp(), [], CONFIG2,
        )
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_raw_shape(self):
        r = evaluate_trade_outcome(
            {"status": "OK", "confirmation_candle": {}},
            _tp(), [], CONFIG2,
        )
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"


class TestInvalidTradePlan:
    def test_null(self):
        r = evaluate_trade_outcome(_dr(), None, [], CONFIG2)
        assert r["failure_code"] == "INVALID_TRADE_PLAN"

    def test_wrong_schema(self):
        r = evaluate_trade_outcome(
            _dr(), _tp(schema_version="TradePlan/v0"), [], CONFIG2
        )
        assert r["failure_code"] == "INVALID_TRADE_PLAN"

    def test_zero_risk(self):
        tp = _tp()
        tp["entry_price"] = _pt(10100)
        tp["stop_price"] = _pt(10100)
        tp["risk"] = _pt(0)
        r = evaluate_trade_outcome(_dr(), tp, [], CONFIG2)
        assert r["failure_code"] == "INVALID_TRADE_PLAN"


class TestTickSizeMismatch:
    def test_bar_ohlc_mismatch(self):
        bad_bar = {
            "bar_utc_ms": 2000,
            "open": _pt(10050, 0.05),
            "high": _pt(10090),
            "low": _pt(10010),
            "close": _pt(10060),
            "volume": None,
        }
        r = evaluate_trade_outcome(_dr(), _tp(), [bad_bar], CONFIG2)
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"


class TestChronologicalOrder:
    def test_non_chronological_rejected(self):
        bars = [_bar(10150, 10060, 5000), _bar(10120, 10040, 3000)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG2)
        assert r["failure_code"] == "BARS_NOT_CHRONOLOGICAL"


class TestMalformedBars:
    def test_non_array(self):
        r = evaluate_trade_outcome(_dr(), _tp(), "not-an-array", CONFIG2)
        assert r["failure_code"] == "INVALID_BARS"

    def test_high_less_than_low(self):
        bad = {
            "bar_utc_ms": 2000,
            "open": _pt(10050),
            "high": _pt(9900),
            "low": _pt(10010),
            "close": _pt(10060),
            "volume": None,
        }
        r = evaluate_trade_outcome(_dr(), _tp(), [bad], CONFIG2)
        assert r["failure_code"] == "INVALID_BARS"


# ── 19. No mutation ─────────────────────────────────────────────────────────


class TestNoMutation:
    def test_inputs_not_mutated(self):
        _reset_ms()
        dr = _dr()
        tp = _tp()
        bars = [_bar(10150, 10060), _bar(10350, 9950)]
        cfg = TradeOutcomeConfig(direction="LONG", exit_target_r=4)

        dr_copy = copy.deepcopy(dr)
        tp_copy = copy.deepcopy(tp)
        bars_copy = copy.deepcopy(bars)

        evaluate_trade_outcome(dr, tp, bars, cfg)

        assert dr == dr_copy
        assert tp == tp_copy
        assert bars == bars_copy


# ── 20. Frozen output ────────────────────────────────────────────────────────


class TestFrozenOutput:
    def test_outcome_is_frozen(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10150, 10060)], CONFIG4)
        with pytest.raises(AttributeError):
            r["outcome"].outcome = TradeOutcomeStatus.STOPPED


# ── 21. Output schema ───────────────────────────────────────────────────────


class TestOutputSchema:
    def test_schema_fields(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10150, 10060)], CONFIG3)
        o = r["outcome"]
        assert o.schema_version == "TradeOutcome/v1"
        assert o.direction == Direction.LONG
        assert o.selected_exit_target_r == 3
        assert o.selected_exit_target_label == "3R"
        assert isinstance(o.entry_triggered, bool)
        assert o.r2_price_ticks == 10300
        assert o.r3_price_ticks == 10400
        assert o.r4_price_ticks == 10500

    def test_25_fields(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10150, 10060)], CONFIG4)
        d = r["outcome"].to_dict()
        assert len(d) == 25


# ── 22. realized_r values ───────────────────────────────────────────────────


class TestRealizedR:
    def test_stopped_minus_1(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10120, 9950)], CONFIG4)
        assert r["outcome"].realized_r == -1

    def test_target_2r(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10350, 10110)], CONFIG2)
        assert r["outcome"].realized_r == 2

    def test_target_3r(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10420, 10110)], CONFIG3)
        assert r["outcome"].realized_r == 3

    def test_session_close_has_r(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10150, 10060)], CONFIG4)
        assert r["outcome"].realized_r is not None  # SESSION_CLOSE computes R

    def test_ambiguous_null(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10520, 9950)], CONFIG4)
        assert r["outcome"].realized_r is None  # AMBIGUOUS has no R

    def test_not_triggered_null(self):
        tp = _tp(entry_model="BREAK_OF_SIGNAL_BAR")
        r = evaluate_trade_outcome(_dr(), tp, [], CONFIG4)
        assert r["outcome"].realized_r is None  # ENTRY_NOT_TRIGGERED has no R


# ── 24. BOSB same-bar entry+stop+target → AMBIGUOUS ────────────────────────


class TestBOSBSameBarAmbiguous:
    def test_entry_stop_terminal(self):
        _reset_ms()
        tp = _tp(entry_model="BREAK_OF_SIGNAL_BAR")
        bars = [_bar(10350, 9950)]
        r = evaluate_trade_outcome(_dr(), tp, bars, CONFIG2)
        assert r["outcome"].outcome == TradeOutcomeStatus.AMBIGUOUS


# ── Equality tests ──────────────────────────────────────────────────────────


class TestEqualityAtBoundaries:
    def test_equality_at_entry_bosb(self):
        """Bar high == entry exactly → triggers."""
        _reset_ms()
        tp = _tp(entry_model="BREAK_OF_SIGNAL_BAR")
        bars = [_bar(10100, 10050)]  # hi == entry=10100
        r = evaluate_trade_outcome(_dr(), tp, bars, CONFIG4)
        assert r["outcome"].entry_triggered is True

    def test_equality_at_stop(self):
        """Bar low == stop exactly → STOPPED."""
        _reset_ms()
        bars = [_bar(10150, 10000)]  # lo == stop=10000
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        assert r["outcome"].outcome == TradeOutcomeStatus.STOPPED

    def test_equality_at_2r(self):
        _reset_ms()
        bars = [_bar(10300, 10110)]  # hi == 2R=10300
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG2)
        assert r["outcome"].outcome == TradeOutcomeStatus.TARGET_HIT
        assert r["outcome"].exit_target_label == "2R"

    def test_equality_at_3r(self):
        _reset_ms()
        bars = [_bar(10400, 10110)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG3)
        assert r["outcome"].outcome == TradeOutcomeStatus.TARGET_HIT
        assert r["outcome"].exit_target_label == "3R"

    def test_equality_at_4r(self):
        _reset_ms()
        bars = [_bar(10500, 10110)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG4)
        assert r["outcome"].outcome == TradeOutcomeStatus.TARGET_HIT
        assert r["outcome"].exit_target_label == "4R"


# ── Entry models ────────────────────────────────────────────────────────────


class TestEntryModels:
    def test_cc_model(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10150, 10060)], CONFIG4)
        o = r["outcome"]
        assert o.entry_model == EntryModel.CONFIRMATION_CLOSE
        assert o.entry_triggered is True
        assert o.bosb_entry_bar_index is None

    def test_bosb_model(self):
        _reset_ms()
        tp = _tp(entry_model="BREAK_OF_SIGNAL_BAR")
        bars = [_bar(10120, 10060)]
        r = evaluate_trade_outcome(_dr(), tp, bars, CONFIG4)
        o = r["outcome"]
        assert o.entry_model == EntryModel.BREAK_OF_SIGNAL_BAR
        assert o.entry_triggered is True
        assert o.bosb_entry_bar_index == 0

    def test_non_cc_follows_bosb(self):
        """Any entry_model other than CONFIRMATION_CLOSE follows BOSB."""
        _reset_ms()
        tp = _tp(entry_model="MARKET_ORDER")
        bars = [_bar(10120, 10060)]
        r = evaluate_trade_outcome(_dr(), tp, bars, CONFIG4)
        o = r["outcome"]
        # Non-CC → BOSB behavior: entry triggers on bar high >= entry
        assert o.entry_triggered is True
        assert o.bosb_entry_bar_index == 0


# ── Nonzero buffers ─────────────────────────────────────────────────────────


class TestNonzeroBuffers:
    def test_evaluator_uses_plan_prices_not_buffers(self):
        """Evaluator consumes already-computed TradePlan prices."""
        _reset_ms()
        tp = _tp(entry_buffer_ticks=5, stop_buffer_ticks=3)
        # The evaluator reads entry_price/stop_price, not buffer values
        bars = [_bar(10150, 10060)]
        r = evaluate_trade_outcome(_dr(), tp, bars, CONFIG4)
        o = r["outcome"]
        assert o.entry_price_ticks == 10100
        assert o.stop_price_ticks == 10000


# ── Large integer ticks ─────────────────────────────────────────────────────


class TestLargeTicks:
    def test_spy_scale(self):
        _reset_ms()
        tp = _tp()
        tp["entry_price"] = _pt(75089)
        tp["stop_price"] = _pt(75036)
        tp["risk"] = _pt(53)
        tp["r2_price"] = _pt(75089 + 2 * 53)
        tp["r3_price"] = _pt(75089 + 3 * 53)
        tp["r4_price"] = _pt(75089 + 4 * 53)
        bars = [_bar(75100, 75000)]  # stop hit
        r = evaluate_trade_outcome(_dr(), tp, bars, CONFIG4)
        assert r["outcome"].outcome == TradeOutcomeStatus.STOPPED
        assert r["outcome"].exit_price_ticks == 75036


# ── Wrapper shapes ──────────────────────────────────────────────────────────


class TestWrapperShapes:
    def test_success_keys(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10150, 10060)], CONFIG4)
        assert set(r.keys()) == {"status", "outcome"}
        assert r["status"] == "OK"

    def test_failure_keys(self):
        r = evaluate_trade_outcome(None, _tp(), [], CONFIG2)
        assert set(r.keys()) == {"status", "failure_code", "reason"}
        assert r["status"] == "FAILED"


# ── Validation precedence ───────────────────────────────────────────────────


class TestValidationPrecedence:
    def test_dr_before_tp(self):
        r = evaluate_trade_outcome(None, None, [], CONFIG2)
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_tp_before_config(self):
        r = evaluate_trade_outcome(_dr(), None, [], None)
        assert r["failure_code"] == "INVALID_TRADE_PLAN"

    def test_config_before_bars(self):
        r = evaluate_trade_outcome(_dr(), _tp(), "not-list", None)
        assert r["failure_code"] == "INVALID_CONFIG"


# ── Exact reason strings ───────────────────────────────────────────────────


class TestExactReasonStrings:
    def test_null_dr(self):
        r = evaluate_trade_outcome(None, _tp(), [], CONFIG2)
        assert r["reason"] == "detectionResult must be a non-null object"

    def test_wrong_dr_schema(self):
        r = evaluate_trade_outcome(
            _dr(schema_version="DetectionResult/v0"), _tp(), [], CONFIG2
        )
        assert (
            r["reason"]
            == 'detectionResult.schema_version must be "DetectionResult/v1";'
            ' got "DetectionResult/v0"'
        )

    def test_null_tp(self):
        r = evaluate_trade_outcome(_dr(), None, [], CONFIG2)
        assert r["reason"] == "tradePlan must be a non-null object"

    def test_null_config(self):
        r = evaluate_trade_outcome(_dr(), _tp(), [], None)
        assert r["reason"] == "config must be a non-null object"

    def test_unsupported_direction(self):
        r = evaluate_trade_outcome(
            _dr(), _tp(), [],
            TradeOutcomeConfig(direction="UP", exit_target_r=2),
        )
        assert r["reason"] == (
            'direction "UP" is not supported;'
            ' only "LONG" and "SHORT" are implemented'
        )

    def test_invalid_exit_r_string(self):
        r = evaluate_trade_outcome(
            _dr(), _tp(), [],
            TradeOutcomeConfig(direction="LONG", exit_target_r="2"),
        )
        assert r["reason"] == (
            'config.exit_target_r must be 2, 3, or 4; got "2"'
        )

    def test_invalid_exit_r_null(self):
        r = evaluate_trade_outcome(
            _dr(), _tp(), [],
            TradeOutcomeConfig(direction="LONG", exit_target_r=None),
        )
        assert r["reason"] == (
            "config.exit_target_r must be 2, 3, or 4; got null"
        )

    def test_invalid_exit_r_float(self):
        r = evaluate_trade_outcome(
            _dr(), _tp(), [],
            TradeOutcomeConfig(direction="LONG", exit_target_r=2.5),
        )
        assert r["reason"] == (
            "config.exit_target_r must be 2, 3, or 4; got 2.5"
        )

    def test_non_array_bars(self):
        r = evaluate_trade_outcome(_dr(), _tp(), "not-an-array", CONFIG2)
        assert r["reason"] == "postConfirmationBars must be an array"

    def test_tp_zero_risk(self):
        tp = _tp()
        tp["risk"] = _pt(0)
        r = evaluate_trade_outcome(_dr(), tp, [], CONFIG2)
        assert r["reason"] == "tradePlan.risk.ticks must be positive"

    def test_tp_bad_price_ticks(self):
        tp = _tp()
        tp["entry_price"] = None
        r = evaluate_trade_outcome(_dr(), tp, [], CONFIG2)
        assert r["reason"] == (
            "tradePlan.entry_price must be a valid PriceTicks object"
        )

    def test_tick_size_mismatch_reason(self):
        bad_bar = {
            "bar_utc_ms": 2000,
            "open": _pt(10050, 0.05),
            "high": _pt(10090),
            "low": _pt(10010),
            "close": _pt(10060),
        }
        r = evaluate_trade_outcome(_dr(), _tp(), [bad_bar], CONFIG2)
        assert "does not match" in r["reason"]
        assert "tick_size" in r["reason"]


# ── Determinism ─────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_repeated_identical(self):
        _reset_ms()
        bars = [_bar(10350, 10110)]
        _reset_ms()
        bars2 = [_bar(10350, 10110)]
        r1 = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG2)
        r2 = evaluate_trade_outcome(_dr(), _tp(), bars2, CONFIG2)
        assert r1["outcome"] == r2["outcome"]


# ── Tick-size mismatch per OHLC field ───────────────────────────────────────


class TestTickSizeMismatchPerField:
    @pytest.mark.parametrize("field", ["open", "high", "low", "close"])
    def test_mismatch_on_field(self, field):
        bar = {
            "bar_utc_ms": 2000,
            "open": _pt(10050),
            "high": _pt(10090),
            "low": _pt(10010),
            "close": _pt(10060),
        }
        bar[field] = _pt(bar[field]["ticks"], 0.05)
        r = evaluate_trade_outcome(_dr(), _tp(), [bar], CONFIG2)
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"
        assert f"bar[0].{field}" in r["reason"]


# ── BOSB events on entry bar ────────────────────────────────────────────────


class TestBOSBEntryBarEvents:
    def test_entry_plus_stop_no_terminal(self):
        """BOSB: entry + stop but no terminal target → STOPPED."""
        _reset_ms()
        tp = _tp(entry_model="BREAK_OF_SIGNAL_BAR")
        # entry=10100, stop=10000, 4R=10500
        # bar: hi=10120 >= entry, lo=9950 <= stop, hi < 4R
        bars = [_bar(10120, 9950)]
        r = evaluate_trade_outcome(_dr(), tp, bars, CONFIG4)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.STOPPED
        assert o.entry_triggered is True
        assert o.exit_price_ticks == 10000

    def test_entry_plus_target_no_stop(self):
        """BOSB: entry + terminal target, no stop → TARGET_HIT."""
        _reset_ms()
        tp = _tp(entry_model="BREAK_OF_SIGNAL_BAR")
        bars = [_bar(10350, 10060)]  # hi >= 2R, lo > stop
        r = evaluate_trade_outcome(_dr(), tp, bars, CONFIG2)
        o = r["outcome"]
        assert o.outcome == TradeOutcomeStatus.TARGET_HIT
        assert o.exit_target_label == "2R"


# ── All seven failure codes ─────────────────────────────────────────────────


class TestAllSevenFailureCodes:
    def test_invalid_detection_result(self):
        r = evaluate_trade_outcome(None, _tp(), [], CONFIG2)
        assert r["failure_code"] == "INVALID_DETECTION_RESULT"

    def test_invalid_trade_plan(self):
        r = evaluate_trade_outcome(_dr(), None, [], CONFIG2)
        assert r["failure_code"] == "INVALID_TRADE_PLAN"

    def test_invalid_config(self):
        r = evaluate_trade_outcome(
            _dr(), _tp(), [],
            TradeOutcomeConfig(direction="LONG", exit_target_r=99),
        )
        assert r["failure_code"] == "INVALID_CONFIG"

    def test_unsupported_direction(self):
        r = evaluate_trade_outcome(
            _dr(), _tp(), [],
            TradeOutcomeConfig(direction="UP", exit_target_r=2),
        )
        assert r["failure_code"] == "UNSUPPORTED_DIRECTION"

    def test_invalid_bars(self):
        r = evaluate_trade_outcome(_dr(), _tp(), "x", CONFIG2)
        assert r["failure_code"] == "INVALID_BARS"

    def test_tick_size_mismatch(self):
        bad = {
            "bar_utc_ms": 2000,
            "open": _pt(10050, 0.05),
            "high": _pt(10090),
            "low": _pt(10010),
            "close": _pt(10060),
        }
        r = evaluate_trade_outcome(_dr(), _tp(), [bad], CONFIG2)
        assert r["failure_code"] == "TICK_SIZE_MISMATCH"

    def test_bars_not_chronological(self):
        bars = [_bar(10150, 10060, 5000), _bar(10120, 10040, 3000)]
        r = evaluate_trade_outcome(_dr(), _tp(), bars, CONFIG2)
        assert r["failure_code"] == "BARS_NOT_CHRONOLOGICAL"


# ── No UUID or timestamp generation ─────────────────────────────────────────


class TestNoGeneratedFields:
    def test_no_uuid(self):
        _reset_ms()
        r = evaluate_trade_outcome(_dr(), _tp(), [_bar(10150, 10060)], CONFIG4)
        d = r["outcome"].to_dict()
        assert "result_id" not in d
        assert "produced_at" not in d


# ── Dict-based config ───────────────────────────────────────────────────────


class TestDictConfig:
    def test_dict_config(self):
        _reset_ms()
        r = evaluate_trade_outcome(
            _dr(), _tp(), [_bar(10150, 10060)],
            {"direction": "LONG", "exit_target_r": 4},
        )
        assert r["status"] == "OK"
        assert r["outcome"].outcome == TradeOutcomeStatus.SESSION_CLOSE
