"""Detector scan cursor — the detector must not go blind after a
consumed / stale / historical setup (2026-08-25 session audit).

Demonstrated root cause
-----------------------
``LiveSignalDetector.evaluate()`` restarts its scan-skip loop from
``skip_before = 0`` on EVERY call, and the loop is bounded by
``range(10)``. Every already-archived break in the session's history is
therefore re-derived and re-skipped from scratch on every single bar.
Once more than ~10 archived breaks accumulate, the 10-attempt budget is
spent entirely on history and the detector can never look at anything
newer — for the rest of the session.

Two distinct freeze mechanisms were observed live on 2026-08-25:

  1. Budget exhaustion on dead breaks (AMD LONG frozen from 11:08 ET,
     SOFI LONG frozen from 09:49 ET — both for the rest of the day).
  2. A historical SIGNAL that the orchestrator rejects as
     SIGNAL_NOT_CURRENT but never consumes, so the detector re-derives
     that same setup on every bar and its scan cursor never advances
     (AAPL SHORT, from 10:09 ET onwards).

Both are the same missing semantic: "a break that has already been
archived must stay archived across calls". The cap itself (10) is
correct and is NOT changed by these tests or by the fix.

Fixture note
------------
The SHORT geometry below is the canonical LONG fixture from
test_signal_detector.py reflected around the level, exactly as
test_t12_gap_b_restart_consumed.py already does — so the rejection
geometry (wick ratio / body ratio / favorable close location / wick
penetration) is the same already-validated geometry, unchanged.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading_lab.live.dual_signal_detector import DualSignalDetector
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator


MS_0930 = 1786455000000
SHORT_LEVEL = 99.00          # ORB_LOW of the fixture below
LONG_LEVEL = 101.00          # ORB_HIGH of the fixture below


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


# ── Canonical LONG building blocks (from test_signal_detector.py) ────────────

_ORB = [
    {"open": 100.00, "high": 101.00, "low": 99.00, "close": 100.50},
    {"open": 100.50, "high": 100.80, "low": 100.00, "close": 100.30},
    {"open": 100.30, "high": 100.70, "low": 99.80, "close": 100.40},
    {"open": 100.40, "high": 100.90, "low": 100.10, "close": 100.60},
    {"open": 100.60, "high": 100.95, "low": 100.20, "close": 100.70},
]
_BREAK = {"open": 100.80, "high": 101.60, "low": 100.70, "close": 101.50}
_DISP = [
    {"open": 101.55, "high": 101.80, "low": 101.20, "close": 101.60},
    {"open": 101.60, "high": 101.90, "low": 101.30, "close": 101.70},
    {"open": 101.70, "high": 101.85, "low": 101.10, "close": 101.40},
]
_REJ = {"open": 101.10, "high": 101.30, "low": 100.80, "close": 101.20}


def _mirror(bar: dict, t: int) -> dict:
    """Reflect a LONG bar around the level into its SHORT equivalent."""
    def m(x: float) -> float:
        return round(SHORT_LEVEL - (x - LONG_LEVEL), 4)

    return {
        "time_ms": t,
        "open": m(bar["open"]),
        "high": m(bar["low"]),
        "low": m(bar["high"]),
        "close": m(bar["close"]),
        "volume": bar.get("volume", 1000),
    }


def _at(bar: dict, t: int) -> dict:
    return {**bar, "time_ms": t, "volume": bar.get("volume", 1000)}


def _short_orb(start: int = 0) -> list[dict]:
    return [_mirror(b, _ms(start + i)) for i, b in enumerate(_ORB)]


def _short_sequence(start: int) -> list[dict]:
    """A complete SHORT break -> displacement -> retest -> rejection
    sequence occupying 5 bars, beginning at bar index ``start``."""
    bars = [_mirror(_BREAK, _ms(start))]
    bars += [_mirror(b, _ms(start + 1 + i)) for i, b in enumerate(_DISP)]
    bars.append(_mirror(_REJ, _ms(start + 4)))
    return bars


def _short_dead_break(start: int) -> list[dict]:
    """Two bars forming a break that is archived immediately:
    the first post-break bar already contacts the level, so
    find_displacement() returns RETEST_BEFORE_DISPLACEMENT — one of the
    verdicts evaluate()'s loop already treats as terminal."""
    return [
        {"time_ms": _ms(start), "open": 99.20, "high": 99.30,
         "low": 98.60, "close": 98.70, "volume": 1000},
        {"time_ms": _ms(start + 1), "open": 98.70, "high": 100.50,
         "low": 98.65, "close": 100.20, "volume": 1000},
    ]


def _session_from(bars: list[dict], symbol: str = "SPY") -> dict:
    sb = LiveSessionBuilder(symbol, "America/New_York")
    for b in bars:
        sb.add_bar(b)
    return sb.current_session()


def _counting_detector(direction: str = "SHORT"):
    """A real detector whose _evaluate_inner calls are counted, so the
    10-attempt cap can be asserted as still being respected."""
    sd = LiveSignalDetector(symbol="SPY", direction=direction, tick_size=0.01)
    calls: list[int] = []
    inner = sd._evaluate_inner

    def counted(session, skip_before=0):
        calls.append(skip_before)
        return inner(session, skip_before=skip_before)

    sd._evaluate_inner = counted
    return sd, calls


def _make_orchestrator(direction: str = "SHORT"):
    sb = LiveSessionBuilder("SPY", "America/New_York")
    sd = LiveSignalDetector(
        symbol="SPY", direction=direction, tick_size=0.01,
        market_timezone="America/New_York", session_open="09:30",
    )
    orch = MaxBotTradeOrchestrator(
        underlying_symbol="SPY", direction=direction, tick_size=0.01,
        session_builder=sb, signal_detector=sd,
        trade_manager=DailyTradeManager(),
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(),
    )
    return orch, sb, sd


# ═════════════════════════════════════════════════════════════════════
# T1 — old dead breaks must not exhaust the attempt cap
# ═════════════════════════════════════════════════════════════════════

_N_DEAD = 12          # deliberately > the 10-attempt cap


def _t1_bars() -> list[dict]:
    bars = _short_orb()
    idx = 5
    for _ in range(_N_DEAD):
        bars += _short_dead_break(idx)
        idx += 2
    bars += _short_sequence(idx)
    return bars


class TestT1DeadBreaksDoNotExhaustCap:
    def test_fixture_really_contains_more_dead_breaks_than_the_cap(self):
        """Guard: the fixture must actually reproduce the pathological
        condition, otherwise the rest of T1 would pass vacuously."""
        assert _N_DEAD > 10

    def test_cap_stays_at_ten(self):
        """The fix must NOT widen the safety cap."""
        import inspect
        src = inspect.getsource(LiveSignalDetector.evaluate)
        assert "range(10)" in src, "the 10-attempt safety cap must stay 10"

    def test_new_setup_is_found_despite_twelve_archived_breaks(self):
        bars = _t1_bars()
        sd, _calls = _counting_detector("SHORT")

        result = None
        for i in range(1, len(bars) + 1):
            result = sd.evaluate(_session_from(bars[:i]))

        assert result.status == SignalStatus.SIGNAL, (
            f"detector never reached the final valid setup — it is still "
            f"stuck on archived history (stage={result.failed_stage})"
        )
        assert result.entry_timestamp_ms == bars[-1]["time_ms"]

    def test_no_single_evaluate_exceeds_the_cap(self):
        """Persisting the cursor must reduce work per call, never
        exceed the existing budget."""
        bars = _t1_bars()
        sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        inner = sd._evaluate_inner
        per_call: list[int] = []

        for i in range(1, len(bars) + 1):
            n = [0]

            def counted(session, skip_before=0, _n=n):
                _n[0] += 1
                return inner(session, skip_before=skip_before)

            sd._evaluate_inner = counted
            sd.evaluate(_session_from(bars[:i]))
            per_call.append(n[0])

        assert max(per_call) <= 10, f"cap exceeded: {max(per_call)}"


# ═════════════════════════════════════════════════════════════════════
# T2 — SIGNAL_NOT_CURRENT must not freeze the detector
# ═════════════════════════════════════════════════════════════════════
#
# Mirrors the real AAPL SHORT case of 2026-08-25: setup A's entry candle
# is already in the past when the orchestrator first looks at it, so the
# edge-trigger gate rejects it as SIGNAL_NOT_CURRENT. Pre-fix that
# verdict consumed nothing, so the detector re-derived A on every later
# bar and never advanced to B.


def _t2_bars() -> list[dict]:
    bars = _short_orb()
    bars += _short_sequence(5)        # idx5-9   -> SIGNAL A, entry at idx9
    # idx10: ONE bounce back inside the ORB (never two consecutive), so
    # A is never invalidated by validate_sequence() — this isolates the
    # NOT_CURRENT mechanism from the SEQUENCE_INVALIDATED one.
    bars.append({"time_ms": _ms(10), "open": 98.8, "high": 99.6,
                 "low": 98.7, "close": 99.3, "volume": 1000})
    bars += _short_sequence(11)       # idx11-15 -> SIGNAL B, entry at idx15
    return bars


class TestT2SignalNotCurrentDoesNotFreeze:
    def test_historical_signal_is_rejected_as_not_current(self):
        """Pre-condition: A really does take the NOT_CURRENT branch —
        not the stale-boundary branch, which is a different mechanism."""
        bars = _t2_bars()
        orch, sb, _sd = _make_orchestrator("SHORT")
        for b in bars[:10]:           # idx0-9 bootstrapped, on_bar never ran
            sb.add_bar(b)
        assert orch._live_boundary_ms == 0, "no restart boundary in this scenario"

        orch.on_bar(bars[10])
        assert not orch.has_pending_signal, (
            "a historical entry candle must never be executed on a later bar"
        )

    def test_not_current_setup_is_archived(self):
        bars = _t2_bars()
        orch, sb, _sd = _make_orchestrator("SHORT")
        for b in bars[:10]:
            sb.add_bar(b)
        orch.on_bar(bars[10])

        setup_a = f"SHORT:ORB_LOW:{bars[5]['time_ms']}"
        assert setup_a in orch._consumed_setups, (
            "a SIGNAL rejected as NOT_CURRENT is regenerated on every "
            "subsequent bar unless its setup_key is archived"
        )

    def test_a_breaks_entry_candle_never_changes(self):
        """The premise the NOT_CURRENT archiving rests on.

        Archiving a non-current setup only forfeits a later entry on
        THAT SAME break. This asserts there is nothing to forfeit: once
        a break has produced an entry candle, more bars never move it,
        so a candle that is historical now is historical forever.
        """
        bars = _t2_bars()
        sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        setup_a = f"SHORT:ORB_LOW:{bars[5]['time_ms']}"

        seen: set[int] = set()
        for i in range(10, len(bars) + 1):
            r = sd.evaluate(_session_from(bars[:i]))
            if r.status == SignalStatus.SIGNAL and r.setup_key == setup_a:
                seen.add(r.entry_timestamp_ms)

        assert seen, "setup A never produced a signal — fixture is vacuous"
        assert seen == {bars[9]["time_ms"]}, (
            f"setup A's entry candle moved across calls: {sorted(seen)}"
        )

    def test_archiving_is_scoped_to_the_replayed_key_only(self):
        bars = _t2_bars()
        orch, sb, _sd = _make_orchestrator("SHORT")
        for b in bars[:10]:
            sb.add_bar(b)
        orch.on_bar(bars[10])

        setup_a = f"SHORT:ORB_LOW:{bars[5]['time_ms']}"
        assert orch._consumed_setups == {setup_a}, (
            "a non-current replay must archive its own key and nothing else"
        )

    def test_later_distinct_setup_is_still_reached(self):
        bars = _t2_bars()
        orch, sb, _sd = _make_orchestrator("SHORT")
        for b in bars[:10]:
            sb.add_bar(b)
        for b in bars[10:]:
            orch.on_bar(b)

        setup_a = f"SHORT:ORB_LOW:{bars[5]['time_ms']}"
        setup_b = f"SHORT:ORB_LOW:{bars[11]['time_ms']}"
        assert orch.has_pending_signal, (
            "detector never reached setup B — it is stuck re-deriving "
            "the historical setup A on every bar"
        )
        assert orch._pending_signal.setup_key == setup_b
        assert orch._pending_signal.setup_key != setup_a


# ═════════════════════════════════════════════════════════════════════
# T3 — a consumed trade must not block a later distinct setup
# ═════════════════════════════════════════════════════════════════════


class TestT3ConsumedTradeThenNewSetup:
    def test_new_sequence_same_direction_is_found(self):
        bars = _t2_bars()
        orch, sb, _sd = _make_orchestrator("SHORT")

        for b in bars:
            orch.on_bar(b)
            if orch.has_pending_signal:
                # Simulate the trade being taken and later closed: the
                # setup stays consumed, the lifecycle returns to
                # WAITING_FOR_SIGNAL, exactly as after a real exit.
                orch._pending_signal = None
        first_key = f"SHORT:ORB_LOW:{bars[5]['time_ms']}"
        second_key = f"SHORT:ORB_LOW:{bars[11]['time_ms']}"

        assert first_key in orch._consumed_setups
        assert second_key in orch._consumed_setups, (
            "the second, structurally distinct setup was never reached"
        )

    def test_detector_level_consumed_skip_still_works(self):
        """The pre-existing consumed-setup skip must be untouched."""
        bars = _t2_bars()
        sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        session = _session_from(bars)
        setup_a = f"SHORT:ORB_LOW:{bars[5]['time_ms']}"

        result = sd.evaluate(session, consumed_setup_keys={setup_a})
        assert result.status == SignalStatus.SIGNAL
        assert result.setup_key == f"SHORT:ORB_LOW:{bars[11]['time_ms']}"


# ═════════════════════════════════════════════════════════════════════
# T4 — dead break -> valid later break (pre-existing semantics)
# ═════════════════════════════════════════════════════════════════════


def _t4_bars() -> list[dict]:
    """One genuinely SEQUENCE_INVALIDATED break, then a valid sequence."""
    bars = _short_orb()
    # idx5: break, then TWO consecutive closes back inside the ORB ->
    # validate_sequence() invalidates it (consecutive_orb_closes = 2).
    bars.append(_mirror(_BREAK, _ms(5)))
    bars.append({"time_ms": _ms(6), "open": 98.8, "high": 99.8,
                 "low": 98.7, "close": 99.4, "volume": 1000})
    bars.append({"time_ms": _ms(7), "open": 99.4, "high": 99.9,
                 "low": 99.1, "close": 99.6, "volume": 1000})
    bars += _short_sequence(8)     # idx8-12 -> valid SIGNAL
    return bars


class TestT4DeadBreakThenValidBreak:
    def test_valid_break_after_dead_break_is_found(self):
        bars = _t4_bars()
        sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        result = None
        for i in range(1, len(bars) + 1):
            result = sd.evaluate(_session_from(bars[:i]))

        assert result.status == SignalStatus.SIGNAL
        assert result.setup_key == f"SHORT:ORB_LOW:{bars[8]['time_ms']}"
        assert result.entry_timestamp_ms == bars[-1]["time_ms"]

    def test_dead_break_never_becomes_the_accepted_setup(self):
        bars = _t4_bars()
        sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        dead_key = f"SHORT:ORB_LOW:{bars[5]['time_ms']}"
        for i in range(1, len(bars) + 1):
            r = sd.evaluate(_session_from(bars[:i]))
            if r.status == SignalStatus.SIGNAL:
                assert r.setup_key != dead_key


# ═════════════════════════════════════════════════════════════════════
# T5 — no cross-direction interference
# ═════════════════════════════════════════════════════════════════════


class TestT5DirectionFlip:
    def test_short_archived_history_does_not_blind_long(self):
        """A SHORT side saturated with archived breaks must not affect
        the LONG side: the cursor is per-detector, and setup_key already
        carries the direction."""
        bars = _t1_bars()                      # 12 archived SHORT breaks
        long_start = 5 + 2 * _N_DEAD + 5
        # A valid LONG sequence appended after everything else.
        bars += [_at(_BREAK, _ms(long_start))]
        bars += [_at(b, _ms(long_start + 1 + i)) for i, b in enumerate(_DISP)]
        bars += [_at(_REJ, _ms(long_start + 4))]

        long_sd = LiveSignalDetector(symbol="SPY", direction="LONG", tick_size=0.01)
        short_sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        dual = DualSignalDetector(long_sd, short_sd)

        result = None
        for i in range(1, len(bars) + 1):
            result = dual.evaluate(_session_from(bars[:i]))

        assert result.status == SignalStatus.SIGNAL
        assert result.direction == "LONG"
        assert result.setup_key.startswith("LONG:ORB_HIGH:")

    def test_cursors_are_independent_objects(self):
        long_sd = LiveSignalDetector(symbol="SPY", direction="LONG", tick_size=0.01)
        short_sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        bars = _t1_bars()
        for i in range(1, len(bars) + 1):
            short_sd.evaluate(_session_from(bars[:i]))

        assert short_sd._archived_before_index > 0, "SHORT cursor should have advanced"
        assert long_sd._archived_before_index == 0, "LONG cursor must be untouched"


# ═════════════════════════════════════════════════════════════════════
# T6 — restart / session-rollover safety
# ═════════════════════════════════════════════════════════════════════


class TestT6RestartAndStaleSafety:
    def test_fresh_detector_starts_from_zero(self):
        sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        assert sd._archived_before_index == 0

    def test_cursor_resets_on_a_new_session_date(self):
        bars = _t1_bars()
        sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        for i in range(1, len(bars) + 1):
            sd.evaluate(_session_from(bars[:i]))
        assert sd._archived_before_index > 0

        # Next trading day: same detector object, different session date.
        next_day = [{**b, "time_ms": b["time_ms"] + 86_400_000} for b in _short_orb()]
        sd.evaluate(_session_from(next_day))
        assert sd._archived_before_index == 0, (
            "a cursor from a previous session must never carry over"
        )

    def test_session_without_a_date_never_persists_a_cursor(self):
        """Degrades to the pre-fix behaviour rather than trusting an
        unidentifiable session."""
        sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        sd._archived_before_index = 7
        sd.evaluate({"candles": []})
        assert sd._archived_before_index == 0

    def test_stale_boundary_branch_is_unchanged(self):
        """The pre-existing SIGNAL_STALE archiving must still happen."""
        bars = _t2_bars()
        orch, sb, _sd = _make_orchestrator("SHORT")
        for b in bars[:11]:
            sb.add_bar(b)
        orch._live_boundary_ms = bars[11]["time_ms"]
        for b in bars[11:]:
            orch.on_bar(b)

        assert orch.has_pending_signal
        assert orch._pending_signal.setup_key == f"SHORT:ORB_LOW:{bars[11]['time_ms']}"


# ═════════════════════════════════════════════════════════════════════
# T7 — a setup is never emitted twice
# ═════════════════════════════════════════════════════════════════════


class TestT7NoDuplicateSignal:
    def test_same_setup_accepted_at_most_once(self):
        bars = _t2_bars()
        orch, sb, _sd = _make_orchestrator("SHORT")
        accepted: list[str] = []

        for b in bars:
            orch.on_bar(b)
            if orch.has_pending_signal:
                accepted.append(orch._pending_signal.signal_key)
                orch._pending_signal = None

        assert len(accepted) == len(set(accepted)), (
            f"the same signal was accepted more than once: {accepted}"
        )

    def test_detector_does_not_re_emit_a_consumed_setup(self):
        bars = _t2_bars()
        sd = LiveSignalDetector(symbol="SPY", direction="SHORT", tick_size=0.01)
        consumed: set[str] = set()
        emitted: list[str] = []

        for i in range(1, len(bars) + 1):
            r = sd.evaluate(_session_from(bars[:i]), consumed_setup_keys=consumed)
            if r.status == SignalStatus.SIGNAL and r.entry_timestamp_ms == bars[i - 1]["time_ms"]:
                emitted.append(r.setup_key)
                consumed.add(r.setup_key)

        assert len(emitted) == len(set(emitted))
