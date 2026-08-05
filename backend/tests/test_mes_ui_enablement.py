"""Tests for MES UI enablement via /api/symbols response and lab/index.html.

Since we cannot run a browser, we verify:
  - Server response shape that the UI depends on
  - lab/index.html source contains the expected logic
"""

import json
from pathlib import Path

import pytest

from trading_lab.backtest_server import _available_symbols

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LAB_HTML = REPO_ROOT / "lab" / "index.html"

_SYMBOLS = None

def _get_symbols():
    global _SYMBOLS
    if _SYMBOLS is None:
        _SYMBOLS = _available_symbols()
    return _SYMBOLS

def _find(sym):
    return next((s for s in _get_symbols() if s["symbol"] == sym), None)

def _has_mes():
    return _find("MES") is not None

def _lab_source():
    return LAB_HTML.read_text()


# ── 1. MES selezionato → Run abilitato ──────────────────────────────────────

class TestMESRunEnabled:
    def test_mes_is_continuous_future(self):
        if not _has_mes(): pytest.skip("MES not available")
        assert _find("MES")["instrument_type"] == "CONTINUOUS_FUTURE"

    def test_ui_enables_run_for_mes(self):
        """The JS logic enables Run when symbol is MES."""
        src = _lab_source()
        # The conditional: if symbol==="MES" → btn.disabled=false
        assert 's.symbol==="MES"' in src
        assert "btn.disabled=false" in src


# ── 2. MNQ selezionato → Run disabilitato ───────────────────────────────────

class TestMNQRunDisabled:
    def test_mnq_instrument_type(self):
        mnq = _find("MNQ")
        if mnq is None: pytest.skip("MNQ not available")
        assert mnq["instrument_type"] == "CONTINUOUS_FUTURE"

    def test_ui_disables_non_mes_futures(self):
        src = _lab_source()
        # The else branch for non-MES futures → btn.disabled=true
        assert "btn.disabled=true" in src
        assert "Futures loader not connected yet" in src


# ── 3. MES invia symbol=MES ─────────────────────────────────────────────────

class TestMESSendsCorrectSymbol:
    def test_mes_symbol_in_response(self):
        if not _has_mes(): pytest.skip("MES not available")
        assert _find("MES")["symbol"] == "MES"

    def test_ui_sends_selected_symbol(self):
        """The run function reads pSymbol value — no hardcoded override."""
        src = _lab_source()
        assert 'document.getElementById("pSymbol").value' in src


# ── 4. MES permette 1m ──────────────────────────────────────────────────────

class TestMESTimeframes:
    def test_mes_1m_loads(self):
        if not _has_mes(): pytest.skip("MES not available")
        from trading_lab.backtest_server import _load_futures_candles
        data = _load_futures_candles("MES", 1)
        assert data is not None
        assert data["session_count"] >= 4

    # ── 5. MES permette 5m ──────────────────────────────────────────────
    def test_mes_5m_loads(self):
        if not _has_mes(): pytest.skip("MES not available")
        from trading_lab.backtest_server import _load_futures_candles
        data = _load_futures_candles("MES", 5)
        assert data is not None
        assert data["session_count"] >= 4


# ── 6. tick size arriva dai metadata server ──────────────────────────────────

class TestTickSizeFromServer:
    def test_mes_tick_size_in_response(self):
        if not _has_mes(): pytest.skip("MES not available")
        assert _find("MES")["tick_size"] == 0.25

    def test_ui_reads_tick_size(self):
        src = _lab_source()
        assert "s.tick_size" in src
        assert 'pTickSize' in src


# ── 7. avviso historical-only visibile ───────────────────────────────────────

class TestHistoricalBanner:
    def test_banner_div_exists(self):
        src = _lab_source()
        assert 'id="futuresBanner"' in src

    def test_banner_text_for_mes(self):
        src = _lab_source()
        assert "Historical continuous future" in src
        assert "not orderable" in src


# ── 8. nessun percorso ordini viene mostrato ────────────────────────────────

class TestNoOrderPath:
    def test_no_order_button_in_html(self):
        src = _lab_source()
        low = src.lower()
        assert "place order" not in low
        assert "send order" not in low
        assert "execute trade" not in low

    def test_tradable_false(self):
        if not _has_mes(): pytest.skip("MES not available")
        assert _find("MES")["tradable"] is False


# ── 9. equity restano invariate ──────────────────────────────────────────────

class TestEquityUnchanged:
    def test_spy_no_banner(self):
        spy = _find("SPY")
        assert spy is not None
        assert spy.get("instrument_type") != "CONTINUOUS_FUTURE"

    def test_spy_has_timeframes(self):
        spy = _find("SPY")
        assert len(spy["timeframes"]) > 0

    def test_nvda_present(self):
        nvda = _find("NVDA")
        assert nvda is not None

    def test_ui_hides_banner_for_equity(self):
        src = _lab_source()
        assert 'banner.style.display="none"' in src
