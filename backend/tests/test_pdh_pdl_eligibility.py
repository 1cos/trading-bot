"""Tests for the PDH/PDL eligibility predicate (micro-task 12, V1 rule).

check_orb_to_level_eligibility() answers exactly one stateless question:
"is PDH (LONG) / PDL (SHORT) eligible to become a candidate structural
level right now?" It does NOT select a level, does NOT build a PDH/PDL
BDRR sequence, and does NOT generate a signal.

Rule V1 (approved, deliberately minimal — no ATR/percentage/distance/
ORB-width-multiple/composite-zone logic):

    LONG:  PDH eligible iff PDH > ORB_HIGH
                         AND a valid LONG break of ORB_HIGH exists
                         AND displacement for that break is complete
                             (existing BDRR rules)
                         AND the ORB structure is not currently
                             invalidated (existing BDRR rules)
    SHORT: symmetric on ORB_LOW / PDL.

Cases covered (exactly as specified):
    E1 LONG positive             -> True
    E2 LONG no displacement      -> False
    E3 LONG wrong geometry       -> False
    E4 SHORT positive            -> True
    E5 SHORT no displacement     -> False
    E6 SHORT wrong geometry      -> False
    E7 invalidated (anti-stuck)  -> recomputed False after invalidation
"""

from __future__ import annotations

from trading_lab.pdh_pdl_eligibility import check_orb_to_level_eligibility
from trading_lab.session_context import build_session_context


MS_0930 = 1786455000000


def _ms(offset_min: int) -> int:
    return MS_0930 + offset_min * 60_000


def _c(offset_min, o, h, l, cl):
    return {"time_ms": _ms(offset_min), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


def _config(direction: str) -> dict:
    """Engine config shape matching LiveSignalDetector's own
    engine_config. level_source is a placeholder — the eligibility
    function overrides it internally based on direction."""
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


def _orb_bars():
    """5 ORB bars (idx0-4): ORB high=101.00, low=99.00."""
    return [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
    ]


def _check(bars, direction, candidate_level_price):
    sc = build_session_context(bars, _config(direction))
    assert sc["status"] == "OK"
    return check_orb_to_level_eligibility(
        sc["candles"], sc, _config(direction), candidate_level_price,
    )


# ═════════════════════════════════════════════════════════════════════════
# LONG (ORB_HIGH / PDH)
# ═════════════════════════════════════════════════════════════════════════

def _long_full_bars():
    """ORB + LONG break (idx5) + 3 valid displacement bars (idx6-8)
    + retest-contact bar (idx9, low <= 101.00) -> displacement complete."""
    bars = _orb_bars()
    bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))   # break: close > 101
    bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))   # disp 1/3 (low > 101)
    bars.append(_c(7, 101.60, 101.90, 101.30, 101.70))   # disp 2/3
    bars.append(_c(8, 101.70, 101.85, 101.10, 101.40))   # disp 3/3
    bars.append(_c(9, 101.10, 101.30, 100.80, 101.20))   # contact: low <= 101
    return bars


def _long_no_displacement_bars():
    """Break (idx5) + 1 bar staying above (idx6) + contact (idx7) ->
    displacement_bar_count = 1 < 3 -> DISPLACEMENT_TOO_SHORT."""
    bars = _orb_bars()
    bars.append(_c(5, 100.80, 101.60, 100.70, 101.50))   # break
    bars.append(_c(6, 101.55, 101.80, 101.20, 101.60))   # 1 disp bar
    bars.append(_c(7, 101.30, 101.40, 100.90, 101.00))   # contact (low <= 101)
    return bars


class TestE1LongPositive:
    def test_pdh_eligible_true(self):
        result = _check(_long_full_bars(), "LONG", candidate_level_price=105.00)
        assert result["eligible"] is True
        assert result["reason"] == "ORB_BREAK_AND_DISPLACEMENT_COMPLETE"


class TestE2LongNoDisplacement:
    def test_pdh_not_eligible_displacement_incomplete(self):
        result = _check(
            _long_no_displacement_bars(), "LONG", candidate_level_price=105.00,
        )
        assert result["eligible"] is False
        assert result["reason"] == "DISPLACEMENT_INCOMPLETE"


class TestE3LongWrongGeometry:
    def test_pdh_not_eligible_below_orb_high(self):
        # Full, otherwise-qualifying break+displacement, but PDH <= ORB_HIGH.
        result = _check(_long_full_bars(), "LONG", candidate_level_price=100.50)
        assert result["eligible"] is False
        assert result["reason"] == "WRONG_GEOMETRY"

    def test_pdh_not_eligible_exactly_equal_orb_high(self):
        result = _check(_long_full_bars(), "LONG", candidate_level_price=101.00)
        assert result["eligible"] is False
        assert result["reason"] == "WRONG_GEOMETRY"


# ═════════════════════════════════════════════════════════════════════════
# SHORT (ORB_LOW / PDL)
# ═════════════════════════════════════════════════════════════════════════

def _short_full_bars():
    """ORB + SHORT break (idx5) + 3 valid displacement bars (idx6-8)
    + retest-contact bar (idx9, high >= 99.00) -> displacement complete."""
    bars = _orb_bars()
    bars.append(_c(5, 99.20, 99.30, 98.40, 98.50))       # break: close < 99
    bars.append(_c(6, 98.45, 98.80, 98.20, 98.30))       # disp 1/3 (high < 99)
    bars.append(_c(7, 98.30, 98.70, 98.10, 98.20))       # disp 2/3
    bars.append(_c(8, 98.20, 98.90, 97.90, 98.60))       # disp 3/3
    bars.append(_c(9, 98.90, 99.20, 98.80, 99.05))       # contact: high >= 99
    return bars


def _short_no_displacement_bars():
    """Break (idx5) + 1 bar staying below (idx6) + contact (idx7) ->
    displacement_bar_count = 1 < 3 -> DISPLACEMENT_TOO_SHORT."""
    bars = _orb_bars()
    bars.append(_c(5, 99.20, 99.30, 98.40, 98.50))       # break
    bars.append(_c(6, 98.45, 98.80, 98.20, 98.30))       # 1 disp bar
    bars.append(_c(7, 98.60, 99.10, 98.50, 98.90))       # contact (high >= 99)
    return bars


class TestE4ShortPositive:
    def test_pdl_eligible_true(self):
        result = _check(_short_full_bars(), "SHORT", candidate_level_price=95.00)
        assert result["eligible"] is True
        assert result["reason"] == "ORB_BREAK_AND_DISPLACEMENT_COMPLETE"


class TestE5ShortNoDisplacement:
    def test_pdl_not_eligible_displacement_incomplete(self):
        result = _check(
            _short_no_displacement_bars(), "SHORT", candidate_level_price=95.00,
        )
        assert result["eligible"] is False
        assert result["reason"] == "DISPLACEMENT_INCOMPLETE"


class TestE6ShortWrongGeometry:
    def test_pdl_not_eligible_above_orb_low(self):
        # Full, otherwise-qualifying break+displacement, but PDL >= ORB_LOW.
        result = _check(_short_full_bars(), "SHORT", candidate_level_price=99.50)
        assert result["eligible"] is False
        assert result["reason"] == "WRONG_GEOMETRY"

    def test_pdl_not_eligible_exactly_equal_orb_low(self):
        result = _check(_short_full_bars(), "SHORT", candidate_level_price=99.00)
        assert result["eligible"] is False
        assert result["reason"] == "WRONG_GEOMETRY"


# ═════════════════════════════════════════════════════════════════════════
# E7 — anti-stuck: eligibility must NOT remain stuck True after the ORB
# structure that justified it is invalidated.
# ═════════════════════════════════════════════════════════════════════════

class TestE7Invalidated:
    def test_eligible_becomes_false_after_orb_invalidation(self):
        bars = _long_full_bars()

        # First: confirm eligible=True on the base scenario (same as E1).
        result_before = _check(bars, "LONG", candidate_level_price=105.00)
        assert result_before["eligible"] is True

        # Now append 2 consecutive candles closing back inside the ORB
        # band (close <= orb_high=101.00) — the same consecutive_orb_closes
        # rule (default threshold 2) already used elsewhere to invalidate
        # an ORB structure. This must flip eligibility back to False,
        # recomputed from scratch — no persisted flag anywhere.
        bars_invalidated = bars + [
            _c(10, 100.90, 101.00, 100.40, 100.50),   # 1st close back inside
            _c(11, 100.50, 100.90, 100.30, 100.60),   # 2nd consecutive -> INVALIDATED
        ]
        result_after = _check(
            bars_invalidated, "LONG", candidate_level_price=105.00,
        )
        assert result_after["eligible"] is False
        assert result_after["reason"] == "ORB_STRUCTURE_INVALIDATED"

    def test_recomputation_is_stateless_not_a_flag(self):
        """Calling the predicate on the pre-invalidation candles again,
        AFTER having already seen the invalidated result, must still
        return True — proving there is no persisted/cached flag."""
        bars = _long_full_bars()
        bars_invalidated = bars + [
            _c(10, 100.90, 101.00, 100.40, 100.50),
            _c(11, 100.50, 100.90, 100.30, 100.60),
        ]

        result_invalidated = _check(
            bars_invalidated, "LONG", candidate_level_price=105.00,
        )
        assert result_invalidated["eligible"] is False

        # Same candles as the original E1 scenario, evaluated again —
        # must still be True. Nothing was mutated by the previous call.
        result_original_again = _check(bars, "LONG", candidate_level_price=105.00)
        assert result_original_again["eligible"] is True


# ═════════════════════════════════════════════════════════════════════════
# Misc: unsupported direction / ORB not ready
# ═════════════════════════════════════════════════════════════════════════

class TestMiscGuards:
    def test_unsupported_direction(self):
        sc = build_session_context(_orb_bars(), _config("LONG"))
        config = {**_config("LONG"), "direction": "SIDEWAYS"}
        result = check_orb_to_level_eligibility(
            sc["candles"], sc, config, candidate_level_price=105.00,
        )
        assert result["eligible"] is False
        assert result["reason"] == "UNSUPPORTED_DIRECTION"

    def test_orb_not_ready_insufficient_candles(self):
        # Only 2 of the 5 ORB bars -> ORB window not complete yet.
        bars = _orb_bars()[:2]
        sc = build_session_context(bars, _config("LONG"))
        assert sc["status"] == "OK"
        result = check_orb_to_level_eligibility(
            sc["candles"], sc, _config("LONG"), candidate_level_price=105.00,
        )
        assert result["eligible"] is False
        assert result["reason"] == "ORB_NOT_READY"
