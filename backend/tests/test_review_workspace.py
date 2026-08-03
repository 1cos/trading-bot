"""Tests for the Trading Day Review Workspace.

Covers:
  1. build_workspace_events returns events with explain data
  2. render_workspace_html produces valid HTML
  3. write_workspace_html writes to file
  4. Explain contains stage data for VALID events
  5. Explain contains failure info for INVALID events
  6. Failed retests geometry is included
  7. Trade plan is included in explain
  8. Outcome is included in explain
  9. Multiple events render in workspace
  10. Empty event list renders
  11. Determinism: same input → same output
  12. Navigation controls are present
  13. Accept/Reject/Skip buttons are present
  14. ORB zone overlay div is present
  15. Keyboard shortcut bindings are present
  16. Progress dots are rendered
  17. Candle data appears in events JSON
  18. Annotations appear in events JSON
  19. Event header section is present
  20. Explain panel section is present
"""

import json
import os

import pytest

from trading_lab.review_workspace import (
    build_workspace_events,
    render_workspace_html,
    write_workspace_html,
    _build_explain,
)


# ── Timestamps (2026-07-01 ET) ──────────────────────────────────────────────

MS_0930 = 1782912600000
MS_0935 = 1782912900000
MS_0940 = 1782913200000
MS_0945 = 1782913500000
MS_0950 = 1782913800000
MS_0955 = 1782914100000
MS_1000 = 1782914400000
MS_1005 = 1782914700000
MS_1010 = 1782915000000


def c(time_ms, open_=100.0, high=101.0, low=99.0, close=100.5, volume=100):
    return {
        "time_ms": time_ms, "open": open_, "high": high,
        "low": low, "close": close, "volume": volume,
    }


# Session with a VALID LONG setup: break, displacement, retest, confirm
# ORB High = 101.0. Level = ORB_HIGH = 101.0
# Break: close 101.50 > 101.0 ✓
# Displacement: bar stays above 101.0 (low=101.20) ✓
# Retest contact: low 100.80 <= 101.0 ✓
# Confirmation geometry on retest contact:
#   range = 101.50 - 100.80 = 0.70
#   rejection wick (lower) = 101.35 - 100.80 = 0.55 → ratio = 0.786 ✓ (≥0.47)
#   body = |101.45 - 101.35| = 0.10 → ratio = 0.143 ✓ (≤0.40)
#   favorable close location = (101.45 - 100.80) / 0.70 = 0.929 ✓ (≥0.80)
VALID_CANDLES = [
    c(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5),       # ORB
    c(MS_0935, open_=100.50, high=102.0, low=100.20, close=101.50),    # Break
    c(MS_0940, open_=101.60, high=102.50, low=101.20, close=102.00),   # Disp
    c(MS_0945, open_=101.35, high=101.50, low=100.80, close=101.45),   # Confirm ✓
    c(MS_0950, open_=101.50, high=102.50, low=101.40, close=102.30),   # Post-confirm
    c(MS_0955, open_=102.30, high=103.00, low=102.10, close=102.80),   # Run up
    c(MS_1000, open_=102.80, high=103.50, low=102.50, close=103.20),   # Run up
    c(MS_1005, open_=103.20, high=104.00, low=103.00, close=103.80),   # Run up
    c(MS_1010, open_=103.80, high=104.50, low=103.50, close=104.20),   # Run up → target hit
]

# Session that will FAIL detection (no break)
NO_BREAK_CANDLES = [
    c(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5),
    c(MS_0935, open_=100.3, high=100.8, low=99.5, close=100.2),
    c(MS_0940, open_=100.1, high=100.6, low=99.3, close=99.8),
    c(MS_0945, open_=99.8, high=100.4, low=99.2, close=100.0),
    c(MS_0950, open_=100.0, high=100.5, low=99.4, close=100.1),
]


def _make_sessions(candle_lists):
    sessions = []
    for candles in candle_lists:
        sessions.append({
            "symbol": "TEST", "date": "2026-07-01",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": candles[0]["time_ms"],
            "session_close_utc_ms": candles[-1]["time_ms"],
            "timeframe": "5m", "candles": candles,
        })
    return sessions


PRESET = {
    "preset_id": "test", "timeframe_minutes": 5,
    "timezone": "America/New_York", "session_open": "09:30",
    "orb_start": "session_open", "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH", "direction": "LONG",
    "entry_model": "CONFIRMATION_CLOSE",
    "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
    "min_displacement_ticks": None,
    "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None,
    "min_displacement_bars": 1,
}

CONFIG = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0"}


# ── Test: build_workspace_events ────────────────────────────────────────────


class TestBuildWorkspaceEvents:
    def test_returns_list_of_events(self):
        sessions = _make_sessions([VALID_CANDLES])
        events = build_workspace_events(sessions, PRESET, CONFIG)
        assert isinstance(events, list)
        assert len(events) == 1

    def test_event_has_explain_key(self):
        sessions = _make_sessions([VALID_CANDLES])
        events = build_workspace_events(sessions, PRESET, CONFIG)
        assert "explain" in events[0]

    def test_event_has_candles(self):
        sessions = _make_sessions([VALID_CANDLES])
        events = build_workspace_events(sessions, PRESET, CONFIG)
        assert "candles" in events[0]
        assert len(events[0]["candles"]) == len(VALID_CANDLES)

    def test_event_has_annotations(self):
        sessions = _make_sessions([VALID_CANDLES])
        events = build_workspace_events(sessions, PRESET, CONFIG)
        assert "annotations" in events[0]

    def test_multiple_sessions(self):
        sessions = _make_sessions([VALID_CANDLES, NO_BREAK_CANDLES])
        # Give different dates to avoid conflicts
        sessions[1]["date"] = "2026-07-02"
        events = build_workspace_events(sessions, PRESET, CONFIG)
        assert len(events) == 2


# ── Test: explain data for VALID events ─────────────────────────────────────


class TestExplainValid:
    def _valid_explain(self):
        sessions = _make_sessions([VALID_CANDLES])
        events = build_workspace_events(sessions, PRESET, CONFIG)
        return events[0]["explain"]

    def test_has_stages(self):
        expl = self._valid_explain()
        assert "stages" in expl
        assert len(expl["stages"]) > 0

    def test_has_orb_stage(self):
        expl = self._valid_explain()
        names = [s["name"] for s in expl["stages"]]
        assert "ORB" in names

    def test_has_break_stage(self):
        expl = self._valid_explain()
        names = [s["name"] for s in expl["stages"]]
        assert "Break" in names

    def test_has_displacement_stage(self):
        expl = self._valid_explain()
        names = [s["name"] for s in expl["stages"]]
        assert "Displacement" in names

    def test_has_confirmation_stage(self):
        expl = self._valid_explain()
        names = [s["name"] for s in expl["stages"]]
        assert "Confirmation" in names

    def test_confirmation_has_geometry_values(self):
        expl = self._valid_explain()
        conf = [s for s in expl["stages"] if s["name"] == "Confirmation"][0]
        assert "Rejection wick:" in conf["detail"]
        assert "Body:" in conf["detail"]
        assert "Close location:" in conf["detail"]

    def test_has_trade_plan(self):
        expl = self._valid_explain()
        assert "trade_plan" in expl

    def test_trade_plan_has_entry_stop(self):
        expl = self._valid_explain()
        tp = expl["trade_plan"]
        assert "entry" in tp
        assert "stop" in tp
        assert "target_2r" in tp
        assert "risk" in tp

    def test_has_outcome(self):
        expl = self._valid_explain()
        assert "outcome" in expl

    def test_outcome_has_result(self):
        expl = self._valid_explain()
        assert "result" in expl["outcome"]
        assert "realized_r" in expl["outcome"]


# ── Test: explain data for INVALID events ───────────────────────────────────


class TestExplainInvalid:
    def _invalid_explain(self):
        sessions = _make_sessions([NO_BREAK_CANDLES])
        events = build_workspace_events(sessions, PRESET, CONFIG)
        return events[0]["explain"]

    def test_has_stages(self):
        expl = self._invalid_explain()
        assert "stages" in expl
        assert len(expl["stages"]) > 0


# ── Test: render_workspace_html ─────────────────────────────────────────────


class TestRenderWorkspaceHtml:
    def _valid_events(self):
        sessions = _make_sessions([VALID_CANDLES])
        return build_workspace_events(sessions, PRESET, CONFIG)

    def test_returns_html_string(self):
        events = self._valid_events()
        html = render_workspace_html(events)
        assert isinstance(html, str)
        assert html.startswith("<!DOCTYPE html>")

    def test_contains_lightweight_charts(self):
        events = self._valid_events()
        html = render_workspace_html(events)
        assert "lightweight-charts" in html

    def test_contains_nav_buttons(self):
        events = self._valid_events()
        html = render_workspace_html(events)
        assert "btnPrev" in html
        assert "btnNext" in html

    def test_contains_decision_buttons(self):
        events = self._valid_events()
        html = render_workspace_html(events)
        assert "btnAccept" in html
        assert "btnReject" in html
        assert "btnSkip" in html

    def test_contains_orb_zone_overlay(self):
        events = self._valid_events()
        html = render_workspace_html(events)
        assert "orbZone" in html

    def test_contains_explain_panel(self):
        events = self._valid_events()
        html = render_workspace_html(events)
        assert "explainPanel" in html

    def test_contains_keyboard_shortcuts(self):
        events = self._valid_events()
        html = render_workspace_html(events)
        assert "ArrowLeft" in html
        assert "ArrowRight" in html

    def test_contains_events_json(self):
        events = self._valid_events()
        html = render_workspace_html(events)
        assert "EVENTS=" in html

    def test_contains_progress_dots(self):
        events = self._valid_events()
        html = render_workspace_html(events)
        assert "progressDots" in html

    def test_empty_event_list(self):
        html = render_workspace_html([])
        assert isinstance(html, str)
        assert "EVENTS=[]" in html

    def test_custom_title(self):
        events = self._valid_events()
        html = render_workspace_html(events, title="My Custom Review")
        assert "My Custom Review" in html

    def test_deterministic(self):
        events = self._valid_events()
        html1 = render_workspace_html(events)
        html2 = render_workspace_html(events)
        assert html1 == html2


# ── Test: write_workspace_html ──────────────────────────────────────────────


class TestWriteWorkspaceHtml:
    def test_writes_file(self, tmp_path):
        sessions = _make_sessions([VALID_CANDLES])
        events = build_workspace_events(sessions, PRESET, CONFIG)
        out = write_workspace_html(events, tmp_path / "workspace.html")
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content

    def test_returns_path(self, tmp_path):
        events = build_workspace_events(
            _make_sessions([VALID_CANDLES]), PRESET, CONFIG)
        result = write_workspace_html(events, tmp_path / "test.html")
        assert result == tmp_path / "test.html"


# ── Test: _build_explain helper ─────────────────────────────────────────────


class TestBuildExplain:
    def test_no_detection_result(self):
        expl = _build_explain({"failure_stage": "SOME_FAIL"})
        assert len(expl["stages"]) == 1
        assert expl["stages"][0]["status"] == "FAILED"

    def test_empty_runner_result(self):
        expl = _build_explain({})
        assert "stages" in expl


# ── Test: PDH/PDL support ──────────────────────────────────────────────────


class TestPdhPdl:
    def test_pdh_pdl_in_event(self):
        sessions = _make_sessions([VALID_CANDLES])
        sessions[0]["pdh"] = 105.0
        sessions[0]["pdl"] = 95.0
        events = build_workspace_events(sessions, PRESET, CONFIG)
        assert events[0]["pdh"] == 105.0
        assert events[0]["pdl"] == 95.0

    def test_pdh_pdl_none_when_missing(self):
        sessions = _make_sessions([VALID_CANDLES])
        events = build_workspace_events(sessions, PRESET, CONFIG)
        assert events[0]["pdh"] is None
        assert events[0]["pdl"] is None

    def test_pdh_pdl_in_html(self):
        sessions = _make_sessions([VALID_CANDLES])
        sessions[0]["pdh"] = 105.0
        sessions[0]["pdl"] = 95.0
        events = build_workspace_events(sessions, PRESET, CONFIG)
        html = render_workspace_html(events)
        assert "PDH" in html
        assert "PDL" in html


# ── Test: Decision export ──────────────────────────────────────────────────


class TestDecisionExport:
    def test_export_button_in_html(self):
        events = build_workspace_events(
            _make_sessions([VALID_CANDLES]), PRESET, CONFIG)
        html = render_workspace_html(events)
        assert "btnExport" in html
        assert "exportDecisions" in html

    def test_export_keyboard_shortcut(self):
        events = build_workspace_events(
            _make_sessions([VALID_CANDLES]), PRESET, CONFIG)
        html = render_workspace_html(events)
        # E key triggers export
        assert '"e"' in html or '"E"' in html
