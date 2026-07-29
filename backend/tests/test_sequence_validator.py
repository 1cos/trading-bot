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
