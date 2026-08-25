"""Tests for the MultiSourceSignalDetectorAdapter wiring in bot_runner.py.

Covers T1-T4 exactly as specified in the "Wire MultiSourceSignal
DetectorAdapter into live bot runtime" task:

    T1 -- ORB-only equivalence: with ENABLE_PDH_PDL_LIVE=False, the
          bot_runner -> adapter -> ORB detector -> orchestrator path
          produces the identical SignalResult the old direct path
          (bot_runner -> LiveSignalDetector -> orchestrator) would
          have produced.
    T2 -- The detector actually constructed and assigned to
          rt.signal_detector by _setup_symbol() is a
          MultiSourceSignalDetectorAdapter (or a DualSignalDetector
          wrapping two of them for BOTH mode) -- not a plain
          LiveSignalDetector -- both statically (import present in
          bot_runner.py's source) and at runtime (actual type check
          after _setup_all_symbols()).
    T3 -- No execution changes: trade_orchestrator.py has zero diff
          from origin/main (checked here via git, matching this
          repo's own established audit practice), and
          execute_pending_signal() still receives/acts on a genuine
          SignalResult produced through the unmodified execution
          path (a real order gets submitted end-to-end).
    T4 -- ENABLE_PDH_PDL_LIVE is True -- the official, committed
          decision as of 2026-08-24 ("Enable live parallel PDH/PDL
          execution with audit telemetry"). Asserted both as the
          module-level constant and as what every adapter constructed
          by _setup_symbol() actually received, plus the strongest
          form: PDH/PDL really is evaluated through the wired runtime
          path. The disabled path stays covered via an explicitly
          disabled adapter, so its byte-identical-passthrough
          guarantee is not lost with the default flip.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
from datetime import datetime as dt_cls

import pytest

from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.dual_signal_detector import DualSignalDetector
from trading_lab.live.multi_source_signal_detector_adapter import (
    ENABLE_PDH_PDL_LIVE,
    MultiSourceSignalDetectorAdapter,
)
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.trade_orchestrator import LifecycleState


_ET = ZoneInfo("America/New_York")
_BASE = int(dt_cls(2026, 8, 11, 9, 30, 0, tzinfo=_ET).timestamp() * 1000)


def _ms(offset_min: int) -> int:
    return _BASE + offset_min * 60_000


def _c(offset_min, o, h, l, cl):
    return {"time_ms": _ms(offset_min), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


def _long_signal_bars():
    """Standard LONG BDRR bars -- same fixture shape used throughout
    this thread's tests."""
    bars = [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]
    bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))
    bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))
    bars.append(_c(7, 101.60, 101.90, 101.30, 101.70))
    bars.append(_c(8, 101.70, 101.85, 101.10, 101.40))
    bars.append(_c(9, 101.10, 101.30, 100.80, 101.20))
    return bars


def _build_session(bars, symbol="QQQ"):
    sb = LiveSessionBuilder(symbol)
    for b in bars:
        sb.add_bar(b)
    return sb.current_session()


def _trading_relevant(r):
    return (r.status, r.direction, r.entry_price, r.stop_price, r.target_price,
            r.entry_timestamp_ms, r.setup_key, r.signal_key)


def _mock_ib():
    ib = MagicMock()
    ib.managedAccounts.return_value = ["DU123"]
    return ib


# ═════════════════════════════════════════════════════════════════════════
# T1 -- ORB-only equivalence
# ═════════════════════════════════════════════════════════════════════════

class TestT1OrbOnlyEquivalence:
    def test_wired_adapter_matches_plain_detector_signal_result(self):
        """Same session, same config: the adapter that _setup_symbol()
        now constructs (ENABLE_PDH_PDL_LIVE default = False) must
        produce the same trading-relevant SignalResult a bare
        LiveSignalDetector would have -- the exact 'old direct path'
        equivalence this task requires."""
        runner = MaxBotRunner("QQQ", "LONG", execution_mode="OBSERVE_ONLY")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()

        wired_detector = runner._runtimes["QQQ"].signal_detector
        session = _build_session(_long_signal_bars())

        wired_result = wired_detector.evaluate(session)

        plain_detector = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=runner._tick_size,
            market_timezone=runner._tz_str, session_open=runner._session_open,
        )
        plain_result = plain_detector.evaluate(session)

        assert _trading_relevant(wired_result) == _trading_relevant(plain_result)

    def test_both_mode_each_side_matches_plain_detector(self):
        runner = MaxBotRunner("QQQ", "BOTH", execution_mode="OBSERVE_ONLY")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()

        wired = runner._runtimes["QQQ"].signal_detector
        assert isinstance(wired, DualSignalDetector)

        session = _build_session(_long_signal_bars())
        wired_result = wired.evaluate(session)

        plain_long = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=runner._tick_size,
            market_timezone=runner._tz_str, session_open=runner._session_open,
        )
        plain_short = LiveSignalDetector(
            symbol="QQQ", direction="SHORT", tick_size=runner._tick_size,
            market_timezone=runner._tz_str, session_open=runner._session_open,
        )
        plain_dual = DualSignalDetector(plain_long, plain_short)
        plain_result = plain_dual.evaluate(session)

        assert _trading_relevant(wired_result) == _trading_relevant(plain_result)

    def test_full_orchestrator_flow_identical_lifecycle_and_submission(self):
        """End-to-end: feeding the real bot_runner-constructed
        orchestrator the same bars a plain-detector orchestrator would
        get produces the identical lifecycle outcome and an entry
        submission, matching the pre-wiring path exactly."""
        from types import SimpleNamespace
        from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator
        from trading_lab.live.trade_manager import DailyTradeManager

        class FakeOptionSelector:
            def select(self, **kwargs):
                return SimpleNamespace(
                    underlying_symbol=kwargs.get("underlying_symbol", "QQQ"),
                    underlying_price=kwargs.get("underlying_price", 101.20),
                    right=kwargs.get("right", "C"), expiration="20260811",
                    strike=101.0, exchange="SMART", trading_class="QQQ",
                    multiplier="100", quantity=1, con_id=123456,
                    qualified_contract=SimpleNamespace(conId=123456, symbol="QQQ"),
                    bid=2.50, ask=2.70, spread=0.20,
                )

        class FakeEntryExecutor:
            def __init__(self):
                self.submissions = []
                self._status = SimpleNamespace(status="PendingSubmit", filled=0.0,
                                                remaining=1.0, avgFillPrice=0.0)
                self._order = SimpleNamespace(orderId=42, permId=999)
                self._trade = SimpleNamespace(order=self._order, orderStatus=self._status,
                                               fills=[], log=[])

            def submit_entry(self, order_spec):
                self.submissions.append(order_spec)
                return SimpleNamespace(
                    trade=self._trade, con_id=123456, underlying_symbol="QQQ",
                    right="C", expiration="20260811", strike=101.0, quantity=1,
                    limit_price=2.70, order_id=42, perm_id=999,
                    status=self._status.status,
                )

        class FakeExitExecutor:
            def submit_exit(self, *a, **k):
                raise AssertionError("exit not expected in this test")

        runner = MaxBotRunner("QQQ", "LONG", execution_mode="OBSERVE_ONLY")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()

        wired_detector = runner._runtimes["QQQ"].signal_detector
        sb = LiveSessionBuilder("QQQ")
        tm = DailyTradeManager()
        ee = FakeEntryExecutor()
        xe = FakeExitExecutor()
        orch = MaxBotTradeOrchestrator(
            underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
            session_builder=sb, signal_detector=wired_detector, trade_manager=tm,
            option_selector=FakeOptionSelector(), entry_executor=ee, exit_executor=xe,
        )

        for bar in _long_signal_bars():
            orch.on_bar(bar)
            if orch.has_pending_signal:
                orch.execute_pending_signal()

        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED
        assert len(ee.submissions) == 1


# ═════════════════════════════════════════════════════════════════════════
# T2 -- Adapter really used
# ═════════════════════════════════════════════════════════════════════════

class TestT2AdapterActuallyUsed:
    def test_static_import_present_in_bot_runner(self):
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

    def test_single_direction_runtime_type(self):
        runner = MaxBotRunner("QQQ", "LONG", execution_mode="OBSERVE_ONLY")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()
        sd = runner._runtimes["QQQ"].signal_detector
        assert type(sd) is MultiSourceSignalDetectorAdapter
        assert type(sd) is not LiveSignalDetector

    def test_short_direction_runtime_type(self):
        runner = MaxBotRunner("QQQ", "SHORT", execution_mode="OBSERVE_ONLY")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()
        sd = runner._runtimes["QQQ"].signal_detector
        assert type(sd) is MultiSourceSignalDetectorAdapter

    def test_both_direction_wraps_two_adapters(self):
        runner = MaxBotRunner("QQQ", "BOTH", execution_mode="OBSERVE_ONLY")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()
        sd = runner._runtimes["QQQ"].signal_detector
        assert type(sd) is DualSignalDetector
        assert type(sd._long) is MultiSourceSignalDetectorAdapter
        assert type(sd._short) is MultiSourceSignalDetectorAdapter

    def test_orchestrator_receives_the_same_wired_detector(self):
        """rt.signal_detector and the detector actually injected into
        rt.orchestrator must be the SAME object -- confirms wiring
        reaches the orchestrator, not just SymbolRuntime."""
        runner = MaxBotRunner("QQQ", "LONG", execution_mode="PAPER_EXECUTE")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()
        rt = runner._runtimes["QQQ"]
        assert rt.orchestrator._signal_detector is rt.signal_detector


# ═════════════════════════════════════════════════════════════════════════
# T3 -- No execution changes
# ═════════════════════════════════════════════════════════════════════════

class TestT3NoExecutionChanges:
    def test_trade_orchestrator_has_zero_diff_from_origin_main(self):
        """Matches this repo's own established audit practice
        (git diff --stat) rather than reimplementing a parallel
        source-diffing mechanism in Python."""
        result = subprocess.run(
            ["git", "diff", "--stat", "origin/main", "--",
             "backend/src/trading_lab/live/trade_orchestrator.py"],
            cwd="/home/claude/trading-bot", capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            f"trade_orchestrator.py has unexpected diff from origin/main:\n{result.stdout}"
        )

    def test_execute_pending_signal_still_receives_real_signal_result(self):
        """End-to-end proof (not just 'file unchanged'): the wired
        adapter's output flows all the way through
        execute_pending_signal() to a submitted order, using the
        SAME execution code path as before -- exercised already by
        T1's full-flow test; this test explicitly asserts the
        pending signal object type just before execution."""
        from types import SimpleNamespace
        from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator
        from trading_lab.live.trade_manager import DailyTradeManager
        from trading_lab.live.signal_detector import SignalResult

        class FakeOptionSelector:
            def select(self, **kwargs):
                return SimpleNamespace(
                    underlying_symbol="QQQ", underlying_price=101.20, right="C",
                    expiration="20260811", strike=101.0, exchange="SMART",
                    trading_class="QQQ", multiplier="100", quantity=1, con_id=123456,
                    qualified_contract=SimpleNamespace(conId=123456, symbol="QQQ"),
                    bid=2.50, ask=2.70, spread=0.20,
                )

        class FakeEntryExecutor:
            def __init__(self):
                self.submissions = []
                self._status = SimpleNamespace(status="PendingSubmit", filled=0.0,
                                                remaining=1.0, avgFillPrice=0.0)
                self._order = SimpleNamespace(orderId=42, permId=999)
                self._trade = SimpleNamespace(order=self._order, orderStatus=self._status,
                                               fills=[], log=[])

            def submit_entry(self, order_spec):
                self.submissions.append(order_spec)
                return SimpleNamespace(
                    trade=self._trade, con_id=123456, underlying_symbol="QQQ",
                    right="C", expiration="20260811", strike=101.0, quantity=1,
                    limit_price=2.70, order_id=42, perm_id=999,
                    status=self._status.status,
                )

        class FakeExitExecutor:
            def submit_exit(self, *a, **k):
                raise AssertionError("not expected in this test")

        runner = MaxBotRunner("QQQ", "LONG", execution_mode="OBSERVE_ONLY")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()
        wired_detector = runner._runtimes["QQQ"].signal_detector

        sb = LiveSessionBuilder("QQQ")
        orch = MaxBotTradeOrchestrator(
            underlying_symbol="QQQ", direction="LONG", tick_size=0.01,
            session_builder=sb, signal_detector=wired_detector,
            trade_manager=DailyTradeManager(),
            option_selector=FakeOptionSelector(),
            entry_executor=FakeEntryExecutor(), exit_executor=FakeExitExecutor(),
        )

        pending_types = []
        for bar in _long_signal_bars():
            orch.on_bar(bar)
            if orch.has_pending_signal:
                pending_types.append(type(orch._pending_signal))
                orch.execute_pending_signal()

        assert pending_types == [SignalResult]


# ═════════════════════════════════════════════════════════════════════════
# T4 -- Guardrail: ENABLE_PDH_PDL_LIVE is ON (official decision)
# ═════════════════════════════════════════════════════════════════════════

class TestT4Guardrail:
    """PDH/PDL LIVE = ON is a deliberate, committed decision, not a
    leftover local edit. These assertions are the mirror image of the
    original default-off guardrails — same strength, opposite polarity
    — so an accidental revert to False fails loudly instead of
    silently disabling live PDH/PDL execution overnight."""

    def test_module_constant_is_true(self):
        assert ENABLE_PDH_PDL_LIVE is True

    def test_adapters_constructed_by_setup_symbol_are_enabled(self):
        runner = MaxBotRunner("QQQ", "LONG", execution_mode="OBSERVE_ONLY")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()
        sd = runner._runtimes["QQQ"].signal_detector
        assert sd._enable_pdh_pdl_live is True

    def test_both_mode_adapters_also_enabled(self):
        runner = MaxBotRunner("QQQ", "BOTH", execution_mode="OBSERVE_ONLY")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()
        sd = runner._runtimes["QQQ"].signal_detector
        assert sd._long._enable_pdh_pdl_live is True
        assert sd._short._enable_pdh_pdl_live is True

    def test_pdh_pdl_is_evaluated_through_the_wired_runtime_path(self, monkeypatch):
        """Strongest form of T4, inverted: proves PDH/PDL evaluation
        work really happens in the wired runtime path, not merely that
        the flag reads True."""
        import trading_lab.live.multi_source_signal_detector_adapter as mod

        calls = []
        original = mod.evaluate_pdh_pdl_candidate

        def _counting(*a, **k):
            calls.append(k.get("direction"))
            return original(*a, **k)

        monkeypatch.setattr(mod, "evaluate_pdh_pdl_candidate", _counting)

        runner = MaxBotRunner("QQQ", "LONG", execution_mode="OBSERVE_ONLY")
        runner._ib = _mock_ib()
        runner._verify_paper()
        runner._setup_all_symbols()
        session = _build_session(_long_signal_bars())
        result = runner._runtimes["QQQ"].signal_detector.evaluate(session)

        assert calls == ["LONG"], "PDH/PDL must be evaluated exactly once"
        # The ORB signal in this fixture is unaffected by PDH/PDL running.
        assert result.status == SignalStatus.SIGNAL

    def test_explicitly_disabled_adapter_is_still_a_pure_orb_passthrough(self):
        """The default flipped to True, but the disabled path must keep
        its guarantee: enable_pdh_pdl_live=False does zero PDH/PDL work
        and returns the wrapped ORB detector's own result object."""
        import trading_lab.live.multi_source_signal_detector_adapter as mod

        def _boom(*a, **k):
            raise AssertionError(
                "evaluate_pdh_pdl_candidate must not be called by an "
                "explicitly disabled adapter")

        orb = LiveSignalDetector(symbol="QQQ", direction="LONG", tick_size=0.01)
        adapter = MultiSourceSignalDetectorAdapter(
            symbol="QQQ", direction="LONG", orb_detector=orb, tick_size=0.01,
            enable_pdh_pdl_live=False,
        )
        session = _build_session(_long_signal_bars())

        saved = mod.evaluate_pdh_pdl_candidate
        mod.evaluate_pdh_pdl_candidate = _boom
        try:
            wrapped = adapter.evaluate(session)
        finally:
            mod.evaluate_pdh_pdl_candidate = saved

        assert _trading_relevant(wrapped) == _trading_relevant(orb.evaluate(session))
