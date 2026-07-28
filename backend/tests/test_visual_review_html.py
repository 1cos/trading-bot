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
        "time_ms": time_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


# ── Sessions ────────────────────────────────────────────────────────────────

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


# ── Helpers ─────────────────────────────────────────────────────────────────

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
        html = render_visual_event_html(_long_event())
        assert isinstance(html, str)
        assert html.startswith("<!DOCTYPE html>")

    def test_contains_symbol(self):
        html = render_visual_event_html(_long_event())
        assert "SPY" in html

    def test_contains_direction(self):
        html = render_visual_event_html(_long_event())
        assert "LONG" in html

    def test_contains_chart_div(self):
        html = render_visual_event_html(_long_event())
        assert 'id="chart"' in html

    def test_contains_lightweight_charts(self):
        html = render_visual_event_html(_long_event())
        assert "lightweight-charts" in html


# ══════════════════════════════════════════════════════════════════════════════
# 2. Valid SHORT event renders
# ══════════════════════════════════════════════════════════════════════════════

class TestShortRenders:
    def test_returns_html_string(self):
        html = render_visual_event_html(_short_event())
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_contains_short_direction(self):
        html = render_visual_event_html(_short_event())
        assert "SHORT" in html

    def test_contains_symbol(self):
        html = render_visual_event_html(_short_event())
        assert "QQQ" in html

    def test_contains_orb_low(self):
        html = render_visual_event_html(_short_event())
        assert "ORB_LOW" in html


# ══════════════════════════════════════════════════════════════════════════════
# 3. Failed event renders
# ══════════════════════════════════════════════════════════════════════════════

class TestFailedRenders:
    def test_renders_without_error(self):
        html = render_visual_event_html(_fail_event())
        assert isinstance(html, str)
        assert len(html) > 100

    def test_contains_symbol(self):
        html = render_visual_event_html(_fail_event())
        assert "AAPL" in html

    def test_no_crash_on_null_annotations(self):
        event = _fail_event()
        html = render_visual_event_html(event)
        assert "chart-container" in html


# ══════════════════════════════════════════════════════════════════════════════
# 4. Candle data appears in the HTML
# ══════════════════════════════════════════════════════════════════════════════

class TestCandleDataInHtml:
    def test_candle_prices_embedded(self):
        event = _long_event()
        html = render_visual_event_html(event)
        # The embedded JSON should contain candle OHLC values
        for candle in event["candles"]:
            assert str(candle["open"]) in html

    def test_candle_count_matches(self):
        event = _long_event()
        html = render_visual_event_html(event)
        # Count candle objects in the embedded JSON
        embedded = html.split("var EVENT=")[1].split(";")[0]
        parsed = json.loads(embedded)
        assert len(parsed["candles"]) == len(LONG_CANDLES)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Available annotations appear
# ══════════════════════════════════════════════════════════════════════════════

class TestAnnotationsAppear:
    def test_entry_price_in_html(self):
        event = _long_event()
        html = render_visual_event_html(event)
        ann = event["annotations"]
        if ann.get("entry_price_ticks") is not None:
            assert "Entry" in html

    def test_stop_price_in_html(self):
        event = _long_event()
        html = render_visual_event_html(event)
        ann = event["annotations"]
        if ann.get("stop_price_ticks") is not None:
            assert "Stop" in html

    def test_target_in_html(self):
        event = _long_event()
        html = render_visual_event_html(event)
        ann = event["annotations"]
        if ann.get("r2_price_ticks") is not None:
            assert "2R" in html


# ══════════════════════════════════════════════════════════════════════════════
# 6. Null annotations omitted safely
# ══════════════════════════════════════════════════════════════════════════════

class TestNullAnnotations:
    def test_failed_event_no_entry_line(self):
        event = _fail_event()
        ann = event["annotations"]
        # For failed events, entry/stop/targets should be null
        assert ann.get("entry_price_ticks") is None
        html = render_visual_event_html(event)
        # Should still render without errors
        assert "chart-container" in html

    def test_minimal_event(self):
        """Completely minimal event with no annotations."""
        event = {
            "event_id": None,
            "symbol": "TEST",
            "session_date": "2026-01-01",
            "direction": None,
            "detection_status": "INVALID",
            "failed_stage": None,
            "failed_rules": [],
            "level_source": None,
            "level_price_ticks": None,
            "candles": [
                {"index": 0, "time_ms": MS_0930, "open": 100.0,
                 "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100},
            ],
            "annotations": {},
        }
        html = render_visual_event_html(event)
        assert "TEST" in html
        assert "<!DOCTYPE html>" in html


# ══════════════════════════════════════════════════════════════════════════════
# 7. HTML escaping works
# ══════════════════════════════════════════════════════════════════════════════

class TestHtmlEscaping:
    def test_xss_in_symbol(self):
        event = _long_event()
        event = {**event, "symbol": '<script>alert("xss")</script>'}
        html = render_visual_event_html(event)
        # The HTML header area must escape the symbol
        header_section = html.split("var EVENT=")[0]
        assert "<script>alert" not in header_section
        assert "&lt;script&gt;" in header_section

    def test_xss_in_session_date(self):
        event = _long_event()
        event = {**event, "session_date": '"><img src=x>'}
        html = render_visual_event_html(event)
        assert "<img" not in html.split("var EVENT=")[0]


# ══════════════════════════════════════════════════════════════════════════════
# 8. Repeated rendering is deterministic
# ══════════════════════════════════════════════════════════════════════════════

class TestDeterministic:
    def test_long_deterministic(self):
        event = _long_event()
        h1 = render_visual_event_html(event)
        h2 = render_visual_event_html(event)
        assert h1 == h2

    def test_short_deterministic(self):
        event = _short_event()
        h1 = render_visual_event_html(event)
        h2 = render_visual_event_html(event)
        assert h1 == h2

    def test_failed_deterministic(self):
        event = _fail_event()
        h1 = render_visual_event_html(event)
        h2 = render_visual_event_html(event)
        assert h1 == h2


# ══════════════════════════════════════════════════════════════════════════════
# 9. File-writing helper writes UTF-8 content
# ══════════════════════════════════════════════════════════════════════════════

class TestFileWriter:
    def test_writes_file(self, tmp_path):
        event = _long_event()
        out = tmp_path / "test_chart.html"
        result = write_visual_event_html(event, out)
        assert result == out
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "SPY" in content

    def test_overwrites_existing(self, tmp_path):
        event = _long_event()
        out = tmp_path / "test_chart.html"
        out.write_text("old content", encoding="utf-8")
        write_visual_event_html(event, out)
        content = out.read_text(encoding="utf-8")
        assert "old content" not in content
        assert "<!DOCTYPE html>" in content

    def test_utf8_encoding(self, tmp_path):
        event = _long_event()
        out = tmp_path / "test_chart.html"
        write_visual_event_html(event, out)
        raw = out.read_bytes()
        assert b"<!DOCTYPE html>" in raw
        # Verify no BOM
        assert not raw.startswith(b"\xef\xbb\xbf")
