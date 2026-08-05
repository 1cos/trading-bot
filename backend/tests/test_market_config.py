"""Tests for market_config — canonical market configuration reader."""

import json
import tempfile
from pathlib import Path

import pytest

from trading_lab.market_config import (
    MarketConfig,
    get_market_config,
    get_all_symbols,
    reload,
    time_to_minutes,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ── Helpers ──────────────────────────────────────────────────────────────────

def _write_manifest(tmp: Path, instruments: dict) -> Path:
    mp = tmp / "market_manifest.json"
    mp.write_text(json.dumps({"version": 1, "instruments": instruments}))
    return mp


def _valid_entry(**overrides) -> dict:
    base = {
        "asset_class": "EQUITY",
        "provider": "IBKR",
        "sec_type": "STK",
        "exchange": "SMART",
        "currency": "USD",
        "timezone": "America/New_York",
        "session_open": "09:30",
        "session_close": "16:00",
        "orb_open": "09:30",
        "orb_close": "09:34",
        "tick_size": 0.01,
        "point_value": 1.0,
        "price_scale": 2,
    }
    base.update(overrides)
    return base


# ── 1. SPY config correct ───────────────────────────────────────────────────

class TestSPY:
    def test_asset_class(self):
        cfg = get_market_config("SPY")
        assert cfg.asset_class == "EQUITY"

    def test_timezone(self):
        cfg = get_market_config("SPY")
        assert cfg.timezone == "America/New_York"

    def test_session_open(self):
        cfg = get_market_config("SPY")
        assert cfg.session_open == "09:30"

    def test_session_close(self):
        cfg = get_market_config("SPY")
        assert cfg.session_close == "16:00"

    def test_orb_open(self):
        cfg = get_market_config("SPY")
        assert cfg.orb_open == "09:30"

    def test_orb_close(self):
        cfg = get_market_config("SPY")
        assert cfg.orb_close == "09:34"

    def test_tick_size(self):
        cfg = get_market_config("SPY")
        assert cfg.tick_size == 0.01

    def test_point_value(self):
        cfg = get_market_config("SPY")
        assert cfg.point_value == 1.0

    def test_exchange(self):
        cfg = get_market_config("SPY")
        assert cfg.exchange == "SMART"

    def test_sec_type(self):
        cfg = get_market_config("SPY")
        assert cfg.sec_type == "STK"

    def test_price_scale(self):
        cfg = get_market_config("SPY")
        assert cfg.price_scale == 2

    def test_session_open_minutes(self):
        cfg = get_market_config("SPY")
        assert cfg.session_open_minutes == 570

    def test_session_close_minutes(self):
        cfg = get_market_config("SPY")
        assert cfg.session_close_minutes == 960

    def test_orb_open_minutes(self):
        cfg = get_market_config("SPY")
        assert cfg.orb_open_minutes == 570

    def test_orb_close_minutes(self):
        cfg = get_market_config("SPY")
        assert cfg.orb_close_minutes == 574


# ── 2. NVDA config correct ──────────────────────────────────────────────────

class TestNVDA:
    def test_asset_class(self):
        cfg = get_market_config("NVDA")
        assert cfg.asset_class == "EQUITY"

    def test_timezone(self):
        cfg = get_market_config("NVDA")
        assert cfg.timezone == "America/New_York"

    def test_tick_size(self):
        cfg = get_market_config("NVDA")
        assert cfg.tick_size == 0.01

    def test_point_value(self):
        cfg = get_market_config("NVDA")
        assert cfg.point_value == 1.0

    def test_session_open(self):
        cfg = get_market_config("NVDA")
        assert cfg.session_open == "09:30"

    def test_orb_open_minutes(self):
        cfg = get_market_config("NVDA")
        assert cfg.orb_open_minutes == 570


# ── 3. MES config correct ───────────────────────────────────────────────────

class TestMES:
    def test_asset_class(self):
        cfg = get_market_config("MES")
        assert cfg.asset_class == "FUTURE"

    def test_timezone(self):
        cfg = get_market_config("MES")
        assert cfg.timezone == "America/Chicago"

    def test_session_open(self):
        cfg = get_market_config("MES")
        assert cfg.session_open == "08:30"

    def test_session_close(self):
        cfg = get_market_config("MES")
        assert cfg.session_close == "15:00"

    def test_orb_open(self):
        cfg = get_market_config("MES")
        assert cfg.orb_open == "08:30"

    def test_orb_close(self):
        cfg = get_market_config("MES")
        assert cfg.orb_close == "08:34"

    def test_tick_size(self):
        cfg = get_market_config("MES")
        assert cfg.tick_size == 0.25

    def test_point_value(self):
        cfg = get_market_config("MES")
        assert cfg.point_value == 5.0

    def test_exchange(self):
        cfg = get_market_config("MES")
        assert cfg.exchange == "CME"

    def test_sec_type(self):
        cfg = get_market_config("MES")
        assert cfg.sec_type == "CONTFUT"

    def test_session_open_minutes(self):
        cfg = get_market_config("MES")
        assert cfg.session_open_minutes == 510

    def test_orb_open_minutes(self):
        cfg = get_market_config("MES")
        assert cfg.orb_open_minutes == 510

    def test_orb_close_minutes(self):
        cfg = get_market_config("MES")
        assert cfg.orb_close_minutes == 514


# ── 4. MNQ config correct ───────────────────────────────────────────────────

class TestMNQ:
    def test_asset_class(self):
        cfg = get_market_config("MNQ")
        assert cfg.asset_class == "FUTURE"

    def test_timezone(self):
        cfg = get_market_config("MNQ")
        assert cfg.timezone == "America/Chicago"

    def test_tick_size(self):
        cfg = get_market_config("MNQ")
        assert cfg.tick_size == 0.25

    def test_point_value(self):
        cfg = get_market_config("MNQ")
        assert cfg.point_value == 2.0

    def test_session_open(self):
        cfg = get_market_config("MNQ")
        assert cfg.session_open == "08:30"

    def test_session_close(self):
        cfg = get_market_config("MNQ")
        assert cfg.session_close == "15:00"

    def test_orb_open_minutes(self):
        cfg = get_market_config("MNQ")
        assert cfg.orb_open_minutes == 510

    def test_exchange(self):
        cfg = get_market_config("MNQ")
        assert cfg.exchange == "CME"


# ── 5. time_to_minutes 09:30 → 570 ──────────────────────────────────────────

class TestTimeConversion:
    def test_0930_to_570(self):
        assert time_to_minutes("09:30") == 570

    def test_0830_to_510(self):
        assert time_to_minutes("08:30") == 510

    def test_0000_to_0(self):
        assert time_to_minutes("00:00") == 0

    def test_2359_to_1439(self):
        assert time_to_minutes("23:59") == 1439

    def test_1600_to_960(self):
        assert time_to_minutes("16:00") == 960

    def test_1500_to_900(self):
        assert time_to_minutes("15:00") == 900

    def test_0834_to_514(self):
        assert time_to_minutes("08:34") == 514

    def test_0934_to_574(self):
        assert time_to_minutes("09:34") == 574

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid time format"):
            time_to_minutes("9:30")

    def test_invalid_format_letters(self):
        with pytest.raises(ValueError, match="Invalid time format"):
            time_to_minutes("ab:cd")

    def test_invalid_hour(self):
        with pytest.raises(ValueError, match="Invalid time format"):
            time_to_minutes("25:00")

    def test_invalid_minute(self):
        with pytest.raises(ValueError, match="Invalid time format"):
            time_to_minutes("09:60")


# ── 7. Unknown symbol rejected ──────────────────────────────────────────────

class TestUnknownSymbol:
    def test_unknown_raises_keyerror(self):
        with pytest.raises(KeyError, match="AAPL"):
            get_market_config("AAPL")

    def test_empty_raises_keyerror(self):
        with pytest.raises(KeyError):
            get_market_config("")

    def test_error_lists_available(self):
        with pytest.raises(KeyError, match="MES"):
            get_market_config("UNKNOWN_SYM")


# ── 8. Invalid timezone rejected ────────────────────────────────────────────

class TestInvalidTimezone:
    def test_bad_timezone(self, tmp_path):
        mp = _write_manifest(tmp_path, {"TEST": _valid_entry(timezone="Mars/Olympus")})
        with pytest.raises(ValueError, match="Invalid timezone"):
            get_market_config("TEST", manifest_path=mp)

    def test_empty_timezone(self, tmp_path):
        mp = _write_manifest(tmp_path, {"TEST": _valid_entry(timezone="")})
        with pytest.raises(ValueError, match="Invalid timezone"):
            get_market_config("TEST", manifest_path=mp)


# ── 9. Invalid tick_size rejected ────────────────────────────────────────────

class TestInvalidTickSize:
    def test_zero(self, tmp_path):
        mp = _write_manifest(tmp_path, {"TEST": _valid_entry(tick_size=0)})
        with pytest.raises(ValueError, match="tick_size"):
            get_market_config("TEST", manifest_path=mp)

    def test_negative(self, tmp_path):
        mp = _write_manifest(tmp_path, {"TEST": _valid_entry(tick_size=-0.01)})
        with pytest.raises(ValueError, match="tick_size"):
            get_market_config("TEST", manifest_path=mp)

    def test_string(self, tmp_path):
        mp = _write_manifest(tmp_path, {"TEST": _valid_entry(tick_size="0.01")})
        with pytest.raises(ValueError, match="tick_size"):
            get_market_config("TEST", manifest_path=mp)

    def test_zero_point_value(self, tmp_path):
        mp = _write_manifest(tmp_path, {"TEST": _valid_entry(point_value=0)})
        with pytest.raises(ValueError, match="point_value"):
            get_market_config("TEST", manifest_path=mp)

    def test_negative_point_value(self, tmp_path):
        mp = _write_manifest(tmp_path, {"TEST": _valid_entry(point_value=-1)})
        with pytest.raises(ValueError, match="point_value"):
            get_market_config("TEST", manifest_path=mp)


# ── 10. Malformed manifest rejected ─────────────────────────────────────────

class TestMalformedManifest:
    def test_not_json(self, tmp_path):
        mp = tmp_path / "market_manifest.json"
        mp.write_text("this is not json{{{")
        with pytest.raises(ValueError, match="Malformed"):
            get_market_config("SPY", manifest_path=mp)

    def test_no_instruments_key(self, tmp_path):
        mp = tmp_path / "market_manifest.json"
        mp.write_text('{"version": 1}')
        with pytest.raises(ValueError, match="instruments"):
            get_market_config("SPY", manifest_path=mp)

    def test_empty_instruments(self, tmp_path):
        mp = tmp_path / "market_manifest.json"
        mp.write_text('{"version": 1, "instruments": {}}')
        with pytest.raises(ValueError, match="non-empty"):
            get_market_config("SPY", manifest_path=mp)

    def test_missing_field(self, tmp_path):
        entry = _valid_entry()
        del entry["tick_size"]
        mp = _write_manifest(tmp_path, {"TEST": entry})
        with pytest.raises(ValueError, match="Missing.*tick_size"):
            get_market_config("TEST", manifest_path=mp)

    def test_missing_manifest_file(self, tmp_path):
        mp = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError):
            get_market_config("SPY", manifest_path=mp)

    def test_invalid_asset_class(self, tmp_path):
        mp = _write_manifest(tmp_path, {"TEST": _valid_entry(asset_class="CRYPTO")})
        with pytest.raises(ValueError, match="asset_class"):
            get_market_config("TEST", manifest_path=mp)

    def test_invalid_session_open(self, tmp_path):
        mp = _write_manifest(tmp_path, {"TEST": _valid_entry(session_open="9:30")})
        with pytest.raises(ValueError, match="session_open"):
            get_market_config("TEST", manifest_path=mp)

    def test_invalid_price_scale(self, tmp_path):
        mp = _write_manifest(tmp_path, {"TEST": _valid_entry(price_scale=-1)})
        with pytest.raises(ValueError, match="price_scale"):
            get_market_config("TEST", manifest_path=mp)


# ── Immutability ─────────────────────────────────────────────────────────────

class TestImmutability:
    def test_frozen(self):
        cfg = get_market_config("SPY")
        with pytest.raises(AttributeError):
            cfg.tick_size = 999

    def test_is_dataclass(self):
        cfg = get_market_config("SPY")
        assert isinstance(cfg, MarketConfig)


# ── get_all_symbols ──────────────────────────────────────────────────────────

class TestGetAllSymbols:
    def test_returns_sorted(self):
        syms = get_all_symbols()
        assert syms == ["MES", "MNQ", "NVDA", "SPY"]

    def test_count(self):
        assert len(get_all_symbols()) == 4
