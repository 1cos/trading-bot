"""Tests for confirmation_wick_penetration_pct_min and body-outside-ORB gates.

Covers:
    LONG: body above, wick penetrates 20% → accepted at threshold 20%
    LONG: wick penetrates 19% → rejected
    LONG: wick touches level only → 0%, rejected with threshold >0
    LONG: body inside ORB → rejected
    LONG: open on level, close above → tested
    LONG: no lower wick → rejected
    SHORT: mirror cases
    Metric separation: wick_ratio independent of penetration_pct
    Real case: QQQ 2026-07-27 at 0%/15%/20%/30%
"""

import pytest

from trading_lab.rejection_finder import find_rejection


# ── Shared fixtures ──────────────────────────────────────────────────────────

TICK = 0.01
MS_0930 = 1719828600000
MS_0935 = MS_0930 + 300000
MS_0940 = MS_0930 + 600000
MS_0945 = MS_0930 + 900000
MS_0950 = MS_0930 + 1200000
MS_0955 = MS_0930 + 1500000


def _cfg(direction="LONG", level_source="ORB_HIGH", pen_pct=0.20, **kw):
    base = {
        "timeframe_minutes": 5, "timezone": "America/New_York",
        "session_open": "09:30", "orb_start": "session_open",
        "orb_duration_minutes": 5, "level_source": level_source,
        "direction": direction, "tick_size": TICK,
        "min_displacement_bars": 1,
        "confirmation_wick_penetration_pct_min": pen_pct,
    }
    base.update(kw)
    return base


def _orb(level, high=101.00, low=100.00, direction="LONG", level_source="ORB_HIGH"):
    return {
        "status": "OK", "date": "2026-07-01",
        "orb_high": high, "orb_low": low,
        "orb_high_ticks": round(high / TICK),
        "orb_low_ticks": round(low / TICK),
        "orb_candle_index": 0,
        "orb_candle": {"time_ms": MS_0930, "open": 100.5, "high": high, "low": low, "close": 100.5},
        "level_price": level,
        "level_price_ticks": round(level / TICK),
        "level_source": level_source,
        "direction": direction,
        "orb_low_active": level_source == "ORB_LOW",
    }


def _brk(idx=1):
    return {
        "status": "OK",
        "break_candle_index": idx,
        "break_candle": {"time_ms": MS_0935, "open": 101.5, "high": 102.0, "low": 101.0, "close": 101.8},
    }


def _disp(idx=2):
    return {
        "status": "OK",
        "first_retest_contact_index": idx,
        "first_retest_contact_candle": {"time_ms": MS_0940, "open": 101.0, "high": 101.5, "low": 100.5, "close": 101.2},
        "displacement_distance": {"ticks": 100},
    }


def _rt(idx=2):
    return {
        "status": "OK",
        "retest_window_start_index": idx,
        "retest_window_end_index": idx,
        "retest_start_timestamp": MS_0940,
    }


def _candles_with(candle, idx=2):
    """Build candle array with ORB at 0, break at 1, retest at idx."""
    base = [
        {"time_ms": MS_0930, "open": 100.5, "high": 101.0, "low": 100.0, "close": 100.5},
        {"time_ms": MS_0935, "open": 101.5, "high": 102.0, "low": 101.0, "close": 101.8},
    ]
    while len(base) < idx:
        base.append({"time_ms": MS_0935 + len(base) * 300000,
                      "open": 101.5, "high": 102.0, "low": 101.5, "close": 101.8})
    base.append(candle)
    return base


# ── LONG Tests ───────────────────────────────────────────────────────────────

class TestLongWickPenetration:
    """LONG / ORB_HIGH level = 101.00"""

    def test_body_above_wick_penetrates_20pct_accepted(self):
        """Body fully above, wick penetrates 20% of rejection wick → accepted at 20%."""
        # level=101.00, O=101.30 H=101.50 L=100.50 C=101.40
        # rej_wick = min(10130,10140)-10050 = 80, penetration = 10100-10050 = 50
        # pen_pct = 50/80 = 62.5% → passes 20%
        candle = {"time_ms": MS_0940, "open": 101.30, "high": 101.50, "low": 100.50, "close": 101.40}
        rej = find_rejection(_candles_with(candle), _orb(101.00), _brk(), _disp(), _rt(), _cfg(pen_pct=0.20))
        assert rej["status"] == "OK"
        assert rej["geometry"]["body_outside_orb"] is True
        assert rej["geometry"]["wick_penetration_pct"] >= 0.20

    def test_wick_penetrates_19pct_rejected(self):
        """Wick penetrates only 19% → rejected at 20%."""
        # level=101.00, O=101.10 H=101.50 L=100.90 C=101.40
        # rej_wick = min(10110,10140)-10090 = 20, penetration = 10100-10090 = 10
        # pen_pct = 10/20 = 50% — wait that's 50%. Let me construct 19%.
        # Need: pen/wick < 0.20. rej_wick=100, pen=19 → pen_pct=0.19
        # level=101.00, low=100.81 → pen=19. rej_wick needs to be 100.
        # min(O,C)-low = 100. min(O,C)=101.81. So O=101.81 or C=101.81
        # O=101.81, C=102.00, H=102.20, L=100.81
        # range=139, body=19, rej_wick=100, opp_wick=20
        # wick_ratio=100/139=0.719 ✓, body_ratio=19/139=0.137 ✓
        # fcl=(10200-10081)/139=0.856 ✓
        # pen=10100-10081=19, pen_pct=19/100=0.19 < 0.20 ✗
        candle = {"time_ms": MS_0940, "open": 101.81, "high": 102.20, "low": 100.81, "close": 102.00}
        rej = find_rejection(_candles_with(candle), _orb(101.00), _brk(), _disp(), _rt(), _cfg(pen_pct=0.20))
        assert rej["status"] == "FAILED"
        fr = rej["failed_retests"][0]
        assert "WICK_PENETRATION_PCT_TOO_LOW" in fr["failed_rules"]
        assert fr["geometry"]["wick_penetration_pct"] == pytest.approx(0.19, abs=0.01)

    def test_wick_touches_only_zero_pen(self):
        """Low == level → penetration 0, rejected with threshold >0."""
        # level=101.00, O=101.50 H=102.00 L=101.00 C=101.80
        # pen = 10100-10100 = 0 → pen_pct = 0
        candle = {"time_ms": MS_0940, "open": 101.50, "high": 102.00, "low": 101.00, "close": 101.80}
        rej = find_rejection(_candles_with(candle), _orb(101.00), _brk(), _disp(), _rt(), _cfg(pen_pct=0.10))
        assert rej["status"] == "FAILED"
        assert "WICK_NO_PENETRATION" in rej["failed_retests"][0]["failed_rules"]

    def test_body_inside_orb_rejected(self):
        """Open below level → body inside ORB → rejected."""
        # level=101.00, O=100.80, H=101.80, L=100.50, C=101.50
        # open_ticks=10080 < level_ticks=10100 → body inside
        candle = {"time_ms": MS_0940, "open": 100.80, "high": 101.80, "low": 100.50, "close": 101.50}
        rej = find_rejection(_candles_with(candle), _orb(101.00), _brk(), _disp(), _rt(), _cfg(pen_pct=0.20))
        assert rej["status"] == "FAILED"
        assert "BODY_INSIDE_ORB" in rej["failed_retests"][0]["failed_rules"]

    def test_open_on_level_close_above(self):
        """Open exactly on level, close above → body_outside = True (open >= level)."""
        # level=101.00, O=101.00 H=101.50 L=100.50 C=101.40
        candle = {"time_ms": MS_0940, "open": 101.00, "high": 101.50, "low": 100.50, "close": 101.40}
        rej = find_rejection(_candles_with(candle), _orb(101.00), _brk(), _disp(), _rt(), _cfg(pen_pct=0.20))
        assert rej["status"] == "OK"
        assert rej["geometry"]["body_outside_orb"] is True

    def test_no_lower_wick_rejected(self):
        """Open == Low → no rejection wick → rejected."""
        # level=101.00, O=101.00 H=102.00 L=101.00 C=101.80
        # rej_wick = min(10100,10180)-10100 = 0 → NO_REJECTION_WICK
        # But pen = 10100-10100 = 0 → WICK_NO_PENETRATION too
        candle = {"time_ms": MS_0940, "open": 101.00, "high": 102.00, "low": 101.00, "close": 101.80}
        rej = find_rejection(_candles_with(candle), _orb(101.00), _brk(), _disp(), _rt(), _cfg(pen_pct=0.10))
        assert rej["status"] == "FAILED"
        rules = rej["failed_retests"][0]["failed_rules"]
        assert "WICK_NO_PENETRATION" in rules or "NO_REJECTION_WICK" in rules


# ── SHORT Tests ──────────────────────────────────────────────────────────────

class TestShortWickPenetration:
    """SHORT / ORB_LOW level = 100.00"""

    def _orb_short(self):
        return _orb(100.00, high=101.00, low=100.00, direction="SHORT", level_source="ORB_LOW")

    def _brk_short(self):
        return {
            "status": "OK", "break_candle_index": 1,
            "break_candle": {"time_ms": MS_0935, "open": 99.5, "high": 100.0, "low": 98.0, "close": 98.5},
        }

    def _disp_short(self):
        return {
            "status": "OK", "first_retest_contact_index": 2,
            "first_retest_contact_candle": {"time_ms": MS_0940, "open": 99.0, "high": 100.5, "low": 98.5, "close": 99.2},
            "displacement_distance": {"ticks": 100},
        }

    def test_short_body_below_wick_penetrates_accepted(self):
        """SHORT: body below level, upper wick enters ORB → accepted."""
        # level=100.00, O=99.70 H=100.50 L=99.30 C=99.50
        # rej_wick(upper) = 10050 - max(9970,9950) = 80
        # pen = 10050-10000 = 50, pen_pct = 50/80 = 62.5%
        candle = {"time_ms": MS_0940, "open": 99.70, "high": 100.50, "low": 99.30, "close": 99.50}
        rej = find_rejection(
            _candles_with(candle), self._orb_short(), self._brk_short(),
            self._disp_short(), _rt(),
            _cfg(direction="SHORT", level_source="ORB_LOW", pen_pct=0.20))
        assert rej["status"] == "OK"
        assert rej["geometry"]["body_outside_orb"] is True
        assert rej["geometry"]["wick_penetration_pct"] >= 0.20

    def test_short_body_above_level_rejected(self):
        """SHORT: open above level → body inside ORB → rejected."""
        # level=100.00, O=100.20 H=100.80 L=99.50 C=99.60
        # open_ticks=10020 > level_ticks=10000 → body inside
        candle = {"time_ms": MS_0940, "open": 100.20, "high": 100.80, "low": 99.50, "close": 99.60}
        rej = find_rejection(
            _candles_with(candle), self._orb_short(), self._brk_short(),
            self._disp_short(), _rt(),
            _cfg(direction="SHORT", level_source="ORB_LOW", pen_pct=0.20))
        assert rej["status"] == "FAILED"
        assert "BODY_INSIDE_ORB" in rej["failed_retests"][0]["failed_rules"]

    def test_short_wick_touches_only_rejected(self):
        """SHORT: high == level → penetration 0 → rejected."""
        candle = {"time_ms": MS_0940, "open": 99.50, "high": 100.00, "low": 99.00, "close": 99.20}
        rej = find_rejection(
            _candles_with(candle), self._orb_short(), self._brk_short(),
            self._disp_short(), _rt(),
            _cfg(direction="SHORT", level_source="ORB_LOW", pen_pct=0.10))
        assert rej["status"] == "FAILED"
        assert "WICK_NO_PENETRATION" in rej["failed_retests"][0]["failed_rules"]


# ── Metric Separation ────────────────────────────────────────────────────────

class TestMetricSeparation:
    def test_high_wick_ratio_low_penetration_rejected(self):
        """Wick ratio >47% but penetration <20% → rejected for penetration."""
        # level=101.00, O=101.81 H=102.20 L=100.81 C=102.00
        # wick_ratio = 100/139 = 0.719 (>0.47) ✓
        # pen = 10100-10081 = 19, pen/wick = 19/100 = 0.19 (<0.20) ✗
        candle = {"time_ms": MS_0940, "open": 101.81, "high": 102.20, "low": 100.81, "close": 102.00}
        rej = find_rejection(_candles_with(candle), _orb(101.00), _brk(), _disp(), _rt(), _cfg(pen_pct=0.20))
        assert rej["status"] == "FAILED"
        fr = rej["failed_retests"][0]
        assert "WICK_PENETRATION_PCT_TOO_LOW" in fr["failed_rules"]
        # Wick ratio should still be correct and high
        assert fr["geometry"]["rejection_wick_ratio"] > 0.47

    def test_changing_pen_threshold_doesnt_affect_wick_ratio(self):
        """Same candle evaluated at pen=0 and pen=0.50: wick_ratio unchanged."""
        candle = {"time_ms": MS_0940, "open": 101.30, "high": 101.50, "low": 100.50, "close": 101.40}
        r0 = find_rejection(_candles_with(candle), _orb(101.00), _brk(), _disp(), _rt(), _cfg(pen_pct=0.0))
        r50 = find_rejection(_candles_with(candle), _orb(101.00), _brk(), _disp(), _rt(), _cfg(pen_pct=0.50))
        g0 = r0["geometry"] if r0["status"] == "OK" else r0["failed_retests"][0]["geometry"]
        g50 = r50["geometry"] if r50["status"] == "OK" else r50["failed_retests"][0]["geometry"]
        assert g0["rejection_wick_ratio"] == g50["rejection_wick_ratio"]
        assert g0["body_ratio"] == g50["body_ratio"]


# ── Real Case: QQQ 2026-07-27 SHORT ─────────────────────────────────────────

class TestQQQ20260727:
    def _run_at_threshold(self, pct):
        from trading_lab.timeframe_aggregation import load_candles_for_timeframe
        from trading_lab.session_context import build_session_context
        from trading_lab.orb_builder import build_orb
        from trading_lab.break_finder import find_break
        from trading_lab.displacement_finder import find_displacement
        from trading_lab.retest_window import find_retest_window

        result = load_candles_for_timeframe("dati", "QQQ", 1)
        candles = result["candles_by_date"]["2026-07-27"]
        cfg = {
            "timeframe_minutes": 1, "timezone": "America/New_York",
            "session_open": "09:30", "orb_start": "session_open",
            "orb_duration_minutes": 5, "level_source": "ORB_LOW",
            "direction": "SHORT", "tick_size": 0.01,
            "min_displacement_bars": 3,
            "min_displacement_ticks": None, "min_penetration_ticks": None,
            "min_close_beyond_level_ticks": None,
            "consecutive_orb_closes": 2,
            "confirmation_wick_penetration_pct_min": pct,
        }
        sc = build_session_context(candles, cfg)
        orb = build_orb(sc["candles"], sc, cfg)
        brk = find_break(sc["candles"], orb, cfg)
        disp = find_displacement(sc["candles"], orb, brk, cfg)
        rt = find_retest_window(sc["candles"], orb, brk, disp, cfg)
        return find_rejection(sc["candles"], orb, brk, disp, rt, cfg)

    def test_at_0pct_accepted(self):
        rej = self._run_at_threshold(0.0)
        assert rej["status"] == "OK"

    def test_at_15pct_rejected(self):
        """QQQ pen=3/55=5.5% → fails at 15%."""
        rej = self._run_at_threshold(0.15)
        assert rej["status"] == "FAILED"

    def test_at_20pct_rejected(self):
        rej = self._run_at_threshold(0.20)
        assert rej["status"] == "FAILED"

    def test_at_30pct_rejected(self):
        rej = self._run_at_threshold(0.30)
        assert rej["status"] == "FAILED"

    def test_geometry_values(self):
        """Verify exact geometry at threshold 0."""
        rej = self._run_at_threshold(0.0)
        assert rej["status"] == "OK"
        g = rej["geometry"]
        assert g["rejection_wick_ticks"] == 55
        assert g["penetration_through_level_ticks"] == 3
        assert g["wick_penetration_pct"] == pytest.approx(3 / 55, abs=0.001)
        assert g["body_outside_orb"] is True
