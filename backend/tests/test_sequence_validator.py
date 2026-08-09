"""Tests for the BDRR Sequence Validator.

Covers:
  1. Sequence remains active when no consecutive inside closes
  2. Sequence invalidated after 2 consecutive closes inside ORB (LONG)
  3. Single close inside ORB + recovery does not invalidate
  4. Sequence invalidated after 2 consecutive closes inside ORB (SHORT)
  5. Custom threshold of 3 consecutive closes
  6. Close below ORB_L counts toward LONG invalidation
  7. Close above ORB_H counts toward SHORT invalidation
  8. max_valid_index is correct (bar before first consecutive inside)
  9. Failed upstream passes through
  10. Invalidation when first_retest_contact itself starts inside streak
"""

import pytest
from trading_lab.sequence_validator import validate_sequence


# ── Helpers ────────────────────────────────────────────────────────────────

def _candle(time_ms, close, high=None, low=None):
    if high is None:
        high = close + 0.5
    if low is None:
        low = close - 0.5
    return {
        "time_ms": time_ms,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
    }


def _orb(orb_high, orb_low):
    return {
        "status": "OK",
        "orb_high": orb_high,
        "orb_low": orb_low,
    }


def _break():
    return {"status": "OK"}


def _disp(first_retest_contact_index):
    return {
        "status": "OK",
        "first_retest_contact_index": first_retest_contact_index,
    }


def _config(direction="LONG", consecutive=2):
    return {
        "direction": direction,
        "consecutive_orb_closes": consecutive,
    }


# ── Tests ──────────────────────────────────────────────────────────────────


class TestSequenceActive:
    def test_all_closes_above_orb_high(self):
        """LONG: all closes above ORB_H → sequence active"""
        candles = [
            _candle(1000, 101.0),  # ORB
            _candle(2000, 102.0),  # break
            _candle(3000, 101.5),  # displacement
            _candle(4000, 101.2, low=100.5),  # first retest contact
            _candle(5000, 101.3),  # above
            _candle(6000, 101.5),  # above
        ]
        orb = _orb(101.0, 99.0)
        result = validate_sequence(candles, orb, _break(), _disp(3), _config())
        assert result["status"] == "OK"
        assert result["max_valid_index"] == 5
        assert result["invalidation_index"] is None

    def test_single_inside_close_then_recovery(self):
        """LONG: one close inside ORB, then recovery → no invalidation"""
        candles = [
            _candle(1000, 101.0),
            _candle(2000, 102.0),
            _candle(3000, 101.5),
            _candle(4000, 100.5, low=100.0),  # retest contact, INSIDE ORB
            _candle(5000, 101.5),              # recovery ABOVE
            _candle(6000, 102.0),
        ]
        orb = _orb(101.0, 99.0)
        result = validate_sequence(candles, orb, _break(), _disp(3), _config())
        assert result["status"] == "OK"


class TestSequenceInvalidated:
    def test_two_consecutive_inside_closes_long(self):
        """LONG: two consecutive closes inside ORB → invalidated"""
        candles = [
            _candle(1000, 101.0),
            _candle(2000, 102.0),
            _candle(3000, 101.5),
            _candle(4000, 100.5, low=100.0),  # inside #1
            _candle(5000, 100.8, low=100.0),  # inside #2 → INVALIDATED
            _candle(6000, 101.5),
        ]
        orb = _orb(101.0, 99.0)
        result = validate_sequence(candles, orb, _break(), _disp(3), _config())
        assert result["status"] == "INVALIDATED"
        assert result["invalidation_index"] == 4
        assert result["max_valid_index"] == 3  # one bar before invalidation bar 4

    def test_max_valid_index_correct(self):
        """max_valid_index is the bar before the first consecutive inside close"""
        candles = [
            _candle(1000, 101.0),
            _candle(2000, 102.0),
            _candle(3000, 101.5),
            _candle(4000, 101.2, low=100.5),  # above (retest contact)
            _candle(5000, 101.3),              # above
            _candle(6000, 100.5),              # inside #1
            _candle(7000, 100.3),              # inside #2 → INVALIDATED
        ]
        orb = _orb(101.0, 99.0)
        result = validate_sequence(candles, orb, _break(), _disp(3), _config())
        assert result["invalidation_index"] == 6
        assert result["max_valid_index"] == 5  # one bar before invalidation bar 6

    def test_close_below_orb_low_counts(self):
        """LONG: close below ORB_L counts toward consecutive inside"""
        candles = [
            _candle(1000, 101.0),
            _candle(2000, 102.0),
            _candle(3000, 101.5),
            _candle(4000, 100.5, low=100.0),  # inside #1
            _candle(5000, 98.0, low=97.0),     # below ORB_L → #2 → INVALIDATED
        ]
        orb = _orb(101.0, 99.0)
        result = validate_sequence(candles, orb, _break(), _disp(3), _config())
        assert result["status"] == "INVALIDATED"
        assert result["invalidation_index"] == 4

    def test_two_consecutive_inside_closes_short(self):
        """SHORT: two consecutive closes inside ORB → invalidated"""
        candles = [
            _candle(1000, 99.0),
            _candle(2000, 98.0),
            _candle(3000, 98.5),
            _candle(4000, 99.5, high=101.0),  # inside #1 (close >= orb_low)
            _candle(5000, 99.8, high=101.0),  # inside #2 → INVALIDATED
        ]
        orb = _orb(101.0, 99.0)
        result = validate_sequence(
            candles, orb, _break(), _disp(3), _config(direction="SHORT"))
        assert result["status"] == "INVALIDATED"
        assert result["invalidation_index"] == 4

    def test_custom_threshold_3(self):
        """Threshold=3: two inside closes not enough, three triggers"""
        candles = [
            _candle(1000, 101.0),
            _candle(2000, 102.0),
            _candle(3000, 101.5),
            _candle(4000, 100.5, low=100.0),  # inside #1
            _candle(5000, 100.8),              # inside #2
            _candle(6000, 101.5),              # recovery → reset
            _candle(7000, 100.2),              # inside #1 again
            _candle(8000, 100.4),              # inside #2
            _candle(9000, 100.6),              # inside #3 → INVALIDATED
        ]
        orb = _orb(101.0, 99.0)
        result = validate_sequence(
            candles, orb, _break(), _disp(3), _config(consecutive=3))
        assert result["status"] == "INVALIDATED"
        assert result["invalidation_index"] == 8

    def test_consecutive_inside_closes_returned(self):
        """The consecutive close bars are returned in the result"""
        candles = [
            _candle(1000, 101.0),
            _candle(2000, 102.0),
            _candle(3000, 101.5),
            _candle(4000, 100.5, low=100.0),
            _candle(5000, 100.3),
        ]
        orb = _orb(101.0, 99.0)
        result = validate_sequence(candles, orb, _break(), _disp(3), _config())
        assert result["status"] == "INVALIDATED"
        closes = result["consecutive_inside_closes"]
        assert len(closes) == 2
        assert closes[0] == (3, 100.5)
        assert closes[1] == (4, 100.3)


class TestUpstreamFailure:
    def test_failed_orb(self):
        result = validate_sequence(
            [], {"status": "FAILED"}, _break(), _disp(0), _config())
        assert result["status"] == "FAILED"

    def test_failed_break(self):
        result = validate_sequence(
            [], _orb(100, 99), {"status": "FAILED"}, _disp(0), _config())
        assert result["status"] == "FAILED"

    def test_failed_displacement(self):
        result = validate_sequence(
            [], _orb(100, 99), _break(), {"status": "FAILED"}, _config())
        assert result["status"] == "FAILED"


# ══════════════════════════════════════════════════════════════════════════════
# LINE-LEVEL INVALIDATION — PREVIOUS_DAY_HIGH / PREVIOUS_DAY_LOW
# ══════════════════════════════════════════════════════════════════════════════


def _pd_level(level_price, level_source="PREVIOUS_DAY_HIGH"):
    """Minimal LevelResult for a line level (no orb_high/orb_low)."""
    return {
        "status": "OK",
        "level_source": level_source,
        "level_price": level_price,
    }


def _pd_config(direction="LONG", level_source="PREVIOUS_DAY_HIGH",
               level_invalidation_closes=2):
    return {
        "direction": direction,
        "level_source": level_source,
        "level_invalidation_closes": level_invalidation_closes,
    }


class TestPDHLongInvalidation:
    """Cases 1-2: PREVIOUS_DAY_HIGH LONG invalidation."""

    def test_one_close_below_still_valid(self):
        """One close below PDH → still valid."""
        candles = [
            _candle(1000, 200.0),  # ORB placeholder
            _candle(2000, 200.0),
            _candle(3000, 200.0),
            _candle(4000, 200.0),
            _candle(5000, 200.0),
            _candle(6000, 199.90),  # close below 200 — first
            _candle(7000, 200.10),  # recovery
        ]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(5),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH"),
        )
        assert r["status"] == "OK"

    def test_two_consecutive_below_invalidated(self):
        """Two consecutive closes below PDH → invalidated."""
        candles = [
            _candle(1000, 200.0),
            _candle(2000, 200.0),
            _candle(3000, 200.0),
            _candle(4000, 200.0),
            _candle(5000, 200.0),
            _candle(6000, 199.90),  # first below
            _candle(7000, 199.80),  # second below → invalidated
        ]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(5),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH"),
        )
        assert r["status"] == "INVALIDATED"
        assert r["invalidation_index"] == 6
        assert r["max_valid_index"] == 5
        assert r["threshold"] == 2
        assert r["level_source"] == "PREVIOUS_DAY_HIGH"


class TestPDHLongRecovery:
    """Case 2: close below, recovery, close below → not invalidated."""

    def test_interrupted_streak_not_invalidated(self):
        candles = [
            _candle(1000, 200.0),
            _candle(2000, 200.0),
            _candle(3000, 200.0),
            _candle(4000, 200.0),
            _candle(5000, 200.0),
            _candle(6000, 199.90),  # below
            _candle(7000, 200.10),  # recovery — resets counter
            _candle(8000, 199.90),  # below again — only 1
        ]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(5),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH"),
        )
        assert r["status"] == "OK"


class TestPDLShortInvalidation:
    """Cases 3-4: PREVIOUS_DAY_LOW SHORT invalidation."""

    def test_one_close_above_still_valid(self):
        """One close above PDL → still valid."""
        candles = [
            _candle(1000, 100.0),
            _candle(2000, 100.0),
            _candle(3000, 100.0),
            _candle(4000, 100.0),
            _candle(5000, 100.0),
            _candle(6000, 100.10),  # above
            _candle(7000, 99.90),   # recovery
        ]
        r = validate_sequence(
            candles, _pd_level(100.0, "PREVIOUS_DAY_LOW"), _break(), _disp(5),
            _pd_config("SHORT", "PREVIOUS_DAY_LOW"),
        )
        assert r["status"] == "OK"

    def test_two_consecutive_above_invalidated(self):
        """Two consecutive closes above PDL → invalidated."""
        candles = [
            _candle(1000, 100.0),
            _candle(2000, 100.0),
            _candle(3000, 100.0),
            _candle(4000, 100.0),
            _candle(5000, 100.0),
            _candle(6000, 100.10),  # first above
            _candle(7000, 100.20),  # second above → invalidated
        ]
        r = validate_sequence(
            candles, _pd_level(100.0, "PREVIOUS_DAY_LOW"), _break(), _disp(5),
            _pd_config("SHORT", "PREVIOUS_DAY_LOW"),
        )
        assert r["status"] == "INVALIDATED"
        assert r["invalidation_index"] == 6
        assert r["level_source"] == "PREVIOUS_DAY_LOW"

    def test_interrupted_streak_not_invalidated(self):
        """Close above, below, above → not invalidated."""
        candles = [
            _candle(1000, 100.0),
            _candle(2000, 100.0),
            _candle(3000, 100.0),
            _candle(4000, 100.0),
            _candle(5000, 100.0),
            _candle(6000, 100.10),  # above
            _candle(7000, 99.90),   # recovery
            _candle(8000, 100.10),  # above — only 1
        ]
        r = validate_sequence(
            candles, _pd_level(100.0, "PREVIOUS_DAY_LOW"), _break(), _disp(5),
            _pd_config("SHORT", "PREVIOUS_DAY_LOW"),
        )
        assert r["status"] == "OK"


class TestExactEquality:
    """Case 5: close exactly at level_price does NOT count."""

    def test_close_at_level_long_not_wrong_side(self):
        candles = [
            _candle(1000, 200.0),
            _candle(2000, 200.0),
            _candle(3000, 200.0),
            _candle(4000, 200.0),
            _candle(5000, 200.0),
            _candle(6000, 200.0),  # exactly at level
            _candle(7000, 200.0),  # exactly at level again
        ]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(5),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH"),
        )
        assert r["status"] == "OK"

    def test_close_at_level_short_not_wrong_side(self):
        candles = [
            _candle(1000, 100.0),
            _candle(2000, 100.0),
            _candle(3000, 100.0),
            _candle(4000, 100.0),
            _candle(5000, 100.0),
            _candle(6000, 100.0),  # exactly at level
            _candle(7000, 100.0),  # exactly at level again
        ]
        r = validate_sequence(
            candles, _pd_level(100.0, "PREVIOUS_DAY_LOW"), _break(), _disp(5),
            _pd_config("SHORT", "PREVIOUS_DAY_LOW"),
        )
        assert r["status"] == "OK"


class TestMaxValidIndex:
    """Cases 6-8: max_valid_index correctness."""

    def test_max_valid_is_bar_before_invalidation(self):
        """max_valid_index = invalidation_index - 1."""
        candles = [
            _candle(1000, 200.0),
            _candle(2000, 200.0),
            _candle(3000, 200.0),
            _candle(4000, 200.0),
            _candle(5000, 200.0),
            _candle(6000, 200.10),  # above level — still OK
            _candle(7000, 199.90),  # first below
            _candle(8000, 199.80),  # second below → invalidated at 7
        ]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(5),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH"),
        )
        assert r["status"] == "INVALIDATED"
        assert r["invalidation_index"] == 7
        assert r["max_valid_index"] == 6

    def test_invalidation_before_retest_blocks(self):
        """If invalidation before first_retest, runner would fail disp."""
        candles = [
            _candle(1000, 200.0),
            _candle(2000, 200.0),
            _candle(3000, 199.90),  # below (frc=3, but invalidated at 3)
            _candle(4000, 199.80),  # second below
        ]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(2),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH"),
        )
        assert r["status"] == "INVALIDATED"
        assert r["max_valid_index"] == 2
        # Runner would compare max_valid_index < first_retest_contact
        # and fail displacement. This test verifies the validator output.

    def test_retest_before_invalidation_usable(self):
        """Retest at index 5, invalidation at 7 → retest is within window."""
        candles = [
            _candle(1000, 200.0),
            _candle(2000, 200.0),
            _candle(3000, 200.0),
            _candle(4000, 200.0),
            _candle(5000, 200.10),  # retest would be here
            _candle(6000, 200.05),
            _candle(7000, 199.90),  # first below
            _candle(8000, 199.80),  # second → invalidation at 7
        ]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(5),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH"),
        )
        assert r["status"] == "INVALIDATED"
        assert r["max_valid_index"] == 6
        # Index 5 is within [5, 6] → retest would be accepted


class TestORBPreserved:
    """Cases 9-10: ORB band invalidation unchanged."""

    def test_orb_high_long_band_invalidation(self):
        """ORB_HIGH LONG: 2 closes ≤ orb_high → invalidated."""
        candles = [
            _candle(1000, 101.0),  # ORB
            _candle(2000, 101.0),
            _candle(3000, 101.0),
            _candle(4000, 101.0),
            _candle(5000, 101.0),
            _candle(6000, 100.0),  # inside ORB
            _candle(7000, 99.5),   # inside ORB
        ]
        r = validate_sequence(
            candles, _orb(100.5, 99.0), _break(), _disp(5),
            {**_config("LONG", 2), "level_source": "ORB_HIGH"},
        )
        assert r["status"] == "INVALIDATED"
        assert "ORB" in r["invalidation_reason"]

    def test_orb_low_short_band_invalidation(self):
        """ORB_LOW SHORT: 2 closes ≥ orb_low → invalidated."""
        candles = [
            _candle(1000, 98.0),
            _candle(2000, 98.0),
            _candle(3000, 98.0),
            _candle(4000, 98.0),
            _candle(5000, 98.0),
            _candle(6000, 99.5),  # inside ORB
            _candle(7000, 100.0), # inside ORB
        ]
        r = validate_sequence(
            candles, _orb(100.5, 99.0), _break(), _disp(5),
            {**_config("SHORT", 2), "level_source": "ORB_LOW"},
        )
        assert r["status"] == "INVALIDATED"
        assert "ORB" in r["invalidation_reason"]


class TestConfigurableThreshold:
    """Cases 11-12: parameterized threshold."""

    def test_threshold_1_single_close_invalidates(self):
        """level_invalidation_closes=1: one close below → invalidated."""
        candles = [
            _candle(1000, 200.0),
            _candle(2000, 200.0),
            _candle(3000, 200.0),
            _candle(4000, 200.0),
            _candle(5000, 200.0),
            _candle(6000, 199.90),  # one close below
        ]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(5),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH",
                       level_invalidation_closes=1),
        )
        assert r["status"] == "INVALIDATED"
        assert r["threshold"] == 1

    def test_threshold_3_needs_three(self):
        """level_invalidation_closes=3: two below → still valid."""
        candles = [
            _candle(1000, 200.0),
            _candle(2000, 200.0),
            _candle(3000, 200.0),
            _candle(4000, 200.0),
            _candle(5000, 200.0),
            _candle(6000, 199.90),
            _candle(7000, 199.80),  # only 2
        ]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(5),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH",
                       level_invalidation_closes=3),
        )
        assert r["status"] == "OK"

    def test_threshold_3_exactly_three_invalidates(self):
        """level_invalidation_closes=3: three consecutive → invalidated."""
        candles = [
            _candle(1000, 200.0),
            _candle(2000, 200.0),
            _candle(3000, 200.0),
            _candle(4000, 200.0),
            _candle(5000, 200.0),
            _candle(6000, 199.90),
            _candle(7000, 199.80),
            _candle(8000, 199.70),  # third
        ]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(5),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH",
                       level_invalidation_closes=3),
        )
        assert r["status"] == "INVALIDATED"
        assert r["threshold"] == 3


class TestInvalidConfig:
    """Case 13: invalid configuration values."""

    def test_threshold_zero_raises(self):
        with pytest.raises(ValueError, match=">= 1"):
            validate_sequence(
                [_candle(1000, 200.0)] * 6,
                _pd_level(200.0), _break(), _disp(5),
                _pd_config("LONG", "PREVIOUS_DAY_HIGH",
                           level_invalidation_closes=0),
            )

    def test_threshold_negative_raises(self):
        with pytest.raises(ValueError, match=">= 1"):
            validate_sequence(
                [_candle(1000, 200.0)] * 6,
                _pd_level(200.0), _break(), _disp(5),
                _pd_config("LONG", "PREVIOUS_DAY_HIGH",
                           level_invalidation_closes=-1),
            )

    def test_threshold_bool_raises(self):
        with pytest.raises(TypeError, match="bool"):
            validate_sequence(
                [_candle(1000, 200.0)] * 6,
                _pd_level(200.0), _break(), _disp(5),
                _pd_config("LONG", "PREVIOUS_DAY_HIGH",
                           level_invalidation_closes=True),
            )

    def test_threshold_float_raises(self):
        with pytest.raises(TypeError, match="int"):
            validate_sequence(
                [_candle(1000, 200.0)] * 6,
                _pd_level(200.0), _break(), _disp(5),
                _pd_config("LONG", "PREVIOUS_DAY_HIGH",
                           level_invalidation_closes=2.0),
            )


class TestUnsupportedSource:
    """Case 14: unsupported level source."""

    def test_unknown_source_not_applicable(self):
        r = validate_sequence(
            [_candle(1000, 200.0)] * 6,
            _pd_level(200.0, "PIVOT_WICK"), _break(), _disp(5),
            _pd_config("LONG", "PIVOT_WICK"),
        )
        assert r["status"] == "NOT_APPLICABLE"
        assert r["max_valid_index"] == 5


class TestOutputFields:
    """Verify new output fields present in all modes."""

    def test_line_ok_has_level_source(self):
        candles = [_candle(1000, 200.0)] * 6 + [_candle(7000, 200.10)]
        r = validate_sequence(
            candles, _pd_level(200.0), _break(), _disp(5),
            _pd_config("LONG", "PREVIOUS_DAY_HIGH"),
        )
        assert r["level_source"] == "PREVIOUS_DAY_HIGH"
        assert r["invalidation_level"] == 200.0

    def test_orb_ok_has_level_source(self):
        candles = [_candle(1000, 101.0)] * 6 + [_candle(7000, 101.0)]
        r = validate_sequence(
            candles, _orb(100.5, 99.0), _break(), _disp(5),
            {**_config("LONG", 2), "level_source": "ORB_HIGH"},
        )
        assert r["level_source"] == "ORB_HIGH"
        assert r["invalidation_level"] == {"orb_high": 100.5, "orb_low": 99.0}
