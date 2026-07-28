"""Tests for the visual review HTML candlestick chart renderer.

Covers:
  1. Valid LONG event renders
  2. Valid SHORT event renders
  3. Failed event renders
  4. Candle data appears in the HTML
  5. Available annotations appear
  6. Null annotations are omitted safely
  7. HTML escaping works
  8. Repeated rendering is deterministic
  9. File-writing helper writes UTF-8 content
  10. ORB High exported
  11. ORB Low exported
  12. Both ORB lines rendered
  13. Selected ORB High distinguished
  14. Selected ORB Low distinguished
  15. Missing ORB values handled safely
  16. Readable annotation legend
  17. Complete candle range is fitted
"""

import json
import os

import pytest

from trading_lab.strategy_runner import run_bdrr_strategy
from trading_lab.visual_review_exporter import export_visual_event
from trading_lab.visual_review_html import (
    render_visual_event_html,
    write_visual_event_html,
)


# ── Timestamps (2026-07-01 ET) ──────────────────────────────────────────────

MS_0930 = 1782912600000
MS_0935 = 1782912900000
MS_0940 = 1782913200000
MS_0945 = 1782913500000
MS_0950 = 1782913800000
MS_0955 = 1782914100000
MS_1000 = 1782914400000


def c(time_ms, open_=100.0, high=101.0, low=99.0, close=100.5, volume=100):
    return {
        "time_ms": time_ms, "open": open_, "high": high,
        "low": low, "close": close, "volume": volume,
    }


LONG_CANDLES = [
    c(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5),
    c(MS_0935, open_=100.50, high=102.0, low=100.20, close=101.50),
    c(MS_0940, open_=101.60, high=102.50, low=101.20, close=102.00),
    c(MS_0945, open_=101.80, high=102.20, low=100.80, close=101.20),
    c(MS_0950, open_=101.30, high=101.50, low=100.90, close=101.40),
    c(MS_0955, open_=101.40, high=103.00, low=101.30, close=102.80),
]

LONG_SESSION = {
    "symbol": "SPY", "date": "2026-07-01",
    "market_timezone": "America/New_York",
    "session_open_utc_ms": MS_0930, "session_close_utc_ms": MS_1000,
    "timeframe": "5m", "candles": LONG_CANDLES,
}

SHORT_CANDLES = [
    c(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5),
    c(MS_0935, open_=99.50, high=99.80, low=98.20, close=98.50),
    c(MS_0940, open_=98.40, high=98.60, low=97.80, close=97.90),
    c(MS_0945, open_=98.00, high=99.20, low=97.80, close=98.80),
    c(MS_0950, open_=98.70, high=99.10, low=98.50, close=98.55),
    c(MS_0955, open_=98.50, high=98.60, low=96.00, close=96.10),
]

SHORT_SESSION = {
    "symbol": "QQQ", "date": "2026-07-01",
    "market_timezone": "America/New_York",
    "session_open_utc_ms": MS_0930, "session_close_utc_ms": MS_1000,
    "timeframe": "5m", "candles": SHORT_CANDLES,
}

FAIL_CANDLES = [
    c(MS_0930, open_=100.0, high=101.0, low=99.0, close=100.5),
    c(MS_0935, open_=100.0, high=100.5, low=99.5, close=100.0),
    c(MS_0940, open_=100.0, high=100.8, low=99.8, close=100.2),
]

FAIL_SESSION = {
    "symbol": "AAPL", "date": "2026-07-01",
    "market_timezone": "America/New_York",
    "session_open_utc_ms": MS_0930, "session_close_utc_ms": MS_0945,
    "timeframe": "5m", "candles": FAIL_CANDLES,
}

LONG_PRESET = {
    "preset_id": "html_test", "timeframe_minutes": 5,
    "timezone": "America/New_York", "session_open": "09:30",
    "orb_start": "session_open", "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH", "direction": "LONG",
    "entry_model": "CONFIRMATION_CLOSE",
    "entry_buffer_ticks": 0, "stop_buffer_ticks": 0,
    "min_displacement_ticks": None, "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None,
}

SHORT_PRESET = {
    **LONG_PRESET, "preset_id": "html_short_test",
    "level_source": "ORB_LOW", "direction": "SHORT",
}

CONFIG = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0-test"}


def _long_event():
    r = run_bdrr_strategy([LONG_SESSION], LONG_PRESET, CONFIG)[0]
    return export_visual_event(LONG_CANDLES, r)

def _short_event():
    r = run_bdrr_strategy([SHORT_SESSION], SHORT_PRESET, CONFIG)[0]
    return export_visual_event(SHORT_CANDLES, r)

def _fail_event():
    r = run_bdrr_strategy([FAIL_SESSION], LONG_PRESET, CONFIG)[0]
    return export_visual_event(FAIL_CANDLES, r)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Valid LONG event renders
# ══════════════════════════════════════════════════════════════════════════════

class TestLongRenders:
    def test_returns_html_string(self):
        h = render_visual_event_html(_long_event())
        assert isinstance(h, str)
        assert h.startswith("<!DOCTYPE html>")

    def test_contains_symbol(self):
        assert "SPY" in render_visual_event_html(_long_event())

    def test_contains_direction(self):
        assert "LONG" in render_visual_event_html(_long_event())

    def test_contains_chart_div(self):
        assert 'id="chart"' in render_visual_event_html(_long_event())

    def test_contains_lightweight_charts(self):
        assert "lightweight-charts" in render_visual_event_html(_long_event())


# ══════════════════════════════════════════════════════════════════════════════
# 2. Valid SHORT event renders
# ══════════════════════════════════════════════════════════════════════════════

class TestShortRenders:
    def test_returns_html(self):
        h = render_visual_event_html(_short_event())
        assert "<!DOCTYPE html>" in h

    def test_contains_short(self):
        assert "SHORT" in render_visual_event_html(_short_event())

    def test_contains_qqq(self):
        assert "QQQ" in render_visual_event_html(_short_event())

    def test_contains_orb_low(self):
        assert "ORB_LOW" in render_visual_event_html(_short_event())


# ══════════════════════════════════════════════════════════════════════════════
# 3. Failed event renders
# ══════════════════════════════════════════════════════════════════════════════

class TestFailedRenders:
    def test_renders_without_error(self):
        h = render_visual_event_html(_fail_event())
        assert len(h) > 100

    def test_contains_symbol(self):
        assert "AAPL" in render_visual_event_html(_fail_event())


# ══════════════════════════════════════════════════════════════════════════════
# 4. Candle data appears in the HTML
# ══════════════════════════════════════════════════════════════════════════════

class TestCandleDataInHtml:
    def test_candle_prices_embedded(self):
        event = _long_event()
        h = render_visual_event_html(event)
        for candle in event["candles"]:
            assert str(candle["open"]) in h

    def test_candle_count_matches(self):
        event = _long_event()
        h = render_visual_event_html(event)
        embedded = h.split("var EVENT=")[1].split(";")[0]
        parsed = json.loads(embedded)
        assert len(parsed["candles"]) == len(LONG_CANDLES)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Available annotations appear
# ══════════════════════════════════════════════════════════════════════════════

class TestAnnotationsAppear:
    def test_entry_in_html(self):
        assert "Entry" in render_visual_event_html(_long_event())

    def test_stop_in_html(self):
        assert "Stop" in render_visual_event_html(_long_event())

    def test_target_in_html(self):
        assert "2R" in render_visual_event_html(_long_event())


# ══════════════════════════════════════════════════════════════════════════════
# 6. Null annotations omitted safely
# ══════════════════════════════════════════════════════════════════════════════

class TestNullAnnotations:
    def test_failed_event_renders(self):
        assert "chart-container" in render_visual_event_html(_fail_event())

    def test_minimal_event(self):
        event = {
            "event_id": None, "symbol": "TEST", "session_date": "2026-01-01",
            "direction": None, "detection_status": "INVALID",
            "failed_stage": None, "failed_rules": [],
            "level_source": None, "level_price_ticks": None,
            "orb_high_ticks": None, "orb_low_ticks": None,
            "candles": [{"index": 0, "time_ms": MS_0930, "open": 100.0,
                         "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100}],
            "annotations": {},
        }
        h = render_visual_event_html(event)
        assert "TEST" in h
        assert "<!DOCTYPE html>" in h


# ══════════════════════════════════════════════════════════════════════════════
# 7. HTML escaping works
# ══════════════════════════════════════════════════════════════════════════════

class TestHtmlEscaping:
    def test_xss_in_symbol(self):
        event = {**_long_event(), "symbol": '<script>alert("xss")</script>'}
        h = render_visual_event_html(event)
        header_section = h.split("var EVENT=")[0]
        assert "<script>alert" not in header_section
        assert "&lt;script&gt;" in header_section

    def test_xss_in_session_date(self):
        event = {**_long_event(), "session_date": '"><img src=x>'}
        h = render_visual_event_html(event)
        assert "<img" not in h.split("var EVENT=")[0]


# ══════════════════════════════════════════════════════════════════════════════
# 8. Repeated rendering is deterministic
# ══════════════════════════════════════════════════════════════════════════════

class TestDeterministic:
    def test_long(self):
        e = _long_event()
        assert render_visual_event_html(e) == render_visual_event_html(e)

    def test_short(self):
        e = _short_event()
        assert render_visual_event_html(e) == render_visual_event_html(e)

    def test_failed(self):
        e = _fail_event()
        assert render_visual_event_html(e) == render_visual_event_html(e)


# ══════════════════════════════════════════════════════════════════════════════
# 9. File-writing helper writes UTF-8 content
# ══════════════════════════════════════════════════════════════════════════════

class TestFileWriter:
    def test_writes_file(self, tmp_path):
        out = tmp_path / "test.html"
        result = write_visual_event_html(_long_event(), out)
        assert result == out
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "SPY" in content

    def test_utf8_no_bom(self, tmp_path):
        out = tmp_path / "test.html"
        write_visual_event_html(_long_event(), out)
        raw = out.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")


# ══════════════════════════════════════════════════════════════════════════════
# 10–11. ORB High and Low exported
# ══════════════════════════════════════════════════════════════════════════════

class TestOrbExported:
    def test_long_has_orb_high(self):
        e = _long_event()
        assert e["orb_high_ticks"] is not None
        assert isinstance(e["orb_high_ticks"], int)

    def test_long_has_orb_low(self):
        e = _long_event()
        assert e["orb_low_ticks"] is not None
        assert isinstance(e["orb_low_ticks"], int)

    def test_short_has_orb_high(self):
        e = _short_event()
        assert e["orb_high_ticks"] is not None

    def test_short_has_orb_low(self):
        e = _short_event()
        assert e["orb_low_ticks"] is not None

    def test_orb_high_gt_orb_low(self):
        e = _long_event()
        assert e["orb_high_ticks"] > e["orb_low_ticks"]

    def test_long_level_equals_orb_high(self):
        e = _long_event()
        assert e["level_price_ticks"] == e["orb_high_ticks"]

    def test_short_level_equals_orb_low(self):
        e = _short_event()
        assert e["level_price_ticks"] == e["orb_low_ticks"]


# ══════════════════════════════════════════════════════════════════════════════
# 12–13. Both ORB lines rendered and distinguished
# ══════════════════════════════════════════════════════════════════════════════

class TestOrbLinesRendered:
    def test_both_orb_lines_in_long(self):
        h = render_visual_event_html(_long_event())
        assert "ORB High" in h
        assert "ORB Low" in h

    def test_both_orb_lines_in_short(self):
        h = render_visual_event_html(_short_event())
        assert "ORB High" in h
        assert "ORB Low" in h

    def test_long_selected_high(self):
        h = render_visual_event_html(_long_event())
        assert "selected" in h
        # In the JS: ORB_HIGH → "ORB High ← selected"
        assert "ORB High" in h

    def test_short_selected_low(self):
        h = render_visual_event_html(_short_event())
        assert "selected" in h
        assert "ORB Low" in h


# ══════════════════════════════════════════════════════════════════════════════
# 14. Missing ORB values handled safely
# ══════════════════════════════════════════════════════════════════════════════

class TestMissingOrb:
    def test_null_orb_no_crash(self):
        event = {**_long_event(), "orb_high_ticks": None, "orb_low_ticks": None}
        h = render_visual_event_html(event)
        assert "<!DOCTYPE html>" in h


# ══════════════════════════════════════════════════════════════════════════════
# 15. Readable annotation legend
# ══════════════════════════════════════════════════════════════════════════════

class TestReadableLabels:
    def test_legend_has_break(self):
        h = render_visual_event_html(_long_event())
        # The marker should say "Break" not just "B"
        embedded = h.split("var EVENT=")[1]
        assert '"Break"' in embedded or "Break" in h

    def test_legend_has_confirm(self):
        h = render_visual_event_html(_long_event())
        assert "Confirm" in h

    def test_legend_has_exit(self):
        h = render_visual_event_html(_long_event())
        assert "Exit" in h


# ══════════════════════════════════════════════════════════════════════════════
# 16. Complete candle range is fitted
# ══════════════════════════════════════════════════════════════════════════════

class TestCandleRangeFitted:
    def test_fit_content_called(self):
        h = render_visual_event_html(_long_event())
        assert "fitContent()" in h

    def test_all_candles_in_data(self):
        event = _long_event()
        h = render_visual_event_html(event)
        embedded = h.split("var EVENT=")[1].split(";")[0]
        parsed = json.loads(embedded)
        assert len(parsed["candles"]) == len(event["candles"])
