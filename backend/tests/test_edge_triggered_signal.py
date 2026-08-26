"""Tests for edge-triggered signal execution (current-candle gate).

Max's rule: a trade may fire ONLY when the CURRENT just-completed bar
IS itself the valid entry/rejection candle (break -> displacement ->
retest -> rejection, closing outside the ORB, on THIS bar). A rejection
candle from earlier in the session is valid historical context but must
never trigger an order on a later bar.

Root cause (pre-fix): ``find_rejection`` scans the retest window
chronologically and returns the FIRST qualifying candle. Nothing
compared that candle's timestamp to "now" — so re-evaluating a growing
session on a later bar could re-surface an old rejection candle as a
fresh SIGNAL, with ``entry_timestamp_ms`` pointing into the past while
the orchestrator executes as if it happened right now.

Fix: ``MaxBotTradeOrchestrator._check_for_signal`` (and the parallel
``ObserveOrchestrator._check_for_signal``) now gate on
``result.entry_timestamp_ms == current_bar["time_ms"]`` before
enqueuing execution, logging ``SIGNAL_NOT_CURRENT`` and returning
otherwise. The `0134c9a` setup/signal consumption gates are untouched
and still apply beforehand.

Covers the 10 required cases from the T20 spec:
  1. Historical valid rejection candle + later ordinary bar -> NO exec.
  2. Historical valid rejection candle + later bar inside ORB -> NO exec.
  3. Current bar is valid SHORT rejection -> SIGNAL allowed.
  4. Current bar is valid LONG rejection -> SIGNAL allowed.
  5. Current SHORT candle touches ORB Low but closes inside -> NO SIGNAL.
  6. Current LONG candle touches ORB High but closes inside -> NO SIGNAL.
  7. Same historical entry candle cannot execute again on later bars.
  8. live_boundary_ms behavior remains intact.
  9. signal_key / setup_key protections remain intact.
  10. Existing relevant regression suite passes (see other test files;
      exercised together via `pytest backend/tests/`).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.trade_orchestrator import (
    MaxBotTradeOrchestrator,
    LifecycleState,
)
from trading_lab.live.observe_orchestrator import ObserveOrchestrator


# ═════════════════════════════════════════════════════════════════════════
# Synthetic BDRR fixtures — real pipeline, no detector mocking.
#
# LONG fixture is the canonical setup also used in test_signal_detector.py
# (ORB high=101.00). SHORT fixture is its exact mirror about price 200.00
# (ORB low=99.00), verified independently to produce a real SIGNAL with
# entry_timestamp_ms == the rejection bar's time_ms.
# ═════════════════════════════════════════════════════════════════════════

MS_0930 = 1786455000000


def _ms(minute_offset: int) -> int:
    return MS_0930 + minute_offset * 60_000


def _orb_bars_long():
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


def _break_bar_long():
    return {"time_ms": _ms(5), "open": 100.80, "high": 101.60, "low": 100.70,
            "close": 101.50, "volume": 1000}


def _disp_bars_long():
    return [
        {"time_ms": _ms(6), "open": 101.55, "high": 101.80, "low": 101.20,
         "close": 101.60, "volume": 1000},
        {"time_ms": _ms(7), "open": 101.60, "high": 101.90, "low": 101.30,
         "close": 101.70, "volume": 1000},
        {"time_ms": _ms(8), "open": 101.70, "high": 101.85, "low": 101.10,
         "close": 101.40, "volume": 1000},
    ]


def _rejection_bar_long():
    """Valid LONG entry candle: wick re-enters ORB, closes above it."""
    return {"time_ms": _ms(9), "open": 101.10, "high": 101.30, "low": 100.80,
            "close": 101.20, "volume": 1000}


def _inside_orb_bar_long():
    """Touches ORB high with wick but CLOSES INSIDE the ORB — invalid entry."""
    return {"time_ms": _ms(9), "open": 100.90, "high": 101.10, "low": 100.80,
            "close": 100.95, "volume": 1000}


def _ordinary_later_bar_long(offset=10):
    """Unremarkable bar after the entry candle — no retest/rejection of its own."""
    return {"time_ms": _ms(offset), "open": 101.25, "high": 101.45,
            "low": 101.15, "close": 101.35, "volume": 1000}


def _inside_orb_later_bar_long(offset=10):
    """A later bar that drifts back inside the ORB (legitimate price action,
    but must NOT be mistaken for a fresh entry on the old break)."""
    return {"time_ms": _ms(offset), "open": 101.20, "high": 101.25,
            "low": 100.60, "close": 100.85, "volume": 1000}


def _mirror(bar: dict, pivot: float = 200.0) -> dict:
    """Mirror a candle about `pivot`, swapping high/low roles — turns the
    canonical LONG/ORB_HIGH fixture into an equivalent SHORT/ORB_LOW one."""
    return {
        "time_ms": bar["time_ms"],
        "open": round(pivot - bar["open"], 2),
        "high": round(pivot - bar["low"], 2),
        "low": round(pivot - bar["high"], 2),
        "close": round(pivot - bar["close"], 2),
        "volume": bar["volume"],
    }


def _orb_bars_short():
    return [_mirror(b) for b in _orb_bars_long()]


def _break_bar_short():
    return _mirror(_break_bar_long())


def _disp_bars_short():
    return [_mirror(b) for b in _disp_bars_long()]


def _rejection_bar_short():
    return _mirror(_rejection_bar_long())


def _inside_orb_bar_short():
    return _mirror(_inside_orb_bar_long())


def _bars_through_rejection(direction: str) -> list[dict]:
    if direction == "LONG":
        return (_orb_bars_long() + [_break_bar_long()] + _disp_bars_long()
                + [_rejection_bar_long()])
    return (_orb_bars_short() + [_break_bar_short()] + _disp_bars_short()
            + [_rejection_bar_short()])


def _bars_through_inside_orb(direction: str) -> list[dict]:
    if direction == "LONG":
        return (_orb_bars_long() + [_break_bar_long()] + _disp_bars_long()
                + [_inside_orb_bar_long()])
    return (_orb_bars_short() + [_break_bar_short()] + _disp_bars_short()
            + [_inside_orb_bar_short()])


# ═════════════════════════════════════════════════════════════════════════
# Real orchestrator wiring (real LiveSignalDetector + LiveSessionBuilder;
# only the IBKR-facing collaborators are mocked).
# ═════════════════════════════════════════════════════════════════════════


def _make_real_orchestrator(direction: str, symbol: str = "QQQ"):
    sb = LiveSessionBuilder(symbol)
    detector = LiveSignalDetector(
        symbol=symbol, direction=direction, tick_size=0.01,
        market_timezone="America/New_York", session_open="09:30",
    )
    tm = MagicMock()
    tm.can_trade = True

    orch = MaxBotTradeOrchestrator(
        underlying_symbol=symbol, direction=direction,
        tick_size=0.01, session_builder=sb,
        signal_detector=detector, trade_manager=tm,
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(),
    )
    return orch


# ═════════════════════════════════════════════════════════════════════════
# 1 & 7. Historical valid rejection candle + later ordinary bar -> NO exec,
#        and the same historical candle stays blocked on further bars.
# ═════════════════════════════════════════════════════════════════════════


class TestHistoricalRejectionNotReplayed:
    """A rejection candle from earlier in the session must never trigger
    execution once a later, unrelated bar closes."""

    def test_ordinary_later_bar_does_not_execute_long(self):
        orch = _make_real_orchestrator("LONG")
        bars = _bars_through_rejection("LONG")

        # Feed everything up to (but not including) the rejection bar.
        for bar in bars[:-1]:
            orch.on_bar(bar)
        assert not orch.has_pending_signal

        # Simulate the bot MISSING the entry bar's own close (e.g. a
        # restart, a processing gap) — jump straight to a later ordinary
        # bar without ever calling on_bar() for the rejection candle itself.
        rej_bar = bars[-1]
        orch._session_builder.add_bar(rej_bar)  # data arrives, but not "processed"

        later = _ordinary_later_bar_long(10)
        orch.on_bar(later)

        assert not orch.has_pending_signal
        # Crucially: the rejected replay must not cost us a LATER,
        # genuinely different setup. That is the property this test
        # guards. It used to be checked via the proxy
        # `not orch._consumed_setups`; that proxy became wrong when the
        # non-current branch started archiving its own setup_key for
        # scanning purposes (2026-08-25 audit — without it the detector
        # re-derives this same historical setup on every later bar and
        # never advances). The property itself is asserted directly
        # below, and end-to-end in test_detector_scan_cursor.py::
        # TestT2SignalNotCurrentDoesNotFreeze.
        replayed_key = f"LONG:ORB_HIGH:{bars[5]['time_ms']}"
        assert orch._consumed_setups <= {replayed_key}, (
            "only the replayed setup itself may be archived — never a "
            "setup belonging to a different break"
        )

    def test_replayed_signal_still_blocked_on_further_bars(self):
        """Requirement 7: the SAME historical entry candle cannot execute
        again no matter how many more ordinary bars follow."""
        orch = _make_real_orchestrator("LONG")
        bars = _bars_through_rejection("LONG")
        for bar in bars[:-1]:
            orch.on_bar(bar)

        orch._session_builder.add_bar(bars[-1])  # rejection bar lands unprocessed

        for i, offset in enumerate((10, 11, 12), start=1):
            orch.on_bar(_ordinary_later_bar_long(offset))
            assert not orch.has_pending_signal, (
                f"bar #{i} after the historical rejection candle must "
                "not trigger execution"
            )


# ═════════════════════════════════════════════════════════════════════════
# 2. Historical valid rejection candle + later bar inside ORB -> NO exec.
# ═════════════════════════════════════════════════════════════════════════


class TestHistoricalRejectionThenInsideOrbBar:
    def test_inside_orb_later_bar_does_not_execute(self):
        orch = _make_real_orchestrator("LONG")
        bars = _bars_through_rejection("LONG")
        for bar in bars[:-1]:
            orch.on_bar(bar)
        orch._session_builder.add_bar(bars[-1])  # rejection bar unprocessed

        inside_bar = _inside_orb_later_bar_long(10)
        orch.on_bar(inside_bar)

        assert not orch.has_pending_signal
        # See test_ordinary_later_bar_does_not_execute_long for why the
        # old `not orch._consumed_setups` proxy was replaced.
        assert orch._consumed_setups <= {f"LONG:ORB_HIGH:{bars[5]['time_ms']}"}


# ═════════════════════════════════════════════════════════════════════════
# 3 & 4. Current completed bar IS the valid rejection candle -> SIGNAL.
# ═════════════════════════════════════════════════════════════════════════


class TestCurrentBarSignalAllowed:
    def test_short_rejection_on_current_bar_allowed(self):
        orch = _make_real_orchestrator("SHORT")
        bars = _bars_through_rejection("SHORT")
        for bar in bars[:-1]:
            orch.on_bar(bar)
        assert not orch.has_pending_signal

        # The CURRENT bar (just added via on_bar) IS the entry candle.
        orch.on_bar(bars[-1])
        assert orch.has_pending_signal
        assert orch._pending_signal.entry_timestamp_ms == bars[-1]["time_ms"]

    def test_long_rejection_on_current_bar_allowed(self):
        orch = _make_real_orchestrator("LONG")
        bars = _bars_through_rejection("LONG")
        for bar in bars[:-1]:
            orch.on_bar(bar)
        assert not orch.has_pending_signal

        orch.on_bar(bars[-1])
        assert orch.has_pending_signal
        assert orch._pending_signal.entry_timestamp_ms == bars[-1]["time_ms"]


# ═════════════════════════════════════════════════════════════════════════
# 5 & 6. Current candle touches the level but closes INSIDE the ORB
#        -> the rejection geometry itself must reject it (no signal at all,
#        regardless of the new current-candle gate).
# ═════════════════════════════════════════════════════════════════════════


class TestCurrentBarInsideOrbNoSignal:
    def test_short_touch_close_inside_orb_no_signal(self):
        orch = _make_real_orchestrator("SHORT")
        bars = _bars_through_inside_orb("SHORT")
        for bar in bars[:-1]:
            orch.on_bar(bar)

        orch.on_bar(bars[-1])
        assert not orch.has_pending_signal

    def test_long_touch_close_inside_orb_no_signal(self):
        orch = _make_real_orchestrator("LONG")
        bars = _bars_through_inside_orb("LONG")
        for bar in bars[:-1]:
            orch.on_bar(bar)

        orch.on_bar(bars[-1])
        assert not orch.has_pending_signal


# ═════════════════════════════════════════════════════════════════════════
# 8. live_boundary_ms behavior remains intact (composes with the new gate).
# ═════════════════════════════════════════════════════════════════════════


def _mock_orchestrator(signal_results):
    sb = MagicMock()
    sb.current_session.return_value = {
        "date": "2026-01-15",
        "candles": [{"time_ms": 5000, "open": 100, "high": 101,
                     "low": 99, "close": 100.5, "volume": 1000}],
    }
    sd = MagicMock()
    sd.evaluate = MagicMock(side_effect=signal_results)
    sd.last_result = None
    tm = MagicMock()
    tm.can_trade = True

    orch = MaxBotTradeOrchestrator(
        underlying_symbol="NVDA", direction="SHORT",
        tick_size=0.01, session_builder=sb,
        signal_detector=sd, trade_manager=tm,
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(),
    )
    return orch


class TestLiveBoundaryStillEnforced:
    def test_stale_entry_before_boundary_still_blocked(self):
        """entry_timestamp_ms < live_boundary_ms must still be rejected,
        even though it matches the current bar's own time_ms (i.e. the
        block must come from the live-boundary check, not the new gate)."""
        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", signal_key="SHORT:1000:5000",
            entry_timestamp_ms=5000,
            pipeline_stage="SIGNAL", trade_plan=MagicMock(),
            detection_result=MagicMock(),
        )
        orch = _mock_orchestrator([sig])
        orch._live_boundary_ms = 6000  # bot started after this candle

        bar = {"time_ms": 5000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)

        assert not orch.has_pending_signal

    def test_current_entry_at_or_after_boundary_allowed(self):
        """A genuinely current signal at/after the live boundary still
        passes both checks."""
        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", signal_key="SHORT:1000:5000",
            entry_timestamp_ms=5000,
            pipeline_stage="SIGNAL", trade_plan=MagicMock(),
            detection_result=MagicMock(),
        )
        orch = _mock_orchestrator([sig])
        orch._live_boundary_ms = 4000  # bot started before this candle

        bar = {"time_ms": 5000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)

        assert orch.has_pending_signal


# ═════════════════════════════════════════════════════════════════════════
# 9. signal_key / setup_key protections remain intact alongside the gate.
# ═════════════════════════════════════════════════════════════════════════


class TestSetupAndSignalKeyProtectionsIntact:
    def test_accepted_current_signal_still_consumes_keys(self):
        """A genuinely current SIGNAL is still consumed at acceptance,
        exactly as before T20 — the new gate only affects non-current
        signals, never the bookkeeping for real ones."""
        orch = _make_real_orchestrator("LONG")
        bars = _bars_through_rejection("LONG")
        for bar in bars[:-1]:
            orch.on_bar(bar)
        orch.on_bar(bars[-1])

        assert orch.has_pending_signal
        pending = orch._pending_signal
        assert pending.setup_key in orch._consumed_setups
        assert pending.signal_key in orch._consumed_signals

    def test_non_current_replay_only_burns_its_own_key(self):
        """A REJECTED (non-current) replay archives its OWN setup_key —
        and nothing else.

        The original concern behind this test was that burning a key on
        a rejected replay could starve "a legitimate future entry on a
        genuinely new break". That cannot happen: setup_key is
        ``direction:level_source:break_time_ms``, so a genuinely new
        break carries a different key by construction. The only thing
        archiving forfeits is a later entry on THIS same break — and
        that is provably nothing, because a break's entry candle is
        immutable once found (asserted in test_detector_scan_cursor.py::
        TestT2SignalNotCurrentDoesNotFreeze::
        test_a_breaks_entry_candle_never_changes).

        Archiving is required: without it the detector re-derives this
        identical historical setup on every subsequent bar, its scan
        cursor never advances past this break, and any genuinely new
        setup formed later is masked (observed live on AAPL SHORT,
        2026-08-25).
        """
        orch = _make_real_orchestrator("LONG")
        bars = _bars_through_rejection("LONG")
        for bar in bars[:-1]:
            orch.on_bar(bar)
        orch._session_builder.add_bar(bars[-1])  # rejection bar unprocessed

        orch.on_bar(_ordinary_later_bar_long(10))

        replayed_setup = f"LONG:ORB_HIGH:{bars[5]['time_ms']}"
        replayed_signal = f"{replayed_setup}:{bars[-1]['time_ms']}"

        assert not orch.has_pending_signal
        assert orch._consumed_setups == {replayed_setup}
        assert orch._consumed_signals == {replayed_signal}

    def test_same_setup_still_blocked_via_mock(self):
        """Existing T19C protection composes correctly: once a setup_key
        IS consumed (via a real accepted trade), the same key stays
        blocked regardless of the new gate."""
        sig1 = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", signal_key="SHORT:1000:2000",
            entry_timestamp_ms=2000,
            pipeline_stage="SIGNAL", trade_plan=MagicMock(),
            detection_result=MagicMock(),
        )
        sig2 = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", signal_key="SHORT:1000:3000",
            entry_timestamp_ms=3000,
            pipeline_stage="SIGNAL", trade_plan=MagicMock(),
            detection_result=MagicMock(),
        )

        sb = MagicMock()

        def _session():
            return {"date": "2026-01-15",
                    "candles": [{"time_ms": sb._t, "open": 100, "high": 101,
                                 "low": 99, "close": 100.5, "volume": 1000}]}
        sb.current_session.side_effect = _session

        sd = MagicMock()
        sd.evaluate = MagicMock(side_effect=[sig1, sig2])
        sd.last_result = None
        tm = MagicMock()
        tm.can_trade = True

        orch = MaxBotTradeOrchestrator(
            underlying_symbol="NVDA", direction="SHORT",
            tick_size=0.01, session_builder=sb,
            signal_detector=sd, trade_manager=tm,
            option_selector=MagicMock(), entry_executor=MagicMock(),
            exit_executor=MagicMock(),
        )

        sb._t = 2000
        orch.on_bar({"time_ms": 2000, "open": 100, "high": 101,
                     "low": 99, "close": 100.5, "volume": 1000})
        assert orch.has_pending_signal
        assert "SHORT:1000" in orch._consumed_setups

        orch._pending_signal = None
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        sb._t = 3000
        orch.on_bar({"time_ms": 3000, "open": 100, "high": 101,
                     "low": 99, "close": 100.5, "volume": 1000})
        # Same setup_key, even with a fresh matching entry_timestamp_ms
        # for the current bar -> still blocked by setup consumption.
        assert not orch.has_pending_signal


# ═════════════════════════════════════════════════════════════════════════
# Diagnostic logging — SIGNAL_NOT_CURRENT includes the required fields.
# ═════════════════════════════════════════════════════════════════════════


class TestSignalNotCurrentDiagnosticLog:
    def test_log_contains_required_fields(self, caplog):
        orch = _make_real_orchestrator("LONG", symbol="AMD")
        bars = _bars_through_rejection("LONG")
        for bar in bars[:-1]:
            orch.on_bar(bar)
        orch._session_builder.add_bar(bars[-1])

        with caplog.at_level("INFO", logger="maxbot"):
            orch.on_bar(_ordinary_later_bar_long(10))

        messages = [r.message for r in caplog.records if "SIGNAL_NOT_CURRENT" in r.message]
        assert len(messages) == 1
        msg = messages[0]
        assert "AMD" in msg
        assert "setup_key=" in msg
        assert str(_ms(9)) in msg  # entry candle timestamp
        assert str(_ms(10)) in msg  # current bar timestamp


# ═════════════════════════════════════════════════════════════════════════
# ObserveOrchestrator parity — the same gate applies in observation mode.
# ═════════════════════════════════════════════════════════════════════════


class TestObserveOrchestratorParity:
    def test_observe_orchestrator_blocks_replayed_signal(self):
        sb = LiveSessionBuilder("QQQ")
        detector = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
        )
        orch = ObserveOrchestrator(
            underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
            session_builder=sb, signal_detector=detector,
            option_selector=MagicMock(),
        )

        bars = _bars_through_rejection("LONG")
        for bar in bars[:-1]:
            orch.on_bar(bar)
        sb.add_bar(bars[-1])  # rejection bar unprocessed by the orchestrator

        orch.on_bar(_ordinary_later_bar_long(10))
        assert not orch.has_pending_signal

    def test_observe_orchestrator_allows_current_signal(self):
        sb = LiveSessionBuilder("QQQ")
        detector = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            market_timezone="America/New_York", session_open="09:30",
        )
        orch = ObserveOrchestrator(
            underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
            session_builder=sb, signal_detector=detector,
            option_selector=MagicMock(),
        )

        bars = _bars_through_rejection("LONG")
        for bar in bars[:-1]:
            orch.on_bar(bar)
        orch.on_bar(bars[-1])
        assert orch.has_pending_signal
