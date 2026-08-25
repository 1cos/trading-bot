"""Pre-market readiness: a PDH/PDL setup must reach a real PAPER order.

End-to-end proof that a SignalResult carrying level_source
PREVIOUS_DAY_HIGH / PREVIOUS_DAY_LOW travels the FULL live path —
MultiSourceSignalDetectorAdapter -> collect_actionable_signals ->
MaxBotTradeOrchestrator -> build_trade_plan -> option selection ->
entry submission — with exactly one order per Max Entry Candle.

Nothing here adds behavior: it only exercises the shipped path. In
particular there is NO 84% Rule / same-level reclaim anywhere in this
file or in what it exercises.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
from datetime import datetime as dt_cls


from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.multi_source_signal_detector_adapter import (
    ENABLE_PDH_PDL_LIVE,
    MultiSourceSignalDetectorAdapter,
)
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_dedup import (
    SignalObservation,
    collect_actionable_signals,
)
from trading_lab.live.signal_detector import SignalResult, SignalStatus
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.trade_orchestrator import (
    LifecycleState,
    MaxBotTradeOrchestrator,
)


_ET = ZoneInfo("America/New_York")
_BASE = int(dt_cls(2026, 8, 11, 9, 30, 0, tzinfo=_ET).timestamp() * 1000)

PDH = 101.90
PDL = 98.10


def _ms(m: int) -> int:
    return _BASE + m * 60_000


def _c(m, o, h, l, cl):
    return {"time_ms": _ms(m), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


def _orb():
    """ORB window idx0-4: high 101.00, low 99.00."""
    return [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]


def _long_pdh_bars():
    """ORB High break -> displacement -> PDH break -> PDH displacement
    -> PDH retest -> Max Entry Candle. Price never returns to ORB_HIGH,
    so ONLY the PDH sequence can produce the entry."""
    return _orb() + [
        _c(5, 100.80, 101.60, 100.70, 101.50),   # ORB BREAK
        _c(6, 101.50, 101.80, 101.10, 101.60),   # ORB DISP 1
        _c(7, 101.60, 101.85, 101.15, 101.70),   # ORB DISP 2
        _c(8, 101.70, 101.88, 101.20, 101.75),   # ORB DISP 3
        _c(9, 101.75, 102.30, 101.70, 102.20),   # ORB DISP 4 + PDH BREAK
        _c(10, 102.20, 102.60, 102.00, 102.50),  # PDH DISP 1
        _c(11, 102.50, 102.70, 102.05, 102.40),  # PDH DISP 2
        _c(12, 102.40, 102.55, 102.10, 102.35),  # PDH DISP 3
        _c(13, 102.35, 102.45, 101.70, 102.30),  # PDH RETEST + Max Entry Candle
        _c(14, 102.30, 102.70, 102.25, 102.65),
    ]


def _short_pdl_bars():
    """Mirror on ORB_LOW / PDL."""
    return _orb() + [
        _c(5, 100.20, 100.30, 98.40, 98.50),     # ORB BREAK
        _c(6, 98.50, 98.90, 98.30, 98.40),       # ORB DISP 1
        _c(7, 98.40, 98.85, 98.25, 98.35),       # ORB DISP 2
        _c(8, 98.35, 98.80, 98.20, 98.30),       # ORB DISP 3
        _c(9, 98.30, 98.35, 97.70, 97.80),       # ORB DISP 4 + PDL BREAK
        _c(10, 97.80, 98.00, 97.40, 97.50),      # PDL DISP 1
        _c(11, 97.50, 97.95, 97.30, 97.60),      # PDL DISP 2
        _c(12, 97.60, 97.90, 97.45, 97.65),      # PDL DISP 3
        _c(13, 97.65, 98.30, 97.55, 97.70),      # PDL RETEST + Max Entry Candle
        _c(14, 97.70, 97.75, 97.30, 97.35),
    ]


def _prev_sessions():
    return [{"date": "2026-08-10", "candles": [
        {"time_ms": 1, "open": 100.0, "high": PDH, "low": PDL,
         "close": 100.0, "volume": 1}]}]


def _mock_ib():
    ib = MagicMock()
    ib.managedAccounts.return_value = ["DU123"]
    return ib


class FakeOptionSelector:
    def __init__(self):
        self.calls = []

    def select(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            underlying_symbol="QQQ", underlying_price=102.30,
            right=kwargs.get("right", "C"), expiration="20260811",
            strike=102.0, exchange="SMART", trading_class="QQQ",
            multiplier="100", quantity=1, con_id=123456,
            qualified_contract=SimpleNamespace(conId=123456, symbol="QQQ"),
            bid=2.50, ask=2.70, spread=0.20,
        )


class FakeEntryExecutor:
    def __init__(self):
        self.submissions = []
        self._status = SimpleNamespace(status="PendingSubmit", filled=0.0,
                                       remaining=1.0, avgFillPrice=0.0)
        self._trade = SimpleNamespace(
            order=SimpleNamespace(orderId=42, permId=999),
            orderStatus=self._status, fills=[], log=[])

    def submit_entry(self, order_spec):
        self.submissions.append(order_spec)
        return SimpleNamespace(
            trade=self._trade, con_id=123456, underlying_symbol="QQQ",
            right="C", expiration="20260811", strike=102.0, quantity=1,
            limit_price=2.70, order_id=42, perm_id=999,
            status=self._status.status)


class FakeExitExecutor:
    def submit_exit(self, *a, **k):
        raise AssertionError("exit not expected in a readiness test")


def _wired_detector(direction: str):
    """The detector bot_runner._setup_symbol() really builds, in the
    real PAPER_EXECUTE configuration."""
    runner = MaxBotRunner("QQQ", direction, execution_mode="PAPER_EXECUTE")
    runner._ib = _mock_ib()
    runner._verify_paper()
    runner._setup_all_symbols()
    sd = runner._runtimes["QQQ"].signal_detector
    sd.set_previous_sessions(_prev_sessions())
    return runner, sd


def _run_to_entry(direction, bars):
    """Feed bars through a real orchestrator wired to the real adapter."""
    runner, sd = _wired_detector(direction)
    sb = LiveSessionBuilder("QQQ")
    ee, os_ = FakeEntryExecutor(), FakeOptionSelector()
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="QQQ", direction=direction, tick_size=0.01,
        session_builder=sb, signal_detector=sd,
        trade_manager=DailyTradeManager(),
        option_selector=os_, entry_executor=ee, exit_executor=FakeExitExecutor())

    pending = []
    for bar in bars:
        orch.on_bar(bar)
        if orch.has_pending_signal:
            pending.append(orch._pending_signal)
            orch.execute_pending_signal()
    return orch, ee, os_, pending


# ═════════════════════════════════════════════════════════════════════════
# 1 — the flag the bot will actually start with
# ═════════════════════════════════════════════════════════════════════════

class TestFlagState:
    def test_enable_pdh_pdl_live_is_true(self):
        assert ENABLE_PDH_PDL_LIVE is True

    def test_every_adapter_built_by_setup_symbol_is_enabled(self):
        for direction in ("LONG", "SHORT", "BOTH"):
            runner = MaxBotRunner("QQQ", direction,
                                  execution_mode="PAPER_EXECUTE")
            runner._ib = _mock_ib()
            runner._verify_paper()
            runner._setup_all_symbols()
            sd = runner._runtimes["QQQ"].signal_detector
            adapters = ([sd._long, sd._short]
                        if hasattr(sd, "_long") else [sd])
            for a in adapters:
                assert isinstance(a, MultiSourceSignalDetectorAdapter)
                assert a._enable_pdh_pdl_live is True


# ═════════════════════════════════════════════════════════════════════════
# 2/3/6 — a PD signal reaches the orchestrator and becomes an order
# ═════════════════════════════════════════════════════════════════════════

class TestLongPdhEndToEnd:
    def test_pdh_signal_becomes_a_submitted_entry(self):
        orch, ee, os_, pending = _run_to_entry("LONG", _long_pdh_bars())

        assert len(pending) == 1, "exactly one signal expected"
        sig = pending[0]
        assert isinstance(sig, SignalResult)
        assert sig.status == SignalStatus.SIGNAL
        assert sig.direction == "LONG"

        # 3 — the level_source really is PDH, not ORB
        assert sig.setup_key.startswith("LONG:PREVIOUS_DAY_HIGH:")
        assert sig.signal_key.startswith("LONG:PREVIOUS_DAY_HIGH:")

        # 5 — a real trade plan was built without any ORB assumption
        assert sig.trade_plan is not None
        assert sig.entry_price is not None
        assert sig.stop_price is not None
        assert sig.target_price is not None
        assert sig.stop_price < sig.entry_price < sig.target_price

        # execution actually happened
        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED
        assert len(ee.submissions) == 1
        assert len(os_.calls) == 1
        assert os_.calls[0]["right"] == "C"

    def test_entry_candle_is_the_pdh_retest_bar(self):
        _, _, _, pending = _run_to_entry("LONG", _long_pdh_bars())
        assert pending[0].entry_timestamp_ms == _ms(13)

    def test_orb_alone_would_not_have_produced_this_trade(self):
        """The ORB sequence never retests ORB_HIGH in this fixture, so
        the trade exists only because PDH ran in parallel."""
        from trading_lab.live.signal_detector import LiveSignalDetector

        sb = LiveSessionBuilder("QQQ")
        for b in _long_pdh_bars():
            sb.add_bar(b)
        orb_only = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01)
        assert orb_only.evaluate(sb.current_session()).status != SignalStatus.SIGNAL


class TestShortPdlEndToEnd:
    def test_pdl_signal_becomes_a_submitted_entry(self):
        orch, ee, os_, pending = _run_to_entry("SHORT", _short_pdl_bars())

        assert len(pending) == 1
        sig = pending[0]
        assert sig.status == SignalStatus.SIGNAL
        assert sig.direction == "SHORT"
        assert sig.setup_key.startswith("SHORT:PREVIOUS_DAY_LOW:")
        assert sig.trade_plan is not None
        assert sig.target_price < sig.entry_price < sig.stop_price

        assert orch.lifecycle == LifecycleState.ENTRY_SUBMITTED
        assert len(ee.submissions) == 1
        assert os_.calls[0]["right"] == "P"


# ═════════════════════════════════════════════════════════════════════════
# 4/5 — no level_source filtering anywhere downstream
# ═════════════════════════════════════════════════════════════════════════

class TestNoLevelSourceFiltering:
    def test_trade_orchestrator_source_mentions_no_level_source(self):
        import inspect
        from trading_lab.live import trade_orchestrator as mod
        src = inspect.getsource(mod)
        for token in ("level_source", "ORB_HIGH", "ORB_LOW",
                      "PREVIOUS_DAY_HIGH", "PREVIOUS_DAY_LOW"):
            assert token not in src, (
                f"trade_orchestrator.py references {token!r} — it must stay "
                f"level-source agnostic")

    def test_trade_plan_builder_mentions_no_level_source(self):
        import inspect
        from trading_lab import trade_plan_builder as mod
        src = inspect.getsource(mod)
        for token in ("level_source", "ORB_HIGH", "ORB_LOW"):
            assert token not in src, (
                f"trade_plan_builder.py references {token!r} — the trade plan "
                f"must not assume an ORB level")


# ═════════════════════════════════════════════════════════════════════════
# 7 — a lone PD signal must survive dedup
# ═════════════════════════════════════════════════════════════════════════

# A confirmation bar shared by two detectors that fired on the SAME
# candle. build_trade_plan() derives entry/stop/target purely from the
# confirmation candle's OHLC plus the config buffers — never from the
# level price — so two signals on one candle, built with one config,
# always agree on prices. That is what makes them mergeable.
_SHARED_BAR = SimpleNamespace(name="confirmation-bar-13")


def _sig(level_source, entry_ms, direction="LONG", entry=102.30,
         stop=101.70, target=103.50, confirmation_bar=_SHARED_BAR):
    detection = (SimpleNamespace(confirmation_bar=confirmation_bar)
                 if confirmation_bar is not None else None)
    return SignalResult(
        status=SignalStatus.SIGNAL, direction=direction,
        entry_price=entry, stop_price=stop, target_price=target,
        entry_timestamp_ms=entry_ms,
        detection_result=detection,
        stage_context={"level_source": level_source},
        setup_key=f"{direction}:{level_source}:1000",
        signal_key=f"{direction}:{level_source}:1000:{entry_ms}")


class TestLonePdSignalSurvivesDedup:
    def test_pd_only_observation_is_returned(self):
        pd = _sig("PREVIOUS_DAY_HIGH", _ms(13))
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=pd)], _ms(13))

        assert len(out) == 1
        assert out[0].signal is pd
        assert out[0].signal.setup_key.startswith("LONG:PREVIOUS_DAY_HIGH:")

    def test_pd_survives_when_orb_is_no_setup(self):
        """A NO_SETUP ORB result must not suppress the PD signal — the
        adapter only forwards SIGNAL observations, but assert the
        primitive itself agrees."""
        pd = _sig("PREVIOUS_DAY_HIGH", _ms(13))
        orb_no_setup = SignalResult(status=SignalStatus.NO_SETUP)
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=orb_no_setup),
             SignalObservation(symbol="QQQ", signal=pd)], _ms(13))

        assert len(out) == 1
        assert out[0].signal is pd

    def test_live_e2e_pd_signal_was_not_deduped_away(self):
        _, ee, _, pending = _run_to_entry("LONG", _long_pdh_bars())
        assert len(pending) == 1 and len(ee.submissions) == 1


# ═════════════════════════════════════════════════════════════════════════
# 8 — ORB and PD on the same Max Entry Candle => ONE trade
# ═════════════════════════════════════════════════════════════════════════

class TestSameEntryCandleSingleOrder:
    def test_orb_and_pd_same_entry_fold_into_one_candidate(self):
        orb = _sig("ORB_HIGH", _ms(13))
        pd = _sig("PREVIOUS_DAY_HIGH", _ms(13))
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=orb),
             SignalObservation(symbol="QQQ", signal=pd)], _ms(13))

        assert len(out) == 1, "same entry candle must yield ONE candidate"
        assert out[0].contributing_level_sources == (
            "ORB_HIGH", "PREVIOUS_DAY_HIGH"), "both sources recorded"

    def test_prices_come_from_the_candle_not_the_level(self):
        """Why the same-candle merge is always safe in the live wiring:
        the trade plan is a function of the confirmation candle and the
        config, so one candle + one config => one set of prices, whether
        the level was ORB_HIGH or PDH."""
        import inspect
        from trading_lab import trade_plan_builder as mod
        src = inspect.getsource(mod.build_trade_plan)
        assert "level_price" not in src
        assert "confirmation_bar" in src

    def test_unverifiable_candle_identity_refuses_to_merge(self):
        """Documented safety behavior, pinned: when the confirmation
        candle cannot be verified the primitive keeps the candidates
        separate rather than blending two trade plans. Unreachable in
        the live wiring (both detectors always attach a real
        DetectionResult), but must not silently change."""
        orb = _sig("ORB_HIGH", _ms(13), confirmation_bar=None)
        pd = _sig("PREVIOUS_DAY_HIGH", _ms(13), confirmation_bar=None)
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=orb),
             SignalObservation(symbol="QQQ", signal=pd)], _ms(13))
        assert len(out) == 2

    def test_disagreeing_prices_refuse_to_merge(self):
        orb = _sig("ORB_HIGH", _ms(13))
        pd = _sig("PREVIOUS_DAY_HIGH", _ms(13), entry=102.99)
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=orb),
             SignalObservation(symbol="QQQ", signal=pd)], _ms(13))
        assert len(out) == 2

    def test_adapter_returns_at_most_one_result_per_evaluation(self):
        """The adapter's own invariant: one direction, one bar => never
        two executable candidates, so the orchestrator can never be
        handed two signals to turn into two orders."""
        runner, sd = _wired_detector("LONG")
        sb = LiveSessionBuilder("QQQ")
        for bar in _long_pdh_bars():
            sb.add_bar(bar)
            result = sd.evaluate(sb.current_session())
            assert isinstance(result, SignalResult)   # exactly one, never a list

    def test_single_order_submitted_over_the_whole_session(self):
        orch, ee, os_, pending = _run_to_entry("LONG", _long_pdh_bars())
        assert len(ee.submissions) == 1, "exactly one order for the session"
        assert len(os_.calls) == 1, "option selected exactly once"

    def test_consumed_setup_blocks_a_second_entry_on_the_same_setup(self):
        orch, ee, _, _ = _run_to_entry("LONG", _long_pdh_bars())
        before = len(ee.submissions)
        # Replay the entry bar: the consumed setup_key must block it.
        orch.on_bar(_c(13, 102.35, 102.45, 101.70, 102.30))
        if orch.has_pending_signal:
            orch.execute_pending_signal()
        assert len(ee.submissions) == before


# ═════════════════════════════════════════════════════════════════════════
# 9 — PD_AUDIT must not interfere with execution
# ═════════════════════════════════════════════════════════════════════════

class TestPdAuditDoesNotInterfere:
    def test_audit_never_touches_the_execution_path(self):
        import inspect
        from trading_lab.live import bot_runner as mod

        src = inspect.getsource(mod.MaxBotRunner._emit_pd_audit)
        for token in ("orchestrator.on_bar", "execute_pending_signal",
                      "_execution_queue", "enqueue", "submit_entry",
                      "_pending_signal"):
            assert token not in src, (
                f"_emit_pd_audit references {token!r} — telemetry must never "
                f"touch execution")

    def test_detector_result_identical_with_audit_running(self):
        """Same bars, same wired detector: results are unchanged by the
        observational audit pass."""
        runner, sd = _wired_detector("LONG")
        sb = LiveSessionBuilder("QQQ")
        for bar in _long_pdh_bars():
            sb.add_bar(bar)
        session = sb.current_session()

        before = sd.evaluate(session)
        rt = runner._runtimes["QQQ"]
        rt.session_builder = sb
        rt.previous_sessions = _prev_sessions()
        runner._update_pdh_pdl_candidate(rt)      # emits PD_AUDIT
        after = sd.evaluate(session)

        keys = ("status", "direction", "entry_price", "stop_price",
                "target_price", "entry_timestamp_ms", "setup_key", "signal_key")
        assert ([getattr(before, k) for k in keys]
                == [getattr(after, k) for k in keys])

    def test_audit_emission_failure_does_not_block_a_trade(self, monkeypatch):
        import trading_lab.live.bot_runner as mod

        def _boom(*a, **k):
            raise RuntimeError("audit exploded")

        monkeypatch.setattr(mod.MaxBotRunner, "_emit_pd_audit", _boom)
        orch, ee, _, pending = _run_to_entry("LONG", _long_pdh_bars())
        assert len(pending) == 1
        assert len(ee.submissions) == 1


# ═════════════════════════════════════════════════════════════════════════
# Guardrail — no 84% Rule / reclaim shipped
# ═════════════════════════════════════════════════════════════════════════

class TestNoReclaimShipped:
    def test_no_reclaim_machinery_in_the_live_pd_path(self):
        import ast
        import inspect
        from trading_lab import pdh_pdl_eligibility
        from trading_lab.live import (
            multi_source_signal_detector_adapter,
            pdh_pdl_candidate_evaluator,
        )
        for mod in (pdh_pdl_eligibility, pdh_pdl_candidate_evaluator,
                    multi_source_signal_detector_adapter):
            # No reclaim entry point exists...
            assert not hasattr(mod, "check_reclaim")
            assert not hasattr(mod, "apply_84_rule")
            # ...and no executable line mentions one (comments and
            # docstrings may, and do, say it is out of scope).
            src = inspect.getsource(mod)
            code = ast.parse(src)
            for node in ast.walk(code):
                if isinstance(node, (ast.Name, ast.Attribute, ast.arg)):
                    label = getattr(node, "id", None) or getattr(
                        node, "attr", None) or getattr(node, "arg", "")
                    assert "reclaim" not in str(label).lower(), (
                        f"{mod.__name__} has a reclaim symbol {label!r}")
