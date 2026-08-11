"""B10.1 — Visual review data builder for trade space grades.

Builds a self-contained review payload for a single historical trade,
combining candlestick data with B9 wall analysis and B10 grading.
The payload is consumed by the review HTML template.

This module does NOT modify the strategy runner, backtest server,
or any production trade flow.  It is a standalone review/debugging tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.contracts.enums import Direction
from trading_lab.rejection_wall_classifier import classify_active_rejection_walls
from trading_lab.rejection_wall_finder import find_rejection_walls
from trading_lab.rejection_wall_space import AnalyzedWall, analyze_rejection_wall_space
from trading_lab.trade_space_grader import grade_trade_space, TradeSpaceGradeResult

ET = ZoneInfo("America/New_York")
TICK = 0.01


@dataclass(frozen=True)
class B10ReviewPayload:
    """Complete payload for the B10 visual review of one trade."""

    symbol: str
    date: str
    direction: str
    timeframe: str
    entry_price: float
    stop_price: float
    target_price: float
    risk_ticks: int
    outcome: str
    grade: str
    reason: str
    active_wall_count: int
    nearest_wall_distance_ticks: int | None
    nearest_wall_distance_r: float | None
    has_wall_within_1r: bool
    walls: list[dict]
    candles: list[dict]
    break_candle_time: int | None
    entry_candle_time: int | None
    wall_contact_times: list[int]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "direction": self.direction,
            "timeframe": self.timeframe,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "risk_ticks": self.risk_ticks,
            "outcome": self.outcome,
            "grade": self.grade,
            "reason": self.reason,
            "active_wall_count": self.active_wall_count,
            "nearest_wall_distance_ticks": self.nearest_wall_distance_ticks,
            "nearest_wall_distance_r": self.nearest_wall_distance_r,
            "has_wall_within_1r": self.has_wall_within_1r,
            "walls": self.walls,
            "candles": self.candles,
            "break_candle_time": self.break_candle_time,
            "entry_candle_time": self.entry_candle_time,
            "wall_contact_times": self.wall_contact_times,
        }


def build_b10_review(
    candles: list[dict],
    break_index: int,
    confirmation_index: int,
    direction: Direction,
    entry_ticks: int,
    stop_ticks: int,
    target_ticks: int,
    symbol: str,
    date: str,
    outcome: str,
    timeframe: str = "1m",
    tick_size: float = 0.01,
) -> B10ReviewPayload:
    """Build a complete B10 review payload for one trade.

    Runs the full B9→B10 pipeline on the given candles and trade parameters.
    Returns a payload suitable for rendering in the review HTML.
    """
    # B9.1: detect walls
    detection = find_rejection_walls(
        candles, break_index, confirmation_index, direction, tick_size,
    )

    # B9.3: classify active/inactive
    classified = classify_active_rejection_walls(
        detection.walls, candles, confirmation_index, direction, tick_size,
    )

    # B9.4: space analysis
    space = analyze_rejection_wall_space(
        classified, direction, entry_ticks, stop_ticks, target_ticks,
    )

    # B10: grade
    grade_result = grade_trade_space(space)

    # Build wall summaries for the chart overlay
    wall_summaries = []
    nearest_idx = None
    if space.nearest_active_between is not None:
        nearest_idx = id(space.nearest_active_between)

    for aw in space.walls:
        cw = aw.classified_wall
        is_nearest = (id(aw) == nearest_idx)
        wall_summaries.append({
            "lower_price": cw.wall.lower_ticks * tick_size,
            "upper_price": cw.wall.upper_ticks * tick_size,
            "representative_price": cw.wall.representative_ticks * tick_size,
            "is_active": cw.is_active,
            "status": cw.status.value,
            "geometry": aw.geometry.value,
            "is_between_entry_and_target": aw.is_between_entry_and_target,
            "is_nearest": is_nearest,
            "distance_ticks": aw.distance_ticks,
            "distance_r": round(aw.distance_r, 4),
            "contact_count": cw.wall.contact_count,
            "rejection_count": cw.wall.rejection_contact_count,
        })

    # Build chart candles (time as unix seconds for Lightweight Charts)
    chart_candles = []
    for c in candles:
        if "time_ms" in c:
            ts = c["time_ms"] // 1000
        elif "time_et" in c:
            dt = datetime.strptime(c["time_et"], "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=ET)
            ts = int(dt.timestamp())
        elif "time" in c:
            try:
                dt = datetime.strptime(c["time"], "%H:%M:%S")
                dt = datetime.strptime(f"{date} {c['time']}", "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=ET)
                ts = int(dt.timestamp())
            except ValueError:
                ts = 0
        else:
            ts = 0

        chart_candles.append({
            "time": ts,
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
        })

    return B10ReviewPayload(
        symbol=symbol,
        date=date,
        direction=direction.value,
        timeframe=timeframe,
        entry_price=round(entry_ticks * tick_size, 6),
        stop_price=round(stop_ticks * tick_size, 6),
        target_price=round(target_ticks * tick_size, 6),
        risk_ticks=abs(entry_ticks - stop_ticks),
        outcome=outcome,
        grade=grade_result.grade.value,
        reason=grade_result.reason.value,
        active_wall_count=grade_result.active_wall_count,
        nearest_wall_distance_ticks=grade_result.nearest_wall_distance_ticks,
        nearest_wall_distance_r=(
            round(grade_result.nearest_wall_distance_r, 4)
            if grade_result.nearest_wall_distance_r is not None else None
        ),
        has_wall_within_1r=grade_result.has_wall_within_1r,
        walls=wall_summaries,
        candles=chart_candles,
        break_candle_time=chart_candles[break_index]["time"] if break_index < len(chart_candles) else None,
        entry_candle_time=chart_candles[confirmation_index]["time"] if confirmation_index < len(chart_candles) else None,
        wall_contact_times=sorted(set(
            chart_candles[c.candle_index]["time"]
            for cw in classified
            for c in cw.wall.contacts
            if cw.is_active and c.candle_index < len(chart_candles)
        )),
    )
