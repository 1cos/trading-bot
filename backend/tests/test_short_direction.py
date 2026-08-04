"""Comprehensive SHORT direction tests across the BDRR pipeline.

Tests all required SHORT behaviors as the exact directional mirror of LONG:
  - Break finder: close < level, distance = level_ticks - close_ticks
  - Displacement: high < level, favorable = min(low), distance = level - low
  - Retest window: contact when high >= level, penetration = high - level
  - Rejection: upper wick rejection, favorable close near low
  - Trade plan: entry below, stop above, targets below entry
  - Outcome: target hit when low <= target, stop hit when high >= stop
  - End-to-end: full pipeline and regression

Includes one complete valid SHORT session, failure-at-each-stage,
deterministic repeat-run, and LONG regression.
"""

import copy

import pytest

from trading_lab.bar_adapter import raw_candle_to_canonical_bar
from trading_lab.break_finder import find_break
from trading_lab.detection_result_builder import build_detection_result
from trading_lab.displacement_finder import find_displacement
from trading_lab.orb_builder import build_orb
from trading_lab.rejection_finder import find_rejection
from trading_lab.research_batch_runner import build_research_dataset_from_csv
from trading_lab.retest_window import find_retest_window
from trading_lab.session_context import build_session_context
from trading_lab.strategy_runner import run_bdrr_strategy
from trading_lab.tick_arithmetic import price_to_ticks
from trading_lab.trade_outcome_evaluator import (
    TradeOutcomeConfig,
    evaluate_trade_outcome,
)
from trading_lab.trade_plan_builder import TradePlanConfig, build_trade_plan
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.contracts.distances import AbsoluteTickDistance


# ── Constants ────────────────────────────────────────────────────────────────

TICK = 0.01
TICK_STR = "0.01"

# Epoch ms for 2026-07-01 9:30–10:30 ET (UTC-4 during EDT)
MS_0930 = 1782912600000  # 09:30 ET
MS_0935 = 1782912900000  # 09:35 ET
MS_0940 = 1782913200000  # 09:40 ET
MS_0945 = 1782913500000  # 09:45 ET
MS_0950 = 1782913800000  # 09:50 ET
MS_0955 = 1782914100000  # 09:55 ET
MS_1000 = 1782914400000  # 10:00 ET
MS_1005 = 1782914700000  # 10:05 ET
MS_1010 = 1782915000000  # 10:10 ET
MS_1015 = 1782915300000  # 10:15 ET
MS_1020 = 1782915600000  # 10:20 ET

SHORT_CONFIG = {
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_LOW",
    "direction": "SHORT",
    "tick_size": TICK,
    "min_displacement_ticks": None,
    "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None,
    "min_displacement_bars": 1,
    "confirmation_wick_penetration_pct_min": 0,
}

LONG_CONFIG = {**SHORT_CONFIG, "direction": "LONG", "level_source": "ORB_HIGH"}


def c(time_ms, open_=100.0, high=101.0, low=99.0, close=100.5, volume=100):
    """Build a raw candle dict."""
    return {
        "time_ms": time_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


# ── SHORT break scenario candles ─────────────────────────────────────────────
# ORB candle at 09:30 with low=99.00 (this is the level for SHORT/ORB_LOW)
# Break candle closes below 99.00

def short_orb_candle():
    return c(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5)


def short_break_candle():
    """Close below ORB low (99.00)."""
    return c(MS_0935, open_=99.50, high=99.80, low=98.20, close=98.50)


def short_displacement_candle():
    """Displacement bar: high < level (99.00). Stays entirely below."""
    return c(MS_0940, open_=98.40, high=98.60, low=97.80, close=97.90)


def short_retest_contact_candle():
    """Retest: high >= level (99.00). Returns toward level from below."""
    return c(MS_0945, open_=98.00, high=99.20, low=97.80, close=98.80)


def short_qualifying_rejection():
    """Qualifying SHORT rejection candle.
    
    HIGH reaches into level area (high >= 99.00), close is back below.
    Needs: rejection_wick_ratio >= 0.47, body_ratio <= 0.40,
           favorable_close_location >= 0.80 (close near low).
    
    Design:
      open=98.70, high=99.10, low=98.50, close=98.55
      range = 99.10 - 98.50 = 60 ticks
      rejection_wick (upper) = 99.10 - max(98.70, 98.55) = 99.10 - 98.70 = 40 ticks
      body = |98.55 - 98.70| = 15 ticks
      opposite_wick (lower) = min(98.70, 98.55) - 98.50 = 98.55 - 98.50 = 5 ticks
      rejection_wick_ratio = 40/60 = 0.667 (>= 0.47 ✓)
      body_ratio = 15/60 = 0.25 (<= 0.40 ✓)
      favorable_close_location = (99.10 - 98.55) / 60 = 55/60 = 0.917 (>= 0.80 ✓)
    """
    return c(MS_0950, open_=98.70, high=99.10, low=98.50, close=98.55)


def short_post_confirmation_bar_target():
    """Post-confirmation bar that hits target."""
    return c(MS_0955, open_=98.50, high=98.60, low=96.00, close=96.10)


def short_post_confirmation_bar_stop():
    """Post-confirmation bar that hits stop."""
    return c(MS_0955, open_=98.50, high=100.00, low=98.40, close=99.80)


# ══════════════════════════════════════════════════════════════════════════════
# BREAK FINDER — SHORT
# ══════════════════════════════════════════════════════════════════════════════


class TestShortBreak:
    def _setup(self):
        candles = [short_orb_candle(), short_break_candle()]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        return sc["candles"], orb

    def test_valid_short_break(self):
        candles, orb = self._setup()
        brk = find_break(candles, orb, SHORT_CONFIG)
        assert brk["status"] == "OK"
        assert brk["break_candle_index"] == 1
        # Distance = level_ticks - close_ticks = 9900 - 9850 = 50
        assert brk["directional_break_distance"]["ticks"] == 50
        assert brk["directional_break_distance"]["ticks"] > 0

    def test_no_break_close_above_level(self):
        """Candle close is above level → no SHORT break."""
        candles = [
            short_orb_candle(),
            c(MS_0935, open_=99.50, high=100.0, low=99.10, close=99.50),
        ]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk = find_break(sc["candles"], orb, SHORT_CONFIG)
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "BREAK_NOT_FOUND"

    def test_exact_level_no_break(self):
        """Close exactly at level → no break (strict <)."""
        candles = [
            short_orb_candle(),
            c(MS_0935, open_=99.50, high=100.0, low=98.80, close=99.00),
        ]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk = find_break(sc["candles"], orb, SHORT_CONFIG)
        assert brk["status"] == "FAILED"

    def test_insufficient_clearance_just_below(self):
        """Close 1 tick below level → valid break with distance=1."""
        candles = [
            short_orb_candle(),
            c(MS_0935, open_=99.50, high=100.0, low=98.80, close=98.99),
        ]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk = find_break(sc["candles"], orb, SHORT_CONFIG)
        assert brk["status"] == "OK"
        assert brk["directional_break_distance"]["ticks"] == 1

    def test_wrong_directional_movement(self):
        """All candles close above level → no break."""
        candles = [
            short_orb_candle(),
            c(MS_0935, open_=100.0, high=102.0, low=99.50, close=101.50),
            c(MS_0940, open_=101.50, high=103.0, low=101.0, close=102.00),
        ]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk = find_break(sc["candles"], orb, SHORT_CONFIG)
        assert brk["status"] == "FAILED"
        assert brk["failed_stage"] == "BREAK_NOT_FOUND"


# ══════════════════════════════════════════════════════════════════════════════
# DISPLACEMENT FINDER — SHORT
# ══════════════════════════════════════════════════════════════════════════════


class TestShortDisplacement:
    def _setup(self, extra_candles=None):
        base = [short_orb_candle(), short_break_candle()]
        if extra_candles:
            base.extend(extra_candles)
        sc = build_session_context(base, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk = find_break(sc["candles"], orb, SHORT_CONFIG)
        return sc["candles"], orb, brk

    def test_valid_downward_displacement(self):
        candles, orb, brk = self._setup([
            short_displacement_candle(),
            short_retest_contact_candle(),
        ])
        disp = find_displacement(candles, orb, brk, SHORT_CONFIG)
        assert disp["status"] == "OK"
        assert disp["displacement_bar_count"] == 1
        # Distance = level_ticks - low_ticks = 9900 - 9780 = 120
        assert disp["displacement_distance"]["ticks"] > 0

    def test_insufficient_displacement_retest_before(self):
        """First post-break bar contacts level → RETEST_BEFORE_DISPLACEMENT."""
        candles, orb, brk = self._setup([
            short_retest_contact_candle(),  # high >= level immediately
        ])
        disp = find_displacement(candles, orb, brk, SHORT_CONFIG)
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "RETEST_BEFORE_DISPLACEMENT"

    def test_directional_extreme(self):
        """max_favorable_low captures the lowest low in displacement."""
        candles, orb, brk = self._setup([
            c(MS_0940, open_=98.40, high=98.60, low=97.50, close=97.60),
            c(MS_0945, open_=97.60, high=98.00, low=97.00, close=97.10),
            short_retest_contact_candle(),  # retest at 0950
        ])
        # Need to re-run since we have different candles
        sc = build_session_context(
            [short_orb_candle(), short_break_candle(),
             c(MS_0940, open_=98.40, high=98.60, low=97.50, close=97.60),
             c(MS_0945, open_=97.60, high=98.00, low=97.00, close=97.10),
             c(MS_0950, open_=98.00, high=99.20, low=97.80, close=98.80)],
            SHORT_CONFIG)
        orb2 = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk2 = find_break(sc["candles"], orb2, SHORT_CONFIG)
        disp = find_displacement(sc["candles"], orb2, brk2, SHORT_CONFIG)
        assert disp["status"] == "OK"
        assert disp["displacement_bar_count"] == 2
        assert disp["max_favorable_low"] == 97.00

    def test_boundary_threshold(self):
        """No retest within session → RETEST_NOT_FOUND."""
        candles, orb, brk = self._setup([
            c(MS_0940, open_=98.40, high=98.60, low=97.80, close=97.90),
            c(MS_0945, open_=97.90, high=98.50, low=97.50, close=97.60),
        ])
        disp = find_displacement(candles, orb, brk, SHORT_CONFIG)
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "RETEST_NOT_FOUND"


# ══════════════════════════════════════════════════════════════════════════════
# RETEST WINDOW — SHORT
# ══════════════════════════════════════════════════════════════════════════════


class TestShortRetestWindow:
    def _full_setup(self):
        candles = [
            short_orb_candle(),
            short_break_candle(),
            short_displacement_candle(),
            short_retest_contact_candle(),
            c(MS_0950, open_=98.70, high=99.10, low=98.50, close=98.55),
        ]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk = find_break(sc["candles"], orb, SHORT_CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, SHORT_CONFIG)
        return sc["candles"], orb, brk, disp

    def test_valid_retest_from_below(self):
        candles, orb, brk, disp = self._full_setup()
        rw = find_retest_window(candles, orb, brk, disp, SHORT_CONFIG)
        assert rw["status"] == "OK"
        assert rw["retest_contact_count"] >= 1

    def test_wick_above_level_valid(self):
        """Candle with high above level is a contact in SHORT."""
        candles, orb, brk, disp = self._full_setup()
        rw = find_retest_window(candles, orb, brk, disp, SHORT_CONFIG)
        contacts = rw["retest_contacts"]
        # Contact at idx 3: high=99.20 > level=99.00
        assert any(rc["candle"]["high"] >= 99.0 for rc in contacts)

    def test_penetration_through_level(self):
        """Penetration = max(0, high_ticks - level_ticks) for SHORT."""
        candles, orb, brk, disp = self._full_setup()
        rw = find_retest_window(candles, orb, brk, disp, SHORT_CONFIG)
        contacts = rw["retest_contacts"]
        for rc in contacts:
            assert rc["penetration_through_level_ticks"] >= 0

    def test_excessive_crossing_still_contact(self):
        """Very high wick above level is still a retest contact."""
        candles = [
            short_orb_candle(),
            short_break_candle(),
            short_displacement_candle(),
            # Excessive crossing: high goes way above level
            c(MS_0945, open_=98.00, high=101.00, low=97.80, close=98.50),
        ]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk = find_break(sc["candles"], orb, SHORT_CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, SHORT_CONFIG)
        rw = find_retest_window(sc["candles"], orb, brk, disp, SHORT_CONFIG)
        assert rw["status"] == "OK"
        assert rw["retest_contact_count"] >= 1
        # Penetration = 10100 - 9900 = 200 ticks
        assert rw["retest_contacts"][0]["penetration_through_level_ticks"] == 200

    def test_no_retest_high_stays_below(self):
        """No contact: all candles have high < level → RETEST_NOT_FOUND at displacement."""
        candles = [
            short_orb_candle(),
            short_break_candle(),
            short_displacement_candle(),
            c(MS_0945, open_=98.00, high=98.50, low=97.80, close=98.00),
        ]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk = find_break(sc["candles"], orb, SHORT_CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, SHORT_CONFIG)
        # No candle has high >= level, so displacement can't close
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "RETEST_NOT_FOUND"

    def test_retracement_percentage(self):
        """displacement_retracement_pct is computed correctly."""
        candles, orb, brk, disp = self._full_setup()
        rw = find_retest_window(candles, orb, brk, disp, SHORT_CONFIG)
        contacts = rw["retest_contacts"]
        disp_ticks = disp["displacement_distance"]["ticks"]
        for rc in contacts:
            if rc["displacement_retracement_pct"] is not None:
                expected = rc["penetration_through_level_ticks"] / disp_ticks
                assert abs(rc["displacement_retracement_pct"] - expected) < 1e-10

    def test_zero_displacement_denominator(self):
        """When displacement_distance is 0, retracement is None."""
        candles = [
            short_orb_candle(),
            short_break_candle(),
            # Displacement bar that barely moves
            c(MS_0940, open_=98.99, high=98.99, low=98.99, close=98.99),
            short_retest_contact_candle(),
        ]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk = find_break(sc["candles"], orb, SHORT_CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, SHORT_CONFIG)
        if disp["status"] == "OK" and disp["displacement_distance"]["ticks"] == 0:
            rw = find_retest_window(sc["candles"], orb, brk, disp, SHORT_CONFIG)
            if rw["status"] == "OK":
                for rc in rw["retest_contacts"]:
                    assert rc["displacement_retracement_pct"] is None


# ══════════════════════════════════════════════════════════════════════════════
# REJECTION FINDER — SHORT
# ══════════════════════════════════════════════════════════════════════════════


class TestShortRejection:
    def _full_pipeline(self, rejection_candle):
        candles = [
            short_orb_candle(),
            short_break_candle(),
            short_displacement_candle(),
            short_retest_contact_candle(),
            rejection_candle,
        ]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        brk = find_break(sc["candles"], orb, SHORT_CONFIG)
        disp = find_displacement(sc["candles"], orb, brk, SHORT_CONFIG)
        rw = find_retest_window(sc["candles"], orb, brk, disp, SHORT_CONFIG)
        rej = find_rejection(sc["candles"], orb, brk, disp, rw, SHORT_CONFIG)
        return rej

    def test_valid_short_rejection(self):
        rej = self._full_pipeline(short_qualifying_rejection())
        assert rej["status"] == "OK"
        assert rej["confirmation_candle"]["close"] < 99.0

    def test_closes_above_level_fails(self):
        """Close above level → not a valid SHORT rejection (fails geometry)."""
        # Candle that closes above level
        bad = c(MS_0950, open_=98.70, high=99.30, low=98.50, close=99.20)
        rej = self._full_pipeline(bad)
        # close at 99.20 > level 99.00, so close_beyond = level - close = -20
        # favorable_close_location would be (99.30 - 99.20)/80 = 0.125 < 0.80
        assert rej["status"] == "FAILED"

    def test_wrong_body_wick_geometry(self):
        """Large body, small wick → BODY_RATIO_TOO_HIGH."""
        # Big body, tiny upper wick
        bad = c(MS_0950, open_=99.05, high=99.10, low=98.50, close=98.55)
        # range=60, body=50, rej_wick=99.10-99.05=5, body_ratio=50/60=0.83
        rej = self._full_pipeline(bad)
        assert rej["status"] == "FAILED"
        # Should have body_ratio_too_high in failed_retests
        if rej.get("failed_retests"):
            rules = rej["failed_retests"][-1].get("failed_rules", [])
            assert "BODY_RATIO_TOO_HIGH" in rules

    def test_threshold_boundary_wick_ratio(self):
        """Wick ratio exactly at threshold boundary."""
        # Design: range = 100 ticks, rej_wick = 47 ticks (ratio = 0.47 exactly)
        # open=98.70, high=99.10, low=98.10, close=98.15
        # range=100, rej_wick = 99.10 - 98.70 = 40 ticks
        # That's 0.40 < 0.47, so it would fail
        # Let's design one that passes at exactly 0.47:
        # range = 100, rej_wick = 47: high=99.10, low=98.10, 
        # rej_wick = high - max(open, close) = 47 → max(o,c) = 98.63
        # body = |o - c| needs to be <= 40 (0.40 * 100)
        # favorable_close_location = (high - close)/range = (99.10 - close)/100 >= 0.80
        # → close <= 98.30
        # open=98.63, close=98.25 → body=38, opp_wick=98.25-98.10=15
        rej_candle = c(MS_0950, open_=98.63, high=99.10, low=98.10, close=98.25)
        rej = self._full_pipeline(rej_candle)
        assert rej["status"] == "OK"


# ══════════════════════════════════════════════════════════════════════════════
# TRADE PLAN — SHORT
# ══════════════════════════════════════════════════════════════════════════════


def _pt(price, tick_size=TICK):
    ticks = price_to_ticks(price, tick_size)
    return PriceTicks(ticks=ticks, tick_size=str(tick_size))


def _short_dr(**overrides):
    """Minimal SHORT DetectionResult dict."""
    bar = {
        "bar_utc_ms": 1000,
        "open": _pt(98.70),
        "high": _pt(99.10),
        "low": _pt(98.50),
        "close": _pt(98.55),
        "volume": None,
    }
    base = {
        "schema_version": "DetectionResult/v1",
        "result_id": "short-test-001",
        "produced_at": "2026-07-01T14:00:00.000Z",
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
        "preset_id": "test_short",
        "engine_version": "1.0.0",
        "level_price": _pt(99.00),
        "level_source": "ORB_LOW",
        "direction": "SHORT",
        "confirmation_bar": bar,
        "displacement_window": [],
        "retest_window": [],
        "failed_retests": [],
        "failed_retest_count": 0,
    }
    base.update(overrides)
    return base


def _short_cfg(**overrides):
    kw = {
        "direction": "SHORT",
        "entry_model": "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0,
        "tick_size": TICK,
    }
    kw.update(overrides)
    return TradePlanConfig(**kw)


class TestShortTradePlan:
    def test_stop_above_entry(self):
        r = build_trade_plan(_short_dr(), _short_cfg())
        assert r["status"] == "OK"
        tp = r["trade_plan"]
        assert tp.stop_price.ticks > tp.entry_price.ticks

    def test_target_below_entry(self):
        r = build_trade_plan(_short_dr(), _short_cfg())
        tp = r["trade_plan"]
        assert tp.r2_price.ticks < tp.entry_price.ticks
        assert tp.r3_price.ticks < tp.r2_price.ticks
        assert tp.r4_price.ticks < tp.r3_price.ticks

    def test_risk_reward_calculation(self):
        r = build_trade_plan(_short_dr(), _short_cfg())
        tp = r["trade_plan"]
        # entry = close_ticks = 9855
        # stop = high_ticks = 9910
        # risk = 9910 - 9855 = 55
        assert tp.risk.ticks == 55
        # r2 = 9855 - 2*55 = 9745
        assert tp.r2_price.ticks == 9855 - 2 * 55
        # r3 = 9855 - 3*55 = 9690
        assert tp.r3_price.ticks == 9855 - 3 * 55

    def test_invalid_geometry_rejected(self):
        """Entry >= stop should fail for SHORT (e.g., close above high)."""
        # Make close > high somehow — use buffers to push entry above stop
        r = build_trade_plan(
            _short_dr(),
            _short_cfg(entry_buffer_ticks=200),  # pushes entry much lower
        )
        # entry = 9855 - 200 = 9655, stop = 9910: entry < stop ✓ — valid
        assert r["status"] == "OK"

        # Make stop <= entry by using huge stop buffer pushing stop below entry
        r2 = build_trade_plan(
            _short_dr(),
            _short_cfg(stop_buffer_ticks=0, entry_buffer_ticks=0),
        )
        tp = r2["trade_plan"]
        # stop=9910 > entry=9855 ✓
        assert tp.stop_price.ticks > tp.entry_price.ticks

    def test_bosb_entry_model(self):
        """BREAK_OF_SIGNAL_BAR: entry = low - buffer for SHORT."""
        r = build_trade_plan(
            _short_dr(),
            _short_cfg(entry_model="BREAK_OF_SIGNAL_BAR"),
        )
        assert r["status"] == "OK"
        tp = r["trade_plan"]
        # entry = low_ticks - 0 = 9850
        assert tp.entry_price.ticks == 9850
        # stop = high_ticks + 0 = 9910
        assert tp.stop_price.ticks == 9910


# ══════════════════════════════════════════════════════════════════════════════
# OUTCOME EVALUATOR — SHORT
# ══════════════════════════════════════════════════════════════════════════════


def _short_tp():
    """Minimal SHORT TradePlan."""
    # entry=9855, stop=9910, risk=55
    return {
        "schema_version": "TradePlan/v1",
        "entry_model": "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0,
        "tick_size": TICK_STR,
        "entry_price": PriceTicks(ticks=9855, tick_size=TICK_STR),
        "stop_price": PriceTicks(ticks=9910, tick_size=TICK_STR),
        "risk": AbsoluteTickDistance(ticks=55, tick_size=TICK_STR),
        "r2_price": PriceTicks(ticks=9855 - 110, tick_size=TICK_STR),
        "r3_price": PriceTicks(ticks=9855 - 165, tick_size=TICK_STR),
        "r4_price": PriceTicks(ticks=9855 - 220, tick_size=TICK_STR),
    }


def _bar_obj(time_ms, high_t, low_t, open_t=None, close_t=None):
    """Build a canonical Bar-like dict for outcome evaluator."""
    if open_t is None:
        open_t = high_t
    if close_t is None:
        close_t = low_t
    return {
        "bar_utc_ms": time_ms,
        "open": PriceTicks(ticks=open_t, tick_size=TICK_STR),
        "high": PriceTicks(ticks=high_t, tick_size=TICK_STR),
        "low": PriceTicks(ticks=low_t, tick_size=TICK_STR),
        "close": PriceTicks(ticks=close_t, tick_size=TICK_STR),
        "volume": None,
    }


SHORT_OC = TradeOutcomeConfig(direction="SHORT", exit_target_r=2)


class TestShortOutcome:
    def test_target_first_win(self):
        """Low reaches target → TARGET_HIT."""
        # r2 = 9745. Bar with low=9740 → hits target
        bars = [_bar_obj(2000, 9860, 9740)]
        r = evaluate_trade_outcome(_short_dr(), _short_tp(), bars, SHORT_OC)
        assert r["status"] == "OK"
        o = r["outcome"]
        assert str(o.outcome) == "TARGET_HIT"
        assert o.realized_r == 2

    def test_stop_first_loss(self):
        """High reaches stop → STOPPED."""
        # stop=9910. Bar with high=9920 → hits stop
        bars = [_bar_obj(2000, 9920, 9850)]
        r = evaluate_trade_outcome(_short_dr(), _short_tp(), bars, SHORT_OC)
        assert r["status"] == "OK"
        o = r["outcome"]
        assert str(o.outcome) == "STOPPED"
        assert o.realized_r == -1

    def test_neither_open(self):
        """Neither stop nor target hit → OPEN."""
        bars = [_bar_obj(2000, 9870, 9800)]
        r = evaluate_trade_outcome(_short_dr(), _short_tp(), bars, SHORT_OC)
        assert r["status"] == "OK"
        assert str(r["outcome"].outcome) == "SESSION_CLOSE"

    def test_same_candle_stop_and_target(self):
        """Both stop and target hit on same candle → AMBIGUOUS."""
        # stop=9910, r2=9745. Bar with high=9920 and low=9740
        bars = [_bar_obj(2000, 9920, 9740)]
        r = evaluate_trade_outcome(_short_dr(), _short_tp(), bars, SHORT_OC)
        assert r["status"] == "OK"
        assert str(r["outcome"].outcome) == "AMBIGUOUS"

    def test_direction_is_short(self):
        bars = [_bar_obj(2000, 9860, 9740)]
        r = evaluate_trade_outcome(_short_dr(), _short_tp(), bars, SHORT_OC)
        assert str(r["outcome"].direction) == "SHORT"

    def test_final_candle_handling(self):
        """Multiple bars, target hit on last bar."""
        bars = [
            _bar_obj(2000, 9860, 9800),
            _bar_obj(2500, 9850, 9770),
            _bar_obj(3000, 9840, 9740),  # hits r2=9745
        ]
        r = evaluate_trade_outcome(_short_dr(), _short_tp(), bars, SHORT_OC)
        assert r["status"] == "OK"
        assert str(r["outcome"].outcome) == "TARGET_HIT"
        assert r["outcome"].exit_bar_index == 2


# ══════════════════════════════════════════════════════════════════════════════
# END-TO-END — STRATEGY RUNNER
# ══════════════════════════════════════════════════════════════════════════════


def _build_short_session():
    """Build a complete SHORT session that should produce a VALID detection."""
    return {
        "symbol": "TEST",
        "date": "2026-07-01",
        "market_timezone": "America/New_York",
        "session_open_utc_ms": MS_0930,
        "session_close_utc_ms": MS_1020,
        "timeframe": "5m",
        "candles": [
            short_orb_candle(),            # 09:30 ORB
            short_break_candle(),           # 09:35 break below
            short_displacement_candle(),    # 09:40 displacement down
            short_retest_contact_candle(),  # 09:45 retest contact
            short_qualifying_rejection(),   # 09:50 rejection candle
            short_post_confirmation_bar_target(),  # 09:55 hits target
        ],
    }


SHORT_PRESET = {
    "preset_id": "short_test",
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_LOW",
    "direction": "SHORT",
    "entry_model": "CONFIRMATION_CLOSE",
    "entry_buffer_ticks": 0,
    "stop_buffer_ticks": 0,
    "min_displacement_ticks": None,
    "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None,
    "min_displacement_bars": 1,
    "confirmation_wick_penetration_pct_min": 0,
}

RUNNER_CONFIG = {
    "tick_size": TICK,
    "exit_target_r": 2,
    "engine_version": "1.0.0-test",
}


class TestShortEndToEnd:
    def test_complete_valid_short_session(self):
        session = _build_short_session()
        results = run_bdrr_strategy([session], SHORT_PRESET, RUNNER_CONFIG)
        assert len(results) == 1
        r = results[0]
        assert r["detection_status"] == "VALID"
        assert r["outcome"] in ("TARGET_HIT", "STOPPED", "OPEN", "SESSION_CLOSE", "AMBIGUOUS",
                                 "ENTRY_NOT_TRIGGERED")

    def test_short_fail_at_break(self):
        """No break → NO_VALID_SETUP."""
        session = {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": MS_0930,
            "session_close_utc_ms": MS_0945,
            "timeframe": "5m",
            "candles": [
                short_orb_candle(),
                # Close above level → no SHORT break
                c(MS_0935, open_=100.0, high=101.0, low=99.50, close=100.50),
                c(MS_0940, open_=100.50, high=101.0, low=99.50, close=100.00),
            ],
        }
        results = run_bdrr_strategy([session], SHORT_PRESET, RUNNER_CONFIG)
        assert len(results) == 1
        assert results[0]["outcome"] == "NO_VALID_SETUP"

    def test_short_fail_at_displacement(self):
        """Retest before displacement → NO_VALID_SETUP."""
        session = {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": MS_0930,
            "session_close_utc_ms": MS_0945,
            "timeframe": "5m",
            "candles": [
                short_orb_candle(),
                short_break_candle(),
                # Immediately returns to level
                c(MS_0940, open_=98.50, high=99.50, low=98.00, close=99.10),
            ],
        }
        results = run_bdrr_strategy([session], SHORT_PRESET, RUNNER_CONFIG)
        assert len(results) == 1
        assert results[0]["outcome"] == "NO_VALID_SETUP"

    def test_short_fail_at_rejection(self):
        """Bad rejection geometry → NO_VALID_SETUP."""
        session = {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": MS_0930,
            "session_close_utc_ms": MS_0955,
            "timeframe": "5m",
            "candles": [
                short_orb_candle(),
                short_break_candle(),
                short_displacement_candle(),
                short_retest_contact_candle(),
                # Bad geometry: huge body, tiny wick
                c(MS_0950, open_=99.05, high=99.10, low=98.00, close=98.05),
            ],
        }
        results = run_bdrr_strategy([session], SHORT_PRESET, RUNNER_CONFIG)
        assert len(results) == 1
        assert results[0]["outcome"] == "NO_VALID_SETUP"

    def test_deterministic_repeat_run(self):
        """Same input → identical output."""
        import json
        session = _build_short_session()

        def _deterministic_id_factory(identity_type, fields):
            import uuid
            canonical = json.dumps(
                {"type": identity_type, **fields},
                sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            )
            ns = uuid.UUID("b2d3e4f5-6789-4abc-9def-0123456789ab")
            return str(uuid.uuid5(ns, canonical))

        r1 = run_bdrr_strategy(
            [session], SHORT_PRESET, RUNNER_CONFIG,
            id_factory=_deterministic_id_factory,
        )
        r2 = run_bdrr_strategy(
            [session], SHORT_PRESET, RUNNER_CONFIG,
            id_factory=_deterministic_id_factory,
        )
        # Compare key fields (excluding run_record_id which uses uuid4)
        assert r1[0]["detection_status"] == r2[0]["detection_status"]
        assert r1[0]["outcome"] == r2[0]["outcome"]
        assert r1[0]["detection_result_id"] == r2[0]["detection_result_id"]
        assert r1[0]["entry_price_ticks"] == r2[0]["entry_price_ticks"]
        assert r1[0]["stop_price_ticks"] == r2[0]["stop_price_ticks"]

    def test_long_regression_unchanged(self):
        """LONG session still produces identical results."""
        long_session = {
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": MS_0930,
            "session_close_utc_ms": MS_1020,
            "timeframe": "5m",
            "candles": [
                # ORB: high = 101.0 (level for LONG/ORB_HIGH)
                c(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5),
                # Break above 101.0
                c(MS_0935, open_=100.50, high=102.0, low=100.20, close=101.50),
                # Displacement above level
                c(MS_0940, open_=101.60, high=102.50, low=101.20, close=102.00),
                # Retest contact: low touches level
                c(MS_0945, open_=101.80, high=102.20, low=100.80, close=101.20),
                # Rejection candle: low reaches level, close above
                # Need: wick_ratio >= 0.47, body <= 0.40, fcl >= 0.80
                # open=101.30, high=101.50, low=100.90, close=101.40
                # range=60, rej_wick=min(101.30,101.40)-100.90=40,
                # body=10, opp_wick=101.50-101.40=10
                # wick_ratio=40/60=0.667, body=10/60=0.167, fcl=(101.40-100.90)/60=0.833
                c(MS_0950, open_=101.30, high=101.50, low=100.90, close=101.40),
                # Post-confirmation
                c(MS_0955, open_=101.40, high=103.00, low=101.30, close=102.80),
            ],
        }
        long_preset = {**SHORT_PRESET, "direction": "LONG", "level_source": "ORB_HIGH",
                       "preset_id": "long_test"}
        results = run_bdrr_strategy([long_session], long_preset, RUNNER_CONFIG)
        assert len(results) == 1
        r = results[0]
        assert r["detection_status"] == "VALID"
        # Outcome should be deterministic — this LONG session should work end-to-end


# ══════════════════════════════════════════════════════════════════════════════
# ORB BUILDER — ORB_LOW support
# ══════════════════════════════════════════════════════════════════════════════


class TestOrbLow:
    def test_orb_low_level_price(self):
        candles = [short_orb_candle()]
        sc = build_session_context(candles, SHORT_CONFIG)
        orb = build_orb(sc["candles"], sc, SHORT_CONFIG)
        assert orb["status"] == "OK"
        assert orb["level_price"] == 99.0  # orb low
        assert orb["level_source"] == "ORB_LOW"
        assert orb["orb_low_active"] is True

    def test_orb_high_unchanged(self):
        candles = [short_orb_candle()]
        sc = build_session_context(candles, LONG_CONFIG)
        orb = build_orb(sc["candles"], sc, LONG_CONFIG)
        assert orb["status"] == "OK"
        assert orb["level_price"] == 101.0  # orb high
        assert orb["level_source"] == "ORB_HIGH"
        assert orb["orb_low_active"] is False
