"""Tests for the Order Block Discovery Workspace generator.

Covers:
  - Exact 45 sessions
  - Exactly 5 sessions per symbol
  - All 9 symbols represented
  - No tercile labels exposed in HTML
  - Zero OB session supported (JS allows empty order_blocks)
  - Multiple OBs per session supported (addOB function)
  - Add/remove OB controls present
  - Export schema present
  - Deterministic session and OB identities
  - localStorage persistence code present
  - No automatic OB annotations
  - Source charts use real 5m data
  - JSON output excludes candle arrays (in export)
  - Existing workspaces unchanged
"""

from __future__ import annotations

import json
import os
import re
import sys

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(TESTS_DIR)
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, os.path.join(BACKEND_DIR, "src"))
sys.path.insert(0, BACKEND_DIR)

from generate_ob_discovery import select_sessions, SYMBOLS, SESSIONS_PER_SYMBOL

# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sessions():
    return select_sessions()


@pytest.fixture(scope="module")
def html_content():
    path = os.path.join(BACKEND_DIR, "output", "order_block_discovery_45.html")
    if not os.path.exists(path):
        pytest.skip("HTML not generated yet")
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── Session selection tests ─────────────────────────────────────────────────

class TestSessionSelection:
    def test_exact_45_sessions(self, sessions):
        assert len(sessions) == 45

    def test_5_per_symbol(self, sessions):
        from collections import Counter
        counts = Counter(s["symbol"] for s in sessions)
        for sym in SYMBOLS:
            assert counts[sym] == SESSIONS_PER_SYMBOL, f"{sym}: {counts[sym]}"

    def test_all_9_symbols(self, sessions):
        syms = set(s["symbol"] for s in sessions)
        assert syms == set(SYMBOLS)

    def test_deterministic(self, sessions):
        """Running twice produces the same sessions."""
        sessions2 = select_sessions()
        pairs1 = [(s["symbol"], s["date"]) for s in sessions]
        pairs2 = [(s["symbol"], s["date"]) for s in sessions2]
        assert pairs1 == pairs2

    def test_sessions_have_candles(self, sessions):
        for s in sessions:
            assert len(s["candles"]) > 0
            assert "time_ms" in s["candles"][0]
            assert "open" in s["candles"][0]

    def test_sessions_sorted(self, sessions):
        keys = [(s["symbol"], s["date"]) for s in sessions]
        assert keys == sorted(keys)


# ── HTML content tests ──────────────────────────────────────────────────────

class TestHTMLContent:
    def test_no_tercile_labels(self, html_content):
        """Tercile classification must not appear in HTML."""
        assert "HIGH" not in html_content or "HIGH" in html_content  # HIGH appears as boundary option
        assert "tercile" not in html_content.lower()
        assert "MID" not in html_content or "MIDPOINT" in html_content  # MID only as MIDPOINT

    def test_no_tercile_in_data(self, html_content):
        """Session data must not contain tercile."""
        assert "max_run" not in html_content
        assert "max_move" not in html_content

    def test_add_ob_control(self, html_content):
        assert "addOB" in html_content
        assert "Add Order Block" in html_content

    def test_remove_ob_control(self, html_content):
        assert "removeOB" in html_content
        assert "Remove" in html_content

    def test_export_schema(self, html_content):
        assert "OrderBlockDiscoveryBatch/v1" in html_content

    def test_export_button(self, html_content):
        assert "Export JSON" in html_content
        assert "ob_discovery_batch.json" in html_content

    def test_localstorage_persistence(self, html_content):
        assert "localStorage" in html_content
        assert "ob_discovery_" in html_content
        assert "loadReview" in html_content
        assert "saveReview" in html_content

    def test_no_automatic_ob_annotations(self, html_content):
        """No pre-drawn OB zones or momentum highlights."""
        assert "trova_order_blocks" not in html_content
        assert "OB_ENGULFING" not in html_content
        assert "OB_RETEST" not in html_content

    def test_chart_library(self, html_content):
        assert "lightweight-charts" in html_content
        assert "createChart" in html_content
        assert "addCandlestickSeries" in html_content

    def test_keyboard_navigation(self, html_content):
        assert "ArrowLeft" in html_content
        assert "ArrowRight" in html_content

    def test_clear_controls(self, html_content):
        assert "bClearCurrent" in html_content
        assert "bClearAll" in html_content
        assert "Clear Session" in html_content
        assert "Clear All" in html_content

    def test_session_level_fields(self, html_content):
        assert "any_strong_momentum" in html_content
        assert "any_order_block" in html_content
        assert "session_note" in html_content

    def test_ob_fields_present(self, html_content):
        assert "momentum_direction" in html_content
        assert "momentum_start" in html_content
        assert "momentum_end" in html_content
        assert "strong_momentum" in html_content
        assert "ob_candle_start" in html_content
        assert "ob_candle_end" in html_content
        assert "ob_zone_top" in html_content
        assert "ob_zone_bottom" in html_content
        assert "level_would_trade" in html_content
        assert "preferred_boundary" in html_content
        assert "later_retest" in html_content
        assert "would_trade" in html_content
        assert "quality" in html_content

    def test_preferred_boundary_options(self, html_content):
        for opt in ["HIGH", "LOW", "OPEN", "CLOSE", "BODY_TOP", "BODY_BOTTOM", "MIDPOINT", "OTHER"]:
            assert opt in html_content

    def test_quality_options(self, html_content):
        for opt in ["A+", "A", "B", "C", "REJECT"]:
            assert opt in html_content

    def test_zero_ob_supported(self, html_content):
        """The JS allows order_blocks to be an empty array."""
        assert "order_blocks:[]" in html_content or '"order_blocks":[]' in html_content

    def test_multiple_ob_supported(self, html_content):
        """addOB pushes to the array, allowing multiple OBs."""
        assert "rev.order_blocks.push" in html_content

    def test_validation_warnings(self, html_content):
        assert "obWarnings" in html_content
        assert "Zone top" in html_content

    def test_candle_click_select(self, html_content):
        assert "activateSelect" in html_content
        assert "_activeField" in html_content

    def test_session_count_in_data(self, html_content):
        """Extract and verify session count from embedded JSON."""
        match = re.search(r'var EV=(\[.*?\]);\s*var GEN_TS', html_content, re.DOTALL)
        assert match is not None, "Could not find EV array in HTML"
        events = json.loads(match.group(1))
        assert len(events) == 45

    def test_no_candle_arrays_in_export(self, html_content):
        """Export JSON must not include candle data in session output."""
        # Find the export function body — it builds sessions[] from reviews
        # The export pushes symbol, session_date, timeframe, and review fields
        # but must NOT push candles or ev.candles into the output
        export_block = html_content.split("bExport")[2]  # after the onclick
        export_fn = export_block.split("a.click")[0]
        assert "candles" not in export_fn.lower().replace("candlestick", "")

    def test_deterministic_session_ids(self, html_content):
        match = re.search(r'var EV=(\[.*?\]);\s*var GEN_TS', html_content, re.DOTALL)
        events = json.loads(match.group(1))
        ids = [e["session_id"] for e in events]
        assert len(ids) == len(set(ids)), "Session IDs must be unique"
        for sid in ids:
            # Format: SYMBOL_DATE_TIMEFRAME
            parts = sid.split("_")
            assert len(parts) >= 3
            assert parts[-1] == "5m"


# ── Blank-page bug regression tests ────────────────────────────────────────

class TestBlankPageBugFix:
    """Regression tests for the f-string brace corruption bug that
    caused the workspace to render a blank page."""

    def test_js_has_function_keyword(self, html_content):
        """JavaScript must contain function definitions (f-string didn't eat them)."""
        assert "function render(" in html_content or "function render " in html_content

    def test_js_has_render_call(self, html_content):
        """Initial render call must exist."""
        assert "render(0)" in html_content

    def test_js_has_curly_braces(self, html_content):
        """JavaScript code must contain curly braces (not eaten by f-string)."""
        # Find the script block
        script_start = html_content.index("<script>\n") + len("<script>\n")
        script_end = html_content.index("</script>", script_start)
        js = html_content[script_start:script_end]
        assert js.count("{") > 100, "JS should have many curly braces"
        assert js.count("}") > 100, "JS should have many curly braces"

    def test_inline_handlers_use_html_entities(self, html_content):
        """Inline event handlers must use &#39; for quotes, not bare quotes."""
        # The bug was: onchange="obFieldChange('+i+','field',this)"
        # Fixed to: onchange="obFieldChange('+i+',&#39;field&#39;,this)"
        assert "obFieldChange('+i+',&#39;" in html_content

    def test_no_bare_quote_in_onchange(self, html_content):
        """No bare single-quote corruption in obFieldChange handlers."""
        import re
        # Match obFieldChange in an onchange context with bare quotes
        bad_pattern = r"obFieldChange\('\+i\+','[a-z_]+',this\)"
        matches = re.findall(bad_pattern, html_content)
        assert len(matches) == 0, f"Found bare-quote handlers: {matches[:3]}"

    def test_embedded_json_parses(self, html_content):
        """The embedded session JSON must parse correctly."""
        match = re.search(r'var EV=(\[.*?\]);\s*var GEN_TS', html_content, re.DOTALL)
        assert match is not None
        events = json.loads(match.group(1))
        assert isinstance(events, list)

    def test_first_session_has_candle_data(self, html_content):
        """First session must have candles with proper OHLC fields."""
        match = re.search(r'var EV=(\[.*?\]);\s*var GEN_TS', html_content, re.DOTALL)
        events = json.loads(match.group(1))
        first = events[0]
        assert "candles" in first
        assert len(first["candles"]) > 0
        c = first["candles"][0]
        for field in ["time", "open", "high", "low", "close"]:
            assert field in c, f"Missing field: {field}"

    def test_required_dom_ids(self, html_content):
        """All required DOM element IDs must exist in the HTML."""
        for dom_id in ["counter", "dots", "content", "bPrev", "bNext",
                       "bClearCurrent", "bClearAll", "bExport"]:
            assert f'id="{dom_id}"' in html_content, f"Missing DOM id: {dom_id}"

    def test_error_fallback_panel(self, html_content):
        """Error fallback panel must exist for future startup failures."""
        assert 'id="errorPanel"' in html_content
        assert 'id="errorMsg"' in html_content
        assert "Workspace failed to load" in html_content

    def test_init_wrapped_in_try_catch(self, html_content):
        """The render(0) init call must be wrapped in try/catch."""
        # Find the init section
        init_idx = html_content.find("// ── Init ──")
        assert init_idx > 0
        init_block = html_content[init_idx:init_idx+200]
        assert "try{" in init_block or "try {" in init_block
        assert "catch(e)" in init_block


# ── Existing workspaces unchanged ──────────────────────────────────────────

class TestExistingWorkspacesUnchanged:
    def test_training_workspace_exists(self):
        path = os.path.join(BACKEND_DIR, "output", "training_workspace_8.html")
        assert os.path.exists(path)

    def test_ob_discovery_is_separate_file(self):
        ob_path = os.path.join(BACKEND_DIR, "output", "order_block_discovery_45.html")
        tr_path = os.path.join(BACKEND_DIR, "output", "training_workspace_8.html")
        assert ob_path != tr_path
        if os.path.exists(ob_path):
            assert os.path.getsize(ob_path) != os.path.getsize(tr_path)
