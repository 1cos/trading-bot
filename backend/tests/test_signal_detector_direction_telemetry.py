"""Regression tests — stage_context['direction'] telemetry fix.

Context (audit 2026-08-23, micro-task "Fix PDH/PDL stage_context.direction
telemetry"): LiveSignalDetector._evaluate_inner() derived a *second*,
independent 'direction' local variable from level_source
("LONG" if level_source == "ORB_HIGH" else "SHORT") purely to populate
stage_context["direction"]. This is accidentally correct for
ORB_HIGH -> LONG, ORB_LOW -> SHORT, PREVIOUS_DAY_LOW -> SHORT, but wrong
for PREVIOUS_DAY_HIGH -> SHORT (should be LONG).

The bug was confirmed telemetry-only: SignalResult.direction, setup_key,
signal_key, and every BDRR stage function (find_break, find_displacement,
validate_sequence, find_retest_window, find_rejection) all use
self._direction / self._engine_config["direction"], never the local
variable. Only stage_context["direction"] was affected.

Fix: stage_context["direction"] now uses self._direction directly,
exactly like SignalResult.direction and setup_key already did.

D1: PDH LONG - stage_context direction must be LONG (was SHORT, bugged).
D2: PDL SHORT - guard, must remain SHORT.
D3: ORB HIGH - guard, must remain LONG.
D4: ORB LOW - guard, must remain SHORT.
D5: PDH SIGNAL fields unchanged except the telemetry direction fix itself.
"""

from __future__ import annotations

from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus
from trading_lab.live.session_builder_live import LiveSessionBuilder


# ── Timestamp helpers (same base as test_pdh_pdl_candidate_evaluator.py) ────

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


# ── PDH (LONG) full BDRR sequence — identical fixture used in the audit ─────
# (mirrors test_pdh_pdl_candidate_evaluator.py::_pdh_full_signal_bars)

def _orb_long_eligible_bars():
    bars = _orb_bars()
    bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))   # ORB break
    bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))   # ORB disp 1/3
    bars.append(_c(7, 101.60, 101.90, 101.30, 101.70))   # ORB disp 2/3
    bars.append(_c(8, 101.70, 101.85, 101.10, 101.40))   # ORB disp 3/3
    bars.append(_c(9, 101.20, 101.40, 100.90, 101.10))   # ORB contact
    return bars


def _pdh_full_signal_bars(pdh_level=103.00):
    shift = pdh_level - 101.00
    bars = _orb_long_eligible_bars()
    bars.append(_c(10, 100.80 + shift, 101.60 + shift, 100.70 + shift, 101.50 + shift))
    bars.append(_c(11, 101.55 + shift, 101.80 + shift, 101.20 + shift, 101.60 + shift))
    bars.append(_c(12, 101.60 + shift, 101.90 + shift, 101.30 + shift, 101.70 + shift))
    bars.append(_c(13, 101.70 + shift, 101.85 + shift, 101.10 + shift, 101.40 + shift))
    bars.append(_c(14, 101.10 + shift, 101.30 + shift, 100.80 + shift, 101.20 + shift))
    return bars


# ── PDL (SHORT) full BDRR sequence — mirrors _pdh_full_signal_bars ──────────

def _orb_short_eligible_bars():
    bars = _orb_bars()
    bars.append(_c(5, 99.20, 99.30, 98.40, 98.50))
    bars.append(_c(6, 98.45, 98.80, 98.20, 98.30))
    bars.append(_c(7, 98.30, 98.70, 98.10, 98.20))
    bars.append(_c(8, 98.20, 98.90, 97.90, 98.60))
    bars.append(_c(9, 98.90, 99.20, 98.80, 99.05))
    return bars


def _pdl_full_signal_bars(pdl_level=97.00):
    shift = 99.00 - pdl_level
    bars = _orb_short_eligible_bars()
    bars.append(_c(10, 99.20 - shift, 99.30 - shift, 98.40 - shift, 98.50 - shift))
    bars.append(_c(11, 98.45 - shift, 98.80 - shift, 98.20 - shift, 98.30 - shift))
    bars.append(_c(12, 98.30 - shift, 98.70 - shift, 98.10 - shift, 98.20 - shift))
    bars.append(_c(13, 98.20 - shift, 98.90 - shift, 97.90 - shift, 98.60 - shift))
    bars.append(_c(14, 98.90 - shift, 99.20 - shift, 98.70 - shift, 98.80 - shift))
    return bars


# ═════════════════════════════════════════════════════════════════════════
# D1 — PDH LONG: stage_context direction must be LONG (fails pre-fix)
# ═════════════════════════════════════════════════════════════════════════

class TestD1PdhLongDirectionTelemetry:
    def test_pdh_stage_context_direction_is_long(self):
        session = _build_session(_pdh_full_signal_bars(pdh_level=103.00))
        detector = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            level_source="PREVIOUS_DAY_HIGH",
        )
        detector.set_previous_sessions(_prev_sessions(pdh=103.00))

        result = detector.evaluate(session)

        assert result.status == SignalStatus.SIGNAL
        assert result.direction == "LONG"
        assert result.stage_context is not None
        assert result.stage_context["direction"] == "LONG"


# ═════════════════════════════════════════════════════════════════════════
# D2 — PDL SHORT: guard against regression
# ═════════════════════════════════════════════════════════════════════════

class TestD2PdlShortDirectionTelemetry:
    def test_pdl_stage_context_direction_is_short(self):
        session = _build_session(_pdl_full_signal_bars(pdl_level=97.00))
        detector = LiveSignalDetector(
            symbol="QQQ", direction="SHORT", tick_size=0.01,
            level_source="PREVIOUS_DAY_LOW",
        )
        detector.set_previous_sessions(_prev_sessions(pdl=97.00))

        result = detector.evaluate(session)

        assert result.status == SignalStatus.SIGNAL
        assert result.direction == "SHORT"
        assert result.stage_context is not None
        assert result.stage_context["direction"] == "SHORT"


# ═════════════════════════════════════════════════════════════════════════
# D3 — ORB HIGH: guard against regression
# ═════════════════════════════════════════════════════════════════════════

class TestD3OrbHighDirectionTelemetry:
    def test_orb_high_stage_context_direction_is_long(self):
        session = _build_session(_orb_long_eligible_bars())
        detector = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
        )  # level_source defaults to ORB_HIGH for LONG

        result = detector.evaluate(session)

        # Break is found (idx5); displacement/retest may or may not
        # complete with this trimmed fixture — only stage_context
        # telemetry is under test here.
        assert result.stage_context is not None
        assert result.stage_context["direction"] == "LONG"


# ═════════════════════════════════════════════════════════════════════════
# D4 — ORB LOW: guard against regression
# ═════════════════════════════════════════════════════════════════════════

class TestD4OrbLowDirectionTelemetry:
    def test_orb_low_stage_context_direction_is_short(self):
        session = _build_session(_orb_short_eligible_bars())
        detector = LiveSignalDetector(
            symbol="QQQ", direction="SHORT", tick_size=0.01,
        )  # level_source defaults to ORB_LOW for SHORT

        result = detector.evaluate(session)

        assert result.stage_context is not None
        assert result.stage_context["direction"] == "SHORT"


# ═════════════════════════════════════════════════════════════════════════
# D5 — PDH SIGNAL: every other field unchanged by the fix
# ═════════════════════════════════════════════════════════════════════════

class TestD5PdhSignalFieldsUnchanged:
    def test_pdh_signal_fields_unchanged_except_direction_telemetry(self):
        session = _build_session(_pdh_full_signal_bars(pdh_level=103.00))
        detector = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=0.01,
            level_source="PREVIOUS_DAY_HIGH",
        )
        detector.set_previous_sessions(_prev_sessions(pdh=103.00))

        result = detector.evaluate(session)

        # Values captured empirically against pre-fix HEAD
        # (58c4e42966b2c51acb7e110672eea18cb137f9f8) during the audit —
        # identical before and after this fix, since the fix only
        # touches stage_context["direction"].
        assert result.status == SignalStatus.SIGNAL
        assert result.stage_context["break_bar_index"] == 10
        assert result.detection_result.displacement_bar_count == 3
        assert float(result.entry_price) == 103.20
        assert float(result.stop_price) == 102.80
        assert float(result.target_price) == 104.00
        assert result.setup_key == "LONG:PREVIOUS_DAY_HIGH:1786455600000"
