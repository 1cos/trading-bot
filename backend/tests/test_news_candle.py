"""Tests for News Candle classification (B3).

Covers spec §9.3 classification bands, boundary behavior,
insufficient history, ATR zero, validation, immutability,
serialization, and configurable threshold.
"""

import math

import pytest

from trading_lab.contracts.enums import CandleAtrStatus
from trading_lab.news_candle import CandleAtrClassification, classify_candle_atr


# ── Helpers ───────────────────────────────────────────────────────────────────


def _c(high, low):
    """Minimal candle dict."""
    return {"time_ms": 0, "open": high, "high": high, "low": low, "close": low}


# ═══════════════════════════════════════════════════════════════════════════════
# Classification with default threshold (3.0)
# ═══════════════════════════════════════════════════════════════════════════════


class TestClassificationDefault:
    def test_normal_ratio_1(self):
        r = classify_candle_atr(_c(11, 10), prev_atr=1.0)
        assert r.status == CandleAtrStatus.NORMAL
        assert r.ratio == pytest.approx(1.0)

    def test_normal_ratio_2_exactly(self):
        """ratio == 2.0 → NORMAL (≤ 2.0)."""
        r = classify_candle_atr(_c(12, 10), prev_atr=1.0)
        assert r.status == CandleAtrStatus.NORMAL
        assert r.ratio == pytest.approx(2.0)

    def test_large_ratio_2_5(self):
        r = classify_candle_atr(_c(12.5, 10), prev_atr=1.0)
        assert r.status == CandleAtrStatus.LARGE
        assert r.ratio == pytest.approx(2.5)

    def test_large_ratio_3_exactly(self):
        """ratio == 3.0 → LARGE (not > 3.0)."""
        r = classify_candle_atr(_c(13, 10), prev_atr=1.0)
        assert r.status == CandleAtrStatus.LARGE
        assert r.ratio == pytest.approx(3.0)

    def test_news_candle_ratio_3_01(self):
        r = classify_candle_atr(_c(13.01, 10), prev_atr=1.0)
        assert r.status == CandleAtrStatus.NEWS_CANDLE
        assert r.ratio == pytest.approx(3.01)

    def test_news_candle_ratio_6(self):
        r = classify_candle_atr(_c(16, 10), prev_atr=1.0)
        assert r.status == CandleAtrStatus.NEWS_CANDLE
        assert r.ratio == pytest.approx(6.0)

    def test_doji_zero_range(self):
        """Doji with ATR > 0 → NORMAL with ratio 0.0."""
        r = classify_candle_atr(_c(10, 10), prev_atr=1.0)
        assert r.status == CandleAtrStatus.NORMAL
        assert r.ratio == pytest.approx(0.0)
        assert r.candle_range == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Classification with custom threshold
# ═══════════════════════════════════════════════════════════════════════════════


class TestClassificationCustomThreshold:
    def test_threshold_2_ratio_2_normal(self):
        """news_threshold=2.0, ratio=2.0 → NORMAL."""
        r = classify_candle_atr(_c(12, 10), prev_atr=1.0, news_threshold=2.0)
        assert r.status == CandleAtrStatus.NORMAL

    def test_threshold_2_ratio_just_above(self):
        """news_threshold=2.0, ratio just above 2.0 → NEWS_CANDLE."""
        r = classify_candle_atr(_c(12.001, 10), prev_atr=1.0, news_threshold=2.0)
        assert r.status == CandleAtrStatus.NEWS_CANDLE

    def test_threshold_2_5_ratio_2_normal(self):
        """news_threshold=2.5, ratio=2.0 → NORMAL."""
        r = classify_candle_atr(_c(12, 10), prev_atr=1.0, news_threshold=2.5)
        assert r.status == CandleAtrStatus.NORMAL

    def test_threshold_2_5_ratio_2_5_large(self):
        """news_threshold=2.5, ratio=2.5 → LARGE (not > 2.5)."""
        r = classify_candle_atr(_c(12.5, 10), prev_atr=1.0, news_threshold=2.5)
        assert r.status == CandleAtrStatus.LARGE

    def test_threshold_2_5_ratio_just_above(self):
        """news_threshold=2.5, ratio just above 2.5 → NEWS_CANDLE."""
        r = classify_candle_atr(_c(12.501, 10), prev_atr=1.0, news_threshold=2.5)
        assert r.status == CandleAtrStatus.NEWS_CANDLE

    def test_threshold_stored_in_result(self):
        r = classify_candle_atr(_c(11, 10), prev_atr=1.0, news_threshold=2.5)
        assert r.news_threshold == 2.5


# ═══════════════════════════════════════════════════════════════════════════════
# Insufficient history and ATR zero
# ═══════════════════════════════════════════════════════════════════════════════


class TestInsufficientHistory:
    def test_prev_atr_none(self):
        r = classify_candle_atr(_c(12, 10), prev_atr=None)
        assert r.status == CandleAtrStatus.INSUFFICIENT_HISTORY
        assert r.previous_atr is None
        assert r.ratio is None
        assert r.candle_range == pytest.approx(2.0)
        assert r.news_threshold == 3.0


class TestAtrZero:
    def test_prev_atr_zero(self):
        r = classify_candle_atr(_c(12, 10), prev_atr=0.0)
        assert r.status == CandleAtrStatus.ATR_ZERO
        assert r.previous_atr == 0.0
        assert r.ratio is None
        assert r.candle_range == pytest.approx(2.0)

    def test_doji_with_atr_zero(self):
        r = classify_candle_atr(_c(10, 10), prev_atr=0.0)
        assert r.status == CandleAtrStatus.ATR_ZERO
        assert r.candle_range == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Result fields and serialization
# ═══════════════════════════════════════════════════════════════════════════════


class TestResultFields:
    def test_all_fields_populated_normal(self):
        r = classify_candle_atr(_c(11, 10), prev_atr=2.0)
        assert r.status == CandleAtrStatus.NORMAL
        assert r.candle_range == pytest.approx(1.0)
        assert r.previous_atr == pytest.approx(2.0)
        assert r.ratio == pytest.approx(0.5)
        assert r.news_threshold == 3.0

    def test_all_fields_populated_news(self):
        r = classify_candle_atr(_c(17, 10), prev_atr=2.0)
        assert r.status == CandleAtrStatus.NEWS_CANDLE
        assert r.candle_range == pytest.approx(7.0)
        assert r.previous_atr == pytest.approx(2.0)
        assert r.ratio == pytest.approx(3.5)


class TestSerialization:
    def test_to_dict_normal(self):
        r = classify_candle_atr(_c(11, 10), prev_atr=2.0)
        d = r.to_dict()
        assert d == {
            "status": "NORMAL",
            "candle_range": pytest.approx(1.0),
            "previous_atr": pytest.approx(2.0),
            "ratio": pytest.approx(0.5),
            "news_threshold": 3.0,
        }

    def test_to_dict_insufficient(self):
        d = classify_candle_atr(_c(11, 10), prev_atr=None).to_dict()
        assert d["status"] == "INSUFFICIENT_HISTORY"
        assert d["previous_atr"] is None
        assert d["ratio"] is None
        assert isinstance(d["candle_range"], float)

    def test_to_dict_atr_zero(self):
        d = classify_candle_atr(_c(11, 10), prev_atr=0.0).to_dict()
        assert d["status"] == "ATR_ZERO"
        assert d["previous_atr"] == 0.0
        assert d["ratio"] is None

    def test_status_serialized_as_string(self):
        d = classify_candle_atr(_c(11, 10), prev_atr=1.0).to_dict()
        assert isinstance(d["status"], str)


class TestImmutability:
    def test_frozen(self):
        r = classify_candle_atr(_c(11, 10), prev_atr=1.0)
        with pytest.raises(AttributeError):
            r.status = CandleAtrStatus.NEWS_CANDLE

    def test_frozen_ratio(self):
        r = classify_candle_atr(_c(11, 10), prev_atr=1.0)
        with pytest.raises(AttributeError):
            r.ratio = 999.0


# ═══════════════════════════════════════════════════════════════════════════════
# Input validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestThresholdValidation:
    def test_threshold_below_2(self):
        with pytest.raises(ValueError, match=">= 2.0"):
            classify_candle_atr(_c(11, 10), prev_atr=1.0, news_threshold=1.9)

    def test_threshold_zero(self):
        with pytest.raises(ValueError, match=">= 2.0"):
            classify_candle_atr(_c(11, 10), prev_atr=1.0, news_threshold=0.0)

    def test_threshold_negative(self):
        with pytest.raises(ValueError, match=">= 2.0"):
            classify_candle_atr(_c(11, 10), prev_atr=1.0, news_threshold=-1.0)

    def test_threshold_nan(self):
        with pytest.raises(ValueError, match="finite"):
            classify_candle_atr(_c(11, 10), prev_atr=1.0, news_threshold=float("nan"))

    def test_threshold_inf(self):
        with pytest.raises(ValueError, match="finite"):
            classify_candle_atr(_c(11, 10), prev_atr=1.0, news_threshold=float("inf"))

    def test_threshold_bool(self):
        with pytest.raises(TypeError, match="bool"):
            classify_candle_atr(_c(11, 10), prev_atr=1.0, news_threshold=True)


class TestPrevAtrValidation:
    def test_prev_atr_negative(self):
        with pytest.raises(ValueError, match=">= 0"):
            classify_candle_atr(_c(11, 10), prev_atr=-1.0)

    def test_prev_atr_nan(self):
        with pytest.raises(ValueError, match="finite"):
            classify_candle_atr(_c(11, 10), prev_atr=float("nan"))

    def test_prev_atr_inf(self):
        with pytest.raises(ValueError, match="finite"):
            classify_candle_atr(_c(11, 10), prev_atr=float("inf"))

    def test_prev_atr_bool(self):
        with pytest.raises(TypeError, match="bool"):
            classify_candle_atr(_c(11, 10), prev_atr=True)

    def test_prev_atr_string(self):
        with pytest.raises(TypeError, match="number"):
            classify_candle_atr(_c(11, 10), prev_atr="2.0")


class TestCandleValidation:
    def test_missing_high(self):
        with pytest.raises(KeyError, match="high"):
            classify_candle_atr({"low": 10.0}, prev_atr=1.0)

    def test_missing_low(self):
        with pytest.raises(KeyError, match="low"):
            classify_candle_atr({"high": 11.0}, prev_atr=1.0)

    def test_high_less_than_low(self):
        with pytest.raises(ValueError, match="high.*<.*low"):
            classify_candle_atr(_c(5, 10), prev_atr=1.0)

    def test_bool_high(self):
        with pytest.raises(TypeError, match="bool"):
            classify_candle_atr({"high": True, "low": 0.0}, prev_atr=1.0)

    def test_bool_low(self):
        with pytest.raises(TypeError, match="bool"):
            classify_candle_atr({"high": 10.0, "low": False}, prev_atr=1.0)

    def test_nan_high(self):
        with pytest.raises(ValueError, match="finite"):
            classify_candle_atr({"high": float("nan"), "low": 10.0}, prev_atr=1.0)

    def test_inf_low(self):
        with pytest.raises(ValueError, match="finite"):
            classify_candle_atr({"high": 10.0, "low": float("-inf")}, prev_atr=1.0)

    def test_integer_prices_accepted(self):
        r = classify_candle_atr(_c(11, 10), prev_atr=1)
        assert r.status == CandleAtrStatus.NORMAL


# ═══════════════════════════════════════════════════════════════════════════════
# Result invariant validation (direct construction)
# ═══════════════════════════════════════════════════════════════════════════════


class TestResultInvariants:
    def test_normal_requires_positive_atr(self):
        with pytest.raises(ValueError, match="positive"):
            CandleAtrClassification(
                status=CandleAtrStatus.NORMAL,
                candle_range=1.0, previous_atr=None,
                ratio=0.5, news_threshold=3.0,
            )

    def test_news_candle_requires_ratio(self):
        with pytest.raises(ValueError, match="ratio"):
            CandleAtrClassification(
                status=CandleAtrStatus.NEWS_CANDLE,
                candle_range=5.0, previous_atr=1.0,
                ratio=None, news_threshold=3.0,
            )

    def test_insufficient_requires_none_atr(self):
        with pytest.raises(ValueError, match="None"):
            CandleAtrClassification(
                status=CandleAtrStatus.INSUFFICIENT_HISTORY,
                candle_range=1.0, previous_atr=1.0,
                ratio=None, news_threshold=3.0,
            )

    def test_atr_zero_requires_zero_atr(self):
        with pytest.raises(ValueError, match="0.0"):
            CandleAtrClassification(
                status=CandleAtrStatus.ATR_ZERO,
                candle_range=1.0, previous_atr=1.0,
                ratio=None, news_threshold=3.0,
            )

    def test_negative_candle_range(self):
        with pytest.raises(ValueError, match=">= 0"):
            CandleAtrClassification(
                status=CandleAtrStatus.NORMAL,
                candle_range=-1.0, previous_atr=1.0,
                ratio=0.5, news_threshold=3.0,
            )

    def test_threshold_below_2_in_result(self):
        with pytest.raises(ValueError, match=">= 2.0"):
            CandleAtrClassification(
                status=CandleAtrStatus.NORMAL,
                candle_range=1.0, previous_atr=1.0,
                ratio=0.5, news_threshold=1.5,
            )
