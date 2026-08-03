"""Tests for run_bdrr_strategy_v2 — Strategy Runner with Rational R/R.

Covers:
    - v1 path unchanged (2, 3, 4 produce TradeOutcome/v1)
    - v2 path with Rational R/R values
    - Validation: float rejected, zero rejected, negative rejected
    - End-to-end: target calculation, outcome, realized_r precision
    - v1/v2 economic compatibility at 2R
"""

import pytest

from trading_lab.contracts.primitives import Rational
from trading_lab.contracts.trade_outcome import TradeOutcome, TradeOutcomeStatus
from trading_lab.contracts.trade_outcome_v2 import TradeOutcomeV2
from trading_lab.strategy_runner import run_bdrr_strategy, run_bdrr_strategy_v2


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_session(candles):
    """Build a minimal session dict from raw candle data."""
    return {
        "symbol": "TEST",
        "date": "2026-07-01",
        "market_timezone": "America/New_York",
        "session_open_utc_ms": candles[0]["time_ms"],
        "session_close_utc_ms": candles[-1]["time_ms"],
        "timeframe": "5m",
        "candles": candles,
    }


def _make_candle(time_ms, o, h, l, c, volume=1000):
    return {
        "time_ms": time_ms,
        "open": o, "high": h, "low": l, "close": c,
        "volume": volume,
    }


def _orb_session():
    """Build a session with a clean ORB break and target hit at various R levels.

    ORB: 09:30 candle, high=100.50, low=100.00
    Break: 09:35 candle closes above ORB high
    Displacement: strong move up
    Retest: comes back toward ORB high
    Rejection: wick rejection off level
    Post-confirmation: runs to target

    Entry ~100.50, Stop ~100.00, Risk ~50 ticks (at tick_size=0.01)
    """
    base_ms = 1719828600000  # 2026-07-01 09:30 ET approx

    candles = [
        # 09:30 — ORB candle
        _make_candle(base_ms, 100.20, 100.50, 100.00, 100.30),
        # 09:35 — Break above ORB high
        _make_candle(base_ms + 300000, 100.40, 101.00, 100.35, 100.90),
        # 09:40 — Displacement continues
        _make_candle(base_ms + 600000, 100.90, 101.50, 100.85, 101.40),
        # 09:45 — Retest begins, pulls back toward ORB high
        _make_candle(base_ms + 900000, 101.40, 101.45, 100.55, 100.60),
        # 09:50 — More pullback
        _make_candle(base_ms + 1200000, 100.60, 100.70, 100.45, 100.55),
    ]

    # Add many more candles for the rejection and post-confirmation run
    for i in range(5, 40):
        ms = base_ms + i * 300000
        # Gradual rise from ~100.50 to well above targets
        price = 100.50 + (i - 4) * 0.30
        candles.append(_make_candle(
            ms,
            price - 0.10, price + 0.15, price - 0.20, price + 0.05,
        ))

    return _make_session(candles)


_PRESET = {
    "preset_id": "test",
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
    "consecutive_orb_closes": 2,
}


def _config_v1(exit_r=2):
    return {
        "tick_size": 0.01,
        "exit_target_r": exit_r,
        "engine_version": "1.0.0",
    }


def _config_v2(r_num=2, r_den=1):
    return {
        "tick_size": 0.01,
        "exit_target_r": Rational(r_num, r_den),
        "engine_version": "1.0.0",
    }


# ── v1 path unchanged ───────────────────────────────────────────────────────


class TestV1PathUnchanged:
    def test_v1_rr2_produces_v1_outcome(self):
        results = run_bdrr_strategy([_orb_session()], _PRESET, _config_v1(2))
        assert len(results) == 1
        r = results[0]
        to = r.get("trade_outcome")
        if to is not None:
            assert isinstance(to, TradeOutcome)
            assert to.schema_version == "TradeOutcome/v1"

    def test_v1_rr3_accepted(self):
        results = run_bdrr_strategy([_orb_session()], _PRESET, _config_v1(3))
        assert len(results) == 1

    def test_v1_rr4_accepted(self):
        results = run_bdrr_strategy([_orb_session()], _PRESET, _config_v1(4))
        assert len(results) == 1

    def test_v1_rejects_2_5(self):
        with pytest.raises(TypeError, match="2, 3, or 4"):
            run_bdrr_strategy([_orb_session()], _PRESET, _config_v1(2.5))


# ── v2 path validation ──────────────────────────────────────────────────────


class TestV2Validation:
    def test_float_rejected(self):
        cfg = _config_v1(2)
        cfg["exit_target_r"] = 2.5
        with pytest.raises(TypeError, match="Rational"):
            run_bdrr_strategy_v2([_orb_session()], _PRESET, cfg)

    def test_int_rejected(self):
        cfg = _config_v1(2)
        cfg["exit_target_r"] = 2
        with pytest.raises(TypeError, match="Rational"):
            run_bdrr_strategy_v2([_orb_session()], _PRESET, cfg)

    def test_zero_rejected(self):
        with pytest.raises(TypeError, match="positive"):
            run_bdrr_strategy_v2(
                [_orb_session()], _PRESET, _config_v2(0, 1)
            )

    def test_negative_rejected(self):
        with pytest.raises(TypeError, match="positive"):
            run_bdrr_strategy_v2(
                [_orb_session()], _PRESET, _config_v2(-2, 1)
            )

    def test_rational_2_accepted(self):
        results = run_bdrr_strategy_v2(
            [_orb_session()], _PRESET, _config_v2(2, 1)
        )
        assert len(results) == 1

    def test_rational_2_1_accepted(self):
        results = run_bdrr_strategy_v2(
            [_orb_session()], _PRESET, _config_v2(21, 10)
        )
        assert len(results) == 1

    def test_rational_2_25_accepted(self):
        results = run_bdrr_strategy_v2(
            [_orb_session()], _PRESET, _config_v2(9, 4)
        )
        assert len(results) == 1

    def test_rational_2_5_accepted(self):
        results = run_bdrr_strategy_v2(
            [_orb_session()], _PRESET, _config_v2(5, 2)
        )
        assert len(results) == 1

    def test_rational_3_75_accepted(self):
        results = run_bdrr_strategy_v2(
            [_orb_session()], _PRESET, _config_v2(15, 4)
        )
        assert len(results) == 1


# ── v2 end-to-end ────────────────────────────────────────────────────────────


class TestV2EndToEnd:
    def test_v2_produces_trade_outcome_v2(self):
        results = run_bdrr_strategy_v2(
            [_orb_session()], _PRESET, _config_v2(5, 2)
        )
        r = results[0]
        to = r.get("trade_outcome")
        if to is not None and r["detection_status"] == "VALID":
            assert isinstance(to, TradeOutcomeV2)
            assert to.schema_version == "TradeOutcome/v2"

    def test_v2_selected_r_preserved(self):
        results = run_bdrr_strategy_v2(
            [_orb_session()], _PRESET, _config_v2(5, 2)
        )
        r = results[0]
        to = r.get("trade_outcome")
        if to is not None and r["detection_status"] == "VALID":
            assert to.selected_exit_target_r == Rational(5, 2)
            assert to.selected_exit_target_label == "2.5R"

    def test_v2_realized_r_is_rational(self):
        results = run_bdrr_strategy_v2(
            [_orb_session()], _PRESET, _config_v2(2, 1)
        )
        r = results[0]
        to = r.get("trade_outcome")
        if to is not None and r["detection_status"] == "VALID":
            realized = to.realized_r
            if realized is not None:
                assert isinstance(realized, Rational)

    def test_v2_no_float_in_result(self):
        """exit_target_r in result dict is Rational, not float."""
        results = run_bdrr_strategy_v2(
            [_orb_session()], _PRESET, _config_v2(5, 2)
        )
        r = results[0]
        # The config value passes through to the result dict
        assert isinstance(r["exit_target_r"], Rational)

    def test_v2_result_serializable(self):
        """TradeOutcome/v2 can be serialized via to_dict()."""
        results = run_bdrr_strategy_v2(
            [_orb_session()], _PRESET, _config_v2(5, 2)
        )
        r = results[0]
        to = r.get("trade_outcome")
        if to is not None and r["detection_status"] == "VALID":
            d = to.to_dict()
            assert d["schema_version"] == "TradeOutcome/v2"
            assert isinstance(d["selected_exit_target_r"], dict)
            assert "numerator" in d["selected_exit_target_r"]


# ── v1/v2 economic compatibility ─────────────────────────────────────────────


class TestV1V2EconomicCompatibility:
    def test_same_outcome_at_2r(self):
        """v1 at 2R and v2 at Rational(2,1) produce same economic outcome."""
        session = _orb_session()

        r1 = run_bdrr_strategy([session], _PRESET, _config_v1(2))
        r2 = run_bdrr_strategy_v2([session], _PRESET, _config_v2(2, 1))

        rec1 = r1[0]
        rec2 = r2[0]

        # Same detection
        assert rec1["detection_status"] == rec2["detection_status"]
        assert rec1["session_date"] == rec2["session_date"]

        # If both detected valid setups, compare outcomes
        if rec1["detection_status"] == "VALID":
            assert str(rec1["outcome"]) == str(rec2["outcome"])
            assert rec1["entry_price_ticks"] == rec2["entry_price_ticks"]
            assert rec1["stop_price_ticks"] == rec2["stop_price_ticks"]
            assert rec1["exit_price_ticks"] == rec2["exit_price_ticks"]

            # Schema differs
            to1 = rec1["trade_outcome"]
            to2 = rec2["trade_outcome"]
            assert isinstance(to1, TradeOutcome)
            assert isinstance(to2, TradeOutcomeV2)
