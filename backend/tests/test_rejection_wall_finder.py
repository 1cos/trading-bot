"""Tests for rejection_wall_finder — B9.1 standalone detector.

Synthetic test matrix covering:
- Core LONG / SHORT symmetry
- Hybrid contact model (rejection + stall)
- Tolerance / span-based clustering
- Wick geometry edge cases
- Window boundary handling
- Optional spatial bounds
"""

import pytest
from trading_lab.contracts.enums import Direction
from trading_lab.rejection_wall_finder import (
    RejectionWall,
    RejectionWallResult,
    WallContact,
    find_rejection_walls,
)

TICK = 0.01


# ── Helpers ───────────────────────────────────────────────────────────────────


def _candle(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}


def _long_rejection(high: float, close_below: float = 0.20) -> dict:
    """Bullish bar wicking up to *high* with significant upper wick."""
    low = high - 0.50
    close = high - close_below
    open_ = close - 0.05
    return _candle(open_, high, low, close)


def _long_stall(high: float) -> dict:
    """Bullish bar closing AT the high — no upper wick."""
    low = high - 0.40
    open_ = low + 0.02
    return _candle(open_, high, low, high)


def _short_rejection(low: float, close_above: float = 0.20) -> dict:
    """Bearish bar wicking down to *low* with significant lower wick."""
    high = low + 0.50
    close = low + close_above
    open_ = close + 0.05
    return _candle(open_, high, low, close)


def _short_stall(low: float) -> dict:
    """Bearish bar closing AT the low — no lower wick."""
    high = low + 0.40
    open_ = high - 0.02
    return _candle(open_, high, low, low)


def _filler(high: float = 50.00) -> dict:
    """Neutral candle that should not contribute to any wall.

    Uses a unique low high value far from test prices, and closes
    near the high to avoid generating wick ratios ≥ 20%.
    """
    return _candle(high - 0.02, high, high - 0.10, high - 0.01)


# ── Core LONG ─────────────────────────────────────────────────────────────────


class TestCoreLong:
    """Tests 1–6: core LONG detection."""

    def test_two_clustered_highs_one_rejection(self):
        """Test 1: two clustered highs, one genuine rejection → wall."""
        candles = [
            _filler(50.00),              # 0 — far from test prices
            _long_rejection(105.03),     # 1 — rejection at 105.03
            _long_stall(105.05),         # 2 — stall at 105.05
            _filler(51.00),              # 3 — far from test prices
        ]
        result = find_rejection_walls(
            candles, 0, 4, Direction.LONG, TICK,
        )
        assert len(result.walls) == 1
        wall = result.walls[0]
        assert wall.contact_count == 2
        assert wall.rejection_contact_count == 1
        assert wall.lower_ticks == 10503
        assert wall.upper_ticks == 10505
        assert any(c.is_rejection for c in wall.contacts)

    def test_two_clustered_highs_both_rejections(self):
        """Test 2: two rejections → wall."""
        candles = [
            _long_rejection(105.03),     # 0
            _long_rejection(105.04),     # 1
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
        )
        assert len(result.walls) == 1
        wall = result.walls[0]
        assert wall.contact_count == 2
        assert wall.rejection_contact_count == 2

    def test_three_contacts_two_stalls_one_rejection(self):
        """Test 3: hybrid — two stalls + one rejection → wall."""
        candles = [
            _long_stall(105.00),         # 0 — stall
            _long_rejection(105.02),     # 1 — rejection
            _long_stall(105.03),         # 2 — stall
        ]
        result = find_rejection_walls(
            candles, 0, 3, Direction.LONG, TICK,
        )
        assert len(result.walls) == 1
        wall = result.walls[0]
        assert wall.contact_count == 3
        assert wall.rejection_contact_count == 1

    def test_four_contacts_canonical_wall2(self):
        """Test 4: four contacts matching canonical Wall #2 geometry."""
        # 771.52, 771.52, 771.52, 771.53 all with UW ≥ 32%
        candles = [
            _candle(771.43, 771.52, 771.30, 771.40),  # UW=0.12/0.22=55%
            _candle(771.41, 771.52, 771.25, 771.31),  # UW=0.11/0.27=41%
            _candle(771.31, 771.52, 771.14, 771.34),  # UW=0.18/0.38=47%
            _candle(771.43, 771.53, 771.22, 771.32),  # UW=0.10/0.31=32%
        ]
        result = find_rejection_walls(
            candles, 0, 4, Direction.LONG, TICK,
        )
        assert len(result.walls) == 1
        wall = result.walls[0]
        assert wall.contact_count == 4
        assert wall.rejection_contact_count == 4
        assert wall.lower_ticks == 77152
        assert wall.upper_ticks == 77153
        # Span is 1 tick — well within tolerance of 5
        assert wall.upper_ticks - wall.lower_ticks == 1

    def test_two_stalls_zero_rejections_no_wall(self):
        """Test 5: two stalls, zero rejections → NO wall."""
        candles = [
            _long_stall(105.00),   # 0
            _long_stall(105.02),   # 1
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
        )
        assert len(result.walls) == 0

    def test_single_rejection_no_wall(self):
        """Test 6: only one contact total → NO wall (min_contacts=2)."""
        candles = [
            _filler(50.00),              # 0
            _long_rejection(105.03),     # 1
            _filler(51.00),              # 2
        ]
        result = find_rejection_walls(
            candles, 0, 3, Direction.LONG, TICK,
        )
        assert len(result.walls) == 0


# ── Core SHORT ────────────────────────────────────────────────────────────────


class TestCoreShort:
    """Mirror of LONG tests using lows / lower wicks."""

    def test_two_clustered_lows_one_rejection(self):
        candles = [
            _filler(50.00),
            _short_rejection(95.03),
            _short_stall(95.05),
            _filler(51.00),
        ]
        result = find_rejection_walls(
            candles, 0, 4, Direction.SHORT, TICK,
        )
        assert len(result.walls) == 1
        wall = result.walls[0]
        assert wall.contact_count == 2
        assert wall.rejection_contact_count == 1
        # For SHORT, extreme is low; lower price = more extreme
        assert wall.lower_ticks == 9503
        assert wall.upper_ticks == 9505

    def test_two_short_rejections(self):
        candles = [
            _short_rejection(95.03),
            _short_rejection(95.04),
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.SHORT, TICK,
        )
        assert len(result.walls) == 1
        assert result.walls[0].rejection_contact_count == 2

    def test_three_contacts_hybrid_short(self):
        candles = [
            _short_stall(95.00),
            _short_rejection(95.02),
            _short_stall(95.03),
        ]
        result = find_rejection_walls(
            candles, 0, 3, Direction.SHORT, TICK,
        )
        assert len(result.walls) == 1
        assert result.walls[0].contact_count == 3
        assert result.walls[0].rejection_contact_count == 1

    def test_two_short_stalls_no_wall(self):
        candles = [
            _short_stall(95.00),
            _short_stall(95.02),
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.SHORT, TICK,
        )
        assert len(result.walls) == 0

    def test_short_sort_descending(self):
        """SHORT walls sorted descending (nearest wall = highest price first)."""
        candles = [
            _short_rejection(95.00),
            _short_rejection(95.02),
            _short_rejection(94.50),
            _short_rejection(94.52),
        ]
        result = find_rejection_walls(
            candles, 0, 4, Direction.SHORT, TICK,
        )
        assert len(result.walls) == 2
        # SHORT: descending by representative_ticks
        assert result.walls[0].representative_ticks > result.walls[1].representative_ticks


# ── Tolerance ─────────────────────────────────────────────────────────────────


class TestTolerance:
    """Tests 7–10: tolerance / clustering edge cases."""

    def test_contacts_exactly_at_tolerance(self):
        """Test 7: spread == tolerance → same cluster."""
        candles = [
            _long_rejection(105.00),
            _long_rejection(105.05),   # exactly 5 ticks apart
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
            cluster_tolerance_ticks=5,
        )
        assert len(result.walls) == 1
        assert result.walls[0].contact_count == 2

    def test_contacts_one_tick_beyond_tolerance(self):
        """Test 8: spread == tolerance + 1 → separate."""
        candles = [
            _long_rejection(105.00),
            _long_rejection(105.06),   # 6 ticks apart, tolerance=5
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
            cluster_tolerance_ticks=5,
        )
        # Each has only 1 contact → below min_contacts=2 → no walls
        assert len(result.walls) == 0

    def test_transitive_chain_trap(self):
        """Test 9: A→B→C where A-B and B-C fit but A-C does not.

        extremes: 100, 104, 108 with tolerance=5
        100-104 span=4 fits, 104-108 span=4 fits,
        but 100-108 span=8 does NOT fit.
        Must NOT merge all three.
        """
        candles = [
            _long_rejection(100.00),   # 10000
            _long_rejection(100.04),   # 10004
            _long_rejection(100.08),   # 10008
        ]
        result = find_rejection_walls(
            candles, 0, 3, Direction.LONG, TICK,
            cluster_tolerance_ticks=5,
        )
        # Best window is either (100.00, 100.04) or (100.04, 100.08)
        # Both have 2 contacts — the algorithm picks the first valid one
        assert len(result.walls) == 1
        wall = result.walls[0]
        assert wall.contact_count == 2
        span = wall.upper_ticks - wall.lower_ticks
        assert span <= 5

    def test_multiple_distinct_walls(self):
        """Test 10: two distinct walls → returned separately."""
        candles = [
            _long_rejection(105.00),   # Wall A
            _long_rejection(105.02),
            _filler(50.00),
            _long_rejection(106.00),   # Wall B
            _long_rejection(106.03),
        ]
        result = find_rejection_walls(
            candles, 0, 5, Direction.LONG, TICK,
        )
        assert len(result.walls) == 2
        # LONG: ascending sort
        assert result.walls[0].representative_ticks < result.walls[1].representative_ticks


# ── Wick geometry ─────────────────────────────────────────────────────────────


class TestWickGeometry:
    """Tests 11–14: wick ratio edge cases."""

    def test_wick_exactly_at_threshold(self):
        """Test 11: wick ratio = 0.20 exactly → qualifies."""
        # range=0.50, upper_wick=0.10 → ratio=0.20
        candles = [
            _candle(105.00, 105.50, 105.00, 105.40),  # UW=0.10/0.50=0.20
            _candle(104.98, 105.52, 104.98, 105.32),   # UW=0.20/0.54≈0.37
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
        )
        assert len(result.walls) == 1
        # First contact should be classified as rejection
        contacts_by_idx = {c.candle_index: c for c in result.walls[0].contacts}
        assert contacts_by_idx[0].is_rejection is True

    def test_wick_just_below_threshold(self):
        """Test 12: wick ratio just below 0.20 → not a rejection."""
        # range=0.52, upper_wick=0.10 → ratio=0.192 < 0.20
        candles = [
            _candle(105.00, 105.52, 105.00, 105.42),  # UW=0.10/0.52=0.192
            _candle(104.95, 105.51, 104.95, 105.41),   # UW=0.10/0.56=0.179
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
        )
        # Both contacts are NOT rejections → 0 rejection contacts → no wall
        assert len(result.walls) == 0

    def test_zero_range_candle(self):
        """Test 13: zero-range candle → wick ratio = 0.0, safe handling."""
        candles = [
            _candle(105.00, 105.00, 105.00, 105.00),   # zero range
            _long_rejection(105.02),                     # normal rejection
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
        )
        # Zero-range bar has wick_ratio=0.0, not a rejection.
        # Only 1 rejection contact out of 2 → hybrid model: qualifies
        assert len(result.walls) == 1
        wall = result.walls[0]
        assert wall.contact_count == 2
        assert wall.rejection_contact_count == 1
        contacts_by_idx = {c.candle_index: c for c in wall.contacts}
        assert contacts_by_idx[0].rejection_wick_ratio == 0.0
        assert contacts_by_idx[0].is_rejection is False

    def test_full_body_stall_reinforces_wall(self):
        """Test 14: full-body stall reinforces wall when rejection present."""
        candles = [
            _long_stall(105.02),         # 0: stall at high
            _long_rejection(105.03),     # 1: genuine rejection
            _long_stall(105.04),         # 2: another stall
        ]
        result = find_rejection_walls(
            candles, 0, 3, Direction.LONG, TICK,
        )
        assert len(result.walls) == 1
        assert result.walls[0].contact_count == 3
        assert result.walls[0].rejection_contact_count == 1


# ── Window handling ───────────────────────────────────────────────────────────


class TestWindowHandling:
    """Tests 15–17: scan window boundaries."""

    def test_candle_before_scan_start_ignored(self):
        """Test 15: candle before scan_start_index → ignored."""
        candles = [
            _long_rejection(105.00),   # 0 — before window
            _long_rejection(105.02),   # 1 — in window
            _filler(50.00),            # 2
            _filler(51.00),            # 3
        ]
        result = find_rejection_walls(
            candles, 1, 4, Direction.LONG, TICK,
        )
        # Only idx=1 is in window; not enough contacts
        assert len(result.walls) == 0

    def test_candle_at_scan_end_ignored(self):
        """Test 16: candle at scan_end_index → ignored (exclusive)."""
        candles = [
            _long_rejection(105.00),   # 0
            _filler(50.00),            # 1
            _long_rejection(105.02),   # 2 — at scan_end, excluded
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
        )
        # Only idx=0 in window
        assert len(result.walls) == 0

    def test_empty_window(self):
        """Test 17a: start == end → empty window → no walls."""
        candles = [_filler(50.00)]
        result = find_rejection_walls(
            candles, 0, 0, Direction.LONG, TICK,
        )
        assert len(result.walls) == 0

    def test_invalid_range_end_before_start(self):
        """Test 17b: end < start → ValueError."""
        with pytest.raises(ValueError, match="scan_end_index"):
            find_rejection_walls(
                [_filler(50.00)], 3, 1, Direction.LONG, TICK,
            )


# ── Spatial bounds ────────────────────────────────────────────────────────────


class TestSpatialBounds:
    """Tests 18–20: optional spatial price bounds."""

    def test_wall_below_lower_bound_excluded(self):
        """Test 18: wall below min_price_exclusive → excluded."""
        candles = [
            _long_rejection(105.00),
            _long_rejection(105.02),
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
            min_price_exclusive=10510,  # 105.10 in ticks
        )
        assert len(result.walls) == 0

    def test_wall_above_upper_bound_excluded(self):
        """Test 19: wall above max_price_exclusive → excluded."""
        candles = [
            _long_rejection(105.00),
            _long_rejection(105.02),
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
            max_price_exclusive=10499,  # below 105.00
        )
        assert len(result.walls) == 0

    def test_wall_exactly_on_exclusive_bound_excluded(self):
        """Test 20: extreme exactly on exclusive bound → excluded."""
        candles = [
            _long_rejection(105.00),   # extreme = 10500
            _long_rejection(105.02),
        ]
        # min_price_exclusive=10500 → extreme 10500 is NOT > 10500
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
            min_price_exclusive=10500,
        )
        # One contact excluded → only 1 remains → no wall
        assert len(result.walls) == 1 or len(result.walls) == 0
        # Actually: 10500 is NOT > 10500, so excluded. 10502 IS > 10500.
        # Only 1 contact remains → no wall
        assert len(result.walls) == 0

    def test_bounds_filter_keeps_valid_wall(self):
        """Bounds that encompass the wall → wall kept."""
        candles = [
            _long_rejection(105.00),
            _long_rejection(105.02),
            _long_rejection(106.00),
            _long_rejection(106.02),
        ]
        result = find_rejection_walls(
            candles, 0, 4, Direction.LONG, TICK,
            min_price_exclusive=10499,
            max_price_exclusive=10510,
        )
        assert len(result.walls) == 1
        assert result.walls[0].lower_ticks == 10500


# ── Validation ────────────────────────────────────────────────────────────────


class TestValidation:
    """Parameter validation edge cases."""

    def test_direction_not_string(self):
        with pytest.raises(TypeError, match="Direction"):
            find_rejection_walls([], 0, 0, "LONG", TICK)

    def test_min_contacts_zero(self):
        with pytest.raises(ValueError, match="min_contacts"):
            find_rejection_walls(
                [], 0, 0, Direction.LONG, TICK, min_contacts=0,
            )

    def test_min_rejection_exceeds_min_contacts(self):
        with pytest.raises(ValueError, match="min_rejection_contacts"):
            find_rejection_walls(
                [], 0, 0, Direction.LONG, TICK,
                min_contacts=2, min_rejection_contacts=3,
            )

    def test_tick_size_zero(self):
        with pytest.raises(ValueError, match="tick_size"):
            find_rejection_walls(
                [], 0, 0, Direction.LONG, 0.0,
            )

    def test_tolerance_zero(self):
        with pytest.raises(ValueError, match="cluster_tolerance_ticks"):
            find_rejection_walls(
                [], 0, 0, Direction.LONG, TICK,
                cluster_tolerance_ticks=0,
            )


# ── Representative price ──────────────────────────────────────────────────────


class TestRepresentativePrice:
    """Verify median convention for representative_ticks."""

    def test_odd_count_median(self):
        """Three contacts → median is the middle value."""
        candles = [
            _long_rejection(105.00),
            _long_rejection(105.02),
            _long_rejection(105.04),
        ]
        result = find_rejection_walls(
            candles, 0, 3, Direction.LONG, TICK,
        )
        assert len(result.walls) == 1
        assert result.walls[0].representative_ticks == 10502

    def test_even_count_lower_median(self):
        """Four contacts → lower median (B6 convention)."""
        candles = [
            _long_rejection(105.00),
            _long_rejection(105.02),
            _long_rejection(105.03),
            _long_rejection(105.04),
        ]
        result = find_rejection_walls(
            candles, 0, 4, Direction.LONG, TICK,
        )
        assert len(result.walls) == 1
        # Sorted extremes: 10500, 10502, 10503, 10504
        # Lower median = 10502
        assert result.walls[0].representative_ticks == 10502

    def test_two_contacts_lower_median(self):
        """Two contacts → lower of the two."""
        candles = [
            _long_rejection(105.00),
            _long_rejection(105.04),
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
        )
        assert len(result.walls) == 1
        assert result.walls[0].representative_ticks == 10500


# ── Result contract ───────────────────────────────────────────────────────────


class TestResultContract:
    """Verify result shape and metadata."""

    def test_result_fields(self):
        candles = [
            _long_rejection(105.00),
            _long_rejection(105.02),
        ]
        result = find_rejection_walls(
            candles, 0, 2, Direction.LONG, TICK,
        )
        assert isinstance(result, RejectionWallResult)
        assert result.scan_start_index == 0
        assert result.scan_end_index == 2
        assert result.direction == Direction.LONG

    def test_wall_contacts_sorted_temporally(self):
        """Contacts within a wall are sorted by candle_index."""
        candles = [
            _long_rejection(105.04),   # 0 — higher extreme but first temporally
            _long_rejection(105.00),   # 1
            _long_rejection(105.02),   # 2
        ]
        result = find_rejection_walls(
            candles, 0, 3, Direction.LONG, TICK,
        )
        assert len(result.walls) == 1
        indices = [c.candle_index for c in result.walls[0].contacts]
        assert indices == sorted(indices)

    def test_long_walls_sorted_ascending(self):
        """LONG: walls sorted ascending by representative_ticks."""
        candles = [
            _long_rejection(106.00),
            _long_rejection(106.02),
            _long_rejection(105.00),
            _long_rejection(105.02),
        ]
        result = find_rejection_walls(
            candles, 0, 4, Direction.LONG, TICK,
        )
        assert len(result.walls) == 2
        assert result.walls[0].representative_ticks < result.walls[1].representative_ticks
