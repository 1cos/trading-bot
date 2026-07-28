"""Tests for full session context export.

Verifies:
  1. Complete session candles exported (not just event window)
  2. Event annotations still point to correct candles
  3. Renderer correctly displays the full candle set
  4. Repeated export remains deterministic
  5. Annotations survive larger candle arrays
"""

import json

from trading_lab.strategy_runner import run_bdrr_strategy
from trading_lab.visual_review_exporter import (
    export_visual_event,
    serialize_visual_events,
)
from trading_lab.visual_review_html import render_visual_event_html


# ── Timestamps ──────────────────────────────────────────────────────────────

MS_0930 = 1782912600000


def _ms(offset_minutes):
    return MS_0930 + offset_minutes * 60_000


def c(time_ms, open_=100.0, high=101.0, low=99.0, close=100.5, volume=100):
    return {
        "time_ms": time_ms, "open": open_, "high": high,
        "low": low, "close": close, "volume": volume,
    }


# ── Build a 30-candle session ───────────────────────────────────────────────
# Candles 0–5: the event (ORB, break, displacement, retest, rejection, post)
# Candles 6–29: additional session context after the event

def _build_full_session_candles():
    """30 candles: 6 event + 24 post-event context."""
    event = [
        c(_ms(0),  open_=100.0, high=101.0, low=99.0, close=100.5),   # 0: ORB
        c(_ms(5),  open_=100.50, high=102.0, low=100.20, close=101.50), # 1: break
        c(_ms(10), open_=101.60, high=102.50, low=101.20, close=102.00), # 2: displ
        c(_ms(15), open_=101.80, high=102.20, low=100.80, close=101.20), # 3: retest
        c(_ms(20), open_=101.30, high=101.50, low=100.90, close=101.40), # 4: confirm
        c(_ms(25), open_=101.40, high=103.00, low=101.30, close=102.80), # 5: post
    ]
    # Generate 24 more candles drifting around 102
    context = []
    price = 102.80
    for i in range(24):
        offset = 30 + i * 5
        drift = 0.10 if i % 3 == 0 else -0.05
        price += drift
        o = round(price, 2)
        h = round(price + 0.30, 2)
        l = round(price - 0.20, 2)
        cl = round(price + 0.05, 2)
        context.append(c(_ms(offset), open_=o, high=h, low=l, close=cl))
    return event + context


FULL_CANDLES = _build_full_session_candles()

FULL_SESSION = {
    "symbol": "SPY",
    "date": "2026-07-01",
    "market_timezone": "America/New_York",
    "session_open_utc_ms": MS_0930,
    "session_close_utc_ms": _ms(150),  # 12:00
    "timeframe": "5m",
    "candles": FULL_CANDLES,
}

PRESET = {
    "preset_id": "full_ctx_test",
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "entry_model": "CONFIRMATION_CLOSE",
    "entry_buffer_ticks": 0,
    "stop_buffer_ticks": 0,
    "min_displacement_ticks": None,
    "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None,
}

CONFIG = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0-test"}


def _run():
    return run_bdrr_strategy([FULL_SESSION], PRESET, CONFIG)[0]


def _export():
    result = _run()
    return export_visual_event(FULL_CANDLES, result)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Complete session candles exported
# ══════════════════════════════════════════════════════════════════════════════


class TestFullSessionExported:
    def test_all_30_candles_present(self):
        event = _export()
        assert len(event["candles"]) == 30

    def test_candle_indexes_are_absolute(self):
        event = _export()
        for i, ec in enumerate(event["candles"]):
            assert ec["index"] == i

    def test_first_candle_is_orb(self):
        event = _export()
        assert event["candles"][0]["time_ms"] == MS_0930

    def test_last_candle_is_session_end(self):
        event = _export()
        last = event["candles"][-1]
        assert last["time_ms"] == _ms(29 * 5)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Event annotations still point to correct candles
# ══════════════════════════════════════════════════════════════════════════════


class TestAnnotationAbsoluteIndexes:
    def test_break_at_index_1(self):
        event = _export()
        ann = event["annotations"]
        assert ann["break_candle_index"] == 1

    def test_break_time_matches_candle(self):
        event = _export()
        ann = event["annotations"]
        bi = ann["break_candle_index"]
        bt = ann["break_candle_time_ms"]
        assert event["candles"][bi]["time_ms"] == bt

    def test_confirmation_at_index_4(self):
        event = _export()
        ann = event["annotations"]
        assert ann["confirmation_candle_index"] == 4

    def test_displacement_indexes_valid(self):
        event = _export()
        ann = event["annotations"]
        ds = ann["displacement_start_index"]
        de = ann["displacement_end_index"]
        assert ds is not None
        assert de is not None
        assert ds <= de
        assert ds < 30
        assert de < 30

    def test_annotations_unchanged_vs_small_session(self):
        """Annotations must be identical whether 6 or 30 candles passed."""
        small_candles = FULL_CANDLES[:6]
        small_session = {**FULL_SESSION, "candles": small_candles,
                         "session_close_utc_ms": _ms(30)}
        small_result = run_bdrr_strategy([small_session], PRESET, CONFIG)[0]
        small_event = export_visual_event(small_candles, small_result)

        full_event = _export()

        # Key annotation indexes must be identical
        for key in ("break_candle_index", "confirmation_candle_index",
                    "displacement_start_index", "displacement_end_index",
                    "entry_price_ticks", "stop_price_ticks"):
            assert full_event["annotations"][key] == small_event["annotations"][key], \
                f"{key} differs: full={full_event['annotations'][key]} vs small={small_event['annotations'][key]}"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Renderer correctly displays the full candle set
# ══════════════════════════════════════════════════════════════════════════════


class TestRendererFullSession:
    def test_renders_without_error(self):
        event = _export()
        html = render_visual_event_html(event)
        assert "<!DOCTYPE html>" in html

    def test_all_candles_in_embedded_json(self):
        event = _export()
        html = render_visual_event_html(event)
        embedded = html.split("var EVENT=")[1].split(";")[0]
        parsed = json.loads(embedded)
        assert len(parsed["candles"]) == 30

    def test_fit_content_called(self):
        event = _export()
        html = render_visual_event_html(event)
        assert "fitContent()" in html

    def test_chart_container_present(self):
        event = _export()
        html = render_visual_event_html(event)
        assert "chart-container" in html


# ══════════════════════════════════════════════════════════════════════════════
# 4. Repeated export remains deterministic
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterministicFullSession:
    def test_export_deterministic(self):
        """Same runner result → same export (event_id may be random)."""
        result = _run()
        e1 = export_visual_event(FULL_CANDLES, result)
        e2 = export_visual_event(FULL_CANDLES, result)
        s1 = serialize_visual_events([e1])
        s2 = serialize_visual_events([e2])
        assert s1 == s2

    def test_render_deterministic(self):
        event = _export()
        h1 = render_visual_event_html(event)
        h2 = render_visual_event_html(event)
        assert h1 == h2
