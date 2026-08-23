"""Tests for evaluate_first_rth_entry_candle() — micro-task 33.

Uses evaluate_single_candle_rejection_geometry() (the frozen geometry
extracted in a prior task) directly and unmodified to evaluate the
first RTH contact candle already located by find_first_rth_level_contact().

Cases covered (exactly as specified):
    E1  PDH LONG PASS               -> ENTRY_CANDLE_FOUND / SINGLE_CANDLE_REJECTION
    E2  PDH LONG FAIL                -> CONTACT_FOUND_NO_ENTRY, real reason
    E3  PDL SHORT PASS               -> symmetric
    E4  PDL SHORT FAIL               -> symmetric
    E5  exact candle integrity       -> evaluates contact_candle only
    E6  exact timestamp              -> entry_timestamp_ms matches real contact
    E7  WAITING_FOR_RETEST propagation  -> no geometry call
    E8  NOT_RETEST_READY propagation    -> no geometry call
    E9  PREMARKET_RETEST_ALREADY_SEEN propagation -> no geometry call
    E10 geometry equivalence         -> matches direct utility call
    E11 stateless                    -> no memory between calls
"""

from __future__ import annotations

from unittest.mock import patch

from trading_lab.first_rth_entry_candle import evaluate_first_rth_entry_candle
from trading_lab.rejection_finder import evaluate_single_candle_rejection_geometry


TICK_SIZE = 0.01
PDH = 101.00
PDL = 99.00


def _bar(time_ms, open_, high, low, close):
    return {"time_ms": time_ms, "open": open_, "high": high, "low": low, "close": close}


def _contact_found(direction, level_price, candle, index=2):
    return {
        "status": "CONTACT_FOUND",
        "direction": direction,
        "level_price": level_price,
        "contact_index": index,
        "contact_timestamp_ms": candle["time_ms"],
        "contact_candle": candle,
    }


def _waiting(direction, level_price):
    return {"status": "WAITING_FOR_RETEST", "direction": direction,
            "level_price": level_price, "candles_checked": 3}


def _not_ready(direction, level_price):
    return {"status": "NOT_RETEST_READY", "direction": direction, "level_price": level_price}


def _premarket_already_seen(direction, level_price):
    return {"status": "PREMARKET_RETEST_ALREADY_SEEN", "direction": direction,
            "level_price": level_price}


# ═════════════════════════════════════════════════════════════════════════
# E1 — PDH LONG PASS
# ═════════════════════════════════════════════════════════════════════════

class TestE1PdhLongPass:
    def test_entry_candle_found(self):
        candle = _bar(100, 101.10, 101.30, 100.80, 101.20)  # canonical PASS geometry
        contact_result = _contact_found("LONG", PDH, candle)

        out = evaluate_first_rth_entry_candle(contact_result, "LONG", PDH, TICK_SIZE)

        assert out["status"] == "ENTRY_CANDLE_FOUND"
        assert out["entry_type"] == "SINGLE_CANDLE_REJECTION"
        assert out["direction"] == "LONG"
        assert out["level_price"] == PDH
        assert out["entry_candle"] == candle
        assert out["entry_timestamp_ms"] == 100
        assert out["geometry"]["rejection_wick_ratio"] >= 0.47


# ═════════════════════════════════════════════════════════════════════════
# E2 — PDH LONG FAIL
# ═════════════════════════════════════════════════════════════════════════

class TestE2PdhLongFail:
    def test_contact_found_no_entry(self):
        candle = _bar(100, 100.50, 101.60, 100.40, 101.50)  # canonical FAIL geometry
        contact_result = _contact_found("LONG", PDH, candle)

        out = evaluate_first_rth_entry_candle(contact_result, "LONG", PDH, TICK_SIZE)

        assert out["status"] == "CONTACT_FOUND_NO_ENTRY"
        assert out["direction"] == "LONG"
        assert out["level_price"] == PDH
        assert out["contact_candle"] == candle
        assert "REJECTION_WICK_RATIO_TOO_LOW" in out["failed_rules"]
        assert "BODY_RATIO_TOO_HIGH" in out["failed_rules"]
        assert "entry_candle" not in out
        assert "entry_timestamp_ms" not in out


# ═════════════════════════════════════════════════════════════════════════
# E3 — PDL SHORT PASS
# ═════════════════════════════════════════════════════════════════════════

class TestE3PdlShortPass:
    def test_entry_candle_found(self):
        candle = _bar(100, 98.90, 99.20, 98.70, 98.80)  # canonical SHORT PASS geometry
        contact_result = _contact_found("SHORT", PDL, candle)

        out = evaluate_first_rth_entry_candle(contact_result, "SHORT", PDL, TICK_SIZE)

        assert out["status"] == "ENTRY_CANDLE_FOUND"
        assert out["entry_type"] == "SINGLE_CANDLE_REJECTION"
        assert out["direction"] == "SHORT"
        assert out["entry_candle"] == candle
        assert out["entry_timestamp_ms"] == 100


# ═════════════════════════════════════════════════════════════════════════
# E4 — PDL SHORT FAIL
# ═════════════════════════════════════════════════════════════════════════

class TestE4PdlShortFail:
    def test_contact_found_no_entry(self):
        candle = _bar(100, 99.50, 99.60, 97.90, 98.00)  # canonical SHORT FAIL geometry
        contact_result = _contact_found("SHORT", PDL, candle)

        out = evaluate_first_rth_entry_candle(contact_result, "SHORT", PDL, TICK_SIZE)

        assert out["status"] == "CONTACT_FOUND_NO_ENTRY"
        assert out["contact_candle"] == candle
        assert "REJECTION_WICK_RATIO_TOO_LOW" in out["failed_rules"]
        assert "BODY_RATIO_TOO_HIGH" in out["failed_rules"]


# ═════════════════════════════════════════════════════════════════════════
# E5 — exact candle integrity: only contact_candle is evaluated, never
# a different (earlier/later) candle.
# ═════════════════════════════════════════════════════════════════════════

class TestE5ExactCandleIntegrity:
    def test_only_contact_candle_evaluated(self):
        the_contact_candle = _bar(100, 101.10, 101.30, 100.80, 101.20)
        contact_result = _contact_found("LONG", PDH, the_contact_candle)

        with patch(
            "trading_lab.first_rth_entry_candle.evaluate_single_candle_rejection_geometry",
            wraps=evaluate_single_candle_rejection_geometry,
        ) as spy:
            out = evaluate_first_rth_entry_candle(contact_result, "LONG", PDH, TICK_SIZE)

        spy.assert_called_once()
        called_candle = spy.call_args[0][0]
        assert called_candle is the_contact_candle
        assert out["entry_candle"] is the_contact_candle


# ═════════════════════════════════════════════════════════════════════════
# E6 — exact timestamp: entry_timestamp_ms matches the real contact
# candle's timestamp on PASS.
# ═════════════════════════════════════════════════════════════════════════

class TestE6ExactTimestamp:
    def test_entry_timestamp_matches_contact_candle(self):
        candle = _bar(123456789, 101.10, 101.30, 100.80, 101.20)
        contact_result = _contact_found("LONG", PDH, candle)

        out = evaluate_first_rth_entry_candle(contact_result, "LONG", PDH, TICK_SIZE)

        assert out["status"] == "ENTRY_CANDLE_FOUND"
        assert out["entry_timestamp_ms"] == 123456789
        assert out["entry_timestamp_ms"] == candle["time_ms"]
        assert out["entry_timestamp_ms"] == contact_result["contact_timestamp_ms"]


# ═════════════════════════════════════════════════════════════════════════
# E7/E8/E9 — non-contact statuses propagate untouched; no geometry call.
# ═════════════════════════════════════════════════════════════════════════

class TestE7WaitingForRetestPropagation:
    def test_no_geometry_evaluated(self):
        contact_result = _waiting("LONG", PDH)
        with patch(
            "trading_lab.first_rth_entry_candle.evaluate_single_candle_rejection_geometry",
        ) as spy:
            out = evaluate_first_rth_entry_candle(contact_result, "LONG", PDH, TICK_SIZE)
        spy.assert_not_called()
        assert out["status"] == "WAITING_FOR_RETEST"
        assert "entry_candle" not in out
        assert "contact_candle" not in out


class TestE8NotRetestReadyPropagation:
    def test_no_geometry_evaluated(self):
        contact_result = _not_ready("LONG", PDH)
        with patch(
            "trading_lab.first_rth_entry_candle.evaluate_single_candle_rejection_geometry",
        ) as spy:
            out = evaluate_first_rth_entry_candle(contact_result, "LONG", PDH, TICK_SIZE)
        spy.assert_not_called()
        assert out["status"] == "NOT_RETEST_READY"


class TestE9PremarketRetestAlreadySeenPropagation:
    def test_no_rth_entry_evaluated(self):
        contact_result = _premarket_already_seen("LONG", PDH)
        with patch(
            "trading_lab.first_rth_entry_candle.evaluate_single_candle_rejection_geometry",
        ) as spy:
            out = evaluate_first_rth_entry_candle(contact_result, "LONG", PDH, TICK_SIZE)
        spy.assert_not_called()
        assert out["status"] == "PREMARKET_RETEST_ALREADY_SEEN"
        assert "entry_candle" not in out
        assert "contact_candle" not in out


# ═════════════════════════════════════════════════════════════════════════
# E10 — geometry equivalence: matches calling the utility directly.
# ═════════════════════════════════════════════════════════════════════════

class TestE10GeometryEquivalence:
    def test_pass_geometry_matches_direct_call(self):
        candle = _bar(100, 101.10, 101.30, 100.80, 101.20)
        contact_result = _contact_found("LONG", PDH, candle)

        out = evaluate_first_rth_entry_candle(contact_result, "LONG", PDH, TICK_SIZE)
        direct = evaluate_single_candle_rejection_geometry(candle, "LONG", PDH, TICK_SIZE)

        assert out["geometry"] == direct["geometry"]

    def test_fail_geometry_matches_direct_call(self):
        candle = _bar(100, 100.50, 101.60, 100.40, 101.50)
        contact_result = _contact_found("LONG", PDH, candle)

        out = evaluate_first_rth_entry_candle(contact_result, "LONG", PDH, TICK_SIZE)
        direct = evaluate_single_candle_rejection_geometry(candle, "LONG", PDH, TICK_SIZE)

        assert out["geometry"] == direct["geometry"]
        assert out["failed_rules"] == direct["failed_rules"]

    def test_config_overrides_are_respected_same_as_utility(self):
        candle = _bar(100, 101.10, 101.30, 100.80, 101.20)
        contact_result = _contact_found("LONG", PDH, candle)
        config = {"rejection_wick_ratio_min": 0.99}  # deliberately strict override

        out = evaluate_first_rth_entry_candle(
            contact_result, "LONG", PDH, TICK_SIZE, config=config,
        )
        direct = evaluate_single_candle_rejection_geometry(
            candle, "LONG", PDH, TICK_SIZE, rejection_wick_ratio_min=0.99,
        )

        assert out["status"] == "CONTACT_FOUND_NO_ENTRY"
        assert out["failed_rules"] == direct["failed_rules"]


# ═════════════════════════════════════════════════════════════════════════
# E11 — stateless: no memory between calls.
# ═════════════════════════════════════════════════════════════════════════

class TestE11Stateless:
    def test_no_memory_between_calls(self):
        fail_candle = _bar(100, 100.50, 101.60, 100.40, 101.50)
        fail_contact = _contact_found("LONG", PDH, fail_candle)

        pass_candle = _bar(200, 101.10, 101.30, 100.80, 101.20)
        pass_contact = _contact_found("LONG", PDH, pass_candle)

        result_fail_1 = evaluate_first_rth_entry_candle(fail_contact, "LONG", PDH, TICK_SIZE)
        assert result_fail_1["status"] == "CONTACT_FOUND_NO_ENTRY"

        result_pass = evaluate_first_rth_entry_candle(pass_contact, "LONG", PDH, TICK_SIZE)
        assert result_pass["status"] == "ENTRY_CANDLE_FOUND"

        # Re-evaluating the original FAIL input again must return the
        # exact same FAIL result — nothing was mutated or cached.
        result_fail_2 = evaluate_first_rth_entry_candle(fail_contact, "LONG", PDH, TICK_SIZE)
        assert result_fail_2["status"] == "CONTACT_FOUND_NO_ENTRY"
        assert result_fail_2 == result_fail_1


# ═════════════════════════════════════════════════════════════════════════
# Misc guards
# ═════════════════════════════════════════════════════════════════════════

class TestMiscGuards:
    def test_malformed_contact_result_is_not_retest_ready(self):
        out = evaluate_first_rth_entry_candle({"status": "UNKNOWN"}, "LONG", PDH, TICK_SIZE)
        assert out["status"] == "NOT_RETEST_READY"

    def test_no_config_uses_frozen_defaults(self):
        candle = _bar(100, 101.10, 101.30, 100.80, 101.20)
        contact_result = _contact_found("LONG", PDH, candle)
        out = evaluate_first_rth_entry_candle(contact_result, "LONG", PDH, TICK_SIZE, config=None)
        assert out["status"] == "ENTRY_CANDLE_FOUND"
