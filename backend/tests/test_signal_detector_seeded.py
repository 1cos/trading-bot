"""Tests for LiveSignalDetector.evaluate_seeded() — additive, not-wired
seeded Stage 4/5 path (micro-task: "PREMARKET_OBSERVED seeded Stage 4/5
in LiveSignalDetector").

Scope: only the case break_origin=PREMARKET_OBSERVED,
reason=DISPLACEMENT_COMPLETE_AWAITING_RETEST, premarket_retest_already_
seen=False — i.e. a real break + real completed displacement already
known, no contact yet, waiting for the first genuine retest (which may
occur after the seed's own candle array, e.g. in RTH).

evaluate_seeded() is a generic BDRR seeding primitive: it accepts the
canonical break_result/displacement_result/level_result envelope shapes
find_break()/find_displacement() already produce, and re-runs ONLY
Stage 4 (find_retest_window) and Stage 5 (find_rejection) — both
existing, unmodified functions — over a caller-supplied `candles` array
(e.g. a premarket + RTH concatenation). It does NOT call find_break(),
find_displacement(), or validate_sequence(), and has no knowledge of
premarket bars, PMH/PML, or any premarket classifier module.

Every scenario here builds break_result/displacement_result by calling
the REAL find_break()/find_displacement() functions against the exact
`combined` candle array passed to evaluate_seeded() — never hand-typed
or fabricated — so indices are guaranteed consistent by construction,
except in S6 where an index mismatch is introduced deliberately.

This test file does NOT import first_rth_contact, first_rth_entry_
candle, premarket_break_classifier, premarket_observed_structure, or
carry_in_separation — evaluate_seeded() is exercised purely with
generic BDRR envelopes, matching its own contract (S8 below verifies
signal_detector.py itself never imports those modules either).
"""

from __future__ import annotations

from trading_lab.break_finder import find_break
from trading_lab.displacement_finder import find_displacement
from trading_lab.tick_arithmetic import price_to_ticks
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.session_builder_live import LiveSessionBuilder


# ── Shared helpers ───────────────────────────────────────────────────────────

LEVEL = 103.00
TICK = 0.01
MS_BASE = 1786449600000  # arbitrary chronological start, well before RTH open

CONFIG = {
    "timeframe_minutes": 1, "timezone": "America/New_York", "session_open": "09:30",
    "orb_start": "session_open", "orb_duration_minutes": 5,
    "level_source": "PREVIOUS_DAY_HIGH", "direction": "LONG", "tick_size": TICK,
    "min_displacement_ticks": None, "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None, "min_displacement_bars": None,
    "consecutive_orb_closes": 2, "rejection_wick_ratio_min": None,
    "body_ratio_max": None, "confirmation_wick_penetration_pct_min": None,
}


def _ms(i: int) -> int:
    return MS_BASE + i * 60_000


def _c(i, o, h, l, cl):
    return {"time_ms": _ms(i), "open": o, "high": h, "low": l, "close": cl, "volume": 1000}


def _level_result(anchor_candle):
    return {
        "status": "OK",
        "date": "2026-08-11",
        "orb_candle_index": 0,
        "orb_candle": anchor_candle,
        "level_price": LEVEL,
        "level_price_ticks": price_to_ticks(LEVEL, TICK),
        "level_source": "PREVIOUS_DAY_HIGH",
    }


def _detector():
    return LiveSignalDetector(
        symbol="QQQ", direction="LONG", tick_size=TICK,
        level_source="PREVIOUS_DAY_HIGH",
    )


# Bars 0-4: "premarket" segment (real break at idx1, 3 valid displacement
# bars at idx2-4, no contact within this segment). Anchor (idx0) mirrors
# the same "compatibility envelope" convention used elsewhere in this
# codebase for premarket structures (anchor = real candle immediately
# preceding the real break, not a claim of a real ORB).
_PREMARKET_SEGMENT = [
    _c(0, 102.40, 102.60, 102.30, 102.50),   # anchor / unbroken
    _c(1, 102.50, 103.60, 102.45, 103.50),   # REAL break (close > 103)
    _c(2, 103.50, 103.80, 103.55, 103.70),   # displacement bar 1
    _c(3, 103.70, 103.95, 103.60, 103.80),   # displacement bar 2
    _c(4, 103.80, 104.00, 103.65, 103.90),   # displacement bar 3, no contact yet
]


def _s1_combined():
    """Premarket segment + RTH continuation + a valid RTH retest/rejection."""
    return _PREMARKET_SEGMENT + [
        _c(5, 103.90, 104.05, 103.85, 104.00),   # RTH continuation, still no contact
        _c(6, 103.10, 103.30, 102.80, 103.20),   # RTH contact + valid LONG rejection
    ]


# ═════════════════════════════════════════════════════════════════════════
# S1 — PREMARKET_OBSERVED -> SIGNAL
# ═════════════════════════════════════════════════════════════════════════

class TestS1PremarketObservedSignal:
    def test_seeded_signal_from_real_premarket_break_and_rth_retest(self):
        combined = _s1_combined()
        level_result = _level_result(combined[0])
        brk = find_break(combined, level_result, CONFIG)
        disp = find_displacement(combined, level_result, brk, CONFIG)
        assert brk["status"] == "OK"
        assert disp["status"] == "OK"

        detector = _detector()
        result = detector.evaluate_seeded(combined, level_result, brk, disp)

        assert result.status == SignalStatus.SIGNAL
        assert result.direction == "LONG"
        # Break identity: the REAL premarket break (idx1), not a
        # synthetic/RTH-invented one.
        expected_setup_key = f"LONG:PREVIOUS_DAY_HIGH:{combined[1]['time_ms']}"
        assert result.setup_key == expected_setup_key
        # Retest/entry happens on the real RTH candle (idx6), after
        # displacement (idx2-4) and continuation (idx5).
        assert result.entry_timestamp_ms == combined[6]["time_ms"]
        assert result.stage_context["level_source"] == "PREVIOUS_DAY_HIGH"

    def test_setup_key_stable_across_repeated_calls(self):
        """Setup identity and statelessness: identical seed -> identical
        result, called twice, no persisted state."""
        combined = _s1_combined()
        level_result = _level_result(combined[0])
        brk = find_break(combined, level_result, CONFIG)
        disp = find_displacement(combined, level_result, brk, CONFIG)

        detector = _detector()
        result1 = detector.evaluate_seeded(combined, level_result, brk, disp)
        result2 = detector.evaluate_seeded(combined, level_result, brk, disp)

        assert result1.status == result2.status == SignalStatus.SIGNAL
        assert result1.setup_key == result2.setup_key
        assert result1.signal_key == result2.signal_key
        assert result1.entry_price == result2.entry_price
        assert result1.stop_price == result2.stop_price


# ═════════════════════════════════════════════════════════════════════════
# S2 — No retest yet
# ═════════════════════════════════════════════════════════════════════════

class TestS2NoRetestYet:
    def test_no_setup_when_no_contact_anywhere_in_seed_array(self):
        """Same premarket structure, but the array ends before any RTH
        bar returns to the level. find_displacement() itself cannot
        report status OK without a contact index — it correctly reports
        RETEST_NOT_FOUND, and evaluate_seeded() surfaces that as-is."""
        combined = list(_PREMARKET_SEGMENT)  # no RTH bars at all
        level_result = _level_result(combined[0])
        brk = find_break(combined, level_result, CONFIG)
        disp = find_displacement(combined, level_result, brk, CONFIG)
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "RETEST_NOT_FOUND"

        detector = _detector()
        result = detector.evaluate_seeded(combined, level_result, brk, disp)

        assert result.status == SignalStatus.NO_SETUP
        assert result.failed_stage == "RETEST_NOT_FOUND"


# ═════════════════════════════════════════════════════════════════════════
# S3 — Contact before displacement completes
# ═════════════════════════════════════════════════════════════════════════

class TestS3RetestBeforeDisplacementComplete:
    def test_premature_contact_cannot_be_chosen_as_retest(self):
        """A candle touching the level right after the break, before
        real displacement separation accumulates, must not produce a
        signal. find_displacement() itself rejects this
        (RETEST_BEFORE_DISPLACEMENT) — evaluate_seeded() never
        second-guesses or re-scans candles on its own; Stage 4 semantics
        come entirely from find_displacement()'s real result."""
        combined = [
            _c(0, 102.40, 102.60, 102.30, 102.50),
            _c(1, 102.50, 103.60, 102.45, 103.50),   # break
            _c(2, 103.50, 103.80, 102.90, 103.20),   # premature contact (low<=103)
            _c(3, 103.20, 103.90, 103.10, 103.80),
            _c(4, 103.80, 104.00, 103.65, 103.90),
        ]
        level_result = _level_result(combined[0])
        brk = find_break(combined, level_result, CONFIG)
        disp = find_displacement(combined, level_result, brk, CONFIG)
        assert disp["status"] == "FAILED"
        assert disp["failed_stage"] == "RETEST_BEFORE_DISPLACEMENT"

        detector = _detector()
        result = detector.evaluate_seeded(combined, level_result, brk, disp)

        assert result.status == SignalStatus.NO_SETUP
        assert result.failed_stage == "RETEST_BEFORE_DISPLACEMENT"


# ═════════════════════════════════════════════════════════════════════════
# S4 — Valid rejection after the real retest (find_rejection() not mocked)
# ═════════════════════════════════════════════════════════════════════════

class TestS4RealRejectionProducesSignal:
    def test_real_find_rejection_computes_genuine_trade_plan(self):
        """Drills into the rejection-specific outputs to demonstrate
        find_rejection() genuinely ran (real geometry, real trade plan)
        rather than being mocked or stubbed."""
        combined = _s1_combined()
        level_result = _level_result(combined[0])
        brk = find_break(combined, level_result, CONFIG)
        disp = find_displacement(combined, level_result, brk, CONFIG)

        detector = _detector()
        result = detector.evaluate_seeded(combined, level_result, brk, disp)

        assert result.status == SignalStatus.SIGNAL
        assert result.detection_result is not None
        assert result.trade_plan is not None
        # Real geometry on the real contact candle (idx6: O103.10 H103.30
        # L102.80 C103.20) -> entry/stop/target from a real TradePlan.
        assert float(result.entry_price) == 103.20
        assert float(result.stop_price) == 102.80
        assert float(result.target_price) == 104.00


# ═════════════════════════════════════════════════════════════════════════
# S5 — Valid retest, invalid rejection geometry
# ═════════════════════════════════════════════════════════════════════════

class TestS5RejectionGeometryFails:
    def test_no_signal_when_contact_candle_fails_geometry(self):
        combined = _PREMARKET_SEGMENT + [
            _c(5, 103.90, 104.05, 103.85, 104.00),
            _c(6, 102.80, 103.60, 102.70, 103.50),   # contact, but weak/thick-body candle
        ]
        level_result = _level_result(combined[0])
        brk = find_break(combined, level_result, CONFIG)
        disp = find_displacement(combined, level_result, brk, CONFIG)
        assert disp["status"] == "OK"   # a real contact IS found this time

        detector = _detector()
        result = detector.evaluate_seeded(combined, level_result, brk, disp)

        assert result.status == SignalStatus.NO_SETUP
        assert result.failed_stage == "NO_QUALIFYING_REJECTION_CANDLE"


# ═════════════════════════════════════════════════════════════════════════
# S6 — Index-space guard
# ═════════════════════════════════════════════════════════════════════════

class TestS6IndexSpaceGuard:
    def test_mismatched_candles_array_fails_safely(self):
        """break_result/displacement_result computed against one array,
        then evaluate_seeded() called with a DIFFERENT candles array
        (indices no longer line up with the real candle timestamps).
        find_retest_window()'s own defensive cross-check must catch
        this — no silent wrong signal, no crash."""
        combined = _s1_combined()
        level_result = _level_result(combined[0])
        brk = find_break(combined, level_result, CONFIG)
        disp = find_displacement(combined, level_result, brk, CONFIG)
        assert disp["status"] == "OK"

        mismatched_candles = combined[1:] + [_c(7, 103.20, 103.40, 103.10, 103.30)]

        detector = _detector()
        result = detector.evaluate_seeded(mismatched_candles, level_result, brk, disp)

        assert result.status == SignalStatus.NO_SETUP
        assert result.failed_stage == "INVALID_INPUT"


# ═════════════════════════════════════════════════════════════════════════
# S7 — Existing evaluate() unchanged
# ═════════════════════════════════════════════════════════════════════════

# evaluate() (unlike evaluate_seeded()) goes through build_session_context,
# which validates real wall-clock alignment to session_open in the
# configured timezone — these fixtures need the real 09:30 ET timestamp
# base already used elsewhere in this test suite (test_signal_detector.py,
# test_pdh_pdl_candidate_evaluator.py), not the seeded path's arbitrary
# MS_BASE above.
MS_0930 = 1786455000000


def _ms_rth(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _c_rth(offset_min, o, h, l, cl):
    return {"time_ms": _ms_rth(offset_min), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


class TestS7ExistingEvaluateUnchanged:
    def test_orb_high_fixture_unchanged(self):
        """ORB_HIGH fixture through the normal evaluate() path — the
        _stage4_5 extraction refactor must not alter this in any way."""
        bars = [
            _c_rth(0, 100.00, 101.00, 99.00, 100.50),
            _c_rth(1, 100.50, 100.80, 100.00, 100.30),
            _c_rth(2, 100.30, 100.70, 99.80, 100.40),
            _c_rth(3, 100.40, 100.90, 100.10, 100.60),
            _c_rth(4, 100.60, 100.95, 100.20, 100.70),
            _c_rth(5, 100.80, 101.60, 100.70, 101.50),
            _c_rth(6, 101.55, 101.80, 101.20, 101.60),
            _c_rth(7, 101.60, 101.90, 101.30, 101.70),
            _c_rth(8, 101.70, 101.85, 101.10, 101.40),
            _c_rth(9, 101.10, 101.30, 100.80, 101.20),
        ]
        sb = LiveSessionBuilder("QQQ")
        for b in bars:
            sb.add_bar(b)
        session = sb.current_session()

        detector = LiveSignalDetector(symbol="QQQ", direction="LONG", tick_size=TICK)
        result = detector.evaluate(session)

        assert result.status == SignalStatus.SIGNAL
        assert result.direction == "LONG"
        assert float(result.entry_price) == 101.20
        assert float(result.stop_price) == 100.80
        assert result.stage_context["level_source"] == "ORB_HIGH"

    def test_pdh_rth_only_fixture_unchanged(self):
        """PDH RTH-only fixture (level_source=PREVIOUS_DAY_HIGH, no
        seeding involved) through the normal evaluate() path — must
        remain byte-identical to before this change."""
        shift = 2.00  # anchors the proven SIGNAL geometry at level=103.00
        orb_bars = [
            _c_rth(0, 100.00, 101.00, 99.00, 100.50),
            _c_rth(1, 100.50, 100.80, 100.00, 100.30),
            _c_rth(2, 100.30, 100.70, 99.80, 100.40),
            _c_rth(3, 100.40, 100.90, 100.10, 100.60),
            _c_rth(4, 100.60, 100.95, 100.20, 100.70),
            _c_rth(5, 100.80, 101.60, 100.70, 101.50),
            _c_rth(6, 101.55, 101.80, 101.20, 101.60),
            _c_rth(7, 101.60, 101.90, 101.30, 101.70),
            _c_rth(8, 101.70, 101.85, 101.10, 101.40),
            _c_rth(9, 101.20, 101.40, 100.90, 101.10),
        ]
        pdh_bars = orb_bars + [
            _c_rth(10, 100.80 + shift, 101.60 + shift, 100.70 + shift, 101.50 + shift),
            _c_rth(11, 101.55 + shift, 101.80 + shift, 101.20 + shift, 101.60 + shift),
            _c_rth(12, 101.60 + shift, 101.90 + shift, 101.30 + shift, 101.70 + shift),
            _c_rth(13, 101.70 + shift, 101.85 + shift, 101.10 + shift, 101.40 + shift),
            _c_rth(14, 101.10 + shift, 101.30 + shift, 100.80 + shift, 101.20 + shift),
        ]
        sb = LiveSessionBuilder("QQQ")
        for b in pdh_bars:
            sb.add_bar(b)
        session = sb.current_session()

        detector = _detector()
        detector.set_previous_sessions([{
            "date": "2026-08-10",
            "candles": [{"time_ms": 1, "open": 100.0, "high": 103.00, "low": 95.0,
                         "close": 100.5, "volume": 500}],
        }])
        result = detector.evaluate(session)

        assert result.status == SignalStatus.SIGNAL
        assert result.direction == "LONG"
        assert float(result.entry_price) == 103.20
        assert float(result.stop_price) == 102.80
        assert result.setup_key == "LONG:PREVIOUS_DAY_HIGH:1786455600000"


# ═════════════════════════════════════════════════════════════════════════
# S8 — No first_rth_contact / premarket module dependency (static guard)
# ═════════════════════════════════════════════════════════════════════════

class TestS8NoForbiddenImports:
    def test_signal_detector_does_not_import_premarket_modules(self):
        import trading_lab.live.signal_detector as mod

        source = open(mod.__file__, encoding="utf-8").read()
        forbidden = [
            "first_rth_contact",
            "first_rth_entry_candle",
            "premarket_break_classifier",
            "premarket_observed_structure",
            "carry_in_separation",
        ]
        for name in forbidden:
            assert name not in source, (
                f"signal_detector.py must not reference {name!r} — "
                f"the seeded path must stay a generic BDRR primitive"
            )
