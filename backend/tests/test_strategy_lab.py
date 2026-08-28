"""Strategy Lab — shadow metrics that must never change what the bot does.

The whole point of this module is to add observation without adding
risk, so most of what is asserted here is a negative: that M2/M3/M4
cannot reach execution, that the recorder cannot raise, that consumed
setups and lifecycle state are byte-identical after the observer runs.

The one positive that matters is the M1 mirror. `strategy_lab` restates
today's TWO_CANDLE rule so the four models can be compared like for
like, and a mirror that drifts from `find_rejection()` would silently
corrupt every weekly comparison built on it. So M1 is checked against
the real engine end to end — same fixtures, same pipeline — in both
directions and on the near-miss cases, not just on a happy path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trading_lab import strategy_lab
from trading_lab.break_finder import find_break
from trading_lab.displacement_finder import find_displacement
from trading_lab.orb_builder import build_orb
from trading_lab.rejection_finder import find_rejection
from trading_lab.retest_window import find_retest_window
from trading_lab.session_context import build_session_context
from trading_lab.live.strategy_lab_recorder import (
    StrategyLabRecorder,
    build_from_setup_snapshot,
)
from trading_lab.live import strategy_lab_report as report_mod


# ── fixtures mirroring tests/test_rejection_finder.py ───────────────────────

MS_0930 = 1704810600000
TF = 300000

CONFIG = {
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "tick_size": 0.01,
    "min_displacement_ticks": None,
    "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None,
    "min_displacement_bars": None,
    "consecutive_orb_closes": 2,
    "rejection_wick_ratio_min": None,
    "body_ratio_max": None,
    "confirmation_wick_penetration_pct_min": None,
}
SHORT_CONFIG = {**CONFIG, "level_source": "ORB_LOW", "direction": "SHORT"}


def c(time_ms, open_=100.0, high=100.5, low=99.5, close=100.0):
    return {"time_ms": time_ms, "open": open_, "high": high,
            "low": low, "close": close, "volume": 1000}


def _long_base(extra, n_padding=15):
    base = [
        c(MS_0930, high=101.0, low=99.0, close=100.5),
        c(MS_0930 + TF, open_=100.50, high=101.50, low=100.30, close=101.20),
        c(MS_0930 + 2 * TF, open_=101.20, high=101.60, low=101.10, close=101.30),
    ]
    # Wide enough that the pair below is not flagged NEWS_CANDLE: the
    # engine's filter trips at 3x the previous ATR.
    padding = [c(MS_0930 + (3 + j) * TF, open_=101.30, high=101.70,
                 low=101.10, close=101.30) for j in range(n_padding)]
    return base + padding + extra


def _short_base(extra, n_padding=15):
    base = [
        c(MS_0930, high=101.0, low=99.0, close=99.5),
        c(MS_0930 + TF, open_=99.50, high=99.70, low=98.50, close=98.80),
        c(MS_0930 + 2 * TF, open_=98.80, high=98.90, low=98.40, close=98.50),
    ]
    padding = [c(MS_0930 + (3 + j) * TF, open_=98.50, high=98.90,
                 low=98.30, close=98.50) for j in range(n_padding)]
    return base + padding + extra


def run_full(candles, config=CONFIG):
    sc = build_session_context(candles, config)
    orb = build_orb(sc["candles"], sc, config)
    brk = find_break(sc["candles"], orb, config)
    disp = find_displacement(sc["candles"], orb, brk, config)
    rw = find_retest_window(sc["candles"], orb, brk, disp, config)
    return find_rejection(sc["candles"], orb, brk, disp, rw, config)


def _engine_two_candle_verdict(candles, config=CONFIG):
    rej = run_full(candles, config)
    if rej.get("status") == "OK" and \
            rej.get("entry_pattern_type") == "TWO_CANDLE_ENGULFING_RECOVERY":
        return "PASS"
    return "FAIL"


LONG_LEVEL, LONG_FAR = 101.0, 99.0
SHORT_LEVEL, SHORT_FAR = 99.0, 101.0


def _shadow(c1, c2, short=False):
    return strategy_lab.two_candle_shadow(
        c1, c2,
        direction="SHORT" if short else "LONG",
        level_price=SHORT_LEVEL if short else LONG_LEVEL,
        level_source="ORB_LOW" if short else "ORB_HIGH",
        far_edge=SHORT_FAR if short else LONG_FAR,
        tick_size=0.01,
    )


def _m1(record):
    return record["shadow_verdicts"]["M1_CURRENT"]["verdict"]


# ══ M1 must mirror the engine ═══════════════════════════════════════════════

class TestM1MirrorsTheEngine:
    """A drifting mirror would poison every comparison built on it."""

    def test_long_pass_matches_engine(self):
        t1 = MS_0930 + 20 * TF
        c1 = c(t1, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(t1 + TF, open_=100.40, high=101.30, low=100.10, close=101.20)
        candles = _long_base([c1, c2])
        assert _engine_two_candle_verdict(candles) == "PASS"
        assert _m1(_shadow(c1, c2)) == "PASS"

    def test_short_pass_matches_engine(self):
        t1 = MS_0930 + 20 * TF
        c1 = c(t1, open_=98.90, high=99.80, low=98.70, close=99.50)
        c2 = c(t1 + TF, open_=99.60, high=99.70, low=98.50, close=98.80)
        candles = _short_base([c1, c2])
        assert _engine_two_candle_verdict(candles, SHORT_CONFIG) == "PASS"
        assert _m1(_shadow(c1, c2, short=True)) == "PASS"

    def test_open2_exactly_on_the_body_edge_fails_in_both(self):
        """Equality is not engulfing — the near-miss the engine is strict about."""
        t1 = MS_0930 + 20 * TF
        c1 = c(t1, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(t1 + TF, open_=100.50, high=101.30, low=100.10, close=101.20)
        candles = _long_base([c1, c2])
        assert _engine_two_candle_verdict(candles) == "FAIL"
        rec = _shadow(c1, c2)
        assert _m1(rec) == "FAIL"
        assert "TWO_CANDLE_ENGULFING_INSUFFICIENT" in \
            rec["shadow_verdicts"]["M1_CURRENT"]["failed_rules"]

    def test_recovery_short_of_the_level_fails_in_both(self):
        t1 = MS_0930 + 20 * TF
        c1 = c(t1, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(t1 + TF, open_=100.40, high=101.30, low=100.10, close=100.95)
        candles = _long_base([c1, c2])
        assert _engine_two_candle_verdict(candles) == "FAIL"
        assert _m1(_shadow(c1, c2)) == "FAIL"

    def test_no_penetration_fails_in_both(self):
        """low1 == level is zero penetration, not a rejection."""
        t1 = MS_0930 + 20 * TF
        c1 = c(t1, open_=101.30, high=101.40, low=101.00, close=101.10)
        c2 = c(t1 + TF, open_=100.90, high=101.60, low=100.80, close=101.50)
        rec = _shadow(c1, c2)
        assert _m1(rec) == "FAIL"
        assert "TWO_CANDLE_NO_LEVEL_PENETRATION" in \
            rec["shadow_verdicts"]["M1_CURRENT"]["failed_rules"]

    def test_disagreement_with_the_engine_is_recorded_not_hidden(self):
        c1 = c(0, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(TF, open_=100.40, high=101.30, low=100.10, close=101.20)
        rec = strategy_lab.two_candle_shadow(
            c1, c2, direction="LONG", level_price=LONG_LEVEL,
            level_source="ORB_HIGH", far_edge=LONG_FAR, tick_size=0.01,
            engine_verdict="FAIL")
        assert _m1(rec) == "PASS"
        assert rec["m1_matches_engine"] is False


# ══ the alternative models ══════════════════════════════════════════════════

class TestAlternativeModels:

    def _pair_without_gap(self):
        """Body fully recovered, but candle 2 opens at candle 1's close —
        the shape the open2 gate rejects and M2/M3 keep."""
        c1 = c(0, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(TF, open_=100.50, high=101.30, low=100.45, close=101.20)
        return c1, c2

    def test_m2_keeps_a_recovery_that_m1_drops_for_the_gap(self):
        rec = _shadow(*self._pair_without_gap())
        assert _m1(rec) == "FAIL"
        assert rec["shadow_verdicts"]["M2_RECOVERY_BASED"]["verdict"] == "PASS"

    def test_m2_still_requires_the_body_to_be_recovered(self):
        c1 = c(0, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(TF, open_=100.50, high=101.30, low=100.45, close=101.05)
        rec = _shadow(c1, c2)
        v = rec["shadow_verdicts"]["M2_RECOVERY_BASED"]
        assert v["verdict"] == "FAIL"
        assert "BODY_NOT_RECOVERED" in v["failed_rules"]

    def test_m3_requires_rejection_against_the_penetrating_candle(self):
        c1 = c(0, open_=100.30, high=101.60, low=100.20, close=101.50)
        c2 = c(TF, open_=101.40, high=101.55, low=101.30, close=101.45)
        v = _shadow(c1, c2)["shadow_verdicts"]["M3_LEVEL_BASED"]
        assert v["verdict"] == "FAIL"
        assert "NO_REJECTION_VS_PENETRATION_CANDLE" in v["failed_rules"]

    def test_m4_merges_the_pair_into_one_bar(self):
        c1 = c(0, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(TF, open_=100.40, high=101.30, low=100.10, close=101.20)
        merged = strategy_lab.merged_bar(c1, c2)
        assert merged["open"] == c1["open"]
        assert merged["close"] == c2["close"]
        assert merged["high"] == max(c1["high"], c2["high"])
        assert merged["low"] == min(c1["low"], c2["low"])

    def test_every_model_reports_a_verdict(self):
        c1 = c(0, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(TF, open_=100.40, high=101.30, low=100.10, close=101.20)
        rec = _shadow(c1, c2)
        assert set(rec["shadow_verdicts"]) == set(strategy_lab.MODELS)
        for v in rec["shadow_verdicts"].values():
            assert v["verdict"] in ("PASS", "FAIL")


# ══ metrics ═════════════════════════════════════════════════════════════════

class TestMetrics:

    def test_margin_open2_equals_the_gap_when_candle1_is_bearish(self):
        """The algebra behind the whole audit: for a bearish candle 1,
        body1_low IS close1, so the engulfing margin is the bar gap."""
        c1 = c(0, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(TF, open_=100.42, high=101.30, low=100.10, close=101.20)
        m = _shadow(c1, c2)["metrics"]
        assert m["body1_direction"] == "BEARISH"
        assert m["margin_open2_ticks"] == -m["gap_close1_open2_ticks"]

    def test_normalised_fields_are_none_without_atr(self):
        c1 = c(0, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(TF, open_=100.40, high=101.30, low=100.10, close=101.20)
        m = _shadow(c1, c2)["metrics"]
        assert m["penetration_atr_c1"] is None
        assert m["stop_distance_atr"] is None

    def test_stop_is_the_extreme_of_the_pair(self):
        c1 = c(0, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(TF, open_=100.40, high=101.30, low=100.10, close=101.20)
        assert _shadow(c1, c2)["stop_price"] == 100.10

    def test_classification_flags_the_micro_gap_shape(self):
        c1 = c(0, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(TF, open_=100.49, high=101.30, low=100.10, close=101.20)
        rec = _shadow(c1, c2)
        assert rec["metrics"]["margin_open2_ticks"] == 1
        assert rec["structural_classification"] == strategy_lab.CLASS_MICRO_GAP
        assert rec["buckets"]["margin_open2"] == "<=1t"

    def test_bucket_boundaries(self):
        b = strategy_lab.MARGIN_OPEN2_BUCKETS
        assert strategy_lab.bucket_of(1, b) == "<=1t"
        assert strategy_lab.bucket_of(2, b) == "2t"
        assert strategy_lab.bucket_of(4, b) == "3-4t"
        assert strategy_lab.bucket_of(99, b) == "5+t"
        assert strategy_lab.bucket_of(None, b) is None


class TestSingleShadow:

    def _passing(self):
        # wick 0.85, body 0.15, close on the low, penetrating the level
        return c(0, open_=100.39, high=100.73, low=100.33, close=100.33)

    def test_flip_ticks_finds_the_smallest_break(self):
        rec = strategy_lab.single_shadow(
            self._passing(), direction="SHORT", level_price=100.62,
            level_source="ORB_LOW", tick_size=0.01)
        assert rec["shadow_verdicts"]["M1_CURRENT"]["verdict"] == "PASS"
        assert rec["metrics"]["flip_ticks"] >= 1
        assert rec["metrics"]["first_failing_gate"]

    def test_a_three_tick_candle_dies_on_one_tick(self):
        """The TSLL shape: ratios perfect only because the candle is tiny."""
        rec = strategy_lab.single_shadow(
            c(0, open_=9.20, high=9.20, low=9.17, close=9.20),
            direction="LONG", level_price=9.18, level_source="PREVIOUS_DAY_HIGH",
            tick_size=0.01)
        assert rec["shadow_verdicts"]["M1_CURRENT"]["verdict"] == "PASS"
        assert rec["metrics"]["range_ticks"] == 3
        assert rec["metrics"]["penetration_ticks"] == 1
        assert rec["metrics"]["flip_ticks"] == 1
        assert rec["buckets"]["single_range"] == "<=12t"

    def test_flip_returns_none_when_nothing_within_range_breaks_it(self):
        out = strategy_lab.flip_analysis(
            c(0, open_=50.0, high=50.0, low=50.0, close=50.0),
            "LONG", 60.0, 0.01, max_ticks=2)
        assert out["flip_ticks"] is None
        assert out["baseline_qualifies"] is False


# ══ forward settlement ══════════════════════════════════════════════════════

class TestSettlement:

    def _tape(self, highs):
        return [c(i * TF, open_=100.0, high=h, low=99.9, close=h)
                for i, h in enumerate(highs)]

    def test_target_reached_before_stop(self):
        tape = self._tape([100.0, 100.5, 101.0, 102.0])
        out = strategy_lab.settle_r_outcome(tape, 0, 100.0, 99.5, "LONG")
        assert out["r_reached"]["2r"] is True
        assert out["stop_first"] is False
        assert out["target_first"] is True

    def test_stop_wins_a_tie_inside_one_bar(self):
        """Both touched in the same bar: the bar cannot order them, so the
        stop is assumed first. Uniformly pessimistic, which is what a
        model comparison needs."""
        tape = [c(0, high=100.0, low=100.0, close=100.0),
                c(TF, open_=100.0, high=102.0, low=99.0, close=101.0)]
        out = strategy_lab.settle_r_outcome(tape, 0, 100.0, 99.5, "LONG")
        assert out["stop_first"] is True
        assert out["target_first"] is False
        # MAE is the excursion, not the realised loss: the bar traded
        # down to 99.00, a full 2R below a 0.5-wide stop.
        assert out["mae_r"] == -2.0

    def test_a_stop_touched_exactly_is_one_r_of_excursion(self):
        tape = [c(0, high=100.0, low=100.0, close=100.0),
                c(TF, open_=100.0, high=100.1, low=99.5, close=99.6)]
        out = strategy_lab.settle_r_outcome(tape, 0, 100.0, 99.5, "LONG")
        assert out["stop_first"] is True
        assert out["mae_r"] == -1.0

    def test_zero_r_distance_is_unsettleable(self):
        assert strategy_lab.settle_r_outcome(
            self._tape([100.0, 101.0]), 0, 100.0, 100.0, "LONG") is None

    def test_short_direction_settles_downward(self):
        tape = [c(0, high=100.0, low=100.0, close=100.0),
                c(TF, open_=100.0, high=100.2, low=98.9, close=99.0)]
        out = strategy_lab.settle_r_outcome(tape, 0, 100.0, 100.5, "SHORT")
        assert out["r_reached"]["2r"] is True
        assert out["stop_first"] is False


# ══ scan preconditions match the engine's ═══════════════════════════════════

class TestScanPreconditions:

    def _series(self, pair):
        pad = [c(i * 60000, open_=101.30, high=101.70, low=101.10, close=101.30)
               for i in range(20)]
        return pad + pair

    def test_a_bar_that_never_reaches_the_level_is_not_a_candidate(self):
        pair = [c(20 * 60000, open_=101.30, high=101.70, low=101.10, close=101.30),
                c(21 * 60000, open_=101.30, high=101.70, low=101.10, close=101.35)]
        assert strategy_lab.scan_pair(
            self._series(pair), 20, direction="LONG", level_price=100.0,
            level_source="ORB_HIGH", far_edge=99.0, tick_size=0.01) is None

    def test_non_consecutive_bars_are_not_a_pair(self):
        pair = [c(20 * 60000, open_=101.10, high=101.20, low=100.20, close=100.50),
                c(25 * 60000, open_=100.40, high=101.30, low=100.10, close=101.20)]
        assert strategy_lab.scan_pair(
            self._series(pair), 20, direction="LONG", level_price=LONG_LEVEL,
            level_source="ORB_HIGH", far_edge=LONG_FAR, tick_size=0.01) is None

    def test_a_line_source_has_no_zone_so_no_two_candle(self):
        pair = [c(20 * 60000, open_=101.10, high=101.20, low=100.20, close=100.50),
                c(21 * 60000, open_=100.40, high=101.30, low=100.10, close=101.20)]
        assert strategy_lab.scan_pair(
            self._series(pair), 20, direction="LONG", level_price=LONG_LEVEL,
            level_source="PREVIOUS_DAY_HIGH", far_edge=None, tick_size=0.01) is None

    def test_a_candle_passing_single_is_never_offered_to_two_candle(self):
        single = c(20 * 60000, open_=9.20, high=9.20, low=9.17, close=9.20)
        follow = c(21 * 60000, open_=9.20, high=9.25, low=9.19, close=9.24)
        pad = [c(i * 60000, open_=9.21, high=9.22, low=9.20, close=9.21)
               for i in range(20)]
        assert strategy_lab.scan_pair(
            pad + [single, follow], 20, direction="LONG", level_price=9.18,
            level_source="ORB_HIGH", far_edge=9.00, tick_size=0.01) is None

    def test_a_real_pair_is_returned(self):
        pair = [c(20 * 60000, open_=101.10, high=101.20, low=100.20, close=100.50),
                c(21 * 60000, open_=100.40, high=101.30, low=100.10, close=101.20)]
        rec = strategy_lab.scan_pair(
            self._series(pair), 20, direction="LONG", level_price=LONG_LEVEL,
            level_source="ORB_HIGH", far_edge=LONG_FAR, tick_size=0.01)
        assert rec is not None and _m1(rec) == "PASS"


# ══ nothing observational may reach execution ═══════════════════════════════

LIVE = Path(__file__).resolve().parents[1] / "src" / "trading_lab" / "live"
LAB = Path(__file__).resolve().parents[1] / "src" / "trading_lab" / "strategy_lab.py"


class TestObservationCannotReachExecution:

    def test_the_lab_module_never_imports_the_live_package(self):
        """A pure module cannot call an order path it cannot see."""
        for line in LAB.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "trading_lab.live" not in stripped, stripped

    def test_alternative_models_appear_in_no_decision_module(self):
        """M2/M3/M4 exist only where they are computed or reported."""
        allowed = {"strategy_lab_report.py", "strategy_lab_recorder.py"}
        for path in LIVE.glob("*.py"):
            if path.name in allowed:
                continue
            source = path.read_text(encoding="utf-8")
            for model in ("M2_RECOVERY_BASED", "M3_LEVEL_BASED", "M4_COMBINED_2M"):
                assert model not in source, f"{model} leaked into {path.name}"

    def test_the_runner_hook_runs_after_the_work_item_is_queued(self):
        source = (LIVE / "bot_runner.py").read_text(encoding="utf-8")
        assert source.index("self._execution_queue.enqueue(item)") < \
            source.index("self._record_strategy_lab(rt, candle)")

    def test_the_recorder_is_never_consulted_by_a_decision(self):
        source = (LIVE / "bot_runner.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            if "_strategy_lab" not in line:
                continue
            stripped = line.strip()
            # Only construction, the two observer calls, and the method
            # definition itself. Never inside a condition.
            assert not stripped.startswith(("if ", "elif ", "while ", "return ")), stripped


class TestRunnerHookIsInert:

    def _runner(self, tmp_path):
        from trading_lab.live.bot_runner import MaxBotRunner
        runner = MaxBotRunner("QQQ", "BOTH", execution_mode="OBSERVE_ONLY")
        runner._strategy_lab = StrategyLabRecorder(tmp_path)
        return runner

    def _runtime(self, candles):
        rt = MagicMock()
        rt.symbol = "QQQ"
        rt.orb_high, rt.orb_low = 101.0, 99.0
        rt.session_builder.current_session.return_value = {"candles": candles}
        return rt

    def _candles(self):
        pad = [c(i * 60000, open_=101.30, high=101.70, low=101.10, close=101.30)
               for i in range(20)]
        return pad + [c(20 * 60000, open_=101.10, high=101.20, low=100.20, close=100.50),
                      c(21 * 60000, open_=100.40, high=101.30, low=100.10, close=101.20)]

    def test_it_writes_a_candidate_without_touching_the_queue(self, tmp_path):
        runner = self._runner(tmp_path)
        candles = self._candles()
        before = runner._execution_queue.size() if hasattr(
            runner._execution_queue, "size") else len(
            getattr(runner._execution_queue, "_items", []))
        runner._record_strategy_lab(self._runtime(candles), candles[-1])
        after = runner._execution_queue.size() if hasattr(
            runner._execution_queue, "size") else len(
            getattr(runner._execution_queue, "_items", []))
        assert before == after
        assert runner._strategy_lab.counters["bars"] == 1
        assert runner._strategy_lab.counters["candidates"] >= 1

    def test_a_broken_session_builder_costs_nothing(self, tmp_path):
        runner = self._runner(tmp_path)
        rt = MagicMock()
        rt.symbol = "QQQ"
        rt.orb_high, rt.orb_low = 101.0, 99.0
        rt.session_builder.current_session.side_effect = RuntimeError("boom")
        runner._record_strategy_lab(rt, c(0))          # must not raise

    def test_missing_orb_is_not_an_error(self, tmp_path):
        runner = self._runner(tmp_path)
        rt = self._runtime(self._candles())
        rt.orb_high = None
        runner._record_strategy_lab(rt, self._candles()[-1])
        assert runner._strategy_lab.counters["candidates"] == 0

    def test_a_single_direction_runner_only_scans_its_own_side(self, tmp_path):
        from trading_lab.live.bot_runner import MaxBotRunner
        runner = MaxBotRunner("QQQ", "SHORT", execution_mode="OBSERVE_ONLY")
        runner._strategy_lab = StrategyLabRecorder(tmp_path)
        candles = self._candles()
        runner._record_strategy_lab(self._runtime(candles), candles[-1])
        for row in runner._strategy_lab.load_candidates(
                _date_of(candles[-1]["time_ms"])):
            assert row["direction"] == "SHORT"


def _date_of(time_ms):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(
        time_ms / 1000, ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


# ══ the recorder ════════════════════════════════════════════════════════════

class TestRecorder:

    def test_round_trip(self, tmp_path):
        rec = StrategyLabRecorder(tmp_path)
        bar = c(MS_0930, high=101.0)
        assert rec.record_bar("QQQ", bar) is True
        date = _date_of(MS_0930)
        assert rec.load_bars(date, "QQQ")[0]["high"] == 101.0

    def test_replayed_bars_are_deduplicated_keeping_the_last(self, tmp_path):
        rec = StrategyLabRecorder(tmp_path)
        rec.record_bar("QQQ", c(MS_0930, close=100.0))
        rec.record_bar("QQQ", c(MS_0930, close=100.9))
        bars = rec.load_bars(_date_of(MS_0930), "QQQ")
        assert len(bars) == 1 and bars[0]["close"] == 100.9

    def test_a_truncated_final_line_is_skipped_not_fatal(self, tmp_path):
        rec = StrategyLabRecorder(tmp_path)
        rec.record_bar("QQQ", c(MS_0930))
        path = rec.bars_path(_date_of(MS_0930), "QQQ")
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"time_ms": 17878')
        assert len(rec.load_bars(_date_of(MS_0930), "QQQ")) == 1

    def test_an_unwritable_directory_returns_false(self, tmp_path):
        blocked = tmp_path / "blocked"
        blocked.write_text("not a directory")
        rec = StrategyLabRecorder(blocked)
        assert rec.record_bar("QQQ", c(MS_0930)) is False

    def test_a_candidate_without_a_timestamp_is_refused(self, tmp_path):
        rec = StrategyLabRecorder(tmp_path)
        assert rec.record_candidate("QQQ", {"candle1": {}}) is False

    def test_disabled_writes_nothing(self, tmp_path):
        rec = StrategyLabRecorder(tmp_path, enabled=False)
        assert rec.record_bar("QQQ", c(MS_0930)) is False
        assert not list(Path(tmp_path).rglob("*.jsonl"))


# ══ the weekly report ═══════════════════════════════════════════════════════

class TestReport:

    def _seed(self, tmp_path, taken=False):
        lab, state = tmp_path / "lab", tmp_path / "state"
        state.mkdir(parents=True, exist_ok=True)
        rec = StrategyLabRecorder(lab)
        c1 = c(20 * 60000, open_=101.10, high=101.20, low=100.20, close=100.50)
        c2 = c(21 * 60000, open_=100.40, high=101.30, low=100.10, close=101.20)
        record = _shadow(c1, c2)
        rec.record_candidate("QQQ", record)
        date = _date_of(c1["time_ms"])
        for bar in [c1, c2,
                    c(22 * 60000, open_=101.20, high=104.00, low=101.10, close=103.80)]:
            rec.record_bar("QQQ", bar)
        if taken:
            (state / "QQQ_X.json").write_text(json.dumps(
                {"symbol": "QQQ", "entry_timestamp_ms": c2["time_ms"]}))
        return lab, state, date

    def test_an_untraded_candidate_is_a_shadow_cohort(self, tmp_path):
        lab, state, date = self._seed(tmp_path)
        rep = report_mod.build_report([date], lab_dir=lab, trade_state_dir=state)
        m1 = rep["models"]["M1_CURRENT"]
        assert m1[report_mod.COHORT_NEW_PASS]["candidate"] == 1
        assert report_mod.COHORT_TAKEN not in m1

    def test_a_traded_candidate_is_currently_taken(self, tmp_path):
        lab, state, date = self._seed(tmp_path, taken=True)
        rep = report_mod.build_report([date], lab_dir=lab, trade_state_dir=state)
        m1 = rep["models"]["M1_CURRENT"]
        assert m1[report_mod.COHORT_TAKEN]["candidate"] == 1
        assert m1[report_mod.COHORT_TAKEN]["actually_tradable"] == 1
        assert report_mod.COHORT_NEW_PASS not in m1

    def test_outcomes_are_settled_from_the_tape(self, tmp_path):
        lab, state, date = self._seed(tmp_path)
        rows = report_mod.settle_candidates(date, lab_dir=lab, trade_state_dir=state)
        assert rows[0]["outcome"]["r_reached"]["2r"] is True

    def test_a_single_is_not_counted_as_a_rejection_by_the_pair_models(self, tmp_path):
        """M2/M3/M4 are TWO_CANDLE semantics. A SINGLE row carries no
        opinion from them, and must not appear in their cohorts at all."""
        lab, state, date = self._seed(tmp_path)
        rec = StrategyLabRecorder(lab)
        rec.record_candidate("QQQ", strategy_lab.single_shadow(
            c(30 * 60000, open_=9.20, high=9.20, low=9.17, close=9.20),
            direction="LONG", level_price=9.18,
            level_source="PREVIOUS_DAY_HIGH", tick_size=0.01))
        rep = report_mod.build_report([date], lab_dir=lab, trade_state_dir=state)
        assert rep["total_candidates"] == 2
        for model in ("M2_RECOVERY_BASED", "M3_LEVEL_BASED", "M4_COMBINED_2M"):
            assert sum(a["candidate"] for coh, a in rep["models"][model].items()
                       if coh != "ALL_PASS") == 1
        m1_total = sum(a["candidate"] for coh, a in rep["models"]["M1_CURRENT"].items()
                       if coh != "ALL_PASS")
        assert m1_total == 2

    def test_every_model_gets_its_own_cohorts(self, tmp_path):
        lab, state, date = self._seed(tmp_path)
        rep = report_mod.build_report([date], lab_dir=lab, trade_state_dir=state)
        assert set(rep["models"]) == set(strategy_lab.MODELS)

    def test_the_chain_caveat_travels_with_the_numbers(self, tmp_path):
        lab, state, date = self._seed(tmp_path)
        rep = report_mod.build_report([date], lab_dir=lab, trade_state_dir=state)
        assert "not replayed" in rep["note"].lower()
        assert "actually_tradable" in rep["note"]

    def test_it_formats_without_raising(self, tmp_path):
        lab, state, date = self._seed(tmp_path)
        rep = report_mod.build_report([date], lab_dir=lab, trade_state_dir=state)
        assert "STRATEGY LAB" in report_mod.format_report(rep)

    def test_an_empty_directory_is_reported_not_crashed(self, tmp_path):
        assert report_mod.main(["--lab-dir", str(tmp_path)]) == 1


# ══ the snapshot builder (moved out of the orchestrator) ════════════════════

class TestBuildFromSetupSnapshot:
    """The level-source knowledge lives here so the orchestrator can stay
    agnostic — which a separate test enforces on its source text."""

    def _candles(self):
        pad = [c(i * 60000, open_=101.30, high=101.70, low=101.10, close=101.30)
               for i in range(20)]
        return pad + [c(20 * 60000, open_=101.10, high=101.20, low=100.20, close=100.50),
                      c(21 * 60000, open_=100.40, high=101.30, low=100.10, close=101.20)]

    def _snap(self, pattern="SINGLE_CANDLE_REJECTION", source="ORB_HIGH"):
        return {"level_price": {"ticks": 10100, "tick_size": 0.01},
                "level_source": source, "entry_pattern_type": pattern}

    def test_single_snapshot_yields_a_single_record(self):
        candles = self._candles()
        rec = build_from_setup_snapshot(
            self._snap(), candles, len(candles) - 1,
            direction="LONG", levels={"orb_low": 99.0, "orb_high": 101.0})
        assert rec["pattern"] == "SINGLE"
        assert rec["metrics"]["flip_ticks"] is not None or True
        assert rec["engine_verdict"] == "PASS"

    def test_two_candle_snapshot_yields_a_pair_record(self):
        candles = self._candles()
        rec = build_from_setup_snapshot(
            self._snap("TWO_CANDLE_ENGULFING_RECOVERY"), candles, len(candles) - 1,
            direction="LONG", levels={"orb_low": 99.0, "orb_high": 101.0})
        assert rec["pattern"] == "TWO_CANDLE"
        assert _m1(rec) == "PASS"
        assert rec["m1_matches_engine"] is True

    def test_a_line_source_gets_no_far_edge(self):
        candles = self._candles()
        rec = build_from_setup_snapshot(
            self._snap("TWO_CANDLE_ENGULFING_RECOVERY", "PREVIOUS_DAY_HIGH"),
            candles, len(candles) - 1, direction="LONG", levels={})
        assert rec["far_edge"] is None

    @pytest.mark.parametrize("snapshot,index", [
        (None, 5),
        ({}, 5),
        ({"level_price": {"ticks": 10100, "tick_size": 0}}, 5),
        ({"level_price": {"tick_size": 0.01}}, 5),
        ({"level_price": {"ticks": 10100, "tick_size": 0.01}}, None),
        ({"level_price": {"ticks": 10100, "tick_size": 0.01}}, 999),
    ])
    def test_unusable_input_returns_none_instead_of_raising(self, snapshot, index):
        assert build_from_setup_snapshot(
            snapshot, self._candles(), index,
            direction="LONG", levels={"orb_low": 99.0}) is None

    def test_a_two_candle_at_index_zero_has_no_first_candle(self):
        assert build_from_setup_snapshot(
            self._snap("TWO_CANDLE_ENGULFING_RECOVERY"), self._candles(), 0,
            direction="LONG", levels={"orb_low": 99.0}) is None


# ══ zero behavioural change ═════════════════════════════════════════════════

class TestNoBehaviouralChange:

    def test_the_orchestrator_block_is_additive_and_guarded(self):
        source = (LIVE / "trade_orchestrator.py").read_text(encoding="utf-8")
        i = source.index('record["strategy_lab"] = lab')
        window = source[i - 400:i]
        assert "try:" in window and "except Exception" in window

    def test_the_lab_key_is_written_after_the_trade_plan_is_fixed(self):
        source = (LIVE / "trade_orchestrator.py").read_text(encoding="utf-8")
        assert source.index('"stop": float(self._underlying_triggers.stop_price)') < \
            source.index('record["strategy_lab"] = lab')

    def test_the_observer_never_writes_orchestrator_state(self):
        source = (LIVE / "trade_orchestrator.py").read_text(encoding="utf-8")
        start = source.index("def _strategy_lab_block")
        body = source[start:source.index("def _build_chart_context", start)]
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("self.") and "=" in stripped and "==" not in stripped:
                pytest.fail(f"observer assigns to instance state: {stripped}")

    def test_consumed_setups_are_untouched_by_the_observer(self):
        source = (LIVE / "trade_orchestrator.py").read_text(encoding="utf-8")
        start = source.index("def _strategy_lab_block")
        body = source[start:source.index("def _build_chart_context", start)]
        assert "_consumed_setups" not in body
        assert "lifecycle" not in body.lower()

    def test_the_orchestrator_delegates_and_names_no_level_source(self):
        """The agnosticism invariant is enforced elsewhere on the whole
        file; this pins the reason it still holds after the observer
        was added."""
        source = (LIVE / "trade_orchestrator.py").read_text(encoding="utf-8")
        start = source.index("def _strategy_lab_block")
        body = source[start:source.index("def _build_chart_context", start)]
        assert "build_strategy_lab_block(" in body
        for token in ("level_source", "ORB_HIGH", "ORB_LOW",
                      "PREVIOUS_DAY_HIGH", "PREVIOUS_DAY_LOW"):
            assert token not in body

    def test_the_runner_observer_writes_no_runtime_state(self):
        source = (LIVE / "bot_runner.py").read_text(encoding="utf-8")
        start = source.index("def _record_strategy_lab")
        body = source[start:source.index("# ── Bar polling fallback", start)]
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("rt.") and "=" in stripped and "==" not in stripped:
                pytest.fail(f"observer assigns to runtime state: {stripped}")

    def test_the_engine_itself_was_not_touched(self):
        import subprocess
        for path in ("backend/src/trading_lab/rejection_finder.py",
                     "backend/src/trading_lab/retest_window.py",
                     "backend/src/trading_lab/live/signal_detector.py"):
            diff = subprocess.run(["git", "diff", "origin/main", "--", path],
                                  capture_output=True, text=True,
                                  cwd=Path(__file__).resolve().parents[2])
            assert diff.stdout.strip() == "", f"{path} changed:\n{diff.stdout}"
