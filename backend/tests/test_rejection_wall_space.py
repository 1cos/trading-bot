"""Tests for rejection_wall_space — B9.4 standalone space analysis.

Quantitative diagnostics only — no strategic decisions.
"""

import json
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
from trading_lab.rejection_wall_space import (
    AnalyzedWall,
    WallGeometryStatus,
    WallSpaceResult,
    analyze_rejection_wall_space,
)

TICK = 0.01


# ── Helpers ───────────────────────────────────────────────────────────────────


def _candle(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}


def _make_wall(lower: int, upper: int, contacts: tuple[int, ...],
               wick_ratio: float = 0.30) -> RejectionWall:
    cs = tuple(
        WallContact(ci, upper, wick_ratio, wick_ratio >= 0.20)
        for ci in contacts
    )
    return RejectionWall(
        lower_ticks=lower, upper_ticks=upper,
        representative_ticks=lower + (upper - lower) // 2,
        contacts=cs, contact_count=len(cs),
        rejection_contact_count=sum(1 for c in cs if c.is_rejection),
    )


def _active(wall: RejectionWall, direction: Direction = Direction.LONG,
            entry_index: int = 99) -> ClassifiedWall:
    return ClassifiedWall(
        wall=wall, status=WallActivityStatus.ACTIVE_UNBROKEN,
        is_active=True,
        last_contact_index=max(c.candle_index for c in wall.contacts),
        entry_index=entry_index, direction=direction,
        bound_compared=wall.upper_ticks if direction == Direction.LONG else wall.lower_ticks,
        acceptance_index=None, acceptance_close_ticks=None,
    )


def _inactive(wall: RejectionWall, direction: Direction = Direction.LONG,
              entry_index: int = 99, acc_idx: int = 50,
              acc_close: int = 0) -> ClassifiedWall:
    return ClassifiedWall(
        wall=wall, status=WallActivityStatus.INACTIVE_ACCEPTED,
        is_active=False,
        last_contact_index=max(c.candle_index for c in wall.contacts),
        entry_index=entry_index, direction=direction,
        bound_compared=wall.upper_ticks if direction == Direction.LONG else wall.lower_ticks,
        acceptance_index=acc_idx, acceptance_close_ticks=acc_close,
    )


# ── Tests 1–2: LONG/SHORT wall between entry and target ──────────────────────


class TestBetweenEntryAndTarget:

    def test_long_wall_between(self):
        """Test 1: LONG wall between entry and target."""
        wall = _make_wall(10520, 10525, (5, 6))
        cw = _active(wall)
        result = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        aw = result.walls[0]
        assert aw.geometry == WallGeometryStatus.BETWEEN_ENTRY_AND_TARGET
        assert aw.near_bound_ticks == 10520  # LONG uses lower_ticks
        assert aw.far_bound_ticks == 10525
        assert aw.distance_ticks == 20  # 10520 - 10500
        assert aw.is_ahead is True
        assert aw.is_between_entry_and_target is True

    def test_short_wall_between(self):
        """Test 2: SHORT wall between entry and target."""
        wall = _make_wall(9470, 9480, (5, 6))
        cw = _active(wall, Direction.SHORT)
        result = analyze_rejection_wall_space(
            (cw,), Direction.SHORT,
            entry_ticks=9500, stop_ticks=9550, target_ticks=9400,
        )
        aw = result.walls[0]
        assert aw.geometry == WallGeometryStatus.BETWEEN_ENTRY_AND_TARGET
        assert aw.near_bound_ticks == 9480  # SHORT uses upper_ticks
        assert aw.far_bound_ticks == 9470
        assert aw.distance_ticks == 20  # 9500 - 9480
        assert aw.is_ahead is True


# ── Tests 3–4: wall behind entry ──────────────────────────────────────────────


class TestBehindEntry:

    def test_long_wall_behind(self):
        """Test 3: LONG wall behind entry."""
        wall = _make_wall(10480, 10490, (5, 6))
        cw = _active(wall)
        result = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        aw = result.walls[0]
        assert aw.geometry == WallGeometryStatus.BEHIND_ENTRY
        assert aw.distance_ticks == -20  # 10480 - 10500
        assert aw.is_ahead is False

    def test_short_wall_behind(self):
        """Test 4: SHORT wall behind entry."""
        wall = _make_wall(9510, 9520, (5, 6))
        cw = _active(wall, Direction.SHORT)
        result = analyze_rejection_wall_space(
            (cw,), Direction.SHORT,
            entry_ticks=9500, stop_ticks=9550, target_ticks=9400,
        )
        aw = result.walls[0]
        assert aw.geometry == WallGeometryStatus.BEHIND_ENTRY
        assert aw.distance_ticks == -20  # 9500 - 9520


# ── Tests 5–7: boundary cases ────────────────────────────────────────────────


class TestBoundary:

    def test_wall_at_entry(self):
        """Test 5: near bound == entry."""
        wall = _make_wall(10500, 10505, (5, 6))
        cw = _active(wall)
        result = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert result.walls[0].geometry == WallGeometryStatus.AT_ENTRY
        assert result.walls[0].distance_ticks == 0

    def test_wall_at_target(self):
        """Test 6: near bound == target."""
        wall = _make_wall(10600, 10605, (5, 6))
        cw = _active(wall)
        result = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert result.walls[0].geometry == WallGeometryStatus.AT_TARGET

    def test_wall_beyond_target(self):
        """Test 7: near bound beyond target."""
        wall = _make_wall(10700, 10705, (5, 6))
        cw = _active(wall)
        result = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert result.walls[0].geometry == WallGeometryStatus.BEYOND_TARGET


# ── Tests 8–9: near bound direction ──────────────────────────────────────────


class TestNearBound:

    def test_long_uses_lower_bound(self):
        """Test 8: LONG near bound = wall.lower_ticks."""
        wall = _make_wall(10520, 10530, (5, 6))
        cw = _active(wall)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.walls[0].near_bound_ticks == 10520

    def test_short_uses_upper_bound(self):
        """Test 9: SHORT near bound = wall.upper_ticks."""
        wall = _make_wall(9470, 9480, (5, 6))
        cw = _active(wall, Direction.SHORT)
        r = analyze_rejection_wall_space(
            (cw,), Direction.SHORT,
            entry_ticks=9500, stop_ticks=9550, target_ticks=9400,
        )
        assert r.walls[0].near_bound_ticks == 9480


# ── Tests 10–14: distance and R calculations ─────────────────────────────────


class TestDistanceCalc:

    def test_distance_ticks_correct(self):
        """Test 10: distance ticks = near_bound - entry (LONG)."""
        wall = _make_wall(10526, 10530, (5, 6))
        cw = _active(wall)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.walls[0].distance_ticks == 26

    def test_distance_r_correct(self):
        """Test 11: distance R = distance_ticks / risk_ticks."""
        wall = _make_wall(10526, 10530, (5, 6))
        cw = _active(wall)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        # risk = 50, distance = 26
        assert r.walls[0].distance_r == 26 / 50
        assert r.walls[0].risk_ticks == 50

    def test_wall_within_1r(self):
        """Test 12: wall at 0.5R → is_within_1r True."""
        wall = _make_wall(10525, 10530, (5, 6))
        cw = _active(wall)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.walls[0].distance_ticks == 25  # 0.5R
        assert r.walls[0].is_within_1r is True
        assert r.has_active_within_1r is True

    def test_wall_exactly_1r(self):
        """Test 13: wall at exactly 1R → is_within_1r True (<=)."""
        wall = _make_wall(10550, 10555, (5, 6))
        cw = _active(wall)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.walls[0].distance_ticks == 50  # exactly 1R
        assert r.walls[0].is_within_1r is True

    def test_wall_beyond_1r(self):
        """Test 14: wall at 1.2R → is_within_1r False."""
        wall = _make_wall(10560, 10565, (5, 6))
        cw = _active(wall)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.walls[0].distance_ticks == 60  # 1.2R
        assert r.walls[0].is_within_1r is False


# ── Tests 15–16: multiple walls and inactive preservation ────────────────────


class TestMultipleWalls:

    def test_disordered_input_nearest_correct(self):
        """Test 15: walls in non-sorted order → nearest is correct."""
        w_far = _make_wall(10580, 10585, (5, 6))
        w_near = _make_wall(10520, 10525, (3, 4))
        cw_far = _active(w_far)
        cw_near = _active(w_near)
        r = analyze_rejection_wall_space(
            (cw_far, cw_near), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.nearest_active_ahead is not None
        assert r.nearest_active_ahead.distance_ticks == 20  # w_near
        assert r.nearest_distance_ticks == 20

    def test_inactive_preserved_not_counted(self):
        """Test 16: inactive wall in output but not counted as obstacle."""
        w_active = _make_wall(10520, 10525, (5, 6))
        w_inactive = _make_wall(10510, 10515, (3, 4))
        cw_a = _active(w_active)
        cw_i = _inactive(w_inactive)
        r = analyze_rejection_wall_space(
            (cw_a, cw_i), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.total_walls == 2
        assert r.active_wall_count == 1
        assert r.active_between_entry_and_target == 1
        # Inactive wall is still in the output
        assert len(r.walls) == 2


# ── Tests 17–20: aggregate edge cases ────────────────────────────────────────


class TestAggregates:

    def test_no_active_walls(self):
        """Test 17: all walls inactive → empty aggregates."""
        w = _make_wall(10520, 10525, (5, 6))
        cw = _inactive(w)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.active_wall_count == 0
        assert r.nearest_active_ahead is None
        assert r.nearest_distance_ticks is None
        assert r.target_clear is True

    def test_no_active_between(self):
        """Test 18: active wall exists but behind entry → none between."""
        w = _make_wall(10480, 10490, (5, 6))
        cw = _active(w)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.active_between_entry_and_target == 0
        assert r.target_clear is True

    def test_target_clear(self):
        """Test 19: no wall between entry and target → target_clear."""
        r = analyze_rejection_wall_space(
            (), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.target_clear is True

    def test_target_not_clear(self):
        """Test 20: active wall between → target not clear."""
        w = _make_wall(10520, 10525, (5, 6))
        cw = _active(w)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.target_clear is False


# ── Test 21: LONG/SHORT symmetry ─────────────────────────────────────────────


class TestSymmetry:

    def test_long_short_symmetric(self):
        """Test 21: same geometry produces same distance in both directions."""
        # LONG: entry 100, stop 95, target 110, wall at 103-105
        w_long = _make_wall(10300, 10500, (5, 6))
        cw_long = _active(w_long)
        r_long = analyze_rejection_wall_space(
            (cw_long,), Direction.LONG,
            entry_ticks=10000, stop_ticks=9500, target_ticks=11000,
        )

        # SHORT: entry 100, stop 105, target 90, wall at 95-97 (mirror)
        w_short = _make_wall(9500, 9700, (5, 6))
        cw_short = _active(w_short, Direction.SHORT)
        r_short = analyze_rejection_wall_space(
            (cw_short,), Direction.SHORT,
            entry_ticks=10000, stop_ticks=10500, target_ticks=9000,
        )

        # Both walls at 300 ticks from entry, 0.6R
        assert r_long.walls[0].distance_ticks == 300
        assert r_short.walls[0].distance_ticks == 300
        assert r_long.walls[0].distance_r == r_short.walls[0].distance_r


# ── Test 22: validation ──────────────────────────────────────────────────────


class TestValidation:

    def test_invalid_direction(self):
        with pytest.raises(TypeError, match="Direction"):
            analyze_rejection_wall_space((), "LONG", 100, 90, 110)

    def test_entry_bool(self):
        with pytest.raises(TypeError, match="entry_ticks"):
            analyze_rejection_wall_space((), Direction.LONG, True, 90, 110)

    def test_stop_bool(self):
        with pytest.raises(TypeError, match="stop_ticks"):
            analyze_rejection_wall_space((), Direction.LONG, 100, True, 110)

    def test_long_stop_above_entry(self):
        with pytest.raises(ValueError, match="stop_ticks"):
            analyze_rejection_wall_space((), Direction.LONG, 100, 110, 120)

    def test_long_target_below_entry(self):
        with pytest.raises(ValueError, match="target_ticks"):
            analyze_rejection_wall_space((), Direction.LONG, 100, 90, 90)

    def test_short_stop_below_entry(self):
        with pytest.raises(ValueError, match="stop_ticks"):
            analyze_rejection_wall_space((), Direction.SHORT, 100, 90, 80)

    def test_short_target_above_entry(self):
        with pytest.raises(ValueError, match="target_ticks"):
            analyze_rejection_wall_space((), Direction.SHORT, 100, 110, 110)

    def test_entry_equals_stop(self):
        with pytest.raises(ValueError, match="stop_ticks"):
            analyze_rejection_wall_space((), Direction.LONG, 100, 100, 110)

    def test_non_classified_wall(self):
        w = _make_wall(10520, 10525, (5, 6))
        with pytest.raises(TypeError, match="ClassifiedWall"):
            analyze_rejection_wall_space((w,), Direction.LONG, 100, 90, 110)


# ── Tests 23–24: serialization and precision ─────────────────────────────────


class TestSerialization:

    def test_json_serializable(self):
        """Test 23: full result is JSON-serializable."""
        w = _make_wall(10526, 10530, (5, 6))
        cw = _active(w)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        d = r.to_dict()
        s = json.dumps(d)  # must not raise
        parsed = json.loads(s)
        assert parsed["active_wall_count"] == 1
        assert parsed["nearest_distance_ticks"] == 26

    def test_no_internal_rounding(self):
        """Test 24: distance_r preserves full precision."""
        # distance=26, risk=50 → 0.52 exactly
        w = _make_wall(10526, 10530, (5, 6))
        cw = _active(w)
        r = analyze_rejection_wall_space(
            (cw,), Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
        )
        assert r.walls[0].distance_r == 0.52  # 26/50 exactly


# ── End-to-end: B9.1 → B9.3 → B9.4 ──────────────────────────────────────────


class TestEndToEnd:

    def test_full_pipeline_synthetic(self):
        """End-to-end: detect → classify → analyze on synthetic data."""
        def _rej(high, close_below=0.20):
            return _candle(high - 0.55, high, high - 0.50, high - close_below)

        def _filler(h=50.0):
            return _candle(h - 0.02, h, h - 0.10, h - 0.01)

        candles = [
            _filler(40),        # 0
            _filler(41),        # 1
            _rej(105.52),       # 2 — Wall A contact (H=105.52, C=105.32)
            _rej(105.54),       # 3 — Wall A contact
            _rej(108.02),       # 4 — Wall B contact (C=107.82)
            _rej(108.04),       # 5 — Wall B contact (C=107.84)
            _candle(105, 105.6, 104.9, 105.55),  # 6 — breaks A (C=105.55 > 105.54)
            _filler(42),        # 7
            _filler(43),        # 8 — entry candle
        ]

        # Detect
        detection = find_rejection_walls(
            candles, 0, 8, Direction.LONG, TICK,
        )
        assert len(detection.walls) >= 2

        # Classify
        classified = classify_active_rejection_walls(
            detection.walls, candles, entry_index=8,
            direction=Direction.LONG, tick_size=TICK,
        )

        # Analyze: entry at close of bar 8 (43 - 0.01 = 42.99) — but use tick values
        # Use simple trade: entry=10500, stop=10450, target=10600
        # Actually, let's use the wall bounds for a realistic scenario
        # Wall A broken → inactive. Wall B active.
        # Find the active wall
        active_walls = [cw for cw in classified if cw.is_active]
        assert len(active_walls) >= 1

        result = analyze_rejection_wall_space(
            classified, Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=11000,
        )

        # At least one active wall should be between entry and target
        assert result.active_wall_count >= 1
        assert result.total_walls == len(classified)
        # The result should be JSON-serializable
        json.dumps(result.to_dict())
