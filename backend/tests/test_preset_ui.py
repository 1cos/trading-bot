"""Tests for preset list, UI save/load, and run-by-preset flow."""

import json
import tempfile
from pathlib import Path

import pytest

from trading_lab.preset_store import PresetStore
from trading_lab.backtest_server import app


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def client(tmp_dir):
    from trading_lab import backtest_server as bs
    old = bs._preset_store
    bs._preset_store = PresetStore(tmp_dir)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    bs._preset_store = old


def _valid_params(**overrides):
    base = {
        "symbol": "SPY", "timeframe": "5m", "direction": "LONG",
        "level_source": "ORB_HIGH", "orb_duration_minutes": 5,
        "consecutive_orb_closes": 2, "min_displacement_bars": 3, "entry_model": "CONFIRMATION_CLOSE",
        "min_close_beyond_level_ticks": None, "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0, "exit_target_r": "2",
        "rejection_wick_ratio_min": "0.47", "body_ratio_max": "0.4",
        "tick_size": "0.01",
    }
    base.update(overrides)
    return base


def _create(client, name="Test", **param_overrides):
    resp = client.post("/api/presets", json={
        "name": name, "parameters": _valid_params(**param_overrides),
    })
    return resp.get_json()


# ── Backend list ─────────────────────────────────────────────────────────────


class TestPresetList:
    def test_empty_list(self, client):
        resp = client.get("/api/presets")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_one_preset(self, client):
        _create(client, "Alpha")
        resp = client.get("/api/presets")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["name"] == "Alpha"
        assert "preset_id" in data[0]
        assert "symbol" in data[0]

    def test_multiple_presets(self, client):
        _create(client, "A")
        _create(client, "B")
        _create(client, "C")
        resp = client.get("/api/presets")
        data = resp.get_json()
        assert len(data) == 3

    def test_ordered_newest_first(self, client):
        _create(client, "Old")
        _create(client, "New")
        data = client.get("/api/presets").get_json()
        assert data[0]["name"] == "New"
        assert data[1]["name"] == "Old"

    def test_temp_files_ignored(self, client, tmp_dir):
        _create(client, "Real")
        (tmp_dir / "garbage.tmp").write_text("{}")
        (tmp_dir / "not-a-preset.txt").write_text("{}")
        data = client.get("/api/presets").get_json()
        assert len(data) == 1

    def test_corrupt_file_skipped(self, client, tmp_dir):
        _create(client, "Good")
        (tmp_dir / ("a" * 32 + ".json")).write_text("not json{{{")
        data = client.get("/api/presets").get_json()
        assert len(data) == 1
        assert data[0]["name"] == "Good"

    def test_summary_fields(self, client):
        _create(client, "Full", symbol="QQQ", direction="BOTH", level_source="BOTH")
        data = client.get("/api/presets").get_json()
        p = data[0]
        assert p["symbol"] == "QQQ"
        assert p["direction"] == "BOTH"
        assert p["level_source"] == "BOTH"
        assert "updated_at" in p


# ── UI save flow ─────────────────────────────────────────────────────────────


class TestUISaveFlow:
    def test_save_creates_preset(self, client):
        resp = client.post("/api/presets", json={
            "name": "My Config",
            "parameters": _valid_params(exit_target_r="2.5"),
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "My Config"
        assert data["parameters"]["exit_target_r"] == "2.5"

    def test_dates_not_in_params(self, client):
        """start_date and end_date must not be in preset parameters."""
        p = _create(client)
        assert "start_date" not in p["parameters"]
        assert "end_date" not in p["parameters"]

    def test_wick_body_as_ratios(self, client):
        """Wick 47% → '0.47', Body 40% → '0.4'."""
        p = _create(client)
        assert p["parameters"]["rejection_wick_ratio_min"] == "0.47"
        assert p["parameters"]["body_ratio_max"] == "0.4"

    def test_list_updated_after_save(self, client):
        _create(client, "First")
        data = client.get("/api/presets").get_json()
        assert len(data) == 1
        _create(client, "Second")
        data = client.get("/api/presets").get_json()
        assert len(data) == 2


# ── UI load flow ─────────────────────────────────────────────────────────────


class TestUILoadFlow:
    def test_load_returns_all_params(self, client):
        p = _create(client, "Load Test", exit_target_r="3.1",
                    rejection_wick_ratio_min="0.55", body_ratio_max="0.35")
        resp = client.get(f"/api/presets/{p['preset_id']}")
        data = resp.get_json()
        assert data["parameters"]["exit_target_r"] == "3.1"
        assert data["parameters"]["rejection_wick_ratio_min"] == "0.55"
        assert data["parameters"]["body_ratio_max"] == "0.35"


# ── Run with active preset ──────────────────────────────────────────────────


class TestRunWithPreset:
    def test_run_by_preset_id(self, client):
        p = _create(client, "Run Me", exit_target_r="2.5")
        resp = client.post("/api/run", json={
            "preset_id": p["preset_id"],
            "config": {"start_date": "2026-06-01"},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["config_source"] == "persistent_preset"
        assert data["preset_id"] == p["preset_id"]

    def test_inline_still_works(self, client):
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "preset": {"direction": "LONG"},
            "config": {"exit_target_r": "2"},
        })
        assert resp.status_code == 200
        assert resp.get_json()["config_source"] == "inline"

    def test_reset_clears_preset_not_backend(self, client):
        p = _create(client, "Keep Me")
        resp = client.get(f"/api/presets/{p['preset_id']}")
        assert resp.status_code == 200

    def test_dates_in_config(self, client):
        """Dates must be in config, not top level."""
        p = _create(client)
        resp = client.post("/api/run", json={
            "preset_id": p["preset_id"],
            "config": {"start_date": "2026-06-01", "end_date": "2026-07-01"},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["start_date"] == "2026-06-01"

    def test_empty_dates_omitted(self, client):
        """No config needed when dates are empty."""
        p = _create(client)
        resp = client.post("/api/run", json={"preset_id": p["preset_id"]})
        assert resp.status_code == 200

    def test_strategic_override_in_config_rejected(self, client):
        """Strategic keys in config must be rejected."""
        p = _create(client)
        resp = client.post("/api/run", json={
            "preset_id": p["preset_id"],
            "config": {"exit_target_r": "3"},
        })
        assert resp.status_code == 400
        assert "strategic" in resp.get_json()["error"].lower() or \
               "exit_target_r" in resp.get_json()["error"]

    def test_top_level_dates_ignored_in_preset_mode(self, client):
        """Top level start_date should be ignored in preset mode."""
        p = _create(client)
        # Only config dates should be read
        resp = client.post("/api/run", json={
            "preset_id": p["preset_id"],
            "config": {"start_date": "2026-07-01"},
        })
        assert resp.status_code == 200
        assert resp.get_json()["start_date"] == "2026-07-01"


# ── UI static checks ────────────────────────────────────────────────────────


class TestUIStatic:
    @pytest.fixture
    def html(self):
        return (Path(__file__).resolve().parent.parent.parent / "lab" / "index.html").read_text()

    def test_preset_name_input_exists(self, html):
        assert 'id="presetNameInput"' in html

    def test_save_button_exists(self, html):
        assert 'btnSavePreset' in html

    def test_preset_select_exists(self, html):
        assert 'id="presetSelect"' in html

    def test_load_button_exists(self, html):
        assert 'btnLoadPreset' in html

    def test_active_preset_label(self, html):
        assert 'presetName' in html

    def test_clear_active_on_change(self, html):
        assert 'clearActivePreset' in html

    def test_save_sends_ratios_not_percentages(self, html):
        # The save function should convert wick/body to ratio strings
        assert 'rejection_wick_ratio_min' in html
        assert 'body_ratio_max' in html

    def test_run_uses_preset_id(self, html):
        assert 'state.activePreset' in html
        assert 'preset_id' in html

    def test_rr_input_preserved(self, html):
        assert 'id="pExitTargetR"' in html
        assert 'type="number"' in html

    def test_level_source_both_preserved(self, html):
        assert 'value="BOTH"' in html
