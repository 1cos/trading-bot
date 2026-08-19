"""Tests for TWO_CANDLE_ENGULFING_RECOVERY structural tightening.

Closes two structural loopholes identified by forensic audit of the real
2026-08-18 live session (SOFI's false-positive first trade):

    A. STRICT ENGULFING — candle #2's body must actually extend BEYOND
       both edges of candle #1's body (`>` / `<`), not merely touch them
       (`>=` / `<=`).  Exact equality let two near-identical doji
       candles (SOFI: body 17.86-17.87 on both) "engulf" each other.

    B. REAL LEVEL INTERACTION — candle #1 must show non-zero
       penetration through the near-edge level (`high1 > level` for
       SHORT, `low1 < level` for LONG), not merely touch it exactly
       (SOFI candle #1: high == level == 17.87, zero penetration).

No new tunable thresholds were introduced — both checks are exact
boundary conditions, not calibrated minimums.  SINGLE_CANDLE_REJECTION
thresholds, ORB/break/displacement/retest, execution, live_boundary,
setup consumption, and signal_key are all untouched.

Covers:
  1-2. Identical body boundaries (SHORT/LONG) → FAIL (isolates fix A;
       candle #1 still has real level penetration).
  3-4. Strict engulfing + high1/low1 == level (SHORT/LONG) → FAIL
       (isolates fix B; engulfing itself is valid with real margin).
  5-6. Strict engulfing + high1/low1 beyond level (SHORT/LONG) → PASS
       (both checks satisfied — the legitimate case).
  7. SINGLE_CANDLE_REJECTION geometry unaffected (MU real candle).
  8. Real regression: SOFI (must now FAIL TWO_CANDLE), NVDA/QQQ (must
     still PASS), using the exact OHLC values from the forensic replay
     of the real 2026-08-18 session.
"""

from __future__ import annotations

from trading_lab.rejection_finder import find_rejection
from trading_lab.session_context import build_session_context
from trading_lab.orb_builder import build_orb
from trading_lab.break_finder import find_break
from trading_lab.displacement_finder import find_displacement
from trading_lab.retest_window import find_retest_window


# ── Shared config / helpers ──────────────────────────────────────────────────

TICK_SIZE = 0.01

CONFIG_5M = {
    "timeframe_minutes": 5,
    "timezone": "America/New_York",
    "session_open": "09:30",
    "orb_start": "session_open",
    "orb_duration_minutes": 5,
    "level_source": "ORB_HIGH",
    "direction": "LONG",
    "tick_size": TICK_SIZE,
    "min_displacement_ticks": None,
    "min_penetration_ticks": None,
    "min_close_beyond_level_ticks": None,
    "min_displacement_bars": 1,
    "confirmation_wick_penetration_pct_min": 0,
}
SHORT_CONFIG_5M = {**CONFIG_5M, "level_source": "ORB_LOW", "direction": "SHORT"}

CONFIG_1M = {**CONFIG_5M, "timeframe_minutes": 1, "orb_duration_minutes": 1}
SHORT_CONFIG_1M = {**CONFIG_1M, "level_source": "ORB_LOW", "direction": "SHORT"}

# 2026-07-01 09:30 EDT (UTC-4) — arbitrary synthetic session anchor
MS_0930 = 1782912600000


def c(time_ms, open_=100.0, high=100.5, low=99.5, close=100.0):
    return {"time_ms": time_ms, "open": open_, "high": high, "low": low, "close": close}


def run_full(candles_list, config):
    sc = build_session_context(candles_list, config)
    orb = build_orb(sc["candles"], sc, config)
    brk = find_break(sc["candles"], orb, config)
    disp = find_displacement(sc["candles"], orb, brk, config)
    rw = find_retest_window(sc["candles"], orb, brk, disp, config)
    rej = find_rejection(sc["candles"], orb, brk, disp, rw, config)
    return rej


def _long_scaffold(pair, step_ms=300_000, n_padding=15):
    """ORB high=101.00 (level), low=99.00 (far edge). Break + displacement
    above the level, then padding, then the candle pair under test."""
    base = [
        c(MS_0930, high=101.0, low=99.0, close=100.5),
        c(MS_0930 + step_ms, open_=100.50, high=101.60, low=100.30, close=101.30),
        c(MS_0930 + 2 * step_ms, open_=101.30, high=101.70, low=101.20, close=101.40),
    ]
    padding = []
    for j in range(n_padding):
        t = MS_0930 + (3 + j) * step_ms
        padding.append(c(t, open_=106.0, high=111.0, low=101.10, close=105.0))
    return base + padding + pair


def _short_scaffold(pair, step_ms=300_000, n_padding=15):
    """ORB low=99.00 (level), high=101.00 (far edge). Break + displacement
    below the level, then padding, then the candle pair under test."""
    base = [
        c(MS_0930, high=101.0, low=99.0, close=99.5),
        c(MS_0930 + step_ms, open_=99.50, high=99.70, low=98.50, close=98.80),
        c(MS_0930 + 2 * step_ms, open_=98.80, high=98.90, low=98.40, close=98.50),
    ]
    padding = []
    for j in range(n_padding):
        t = MS_0930 + (3 + j) * step_ms
        padding.append(c(t, open_=93.0, high=93.5, low=88.0, close=90.0))
    return base + padding + pair


def _pair_start(n_padding=15, step_ms=300_000):
    """time_ms of the first candle of the pair, given the scaffold above."""
    return MS_0930 + (3 + n_padding) * step_ms


# ═════════════════════════════════════════════════════════════════════════
# 1-2. Identical body boundaries → FAIL (isolates fix A: strict engulfing)
# ═════════════════════════════════════════════════════════════════════════


class TestIdenticalBodyBoundariesRejected:
    def test_short_identical_body_boundaries_fails(self):
        """SHORT: candle #2's body exactly equals candle #1's body.
        Candle #1 DOES have real level penetration (high=101.20 > level
        99.00 far edge... wait: level here is orb_low=99.00), isolating
        the failure to the engulfing equality alone."""
        t1 = _pair_start()
        t2 = t1 + 300_000
        # level (orb_low) = 99.00. candle1 high must be > 99.00 for
        # real penetration (fix B) to pass, isolating fix A.
        c1 = c(t1, open_=100.50, high=101.20, low=99.30, close=99.50)
        # candle2 body EXACTLY equals candle1 body [99.50, 100.50]
        c2 = c(t2, open_=100.50, high=100.60, low=99.40, close=99.50)
        candles = _short_scaffold([c1, c2])
        rej = run_full(candles, SHORT_CONFIG_5M)
        assert rej["status"] == "FAILED" or rej.get("entry_pattern_type") != "TWO_CANDLE_ENGULFING_RECOVERY"
        fr = [f for f in rej.get("failed_retests", [])
              if f.get("two_candle_failed_rules")
              and "TWO_CANDLE_ENGULFING_INSUFFICIENT" in f["two_candle_failed_rules"]]
        assert len(fr) >= 1

    def test_long_identical_body_boundaries_fails(self):
        """LONG mirror: candle #2's body exactly equals candle #1's body,
        with real level penetration on candle #1 (low < level)."""
        t1 = _pair_start()
        t2 = t1 + 300_000
        # level (orb_high) = 101.00. candle1 low must be < 101.00.
        c1 = c(t1, open_=100.50, high=101.70, low=100.30, close=101.50)
        # candle2 body EXACTLY equals candle1 body [100.50, 101.50]
        c2 = c(t2, open_=100.50, high=101.60, low=100.40, close=101.50)
        candles = _long_scaffold([c1, c2])
        rej = run_full(candles, CONFIG_5M)
        assert rej["status"] == "FAILED" or rej.get("entry_pattern_type") != "TWO_CANDLE_ENGULFING_RECOVERY"
        fr = [f for f in rej.get("failed_retests", [])
              if f.get("two_candle_failed_rules")
              and "TWO_CANDLE_ENGULFING_INSUFFICIENT" in f["two_candle_failed_rules"]]
        assert len(fr) >= 1


# ═════════════════════════════════════════════════════════════════════════
# 3-4. Strict engulfing + level EXACTLY touched (no penetration) → FAIL
#      (isolates fix B: real level interaction)
# ═════════════════════════════════════════════════════════════════════════


class TestZeroLevelPenetrationRejected:
    def test_short_high1_equals_level_fails(self):
        """SHORT: candle #2 strictly engulfs candle #1 with real margin,
        but candle #1's high is EXACTLY at the level (zero penetration).
        Candle #1's body_ratio (0.60) and wick_ratio (0.20) both fail
        SINGLE_CANDLE on their own, so TWO_CANDLE is genuinely attempted."""
        t1 = _pair_start()
        t2 = t1 + 300_000
        # level (orb_low) = 99.00. high1 == level exactly.
        c1 = c(t1, open_=98.90, high=99.00, low=98.50, close=98.60)
        # candle2 strictly engulfs candle1's body [98.60, 98.90] with margin
        c2 = c(t2, open_=99.00, high=99.05, low=98.45, close=98.50)
        candles = _short_scaffold([c1, c2])
        rej = run_full(candles, SHORT_CONFIG_5M)
        assert rej["status"] == "FAILED" or rej.get("entry_pattern_type") != "TWO_CANDLE_ENGULFING_RECOVERY"
        fr = [f for f in rej.get("failed_retests", [])
              if f.get("two_candle_failed_rules")
              and "TWO_CANDLE_NO_LEVEL_PENETRATION" in f["two_candle_failed_rules"]]
        assert len(fr) >= 1
        # Confirm engulfing itself was NOT the failure reason (isolation).
        assert "TWO_CANDLE_ENGULFING_INSUFFICIENT" not in fr[0]["two_candle_failed_rules"]

    def test_long_low1_equals_level_fails(self):
        """LONG mirror: candle #1's low is EXACTLY at the level."""
        t1 = _pair_start()
        t2 = t1 + 300_000
        # level (orb_high) = 101.00. low1 == level exactly.
        c1 = c(t1, open_=101.10, high=101.50, low=101.00, close=101.40)
        # candle2 strictly engulfs candle1's body [101.10, 101.40] with margin
        c2 = c(t2, open_=101.00, high=101.55, low=100.95, close=101.50)
        candles = _long_scaffold([c1, c2])
        rej = run_full(candles, CONFIG_5M)
        assert rej["status"] == "FAILED" or rej.get("entry_pattern_type") != "TWO_CANDLE_ENGULFING_RECOVERY"
        fr = [f for f in rej.get("failed_retests", [])
              if f.get("two_candle_failed_rules")
              and "TWO_CANDLE_NO_LEVEL_PENETRATION" in f["two_candle_failed_rules"]]
        assert len(fr) >= 1
        assert "TWO_CANDLE_ENGULFING_INSUFFICIENT" not in fr[0]["two_candle_failed_rules"]


# ═════════════════════════════════════════════════════════════════════════
# 5-6. Strict engulfing + real level penetration → PASS (legitimate case)
# ═════════════════════════════════════════════════════════════════════════


class TestLegitimateTwoCandleStillPasses:
    def test_short_high1_beyond_level_passes(self):
        """Same shape as the zero-penetration case above, except high1 is
        now genuinely beyond the level — both fixes A and B are satisfied.
        Candle #1 still fails SINGLE_CANDLE on its own (body_ratio too
        high), so this exercises the TWO_CANDLE path exactly like NVDA/QQQ."""
        t1 = _pair_start()
        t2 = t1 + 300_000
        # level (orb_low) = 99.00. high1 = 99.05 > level (real penetration).
        c1 = c(t1, open_=98.90, high=99.05, low=98.50, close=98.60)
        c2 = c(t2, open_=99.00, high=99.10, low=98.45, close=98.50)
        candles = _short_scaffold([c1, c2])
        rej = run_full(candles, SHORT_CONFIG_5M)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "TWO_CANDLE_ENGULFING_RECOVERY"
        assert rej["confirmation_candle"]["time_ms"] == t2

    def test_long_low1_beyond_level_passes(self):
        t1 = _pair_start()
        t2 = t1 + 300_000
        # level (orb_high) = 101.00. low1 = 100.95 < level (real penetration).
        c1 = c(t1, open_=101.10, high=101.50, low=100.95, close=101.40)
        c2 = c(t2, open_=101.00, high=101.55, low=100.90, close=101.50)
        candles = _long_scaffold([c1, c2])
        rej = run_full(candles, CONFIG_5M)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "TWO_CANDLE_ENGULFING_RECOVERY"
        assert rej["confirmation_candle"]["time_ms"] == t2


# ═════════════════════════════════════════════════════════════════════════
# 7. SINGLE_CANDLE_REJECTION unaffected (real MU candle)
# ═════════════════════════════════════════════════════════════════════════


class TestSingleCandleUnaffected:
    def test_mu_real_single_candle_still_qualifies(self):
        """MU's real 2026-08-18 entry candle (10:19 ET): a textbook
        SINGLE_CANDLE_REJECTION. Must qualify identically after the
        TWO_CANDLE-only structural tightening (no code path shared)."""
        t1 = _pair_start()
        # Real OHLC from the forensic replay: level=954.01 (orb_low, SHORT)
        mu_candle = c(t1, open_=953.87, high=955.35, low=953.36, close=953.36)
        candles = _short_scaffold([mu_candle], n_padding=15)
        # Rebuild scaffold at MU's real price scale (orb_low=954.01)
        base = [
            c(MS_0930, high=976.85, low=954.01, close=960.0),
            c(MS_0930 + 300_000, open_=960.0, high=961.0, low=956.0, close=956.01),
            c(MS_0930 + 2 * 300_000, open_=956.01, high=956.5, low=951.51, close=952.02),
        ]
        padding = []
        for j in range(15):
            t = MS_0930 + (3 + j) * 300_000
            padding.append(c(t, open_=950.0, high=951.0, low=946.0, close=948.0))
        candles = base + padding + [mu_candle]
        rej = run_full(candles, SHORT_CONFIG_5M)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "SINGLE_CANDLE_REJECTION"
        assert rej["geometry"]["rejection_wick_ratio"] > 0.47
        assert rej["geometry"]["body_ratio"] < 0.40


# ═════════════════════════════════════════════════════════════════════════
# 8. Real regression cases — exact OHLC from the 2026-08-18 forensic replay
# ═════════════════════════════════════════════════════════════════════════


def _real_short_scaffold(orb_high, orb_low, break_close, disp_close, pair,
                          n_padding=6, step_ms=60_000):
    """1-minute scaffold mirroring the real ORB/break/displacement shape,
    at the real price scale, for a SHORT setup. Anchored to the REAL
    2026-08-18 09:30 ET session date so it shares a calendar date with
    the real candle pair appended at the end."""
    anchor = 1787059800000  # 2026-08-18 09:30:00 America/New_York
    base = [
        c(anchor, high=orb_high, low=orb_low, close=(orb_high + orb_low) / 2),
        c(anchor + step_ms, open_=orb_low + 0.02, high=orb_low + 0.03,
          low=break_close - 0.01, close=break_close),
    ]
    padding = []
    for j in range(n_padding):
        t = anchor + (2 + j) * step_ms
        padding.append(c(t, open_=disp_close, high=disp_close + 0.02,
                          low=disp_close - 0.02, close=disp_close))
    return base + padding + pair


class TestRealSessionRegression:
    """Exact OHLC pairs from the forensic replay of 2026-08-18 (SOFI/NVDA/
    QQQ first live trades). SOFI must now fail TWO_CANDLE; NVDA and QQQ
    must continue to pass — the pattern quality that made them legitimate
    (real body expansion + real wick penetration) is untouched by fixes
    A and B."""

    def test_sofi_real_pair_now_fails(self):
        """SOFI 2026-08-18 14:14-14:15 ET. orb_low=17.87 (level).
        Candle 1: O17.87 H17.87 L17.86 C17.86 (zero penetration).
        Candle 2: O17.87 H17.87 L17.85 C17.86 (identical body to candle 1).
        Before this fix: TWO_CANDLE_ENGULFING_RECOVERY (false positive).
        After: must fail both the engulfing-equality and level-penetration
        checks."""
        c1 = c(1787076840000, open_=17.87, high=17.87, low=17.86, close=17.86)
        c2 = c(1787076900000, open_=17.87, high=17.87, low=17.85, close=17.86)
        candles = _real_short_scaffold(
            orb_high=18.08, orb_low=17.87, break_close=17.85, disp_close=17.84,
            pair=[c1, c2],
        )
        rej = run_full(candles, SHORT_CONFIG_1M)
        assert rej["status"] == "FAILED" or rej.get("entry_pattern_type") != "TWO_CANDLE_ENGULFING_RECOVERY"
        fr = [f for f in rej.get("failed_retests", [])
              if f.get("two_candle_failed_rules")]
        assert fr, "expected candle 1 to be recorded with two_candle_failed_rules"
        rules = fr[0]["two_candle_failed_rules"]
        # Candle #1's own checks (body-traversal, level-penetration) are
        # evaluated BEFORE candle #2 is even looked at, so a level-
        # penetration failure short-circuits before engulfing is checked.
        # SOFI's candle #1 (high == level exactly) fails on level
        # penetration; separately verified (test_short_identical_body_
        # boundaries_fails / capture_geometry.py replay) that its body
        # edges are also identical to candle #2's, so strict engulfing
        # would equally have failed had it been reached.
        assert "TWO_CANDLE_NO_LEVEL_PENETRATION" in rules

    def test_sofi_real_body_edges_alone_fail_engulfing(self):
        """Isolates fix A on SOFI's exact real body values: even if candle
        #1's high is bumped just 1 tick above the level (satisfying fix B
        without being large enough to independently qualify as its own
        SINGLE_CANDLE_REJECTION), the identical body edges (17.87/17.86
        on both candles, candle #2 unmodified from the real data) still
        fail strict engulfing on their own."""
        c1 = c(1787076840000, open_=17.87, high=17.88, low=17.86, close=17.86)
        c2 = c(1787076900000, open_=17.87, high=17.87, low=17.85, close=17.86)
        candles = _real_short_scaffold(
            orb_high=18.08, orb_low=17.87, break_close=17.85, disp_close=17.84,
            pair=[c1, c2],
        )
        rej = run_full(candles, SHORT_CONFIG_1M)
        assert rej["status"] == "FAILED" or rej.get("entry_pattern_type") != "TWO_CANDLE_ENGULFING_RECOVERY"
        fr = [f for f in rej.get("failed_retests", [])
              if f.get("two_candle_failed_rules")]
        assert fr
        assert "TWO_CANDLE_ENGULFING_INSUFFICIENT" in fr[0]["two_candle_failed_rules"]
        assert "TWO_CANDLE_NO_LEVEL_PENETRATION" not in fr[0]["two_candle_failed_rules"]

    def test_nvda_real_pair_still_passes(self):
        """NVDA 2026-08-18 10:20-10:21 ET. orb_low=219.43 (level).
        Candle 1: O219.24 H219.48 L219.14 C219.35 (5-tick penetration).
        Candle 2: O219.38 H219.44 L218.99 C219.11 (strict engulf, real
        margin). Must remain a legitimate TWO_CANDLE pass."""
        c1 = c(1787062800000, open_=219.24, high=219.48, low=219.14, close=219.35)
        c2 = c(1787062860000, open_=219.38, high=219.44, low=218.99, close=219.11)
        candles = _real_short_scaffold(
            orb_high=220.68, orb_low=219.43, break_close=219.16, disp_close=219.20,
            pair=[c1, c2],
        )
        rej = run_full(candles, SHORT_CONFIG_1M)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "TWO_CANDLE_ENGULFING_RECOVERY"
        assert rej["confirmation_candle"]["close"] == 219.11

    def test_qqq_real_pair_still_passes(self):
        """QQQ 2026-08-18 10:20-10:21 ET. orb_low=718.79 (level).
        Candle 1: O718.47 H718.99 L718.36 C718.89 (20-tick penetration).
        Candle 2: O718.91 H719.07 L717.98 C717.99 (strict engulf, large
        real margin). Must remain a legitimate TWO_CANDLE pass."""
        c1 = c(1787062800000, open_=718.47, high=718.99, low=718.36, close=718.89)
        c2 = c(1787062860000, open_=718.91, high=719.07, low=717.98, close=717.99)
        candles = _real_short_scaffold(
            orb_high=721.77, orb_low=718.79, break_close=718.50, disp_close=717.80,
            pair=[c1, c2],
        )
        rej = run_full(candles, SHORT_CONFIG_1M)
        assert rej["status"] == "OK"
        assert rej["entry_pattern_type"] == "TWO_CANDLE_ENGULFING_RECOVERY"
        assert rej["confirmation_candle"]["close"] == 717.99
