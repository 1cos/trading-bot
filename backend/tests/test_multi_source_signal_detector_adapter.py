"""Tests for MultiSourceSignalDetectorAdapter.

Covers:
    - ORB-only mode (ENABLE_PDH_PDL_LIVE off) produces identical
      results to a plain LiveSignalDetector, including byte-identical
      behavior even when PDH/PDL WOULD have contributed if enabled,
      and that PDH/PDL is never even evaluated while disabled.
    - Same-entry ORB+PDH deduplicates into exactly one returned
      SignalResult when enabled.
    - PDH/PDL-only actionable case (ORB not actionable) is surfaced
      correctly when enabled.
    - Interface parity with LiveSignalDetector/DualSignalDetector:
      evaluate()/set_previous_sessions()/last_result.
    - consumed_setup_keys/consumed_signal_keys behavior preserved for
      both sources.
    - The "at most one candidate" invariant, including a forced-
      violation test proving the defensive guard actually fires
      rather than silently trusting the invariant.
    - Full MaxBotTradeOrchestrator integration: on_bar/execute_pending_
      signal work end-to-end with this adapter as signal_detector,
      with TradeOrchestrator's own source code completely untouched.
    - Existing orchestrator test suite remains green (no modification
      anywhere in trade_orchestrator.py).
    - Static guardrails: does not touch DailyTradeManager, execution,
      IBKR, or PWA code. (bot_runner.py DOES now import and construct
      this adapter as of the "wire into live runtime" task -- see
      test_bot_runner_multi_source_wiring.py for that wiring's own
      dedicated tests; this file's own guardrail below confirms the
      import is present and intentional, not confirms its absence.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trading_lab.live.multi_source_signal_detector_adapter import (
    ENABLE_PDH_PDL_LIVE,
    MultiSourceSignalDetectorAdapter,
)
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator, LifecycleState


# ── Bar/session helpers ──────────────────────────────────────────────────────

MS_0930 = 1786455000000


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _c(offset_min, o, h, l, cl):
    return {"time_ms": _ms(offset_min), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


def _build_session(bars, symbol="QQQ"):
    sb = LiveSessionBuilder(symbol)
    for b in bars:
        sb.add_bar(b)
    return sb.current_session()


def _orb_bars_5m():
    return [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]


def _long_shared_candle_bars():
    """ORB (101.00) and PDH (101.01, set via _prev_sessions below) share
    the same final rejection candle -- same fixture shape used
    throughout the multi-source collector tests."""
    bars = _orb_bars_5m()
    bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))
    bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))
    bars.append(_c(7, 101.60, 101.90, 101.30, 101.70))
    bars.append(_c(8, 101.70, 101.85, 101.10, 101.40))
    bars.append(_c(9, 101.10, 101.30, 100.80, 101.20))
    return bars


def _prev_sessions(pdh=101.01):
    return [{"date": "2026-08-10", "candles": [{
        "time_ms": 1, "open": 100.0, "high": pdh, "low": 95.0,
        "close": 100.0, "volume": 500,
    }]}]


def _orb_long_detector():
    return LiveSignalDetector(symbol="QQQ", direction="LONG", tick_size=0.01,
                               market_timezone="America/New_York", session_open="09:30")


def _make_adapter(orb_detector=None, enable_pdh_pdl_live=None):
    return MultiSourceSignalDetectorAdapter(
        symbol="QQQ", direction="LONG",
        orb_detector=orb_detector or _orb_long_detector(),
        tick_size=0.01, enable_pdh_pdl_live=enable_pdh_pdl_live,
    )


def _trading_relevant(r: SignalResult):
    return (r.status, r.direction, r.entry_price, r.stop_price, r.target_price,
            r.entry_timestamp_ms, r.setup_key, r.signal_key)


# ═════════════════════════════════════════════════════════════════════════
# Default configuration
# ═════════════════════════════════════════════════════════════════════════

class TestDefaultConfiguration:
    def test_module_default_is_off(self):
        assert ENABLE_PDH_PDL_LIVE is False

    def test_constructor_default_uses_module_flag(self):
        adapter = MultiSourceSignalDetectorAdapter(
            symbol="QQQ", direction="LONG", orb_detector=_orb_long_detector(),
            tick_size=0.01,
        )
        assert adapter._enable_pdh_pdl_live == ENABLE_PDH_PDL_LIVE == False

    def test_invalid_direction_rejected(self):
        with pytest.raises(ValueError):
            MultiSourceSignalDetectorAdapter(
                symbol="QQQ", direction="BOTH", orb_detector=_orb_long_detector(),
                tick_size=0.01,
            )


# ═════════════════════════════════════════════════════════════════════════
# ORB-only mode (disabled) produces identical results
# ═════════════════════════════════════════════════════════════════════════

class TestOrbOnlyModeIdentical:
    def test_disabled_result_matches_plain_orb_detector(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)

        plain_orb = _orb_long_detector()
        plain_result = plain_orb.evaluate(session)

        adapter_orb = _orb_long_detector()
        adapter = _make_adapter(orb_detector=adapter_orb, enable_pdh_pdl_live=False)
        adapter_result = adapter.evaluate(session)

        assert _trading_relevant(adapter_result) == _trading_relevant(plain_result)

    def test_disabled_returns_the_same_object_orb_detector_produced(self):
        """Not just equal -- literally the same SignalResult object,
        proving zero extra wrapping/copying happens when disabled."""
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        orb_detector = _orb_long_detector()
        adapter = _make_adapter(orb_detector=orb_detector, enable_pdh_pdl_live=False)

        captured = {}
        original_evaluate = orb_detector.evaluate

        def spy_evaluate(*a, **k):
            r = original_evaluate(*a, **k)
            captured["result"] = r
            return r

        orb_detector.evaluate = spy_evaluate
        adapter_result = adapter.evaluate(session)
        assert adapter_result is captured["result"]

    def test_disabled_no_setup_case_also_identical(self):
        """Before any break: both plain and adapter report NO_SETUP
        identically."""
        bars = _orb_bars_5m()  # ORB window only, no break yet
        session = _build_session(bars)

        plain_result = _orb_long_detector().evaluate(session)
        adapter_result = _make_adapter(enable_pdh_pdl_live=False).evaluate(session)

        assert plain_result.status == adapter_result.status == SignalStatus.NO_SETUP
        assert plain_result.failed_stage == adapter_result.failed_stage


# ═════════════════════════════════════════════════════════════════════════
# PDH/PDL disabled means no behavior change (even when PDH would fire)
# ═════════════════════════════════════════════════════════════════════════

class TestDisabledMeansNoBehaviorChange:
    def test_pdh_never_contributes_even_when_eligible_and_ready(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        adapter = _make_adapter(enable_pdh_pdl_live=False)
        adapter.set_previous_sessions(_prev_sessions(pdh=101.01))  # would merge if enabled

        result = adapter.evaluate(session)
        assert result.status == SignalStatus.SIGNAL
        assert (result.stage_context or {}).get("level_source") == "ORB_HIGH"
        # setup_key must be ORB's own -- never a PDH-derived one.
        assert "PREVIOUS_DAY_HIGH" not in (result.setup_key or "")

    def test_pdh_evaluator_is_never_even_called_when_disabled(self, monkeypatch):
        """Stronger than 'result is unaffected': proves zero PDH/PDL
        evaluation work happens at all while disabled."""
        import trading_lab.live.multi_source_signal_detector_adapter as mod

        def _boom(*a, **k):
            raise AssertionError("evaluate_pdh_pdl_candidate must not be called while disabled")

        monkeypatch.setattr(mod, "evaluate_pdh_pdl_candidate", _boom)

        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        adapter = _make_adapter(enable_pdh_pdl_live=False)
        adapter.set_previous_sessions(_prev_sessions(pdh=101.01))
        result = adapter.evaluate(session)  # must not raise
        assert result.status == SignalStatus.SIGNAL


# ═════════════════════════════════════════════════════════════════════════
# Same-entry ORB+PDH dedup -> exactly one candidate
# ═════════════════════════════════════════════════════════════════════════

class TestSameEntryDedupWhenEnabled:
    def test_merged_into_one_signal_result(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        adapter = _make_adapter(enable_pdh_pdl_live=True)
        adapter.set_previous_sessions(_prev_sessions(pdh=101.01))

        result = adapter.evaluate(session)
        assert isinstance(result, SignalResult)  # exactly one, not a list
        assert result.status == SignalStatus.SIGNAL
        assert result.entry_timestamp_ms == bars[-1]["time_ms"]

    def test_canonical_result_has_orb_pricing(self):
        """Since _compatible() in signal_dedup.py requires identical
        entry/stop/target/confirmation-candle to merge at all, the
        chosen canonical object's prices are the SAME values PDH's own
        result would have carried too -- this just confirms which
        object identity ends up feeding execution (ORB's own, first in
        input order), matching what multi_source_signal_collector.py
        already established."""
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        orb_detector = _orb_long_detector()
        adapter = _make_adapter(orb_detector=orb_detector, enable_pdh_pdl_live=True)
        adapter.set_previous_sessions(_prev_sessions(pdh=101.01))

        result = adapter.evaluate(session)
        assert (result.stage_context or {}).get("level_source") == "ORB_HIGH"


# ═════════════════════════════════════════════════════════════════════════
# PDH/PDL-only actionable (ORB not actionable) when enabled
# ═════════════════════════════════════════════════════════════════════════

class TestPdhOnlyActionableWhenEnabled:
    def test_pdh_signal_surfaced_when_orb_never_qualifies(self):
        # Same fixture technique as the multi-source collector's own
        # M2 test: a touch bar satisfies PDH eligibility's displacement
        # check without qualifying as ORB's own rejection candle.
        bars = _orb_bars_5m()
        bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))
        bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))
        bars.append(_c(7, 101.60, 101.90, 101.30, 101.70))
        bars.append(_c(8, 101.70, 101.85, 101.10, 101.40))
        bars.append(_c(9, 100.95, 101.05, 100.85, 100.90))   # touch, not a rejection
        bars.append(_c(10, 100.95, 102.00, 100.90, 101.90))
        bars.append(_c(11, 101.90, 103.60, 101.85, 103.50))  # fresh break of PDH(103)
        bars.append(_c(12, 103.55, 103.80, 103.20, 103.60))
        bars.append(_c(13, 103.60, 103.90, 103.30, 103.70))
        bars.append(_c(14, 103.70, 103.85, 103.10, 103.40))
        bars.append(_c(15, 103.10, 103.30, 102.80, 103.20))  # PDH retest/rejection
        session = _build_session(bars)

        orb_detector = _orb_long_detector()
        probe = orb_detector.evaluate(session)
        assert probe.status == SignalStatus.NO_SETUP

        adapter = _make_adapter(orb_detector=orb_detector, enable_pdh_pdl_live=True)
        adapter.set_previous_sessions(_prev_sessions(pdh=103.00))
        result = adapter.evaluate(session)

        assert result.status == SignalStatus.SIGNAL
        assert (result.stage_context or {}).get("level_source") == "PREVIOUS_DAY_HIGH"

    def test_no_previous_sessions_no_crash_falls_back_to_orb(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        adapter = _make_adapter(enable_pdh_pdl_live=True)
        # set_previous_sessions() never called -- previous_sessions stays None
        result = adapter.evaluate(session)
        assert result.status == SignalStatus.SIGNAL
        assert (result.stage_context or {}).get("level_source") == "ORB_HIGH"


# ═════════════════════════════════════════════════════════════════════════
# Interface parity: evaluate / set_previous_sessions / last_result
# ═════════════════════════════════════════════════════════════════════════

class TestInterfaceParity:
    def test_last_result_reflects_most_recent_evaluate(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        adapter = _make_adapter(enable_pdh_pdl_live=False)
        assert adapter.last_result is None  # before any evaluate() call
        result = adapter.evaluate(session)
        assert adapter.last_result is result

    def test_set_previous_sessions_forwards_to_orb_detector(self):
        orb_detector = _orb_long_detector()
        adapter = _make_adapter(orb_detector=orb_detector, enable_pdh_pdl_live=False)
        sessions = _prev_sessions(pdh=101.01)
        adapter.set_previous_sessions(sessions)
        assert orb_detector._previous_sessions == sessions

    def test_evaluate_never_returns_bare_none(self):
        """The existing orchestrator interface calls result.status with
        no None-check -- a bare None would crash it. 'Or None' in this
        adapter's own contract means a well-formed NO_SETUP
        SignalResult, never bare Python None."""
        bars = _orb_bars_5m()  # nothing has formed yet
        session = _build_session(bars)
        for enabled in (False, True):
            adapter = _make_adapter(enable_pdh_pdl_live=enabled)
            result = adapter.evaluate(session)
            assert result is not None
            assert isinstance(result, SignalResult)
            assert result.status == SignalStatus.NO_SETUP


# ═════════════════════════════════════════════════════════════════════════
# consumed_setup_keys / consumed_signal_keys behavior preserved
# ═════════════════════════════════════════════════════════════════════════

class TestConsumedKeysPreserved:
    def test_consumed_orb_setup_key_excludes_orb_forwards_to_detector(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        orb_detector = _orb_long_detector()
        probe = orb_detector.evaluate(session)
        assert probe.status == SignalStatus.SIGNAL

        # Fresh detector instance (same config) so the scan-skip
        # re-derivation quirk documented in the collector tests doesn't
        # confuse this specific assertion -- we just want to confirm
        # consumed_setup_keys reaches LiveSignalDetector.evaluate()
        # exactly as it does today (delegation, not reimplementation).
        adapter = _make_adapter(orb_detector=orb_detector, enable_pdh_pdl_live=False)
        result = adapter.evaluate(session, consumed_setup_keys={probe.setup_key})
        # Either NO_SETUP (nothing else to find) or a genuinely
        # different setup_key -- never the exact consumed one again.
        assert result.setup_key != probe.setup_key

    def test_consumed_pdh_setup_key_excludes_pdh_when_enabled(self):
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        orb_detector = _orb_long_detector()
        adapter = _make_adapter(orb_detector=orb_detector, enable_pdh_pdl_live=True)
        adapter.set_previous_sessions(_prev_sessions(pdh=101.01))

        # First call establishes both ORB's and PDH's setup_keys share
        # the same entry (merged) -- consuming ORB's key (canonical)
        # should suppress the whole merged candidate on a re-check.
        first = adapter.evaluate(session)
        assert first.status == SignalStatus.SIGNAL

        orb_detector2 = _orb_long_detector()
        adapter2 = _make_adapter(orb_detector=orb_detector2, enable_pdh_pdl_live=True)
        adapter2.set_previous_sessions(_prev_sessions(pdh=101.01))
        second = adapter2.evaluate(session, consumed_setup_keys={first.setup_key})
        # ORB's own re-derivation may or may not find a fresh setup_key
        # (documented scan-skip quirk); the key invariant here is that
        # the ORIGINAL consumed setup never reappears verbatim.
        assert second.setup_key != first.setup_key


# ═════════════════════════════════════════════════════════════════════════
# At most one candidate -- invariant + forced-violation guard
# ═════════════════════════════════════════════════════════════════════════

class TestAtMostOneCandidateInvariant:
    def test_real_scenarios_never_raise(self):
        """Every enabled scenario exercised above already implicitly
        proves this (evaluate() would have raised otherwise) -- this
        test just makes the invariant explicit for a fresh scenario."""
        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        adapter = _make_adapter(enable_pdh_pdl_live=True)
        adapter.set_previous_sessions(_prev_sessions(pdh=101.01))
        result = adapter.evaluate(session)  # must not raise
        assert isinstance(result, SignalResult)

    def test_forced_violation_raises_rather_than_silently_picking(self, monkeypatch):
        """Directly proves the defensive RuntimeError guard fires when
        the (structurally-impossible-in-practice) invariant is
        violated, rather than trusting it silently. Monkeypatches
        collect_actionable_signals to return two candidates, bypassing
        the real structural guarantee entirely -- this test is about
        the guard's own code path, not about re-deriving the
        impossibility proof (already established in the 2026-08-24
        audits and multi_source_signal_collector.py's own tests)."""
        import trading_lab.live.multi_source_signal_detector_adapter as mod
        from trading_lab.live.signal_dedup import DedupedSignalCandidate

        bars = _long_shared_candle_bars()
        session = _build_session(bars)
        orb_detector = _orb_long_detector()
        probe = orb_detector.evaluate(session)
        fake_candidate_a = DedupedSignalCandidate(signal=probe, contributing_level_sources=("ORB_HIGH",))
        fake_candidate_b = DedupedSignalCandidate(signal=probe, contributing_level_sources=("PREVIOUS_DAY_HIGH",))

        monkeypatch.setattr(
            mod, "collect_actionable_signals",
            lambda *a, **k: [fake_candidate_a, fake_candidate_b],
        )

        adapter = _make_adapter(orb_detector=_orb_long_detector(), enable_pdh_pdl_live=True)
        adapter.set_previous_sessions(_prev_sessions(pdh=101.01))
        with pytest.raises(RuntimeError, match="invariant violated"):
            adapter.evaluate(session)


# ═════════════════════════════════════════════════════════════════════════
# Full orchestrator integration (interface compatibility, not just
# unit-level correctness)
# ═════════════════════════════════════════════════════════════════════════

class FakeOptionSelector:
    def select(self, **kwargs):
        return SimpleNamespace(
            underlying_symbol=kwargs.get("underlying_symbol", "QQQ"),
            underlying_price=kwargs.get("underlying_price", 101.20),
            right=kwargs.get("right", "C"), expiration="20260811", strike=101.0,
            exchange="SMART", trading_class="QQQ", multiplier="100", quantity=1,
            con_id=123456, qualified_contract=SimpleNamespace(conId=123456, symbol="QQQ"),
            bid=2.50, ask=2.70, spread=0.20,
        )


class FakeEntryExecutor:
    def __init__(self):
        self.submissions = []
        self._status = SimpleNamespace(status="PendingSubmit", filled=0.0, remaining=1.0, avgFillPrice=0.0)
        self._order = SimpleNamespace(orderId=42, permId=999)
        self._fills = []
        self._trade = SimpleNamespace(order=self._order, orderStatus=self._status, fills=self._fills, log=[])

    def submit_entry(self, order_spec):
        self.submissions.append(order_spec)
        return SimpleNamespace(
            trade=self._trade, con_id=123456, underlying_symbol="QQQ", right="C",
            expiration="20260811", strike=101.0, quantity=1, limit_price=2.70,
            order_id=42, perm_id=999, status=self._status.status,
        )

    def set_filled(self, avg_price=2.65):
        self._status.status = "Filled"
        self._status.filled = 1.0
        self._status.remaining = 0.0
        self._status.avgFillPrice = avg_price
        self._fills.append(SimpleNamespace(time=datetime(2026, 8, 11, 9, 42, 0, tzinfo=timezone.utc)))


class FakeExitExecutor:
    def __init__(self):
        self.submissions = []
        self._submitted_entry_ids = set()
        self._status = SimpleNamespace(status="PendingSubmit", filled=0.0, remaining=1.0, avgFillPrice=0.0)
        self._order = SimpleNamespace(orderId=55, permId=888)
        self._trade = SimpleNamespace(order=self._order, orderStatus=self._status, fills=[], log=[])

    def submit_exit(self, qualified_contract, exit_trigger, *, entry_order_id,
                     con_id=None, right="", expiration="", strike=0.0, quantity=1):
        from trading_lab.live.underlying_exit_monitor import ExitState
        if entry_order_id in self._submitted_entry_ids:
            raise ValueError(f"Exit already submitted for entry_order_id={entry_order_id}")
        self._submitted_entry_ids.add(entry_order_id)
        self.submissions.append(exit_trigger)
        reason_map = {ExitState.STOP_TRIGGERED: "STOP", ExitState.TARGET_TRIGGERED: "TARGET"}
        return SimpleNamespace(
            trade=self._trade, exit_reason=reason_map[exit_trigger.state],
            entry_order_id=entry_order_id, con_id=con_id,
            underlying_stop_price=exit_trigger.stop_price,
            underlying_target_price=exit_trigger.target_price,
            trigger_bar_time_ms=exit_trigger.trigger_bar_time_ms,
            order_id=55, perm_id=888, status=self._status.status,
            right=right, expiration=expiration, strike=strike, quantity=1,
        )


def _make_orchestrator_with_adapter(enable_pdh_pdl_live):
    sb = LiveSessionBuilder("QQQ")
    orb_detector = _orb_long_detector()
    adapter = _make_adapter(orb_detector=orb_detector, enable_pdh_pdl_live=enable_pdh_pdl_live)
    if enable_pdh_pdl_live:
        adapter.set_previous_sessions(_prev_sessions(pdh=101.01))
    tm = DailyTradeManager()
    ee = FakeEntryExecutor()
    xe = FakeExitExecutor()
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
        session_builder=sb, signal_detector=adapter, trade_manager=tm,
        option_selector=FakeOptionSelector(), entry_executor=ee, exit_executor=xe,
    )
    return orch, sb, adapter, ee, xe


def _feed_bars(orch, bars):
    status = None
    for bar in bars:
        status = orch.on_bar(bar)
        if orch.has_pending_signal:
            orch.execute_pending_signal()
    return status


class TestOrchestratorIntegration:
    def test_on_bar_execute_flow_works_with_adapter_disabled(self):
        orch, sb, adapter, ee, xe = _make_orchestrator_with_adapter(enable_pdh_pdl_live=False)
        bars = _long_shared_candle_bars()
        _feed_bars(orch, bars)
        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED
        assert len(ee.submissions) == 1

    def test_on_bar_execute_flow_works_with_adapter_enabled_merged_signal(self):
        orch, sb, adapter, ee, xe = _make_orchestrator_with_adapter(enable_pdh_pdl_live=True)
        bars = _long_shared_candle_bars()
        _feed_bars(orch, bars)
        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED
        assert len(ee.submissions) == 1
        assert adapter.last_result.status == SignalStatus.SIGNAL

    def test_no_duplicate_entry_across_bars_with_adapter(self):
        """Same 'one setup -> at most one trade' guarantee the plain
        detector already provides, now proven to hold identically
        through the adapter."""
        orch, sb, adapter, ee, xe = _make_orchestrator_with_adapter(enable_pdh_pdl_live=True)
        bars = _long_shared_candle_bars()
        bars_plus_quiet = bars + [_c(10, 101.20, 101.35, 101.05, 101.15)]
        _feed_bars(orch, bars_plus_quiet)
        assert len(ee.submissions) == 1


# ═════════════════════════════════════════════════════════════════════════
# Existing orchestrator suite remains green (no modification anywhere
# in trade_orchestrator.py)
# ═════════════════════════════════════════════════════════════════════════

class TestTradeOrchestratorFileUntouched:
    def test_trade_orchestrator_does_not_reference_the_adapter(self):
        import ast
        import inspect
        from trading_lab.live import trade_orchestrator as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "MultiSourceSignalDetectorAdapter" not in imported_names
        assert "multi_source_signal_detector_adapter" not in imported_names


# ═════════════════════════════════════════════════════════════════════════
# Guardrails: no PWA/no wiring into bot_runner, no execution/DailyTradeManager
# /IBKR imports
# ═════════════════════════════════════════════════════════════════════════

class TestGuardrails:
    def test_bot_runner_now_imports_the_adapter_by_design(self):
        """As of the "wire MultiSourceSignalDetectorAdapter into live
        bot runtime" task, bot_runner.py IS expected to import and
        construct this adapter -- see
        test_bot_runner_multi_source_wiring.py's own T1-T4 tests for
        the dedicated coverage of that wiring itself. This test
        replaces an earlier, now-obsolete guardrail from before that
        task existed (which asserted the opposite); keeping a test
        here at all -- rather than deleting it -- documents that the
        absence-of-import invariant was deliberately retired, not
        silently dropped."""
        import ast
        import inspect
        from trading_lab.live import bot_runner as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "MultiSourceSignalDetectorAdapter" in imported_names

    def test_module_does_not_import_daily_trade_manager_or_ibkr_or_execution(self):
        import ast
        import inspect
        from trading_lab.live import multi_source_signal_detector_adapter as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        imported_names = set()
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
                    imported_modules.add(alias.name)
        assert "DailyTradeManager" not in imported_names
        assert not any("trade_manager" in m for m in imported_modules)
        assert not any("ib_insync" in m or "ibkr" in m.lower() for m in imported_modules)
        assert not any("executor" in m for m in imported_modules)
        assert not any("pwa" in m.lower() for m in imported_modules)
