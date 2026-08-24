"""Tests for premarket context rendering in the PWA audit timeline.

Scope: dashboard.html::appendEvents() — the SYMBOL_ENABLED branch that
renders `data.premarket_context` as factual, neutral lines in the
existing event timeline. No backend logic is exercised here; this is a
pure frontend-rendering regression suite.

Approach: the real `appendEvents` function body is extracted verbatim
from the shipped dashboard.html (no reimplementation, no copy-paste
drift) and executed in Node.js against a minimal DOM shim that
implements only what the function actually touches:
document.getElementById / document.createElement, and a fake
`timeline` container with insertBefore/firstChild semantics. This
mirrors real browser behavior for the one function under test without
pulling in a browser/jsdom dependency.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

DASHBOARD_HTML = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "trading_lab"
    / "live"
    / "ui"
    / "dashboard.html"
)

NODE = shutil.which("node")


def _extract_append_events_source() -> str:
    html = DASHBOARD_HTML.read_text()
    m = re.search(r"function appendEvents\(events\) \{.*?\n\}\n", html, re.S)
    assert m, "appendEvents() not found in dashboard.html — has it been renamed/moved?"
    return m.group(0)


APPEND_EVENTS_SRC = _extract_append_events_source()

HARNESS_TEMPLATE = """
'use strict';
let lastSeq = 0;

class FakeEl {{
  constructor(tag) {{ this.tagName = tag; this.className = ''; this.innerHTML = ''; }}
}}

class FakeContainer {{
  constructor() {{ this.kids = []; }}
  get firstChild() {{ return this.kids.length ? this.kids[0] : null; }}
  insertBefore(node, ref) {{
    if (ref === null || ref === undefined) {{ this.kids.push(node); return; }}
    const idx = this.kids.indexOf(ref);
    this.kids.splice(idx === -1 ? this.kids.length : idx, 0, node);
  }}
}}

const timeline = new FakeContainer();
const document = {{
  getElementById(id) {{ if (id === 'timeline') return timeline; return new FakeEl('div'); }},
  createElement(tag) {{ return new FakeEl(tag); }},
}};

{fn_src}

const events = JSON.parse(process.argv[1]);
appendEvents(events);
process.stdout.write(JSON.stringify(timeline.kids.map(k => k.innerHTML)));
"""


def run_append_events(events: list[dict]) -> list[str]:
    """Runs the real appendEvents() against `events` in Node, returns
    the rendered innerHTML for each resulting event row (newest first,
    matching real insertBefore-at-front behavior)."""
    assert NODE, "node binary not found on PATH — required for JS harness tests"
    script = HARNESS_TEMPLATE.format(fn_src=APPEND_EVENTS_SRC)
    r = subprocess.run(
        [NODE, "-e", script, "--", json.dumps(events)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert r.returncode == 0, f"node harness failed:\nSTDOUT={r.stdout}\nSTDERR={r.stderr}"
    return json.loads(r.stdout)


def _symbol_enabled_event(premarket_context, seq=1, symbol="SPY"):
    return {
        "seq": seq,
        "event_type": "SYMBOL_ENABLED",
        "symbol": symbol,
        "timestamp_ms": None,
        "data": {"premarket_context": premarket_context},
    }


pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


# ── U1 — NONE / LONG ──────────────────────────────────────────────────────

class TestU1NoneLong:
    def test_none_long_renders_no_break_observed(self):
        ctx = {"LONG": {"break_origin": "NONE", "break_timestamp_ms": None,
                         "level_source": "PREVIOUS_DAY_HIGH", "level_price": 103.0}}
        [html] = run_append_events([_symbol_enabled_event(ctx)])
        assert "PDH: No premarket break observed" in html


# ── U2 — PREMARKET_OBSERVED / LONG ───────────────────────────────────────

class TestU2ObservedLong:
    def test_observed_long_renders_timestamp(self):
        # 2025-01-15 07:42:00 CT (CST, UTC-6)
        ts_ms = 1736948520000
        ctx = {"LONG": {"break_origin": "PREMARKET_OBSERVED", "break_timestamp_ms": ts_ms,
                         "level_source": "PREVIOUS_DAY_HIGH", "level_price": 103.0}}
        [html] = run_append_events([_symbol_enabled_event(ctx)])
        assert "PDH: Premarket break observed at 07:42 CT" in html


# ── U3 — PREMARKET_CARRY_IN / LONG ───────────────────────────────────────

class TestU3CarryInLong:
    def test_carry_in_long(self):
        ctx = {"LONG": {"break_origin": "PREMARKET_CARRY_IN", "break_timestamp_ms": None,
                         "level_source": "PREVIOUS_DAY_HIGH", "level_price": 103.0}}
        [html] = run_append_events([_symbol_enabled_event(ctx)])
        assert "PDH: Already beyond level on first premarket bar" in html


# ── U4 — SHORT / PDL mapping ──────────────────────────────────────────────

class TestU4ShortPdl:
    def test_short_maps_to_pdl(self):
        ctx = {"SHORT": {"break_origin": "NONE", "break_timestamp_ms": None,
                          "level_source": "PREVIOUS_DAY_LOW", "level_price": 97.0}}
        [html] = run_append_events([_symbol_enabled_event(ctx)])
        assert "PDL: No premarket break observed" in html
        assert "PDH" not in html

    def test_short_observed_pdl_timestamp(self):
        ts_ms = 1736945880000  # 2025-01-15 06:58:00 CT (CST, UTC-6)
        ctx = {"SHORT": {"break_origin": "PREMARKET_OBSERVED", "break_timestamp_ms": ts_ms,
                          "level_source": "PREVIOUS_DAY_LOW", "level_price": 97.0}}
        [html] = run_append_events([_symbol_enabled_event(ctx)])
        assert "PDL: Premarket break observed at 06:58 CT" in html


# ── U5 — LONG + SHORT together ────────────────────────────────────────────

class TestU5BothDirections:
    def test_both_directions_rendered(self):
        ctx = {
            "LONG": {"break_origin": "NONE", "break_timestamp_ms": None,
                     "level_source": "PREVIOUS_DAY_HIGH", "level_price": 103.0},
            "SHORT": {"break_origin": "PREMARKET_CARRY_IN", "break_timestamp_ms": None,
                      "level_source": "PREVIOUS_DAY_LOW", "level_price": 97.0},
        }
        [html] = run_append_events([_symbol_enabled_event(ctx)])
        assert "PDH: No premarket break observed" in html
        assert "PDL: Already beyond level on first premarket bar" in html


# ── U6 — context absent (legacy event) ────────────────────────────────────

class TestU6ContextAbsent:
    def test_legacy_event_unchanged(self):
        e = {"seq": 1, "event_type": "SYMBOL_ENABLED", "symbol": "SPY",
             "timestamp_ms": None, "data": {}}
        [html] = run_append_events([e])
        assert "sym-detail" not in html
        assert "PDH" not in html and "PDL" not in html

    def test_empty_context_dict_unchanged(self):
        [html] = run_append_events([_symbol_enabled_event({})])
        assert "sym-detail" not in html


# ── U7 — unknown break_origin ──────────────────────────────────────────────

class TestU7UnknownBreakOrigin:
    def test_unknown_origin_omitted_no_crash(self):
        ctx = {"LONG": {"break_origin": "SOMETHING_NEW", "break_timestamp_ms": None,
                         "level_source": "PREVIOUS_DAY_HIGH", "level_price": 103.0}}
        [html] = run_append_events([_symbol_enabled_event(ctx)])
        assert "sym-detail" not in html
        assert "SOMETHING_NEW" not in html


# ── U8 — observed timestamp missing ────────────────────────────────────────

class TestU8ObservedMissingTimestamp:
    def test_no_invalid_date_artifacts(self):
        ctx = {"LONG": {"break_origin": "PREMARKET_OBSERVED", "break_timestamp_ms": None,
                         "level_source": "PREVIOUS_DAY_HIGH", "level_price": 103.0}}
        [html] = run_append_events([_symbol_enabled_event(ctx)])
        assert "PDH: Premarket break observed" in html
        assert "at " not in html.split("Premarket break observed")[1][:5]
        for bad in ("Invalid Date", "NaN", "undefined", "null"):
            assert bad not in html


# ── U9 — existing branches unaffected ──────────────────────────────────────

class TestU9ExistingBranchesUnchanged:
    def test_option_selected_unaffected(self):
        e = {"seq": 1, "event_type": "OPTION_SELECTED", "symbol": "SPY",
             "timestamp_ms": None, "data": {"strike": 450, "right": "C", "bid": 1.2, "ask": 1.3}}
        [html] = run_append_events([e])
        assert "CALL 450 selected" in html
        assert "sym-detail" not in html

    def test_entry_order_built_unaffected(self):
        e = {"seq": 1, "event_type": "ENTRY_ORDER_BUILT", "symbol": "SPY",
             "timestamp_ms": None, "data": {"limit_price": 1.25}}
        [html] = run_append_events([e])
        assert "BUY 1 LMT @ 1.25" in html

    def test_trade_completed_unaffected(self):
        e = {"seq": 1, "event_type": "TRADE_COMPLETED", "symbol": "SPY",
             "timestamp_ms": None, "data": {"result": "WIN", "gross_pnl": 42}}
        [html] = run_append_events([e])
        assert "WIN" in html and "42" in html


# ── U10 — no operational-card contamination (static guardrail) ─────────────

class TestU10NoCardContamination:
    def test_premarket_context_not_read_in_update_symbols(self):
        html = DASHBOARD_HTML.read_text()
        m = re.search(r"function updateSymbols\(symbols\) \{.*?\n\}\n", html, re.S)
        assert m, "updateSymbols() not found"
        assert "premarket_context" not in m.group(0), (
            "premarket_context must never be read inside updateSymbols() / "
            "symbol-card rendering — audit-timeline-only per spec"
        )

    def test_premarket_context_only_in_append_events(self):
        html = DASHBOARD_HTML.read_text()
        # Every occurrence of the literal must live inside appendEvents()
        append_start = html.index("function appendEvents(events) {")
        append_end = html.index("\n}\n", append_start) + 3
        for m in re.finditer(r"premarket_context", html):
            assert append_start <= m.start() < append_end, (
                f"unexpected premarket_context reference outside appendEvents() at offset {m.start()}"
            )
