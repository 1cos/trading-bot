"""Tests for the ATR (Average True Range) foundation.

Covers all acceptance criteria and test matrix from B2 preflight.
Method under test: SMA of True Range (MaxBot-specific, not Wilder).
"""

import math

import pytest

from trading_lab.atr import atr_series, previous_atr, true_range


# ── Helpers ───────────────────────────────────────────────────────────────────


def _c(high, low, close=None, open_=None):
    """Build a minimal raw candle dict."""
    d = {
        "time_ms": 0,
        "open": open_ if open_ is not None else high,
        "high": high,
        "low": low,
        "close": close if close is not None else (high + low) / 2,
    }
    return d


def _uniform(n, high=10.0, low=8.0, close=9.0):
    """Build n identical candles."""
    return [_c(high, low, close) for _ in range(n)]


# ═══════════════════════════════════════════════════════════════════════════════
# true_range
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrueRangeBasic:
    def test_first_candle_no_gap(self):
        assert true_range(_c(10, 8), None) == 2.0

    def test_normal_no_gap(self):
        # prev close inside bar range: TR = high - low
        assert true_range(_c(12, 9), 10.0) == 3.0

    def test_gap_up(self):
        # prev close below low: TR = high - prev_close
        assert true_range(_c(15, 13), 10.0) == 5.0

    def test_gap_down(self):
        # prev close above high: TR = prev_close - low
        assert true_range(_c(8, 6), 12.0) == 6.0

    def test_doji_no_gap(self):
        assert true_range(_c(10, 10), 10.0) == 0.0

    def test_doji_with_gap(self):
        assert true_range(_c(10, 10), 8.0) == 2.0

    def test_integer_prices(self):
        assert true_range(_c(100, 98), 99) == 2.0

    def test_result_always_non_negative(self):
        for pc in [5.0, 10.0, 15.0, None]:
            tr = true_range(_c(10, 8), pc)
            assert tr >= 0, f"TR negative for previous_close={pc}"


# ── true_range validation ────────────────────────────────────────────────────


class TestTrueRangeValidation:
    def test_high_less_than_low(self):
        with pytest.raises(ValueError, match="high.*<.*low"):
            true_range(_c(5, 10))

    def test_missing_high(self):
        with pytest.raises(KeyError, match="high"):
            true_range({"low": 5.0}, None)

    def test_missing_low(self):
        with pytest.raises(KeyError, match="low"):
            true_range({"high": 10.0}, None)

    def test_bool_high(self):
        with pytest.raises(TypeError, match="bool"):
            true_range({"high": True, "low": 0.0}, None)

    def test_bool_low(self):
        with pytest.raises(TypeError, match="bool"):
            true_range({"high": 10.0, "low": False}, None)

    def test_nan_high(self):
        with pytest.raises(ValueError, match="finite"):
            true_range(_c(float("nan"), 5.0), None)

    def test_inf_low(self):
        with pytest.raises(ValueError, match="finite"):
            true_range(_c(10.0, float("-inf")), None)

    def test_nan_previous_close(self):
        with pytest.raises(ValueError, match="finite"):
            true_range(_c(10, 8), float("nan"))

    def test_inf_previous_close(self):
        with pytest.raises(ValueError, match="finite"):
            true_range(_c(10, 8), float("inf"))

    def test_bool_previous_close(self):
        with pytest.raises(TypeError, match="bool"):
            true_range(_c(10, 8), True)


# ═══════════════════════════════════════════════════════════════════════════════
# atr_series
# ═══════════════════════════════════════════════════════════════════════════════


class TestAtrSeriesBasic:
    def test_empty_list(self):
        assert atr_series([], 14) == []

    def test_14_uniform_candles(self):
        # All TR = 2.0 (h=10, l=8, no gaps)
        candles = _uniform(14)
        result = atr_series(candles, 14)
        assert len(result) == 14
        # First 13 are None (insufficient window)
        for i in range(13):
            assert result[i] is None, f"result[{i}] should be None"
        # Index 13: mean of 14 TR values all = 2.0
        assert result[13] == pytest.approx(2.0)

    def test_fewer_than_period(self):
        candles = _uniform(5)
        result = atr_series(candles, 14)
        assert all(v is None for v in result)

    def test_20_candles_with_varying_ranges(self):
        # Candles with TR = 1, 2, 3, ..., 20 (no gaps, prev_close=None for first)
        candles = []
        for i in range(20):
            tr = float(i + 1)
            candles.append(_c(100 + tr, 100.0, close=100.0))
        result = atr_series(candles, 14)
        # result[13] = mean(TR[0..13]) = mean(1..14) = 7.5
        assert result[13] == pytest.approx(7.5)
        # result[14] = mean(TR[1..14]) = mean(2..15) = 8.5
        assert result[14] == pytest.approx(8.5)
        # result[19] = mean(TR[6..19]) = mean(7..20) = 13.5
        assert result[19] == pytest.approx(13.5)

    def test_length_matches_input(self):
        for n in [0, 1, 5, 14, 30]:
            result = atr_series(_uniform(n), 14)
            assert len(result) == n

    def test_period_1(self):
        candles = [_c(10, 8), _c(12, 9), _c(15, 13)]
        result = atr_series(candles, period=1)
        assert result[0] == pytest.approx(2.0)  # h-l, no prev
        # TR[1] = max(12, 9) - min(9, 9) = 3.0 (prev close = 9)
        assert result[1] == pytest.approx(3.0)

    def test_rolling_sum_correctness(self):
        """Verify multiple consecutive rolling windows."""
        candles = _uniform(30, high=10, low=8, close=9)
        result = atr_series(candles, 14)
        # All TR = 2.0, so all ATR = 2.0 for i >= 13
        for i in range(13, 30):
            assert result[i] == pytest.approx(2.0), f"result[{i}]"


class TestAtrSeriesGap:
    def test_initial_previous_close_captures_gap(self):
        """Segment with explicit previous close captures the gap."""
        # Gap up: previous close was 90, first candle opens at 100
        candles = [_c(102, 100, close=101)]
        # Without initial_previous_close: TR = 102 - 100 = 2
        r_no_gap = atr_series(candles, period=1)
        assert r_no_gap[0] == pytest.approx(2.0)
        # With initial_previous_close=90: TR = max(102,90) - min(100,90) = 12
        r_gap = atr_series(candles, period=1, initial_previous_close=90.0)
        assert r_gap[0] == pytest.approx(12.0)

    def test_initial_previous_close_none_is_default(self):
        candles = _uniform(14)
        r1 = atr_series(candles, 14)
        r2 = atr_series(candles, 14, initial_previous_close=None)
        assert r1 == r2


# ── atr_series validation ────────────────────────────────────────────────────


class TestAtrSeriesValidation:
    def test_period_zero(self):
        with pytest.raises(ValueError, match=">= 1"):
            atr_series(_uniform(5), period=0)

    def test_period_negative(self):
        with pytest.raises(ValueError, match=">= 1"):
            atr_series(_uniform(5), period=-3)

    def test_period_bool(self):
        with pytest.raises(TypeError, match="bool"):
            atr_series(_uniform(5), period=True)

    def test_nan_initial_previous_close(self):
        with pytest.raises(ValueError, match="finite"):
            atr_series(_uniform(5), 14, initial_previous_close=float("nan"))

    def test_bool_initial_previous_close(self):
        with pytest.raises(TypeError, match="bool"):
            atr_series(_uniform(5), 14, initial_previous_close=True)


# ═══════════════════════════════════════════════════════════════════════════════
# previous_atr
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreviousAtrBasic:
    def test_index_0_returns_none(self):
        candles = _uniform(20)
        assert previous_atr(candles, 0) is None

    def test_index_less_than_period_returns_none(self):
        candles = _uniform(20)
        for i in range(14):
            assert previous_atr(candles, i) is None, f"index={i}"

    def test_index_14_returns_atr_of_0_through_13(self):
        candles = _uniform(20, high=10, low=8, close=9)
        result = previous_atr(candles, 14)
        # TR of each = 2.0, mean of 14 = 2.0
        assert result == pytest.approx(2.0)

    def test_index_15_excludes_candle_15(self):
        # 16 candles: first 15 uniform (TR=2), candle[15] has TR=100
        candles = _uniform(15, high=10, low=8, close=9)
        candles.append(_c(200, 100, close=150))
        # previous_atr(16th candle) should use candles[1..14]
        # Actually candles[15] is the 16th. previous_atr(candles, 15)
        # = ATR of candles[1..14] = mean of TR[1..14]
        result = previous_atr(candles, 15)
        # All those TRs are 2.0
        assert result == pytest.approx(2.0)

    def test_candle_at_index_never_affects_result(self):
        """Modifying candle[i] must not change previous_atr(i)."""
        candles = _uniform(20, high=10, low=8, close=9)
        val_before = previous_atr(candles, 14)

        # Replace candle[14] with something wildly different
        candles[14] = _c(500, 1, close=250)
        val_after = previous_atr(candles, 14)

        assert val_before == val_after


class TestPreviousAtrEquivalence:
    def test_matches_atr_series_shifted(self):
        """previous_atr(i) must equal atr_series[i-1]."""
        candles = []
        for i in range(30):
            candles.append(_c(100 + i, 100.0, close=100.0 + i * 0.5))
        series = atr_series(candles, 14)
        for i in range(1, 30):
            pa = previous_atr(candles, i, 14)
            expected = series[i - 1]
            assert pa == expected, (
                f"previous_atr({i})={pa} != atr_series[{i-1}]={expected}"
            )

    def test_with_cache_matches_without(self):
        candles = _uniform(20)
        series = atr_series(candles, 14)
        for i in range(20):
            cached = previous_atr(candles, i, 14, _atr_cache=series)
            direct = previous_atr(candles, i, 14)
            assert cached == direct, f"index={i}"


class TestPreviousAtrWithGap:
    def test_initial_previous_close_propagates(self):
        candles = [_c(102, 100, close=101)] * 20
        # Gap from 50 to 100: TR[0] should be large
        pa_gap = previous_atr(
            candles, 14, period=14, initial_previous_close=50.0
        )
        pa_no = previous_atr(candles, 14, period=14)
        # With gap, TR[0] = max(102,50)-min(100,50) = 52
        # Without gap, TR[0] = 102-100 = 2
        # Both have TR[1..13] = 2 (no gap, prev close=101)
        # pa_gap = mean(52, 2*13) / 14 = (52+26)/14 = 78/14 ≈ 5.571
        assert pa_gap == pytest.approx(78.0 / 14)
        # pa_no = mean(2*14) / 14 = 2.0
        assert pa_no == pytest.approx(2.0)


# ── previous_atr validation ──────────────────────────────────────────────────


class TestPreviousAtrValidation:
    def test_index_negative(self):
        with pytest.raises(ValueError, match=">= 0"):
            previous_atr(_uniform(5), -1)

    def test_index_beyond_length(self):
        with pytest.raises(ValueError, match="< len"):
            previous_atr(_uniform(5), 5)

    def test_index_bool(self):
        with pytest.raises(TypeError, match="bool"):
            previous_atr(_uniform(5), True)

    def test_period_zero(self):
        with pytest.raises(ValueError, match=">= 1"):
            previous_atr(_uniform(5), 0, period=0)

    def test_period_negative(self):
        with pytest.raises(ValueError, match=">= 1"):
            previous_atr(_uniform(5), 0, period=-1)

    def test_period_bool(self):
        with pytest.raises(TypeError, match="bool"):
            previous_atr(_uniform(5), 0, period=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Determinism and edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_identical_inputs_identical_outputs(self):
        candles = _uniform(30)
        r1 = atr_series(candles, 14)
        r2 = atr_series(candles, 14)
        assert r1 == r2

    def test_no_nan_produced(self):
        """ATR never produces NaN or Infinity for valid inputs."""
        candles = _uniform(30)
        for v in atr_series(candles, 14):
            if v is not None:
                assert math.isfinite(v)

    def test_all_zero_range(self):
        """Candles with h==l==close produce ATR = 0."""
        candles = [_c(100, 100, 100) for _ in range(20)]
        result = atr_series(candles, 14)
        assert result[13] == pytest.approx(0.0)
        assert result[19] == pytest.approx(0.0)
