"""Tests for dedupe_same_entry_signals() — pure same-entry-candle dedup.

Covers D1-D12 exactly as specified. This module is a standalone
primitive: it is not imported by bot_runner.py, trade_orchestrator.py,
pdh_pdl_candidate_evaluator.py, LiveSignalDetector, or any execution
path (D12 is a static guardrail test for this).

D1/D2 use REAL SignalResult objects produced by LiveSignalDetector on
synthetic bars — the same empirical technique used in the prior
same-entry-candle audit — to prove entry/stop/target/confirmation
candle really do come out identical for two independent detectors
sharing an entry_timestamp_ms, not just to exercise the dedup
function's own logic. D3 and onward use hand-built SignalResult /
DetectionResult stand-ins, since those tests are about the dedup
function's behavior, not about re-proving the pipeline produces
identical prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.primitives import PriceTicks
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_dedup import (
    DedupedSignalCandidate,
    SignalObservation,
    collect_actionable_signals,
    dedupe_same_entry_signals,
)
from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)


# ── Real-pipeline fixtures (D1, D2) ──────────────────────────────────────────

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


def _long_same_candle_signals():
    """Real ORB LONG + PDH LONG SignalResults sharing one entry candle.

    ORB breaks first (idx5, level=101.00), PDH breaks later (idx9,
    level=103.00, previous-session high=103.00) — two structurally
    independent BDRR sequences that converge on the identical final
    wide-range candle (idx13) as their Max Entry Candle.
    """
    bars = [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]
    bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))   # ORB break
    bars.append(_c(6, 101.55, 101.90, 101.20, 101.80))   # ORB disp1
    bars.append(_c(7, 101.80, 102.30, 101.50, 102.20))   # ORB disp2 / approach PDH
    bars.append(_c(8, 102.20, 102.70, 101.90, 102.60))   # ORB disp3
    bars.append(_c(9, 102.60, 103.30, 102.40, 103.20))   # PDH break (level=103)
    bars.append(_c(10, 103.20, 103.60, 103.10, 103.50))  # PDH disp1
    bars.append(_c(11, 103.50, 103.80, 103.20, 103.60))  # PDH disp2
    bars.append(_c(12, 103.60, 103.90, 103.15, 103.40))  # PDH disp3
    # Shared retest/rejection candle: wick down through both 101 and
    # 103, close above both.
    bars.append(_c(13, 103.35, 104.00, 100.00, 103.90))

    session = _build_session(bars)

    orb_sd = LiveSignalDetector(symbol="QQQ", direction="LONG", tick_size=0.01,
                                 market_timezone="America/New_York", session_open="09:30")
    orb_result = orb_sd.evaluate(session)

    prev_sessions = [{"date": "2026-08-10",
                       "candles": [{"time_ms": 1, "open": 100.0, "high": 103.00,
                                    "low": 95.0, "close": 101.0, "volume": 500}]}]
    pdh_sd = LiveSignalDetector(symbol="QQQ", direction="LONG", tick_size=0.01,
                                 market_timezone="America/New_York", session_open="09:30",
                                 level_source="PREVIOUS_DAY_HIGH")
    pdh_sd.set_previous_sessions(prev_sessions)
    pdh_result = pdh_sd.evaluate(session)

    assert orb_result.status == SignalStatus.SIGNAL
    assert pdh_result.status == SignalStatus.SIGNAL
    assert orb_result.entry_timestamp_ms == pdh_result.entry_timestamp_ms
    return orb_result, pdh_result


def _short_same_candle_signals():
    """Real ORB SHORT + PDL SHORT SignalResults sharing one entry
    candle — mirror of _long_same_candle_signals()."""
    bars = [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]
    bars.append(_c(5, 99.20, 99.30, 98.40, 98.50))    # ORB break: close < 99
    bars.append(_c(6, 98.45, 98.80, 98.20, 98.30))    # ORB disp1
    bars.append(_c(7, 98.30, 98.70, 97.90, 98.00))    # ORB disp2 / approach PDL
    bars.append(_c(8, 98.00, 98.20, 97.60, 97.70))    # ORB disp3
    bars.append(_c(9, 97.70, 97.90, 96.80, 96.90))    # PDL break (level=97)
    bars.append(_c(10, 96.90, 96.80, 96.60, 96.70))   # PDL disp1
    bars.append(_c(11, 96.70, 96.65, 96.50, 96.60))   # PDL disp2
    bars.append(_c(12, 96.60, 96.55, 96.45, 96.55))   # PDL disp3
    # Shared retest/rejection candle: wick up through both 99 and 97,
    # close below both.
    bars.append(_c(13, 96.60, 100.00, 96.40, 96.60))

    session = _build_session(bars)

    orb_sd = LiveSignalDetector(symbol="QQQ", direction="SHORT", tick_size=0.01,
                                 market_timezone="America/New_York", session_open="09:30")
    orb_result = orb_sd.evaluate(session)

    prev_sessions = [{"date": "2026-08-10",
                       "candles": [{"time_ms": 1, "open": 100.0, "high": 103.00,
                                    "low": 97.00, "close": 99.0, "volume": 500}]}]
    pdl_sd = LiveSignalDetector(symbol="QQQ", direction="SHORT", tick_size=0.01,
                                 market_timezone="America/New_York", session_open="09:30",
                                 level_source="PREVIOUS_DAY_LOW")
    pdl_sd.set_previous_sessions(prev_sessions)
    pdl_result = pdl_sd.evaluate(session)

    assert orb_result.status == SignalStatus.SIGNAL
    assert pdl_result.status == SignalStatus.SIGNAL
    assert orb_result.entry_timestamp_ms == pdl_result.entry_timestamp_ms
    return orb_result, pdl_result


# ── Hand-built fixtures (D3+) ────────────────────────────────────────────────

def _bar(ts, o, h, l, c):
    return Bar(
        bar_utc_ms=ts,
        open=PriceTicks(ticks=int(round(o * 100)), tick_size="0.01"),
        high=PriceTicks(ticks=int(round(h * 100)), tick_size="0.01"),
        low=PriceTicks(ticks=int(round(l * 100)), tick_size="0.01"),
        close=PriceTicks(ticks=int(round(c * 100)), tick_size="0.01"),
        volume=1000,
    )


def _signal(
    *, direction="LONG", entry_ts=1_000, entry=Decimal("101.00"),
    stop=Decimal("100.00"), target=Decimal("103.00"),
    level_source="ORB_HIGH", confirmation_bar=None, setup_key=None,
):
    if confirmation_bar is None:
        confirmation_bar = _bar(entry_ts, 100.50, 101.50, 100.00, 101.20)
    dr = SimpleNamespace(confirmation_bar=confirmation_bar)
    return SignalResult(
        status=SignalStatus.SIGNAL,
        direction=direction,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        entry_timestamp_ms=entry_ts,
        detection_result=dr,
        trade_plan=None,
        setup_key=setup_key or f"{direction}:{level_source}:{entry_ts - 500}",
        signal_key=f"{setup_key or f'{direction}:{level_source}:{entry_ts - 500}'}:{entry_ts}",
        pipeline_stage="SIGNAL",
        stage_context={"level_source": level_source},
    )


def _no_setup(direction="LONG", failed_stage="BREAK_NOT_FOUND"):
    return SignalResult(status=SignalStatus.NO_SETUP, direction=None,
                         failed_stage=failed_stage, pipeline_stage="WAITING")


# ═════════════════════════════════════════════════════════════════════════
# D1 — ORB + PDH same candle LONG
# ═════════════════════════════════════════════════════════════════════════

class TestD1SameCandeLong:
    def test_one_candidate_both_sources(self):
        orb_result, pdh_result = _long_same_candle_signals()
        obs = [
            SignalObservation(symbol="QQQ", signal=orb_result),
            SignalObservation(symbol="QQQ", signal=pdh_result),
        ]
        out = dedupe_same_entry_signals(obs)
        assert len(out) == 1
        cand = out[0]
        assert set(cand.contributing_level_sources) == {"ORB_HIGH", "PREVIOUS_DAY_HIGH"}
        assert cand.signal.entry_price == orb_result.entry_price == pdh_result.entry_price
        assert cand.signal.stop_price == orb_result.stop_price == pdh_result.stop_price
        assert cand.signal.target_price == orb_result.target_price == pdh_result.target_price


# ═════════════════════════════════════════════════════════════════════════
# D2 — ORB + PDL same candle SHORT
# ═════════════════════════════════════════════════════════════════════════

class TestD2SameCandeShort:
    def test_one_candidate_both_sources(self):
        orb_result, pdl_result = _short_same_candle_signals()
        obs = [
            SignalObservation(symbol="QQQ", signal=orb_result),
            SignalObservation(symbol="QQQ", signal=pdl_result),
        ]
        out = dedupe_same_entry_signals(obs)
        assert len(out) == 1
        cand = out[0]
        assert set(cand.contributing_level_sources) == {"ORB_LOW", "PREVIOUS_DAY_LOW"}
        assert cand.signal.entry_price == orb_result.entry_price == pdl_result.entry_price


# ═════════════════════════════════════════════════════════════════════════
# D3 — Same source duplicate
# ═════════════════════════════════════════════════════════════════════════

class TestD3SameSourceDuplicate:
    def test_one_candidate_one_unique_source(self):
        bar = _bar(1_000, 100.50, 101.50, 100.00, 101.20)
        s1 = _signal(entry_ts=1_000, level_source="ORB_HIGH", confirmation_bar=bar,
                      setup_key="LONG:ORB_HIGH:500")
        s2 = _signal(entry_ts=1_000, level_source="ORB_HIGH", confirmation_bar=bar,
                      setup_key="LONG:ORB_HIGH:500")
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=s1),
            SignalObservation(symbol="QQQ", signal=s2),
        ])
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("ORB_HIGH",)


# ═════════════════════════════════════════════════════════════════════════
# D4 — Different entry candles
# ═════════════════════════════════════════════════════════════════════════

class TestD4DifferentEntryCandles:
    def test_two_candidates_no_arbitration(self):
        s1 = _signal(entry_ts=1_000, level_source="ORB_HIGH",
                      confirmation_bar=_bar(1_000, 100.5, 101.5, 100.0, 101.2))
        s2 = _signal(entry_ts=2_000, level_source="PREVIOUS_DAY_HIGH",
                      confirmation_bar=_bar(2_000, 102.5, 103.5, 102.0, 103.2))
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=s1),
            SignalObservation(symbol="QQQ", signal=s2),
        ])
        assert len(out) == 2
        assert {c.signal.entry_timestamp_ms for c in out} == {1_000, 2_000}


# ═════════════════════════════════════════════════════════════════════════
# D5 — Opposite directions, same timestamp
# ═════════════════════════════════════════════════════════════════════════

class TestD5OppositeDirectionsSameTimestamp:
    def test_two_candidates_never_merged(self):
        bar = _bar(1_000, 100.5, 101.5, 100.0, 101.2)
        s_long = _signal(direction="LONG", entry_ts=1_000, level_source="ORB_HIGH",
                          confirmation_bar=bar)
        s_short = _signal(direction="SHORT", entry_ts=1_000, level_source="PREVIOUS_DAY_LOW",
                           confirmation_bar=bar)
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=s_long),
            SignalObservation(symbol="QQQ", signal=s_short),
        ])
        assert len(out) == 2
        assert {c.signal.direction for c in out} == {"LONG", "SHORT"}


# ═════════════════════════════════════════════════════════════════════════
# D6 — Different symbols
# ═════════════════════════════════════════════════════════════════════════

class TestD6DifferentSymbols:
    def test_two_candidates_no_cross_symbol_merge(self):
        bar = _bar(1_000, 100.5, 101.5, 100.0, 101.2)
        s_qqq = _signal(entry_ts=1_000, level_source="ORB_HIGH", confirmation_bar=bar)
        s_nvda = _signal(entry_ts=1_000, level_source="ORB_HIGH", confirmation_bar=bar)
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=s_qqq),
            SignalObservation(symbol="NVDA", signal=s_nvda),
        ])
        assert len(out) == 2


# ═════════════════════════════════════════════════════════════════════════
# D7 — Incompatible trade plans (same direction+timestamp, different
#       entry/stop/target)
# ═════════════════════════════════════════════════════════════════════════

class TestD7IncompatibleTradePlans:
    def test_no_silent_merge_on_price_mismatch(self):
        bar = _bar(1_000, 100.5, 101.5, 100.0, 101.2)
        s1 = _signal(entry_ts=1_000, entry=Decimal("101.00"), stop=Decimal("100.00"),
                      target=Decimal("103.00"), level_source="ORB_HIGH",
                      confirmation_bar=bar)
        s2 = _signal(entry_ts=1_000, entry=Decimal("101.05"), stop=Decimal("100.00"),
                      target=Decimal("103.00"), level_source="PREVIOUS_DAY_HIGH",
                      confirmation_bar=bar)
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=s1),
            SignalObservation(symbol="QQQ", signal=s2),
        ])
        # Defensive fallback: never blend — each member stays its own
        # candidate.
        assert len(out) == 2
        prices = {c.signal.entry_price for c in out}
        assert prices == {Decimal("101.00"), Decimal("101.05")}
        # Each unmerged candidate keeps only its own source.
        assert out[0].contributing_level_sources == ("ORB_HIGH",)
        assert out[1].contributing_level_sources == ("PREVIOUS_DAY_HIGH",)


# ═════════════════════════════════════════════════════════════════════════
# D8 — Confirmation candle mismatch
# ═════════════════════════════════════════════════════════════════════════

class TestD8ConfirmationCandleMismatch:
    def test_no_silent_merge_on_candle_mismatch(self):
        bar_a = _bar(1_000, 100.5, 101.5, 100.0, 101.2)
        bar_b = _bar(1_000, 100.5, 101.5, 99.5, 101.2)  # different low
        s1 = _signal(entry_ts=1_000, level_source="ORB_HIGH", confirmation_bar=bar_a)
        s2 = _signal(entry_ts=1_000, level_source="PREVIOUS_DAY_HIGH", confirmation_bar=bar_b)
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=s1),
            SignalObservation(symbol="QQQ", signal=s2),
        ])
        assert len(out) == 2

    def test_missing_detection_result_never_merges(self):
        s1 = _signal(entry_ts=1_000, level_source="ORB_HIGH")
        s2 = _signal(entry_ts=1_000, level_source="PREVIOUS_DAY_HIGH")
        object.__setattr__(s2, "detection_result", None)
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=s1),
            SignalObservation(symbol="QQQ", signal=s2),
        ])
        assert len(out) == 2


# ═════════════════════════════════════════════════════════════════════════
# D9 — Non-SIGNAL inputs
# ═════════════════════════════════════════════════════════════════════════

class TestD9NonSignalInputs:
    def test_no_setup_filtered_out(self):
        s1 = _signal(entry_ts=1_000)
        no_setup = _no_setup()
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=s1),
            SignalObservation(symbol="QQQ", signal=no_setup),
        ])
        assert len(out) == 1
        assert out[0].signal.status == SignalStatus.SIGNAL

    def test_all_non_signal_yields_empty(self):
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=_no_setup()),
            SignalObservation(symbol="QQQ", signal=_no_setup(direction=None)),
        ])
        assert out == []


# ═════════════════════════════════════════════════════════════════════════
# D10 — Determinism
# ═════════════════════════════════════════════════════════════════════════

class TestD10Determinism:
    def test_same_input_same_output(self):
        orb_result, pdh_result = _long_same_candle_signals()
        obs = [
            SignalObservation(symbol="QQQ", signal=orb_result),
            SignalObservation(symbol="QQQ", signal=pdh_result),
        ]
        out1 = dedupe_same_entry_signals(obs)
        out2 = dedupe_same_entry_signals(obs)
        assert out1 == out2


# ═════════════════════════════════════════════════════════════════════════
# D11 — Source metadata stable (dedup + deterministic order)
# ═════════════════════════════════════════════════════════════════════════

class TestD11SourceMetadataStable:
    def test_sources_deduped_and_ordered(self):
        bar = _bar(1_000, 100.5, 101.5, 100.0, 101.2)
        s_orb = _signal(entry_ts=1_000, level_source="ORB_HIGH", confirmation_bar=bar)
        s_pdh = _signal(entry_ts=1_000, level_source="PREVIOUS_DAY_HIGH", confirmation_bar=bar)
        s_orb_dup = _signal(entry_ts=1_000, level_source="ORB_HIGH", confirmation_bar=bar)
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=s_orb),
            SignalObservation(symbol="QQQ", signal=s_pdh),
            SignalObservation(symbol="QQQ", signal=s_orb_dup),
        ])
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("ORB_HIGH", "PREVIOUS_DAY_HIGH")
        assert len(out[0].contributing_level_sources) == len(set(out[0].contributing_level_sources))


# ═════════════════════════════════════════════════════════════════════════
# D12 — No runtime wiring (static guardrail)
# ═════════════════════════════════════════════════════════════════════════

class TestD12NoRuntimeWiring:
    def test_not_imported_by_bot_runner(self):
        import inspect
        from trading_lab.live import bot_runner
        src = inspect.getsource(bot_runner)
        assert "signal_dedup" not in src

    def test_not_imported_by_trade_orchestrator(self):
        import inspect
        from trading_lab.live import trade_orchestrator
        src = inspect.getsource(trade_orchestrator)
        assert "signal_dedup" not in src

    def test_not_imported_by_candidate_evaluator(self):
        import inspect
        from trading_lab.live import pdh_pdl_candidate_evaluator
        src = inspect.getsource(pdh_pdl_candidate_evaluator)
        assert "signal_dedup" not in src

    def test_not_imported_by_signal_detector(self):
        import inspect
        from trading_lab.live import signal_detector
        src = inspect.getsource(signal_detector)
        assert "signal_dedup" not in src


# ═════════════════════════════════════════════════════════════════════════
# Bonus: canonical signal selection is order-based, documented as
# technical-only (not exercised by the numbered D-tests but supports
# the "no strategic winner" claim in the report).
# ═════════════════════════════════════════════════════════════════════════

class TestCanonicalSelectionIsInputOrder:
    def test_first_member_becomes_canonical(self):
        bar = _bar(1_000, 100.5, 101.5, 100.0, 101.2)
        s_pdh = _signal(entry_ts=1_000, level_source="PREVIOUS_DAY_HIGH", confirmation_bar=bar)
        s_orb = _signal(entry_ts=1_000, level_source="ORB_HIGH", confirmation_bar=bar)
        out = dedupe_same_entry_signals([
            SignalObservation(symbol="QQQ", signal=s_pdh),
            SignalObservation(symbol="QQQ", signal=s_orb),
        ])
        assert len(out) == 1
        # canonical = first in input order = PDH here, purely because
        # it was listed first — reversing the input order would flip
        # this, proving it carries no strategic meaning.
        assert out[0].signal is s_pdh


# ═════════════════════════════════════════════════════════════════════════
# collect_actionable_signals() — current-bar actionable collector (C1-C15)
# ═════════════════════════════════════════════════════════════════════════
#
# C1 single current signal
# C2 old + current
# C3 ORB + PDH same current candle
# C4 opposite directions same current candle
# C5 stale current signal
# C6 consumed setup
# C7 consumed signal
# C8 historical not-current
# C9 future timestamp
# C10 non-SIGNAL
# C11 deterministic output
# C12 inputs/consumed sets unchanged
# C13 existing dedup metadata preserved
# C14 no runtime wiring
# C15 no detector imports/execution


class TestC1SingleCurrentSignal:
    def test_current_signal_becomes_one_candidate(self):
        s = _signal(entry_ts=1_000, level_source="ORB_HIGH")
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=s)],
            current_bar_time_ms=1_000,
        )
        assert len(out) == 1
        assert out[0].signal is s


class TestC2OldPlusCurrent:
    def test_old_filtered_current_survives(self):
        s_old = _signal(entry_ts=1_000, level_source="ORB_HIGH", setup_key="LONG:ORB_HIGH:500")
        s_current = _signal(entry_ts=2_000, level_source="PREVIOUS_DAY_HIGH",
                             setup_key="LONG:PREVIOUS_DAY_HIGH:1500")
        out = collect_actionable_signals(
            [
                SignalObservation(symbol="QQQ", signal=s_old),
                SignalObservation(symbol="QQQ", signal=s_current),
            ],
            current_bar_time_ms=2_000,
        )
        assert len(out) == 1
        assert out[0].signal.entry_timestamp_ms == 2_000
        assert out[0].signal is s_current


class TestC3OrbPdhSameCurrentCandle:
    def test_real_detector_outputs_merge_into_one_candidate(self):
        # Real ORB + real PDH SignalResults from the actual detector
        # pipeline (same fixture used by D1), sharing one entry candle.
        orb_result, pdh_result = _long_same_candle_signals()
        out = collect_actionable_signals(
            [
                SignalObservation(symbol="QQQ", signal=orb_result),
                SignalObservation(symbol="QQQ", signal=pdh_result),
            ],
            current_bar_time_ms=orb_result.entry_timestamp_ms,
        )
        assert len(out) == 1
        assert set(out[0].contributing_level_sources) == {"ORB_HIGH", "PREVIOUS_DAY_HIGH"}


class TestC4OppositeDirectionsSameCurrentBar:
    def test_never_merged_both_survive_as_separate_candidates(self):
        bar = _bar(1_000, 100.5, 101.5, 100.0, 101.2)
        s_long = _signal(direction="LONG", entry_ts=1_000, level_source="ORB_HIGH",
                          confirmation_bar=bar)
        s_short = _signal(direction="SHORT", entry_ts=1_000, level_source="PREVIOUS_DAY_LOW",
                           confirmation_bar=bar)
        out = collect_actionable_signals(
            [
                SignalObservation(symbol="QQQ", signal=s_long),
                SignalObservation(symbol="QQQ", signal=s_short),
            ],
            current_bar_time_ms=1_000,
        )
        assert len(out) == 2
        assert {c.signal.direction for c in out} == {"LONG", "SHORT"}


class TestC5StaleCurrentSignal:
    def test_stale_excludes_even_though_current_bar_condition_holds(self):
        s = _signal(entry_ts=1_000, level_source="ORB_HIGH")
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=s)],
            current_bar_time_ms=1_000,
            live_boundary_ms=1_001,  # boundary just after the entry candle
        )
        assert out == []


class TestC6ConsumedSetup:
    def test_consumed_setup_excluded(self):
        s = _signal(entry_ts=1_000, level_source="ORB_HIGH", setup_key="LONG:ORB_HIGH:500")
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=s)],
            current_bar_time_ms=1_000,
            consumed_setup_keys={"LONG:ORB_HIGH:500"},
        )
        assert out == []


class TestC7ConsumedSignal:
    def test_consumed_signal_excluded(self):
        s = _signal(entry_ts=1_000, level_source="ORB_HIGH", setup_key="LONG:ORB_HIGH:500")
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=s)],
            current_bar_time_ms=1_000,
            consumed_signal_keys={s.signal_key},
        )
        assert out == []


class TestC8HistoricalNotCurrent:
    def test_excluded_and_consumed_sets_not_auto_populated(self):
        s = _signal(entry_ts=1_000, level_source="ORB_HIGH", setup_key="LONG:ORB_HIGH:500")
        consumed_setups = set()
        consumed_signals = set()
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=s)],
            current_bar_time_ms=2_000,  # later than entry_timestamp_ms=1_000
            consumed_setup_keys=consumed_setups,
            consumed_signal_keys=consumed_signals,
        )
        assert out == []
        # Coherent with _check_for_signal(): a non-current historical
        # candidate is excluded, but never auto-added to consumed sets.
        assert consumed_setups == set()
        assert consumed_signals == set()


class TestC9FutureTimestamp:
    def test_future_timestamp_excluded_no_crash(self):
        s = _signal(entry_ts=3_000, level_source="ORB_HIGH")
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=s)],
            current_bar_time_ms=2_000,  # earlier than entry_timestamp_ms=3_000
        )
        assert out == []


class TestC10NonSignal:
    def test_no_setup_never_executable(self):
        out = collect_actionable_signals(
            [SignalObservation(symbol="QQQ", signal=_no_setup())],
            current_bar_time_ms=1_000,
        )
        assert out == []


class TestC11DeterministicOutput:
    def test_same_input_same_output(self):
        s1 = _signal(entry_ts=1_000, level_source="ORB_HIGH")
        s2 = _signal(entry_ts=1_000, level_source="PREVIOUS_DAY_HIGH",
                      confirmation_bar=s1.detection_result.confirmation_bar)
        obs = [
            SignalObservation(symbol="QQQ", signal=s1),
            SignalObservation(symbol="QQQ", signal=s2),
        ]
        out1 = collect_actionable_signals(obs, current_bar_time_ms=1_000)
        out2 = collect_actionable_signals(obs, current_bar_time_ms=1_000)
        assert out1 == out2


class TestC12InputsAndConsumedSetsUnchanged:
    def test_purity(self):
        s1 = _signal(entry_ts=1_000, level_source="ORB_HIGH", setup_key="LONG:ORB_HIGH:500")
        s2 = _signal(entry_ts=2_000, level_source="PREVIOUS_DAY_HIGH",
                      setup_key="LONG:PREVIOUS_DAY_HIGH:1500")
        obs = [
            SignalObservation(symbol="QQQ", signal=s1),
            SignalObservation(symbol="QQQ", signal=s2),
        ]
        obs_snapshot = list(obs)
        consumed_setups = {"SOME:OTHER:KEY"}
        consumed_setups_snapshot = set(consumed_setups)
        consumed_signals = {"SOME:OTHER:SIGNAL"}
        consumed_signals_snapshot = set(consumed_signals)

        collect_actionable_signals(
            obs, current_bar_time_ms=2_000,
            consumed_setup_keys=consumed_setups,
            consumed_signal_keys=consumed_signals,
        )

        assert obs == obs_snapshot
        assert s1.status == SignalStatus.SIGNAL  # untouched dataclass
        assert consumed_setups == consumed_setups_snapshot
        assert consumed_signals == consumed_signals_snapshot


class TestC13ExistingDedupMetadataPreserved:
    def test_contributing_level_sources_survives_the_collector(self):
        orb_result, pdh_result = _long_same_candle_signals()
        out = collect_actionable_signals(
            [
                SignalObservation(symbol="QQQ", signal=orb_result),
                SignalObservation(symbol="QQQ", signal=pdh_result),
            ],
            current_bar_time_ms=orb_result.entry_timestamp_ms,
        )
        assert len(out) == 1
        assert out[0].contributing_level_sources == ("ORB_HIGH", "PREVIOUS_DAY_HIGH")


class TestC14NoRuntimeWiring:
    def test_collector_name_absent_from_orchestrator_and_runner(self):
        import inspect
        from trading_lab.live import bot_runner, trade_orchestrator, pdh_pdl_candidate_evaluator, signal_detector
        for mod in (bot_runner, trade_orchestrator, pdh_pdl_candidate_evaluator, signal_detector):
            src = inspect.getsource(mod)
            assert "collect_actionable_signals" not in src
            assert "signal_dedup" not in src


class TestC15NoDetectorImportsOrExecution:
    def test_module_does_not_import_detector_classes(self):
        # AST-based, not a raw substring search: the module's own
        # docstrings legitimately name LiveSignalDetector/
        # evaluate_pdh_pdl_candidate/DualSignalDetector in prose (to
        # document what this module deliberately does NOT do), so a
        # substring check on full source would false-positive on its
        # own documentation. This checks actual import statements only.
        import ast
        import inspect
        from trading_lab.live import signal_dedup
        src = inspect.getsource(signal_dedup)
        tree = ast.parse(src)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        assert "LiveSignalDetector" not in imported_names
        assert "evaluate_pdh_pdl_candidate" not in imported_names
        assert "DualSignalDetector" not in imported_names
