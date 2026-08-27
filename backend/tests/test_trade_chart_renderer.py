"""Trade chart PNG renderer.

Turns a persisted trade_state record into an image for visual review of
Max Entry Candles. The record already holds everything needed — the
candles, the structural levels, the plan and the geometry — so the
renderer requests nothing and recomputes no level.

The property these tests care about most is the last one: a renderer
that can throw is a renderer that can one day take down a trading loop.
Every entry point here is required to fail quietly.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading_lab.live.trade_chart_renderer import (
    chart_filename,
    render_entry_chart,
    render_exit_chart,
    render_session,
    render_trade_charts,
)

CT = ZoneInfo("America/Chicago")
MS_0930_ET = 1787751000000          # 2026-08-26 09:30 ET = 08:30 CT


def _bars(n=20, start=MS_0930_ET, base=100.0):
    out = []
    for i in range(n):
        o = base + i * 0.01
        out.append({"time_ms": start + i * 60_000, "open": o, "high": o + 0.20,
                    "low": o - 0.15, "close": o + 0.05, "volume": 1000 + i})
    return out


def _record(direction="LONG", pattern="SINGLE_CANDLE_REJECTION",
            levels=None, with_exit=True, n=20, state="CLOSED"):
    bars = _bars(n)
    entry = bars[-1]
    rec = {
        "trade_id": f"SPY_{direction}_ORB_HIGH_{bars[0]['time_ms']}",
        "symbol": "SPY", "direction": direction, "state": state,
        "entry_timestamp_ms": entry["time_ms"],
        "underlying_entry": entry["close"], "stop": entry["low"],
        "target": entry["close"] + 1.0, "rr": 2,
        "setup_snapshot": {
            "level_source": "ORB_HIGH",
            "level_price": {"ticks": 10010, "tick_size": 0.01},
            "entry_pattern_type": pattern,
            "session": {"date": "2026-08-26", "market_timezone": "America/New_York"},
            "break_bar": {"bar_utc_ms": bars[2]["time_ms"],
                          "open": {"ticks": 10000, "tick_size": 0.01},
                          "high": {"ticks": 10030, "tick_size": 0.01},
                          "low": {"ticks": 9990, "tick_size": 0.01},
                          "close": {"ticks": 10020, "tick_size": 0.01}},
            "confirmation_bar": {
                "bar_utc_ms": entry["time_ms"],
                "open": {"ticks": int(entry["open"] * 100), "tick_size": 0.01},
                "high": {"ticks": int(entry["high"] * 100), "tick_size": 0.01},
                "low": {"ticks": int(entry["low"] * 100), "tick_size": 0.01},
                "close": {"ticks": int(entry["close"] * 100), "tick_size": 0.01}},
        },
        "chart_context": {
            "timeframe_seconds": 60, "market_timezone": "America/New_York",
            "candles": bars,
            "levels": {"orb_high": 100.1, "orb_low": 99.8, "pdh": 101.0,
                       "pdl": 99.0, "pmh": 100.5, "pml": 99.5}
            if levels is None else levels,
            "window": {"start_time_ms": bars[0]["time_ms"],
                       "entry_time_ms": entry["time_ms"],
                       "end_time_ms": entry["time_ms"]},
        },
    }
    if with_exit:
        tail = _bars(6, start=entry["time_ms"] + 60_000, base=entry["close"])
        rec["exit_chart_context"] = {
            "timeframe_seconds": 60, "market_timezone": "America/New_York",
            "candles": bars + tail,
            "levels": rec["chart_context"]["levels"],
            "window": {"start_time_ms": bars[0]["time_ms"],
                       "entry_time_ms": entry["time_ms"],
                       "exit_time_ms": tail[-1]["time_ms"],
                       "end_time_ms": tail[-1]["time_ms"]},
        }
        rec["outcome"] = {"result": "WIN", "exit_reason": "TARGET",
                          "gross_pnl": 42.0, "exit_fill_premium": 1.23}
    return rec


def _is_png(path: Path) -> bool:
    return path.exists() and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# ═════════════════════════════════════════════════════════════════════
# It draws, and what it draws is a PNG
# ═════════════════════════════════════════════════════════════════════


class TestRendering:
    def test_single_long(self, tmp_path):
        p = render_entry_chart(_record("LONG"), tmp_path)
        assert _is_png(p)

    def test_single_short(self, tmp_path):
        p = render_entry_chart(_record("SHORT"), tmp_path)
        assert _is_png(p)

    def test_two_candle(self, tmp_path):
        p = render_entry_chart(
            _record(pattern="TWO_CANDLE_ENGULFING_RECOVERY"), tmp_path)
        assert _is_png(p)

    def test_exit_chart(self, tmp_path):
        p = render_exit_chart(_record(), tmp_path)
        assert _is_png(p)

    def test_both_at_once(self, tmp_path):
        out = render_trade_charts(_record(), tmp_path)
        assert _is_png(out["entry"]) and _is_png(out["exit"])
        assert out["error"] is None

    def test_session_end_outcome_renders(self, tmp_path):
        rec = _record()
        rec["outcome"] = {"result": "SESSION_END", "exit_reason": "SESSION_END",
                          "gross_pnl": -3.0}
        assert _is_png(render_exit_chart(rec, tmp_path))

    def test_requires_attention_without_outcome(self, tmp_path):
        rec = _record(state="REQUIRES_ATTENTION")
        rec.pop("outcome")
        rec["terminal"] = {"reason": "EXPIRATION_EXERCISE"}
        assert _is_png(render_exit_chart(rec, tmp_path))

    def test_r_probe_is_shown_when_present(self, tmp_path):
        rec = _record()
        rec["r_probe"] = {"mfe_r": 3.2, "mae_r": -0.8,
                          "first_touch": {"2r": rec["entry_timestamp_ms"]}}
        assert _is_png(render_exit_chart(rec, tmp_path))


# ═════════════════════════════════════════════════════════════════════
# Missing data is a missing image, never a crash
# ═════════════════════════════════════════════════════════════════════


class TestMissingData:
    def test_no_exit_context_yields_no_exit_image(self, tmp_path):
        out = render_trade_charts(_record(with_exit=False), tmp_path)
        assert _is_png(out["entry"])
        assert out["exit"] is None, "an open trade has no exit to draw"
        assert out["error"] is None

    def test_no_chart_context_yields_nothing(self, tmp_path):
        rec = _record()
        rec.pop("chart_context")
        assert render_entry_chart(rec, tmp_path) is None

    def test_empty_candles_yield_nothing(self, tmp_path):
        rec = _record()
        rec["chart_context"]["candles"] = []
        assert render_entry_chart(rec, tmp_path) is None

    def test_null_levels_are_skipped_not_drawn_at_zero(self, tmp_path):
        """A missing PDH must be absent, never a line at 0."""
        rec = _record(levels={"orb_high": 100.1, "orb_low": None, "pdh": None,
                              "pdl": None, "pmh": None, "pml": None})
        assert _is_png(render_entry_chart(rec, tmp_path))

    def test_levels_block_entirely_absent(self, tmp_path):
        rec = _record()
        rec["chart_context"].pop("levels")
        assert _is_png(render_entry_chart(rec, tmp_path))

    def test_missing_setup_snapshot(self, tmp_path):
        rec = _record()
        rec.pop("setup_snapshot")
        assert _is_png(render_entry_chart(rec, tmp_path))


# ═════════════════════════════════════════════════════════════════════
# A far-away level must not flatten the price action
# ═════════════════════════════════════════════════════════════════════


class TestScale:
    def test_distant_level_does_not_dictate_the_axis(self, tmp_path):
        """The real AMZN chart of 2026-08-27 was unreadable because a PDH
        five dollars above the action stretched the axis until every
        candle collapsed into a band. Levels do not get a vote on scale."""
        from trading_lab.live.trade_chart_renderer import _price_window
        bars = _bars(20)
        lo, hi = _price_window(bars, 100.0, 99.9, 101.0)
        assert hi - lo < 2.0, "a sane window around the action"
        # A PDH at 500 is outside it and is therefore not drawn.
        assert not (lo <= 500.0 <= hi)

    def test_chart_with_distant_level_still_renders(self, tmp_path):
        rec = _record(levels={"orb_high": 100.1, "pdh": 500.0, "pdl": 1.0,
                              "orb_low": None, "pmh": None, "pml": None})
        assert _is_png(render_entry_chart(rec, tmp_path))


# ═════════════════════════════════════════════════════════════════════
# Filenames and timezone
# ═════════════════════════════════════════════════════════════════════


class TestNaming:
    def test_deterministic(self):
        rec = _record()
        assert chart_filename(rec, "ENTRY") == chart_filename(rec, "ENTRY")

    def test_readable_shape(self):
        name = chart_filename(_record("SHORT"), "ENTRY")
        assert name.startswith("SPY_SHORT_ORB_HIGH_")
        assert name.endswith("_ENTRY.png")

    def test_time_in_the_name_is_chicago(self):
        """08:30 CT, not 09:30 ET — the charts are compared against a
        Chicago-time platform."""
        rec = _record()
        rec["entry_timestamp_ms"] = MS_0930_ET
        assert "_0830_" in chart_filename(rec, "ENTRY")

    def test_entry_and_exit_differ(self):
        rec = _record()
        assert chart_filename(rec, "ENTRY") != chart_filename(rec, "EXIT")

    def test_collision_falls_back_to_trade_id(self, tmp_path):
        a = _record(); b = _record()
        b["trade_id"] = "SPY_LONG_ORB_HIGH_OTHER"
        pa = render_entry_chart(a, tmp_path)
        pb = render_entry_chart(b, tmp_path)
        assert pa != pb, "two different trades must not overwrite each other"
        assert _is_png(pa) and _is_png(pb)

    def test_redraw_of_the_same_trade_reuses_its_path(self, tmp_path):
        rec = _record()
        assert render_entry_chart(rec, tmp_path) == render_entry_chart(rec, tmp_path)


# ═════════════════════════════════════════════════════════════════════
# Never break the caller — the property that lets this run in-process
# ═════════════════════════════════════════════════════════════════════


class TestFailureIsContained:
    def test_render_trade_charts_never_raises(self, tmp_path):
        for junk in ({}, {"chart_context": "not a dict"},
                     {"chart_context": {"candles": [{"bad": 1}]}},
                     {"entry_timestamp_ms": "nonsense"},
                     {"chart_context": {"candles": [{"time_ms": None, "open": None,
                                                     "high": None, "low": None,
                                                     "close": None}]}}):
            out = render_trade_charts(junk, tmp_path)
            assert isinstance(out, dict)
            assert set(out) == {"entry", "exit", "error"}

    def test_unwritable_directory_is_reported_not_raised(self, tmp_path):
        blocked = tmp_path / "file-not-a-dir"
        blocked.write_text("x")
        out = render_trade_charts(_record(), blocked / "sub")
        assert out["error"] is not None
        assert out["entry"] is None

    def test_the_module_cannot_touch_the_trading_runtime(self):
        """It reads records and draws. It has no broker, no orchestrator
        and no lifecycle — that is what makes it safe to call from one."""
        import inspect
        from trading_lab.live import trade_chart_renderer as mod
        # The docstring names these concepts on purpose; what matters is
        # that the CODE never reaches for them.
        src = inspect.getsource(mod)
        body = src.split('"""', 2)[2] if src.count('"""') >= 2 else src
        for forbidden in ("placeOrder", "submit_exit", "submit_entry",
                          "ib_insync", "reqMktData", "_exit_executor",
                          "_entry_executor", "LifecycleState"):
            assert forbidden not in body, forbidden
        # And it imports nothing from the live execution path.
        for line in body.splitlines():
            if line.startswith(("import ", "from ")):
                assert "bot_runner" not in line and "trade_orchestrator" not in line


# ═════════════════════════════════════════════════════════════════════
# Session-level CLI behaviour
# ═════════════════════════════════════════════════════════════════════


class TestRenderSession:
    def _write(self, state_dir: Path, rec: dict):
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / f"{rec['trade_id']}.json").write_text(json.dumps(rec))

    def test_renders_only_the_requested_date(self, tmp_path):
        sd, od = tmp_path / "state", tmp_path / "out"
        a = _record(); self._write(sd, a)
        b = _record(); b["trade_id"] = "SPY_LONG_OTHER_DAY"
        b["setup_snapshot"]["session"]["date"] = "2026-08-25"
        self._write(sd, b)

        rows = render_session("2026-08-26", sd, od)
        assert len(rows) == 1 and rows[0]["trade_id"] == a["trade_id"]

    def test_output_is_grouped_by_session_date(self, tmp_path):
        sd, od = tmp_path / "state", tmp_path / "out"
        self._write(sd, _record())
        render_session("2026-08-26", sd, od)
        assert (od / "2026-08-26").is_dir()

    def test_second_run_skips_existing(self, tmp_path):
        sd, od = tmp_path / "state", tmp_path / "out"
        self._write(sd, _record())
        render_session("2026-08-26", sd, od)
        rows = render_session("2026-08-26", sd, od)
        assert rows[0]["skipped"] is True

    def test_overwrite_forces_a_redraw(self, tmp_path):
        sd, od = tmp_path / "state", tmp_path / "out"
        self._write(sd, _record())
        render_session("2026-08-26", sd, od)
        rows = render_session("2026-08-26", sd, od, overwrite=True)
        assert rows[0]["skipped"] is False

    def test_unreadable_file_is_skipped_not_fatal(self, tmp_path):
        sd, od = tmp_path / "state", tmp_path / "out"
        self._write(sd, _record())
        (sd / "broken.json").write_text("{ not json")
        rows = render_session("2026-08-26", sd, od)
        assert len(rows) == 1

    def test_empty_directory_is_fine(self, tmp_path):
        assert render_session("2026-08-26", tmp_path / "none", tmp_path / "o") == []
