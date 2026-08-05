"""Tests for futures manifest infrastructure.

Covers all 12 required verification points:
  1. MES/MNQ not treated as Stock
  2. Equity and futures manifests are separate
  3. Tick size and tick value correct
  4. Globex raw preserved
  5. Strategy filters 08:30–15:00 CT
  6. ORB uses 08:30–08:34 CT
  7. No session crosses a rollover
  8. Contract metadata present in trade
  9. Old Yahoo not selected as IBKR canonical
  10. Lab enables symbol only with validated contract
  11. Replay Bot accepts futures instruments without duplicating strategy
  12. Full regression green (run separately)
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from trading_lab.futures_manifest import (
    load_futures_manifest,
    get_futures_root_symbols,
    get_futures_spec,
    get_validated_contracts,
    has_validated_data,
    is_futures_symbol,
    futures_session_config,
    register_contract,
)
from trading_lab.timeframe_aggregation import is_ibkr_equity


# ── Fixtures ─────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

@pytest.fixture
def real_futures_dir():
    """Point to the real futures dir in the repo."""
    return REPO_ROOT / "dati" / "futures"


@pytest.fixture
def real_dati_dir():
    """Point to the real dati dir."""
    return REPO_ROOT / "dati"


@pytest.fixture
def tmp_futures_dir():
    """Create a temp futures dir with a manifest for isolated tests."""
    d = Path(tempfile.mkdtemp())
    manifest = {
        "provider": "IBKR",
        "instrument_type": "FUTURE",
        "root_symbols": {
            "MES": {
                "secType": "FUT",
                "exchange": "CME",
                "currency": "USD",
                "tick_size": "0.25",
                "tick_value_usd": "1.25",
                "point_value_usd": "5.00",
                "strategy_session_timezone": "America/Chicago",
                "strategy_session_open": "08:30",
                "strategy_session_close": "15:00",
                "orb_start": "08:30",
                "orb_duration_minutes": 5,
                "contracts": [],
            },
            "MNQ": {
                "secType": "FUT",
                "exchange": "CME",
                "currency": "USD",
                "tick_size": "0.25",
                "tick_value_usd": "0.50",
                "point_value_usd": "2.00",
                "strategy_session_timezone": "America/Chicago",
                "strategy_session_open": "08:30",
                "strategy_session_close": "15:00",
                "orb_start": "08:30",
                "orb_duration_minutes": 5,
                "contracts": [],
            },
        },
    }
    (d / "futures_manifest.json").write_text(json.dumps(manifest, indent=2))
    (d / "1m" / "MES").mkdir(parents=True)
    (d / "1m" / "MNQ").mkdir(parents=True)
    yield d
    shutil.rmtree(d)


@pytest.fixture
def tmp_futures_with_contract(tmp_futures_dir):
    """Tmp dir with a mock validated contract for MES."""
    # Create a mock CSV
    csv_path = tmp_futures_dir / "1m" / "MES" / "MESU6_1m.csv"
    csv_path.write_text(
        "time_ct,open,high,low,close,volume\n"
        "2026-07-28 08:30:00,5600.25,5601.00,5599.75,5600.50,150\n"
        "2026-07-28 08:31:00,5600.50,5601.25,5600.00,5600.75,120\n"
        "2026-07-28 08:32:00,5600.75,5601.50,5600.25,5601.00,100\n"
        "2026-07-28 08:33:00,5601.00,5601.75,5600.50,5601.25,90\n"
        "2026-07-28 08:34:00,5601.25,5602.00,5600.75,5601.50,80\n"
    )
    # Register contract in manifest
    register_contract("MES", {
        "localSymbol": "MESU6",
        "expiry": "20260918",
        "conId": 999999,
        "tradingClass": "MES",
        "exchange": "CME",
        "file": "1m/MES/MESU6_1m.csv",
        "downloaded_at": "2026-08-01T12:00:00-05:00",
        "earliest_bar": "2026-07-28",
        "latest_bar": "2026-07-28",
        "session_count": 1,
    }, futures_dir=tmp_futures_dir)
    return tmp_futures_dir


# ── 1. MES/MNQ not treated as Stock ─────────────────────────────────────────

class TestNotTreatedAsStock:
    def test_mes_not_ibkr_equity(self, real_dati_dir):
        """MES must not be in the IBKR equity symbol set."""
        assert not is_ibkr_equity("MES", real_dati_dir)

    def test_mnq_not_ibkr_equity(self, real_dati_dir):
        """MNQ must not be in the IBKR equity symbol set."""
        assert not is_ibkr_equity("MNQ", real_dati_dir)

    def test_spy_is_ibkr_equity(self, real_dati_dir):
        """SPY must still be in the IBKR equity set (sanity)."""
        assert is_ibkr_equity("SPY", real_dati_dir)

    def test_mes_is_futures(self, real_futures_dir):
        """MES is recognized as a futures root symbol."""
        assert is_futures_symbol("MES", real_futures_dir)

    def test_mnq_is_futures(self, real_futures_dir):
        """MNQ is recognized as a futures root symbol."""
        assert is_futures_symbol("MNQ", real_futures_dir)

    def test_spy_not_futures(self, real_futures_dir):
        """SPY must not be recognized as futures."""
        assert not is_futures_symbol("SPY", real_futures_dir)


# ── 2. Manifests are separate ────────────────────────────────────────────────

class TestManifestsSeparate:
    def test_equity_manifest_excludes_mes_mnq(self, real_dati_dir):
        """The equity manifest must not list MES/MNQ in its symbols."""
        manifest_path = real_dati_dir / "1m" / "ibkr_manifest.json"
        data = json.loads(manifest_path.read_text())
        symbols = set(data.get("symbols", []))
        assert "MES" not in symbols
        assert "MNQ" not in symbols

    def test_futures_manifest_exists(self, real_futures_dir):
        """A separate futures manifest must exist."""
        mp = real_futures_dir / "futures_manifest.json"
        assert mp.exists()

    def test_futures_manifest_has_instrument_type(self, real_futures_dir):
        """Futures manifest declares instrument_type FUTURE."""
        m = load_futures_manifest(real_futures_dir)
        assert m.get("instrument_type") == "FUTURE"

    def test_futures_manifest_has_both_roots(self, real_futures_dir):
        """Futures manifest has MES and MNQ."""
        roots = get_futures_root_symbols(real_futures_dir)
        assert "MES" in roots
        assert "MNQ" in roots


# ── 3. Tick size and tick value correct ──────────────────────────────────────

class TestTickValues:
    def test_mes_tick_size(self, real_futures_dir):
        spec = get_futures_spec("MES", real_futures_dir)
        assert spec is not None
        assert float(spec["tick_size"]) == 0.25

    def test_mes_tick_value(self, real_futures_dir):
        spec = get_futures_spec("MES", real_futures_dir)
        assert float(spec["tick_value_usd"]) == 1.25

    def test_mes_point_value(self, real_futures_dir):
        spec = get_futures_spec("MES", real_futures_dir)
        assert float(spec["point_value_usd"]) == 5.00

    def test_mnq_tick_size(self, real_futures_dir):
        spec = get_futures_spec("MNQ", real_futures_dir)
        assert float(spec["tick_size"]) == 0.25

    def test_mnq_tick_value(self, real_futures_dir):
        spec = get_futures_spec("MNQ", real_futures_dir)
        assert float(spec["tick_value_usd"]) == 0.50

    def test_mnq_point_value(self, real_futures_dir):
        spec = get_futures_spec("MNQ", real_futures_dir)
        assert float(spec["point_value_usd"]) == 2.00

    def test_session_config_tick_size(self, real_futures_dir):
        cfg = futures_session_config("MES", real_futures_dir)
        assert cfg["tick_size"] == 0.25


# ── 4. Globex raw preserved ─────────────────────────────────────────────────

class TestGlobexPreserved:
    def test_old_mes_has_globex_bars(self, real_dati_dir):
        """Old MES_1m.csv starts at 00:00 (Globex) — raw is preserved."""
        csv_path = real_dati_dir / "1m" / "MES_1m.csv"
        if not csv_path.exists():
            pytest.skip("MES_1m.csv not present")
        with open(csv_path) as f:
            f.readline()  # header
            first_data = f.readline().strip()
        # First bar should be at 00:00 (overnight Globex)
        time_str = first_data.split(",")[0]
        hour = int(time_str.split(" ")[1].split(":")[0])
        assert hour == 0, f"Expected Globex bar at hour 0, got {hour}"


# ── 5. Strategy filters 08:30–15:00 CT ──────────────────────────────────────

class TestSessionWindow:
    def test_mes_session_open(self, real_futures_dir):
        cfg = futures_session_config("MES", real_futures_dir)
        assert cfg["session_open"] == "08:30"

    def test_mes_session_close(self, real_futures_dir):
        spec = get_futures_spec("MES", real_futures_dir)
        assert spec["strategy_session_close"] == "15:00"

    def test_mes_session_timezone(self, real_futures_dir):
        cfg = futures_session_config("MES", real_futures_dir)
        assert cfg["timezone"] == "America/Chicago"

    def test_mnq_session_open(self, real_futures_dir):
        cfg = futures_session_config("MNQ", real_futures_dir)
        assert cfg["session_open"] == "08:30"

    def test_mnq_session_timezone(self, real_futures_dir):
        cfg = futures_session_config("MNQ", real_futures_dir)
        assert cfg["timezone"] == "America/Chicago"


# ── 6. ORB uses 08:30–08:34 CT ──────────────────────────────────────────────

class TestOrbWindow:
    def test_mes_orb_start(self, real_futures_dir):
        cfg = futures_session_config("MES", real_futures_dir)
        assert cfg["orb_start"] == "08:30"

    def test_mes_orb_duration(self, real_futures_dir):
        cfg = futures_session_config("MES", real_futures_dir)
        assert cfg["orb_duration_minutes"] == 5

    def test_mnq_orb_start(self, real_futures_dir):
        cfg = futures_session_config("MNQ", real_futures_dir)
        assert cfg["orb_start"] == "08:30"


# ── 7. No session crosses rollover ──────────────────────────────────────────

class TestRolloverPolicy:
    def test_manifest_rollover_policy(self, real_futures_dir):
        m = load_futures_manifest(real_futures_dir)
        policy = m.get("rollover_policy", {})
        assert "No back-adjustment" in policy.get("description", "")

    def test_contracts_sorted_by_expiry(self, tmp_futures_with_contract):
        """Contracts are stored sorted by expiry."""
        # Add a second contract with earlier expiry
        register_contract("MES", {
            "localSymbol": "MESM6",
            "expiry": "20260619",
            "conId": 888888,
            "tradingClass": "MES",
            "exchange": "CME",
            "file": "1m/MES/MESM6_1m.csv",
            "downloaded_at": "2026-08-01T12:00:00-05:00",
            "earliest_bar": "2026-04-01",
            "latest_bar": "2026-06-18",
            "session_count": 50,
        }, futures_dir=tmp_futures_with_contract)
        # Write dummy file so it validates
        (tmp_futures_with_contract / "1m" / "MES" / "MESM6_1m.csv").write_text("time_ct,open,high,low,close,volume\n")
        contracts = get_validated_contracts("MES", tmp_futures_with_contract)
        expiries = [c["expiry"] for c in contracts]
        assert expiries == sorted(expiries), "Contracts must be sorted by expiry"


# ── 8. Contract metadata present in trade ────────────────────────────────────

class TestContractMetadata:
    def test_validated_contract_has_required_fields(self, tmp_futures_with_contract):
        contracts = get_validated_contracts("MES", tmp_futures_with_contract)
        assert len(contracts) == 1
        c = contracts[0]
        assert c["localSymbol"] == "MESU6"
        assert c["expiry"] == "20260918"
        assert c["conId"] == 999999
        assert c["tradingClass"] == "MES"
        assert c["exchange"] == "CME"

    def test_register_requires_all_fields(self, tmp_futures_dir):
        with pytest.raises(ValueError, match="Missing required"):
            register_contract("MES", {
                "localSymbol": "MESU6",
                # Missing most fields
            }, futures_dir=tmp_futures_dir)


# ── 9. Old Yahoo not selected as IBKR canonical ─────────────────────────────

class TestYahooExclusion:
    def test_equity_manifest_notes_yahoo_origin(self, real_dati_dir):
        """Equity manifest explicitly marks MES/MNQ as Yahoo, not IBKR."""
        manifest_path = real_dati_dir / "1m" / "ibkr_manifest.json"
        data = json.loads(manifest_path.read_text())
        excluded = data.get("excluded", {})
        assert "MES" in excluded
        assert "MNQ" in excluded
        assert "Yahoo" in excluded["MES"]
        assert "Yahoo" in excluded["MNQ"]

    def test_no_validated_data_without_ibkr_download(self, real_futures_dir):
        """Without IBKR download, MES has no validated contracts."""
        assert not has_validated_data("MES", real_futures_dir)
        assert not has_validated_data("MNQ", real_futures_dir)


# ── 10. Lab enables symbol only with validated contract ──────────────────────

class TestLabEnablement:
    def test_no_contracts_means_not_shown(self, tmp_futures_dir):
        """Without validated contracts, futures root should not appear in Lab."""
        # No contracts registered, no CSV files
        assert not has_validated_data("MES", tmp_futures_dir)
        assert not has_validated_data("MNQ", tmp_futures_dir)

    def test_with_contract_means_shown(self, tmp_futures_with_contract):
        """With a validated contract, futures root should appear."""
        assert has_validated_data("MES", tmp_futures_with_contract)

    def test_contract_without_csv_not_validated(self, tmp_futures_dir):
        """A registered contract whose CSV is missing is not validated."""
        register_contract("MES", {
            "localSymbol": "MESU6",
            "expiry": "20260918",
            "conId": 999999,
            "tradingClass": "MES",
            "exchange": "CME",
            "file": "1m/MES/MESU6_1m.csv",
            "downloaded_at": "2026-08-01T12:00:00-05:00",
            "earliest_bar": "2026-07-28",
            "latest_bar": "2026-07-28",
            "session_count": 1,
        }, futures_dir=tmp_futures_dir)
        # CSV does not exist
        assert not has_validated_data("MES", tmp_futures_dir)


# ── 11. Replay/Runner accepts futures instruments ────────────────────────────

class TestRunnerInstrumentAgnostic:
    def test_session_config_returns_futures_params(self, real_futures_dir):
        """futures_session_config returns all params the runner needs."""
        cfg = futures_session_config("MES", real_futures_dir)
        assert cfg is not None
        required_keys = {
            "timezone", "session_open", "session_close",
            "orb_start", "orb_duration_minutes",
            "tick_size", "tick_value_usd", "point_value_usd",
        }
        assert required_keys <= set(cfg.keys())

    def test_equity_returns_none(self, real_futures_dir):
        """SPY is not a futures root, returns None."""
        cfg = futures_session_config("SPY", real_futures_dir)
        assert cfg is None

    def test_config_values_match_manifest(self, real_futures_dir):
        """Session config values must match manifest values exactly."""
        cfg = futures_session_config("MNQ", real_futures_dir)
        assert cfg["tick_size"] == 0.25
        assert cfg["tick_value_usd"] == 0.50
        assert cfg["point_value_usd"] == 2.00
        assert cfg["timezone"] == "America/Chicago"
        assert cfg["session_open"] == "08:30"
        assert cfg["session_close"] == "15:00"


# ── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_missing_manifest_returns_empty(self):
        """If no manifest exists, functions return safe defaults."""
        fake_dir = Path("/nonexistent/path")
        assert get_futures_root_symbols(fake_dir) == []
        assert not is_futures_symbol("MES", fake_dir)
        assert get_futures_spec("MES", fake_dir) is None
        assert not has_validated_data("MES", fake_dir)

    def test_register_unknown_root_raises(self, tmp_futures_dir):
        """Registering a contract for an unknown root raises ValueError."""
        with pytest.raises(ValueError, match="not in manifest"):
            register_contract("ES", {
                "localSymbol": "ESU6",
                "expiry": "20260918",
                "conId": 111111,
                "tradingClass": "ES",
                "exchange": "CME",
                "file": "1m/ES/ESU6_1m.csv",
                "downloaded_at": "2026-08-01T12:00:00-05:00",
                "earliest_bar": "2026-07-28",
                "latest_bar": "2026-07-28",
                "session_count": 1,
            }, futures_dir=tmp_futures_dir)

    def test_re_register_replaces(self, tmp_futures_with_contract):
        """Re-registering same localSymbol replaces, not duplicates."""
        register_contract("MES", {
            "localSymbol": "MESU6",
            "expiry": "20260918",
            "conId": 999999,
            "tradingClass": "MES",
            "exchange": "CME",
            "file": "1m/MES/MESU6_1m.csv",
            "downloaded_at": "2026-08-02T12:00:00-05:00",
            "earliest_bar": "2026-07-28",
            "latest_bar": "2026-08-01",
            "session_count": 5,
        }, futures_dir=tmp_futures_with_contract)
        contracts = get_validated_contracts("MES", tmp_futures_with_contract)
        assert len(contracts) == 1
        assert contracts[0]["session_count"] == 5


# ── Download script uses Future not Stock ────────────────────────────────────

class TestDownloaderStructure:
    def test_downloader_script_exists(self):
        script = REPO_ROOT / "scripts" / "download_ib_futures_1m.py"
        assert script.exists()

    def test_downloader_uses_future_not_stock(self):
        script = REPO_ROOT / "scripts" / "download_ib_futures_1m.py"
        content = script.read_text()
        assert "from ib_insync import IB, Future" in content
        # Must not import Stock from ib_insync
        assert "import Stock" not in content
        # Must not instantiate Stock anywhere in executable code
        import re
        # Find all lines that call Stock(...) as a constructor (not in strings)
        stock_calls = re.findall(r'^\s*\w.*=\s*Stock\(', content, re.MULTILINE)
        assert stock_calls == [], f"Downloader must not use Stock(): {stock_calls}"

    def test_downloader_saves_contract_identity(self):
        script = REPO_ROOT / "scripts" / "download_ib_futures_1m.py"
        content = script.read_text()
        for field in ["localSymbol", "expiry", "conId", "tradingClass", "exchange"]:
            assert field in content, f"Downloader must save {field}"


# ── File structure ───────────────────────────────────────────────────────────

class TestFileStructure:
    def test_futures_dir_exists(self, real_futures_dir):
        assert real_futures_dir.is_dir()

    def test_mes_subdir_exists(self, real_futures_dir):
        assert (real_futures_dir / "1m" / "MES").is_dir()

    def test_mnq_subdir_exists(self, real_futures_dir):
        assert (real_futures_dir / "1m" / "MNQ").is_dir()

    def test_manifest_in_futures_dir(self, real_futures_dir):
        assert (real_futures_dir / "futures_manifest.json").exists()
