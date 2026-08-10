"""Tests for b10_review — B10.1 visual review payload builder.

Verifies that the review payload correctly passes through B9/B10 results
and that grade, walls, and geometry are consistent with the underlying
contracts.
"""

import json
import pytest
from trading_lab.contracts.enums import Direction
from trading_lab.b10_review import build_b10_review, B10ReviewPayload
from trading_lab.trade_space_grader import TradeSpaceGrade


TICK = 0.01


# ── Helpers ───────────────────────────────────────────────────────────────────


def _candle(o, h, l, c, time_ms=0):
    return {"open": o, "high": h, "low": l, "close": c, "time_ms": time_ms}


def _long_rejection(high, close_below=0.20, time_ms=0):
    low = high - 0.50
    close = high - close_below
    open_ = close - 0.05
    return _candle(open_, high, low, close, time_ms)


def _filler(h=50.0, time_ms=0):
    return _candle(h - 0.02, h, h - 0.10, h - 0.01, time_ms)


def _build_simple_trade(wall_highs=None, entry=10500, stop=10450, target=10600):
    """Build a simple synthetic trade with optional wall contacts.

    Returns candles, break_idx, conf_idx, direction, entry, stop, target.
    """
    candles = [
        _filler(40, 1000000),  # 0 - ORB
        _filler(41, 1060000),  # 1
    ]
    if wall_highs:
        for i, wh in enumerate(wall_highs):
            candles.append(_long_rejection(wh * TICK, time_ms=1120000 + i * 60000))
    else:
        candles.append(_filler(42, 1120000))  # 2
        candles.append(_filler(43, 1180000))  # 3

    # Add filler bars to reach confirmation index
    while len(candles) < 8:
        candles.append(_filler(44 + len(candles), 1120000 + len(candles) * 60000))

    break_idx = 1
    conf_idx = len(candles) - 1  # last bar before entry
    return candles, break_idx, conf_idx


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestGradePassthrough:

    def test_grade_a_no_walls(self):
        """Test 1: B10 grade A passed through when no walls."""
        candles, bi, ci = _build_simple_trade()
        payload = build_b10_review(
            candles, bi, ci, Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
            symbol="TEST", date="2025-01-01", outcome="TARGET_HIT",
        )
        assert payload.grade == "A"
        assert payload.reason == "CLEAR_PATH_TO_TARGET"
        assert payload.active_wall_count == 0
        assert payload.nearest_wall_distance_ticks is None

    def test_grade_a_no_nearest_wall_overlay(self):
        """Test 2: Grade A → no wall overlay between entry/target."""
        candles, bi, ci = _build_simple_trade()
        payload = build_b10_review(
            candles, bi, ci, Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
            symbol="TEST", date="2025-01-01", outcome="TARGET_HIT",
        )
        active_between = [w for w in payload.walls
                          if w["is_active"] and w["is_between_entry_and_target"]]
        assert len(active_between) == 0

    def test_b_plus_nearest_wall_correct(self):
        """Test 3: B_PLUS identifies nearest wall correctly."""
        # Wall at 106.0 (10600 ticks) — beyond 1R (risk=50, distance=60=1.2R)
        # But need wall between entry and target (entry=10500, target=10600)
        # Wall at 105.60 → 10560 ticks, distance=60, risk=50 → 1.2R
        candles, bi, ci = _build_simple_trade(wall_highs=[10560, 10562])
        payload = build_b10_review(
            candles, bi, ci, Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
            symbol="TEST", date="2025-01-01", outcome="STOPPED",
        )
        assert payload.grade == "B_PLUS"
        assert payload.nearest_wall_distance_ticks == 60  # 10560 - 10500

    def test_b_nearest_wall_correct(self):
        """Test 4: B identifies nearest wall correctly."""
        # Wall at 105.25 (10525 ticks) — within 1R (risk=50, distance=25=0.5R)
        candles, bi, ci = _build_simple_trade(wall_highs=[10525, 10527])
        payload = build_b10_review(
            candles, bi, ci, Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
            symbol="TEST", date="2025-01-01", outcome="STOPPED",
        )
        assert payload.grade == "B"
        assert payload.nearest_wall_distance_ticks == 25

    def test_multiple_active_walls_present(self):
        """Test 5: Multiple active walls all present in payload."""
        # Build candles manually so closes don't accidentally break walls
        candles = [
            _filler(40, 1000000),  # 0
            _filler(41, 1060000),  # 1 - break
            # Wall A contacts: high at 105.25 but close below 105.25
            _candle(104.80, 105.25, 104.70, 104.90, 1120000),  # 2
            _candle(104.80, 105.27, 104.70, 104.90, 1180000),  # 3
            # Wall B contacts: high at 105.60 but close below 105.25 (don't break A)
            _candle(104.80, 105.60, 104.70, 104.90, 1240000),  # 4
            _candle(104.80, 105.62, 104.70, 104.90, 1300000),  # 5
            _filler(42, 1360000),  # 6
            _filler(43, 1420000),  # 7 - confirmation
        ]
        payload = build_b10_review(
            candles, 1, 7, Direction.LONG,
            entry_ticks=10500, stop_ticks=10450, target_ticks=10600,
            symbol="TEST", date="2025-01-01", outcome="STOPPED",
        )
        active_between = [w for w in payload.walls
                          if w["is_active"] and w["is_between_entry_and_target"]]
        assert len(active_between) >= 2

    def test_nearest_wall_independent_of_ordering(self):
        """Test 6: Nearest wall identified regardless of input order."""
        # Both orderings produce identical candle arrays with closes below both walls
        def _make_two_wall_candles(near_first):
            candles = [_filler(40, 1000000), _filler(41, 1060000)]
            if near_first:
                pairs = [(105.25, 105.27), (105.75, 105.77)]
            else:
                pairs = [(105.75, 105.77), (105.25, 105.27)]
            for h1, h2 in pairs:
                candles.append(_candle(104.80, h1, 104.70, 104.90, 1120000 + len(candles)*60000))
                candles.append(_candle(104.80, h2, 104.70, 104.90, 1120000 + len(candles)*60000))
            while len(candles) < 8:
                candles.append(_filler(42+len(candles), 1120000+len(candles)*60000))
            return candles

        ca = _make_two_wall_candles(True)
        cb = _make_two_wall_candles(False)
        pa = build_b10_review(ca, 1, 7, Direction.LONG, 10500, 10450, 10600, "T", "2025-01-01", "S")
        pb = build_b10_review(cb, 1, 7, Direction.LONG, 10500, 10450, 10600, "T", "2025-01-01", "S")
        assert pa.nearest_wall_distance_ticks == pb.nearest_wall_distance_ticks == 25

    def test_long_geometry(self):
        """Test 7: LONG geometry preserved in payload."""
        candles, bi, ci = _build_simple_trade(wall_highs=[10525, 10527])
        payload = build_b10_review(
            candles, bi, ci, Direction.LONG,
            10500, 10450, 10600, "TEST", "2025-01-01", "STOPPED",
        )
        assert payload.direction == "LONG"
        assert payload.entry_price < payload.target_price

    def test_short_geometry(self):
        """Test 8: SHORT geometry preserved."""
        # SHORT: entry=100, stop=105, target=90
        # Need candles with lows near 95 for wall detection
        candles = [_filler(50, 1000000), _filler(51, 1060000)]
        # Add SHORT rejection candles (low wick)
        for i in range(4):
            low = 95.25
            candles.append(_candle(95.70, 96.00, low, 95.60, 1120000 + i * 60000))
        while len(candles) < 8:
            candles.append(_filler(50 + len(candles), 1120000 + len(candles) * 60000))

        payload = build_b10_review(
            candles, 1, 7, Direction.SHORT,
            10000, 10500, 9000, "TEST", "2025-01-01", "STOPPED",
        )
        assert payload.direction == "SHORT"
        assert payload.entry_price > payload.target_price

    def test_outcome_display_only(self):
        """Test 9: Outcome does not affect grade."""
        candles, bi, ci = _build_simple_trade(wall_highs=[10525, 10527])
        p_win = build_b10_review(candles, bi, ci, Direction.LONG,
                                 10500, 10450, 10600, "T", "2025-01-01", "TARGET_HIT")
        p_stop = build_b10_review(candles, bi, ci, Direction.LONG,
                                  10500, 10450, 10600, "T", "2025-01-01", "STOPPED")
        assert p_win.grade == p_stop.grade
        assert p_win.outcome == "TARGET_HIT"
        assert p_stop.outcome == "STOPPED"


class TestSerialization:

    def test_json_serializable(self):
        """Payload to_dict produces valid JSON."""
        candles, bi, ci = _build_simple_trade(wall_highs=[10525, 10527])
        payload = build_b10_review(
            candles, bi, ci, Direction.LONG,
            10500, 10450, 10600, "TEST", "2025-01-01", "STOPPED",
        )
        d = payload.to_dict()
        s = json.dumps(d)
        parsed = json.loads(s)
        assert parsed["grade"] == "B"
        assert isinstance(parsed["walls"], list)
        assert isinstance(parsed["candles"], list)
