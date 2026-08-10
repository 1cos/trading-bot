"""Tests for trade_space_grader — B10 standalone prototype.

The grader is descriptive only — it does not modify trade eligibility.
"""

import json
import pytest
from trading_lab.contracts.enums import Direction
from trading_lab.rejection_wall_classifier import (
    ClassifiedWall,
    WallActivityStatus,
)
from trading_lab.rejection_wall_finder import RejectionWall, WallContact
from trading_lab.rejection_wall_space import (
    WallSpaceResult,
    analyze_rejection_wall_space,
)
from trading_lab.trade_space_grader import (
    TradeSpaceGrade,
    TradeSpaceGradeResult,
    TradeSpaceReason,
    grade_trade_space,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


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


def _active(wall: RejectionWall, direction: Direction = Direction.LONG) -> ClassifiedWall:
    return ClassifiedWall(
        wall=wall, status=WallActivityStatus.ACTIVE_UNBROKEN,
        is_active=True,
        last_contact_index=max(c.candle_index for c in wall.contacts),
        entry_index=99, direction=direction,
        bound_compared=wall.upper_ticks if direction == Direction.LONG else wall.lower_ticks,
        acceptance_index=None, acceptance_close_ticks=None,
    )


def _inactive(wall: RejectionWall, direction: Direction = Direction.LONG) -> ClassifiedWall:
    return ClassifiedWall(
        wall=wall, status=WallActivityStatus.INACTIVE_ACCEPTED,
        is_active=False,
        last_contact_index=max(c.candle_index for c in wall.contacts),
        entry_index=99, direction=direction,
        bound_compared=wall.upper_ticks if direction == Direction.LONG else wall.lower_ticks,
        acceptance_index=50, acceptance_close_ticks=0,
    )


def _space(classified_walls, direction, entry, stop, target):
    """Build WallSpaceResult from classified walls + trade params."""
    return analyze_rejection_wall_space(
        classified_walls, direction, entry, stop, target,
    )


# ── Core grading ──────────────────────────────────────────────────────────────


class TestCoreGrading:

    def test_long_no_walls_grade_a(self):
        """Test 1: LONG, no walls → A."""
        space = _space((), Direction.LONG, 10500, 10450, 10600)
        result = grade_trade_space(space)
        assert result.grade == TradeSpaceGrade.A
        assert result.reason == TradeSpaceReason.CLEAR_PATH_TO_TARGET
        assert result.active_wall_count == 0
        assert result.nearest_wall_distance_ticks is None
        assert result.has_wall_within_1r is False

    def test_short_no_walls_grade_a(self):
        """Test 2: SHORT, no walls → A."""
        space = _space((), Direction.SHORT, 9500, 9550, 9400)
        result = grade_trade_space(space)
        assert result.grade == TradeSpaceGrade.A

    def test_long_wall_beyond_1r_grade_b_plus(self):
        """Test 3: LONG, nearest active wall >1R → B_PLUS."""
        # risk = 50, wall at +60 ticks (1.2R)
        wall = _make_wall(10560, 10565, (5, 6))
        cw = _active(wall)
        space = _space((cw,), Direction.LONG, 10500, 10450, 10600)
        result = grade_trade_space(space)
        assert result.grade == TradeSpaceGrade.B_PLUS
        assert result.reason == TradeSpaceReason.ACTIVE_WALL_BEYOND_1R
        assert result.active_wall_count == 1
        assert result.has_wall_within_1r is False

    def test_short_wall_beyond_1r_grade_b_plus(self):
        """Test 4: SHORT, nearest active wall >1R → B_PLUS."""
        # risk = 50, wall at -60 ticks (1.2R)
        wall = _make_wall(9435, 9440, (5, 6))
        cw = _active(wall, Direction.SHORT)
        space = _space((cw,), Direction.SHORT, 9500, 9550, 9400)
        result = grade_trade_space(space)
        assert result.grade == TradeSpaceGrade.B_PLUS

    def test_long_wall_exactly_1r_grade_b(self):
        """Test 5: LONG, active wall exactly at 1R → B."""
        # risk = 50, wall lower_bound at entry+50 = 10550
        wall = _make_wall(10550, 10555, (5, 6))
        cw = _active(wall)
        space = _space((cw,), Direction.LONG, 10500, 10450, 10600)
        result = grade_trade_space(space)
        assert result.grade == TradeSpaceGrade.B
        assert result.reason == TradeSpaceReason.ACTIVE_WALL_WITHIN_1R
        assert result.has_wall_within_1r is True

    def test_short_wall_exactly_1r_grade_b(self):
        """Test 6: SHORT, active wall exactly at 1R → B."""
        # risk = 50, wall upper_bound at entry-50 = 9450
        wall = _make_wall(9445, 9450, (5, 6))
        cw = _active(wall, Direction.SHORT)
        space = _space((cw,), Direction.SHORT, 9500, 9550, 9400)
        result = grade_trade_space(space)
        assert result.grade == TradeSpaceGrade.B

    def test_wall_within_1r_grade_b(self):
        """Test 7: active wall <1R → B."""
        # risk = 50, wall at +25 ticks (0.5R)
        wall = _make_wall(10525, 10530, (5, 6))
        cw = _active(wall)
        space = _space((cw,), Direction.LONG, 10500, 10450, 10600)
        result = grade_trade_space(space)
        assert result.grade == TradeSpaceGrade.B
        assert result.nearest_wall_distance_ticks == 25
        assert result.nearest_wall_distance_r == 0.5


class TestMultiWallAndEdge:

    def test_multiple_walls_nearest_determines_grade(self):
        """Test 8: nearest wall determines grade."""
        # Wall A at 0.5R (25 ticks), Wall B at 1.5R (75 ticks)
        wall_near = _make_wall(10525, 10530, (3, 4))
        wall_far = _make_wall(10575, 10580, (5, 6))
        cn = _active(wall_near)
        cf = _active(wall_far)
        space = _space((cn, cf), Direction.LONG, 10500, 10450, 10600)
        result = grade_trade_space(space)
        assert result.grade == TradeSpaceGrade.B  # nearest at 0.5R
        assert result.active_wall_count == 2

    def test_inactive_wall_does_not_downgrade(self):
        """Test 9: inactive wall does not affect grade."""
        wall = _make_wall(10525, 10530, (5, 6))
        cw = _inactive(wall)
        space = _space((cw,), Direction.LONG, 10500, 10450, 10600)
        result = grade_trade_space(space)
        assert result.grade == TradeSpaceGrade.A

    def test_wall_outside_entry_target_no_downgrade(self):
        """Test 10: wall behind entry does not downgrade."""
        wall = _make_wall(10480, 10490, (5, 6))
        cw = _active(wall)
        space = _space((cw,), Direction.LONG, 10500, 10450, 10600)
        result = grade_trade_space(space)
        assert result.grade == TradeSpaceGrade.A

    def test_wall_ordering_does_not_alter_result(self):
        """Test 11: reversed input order → same grade."""
        w1 = _make_wall(10525, 10530, (3, 4))
        w2 = _make_wall(10575, 10580, (5, 6))
        c1 = _active(w1)
        c2 = _active(w2)
        space_12 = _space((c1, c2), Direction.LONG, 10500, 10450, 10600)
        space_21 = _space((c2, c1), Direction.LONG, 10500, 10450, 10600)
        assert grade_trade_space(space_12).grade == grade_trade_space(space_21).grade

    def test_long_short_symmetric(self):
        """Test 12: mirrored LONG/SHORT geometry → same grade."""
        # LONG: entry=100, stop=95, target=110, wall at 103 (0.6R)
        w_l = _make_wall(10300, 10310, (5, 6))
        c_l = _active(w_l)
        space_l = _space((c_l,), Direction.LONG, 10000, 9500, 11000)

        # SHORT: entry=100, stop=105, target=90, wall at 97 (0.6R)
        w_s = _make_wall(9690, 9700, (5, 6))
        c_s = _active(w_s, Direction.SHORT)
        space_s = _space((c_s,), Direction.SHORT, 10000, 10500, 9000)

        assert grade_trade_space(space_l).grade == grade_trade_space(space_s).grade


class TestValidation:

    def test_invalid_input_type(self):
        with pytest.raises(TypeError, match="WallSpaceResult"):
            grade_trade_space({"fake": True})


class TestSerialization:

    def test_to_dict_json(self):
        space = _space((), Direction.LONG, 10500, 10450, 10600)
        result = grade_trade_space(space)
        d = result.to_dict()
        s = json.dumps(d)
        parsed = json.loads(s)
        assert parsed["grade"] == "A"
        assert parsed["reason"] == "CLEAR_PATH_TO_TARGET"
