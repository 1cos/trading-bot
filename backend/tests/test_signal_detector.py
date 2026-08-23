"""Tests for LiveSignalDetector — incremental live signal evaluation.

Verifies:
  1. Empty/insufficient session → NO_SETUP.
  2. Partial setup before entry candle → NO_SETUP.
  3. Valid setup at entry candle → SIGNAL.
  4. Signal exposes correct direction.
  5. Signal exposes correct entry/stop/target from existing trade plan builder.
  6. Repeated evaluation of identical session is deterministic.
  7. Adding future bars is not required to create the signal.
  8. Detector does not call/use simulated trade outcome.
"""

import pytest
from decimal import Decimal

from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)
from trading_lab.live.session_builder_live import LiveSessionBuilder


# ── Timestamp helpers ────────────────────────────────────────────────────────

# 2026-08-11 EDT (UTC-4): 09:30 ET = 13:30 UTC
MS_0930 = 1786455000000


def _ms(minute_offset: int) -> int:
    """Return epoch ms for 09:30 + minute_offset minutes."""
    return MS_0930 + minute_offset * 60_000


# ── Synthetic LONG setup ─────────────────────────────────────────────────────
#
# With timeframe_minutes=1 and orb_duration_minutes=5, the ORB uses
# bars 0–4 (09:30–09:34). The ORB high/low are the max high / min low
# of those 5 bars. orb_candle_index = 4. Scanning starts from index 5.
#
# ORB bars (09:30–09:34):
#   All within range 99.00–101.00.
#   ORB high = 101.00, ORB low = 99.00.
#   level_price = 101.00 (ORB_HIGH for LONG).
#
# Break candle (09:35, index 5): close=101.50 > 101.00 → confirmed break
#
# Displacement bars (09:36–09:38, indices 6–8): price stays above level
#   low > 101.00, never touching level. 3 bars of displacement.
#
# Rejection/entry candle (09:39, index 9):
#   open=101.10, high=101.30, low=100.80, close=101.20
#   range = 50 ticks
#   body = 10 ticks → body_ratio = 0.20 ✓ (<= 0.40)
#   rejection_wick = 30 ticks → wick_ratio = 0.60 ✓ (>= 0.47)
#   favorable_close_location = 0.80 ✓ (>= 0.25)
#   body outside ORB: open=101.10 >= 101.00 ✓, close=101.20 > 101.00 ✓
#   wick penetration: (101.00 - 100.80) / 30 = 0.667 ✓ (>= 0.20)


def _orb_bars():
    """5 ORB bars (09:30–09:34) defining ORB high=101.00, low=99.00."""
    return [
        {"time_ms": _ms(0), "open": 100.00, "high": 101.00, "low": 99.00,
         "close": 100.50, "volume": 1000},
        {"time_ms": _ms(1), "open": 100.50, "high": 100.80, "low": 100.00,
         "close": 100.30, "volume": 1000},
        {"time_ms": _ms(2), "open": 100.30, "high": 100.70, "low": 99.80,
         "close": 100.40, "volume": 1000},
        {"time_ms": _ms(3), "open": 100.40, "high": 100.90, "low": 100.10,
         "close": 100.60, "volume": 1000},
        {"time_ms": _ms(4), "open": 100.60, "high": 100.95, "low": 100.20,
         "close": 100.70, "volume": 1000},
    ]


def _break_bar():
    """Break candle at 09:35 (index 5): close > ORB high."""
    return {
        "time_ms": _ms(5),
        "open": 100.80,
        "high": 101.60,
        "low": 100.70,
        "close": 101.50,
        "volume": 1000,
    }


def _displacement_bars():
    """3 displacement bars (09:36–09:38) staying above level (low > 101.00)."""
    return [
        {"time_ms": _ms(6), "open": 101.55, "high": 101.80, "low": 101.20,
         "close": 101.60, "volume": 1000},
        {"time_ms": _ms(7), "open": 101.60, "high": 101.90, "low": 101.30,
         "close": 101.70, "volume": 1000},
        {"time_ms": _ms(8), "open": 101.70, "high": 101.85, "low": 101.10,
         "close": 101.40, "volume": 1000},
    ]


def _rejection_bar():
    """Rejection/entry candle at 09:39 (index 9): touches level with proper geometry."""
    return {
        "time_ms": _ms(9),
        "open": 101.10,
        "high": 101.30,
        "low": 100.80,
        "close": 101.20,
        "volume": 1000,
    }


def _build_session_up_to(bars: list[dict]) -> dict:
    """Build a session dict from a list of bars via LiveSessionBuilder."""
    builder = LiveSessionBuilder("SPY")
    for bar in bars:
        builder.add_bar(bar)
    return builder.current_session()


def _all_bars_through_rejection():
    """All bars needed for a complete LONG setup."""
    return _orb_bars() + [_break_bar()] + _displacement_bars() + [_rejection_bar()]


def _make_detector(**kwargs):
    """Create a LONG detector with SPY defaults."""
    defaults = {
        "symbol": "SPY",
        "direction": "LONG",
        "tick_size": 0.01,
        "market_timezone": "America/New_York",
        "session_open": "09:30",
        "entry_model": "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0,
        "exit_target_r": 2,
    }
    defaults.update(kwargs)
    return LiveSignalDetector(**defaults)


# ── Test 1: Empty/insufficient session → NO_SETUP ───────────────────────────

class TestNoSetup:
    def test_none_session(self):
        d = _make_detector()
        result = d.evaluate(None)
        assert result.status == SignalStatus.NO_SETUP

    def test_empty_candles(self):
        d = _make_detector()
        result = d.evaluate({"candles": [], "symbol": "SPY", "date": "2026-08-11",
                             "market_timezone": "America/New_York",
                             "session_open_utc_ms": MS_0930,
                             "session_close_utc_ms": MS_0930,
                             "timeframe": "1m"})
        assert result.status == SignalStatus.NO_SETUP

    def test_only_orb_bars(self):
        d = _make_detector()
        sess = _build_session_up_to(_orb_bars())
        result = d.evaluate(sess)
        assert result.status == SignalStatus.NO_SETUP


# ── Test 2: Partial setup before entry candle → NO_SETUP ────────────────────

class TestPartialSetup:
    def test_break_only(self):
        d = _make_detector()
        sess = _build_session_up_to(_orb_bars() + [_break_bar()])
        result = d.evaluate(sess)
        assert result.status == SignalStatus.NO_SETUP

    def test_break_plus_displacement_no_retest(self):
        d = _make_detector()
        bars = _orb_bars() + [_break_bar()] + _displacement_bars()
        sess = _build_session_up_to(bars)
        result = d.evaluate(sess)
        assert result.status == SignalStatus.NO_SETUP


# ── Test 3: Valid setup at entry candle → SIGNAL ─────────────────────────────

class TestSignal:
    def test_signal_at_rejection(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert result.status == SignalStatus.SIGNAL

    def test_signal_has_trade_plan(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert result.trade_plan is not None
        assert result.detection_result is not None


# ── Test 4: Signal exposes correct direction ─────────────────────────────────

class TestDirection:
    def test_long_direction(self):
        d = _make_detector(direction="LONG")
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert result.direction == "LONG"


# ── Test 5: Signal exposes correct entry/stop/target ─────────────────────────

class TestPrices:
    def test_entry_price(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert result.status == SignalStatus.SIGNAL
        # CONFIRMATION_CLOSE, LONG, buffer=0: entry = close of rejection bar
        assert result.entry_price == Decimal("101.20")

    def test_stop_price(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        # LONG, buffer=0: stop = low of rejection bar = 100.80
        assert result.stop_price == Decimal("100.80")

    def test_target_2r(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        # risk = entry - stop = 101.20 - 100.80 = 0.40
        # target = entry + 2 * risk = 101.20 + 0.80 = 102.00
        assert result.target_price == Decimal("102.00")

    def test_entry_timestamp(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert result.entry_timestamp_ms == _ms(9)


# ── Test 6: Repeated evaluation is deterministic ────────────────────────────

class TestDeterminism:
    def test_same_session_same_result(self):
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        r1 = d.evaluate(sess)
        r2 = d.evaluate(sess)
        assert r1.status == r2.status
        assert r1.entry_price == r2.entry_price
        assert r1.stop_price == r2.stop_price
        assert r1.target_price == r2.target_price
        assert r1.direction == r2.direction

    def test_no_setup_is_deterministic(self):
        d = _make_detector()
        sess = _build_session_up_to(_orb_bars() + [_break_bar()])
        r1 = d.evaluate(sess)
        r2 = d.evaluate(sess)
        assert r1.status == r2.status == SignalStatus.NO_SETUP


# ── Test 7: No future bars required ─────────────────────────────────────────

class TestNoLookahead:
    def test_signal_without_future_bars(self):
        """SIGNAL appears with exactly the bars through the entry candle.
        No post-entry bars are needed."""
        d = _make_detector()
        bars = _all_bars_through_rejection()
        assert len(bars) == 10  # 5 ORB + break + 3 disp + rejection

        sess = _build_session_up_to(bars)
        result = d.evaluate(sess)
        assert result.status == SignalStatus.SIGNAL

    def test_signal_appears_only_when_entry_candle_added(self):
        """Before the entry candle → NO_SETUP.
        After adding it → SIGNAL.
        Proves no future data leaks."""
        d = _make_detector()
        bars_before = _orb_bars() + [_break_bar()] + _displacement_bars()
        sess_before = _build_session_up_to(bars_before)
        assert d.evaluate(sess_before).status == SignalStatus.NO_SETUP

        bars_after = bars_before + [_rejection_bar()]
        sess_after = _build_session_up_to(bars_after)
        assert d.evaluate(sess_after).status == SignalStatus.SIGNAL

    def test_adding_more_bars_preserves_signal(self):
        """Adding post-entry bars doesn't break the signal."""
        d = _make_detector()
        bars = _all_bars_through_rejection() + [{
            "time_ms": _ms(10),
            "open": 101.25,
            "high": 101.50,
            "low": 101.15,
            "close": 101.35,
            "volume": 1000,
        }]
        sess = _build_session_up_to(bars)
        result = d.evaluate(sess)
        assert result.status == SignalStatus.SIGNAL


# ── Test 8: No trade outcome evaluation ──────────────────────────────────────

class TestNoOutcome:
    def test_result_has_no_outcome_fields(self):
        """SignalResult does not contain trade_outcome or realized_r."""
        d = _make_detector()
        sess = _build_session_up_to(_all_bars_through_rejection())
        result = d.evaluate(sess)
        assert not hasattr(result, "trade_outcome")
        assert not hasattr(result, "realized_r")

    def test_signal_detector_does_not_import_outcome_evaluator(self):
        """Verify that signal_detector.py does not import
        evaluate_trade_outcome."""
        import trading_lab.live.signal_detector as mod
        # Check no import of the outcome evaluator module
        assert not hasattr(mod, "evaluate_trade_outcome")
        # Check module's direct imports don't include it
        import sys
        outcome_mod = "trading_lab.trade_outcome_evaluator"
        # The module should not have caused trade_outcome_evaluator to load
        # (it may already be loaded from other tests, so check the import lines)
        import inspect
        source = inspect.getsource(mod)
        assert "from trading_lab.trade_outcome_evaluator" not in source
        assert "import trading_lab.trade_outcome_evaluator" not in source


# ── Test: Failed stage reported on NO_SETUP ──────────────────────────────────

class TestFailedStage:
    def test_no_session_reports_stage(self):
        d = _make_detector()
        result = d.evaluate(None)
        assert result.failed_stage == "NO_SESSION"

    def test_no_break_reports_stage(self):
        d = _make_detector()
        sess = _build_session_up_to(_orb_bars())
        result = d.evaluate(sess)
        assert result.failed_stage is not None


# ── Test: previous_sessions wiring (PDH/PDL micro-task 4) ────────────────────
#
# level_source stays ORB — these tests verify only that the data is
# reachable on the detector and that passing it through to
# build_level(all_sessions=...) does not change ORB behavior.

_FAKE_PREVIOUS_SESSIONS = [
    {"date": "2026-08-10", "candles": [
        {"time_ms": 1, "open": 100.0, "high": 105.0, "low": 95.0,
         "close": 101.0, "volume": 500},
    ]},
]


class TestPreviousSessionsWiring:
    def test_default_none(self):
        """Before set_previous_sessions() is called, the detector has none."""
        d = _make_detector()
        assert d._previous_sessions is None

    def test_set_previous_sessions_stores_data(self):
        """previous_sessions reaches the detector correctly for its symbol."""
        d = _make_detector()
        d.set_previous_sessions(_FAKE_PREVIOUS_SESSIONS)
        assert d._previous_sessions == _FAKE_PREVIOUS_SESSIONS

    def test_orb_signal_unchanged_with_previous_sessions(self):
        """Passing previous_sessions through must not alter the ORB
        signal outcome — level_source stays ORB_HIGH, which ignores
        all_sessions entirely (level_provider._build_orb_level does
        not take that parameter)."""
        bars = _all_bars_through_rejection()

        d_without = _make_detector()
        sess_without = _build_session_up_to(bars)
        result_without = d_without.evaluate(sess_without)

        d_with = _make_detector()
        d_with.set_previous_sessions(_FAKE_PREVIOUS_SESSIONS)
        sess_with = _build_session_up_to(bars)
        result_with = d_with.evaluate(sess_with)

        assert result_without.status == SignalStatus.SIGNAL
        assert result_with.status == SignalStatus.SIGNAL
        assert result_with.direction == result_without.direction
        assert result_with.entry_price == result_without.entry_price
        assert result_with.stop_price == result_without.stop_price
        assert result_with.target_price == result_without.target_price
        assert result_with.entry_timestamp_ms == result_without.entry_timestamp_ms

    def test_orb_no_setup_unchanged_with_previous_sessions(self):
        """Same NO_SETUP outcome with or without previous_sessions set,
        for a partial (non-signal) session."""
        bars = _orb_bars() + [_break_bar()]

        d_without = _make_detector()
        result_without = d_without.evaluate(_build_session_up_to(bars))

        d_with = _make_detector()
        d_with.set_previous_sessions(_FAKE_PREVIOUS_SESSIONS)
        result_with = d_with.evaluate(_build_session_up_to(bars))

        assert result_without.status == result_with.status == SignalStatus.NO_SETUP
        assert result_without.failed_stage == result_with.failed_stage

    def test_engine_config_level_source_still_orb(self):
        """level_source is untouched by this wiring — still ORB."""
        d = _make_detector()
        d.set_previous_sessions(_FAKE_PREVIOUS_SESSIONS)
        assert d._engine_config["level_source"] == "ORB_HIGH"


# ── Test: explicit level_source constructor param (PDH/PDL micro-task 5) ─────
#
# level_source is pure configurability here — no dynamic selection logic,
# no operational PDH/PDL signal generation, no engulfing/ORB-superato
# decisions. Default (None) must reproduce the exact prior behavior.

class TestDefaultLevelSourceUnchanged:
    def test_long_default_still_orb_high(self):
        d = _make_detector(direction="LONG")
        assert d._engine_config["level_source"] == "ORB_HIGH"

    def test_short_default_still_orb_low(self):
        d = _make_detector(direction="SHORT")
        assert d._engine_config["level_source"] == "ORB_LOW"

    def test_explicit_none_same_as_omitted(self):
        d_omitted = _make_detector(direction="LONG")
        d_none = _make_detector(direction="LONG", level_source=None)
        assert (d_omitted._engine_config["level_source"]
                == d_none._engine_config["level_source"] == "ORB_HIGH")


class TestExplicitLevelSourceConstruction:
    def test_long_can_be_constructed_with_previous_day_high(self):
        d = _make_detector(direction="LONG", level_source="PREVIOUS_DAY_HIGH")
        assert d._engine_config["level_source"] == "PREVIOUS_DAY_HIGH"
        # direction (BDRR sign logic) is untouched by level_source choice.
        assert d._direction == "LONG"
        assert d._engine_config["direction"] == "LONG"

    def test_short_can_be_constructed_with_previous_day_low(self):
        d = _make_detector(direction="SHORT", level_source="PREVIOUS_DAY_LOW")
        assert d._engine_config["level_source"] == "PREVIOUS_DAY_LOW"
        assert d._direction == "SHORT"
        assert d._engine_config["direction"] == "SHORT"

    def test_invalid_direction_still_rejected_regardless_of_level_source(self):
        with pytest.raises(ValueError):
            _make_detector(direction="SIDEWAYS", level_source="PREVIOUS_DAY_HIGH")


class TestLevelSourceReachesBuildLevel:
    """Verify the explicit level_source is not just stored, but actually
    used by build_level() — and that a PDH level can be constructed from
    previous_sessions already propagated (micro-task 4). No SIGNAL is
    required here — only that level construction succeeds."""

    def test_previous_day_high_level_built_from_propagated_sessions(self):
        from trading_lab.level_provider import build_level
        from trading_lab.session_context import build_session_context

        d = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
            level_source="PREVIOUS_DAY_HIGH",
        )
        previous_sessions = [{
            "date": "2026-08-10",
            "candles": [
                {"time_ms": 1, "open": 100.0, "high": 105.0, "low": 95.0,
                 "close": 101.0, "volume": 500},
            ],
        }]
        d.set_previous_sessions(previous_sessions)

        # Today's ORB-window bars (2026-08-11 09:30-09:34) — needed by
        # _build_pdh_pdl_level for scan_from_index geometry.
        today_bars = _orb_bars()
        sc = build_session_context(today_bars, d._engine_config)
        assert sc["status"] == "OK"

        result = build_level(
            sc["candles"], sc, d._engine_config,
            all_sessions=d._previous_sessions,
        )
        assert result["status"] == "OK"
        assert result["level_source"] == "PREVIOUS_DAY_HIGH"
        assert result["level_price"] == 105.0

    def test_previous_day_low_level_built_from_propagated_sessions(self):
        from trading_lab.level_provider import build_level
        from trading_lab.session_context import build_session_context

        d = LiveSignalDetector(
            symbol="QQQ", direction="SHORT", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
            level_source="PREVIOUS_DAY_LOW",
        )
        previous_sessions = [{
            "date": "2026-08-10",
            "candles": [
                {"time_ms": 1, "open": 100.0, "high": 105.0, "low": 95.0,
                 "close": 101.0, "volume": 500},
            ],
        }]
        d.set_previous_sessions(previous_sessions)

        today_bars = _orb_bars()
        sc = build_session_context(today_bars, d._engine_config)
        assert sc["status"] == "OK"

        result = build_level(
            sc["candles"], sc, d._engine_config,
            all_sessions=d._previous_sessions,
        )
        assert result["status"] == "OK"
        assert result["level_source"] == "PREVIOUS_DAY_LOW"
        assert result["level_price"] == 95.0

    def test_without_previous_sessions_pdh_fails_gracefully(self):
        """Explicit PREVIOUS_DAY_HIGH with no previous_sessions propagated
        yet must fail cleanly (MISSING_SESSIONS_DATA), not crash."""
        from trading_lab.level_provider import build_level
        from trading_lab.session_context import build_session_context

        d = _make_detector(direction="LONG", level_source="PREVIOUS_DAY_HIGH")
        assert d._previous_sessions is None

        today_bars = _orb_bars()
        sc = build_session_context(today_bars, d._engine_config)
        result = build_level(
            sc["candles"], sc, d._engine_config,
            all_sessions=d._previous_sessions,
        )
        assert result["status"] == "FAILED"
        assert result["failed_stage"] == "MISSING_SESSIONS_DATA"


# ── Test: setup_key is level-source-aware (PDH/PDL micro-task 10) ────────────
#
# Direct test of the new identity property: same direction, same break
# timestamp, different level_source -> different setup_key. This is the
# collision this task exists to prevent (ORB vs PDH sharing a break
# candle must never be treated as the same structural setup by
# _consumed_setups/_consumed_signals or stale/restart handling).

class TestSetupKeyLevelSourceCollisionPrevention:
    def test_orb_and_pdh_same_break_produce_different_setup_keys(self):
        bars = _all_bars_through_rejection()

        # ORB detector: default level_source (ORB_HIGH), level_price=101.00
        # (see _orb_bars() docstring at top of file).
        d_orb = _make_detector(direction="LONG")
        result_orb = d_orb.evaluate(_build_session_up_to(bars))
        assert result_orb.status == SignalStatus.SIGNAL

        # PDH detector: explicit PREVIOUS_DAY_HIGH, with a previous
        # session whose high equals the exact same numeric level_price
        # (101.00) as ORB_High. Break/displacement/retest/rejection
        # geometry is entirely level_price-driven (not level_source-
        # driven — see prior micro-task audits), so this makes the SAME
        # candle (index 5) qualify as the break for both providers,
        # producing an identical break timestamp on both sides.
        d_pdh = _make_detector(direction="LONG", level_source="PREVIOUS_DAY_HIGH")
        d_pdh.set_previous_sessions([{
            "date": "2026-08-10",
            "candles": [{"time_ms": 1, "open": 100.0, "high": 101.00,
                         "low": 95.0, "close": 100.5, "volume": 500}],
        }])
        result_pdh = d_pdh.evaluate(_build_session_up_to(bars))
        assert result_pdh.status == SignalStatus.SIGNAL

        orb_break_ts = (result_orb.stage_context or {}).get("break_time_ms")
        pdh_break_ts = (result_pdh.stage_context or {}).get("break_time_ms")
        assert orb_break_ts is not None
        assert orb_break_ts == pdh_break_ts, (
            "test setup invalid: ORB and PDH must share the same break "
            "candle for this to be a meaningful collision test"
        )
        assert result_orb.direction == result_pdh.direction == "LONG"

        # The decisive assertion: setup_key/signal_key must differ,
        # purely because of level_source, even though direction and
        # break timestamp are identical.
        assert result_orb.setup_key != result_pdh.setup_key
        assert result_orb.signal_key != result_pdh.signal_key
        assert result_orb.setup_key == f"LONG:ORB_HIGH:{orb_break_ts}"
        assert result_pdh.setup_key == f"LONG:PREVIOUS_DAY_HIGH:{pdh_break_ts}"

        # And therefore they can never collide in a shared consumed-set.
        consumed = {result_orb.setup_key}
        assert result_pdh.setup_key not in consumed


