"""Tests for rejection_wall_classifier — B9.3 standalone active-wall classifier.

Tests the provisional active-wall acceptance rule: a wall is INACTIVE_ACCEPTED
if at least one candle after the wall's last contact and before the entry bar
closed strictly beyond the wall's bound.

This rule is PROVISIONAL — calibrated from SPY 2026-08-06 §16.
"""

import json
import math

import pytest
from trading_lab.contracts.enums import Direction
from trading_lab.rejection_wall_classifier import (
    ClassifiedWall,
    WallActivityStatus,
    classify_active_rejection_walls,
)
from trading_lab.rejection_wall_finder import (
    RejectionWall,
    WallContact,
    find_rejection_walls,
)

TICK = 0.01


# ── Helpers ───────────────────────────────────────────────────────────────────


def _candle(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}


def _make_wall(
    lower: int,
    upper: int,
    contact_indices: tuple[int, ...],
    extreme_ticks: int | None = None,
    rejection_wick_ratio: float = 0.30,
) -> RejectionWall:
    """Build a synthetic RejectionWall for testing."""
    ext = extreme_ticks if extreme_ticks is not None else upper
    contacts = tuple(
        WallContact(
            candle_index=ci,
            extreme_ticks=ext,
            rejection_wick_ratio=rejection_wick_ratio,
            is_rejection=rejection_wick_ratio >= 0.20,
        )
        for ci in contact_indices
    )
    rep = lower + (upper - lower) // 2
    return RejectionWall(
        lower_ticks=lower,
        upper_ticks=upper,
        representative_ticks=rep if rep >= lower else lower,
        contacts=contacts,
        contact_count=len(contacts),
        rejection_contact_count=sum(1 for c in contacts if c.is_rejection),
    )


# ── LONG basic ────────────────────────────────────────────────────────────────


class TestLongBasic:
    """Tests 1–4: core LONG classification."""

    def test_no_close_above_upper_bound_active(self):
        """Test 1: no close above upper bound → ACTIVE."""
        wall = _make_wall(10500, 10505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),    # 0
            _candle(100, 101, 99, 100),    # 1
            _candle(100, 105.05, 99, 104), # 2 — contact
            _candle(100, 105.03, 99, 104), # 3 — contact
            _candle(100, 101, 99, 104.50), # 4 — close below 105.05
            _candle(100, 101, 99, 105.00), # 5 — close exactly at 105.00, still below upper 105.05
            _candle(100, 101, 99, 100),    # 6 — entry
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=6, direction=Direction.LONG, tick_size=TICK,
        )
        assert len(result) == 1
        cw = result[0]
        assert cw.status == WallActivityStatus.ACTIVE_UNBROKEN
        assert cw.is_active is True
        assert cw.acceptance_index is None
        assert cw.acceptance_close_ticks is None
        assert cw.last_contact_index == 3
        assert cw.bound_compared == 10505

    def test_one_close_above_upper_bound_inactive(self):
        """Test 2: one close above upper bound → INACTIVE."""
        wall = _make_wall(10500, 10505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),    # 0
            _candle(100, 101, 99, 100),    # 1
            _candle(100, 105.05, 99, 104), # 2
            _candle(100, 105.03, 99, 104), # 3
            _candle(100, 106, 99, 105.10), # 4 — close at 105.10 > 105.05 ✓
            _candle(100, 101, 99, 100),    # 5
            _candle(100, 101, 99, 100),    # 6 — entry
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=6, direction=Direction.LONG, tick_size=TICK,
        )
        cw = result[0]
        assert cw.status == WallActivityStatus.INACTIVE_ACCEPTED
        assert cw.is_active is False
        assert cw.acceptance_index == 4
        assert cw.acceptance_close_ticks == 10510

    def test_close_equal_upper_bound_active(self):
        """Test 3: close exactly at upper bound → ACTIVE (strict comparison)."""
        wall = _make_wall(10500, 10505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),    # 0
            _candle(100, 101, 99, 100),    # 1
            _candle(100, 105.05, 99, 104), # 2
            _candle(100, 105.03, 99, 104), # 3
            _candle(100, 106, 99, 105.05), # 4 — close exactly 105.05 = upper bound
            _candle(100, 101, 99, 100),    # 5 — entry
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=5, direction=Direction.LONG, tick_size=TICK,
        )
        cw = result[0]
        assert cw.status == WallActivityStatus.ACTIVE_UNBROKEN
        assert cw.is_active is True

    def test_high_above_but_close_not_above_active(self):
        """Test 4: high pierces above but close stays below → ACTIVE."""
        wall = _make_wall(10500, 10505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),    # 0
            _candle(100, 101, 99, 100),    # 1
            _candle(100, 105.05, 99, 104), # 2
            _candle(100, 105.03, 99, 104), # 3
            _candle(100, 106.00, 99, 104.90),  # 4 — high 106 > 105.05 but close 104.90 < 105.05
            _candle(100, 101, 99, 100),    # 5 — entry
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=5, direction=Direction.LONG, tick_size=TICK,
        )
        assert result[0].is_active is True


# ── SHORT basic ───────────────────────────────────────────────────────────────


class TestShortBasic:
    """Tests 5–8: core SHORT classification."""

    def test_no_close_below_lower_bound_active(self):
        """Test 5: no close below lower bound → ACTIVE."""
        wall = _make_wall(9500, 9505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),   # 0
            _candle(100, 101, 99, 100),   # 1
            _candle(100, 101, 95, 96),    # 2
            _candle(100, 101, 95.03, 96), # 3
            _candle(100, 101, 95, 95.10), # 4 — close 95.10 > lower 95.00
            _candle(100, 101, 99, 100),   # 5 — entry
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=5, direction=Direction.SHORT, tick_size=TICK,
        )
        assert result[0].is_active is True
        assert result[0].bound_compared == 9500

    def test_one_close_below_lower_bound_inactive(self):
        """Test 6: one close below lower bound → INACTIVE."""
        wall = _make_wall(9500, 9505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),   # 0
            _candle(100, 101, 99, 100),   # 1
            _candle(100, 101, 95, 96),    # 2
            _candle(100, 101, 95.03, 96), # 3
            _candle(100, 101, 94, 94.90), # 4 — close 94.90 < lower 95.00 ✓
            _candle(100, 101, 99, 100),   # 5 — entry
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=5, direction=Direction.SHORT, tick_size=TICK,
        )
        cw = result[0]
        assert cw.status == WallActivityStatus.INACTIVE_ACCEPTED
        assert cw.acceptance_index == 4
        assert cw.acceptance_close_ticks == 9490

    def test_close_equal_lower_bound_active(self):
        """Test 7: close exactly at lower bound → ACTIVE."""
        wall = _make_wall(9500, 9505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),   # 0
            _candle(100, 101, 99, 100),   # 1
            _candle(100, 101, 95, 96),    # 2
            _candle(100, 101, 95.03, 96), # 3
            _candle(100, 101, 94, 95.00), # 4 — close exactly 95.00
            _candle(100, 101, 99, 100),   # 5 — entry
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=5, direction=Direction.SHORT, tick_size=TICK,
        )
        assert result[0].is_active is True

    def test_low_below_but_close_not_below_active(self):
        """Test 8: low below bound but close stays above → ACTIVE."""
        wall = _make_wall(9500, 9505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),   # 0
            _candle(100, 101, 99, 100),   # 1
            _candle(100, 101, 95, 96),    # 2
            _candle(100, 101, 95.03, 96), # 3
            _candle(100, 101, 93, 95.50), # 4 — low 93 < 95 but close 95.50 > 95
            _candle(100, 101, 99, 100),   # 5 — entry
        ]
        assert classify_active_rejection_walls(
            (wall,), candles, entry_index=5, direction=Direction.SHORT, tick_size=TICK,
        )[0].is_active is True


# ── Temporal edge cases ───────────────────────────────────────────────────────


class TestTemporalEdgeCases:
    """Tests 9–11: temporal window logic."""

    def test_acceptance_before_last_contact_does_not_count(self):
        """Test 9: close beyond bound BEFORE last contact → still ACTIVE."""
        # Contact at idx 2 and 5.  Close above at idx 3 (between contacts).
        wall = _make_wall(10500, 10505, (2, 5))
        candles = [
            _candle(100, 101, 99, 100),    # 0
            _candle(100, 101, 99, 100),    # 1
            _candle(100, 105.05, 99, 104), # 2 — first contact
            _candle(100, 106, 99, 105.20), # 3 — close above! but before last contact
            _candle(100, 101, 99, 100),    # 4
            _candle(100, 105.03, 99, 104), # 5 — last contact
            _candle(100, 101, 99, 104.50), # 6 — close below bound
            _candle(100, 101, 99, 100),    # 7 — entry
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=7, direction=Direction.LONG, tick_size=TICK,
        )
        assert result[0].is_active is True
        assert result[0].last_contact_index == 5

    def test_acceptance_on_entry_candle_does_not_count(self):
        """Test 10: close beyond bound on entry candle → still ACTIVE."""
        wall = _make_wall(10500, 10505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),    # 0
            _candle(100, 101, 99, 100),    # 1
            _candle(100, 105.05, 99, 104), # 2
            _candle(100, 105.03, 99, 104), # 3
            _candle(100, 101, 99, 104.50), # 4 — below bound
            _candle(100, 106, 99, 105.50), # 5 — entry candle closes above
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=5, direction=Direction.LONG, tick_size=TICK,
        )
        # Entry candle excluded from scan → wall is active
        assert result[0].is_active is True

    def test_first_acceptance_index_saved(self):
        """Test 11: first close beyond bound is saved as acceptance_index."""
        wall = _make_wall(10500, 10505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),    # 0
            _candle(100, 101, 99, 100),    # 1
            _candle(100, 105.05, 99, 104), # 2
            _candle(100, 105.03, 99, 104), # 3
            _candle(100, 106, 99, 105.10), # 4 — first close above
            _candle(100, 106, 99, 105.50), # 5 — also above
            _candle(100, 101, 99, 100),    # 6 — entry
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=6, direction=Direction.LONG, tick_size=TICK,
        )
        cw = result[0]
        assert cw.acceptance_index == 4  # first, not 5


# ── Multiple walls ────────────────────────────────────────────────────────────


class TestMultipleWalls:
    """Tests 12–13: multi-wall classification."""

    def test_multiple_walls_independent(self):
        """Test 12: each wall classified independently."""
        wall_a = _make_wall(10500, 10505, (2, 3))  # never broken
        wall_b = _make_wall(10400, 10405, (2, 3))  # will be broken
        candles = [
            _candle(100, 101, 99, 100),    # 0
            _candle(100, 101, 99, 100),    # 1
            _candle(100, 105.05, 99, 104), # 2
            _candle(100, 105.03, 99, 104), # 3
            _candle(100, 106, 99, 104.10), # 4 — close > 104.05 (breaks B) but < 105.05 (not A)
            _candle(100, 101, 99, 100),    # 5 — entry
        ]
        result = classify_active_rejection_walls(
            (wall_a, wall_b), candles, entry_index=5, direction=Direction.LONG, tick_size=TICK,
        )
        assert result[0].is_active is True     # wall A: active
        assert result[1].is_active is False    # wall B: broken

    def test_wall_order_does_not_affect_classification(self):
        """Test 13: reversing input order doesn't change classification."""
        wall_a = _make_wall(10500, 10505, (2, 3))
        wall_b = _make_wall(10400, 10405, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),    # 0
            _candle(100, 101, 99, 100),    # 1
            _candle(100, 105.05, 99, 104), # 2
            _candle(100, 105.03, 99, 104), # 3
            _candle(100, 106, 99, 104.10), # 4 — breaks B only
            _candle(100, 101, 99, 100),    # 5 — entry
        ]
        result_ab = classify_active_rejection_walls(
            (wall_a, wall_b), candles, entry_index=5, direction=Direction.LONG, tick_size=TICK,
        )
        result_ba = classify_active_rejection_walls(
            (wall_b, wall_a), candles, entry_index=5, direction=Direction.LONG, tick_size=TICK,
        )
        # Same walls → same results regardless of order
        assert result_ab[0].is_active is True   # A active
        assert result_ab[1].is_active is False  # B inactive
        assert result_ba[0].is_active is False  # B inactive
        assert result_ba[1].is_active is True   # A active


# ── Validation ────────────────────────────────────────────────────────────────


class TestValidation:
    """Test 14: input validation."""

    def test_invalid_direction(self):
        with pytest.raises(TypeError, match="Direction"):
            classify_active_rejection_walls((), [], 0, "LONG", TICK)

    def test_entry_index_bool(self):
        with pytest.raises(TypeError, match="entry_index"):
            classify_active_rejection_walls((), [], True, Direction.LONG, TICK)

    def test_entry_index_negative(self):
        with pytest.raises(ValueError, match="entry_index"):
            classify_active_rejection_walls((), [], -1, Direction.LONG, TICK)

    def test_entry_index_out_of_range(self):
        with pytest.raises(ValueError, match="entry_index"):
            classify_active_rejection_walls((), [_candle(1,1,1,1)], 5, Direction.LONG, TICK)

    def test_last_contact_at_entry(self):
        wall = _make_wall(10500, 10505, (3,))  # single contact at entry idx
        candles = [_candle(1,1,1,1)] * 4
        with pytest.raises(ValueError, match="last_contact_index"):
            classify_active_rejection_walls(
                (wall,), candles, entry_index=3, direction=Direction.LONG, tick_size=TICK,
            )

    def test_nan_close(self):
        wall = _make_wall(10500, 10505, (1, 2))
        candles = [
            _candle(1,1,1,1),       # 0
            _candle(1,105.05,1,1),  # 1
            _candle(1,105.03,1,1),  # 2
            _candle(1,1,1, float('nan')),  # 3 — NaN close
            _candle(1,1,1,1),       # 4 — entry
        ]
        with pytest.raises(ValueError, match="finite"):
            classify_active_rejection_walls(
                (wall,), candles, entry_index=4, direction=Direction.LONG, tick_size=TICK,
            )

    def test_inf_close(self):
        wall = _make_wall(10500, 10505, (1, 2))
        candles = [
            _candle(1,1,1,1),
            _candle(1,105.05,1,1),
            _candle(1,105.03,1,1),
            _candle(1,1,1, float('inf')),
            _candle(1,1,1,1),
        ]
        with pytest.raises(ValueError, match="finite"):
            classify_active_rejection_walls(
                (wall,), candles, entry_index=4, direction=Direction.LONG, tick_size=TICK,
            )

    def test_non_wall_type_rejected(self):
        with pytest.raises(TypeError, match="RejectionWall"):
            classify_active_rejection_walls(
                ({"fake": True},), [_candle(1,1,1,1)]*3, 2, Direction.LONG, TICK,
            )

    def test_tick_size_zero(self):
        with pytest.raises(ValueError, match="tick_size"):
            classify_active_rejection_walls((), [], 0, Direction.LONG, 0.0)


# ── JSON serialization ───────────────────────────────────────────────────────


class TestSerialization:
    """Test 15: to_dict produces valid JSON."""

    def test_to_dict_json_serializable(self):
        wall = _make_wall(10500, 10505, (2, 3))
        candles = [
            _candle(100, 101, 99, 100),
            _candle(100, 101, 99, 100),
            _candle(100, 105.05, 99, 104),
            _candle(100, 105.03, 99, 104),
            _candle(100, 106, 99, 105.10),
            _candle(100, 101, 99, 100),
        ]
        result = classify_active_rejection_walls(
            (wall,), candles, entry_index=5, direction=Direction.LONG, tick_size=TICK,
        )
        d = result[0].to_dict()
        # Must not raise
        s = json.dumps(d)
        parsed = json.loads(s)
        assert parsed["status"] == "INACTIVE_ACCEPTED"
        assert parsed["is_active"] is False
        assert parsed["acceptance_index"] == 4
        assert parsed["wall"]["contact_count"] == 2


# ── End-to-end: B9.1 → B9.3 ─────────────────────────────────────────────────


class TestEndToEnd:
    """End-to-end: detector → classifier on synthetic data."""

    def _long_rejection(self, high: float, close_below: float = 0.20):
        low = high - 0.50
        close = high - close_below
        open_ = close - 0.05
        return _candle(open_, high, low, close)

    def _filler(self, h: float = 50.0):
        return _candle(h - 0.02, h, h - 0.10, h - 0.01)

    def test_detect_then_classify(self):
        """Full pipeline: find walls → classify active status."""
        # Wall B at ~105.02-105.04 (lower, broken by bar 6 close at 105.10 → INACTIVE)
        # Wall A at ~108.02-108.04 (higher, never broken → ACTIVE)
        # Wall A contacts must close BELOW Wall B upper (105.04) so they don't break B
        candles = [
            self._filler(40),             # 0
            self._filler(41),             # 1
            self._long_rejection(105.02), # 2 — Wall B contact (close ~104.82)
            self._long_rejection(105.04), # 3 — Wall B contact (close ~104.84)
            _candle(104.50, 108.02, 104.40, 104.60),  # 4 — Wall A contact: H=108.02 but close 104.60 < 105.04
            _candle(104.50, 108.04, 104.40, 104.60),  # 5 — Wall A contact: H=108.04 but close 104.60 < 105.04
            _candle(105.00, 105.15, 104.90, 105.10),  # 6 — close 105.10 > 105.04 (breaks B) but < 108.04 (not A)
            self._filler(42),             # 7
            self._filler(43),             # 8 — entry
        ]

        # Detect
        detection = find_rejection_walls(
            candles, scan_start_index=0, scan_end_index=8,
            direction=Direction.LONG, tick_size=TICK,
        )
        assert len(detection.walls) == 2

        # Classify
        classified = classify_active_rejection_walls(
            detection.walls, candles, entry_index=8,
            direction=Direction.LONG, tick_size=TICK,
        )
        assert len(classified) == 2

        # LONG ascending: Wall B (lower ~105) first, Wall A (higher ~108) second
        assert classified[0].is_active is False  # Wall B: broken at bar 6
        assert classified[0].acceptance_index == 6
        assert classified[1].is_active is True   # Wall A: never broken
