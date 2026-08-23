"""Tests for evaluate_pdh_pdl_candidate() — micro-task 14.

Connects, for the first time, check_orb_to_level_eligibility() to a
real LiveSignalDetector configured with level_source=PREVIOUS_DAY_HIGH/
PREVIOUS_DAY_LOW, proving the full BDRR pipeline (break -> displacement
-> retest -> SINGLE_CANDLE_REJECTION) works end-to-end on PDH/PDL.

This is detection/wiring only:
    - no order is created
    - no orchestrator is touched
    - no IBKR call happens
    - the ORB-only live path is completely unaffected

Cases covered (exactly as specified):
    P1 PDH not eligible (ORB displacement incomplete) -> pipeline NOT evaluated
    P2 PDH eligible                                   -> pipeline evaluated
    P3 PDH full BDRR                                  -> SIGNAL, PDH identity
    P4 PDL full BDRR                                  -> SIGNAL, PDL identity
    P5 eligibility invalidated after being True        -> pipeline NOT evaluated again
    P6 ORB behavior unaffected                         -> identical with/without this module
"""

from __future__ import annotations

from trading_lab.live.pdh_pdl_candidate_evaluator import evaluate_pdh_pdl_candidate
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus


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


def _prev_sessions(pdh=None, pdl=None):
    """A single previous session whose high/low equal the given PDH/PDL."""
    return [{
        "date": "2026-08-10",
        "candles": [{
            "time_ms": 1, "open": 100.0,
            "high": pdh if pdh is not None else 105.0,
            "low": pdl if pdl is not None else 95.0,
            "close": 100.5, "volume": 500,
        }],
    }]


def _orb_bars():
    """5 ORB bars (idx0-4): ORB high=101.00, low=99.00."""
    return [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]


# ═════════════════════════════════════════════════════════════════════════
# LONG / PDH fixtures
# ═════════════════════════════════════════════════════════════════════════

def _orb_long_eligible_bars():
    """ORB + LONG break (idx5) + 3 valid displacement bars (idx6-8)
    + ORB retest-contact (idx9, low <= 101.00) -> ORB displacement
    complete -> eligible (given PDH > 101.00), but no PDH-specific
    structure yet."""
    bars = _orb_bars()
    bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))   # ORB break
    bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))   # ORB disp 1/3
    bars.append(_c(7, 101.60, 101.90, 101.30, 101.70))   # ORB disp 2/3
    bars.append(_c(8, 101.70, 101.85, 101.10, 101.40))   # ORB disp 3/3
    bars.append(_c(9, 101.20, 101.40, 100.90, 101.10))   # ORB contact (low<=101)
    return bars


def _orb_long_no_displacement_bars():
    """ORB + LONG break (idx5) + only 1 displacement bar (idx6) +
    early contact (idx7) -> ORB displacement INCOMPLETE."""
    bars = _orb_bars()
    bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))   # ORB break
    bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))   # 1 disp bar
    bars.append(_c(7, 101.30, 101.40, 100.90, 101.00))   # contact (low<=101)
    return bars


def _pdh_full_signal_bars(pdh_level=103.00):
    """Full session: ORB structure (eligible, idx0-9) followed by a
    complete, independent PDH BDRR sequence (idx10-14) at pdh_level,
    built by shifting the already-validated ORB break/displacement/
    rejection geometry by (pdh_level - 101.00) — same relative shape,
    proven to satisfy find_rejection()'s SINGLE_CANDLE_REJECTION
    checks, just anchored to a different absolute price."""
    shift = pdh_level - 101.00
    bars = _orb_long_eligible_bars()
    bars.append(_c(10, 100.80 + shift, 101.60 + shift, 100.70 + shift, 101.50 + shift))  # PDH break
    bars.append(_c(11, 101.55 + shift, 101.80 + shift, 101.20 + shift, 101.60 + shift))  # PDH disp 1/3
    bars.append(_c(12, 101.60 + shift, 101.90 + shift, 101.30 + shift, 101.70 + shift))  # PDH disp 2/3
    bars.append(_c(13, 101.70 + shift, 101.85 + shift, 101.10 + shift, 101.40 + shift))  # PDH disp 3/3
    bars.append(_c(14, 101.10 + shift, 101.30 + shift, 100.80 + shift, 101.20 + shift))  # PDH rejection
    return bars


# ═════════════════════════════════════════════════════════════════════════
# SHORT / PDL fixtures (mirror of LONG, ORB_LOW=99.00)
# ═════════════════════════════════════════════════════════════════════════

def _orb_short_eligible_bars():
    bars = _orb_bars()
    bars.append(_c(5, 99.20, 99.30, 98.40, 98.50))       # ORB break: close < 99
    bars.append(_c(6, 98.45, 98.80, 98.20, 98.30))       # ORB disp 1/3 (high < 99)
    bars.append(_c(7, 98.30, 98.70, 98.10, 98.20))       # ORB disp 2/3
    bars.append(_c(8, 98.20, 98.90, 97.90, 98.60))       # ORB disp 3/3
    bars.append(_c(9, 98.90, 99.20, 98.80, 99.05))       # ORB contact: high >= 99
    return bars


def _pdl_full_signal_bars(pdl_level=97.00):
    """Full session: ORB SHORT structure (eligible, idx0-9) followed
    by a complete, independent PDL BDRR sequence (idx10-14), built by
    mirroring the LONG rejection geometry around a level then shifting
    it to pdl_level — same technique as _pdh_full_signal_bars(),
    mirrored for SHORT."""
    shift = 99.00 - pdl_level  # mirror-then-shift offset applied below
    bars = _orb_short_eligible_bars()
    # Mirrored LONG break (99.20,99.30,98.40,98.50 at level=99) shifted
    # down by `shift` to anchor it at pdl_level instead.
    bars.append(_c(10, 99.20 - shift, 99.30 - shift, 98.40 - shift, 98.50 - shift))  # PDL break
    bars.append(_c(11, 98.45 - shift, 98.80 - shift, 98.20 - shift, 98.30 - shift))  # PDL disp 1/3
    bars.append(_c(12, 98.30 - shift, 98.70 - shift, 98.10 - shift, 98.20 - shift))  # PDL disp 2/3
    bars.append(_c(13, 98.20 - shift, 98.90 - shift, 97.90 - shift, 98.60 - shift))  # PDL disp 3/3
    # Mirrored LONG rejection candle (98.90,99.20,98.70,98.80 at level=99)
    bars.append(_c(14, 98.90 - shift, 99.20 - shift, 98.70 - shift, 98.80 - shift))  # PDL rejection
    return bars


# ═════════════════════════════════════════════════════════════════════════
# P1 — PDH not eligible: pipeline NOT evaluated
# ═════════════════════════════════════════════════════════════════════════

class TestP1NotEligible:
    def test_pipeline_not_evaluated_when_displacement_incomplete(self):
        session = _build_session(_orb_long_no_displacement_bars())
        out = evaluate_pdh_pdl_candidate(
            session, _prev_sessions(pdh=105.00), symbol="QQQ",
            direction="LONG", tick_size=0.01,
        )
        assert out["eligibility"]["eligible"] is False
        assert out["eligibility"]["reason"] == "DISPLACEMENT_INCOMPLETE"
        assert out["pdh_pdl_result"] is None


# ═════════════════════════════════════════════════════════════════════════
# P2 — PDH eligible: pipeline IS evaluated
# ═════════════════════════════════════════════════════════════════════════

class TestP2Eligible:
    def test_pipeline_evaluated_when_eligible(self):
        session = _build_session(_orb_long_eligible_bars())
        out = evaluate_pdh_pdl_candidate(
            session, _prev_sessions(pdh=105.00), symbol="QQQ",
            direction="LONG", tick_size=0.01,
        )
        assert out["eligibility"]["eligible"] is True
        # No PDH-specific break has happened yet in these candles, so
        # the freshly-built PDH detector must reach a real NO_SETUP
        # stage (not SIGNAL) — but crucially it WAS evaluated.
        assert out["pdh_pdl_result"] is not None
        assert out["pdh_pdl_result"].status == SignalStatus.NO_SETUP
        assert out["pdh_pdl_result"].failed_stage == "BREAK_NOT_FOUND"


# ═════════════════════════════════════════════════════════════════════════
# P3 — PDH full BDRR -> SIGNAL
# ═════════════════════════════════════════════════════════════════════════

class TestP3PdhFullSignal:
    def test_pdh_signal_produced_with_correct_identity(self):
        session = _build_session(_pdh_full_signal_bars(pdh_level=103.00))
        out = evaluate_pdh_pdl_candidate(
            session, _prev_sessions(pdh=103.00), symbol="QQQ",
            direction="LONG", tick_size=0.01,
        )
        assert out["eligibility"]["eligible"] is True
        result = out["pdh_pdl_result"]
        assert result is not None
        assert result.status == SignalStatus.SIGNAL
        assert result.direction == "LONG"

        break_ts = (result.stage_context or {}).get("break_time_ms")
        assert break_ts is not None
        assert result.setup_key == f"LONG:PREVIOUS_DAY_HIGH:{break_ts}"
        assert result.signal_key == f"{result.setup_key}:{result.entry_timestamp_ms}"


# ═════════════════════════════════════════════════════════════════════════
# P4 — PDL full BDRR -> SIGNAL (symmetric)
# ═════════════════════════════════════════════════════════════════════════

class TestP4PdlFullSignal:
    def test_pdl_signal_produced_with_correct_identity(self):
        session = _build_session(_pdl_full_signal_bars(pdl_level=97.00))
        out = evaluate_pdh_pdl_candidate(
            session, _prev_sessions(pdl=97.00), symbol="QQQ",
            direction="SHORT", tick_size=0.01,
        )
        assert out["eligibility"]["eligible"] is True
        result = out["pdh_pdl_result"]
        assert result is not None
        assert result.status == SignalStatus.SIGNAL
        assert result.direction == "SHORT"

        break_ts = (result.stage_context or {}).get("break_time_ms")
        assert break_ts is not None
        assert result.setup_key == f"SHORT:PREVIOUS_DAY_LOW:{break_ts}"
        assert result.signal_key == f"{result.setup_key}:{result.entry_timestamp_ms}"


# ═════════════════════════════════════════════════════════════════════════
# P5 — eligibility invalidated after being True: pipeline NOT evaluated
# ═════════════════════════════════════════════════════════════════════════

class TestP5InvalidatedNoLongerEvaluated:
    def test_no_longer_evaluated_after_orb_invalidation(self):
        prev = _prev_sessions(pdh=105.00)

        base_bars = _orb_long_eligible_bars()
        out_before = evaluate_pdh_pdl_candidate(
            _build_session(base_bars), prev, symbol="QQQ",
            direction="LONG", tick_size=0.01,
        )
        assert out_before["eligibility"]["eligible"] is True
        assert out_before["pdh_pdl_result"] is not None

        # Append 2 consecutive closes back inside the ORB band
        # (close <= orb_high=101.00) -> validate_sequence() reports
        # INVALIDATED (same rule used elsewhere, threshold 2).
        invalidated_bars = base_bars + [
            _c(10, 100.90, 101.00, 100.40, 100.50),
            _c(11, 100.50, 100.90, 100.30, 100.60),
        ]
        out_after = evaluate_pdh_pdl_candidate(
            _build_session(invalidated_bars), prev, symbol="QQQ",
            direction="LONG", tick_size=0.01,
        )
        assert out_after["eligibility"]["eligible"] is False
        assert out_after["eligibility"]["reason"] == "ORB_STRUCTURE_INVALIDATED"
        assert out_after["pdh_pdl_result"] is None


# ═════════════════════════════════════════════════════════════════════════
# P6 — ORB behavior unaffected by this module's existence
# ═════════════════════════════════════════════════════════════════════════

class TestP6OrbUnaffected:
    def test_orb_detector_identical_with_and_without_pdh_evaluator(self):
        bars = _orb_long_eligible_bars()
        session_a = _build_session(bars)
        session_b = _build_session(bars)

        # ORB detector evaluated alone.
        orb_alone = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
        )
        result_alone = orb_alone.evaluate(session_a)

        # ORB detector evaluated again, but only AFTER also invoking
        # the PDH evaluator on the same session/candles — must be
        # byte-for-byte identical; no shared mutable state.
        evaluate_pdh_pdl_candidate(
            session_b, _prev_sessions(pdh=105.00), symbol="QQQ",
            direction="LONG", tick_size=0.01,
        )
        orb_after = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
        )
        result_after = orb_after.evaluate(session_b)

        assert result_alone.status == result_after.status
        assert result_alone.failed_stage == result_after.failed_stage
        assert result_alone.setup_key == result_after.setup_key
        assert result_alone.pipeline_stage == result_after.pipeline_stage

    def test_orb_level_source_still_default(self):
        """The ORB detector's own level_source is untouched — still
        the canonical direction-derived default."""
        orb_detector = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
        )
        assert orb_detector._engine_config["level_source"] == "ORB_HIGH"


# ═════════════════════════════════════════════════════════════════════════
# Misc: no previous sessions -> pipeline not evaluated (no crash)
# ═════════════════════════════════════════════════════════════════════════

class TestMiscGuards:
    def test_no_previous_sessions_not_evaluated(self):
        session = _build_session(_orb_long_eligible_bars())
        out = evaluate_pdh_pdl_candidate(
            session, None, symbol="QQQ", direction="LONG", tick_size=0.01,
        )
        assert out["eligibility"]["eligible"] is False
        assert out["eligibility"]["reason"] == "NO_PREVIOUS_SESSIONS"
        assert out["pdh_pdl_result"] is None
