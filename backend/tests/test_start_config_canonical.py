"""Una sola source per la configurazione di avvio.

Prima esistevano due definizioni indipendenti della stessa cosa: la
watchlist come value= di un <input> in dashboard.html, e una costante in
control_api.py per l'auto-start. Concordavano — ed e' esattamente il
modo in cui si rompono: modificarne una sola fa partire manuale e
automatico su book diversi.
"""

import json
import re
from pathlib import Path

import pytest

from trading_lab.live.control_api import (
    MaxBotController,
    autostart_config_from_env,
    create_app,
)
from trading_lab.live.start_config import (
    DEFAULT_DIRECTION,
    DEFAULT_EXECUTION_MODE,
    DEFAULT_SYMBOLS,
    DEFAULT_TRADE_LIMITS_ENABLED,
    start_config_defaults,
)


# Baseline congelata: i valori operativi PRIMA del refactor.
BASELINE_SYMBOLS = [
    "SPY", "QQQ", "AAPL", "TSLA", "NVDA", "AMD", "AMZN", "TSLL", "NFLX",
    "GOOGL", "SOFI", "META", "MU", "INTC", "SNDK", "PLTR", "MSFT",
]
BASELINE_DIRECTION = "BOTH"
BASELINE_MODE = "PAPER_EXECUTE"
BASELINE_TRADE_LIMITS = False

DASHBOARD = (Path(__file__).resolve().parents[1]
             / "src/trading_lab/live/ui/dashboard.html")
CONTROL_API = (Path(__file__).resolve().parents[1]
               / "src/trading_lab/live/control_api.py")


def _client(tmp_path):
    ctrl = MaxBotController(trade_state_dir=tmp_path)
    app = create_app(ctrl)
    app.config["TESTING"] = True
    return app.test_client(), ctrl


# ═════════════════════════════════════════════════════════════════════════
# T1 — watchlist invariata
# ═════════════════════════════════════════════════════════════════════════

class TestT1WatchlistUnchanged:
    def test_symbols_match_baseline_exactly(self):
        assert list(DEFAULT_SYMBOLS) == BASELINE_SYMBOLS

    def test_order_is_preserved(self):
        """L'ordine e' quello di sottoscrizione in _subscribe_all():
        cambiarlo cambia quale simbolo riceve la prima richiesta."""
        assert list(DEFAULT_SYMBOLS)[0] == "SPY"
        assert list(DEFAULT_SYMBOLS)[-1] == "MSFT"
        assert len(DEFAULT_SYMBOLS) == 17

    def test_other_defaults_match_baseline(self):
        assert DEFAULT_DIRECTION == BASELINE_DIRECTION
        assert DEFAULT_EXECUTION_MODE == BASELINE_MODE
        assert DEFAULT_TRADE_LIMITS_ENABLED is BASELINE_TRADE_LIMITS

    def test_defaults_dict_is_a_fresh_copy(self):
        a, b = start_config_defaults(), start_config_defaults()
        a["symbols"].append("HACK")
        assert b["symbols"] == BASELINE_SYMBOLS
        assert list(DEFAULT_SYMBOLS) == BASELINE_SYMBOLS


# ═════════════════════════════════════════════════════════════════════════
# T2 — manual START invariato
# ═════════════════════════════════════════════════════════════════════════

class TestT2ManualStartUnchanged:
    def test_status_exposes_the_canonical_defaults(self, tmp_path):
        client, _ = _client(tmp_path)
        d = client.get("/api/bot/status").get_json()

        assert "start_defaults" in d
        sd = d["start_defaults"]
        assert sd["symbols"] == BASELINE_SYMBOLS
        assert sd["direction"] == BASELINE_DIRECTION
        assert sd["execution_mode"] == BASELINE_MODE
        assert sd["trade_limits_enabled"] is BASELINE_TRADE_LIMITS

    def test_defaults_available_with_bot_stopped(self, tmp_path):
        client, ctrl = _client(tmp_path)
        assert ctrl._runner is None
        d = client.get("/api/bot/status").get_json()
        assert d["state"] == "STOPPED"
        assert d["start_defaults"]["symbols"] == BASELINE_SYMBOLS

    def test_env_autostart_override_does_not_touch_manual_defaults(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAXBOT_AUTOSTART_SYMBOLS", "QQQ")
        monkeypatch.setenv("MAXBOT_AUTOSTART_MODE", "OBSERVE_ONLY")
        client, _ = _client(tmp_path)
        sd = client.get("/api/bot/status").get_json()["start_defaults"]
        assert sd["symbols"] == BASELINE_SYMBOLS
        assert sd["execution_mode"] == BASELINE_MODE


# ═════════════════════════════════════════════════════════════════════════
# T3 — auto-start invariato
# ═════════════════════════════════════════════════════════════════════════

class TestT3AutostartUnchanged:
    def test_without_env_uses_canonical_values(self, monkeypatch):
        for v in ("SYMBOLS", "DIRECTION", "MODE", "TRADE_LIMITS"):
            monkeypatch.delenv(f"MAXBOT_AUTOSTART_{v}", raising=False)
        c = autostart_config_from_env()
        assert c.symbols == BASELINE_SYMBOLS
        assert c.direction == BASELINE_DIRECTION
        assert c.execution_mode == BASELINE_MODE
        assert c.trade_limits_enabled is BASELINE_TRADE_LIMITS

    def test_autostart_and_manual_agree(self, tmp_path, monkeypatch):
        for v in ("SYMBOLS", "DIRECTION", "MODE", "TRADE_LIMITS"):
            monkeypatch.delenv(f"MAXBOT_AUTOSTART_{v}", raising=False)
        client, _ = _client(tmp_path)
        sd = client.get("/api/bot/status").get_json()["start_defaults"]
        c = autostart_config_from_env()
        assert c.as_start_kwargs() == sd

    def test_mutating_autostart_config_cannot_corrupt_defaults(
            self, monkeypatch):
        for v in ("SYMBOLS", "DIRECTION", "MODE", "TRADE_LIMITS"):
            monkeypatch.delenv(f"MAXBOT_AUTOSTART_{v}", raising=False)
        c = autostart_config_from_env()
        c.as_start_kwargs()["symbols"].append("HACK")
        assert list(DEFAULT_SYMBOLS) == BASELINE_SYMBOLS


# ═════════════════════════════════════════════════════════════════════════
# T4 — una sola source
# ═════════════════════════════════════════════════════════════════════════

class TestT4SingleSource:
    def test_dashboard_has_no_hardcoded_watchlist(self):
        html = DASHBOARD.read_text()
        assert "SPY,QQQ,AAPL" not in html
        m = re.search(r'id="cfg-symbols"[^>]*', html)
        assert m, "input cfg-symbols non trovato"
        assert "value=" not in m.group(0), (
            f"la watchlist e' ancora hardcoded nel markup: {m.group(0)}")

    def test_control_api_has_no_second_list(self):
        src = CONTROL_API.read_text()
        assert "_AUTOSTART_DEFAULT_SYMBOLS" not in src
        assert "SPY,QQQ,AAPL" not in src

    def test_symbol_list_appears_once_in_the_codebase(self):
        """Solo start_config.py deve enumerare i 17 simboli."""
        root = Path(__file__).resolve().parents[1] / "src"
        hits = []
        for path in root.rglob("*"):
            if path.suffix not in (".py", ".html") or not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            if "SNDK" in text and "TSLL" in text and "GOOGL" in text:
                hits.append(path.name)
        assert hits == ["start_config.py"], f"watchlist duplicata in: {hits}"

    def test_dashboard_reads_defaults_from_status(self):
        html = DASHBOARD.read_text()
        assert "applyStartDefaults" in html
        assert "start_defaults" in html


# ═════════════════════════════════════════════════════════════════════════
# T5 — env override, solo auto-start
# ═════════════════════════════════════════════════════════════════════════

class TestT5EnvOverride:
    def test_symbols_override(self, monkeypatch):
        monkeypatch.setenv("MAXBOT_AUTOSTART_SYMBOLS", "qqq, spy , nvda")
        c = autostart_config_from_env()
        assert c.symbols == ["QQQ", "SPY", "NVDA"]

    def test_mode_and_direction_override(self, monkeypatch):
        monkeypatch.setenv("MAXBOT_AUTOSTART_MODE", "OBSERVE_ONLY")
        monkeypatch.setenv("MAXBOT_AUTOSTART_DIRECTION", "LONG")
        c = autostart_config_from_env()
        assert c.execution_mode == "OBSERVE_ONLY"
        assert c.direction == "LONG"

    def test_trade_limits_override(self, monkeypatch):
        monkeypatch.setenv("MAXBOT_AUTOSTART_TRADE_LIMITS", "1")
        assert autostart_config_from_env().trade_limits_enabled is True
        monkeypatch.setenv("MAXBOT_AUTOSTART_TRADE_LIMITS", "0")
        assert autostart_config_from_env().trade_limits_enabled is False

    def test_partial_override_keeps_canonical_rest(self, monkeypatch):
        for v in ("DIRECTION", "MODE", "TRADE_LIMITS"):
            monkeypatch.delenv(f"MAXBOT_AUTOSTART_{v}", raising=False)
        monkeypatch.setenv("MAXBOT_AUTOSTART_SYMBOLS", "QQQ")
        c = autostart_config_from_env()
        assert c.symbols == ["QQQ"]
        assert c.direction == BASELINE_DIRECTION
        assert c.execution_mode == BASELINE_MODE

    def test_override_does_not_mutate_the_canonical_defaults(self, monkeypatch):
        monkeypatch.setenv("MAXBOT_AUTOSTART_SYMBOLS", "QQQ")
        autostart_config_from_env()
        assert list(DEFAULT_SYMBOLS) == BASELINE_SYMBOLS


# ═════════════════════════════════════════════════════════════════════════
# T6 — validazione invariata
# ═════════════════════════════════════════════════════════════════════════

class TestT6ValidationUnchanged:
    def test_live_still_rejected(self, tmp_path):
        client, _ = _client(tmp_path)
        r = client.post("/api/bot/start",
                        json={"symbols": ["QQQ"], "execution_mode": "LIVE"})
        assert r.status_code == 400

    def test_empty_symbols_still_rejected(self, tmp_path):
        client, _ = _client(tmp_path)
        r = client.post("/api/bot/start",
                        json={"symbols": [], "execution_mode": "PAPER_EXECUTE"})
        assert r.status_code == 400

    def test_both_valid_modes_accepted(self, tmp_path):
        from unittest.mock import MagicMock
        for mode in ("PAPER_EXECUTE", "OBSERVE_ONLY"):
            ctrl = MaxBotController(trade_state_dir=tmp_path)
            ctrl.start = MagicMock()
            app = create_app(ctrl)
            app.config["TESTING"] = True
            r = app.test_client().post("/api/bot/start", json={
                "symbols": ["QQQ"], "execution_mode": mode})
            assert r.status_code == 200, mode

    def test_canonical_mode_is_a_valid_mode(self):
        assert DEFAULT_EXECUTION_MODE in ("PAPER_EXECUTE", "OBSERVE_ONLY")


# ═════════════════════════════════════════════════════════════════════════
# T9 — nessuna sessione reale
# ═════════════════════════════════════════════════════════════════════════

class TestT9NoRealSession:
    def test_reading_defaults_starts_nothing(self, tmp_path):
        client, ctrl = _client(tmp_path)
        for _ in range(3):
            client.get("/api/bot/status")
        assert str(ctrl.state) == "STOPPED"
        assert ctrl._runner is None

    def test_start_config_module_has_no_side_effects(self):
        import trading_lab.live.start_config as m
        assert not hasattr(m, "start")
        assert not hasattr(m, "IB")
