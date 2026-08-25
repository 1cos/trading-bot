"""T1-T8: PDH/PDL may start its own BDRR in parallel with the ORB one.

Old semantics
-------------
check_orb_to_level_eligibility() gated the candidate level behind
find_displacement() returning status "OK". That function only closes
its displacement window at the FIRST RETEST CONTACT of the ORB level,
so "displacement complete" silently meant "price already came back to
touch ORB_HIGH/ORB_LOW". A clean trend that broke the ORB and ran
straight through PDH without looking back therefore never made PDH
eligible at all — the strongest continuation case was excluded.

New semantics
-------------
The ORB break is the directional gate and nothing more. While the ORB
displacement is still running (find_displacement -> RETEST_NOT_FOUND),
the candidate level is eligible and starts its own independent BDRR.
Both sequences then run side by side on the same directional thesis.

Explicitly NOT part of this change: no reversal (LONG stays on PDH,
SHORT on PDL), and no same-level reclaim after a failed PD sequence
(the future "84% Rule").
"""

from __future__ import annotations

from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.pdh_pdl_candidate_evaluator import evaluate_pdh_pdl_candidate
from trading_lab.live.signal_detector import SignalStatus
from trading_lab.pdh_pdl_eligibility import check_orb_to_level_eligibility
from trading_lab.session_context import build_session_context


MS_0930 = 1786455000000


def _ms(m: int) -> int:
    return MS_0930 + m * 60_000


def _c(m, o, h, l, cl):
    return {"time_ms": _ms(m), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


def _config(direction: str) -> dict:
    return {
        "timeframe_minutes": 1,
        "timezone": "America/New_York",
        "session_open": "09:30",
        "orb_start": "session_open",
        "orb_duration_minutes": 5,
        "level_source": "ORB_HIGH" if direction == "LONG" else "ORB_LOW",
        "direction": direction,
        "tick_size": 0.01,
        "min_displacement_ticks": None,
        "min_displacement_bars": None,
    }


def _check(bars, direction, level_price):
    sc = build_session_context(bars, _config(direction))
    assert sc["status"] == "OK"
    return check_orb_to_level_eligibility(
        sc["candles"], sc, _config(direction), level_price)


def _session(bars, sym="QQQ"):
    sb = LiveSessionBuilder(sym)
    for b in bars:
        sb.add_bar(b)
    return sb.current_session()


def _prev(pdh=200.00, pdl=1.00):
    return [{"date": "2026-08-10", "candles": [
        {"time_ms": 1, "open": 100.0, "high": pdh, "low": pdl,
         "close": 100.0, "volume": 1}]}]


def _evaluate(bars, direction, pdh=200.00, pdl=1.00):
    return evaluate_pdh_pdl_candidate(
        _session(bars), _prev(pdh, pdl), symbol="QQQ", direction=direction,
        tick_size=0.01)


# ORB window idx0-4: high 101.00, low 99.00
def _orb():
    return [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]


PDH = 101.90
PDL = 98.10


def _long_parallel():
    """ORB_HIGH=101.00, PDH=101.90. Break, then a pure run: every bar
    after the break stays strictly above ORB_HIGH, so the ORB retest
    NEVER happens. PDH is broken at idx8 mid-run."""
    return _orb() + [
        _c(5, 100.80, 101.60, 100.70, 101.50),   # ORB BREAK (close > 101.00)
        _c(6, 101.50, 101.80, 101.10, 101.60),   # ORB DISP 1 (low > 101.00)
        _c(7, 101.60, 101.85, 101.15, 101.70),   # ORB DISP 2
        _c(8, 101.70, 102.30, 101.20, 102.20),   # ORB DISP 3 + PDH BREAK
        _c(9, 102.20, 102.60, 102.00, 102.50),   # ORB DISP 4 + PDH DISP 1
        _c(10, 102.50, 102.70, 102.05, 102.40),  # ORB DISP 5 + PDH DISP 2
    ]


def _short_parallel():
    """Mirror: ORB_LOW=99.00, PDL=98.10, no ORB retest ever."""
    return _orb() + [
        _c(5, 100.20, 100.30, 98.40, 98.50),     # ORB BREAK (close < 99.00)
        _c(6, 98.50, 98.90, 98.30, 98.40),       # ORB DISP 1 (high < 99.00)
        _c(7, 98.40, 98.85, 98.25, 98.35),       # ORB DISP 2
        _c(8, 98.35, 98.80, 97.70, 97.80),       # ORB DISP 3 + PDL BREAK
        _c(9, 97.80, 98.00, 97.40, 97.50),       # ORB DISP 4 + PDL DISP 1
        _c(10, 97.50, 97.95, 97.30, 97.60),      # ORB DISP 5 + PDL DISP 2
    ]


# ═════════════════════════════════════════════════════════════════════════
# T1 -- LONG parallel
# ═════════════════════════════════════════════════════════════════════════

class TestT1LongParallel:
    def test_eligible_while_orb_displacement_still_running(self):
        bars = _long_parallel()
        result = _check(bars, "LONG", PDH)

        assert result["eligible"] is True
        assert result["reason"] == "ORB_BREAK_DISPLACEMENT_IN_PROGRESS"
        assert result["orb_high"] == 101.00
        assert result["break_candle_index"] == 5

    def test_eligible_from_the_orb_break_bar_onward(self):
        """No ORB retest anywhere in the run -> eligible at every bar
        from the break on, not just once the run is long."""
        bars = _long_parallel()
        for n in range(6, len(bars) + 1):
            r = _check(bars[:n], "LONG", PDH)
            assert r["eligible"] is True, f"not eligible at {n} bars: {r}"

    def test_pdh_starts_its_own_displacement(self):
        """PDH must not merely be eligible — its own BDRR must be
        running: broken at idx8 and displacing afterwards."""
        out = _evaluate(_long_parallel(), "LONG", pdh=PDH)
        r = out["pdh_pdl_result"]

        assert out["eligibility"]["eligible"] is True
        assert r is not None, "PD detector must be constructed"
        # PDH is broken and price has not returned to it -> the PD
        # sequence is past BREAK and waiting on its own retest.
        assert r.failed_stage == "RETEST_NOT_FOUND"
        assert r.pipeline_stage == "WAITING FOR RETEST"

    def test_pdh_not_yet_broken_is_still_eligible_but_pre_break(self):
        """Eligibility follows the ORB break; the PD sequence itself
        only starts once PD is actually broken."""
        bars = _long_parallel()[:8]      # up to idx7, PDH not broken yet
        out = _evaluate(bars, "LONG", pdh=PDH)

        assert out["eligibility"]["eligible"] is True
        assert out["pdh_pdl_result"].failed_stage == "BREAK_NOT_FOUND"


# ═════════════════════════════════════════════════════════════════════════
# T2 -- SHORT parallel
# ═════════════════════════════════════════════════════════════════════════

class TestT2ShortParallel:
    def test_eligible_while_orb_displacement_still_running(self):
        result = _check(_short_parallel(), "SHORT", PDL)

        assert result["eligible"] is True
        assert result["reason"] == "ORB_BREAK_DISPLACEMENT_IN_PROGRESS"
        assert result["orb_low"] == 99.00
        assert result["break_candle_index"] == 5

    def test_pdl_starts_its_own_displacement(self):
        out = _evaluate(_short_parallel(), "SHORT", pdl=PDL)
        r = out["pdh_pdl_result"]

        assert out["eligibility"]["eligible"] is True
        assert r is not None
        assert r.failed_stage == "RETEST_NOT_FOUND"
        assert r.pipeline_stage == "WAITING FOR RETEST"


# ═════════════════════════════════════════════════════════════════════════
# T3 -- Regression: no ORB break => never a candidate
# ═════════════════════════════════════════════════════════════════════════

class TestT3NoOrbBreak:
    def test_long_no_break(self):
        bars = _orb() + [
            _c(5, 100.70, 100.90, 100.30, 100.60),
            _c(6, 100.60, 100.85, 100.20, 100.50),
        ]
        r = _check(bars, "LONG", PDH)
        assert r["eligible"] is False
        assert r["reason"] == "NO_ORB_BREAK"

    def test_short_no_break(self):
        bars = _orb() + [
            _c(5, 100.20, 100.40, 99.60, 100.00),
            _c(6, 100.00, 100.30, 99.50, 99.80),
        ]
        r = _check(bars, "SHORT", PDL)
        assert r["eligible"] is False
        assert r["reason"] == "NO_ORB_BREAK"

    def test_pd_pipeline_not_evaluated_without_orb_break(self):
        bars = _orb() + [_c(5, 100.70, 100.90, 100.30, 100.60)]
        out = _evaluate(bars, "LONG", pdh=PDH)
        assert out["eligibility"]["eligible"] is False
        assert out["pdh_pdl_result"] is None


# ═════════════════════════════════════════════════════════════════════════
# T4 -- Regression: ORB invalidated => candidate dies
# ═════════════════════════════════════════════════════════════════════════

class TestT4OrbInvalidated:
    def _long_invalidated(self):
        """Run, then price returns through ORB_HIGH and closes back
        inside the ORB band twice consecutively."""
        return _long_parallel() + [
            _c(11, 102.40, 102.50, 100.90, 101.20),  # retest contact
            _c(12, 101.20, 101.30, 100.40, 100.80),  # 1st close inside
            _c(13, 100.80, 101.00, 100.30, 100.60),  # 2nd -> INVALIDATED
        ]

    def test_eligible_flips_to_false_after_invalidation(self):
        before = _check(_long_parallel(), "LONG", PDH)
        assert before["eligible"] is True

        after = _check(self._long_invalidated(), "LONG", PDH)
        assert after["eligible"] is False
        assert after["reason"] == "ORB_STRUCTURE_INVALIDATED"

    def test_pd_pipeline_not_evaluated_once_invalidated(self):
        out = _evaluate(self._long_invalidated(), "LONG", pdh=PDH)
        assert out["eligibility"]["eligible"] is False
        assert out["pdh_pdl_result"] is None

    def test_invalidation_gate_still_applies_after_the_retest(self):
        """The relaxed branch must not leak: once the ORB retest has
        happened the real invalidation gate is back in force."""
        r = _check(self._long_invalidated(), "LONG", PDH)
        assert r["reason"] != "ORB_BREAK_DISPLACEMENT_IN_PROGRESS"


# ═════════════════════════════════════════════════════════════════════════
# T5 -- Coexistence: both sequences valid at once, same direction
# ═════════════════════════════════════════════════════════════════════════

class TestT5Coexistence:
    def test_orb_and_pdh_sequences_alive_simultaneously(self):
        from trading_lab.break_finder import find_break
        from trading_lab.displacement_finder import find_displacement
        from trading_lab.level_provider import build_level

        bars = _long_parallel()
        cfg_orb = _config("LONG")
        sc = build_session_context(bars, cfg_orb)

        orb_lvl = build_level(sc["candles"], sc, cfg_orb)
        orb_brk = find_break(sc["candles"], orb_lvl, cfg_orb)
        orb_disp = find_displacement(sc["candles"], orb_lvl, orb_brk, cfg_orb)

        cfg_pd = {**cfg_orb, "level_source": "PREVIOUS_DAY_HIGH"}
        pd_lvl = build_level(sc["candles"], sc, cfg_pd,
                             all_sessions=_prev(pdh=PDH))
        pd_brk = find_break(sc["candles"], pd_lvl, cfg_pd)

        # Both levels are broken, in the same LONG direction...
        assert orb_brk["status"] == "OK"
        assert pd_brk["status"] == "OK"
        # ...the ORB break strictly precedes the PD break (guaranteed by
        # the geometry gate: PDH > ORB_HIGH)...
        assert orb_brk["break_candle_index"] < pd_brk["break_candle_index"]
        # ...and both are still displacing, neither retested yet.
        assert orb_disp["failed_stage"] == "RETEST_NOT_FOUND"

        out = _evaluate(bars, "LONG", pdh=PDH)
        assert out["eligibility"]["eligible"] is True
        assert out["pdh_pdl_result"].failed_stage == "RETEST_NOT_FOUND"

    def test_two_independent_setup_identities(self):
        """The two sequences must stay distinguishable: different
        level_source in the setup key namespace, never merged."""
        assert _check(_long_parallel(), "LONG", PDH)["eligible"] is True
        out = _evaluate(_long_parallel(), "LONG", pdh=PDH)
        # PD result was produced by a PD-configured detector, so any
        # setup_key it later emits carries PREVIOUS_DAY_HIGH.
        assert out["pdh_pdl_result"] is not None
        assert out["eligibility"]["candidate_level_price"] == PDH


# ═════════════════════════════════════════════════════════════════════════
# T6 -- Pullback through PD without a Max Entry Candle
# ═════════════════════════════════════════════════════════════════════════

class TestT6PullbackThroughPd:
    def _through_pdh_no_entry(self):
        """Price crosses back down through PDH with no qualifying
        rejection candle, but holds above ORB_HIGH (no invalidation)."""
        return _long_parallel() + [
            # straight down through PDH, no wick rejection, closes below
            _c(11, 102.40, 102.45, 101.30, 101.40),
            _c(12, 101.40, 101.50, 101.20, 101.30),
            _c(13, 101.30, 101.45, 101.15, 101.25),
        ]

    def test_no_pd_trade_when_crossed_without_entry_candle(self):
        out = _evaluate(self._through_pdh_no_entry(), "LONG", pdh=PDH)
        r = out["pdh_pdl_result"]
        assert r is not None
        assert r.status != SignalStatus.SIGNAL, "PD must not produce a trade"
        assert r.setup_key is None

    def test_orb_thesis_survives_the_pd_failure(self):
        """PD failing must not kill the ORB structure: closes stayed
        above ORB_HIGH, so the level remains eligible."""
        r = _check(self._through_pdh_no_entry(), "LONG", PDH)
        assert r["eligible"] is True
        assert r["reason"] != "ORB_STRUCTURE_INVALIDATED"


# ═════════════════════════════════════════════════════════════════════════
# T7 -- No reversal
# ═════════════════════════════════════════════════════════════════════════

class TestT7NoReversal:
    def test_long_is_always_built_on_pdh_never_pdl(self):
        out = evaluate_pdh_pdl_candidate(
            _session(_long_parallel()), _prev(pdh=PDH, pdl=PDL),
            symbol="QQQ", direction="LONG", tick_size=0.01)
        # The LONG candidate price is PDH, never PDL.
        assert out["eligibility"]["candidate_level_price"] == PDH

    def test_short_is_always_built_on_pdl_never_pdh(self):
        out = evaluate_pdh_pdl_candidate(
            _session(_short_parallel()), _prev(pdh=PDH, pdl=PDL),
            symbol="QQQ", direction="SHORT", tick_size=0.01)
        assert out["eligibility"]["candidate_level_price"] == PDL

    def test_geometry_gate_blocks_the_reversal_orientation(self):
        """A LONG whose candidate sits BELOW ORB_HIGH (i.e. the level
        the price is falling toward, the reversal orientation) is
        rejected outright — unchanged by this task."""
        r = _check(_long_parallel(), "LONG", 100.50)
        assert r["eligible"] is False
        assert r["reason"] == "WRONG_GEOMETRY"

        r = _check(_short_parallel(), "SHORT", 99.50)
        assert r["eligible"] is False
        assert r["reason"] == "WRONG_GEOMETRY"

    def test_direction_still_gated_to_long_short(self):
        sc = build_session_context(_long_parallel(), _config("LONG"))
        r = check_orb_to_level_eligibility(
            sc["candles"], sc, {**_config("LONG"), "direction": "BOTH"}, PDH)
        assert r["eligible"] is False
        assert r["reason"] == "UNSUPPORTED_DIRECTION"


# ═════════════════════════════════════════════════════════════════════════
# T8 -- No reclaim (explicitly out of scope)
# ═════════════════════════════════════════════════════════════════════════

class TestT8NoReclaim:
    """A failed PD sequence followed by a reclaim of the same level
    must NOT auto-start a second sequence here. That is the future
    "84% Rule / same-level reclaim" feature; this task adds no part of
    it. These tests pin the current, deliberately limited behavior so
    that implementing reclaim later is a visible, intentional change.
    """

    def _fail_then_reclaim(self):
        """PD broken, crossed back down (sequence dead), then price
        closes back above PDH again — a reclaim."""
        return _long_parallel() + [
            _c(11, 102.40, 102.45, 101.30, 101.40),   # down through PDH
            _c(12, 101.40, 101.50, 101.20, 101.30),   # stays below PDH
            _c(13, 101.30, 101.45, 101.15, 101.25),   # stays below PDH
            _c(14, 101.25, 102.10, 101.20, 102.00),   # RECLAIM: close > PDH
            _c(15, 102.00, 102.30, 101.95, 102.20),
        ]

    def test_reclaim_does_not_produce_a_second_pd_signal(self):
        out = _evaluate(self._fail_then_reclaim(), "LONG", pdh=PDH)
        r = out["pdh_pdl_result"]
        assert r is not None
        assert r.status != SignalStatus.SIGNAL
        assert r.setup_key is None

    def test_no_reclaim_logic_exists_in_the_eligibility_module(self):
        """Guardrail: the source must contain no reclaim/84% machinery."""
        import inspect
        import trading_lab.pdh_pdl_eligibility as mod

        src = inspect.getsource(mod)
        body = src[src.index("def check_orb_to_level_eligibility"):]
        for token in ("reclaim", "84", "second_sequence", "re_break"):
            assert token not in body.lower(), (
                f"unexpected reclaim-related token {token!r} in the "
                f"eligibility predicate body")
