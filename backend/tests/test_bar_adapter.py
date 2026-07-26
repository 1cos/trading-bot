"""Tests for canonical raw-candle-to-Bar adapter.

Parity vectors verified by running rawCandleToCanonicalBar in
estrategie/bdrr_strategy_runner.js via Node.js on dati/SPY_5m.csv candles.
"""

import copy

import pytest

from trading_lab.bar_adapter import raw_candle_to_canonical_bar
from trading_lab.contracts.bar import Bar
from trading_lab.contracts.primitives import PriceTicks


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICK_SIZE = 0.01
TICK_SIZE_STR = "0.01"


def raw_candle(time_ms=1777037400000, open_=710.75, high=711.16,
               low=709.76, close=709.835):
    return {
        "time_ms": time_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


# SPY_5m.csv row 0 — JS parity vector
SPY_ROW0 = raw_candle(
    time_ms=1777037400000,
    open_=710.75,
    high=711.1599731445312,
    low=709.760009765625,
    close=709.8350219726562,
)
# JS expected ticks for row 0:
#   open=71075, high=71116, low=70976, close=70984
SPY_ROW0_OPEN = 71075
SPY_ROW0_HIGH = 71116
SPY_ROW0_LOW = 70976
SPY_ROW0_CLOSE = 70984


# ═══════════════════════════════════════════════════════════════════════════════
# Output type
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutputType:
    def test_returns_bar(self):
        bar = raw_candle_to_canonical_bar(raw_candle(), TICK_SIZE)
        assert isinstance(bar, Bar)

    def test_immutable(self):
        bar = raw_candle_to_canonical_bar(raw_candle(), TICK_SIZE)
        with pytest.raises(AttributeError):
            bar.open = PriceTicks(ticks=0, tick_size="0.01")  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# JS parity — SPY row 0
# ═══════════════════════════════════════════════════════════════════════════════


class TestJSParity:
    """Exact parity with JS rawCandleToCanonicalBar on SPY_5m.csv row 0."""

    def test_timestamp(self):
        bar = raw_candle_to_canonical_bar(SPY_ROW0, TICK_SIZE)
        assert bar.bar_utc_ms == 1777037400000

    def test_open_ticks(self):
        bar = raw_candle_to_canonical_bar(SPY_ROW0, TICK_SIZE)
        assert bar.open.ticks == SPY_ROW0_OPEN

    def test_high_ticks(self):
        bar = raw_candle_to_canonical_bar(SPY_ROW0, TICK_SIZE)
        assert bar.high.ticks == SPY_ROW0_HIGH

    def test_low_ticks(self):
        bar = raw_candle_to_canonical_bar(SPY_ROW0, TICK_SIZE)
        assert bar.low.ticks == SPY_ROW0_LOW

    def test_close_ticks(self):
        bar = raw_candle_to_canonical_bar(SPY_ROW0, TICK_SIZE)
        assert bar.close.ticks == SPY_ROW0_CLOSE

    def test_tick_size_propagated(self):
        bar = raw_candle_to_canonical_bar(SPY_ROW0, TICK_SIZE)
        assert bar.open.tick_size == TICK_SIZE_STR
        assert bar.high.tick_size == TICK_SIZE_STR
        assert bar.low.tick_size == TICK_SIZE_STR
        assert bar.close.tick_size == TICK_SIZE_STR

    def test_volume_none(self):
        bar = raw_candle_to_canonical_bar(SPY_ROW0, TICK_SIZE)
        assert bar.volume is None

    def test_row1_parity(self):
        """SPY row 1: open=70984, high=71042, low=70955, close=71031."""
        c = raw_candle(
            time_ms=1777037700000,
            open_=709.8400268554688,
            high=710.4199829101562,
            low=709.5499877929688,
            close=710.3099975585938,
        )
        bar = raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert bar.open.ticks == 70984
        assert bar.high.ticks == 71042
        assert bar.low.ticks == 70955
        assert bar.close.ticks == 71031

    def test_last_row_parity(self):
        """SPY last row: open=74838, high=74858, low=74809, close=74833."""
        c = raw_candle(
            time_ms=1784663700000,
            open_=748.3800048828125,
            high=748.5800170898438,
            low=748.0900268554688,
            close=748.3300170898438,
        )
        bar = raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert bar.open.ticks == 74838
        assert bar.high.ticks == 74858
        assert bar.low.ticks == 74809
        assert bar.close.ticks == 74833

    def test_rounding_edge(self):
        """SPY 750.364990234375 → 75036 ticks (not 75037).
        Confirmed from JS: priceToTicks(750.364990234375, 0.01) = 75036."""
        c = raw_candle(low=750.364990234375)
        bar = raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert bar.low.ticks == 75036


# ═══════════════════════════════════════════════════════════════════════════════
# Exact serialization
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialization:
    def test_to_dict_shape(self):
        bar = raw_candle_to_canonical_bar(raw_candle(), TICK_SIZE)
        d = bar.to_dict()
        assert set(d.keys()) == {
            "bar_utc_ms", "open", "high", "low", "close", "volume"
        }
        assert isinstance(d["open"], dict)
        assert d["open"]["tick_size"] == TICK_SIZE_STR
        assert d["volume"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# Tick-size variants
# ═══════════════════════════════════════════════════════════════════════════════


class TestTickSizeVariants:
    def test_quarter_tick(self):
        c = raw_candle(open_=100.00, high=100.25, low=99.75, close=100.00)
        bar = raw_candle_to_canonical_bar(c, 0.25)
        assert bar.open.ticks == 400
        assert bar.high.ticks == 401
        assert bar.low.ticks == 399
        assert bar.close.ticks == 400
        assert bar.open.tick_size == "0.25"

    def test_int_tick_size(self):
        c = raw_candle(open_=100.0, high=101.0, low=99.0, close=100.0)
        bar = raw_candle_to_canonical_bar(c, 1)
        assert bar.open.ticks == 100
        assert bar.open.tick_size == "1.0"


# ═══════════════════════════════════════════════════════════════════════════════
# Rounding boundaries
# ═══════════════════════════════════════════════════════════════════════════════


class TestRoundingBoundaries:
    def test_exact_tick_boundary(self):
        c = raw_candle(open_=100.50)
        bar = raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert bar.open.ticks == 10050

    def test_just_below_boundary(self):
        c = raw_candle(open_=100.004)
        bar = raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert bar.open.ticks == 10000

    def test_just_above_boundary(self):
        c = raw_candle(open_=100.006)
        bar = raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert bar.open.ticks == 10001

    def test_half_tick_positive(self):
        c = raw_candle(open_=100.005)
        bar = raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert bar.open.ticks == 10001

    def test_negative_price(self):
        c = raw_candle(open_=-5.50, high=-5.00, low=-6.00, close=-5.25)
        bar = raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert bar.open.ticks == -550
        assert bar.close.ticks == -525


# ═══════════════════════════════════════════════════════════════════════════════
# Input not mutated
# ═══════════════════════════════════════════════════════════════════════════════


class TestInputNotMutated:
    def test_candle_unchanged(self):
        c = raw_candle()
        original = copy.deepcopy(c)
        raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert c == original


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid inputs
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidCandle:
    def test_none(self):
        with pytest.raises(TypeError, match="must be a dict"):
            raw_candle_to_canonical_bar(None, TICK_SIZE)

    def test_list(self):
        with pytest.raises(TypeError, match="must be a dict"):
            raw_candle_to_canonical_bar([], TICK_SIZE)

    def test_missing_time_ms(self):
        c = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
        with pytest.raises(KeyError):
            raw_candle_to_canonical_bar(c, TICK_SIZE)

    def test_missing_open(self):
        c = raw_candle()
        del c["open"]
        with pytest.raises(KeyError):
            raw_candle_to_canonical_bar(c, TICK_SIZE)

    def test_missing_high(self):
        c = raw_candle()
        del c["high"]
        with pytest.raises(KeyError):
            raw_candle_to_canonical_bar(c, TICK_SIZE)

    def test_missing_low(self):
        c = raw_candle()
        del c["low"]
        with pytest.raises(KeyError):
            raw_candle_to_canonical_bar(c, TICK_SIZE)

    def test_missing_close(self):
        c = raw_candle()
        del c["close"]
        with pytest.raises(KeyError):
            raw_candle_to_canonical_bar(c, TICK_SIZE)


class TestInvalidOHLC:
    def test_nan_open(self):
        c = raw_candle(open_=float("nan"))
        with pytest.raises(ValueError, match="finite"):
            raw_candle_to_canonical_bar(c, TICK_SIZE)

    def test_inf_high(self):
        c = raw_candle(high=float("inf"))
        with pytest.raises(ValueError, match="finite"):
            raw_candle_to_canonical_bar(c, TICK_SIZE)

    def test_string_close(self):
        c = raw_candle()
        c["close"] = "100.0"
        with pytest.raises(TypeError, match="finite number"):
            raw_candle_to_canonical_bar(c, TICK_SIZE)

    def test_bool_open(self):
        c = raw_candle()
        c["open"] = True
        with pytest.raises(TypeError, match="got bool"):
            raw_candle_to_canonical_bar(c, TICK_SIZE)


class TestInvalidTickSize:
    def test_zero(self):
        with pytest.raises(ValueError, match="positive"):
            raw_candle_to_canonical_bar(raw_candle(), 0)

    def test_negative(self):
        with pytest.raises(ValueError, match="positive"):
            raw_candle_to_canonical_bar(raw_candle(), -0.01)

    def test_nan(self):
        with pytest.raises(ValueError, match="positive"):
            raw_candle_to_canonical_bar(raw_candle(), float("nan"))

    def test_string(self):
        with pytest.raises(TypeError, match="positive finite number"):
            raw_candle_to_canonical_bar(raw_candle(), "0.01")

    def test_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            raw_candle_to_canonical_bar(raw_candle(), True)


# ═══════════════════════════════════════════════════════════════════════════════
# Extra fields ignored
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtraFields:
    def test_extra_fields_ignored(self):
        c = raw_candle()
        c["volume"] = 999999
        c["vwap"] = 710.0
        bar = raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert isinstance(bar, Bar)
        # volume from raw candle is NOT transferred (matching JS behavior)
        assert bar.volume is None


# ═══════════════════════════════════════════════════════════════════════════════
# No OHLC invariant enforcement
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoOHLCInvariant:
    def test_high_below_low_accepted(self):
        """Bar contract does not enforce high >= low (per Task 3)."""
        c = raw_candle(high=99.0, low=101.0)
        bar = raw_candle_to_canonical_bar(c, TICK_SIZE)
        assert bar.high.ticks < bar.low.ticks


# ═══════════════════════════════════════════════════════════════════════════════
# No session/ORB/detector fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoDetectorFields:
    def test_no_extra_attributes(self):
        bar = raw_candle_to_canonical_bar(raw_candle(), TICK_SIZE)
        d = bar.to_dict()
        assert set(d.keys()) == {
            "bar_utc_ms", "open", "high", "low", "close", "volume"
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministic:
    def test_repeated(self):
        c = raw_candle()
        bars = [raw_candle_to_canonical_bar(c, TICK_SIZE) for _ in range(10)]
        assert all(b == bars[0] for b in bars)


# ═══════════════════════════════════════════════════════════════════════════════
# Real CSV parity
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealCSVParity:
    """Convert first and last SPY candles and verify against JS vectors."""

    @pytest.fixture()
    def spy_candles(self):
        import os
        from trading_lab.csv_parser import parse_candles_from_csv
        csv_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "dati", "SPY_5m.csv"
        )
        if not os.path.exists(csv_path):
            pytest.skip("SPY_5m.csv not available")
        with open(csv_path) as f:
            return parse_candles_from_csv(f.read())

    def test_first_candle(self, spy_candles):
        bar = raw_candle_to_canonical_bar(spy_candles[0], 0.01)
        assert bar.bar_utc_ms == 1777037400000
        assert bar.open.ticks == 71075
        assert bar.high.ticks == 71116
        assert bar.low.ticks == 70976
        assert bar.close.ticks == 70984

    def test_last_candle(self, spy_candles):
        bar = raw_candle_to_canonical_bar(spy_candles[-1], 0.01)
        assert bar.bar_utc_ms == 1784663700000
        assert bar.open.ticks == 74838
        assert bar.high.ticks == 74858
        assert bar.low.ticks == 74809
        assert bar.close.ticks == 74833
