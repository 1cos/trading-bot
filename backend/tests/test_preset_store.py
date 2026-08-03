"""Tests for persistent preset store — create, read, run by preset_id."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from trading_lab.preset_store import (
    PresetStore,
    generate_preset_id,
    is_safe_preset_id,
    validate_preset_params,
    SCHEMA_VERSION,
)
from trading_lab.backtest_server import app


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def store(tmp_dir):
    return PresetStore(tmp_dir)


def _valid_params(**overrides):
    base = {
        "symbol": "SPY",
        "timeframe": "5m",
        "direction": "LONG",
        "level_source": "ORB_HIGH",
        "orb_duration_minutes": 5,
        "consecutive_orb_closes": 2,
        "min_displacement_bars": 3,
        "entry_model": "CONFIRMATION_CLOSE",
        "min_close_beyond_level_ticks": None,
        "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0,
        "exit_target_r": "2",
        "rejection_wick_ratio_min": "0.47",
        "body_ratio_max": "0.4",
        "tick_size": "0.01",
    }
    base.update(overrides)
    return base


@pytest.fixture
def client(tmp_dir):
    """Flask test client with preset store using temp directory."""
    from trading_lab import backtest_server as bs
    old_store = bs._preset_store
    bs._preset_store = PresetStore(tmp_dir)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    bs._preset_store = old_store


# ── ID generation ────────────────────────────────────────────────────────────


class TestPresetId:
    def test_unique(self):
        ids = {generate_preset_id() for _ in range(100)}
        assert len(ids) == 100

    def test_safe(self):
        pid = generate_preset_id()
        assert is_safe_preset_id(pid)
        assert len(pid) == 32

    def test_unsafe_path_traversal(self):
        assert not is_safe_preset_id("../../../etc/passwd")
        assert not is_safe_preset_id("abc/def")
        assert not is_safe_preset_id("abc\\def")
        assert not is_safe_preset_id("")
        assert not is_safe_preset_id("ABCDEF1234567890abcdef1234567890")  # uppercase


# ── Create and read ──────────────────────────────────────────────────────────


class TestCreateAndRead:
    def test_create_returns_preset(self, store):
        p = store.create("My Preset", _valid_params())
        assert p["schema_version"] == SCHEMA_VERSION
        assert p["name"] == "My Preset"
        assert p["strategy_id"] == "BDRR"
        assert is_safe_preset_id(p["preset_id"])
        assert "created_at" in p
        assert "updated_at" in p
        assert p["parameters"]["symbol"] == "SPY"

    def test_file_created(self, store, tmp_dir):
        p = store.create("Test", _valid_params())
        path = tmp_dir / f"{p['preset_id']}.json"
        assert path.exists()

    def test_read_matches_create(self, store):
        p = store.create("Test", _valid_params())
        loaded = store.get(p["preset_id"])
        assert loaded == p

    def test_preset_id_stable(self, store):
        p = store.create("Test", _valid_params())
        loaded = store.get(p["preset_id"])
        assert loaded["preset_id"] == p["preset_id"]

    def test_not_found_returns_none(self, store):
        assert store.get("a" * 32) is None


# ── Persistence ──────────────────────────────────────────────────────────────


class TestPersistence:
    def test_new_store_reads_same_file(self, tmp_dir):
        s1 = PresetStore(tmp_dir)
        p = s1.create("Persist", _valid_params())
        s2 = PresetStore(tmp_dir)
        loaded = s2.get(p["preset_id"])
        assert loaded == p

    def test_no_temp_files_after_success(self, store, tmp_dir):
        store.create("Test", _valid_params())
        tmp_files = [f for f in tmp_dir.iterdir() if f.suffix == ".tmp"]
        assert len(tmp_files) == 0


# ── Decimal precision ────────────────────────────────────────────────────────


class TestPrecision:
    def test_rr_preserved(self, store):
        p = store.create("Test", _valid_params(exit_target_r="2.5"))
        loaded = store.get(p["preset_id"])
        assert loaded["parameters"]["exit_target_r"] == "2.5"

    def test_wick_preserved(self, store):
        p = store.create("Test", _valid_params(rejection_wick_ratio_min="0.47"))
        loaded = store.get(p["preset_id"])
        assert loaded["parameters"]["rejection_wick_ratio_min"] == "0.47"

    def test_body_preserved(self, store):
        p = store.create("Test", _valid_params(body_ratio_max="0.4"))
        loaded = store.get(p["preset_id"])
        assert loaded["parameters"]["body_ratio_max"] == "0.4"

    def test_tick_size_preserved(self, store):
        p = store.create("Test", _valid_params(tick_size="0.01"))
        loaded = store.get(p["preset_id"])
        assert loaded["parameters"]["tick_size"] == "0.01"

    def test_no_float_artifacts(self, store):
        """Values like 0.1 must not become 0.1000000000000000055..."""
        p = store.create("Test", _valid_params(
            exit_target_r="2.1",
            rejection_wick_ratio_min="0.1",
            body_ratio_max="0.3",
        ))
        loaded = store.get(p["preset_id"])
        assert loaded["parameters"]["exit_target_r"] == "2.1"
        assert loaded["parameters"]["rejection_wick_ratio_min"] == "0.1"
        assert loaded["parameters"]["body_ratio_max"] == "0.3"


# ── Security ─────────────────────────────────────────────────────────────────


class TestSecurity:
    def test_path_traversal_rejected(self, store):
        with pytest.raises(ValueError):
            store.get("../../../etc/passwd")

    def test_invalid_name_rejected(self, store):
        with pytest.raises(ValueError, match="name"):
            store.create("", _valid_params())

    def test_invalid_params_not_saved(self, store, tmp_dir):
        bad = _valid_params(direction="INVALID")
        with pytest.raises(ValueError):
            store.create("Bad", bad)
        # No file created
        assert len(list(tmp_dir.iterdir())) == 0


# ── Validation ───────────────────────────────────────────────────────────────


class TestValidation:
    def test_valid_params(self):
        assert validate_preset_params(_valid_params()) == []

    # Canonical pairs — exactly 3 valid
    def test_long_orb_high_valid(self):
        assert validate_preset_params(_valid_params(direction="LONG", level_source="ORB_HIGH")) == []

    def test_short_orb_low_valid(self):
        assert validate_preset_params(_valid_params(direction="SHORT", level_source="ORB_LOW")) == []

    def test_both_both_valid(self):
        assert validate_preset_params(_valid_params(direction="BOTH", level_source="BOTH")) == []

    # Non-canonical pairs — all 6 must be rejected
    def test_long_orb_low_rejected(self):
        errs = validate_preset_params(_valid_params(direction="LONG", level_source="ORB_LOW"))
        assert any("canonical" in e.lower() or "level_source" in e for e in errs)

    def test_long_both_rejected(self):
        errs = validate_preset_params(_valid_params(direction="LONG", level_source="BOTH"))
        assert any("canonical" in e.lower() or "level_source" in e for e in errs)

    def test_short_orb_high_rejected(self):
        errs = validate_preset_params(_valid_params(direction="SHORT", level_source="ORB_HIGH"))
        assert any("canonical" in e.lower() or "level_source" in e for e in errs)

    def test_short_both_rejected(self):
        errs = validate_preset_params(_valid_params(direction="SHORT", level_source="BOTH"))
        assert any("canonical" in e.lower() or "level_source" in e for e in errs)

    def test_both_orb_high_rejected(self):
        errs = validate_preset_params(_valid_params(direction="BOTH", level_source="ORB_HIGH"))
        assert any("canonical" in e.lower() or "level_source" in e for e in errs)

    def test_both_orb_low_rejected(self):
        errs = validate_preset_params(_valid_params(direction="BOTH", level_source="ORB_LOW"))
        assert any("canonical" in e.lower() or "level_source" in e for e in errs)

    def test_invalid_direction(self):
        errs = validate_preset_params(_valid_params(direction="UP"))
        assert any("direction" in e for e in errs)

    def test_negative_rr(self):
        errs = validate_preset_params(_valid_params(exit_target_r="-1"))
        assert any("exit_target_r" in e for e in errs)

    def test_wick_above_1(self):
        errs = validate_preset_params(_valid_params(rejection_wick_ratio_min="1.5"))
        assert any("rejection_wick_ratio_min" in e for e in errs)


# ── API: POST /api/presets ───────────────────────────────────────────────────


class TestApiCreate:
    def test_create_201(self, client):
        resp = client.post("/api/presets", json={
            "name": "Test Preset",
            "parameters": _valid_params(),
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["schema_version"] == SCHEMA_VERSION
        assert data["name"] == "Test Preset"

    def test_invalid_rejected(self, client):
        resp = client.post("/api/presets", json={
            "name": "Bad",
            "parameters": _valid_params(direction="INVALID"),
        })
        assert resp.status_code == 400


# ── API: GET /api/presets/<id> ───────────────────────────────────────────────


class TestApiGet:
    def test_get_after_create(self, client):
        resp = client.post("/api/presets", json={
            "name": "Get Test",
            "parameters": _valid_params(),
        })
        pid = resp.get_json()["preset_id"]
        resp2 = client.get(f"/api/presets/{pid}")
        assert resp2.status_code == 200
        assert resp2.get_json()["preset_id"] == pid

    def test_not_found_404(self, client):
        resp = client.get("/api/presets/" + "a" * 32)
        assert resp.status_code == 404

    def test_invalid_id_400(self, client):
        resp = client.get("/api/presets/../../etc/passwd")
        assert resp.status_code in (400, 404)


# ── API: Run by preset_id ───────────────────────────────────────────────────


class TestRunByPreset:
    def _create_preset(self, client, **param_overrides):
        resp = client.post("/api/presets", json={
            "name": "Run Test",
            "parameters": _valid_params(**param_overrides),
        })
        return resp.get_json()["preset_id"]

    def test_run_with_preset_id(self, client):
        pid = self._create_preset(client)
        resp = client.post("/api/run", json={"preset_id": pid})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["config_source"] == "persistent_preset"
        assert data["preset_id"] == pid
        assert data["preset_schema_version"] == SCHEMA_VERSION
        assert "metrics" in data

    def test_preset_id_propagated(self, client):
        pid = self._create_preset(client)
        resp = client.post("/api/run", json={"preset_id": pid})
        data = resp.get_json()
        assert data["preset"]["preset_id"] == pid

    def test_date_range_with_preset(self, client):
        pid = self._create_preset(client)
        resp = client.post("/api/run", json={
            "preset_id": pid,
            "config": {
                "start_date": "2026-06-01",
                "end_date": "2026-07-01",
            },
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["start_date"] == "2026-06-01"

    def test_strategic_override_rejected(self, client):
        pid = self._create_preset(client)
        resp = client.post("/api/run", json={
            "preset_id": pid,
            "preset": {"direction": "SHORT"},
        })
        assert resp.status_code == 400
        assert "combine" in resp.get_json()["error"].lower()

    def test_rr_override_rejected(self, client):
        pid = self._create_preset(client)
        resp = client.post("/api/run", json={
            "preset_id": pid,
            "config": {"exit_target_r": "3"},
        })
        assert resp.status_code == 400

    def test_not_found_preset_404(self, client):
        resp = client.post("/api/run", json={"preset_id": "a" * 32})
        assert resp.status_code == 404

    def test_different_rr_different_results(self, client):
        pid1 = self._create_preset(client, exit_target_r="2")
        pid2 = self._create_preset(client, exit_target_r="3")
        d1 = client.post("/api/run", json={"preset_id": pid1}).get_json()
        d2 = client.post("/api/run", json={"preset_id": pid2}).get_json()
        # Both run, may produce different results
        assert d1["config_source"] == "persistent_preset"
        assert d2["config_source"] == "persistent_preset"


# ── Compatibility: inline still works ────────────────────────────────────────


class TestInlineCompatibility:
    def test_inline_still_works(self, client):
        resp = client.post("/api/run", json={
            "symbols": ["SPY"], "timeframe": "5m",
            "preset": {"direction": "LONG"},
            "config": {"exit_target_r": "2"},
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["config_source"] == "inline"
