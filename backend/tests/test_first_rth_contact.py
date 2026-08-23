"""Tests for find_first_rth_level_contact() — micro-task 28.

Pure predicate: for a PDH/PDL structure already known to be
RETEST_READY (from either premarket_observed_structure.py or
carry_in_separation.py), recognizes the first RTH candle that
contacts the level from the correct side. No break chain, no
rejection geometry, no entry candle, no second-retest policy.

Cases covered (exactly as specified):
    RTH1 PDH LONG contact       -> first contact identified
    RTH2 PDH LONG no contact    -> WAITING_FOR_RETEST
    RTH3 PDL SHORT contact      -> first contact identified
    RTH4 PDL SHORT no contact   -> WAITING_FOR_RETEST
    RTH5 first means first      -> only the earliest contact returned
    RTH6 not ready               -> NOT_RETEST_READY
    RTH7 timestamp integrity    -> output timestamp is the real candle's
    RTH8 stateless               -> no memory between calls
"""

from __future__ import annotations

from trading_lab.first_rth_contact import find_first_rth_level_contact


def _bar(time_ms, close, open_=None, high=None, low=None, volume=100):
    return {
        "time_ms": time_ms,
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": volume,
    }


PDH = 105.00
PDL = 95.00


# ═════════════════════════════════════════════════════════════════════════
# RTH1 — PDH LONG contact: ready, premarket bars stay above PDH, then
# a real low <= PDH candle appears.
# ═════════════════════════════════════════════════════════════════════════

class TestRTH1PdhLongContact:
    def test_first_contact_identified(self):
        bars = [
            _bar(1, close=105.80, low=105.50, high=106.00),  # above PDH
            _bar(2, close=105.60, low=105.30, high=105.90),  # above PDH
            _bar(3, close=105.10, low=104.80, high=105.30),  # contact: low <= PDH
            _bar(4, close=105.50, low=105.10, high=105.60),  # irrelevant, after
        ]
        out = find_first_rth_level_contact(bars, "LONG", PDH, retest_ready=True)
        assert out["status"] == "CONTACT_FOUND"
        assert out["contact_index"] == 2
        assert out["contact_timestamp_ms"] == 3
        assert out["contact_candle"]["low"] == 104.80
        assert out["direction"] == "LONG"
        assert out["level_price"] == PDH


# ═════════════════════════════════════════════════════════════════════════
# RTH2 — PDH LONG no contact yet: all bars strictly above PDH.
# ═════════════════════════════════════════════════════════════════════════

class TestRTH2PdhLongNoContactYet:
    def test_waiting_for_retest(self):
        bars = [
            _bar(1, close=105.80, low=105.50, high=106.00),
            _bar(2, close=105.60, low=105.30, high=105.90),
            _bar(3, close=105.90, low=105.40, high=106.10),
        ]
        out = find_first_rth_level_contact(bars, "LONG", PDH, retest_ready=True)
        assert out["status"] == "WAITING_FOR_RETEST"
        assert out["candles_checked"] == 3


# ═════════════════════════════════════════════════════════════════════════
# RTH3 — PDL SHORT contact: symmetric.
# ═════════════════════════════════════════════════════════════════════════

class TestRTH3PdlShortContact:
    def test_first_contact_identified(self):
        bars = [
            _bar(1, close=94.20, low=93.90, high=94.50),   # below PDL
            _bar(2, close=94.40, low=94.00, high=94.70),   # below PDL
            _bar(3, close=94.90, low=94.60, high=95.20),   # contact: high >= PDL
            _bar(4, close=94.50, low=94.20, high=94.80),   # irrelevant, after
        ]
        out = find_first_rth_level_contact(bars, "SHORT", PDL, retest_ready=True)
        assert out["status"] == "CONTACT_FOUND"
        assert out["contact_index"] == 2
        assert out["contact_timestamp_ms"] == 3
        assert out["contact_candle"]["high"] == 95.20


# ═════════════════════════════════════════════════════════════════════════
# RTH4 — PDL SHORT no contact
# ═════════════════════════════════════════════════════════════════════════

class TestRTH4PdlShortNoContact:
    def test_waiting_for_retest(self):
        bars = [
            _bar(1, close=94.20, low=93.90, high=94.50),
            _bar(2, close=94.40, low=94.00, high=94.70),
        ]
        out = find_first_rth_level_contact(bars, "SHORT", PDL, retest_ready=True)
        assert out["status"] == "WAITING_FOR_RETEST"
        assert out["candles_checked"] == 2


# ═════════════════════════════════════════════════════════════════════════
# RTH5 — first means first: multiple contact bars, only the earliest
# is returned.
# ═════════════════════════════════════════════════════════════════════════

class TestRTH5FirstMeansFirst:
    def test_only_earliest_contact_returned(self):
        bars = [
            _bar(1, close=105.80, low=105.50, high=106.00),  # above PDH
            _bar(2, close=105.10, low=104.90, high=105.30),  # contact #1
            _bar(3, close=104.80, low=104.50, high=105.00),  # contact #2 (also low<=PDH)
            _bar(4, close=104.70, low=104.40, high=104.90),  # contact #3
        ]
        out = find_first_rth_level_contact(bars, "LONG", PDH, retest_ready=True)
        assert out["status"] == "CONTACT_FOUND"
        assert out["contact_index"] == 1
        assert out["contact_timestamp_ms"] == 2
        assert out["contact_candle"]["low"] == 104.90


# ═════════════════════════════════════════════════════════════════════════
# RTH6 — not ready: retest_ready=False -> NOT_RETEST_READY, nothing scanned.
# ═════════════════════════════════════════════════════════════════════════

class TestRTH6NotReady:
    def test_not_retest_ready(self):
        bars = [
            _bar(1, close=105.10, low=104.80, high=105.30),  # would be a contact
        ]
        out = find_first_rth_level_contact(bars, "LONG", PDH, retest_ready=False)
        assert out["status"] == "NOT_RETEST_READY"
        assert "contact_index" not in out
        assert "contact_timestamp_ms" not in out


# ═════════════════════════════════════════════════════════════════════════
# RTH7 — timestamp integrity: output timestamp belongs to the real
# identified candle.
# ═════════════════════════════════════════════════════════════════════════

class TestRTH7TimestampIntegrity:
    def test_timestamp_matches_real_candle(self):
        bars = [
            _bar(1, close=105.80, low=105.50, high=106.00),
            _bar(2, close=105.10, low=104.80, high=105.30),
        ]
        out = find_first_rth_level_contact(bars, "LONG", PDH, retest_ready=True)
        assert out["status"] == "CONTACT_FOUND"
        assert out["contact_timestamp_ms"] == out["contact_candle"]["time_ms"]
        real_timestamps = {b["time_ms"] for b in bars}
        assert out["contact_timestamp_ms"] in real_timestamps


# ═════════════════════════════════════════════════════════════════════════
# RTH8 — stateless: no memory between calls.
# ═════════════════════════════════════════════════════════════════════════

class TestRTH8Stateless:
    def test_no_memory_between_calls(self):
        no_contact_bars = [
            _bar(1, close=105.80, low=105.50, high=106.00),
            _bar(2, close=105.60, low=105.30, high=105.90),
        ]
        with_contact_bars = no_contact_bars + [
            _bar(3, close=105.10, low=104.80, high=105.30),
        ]

        result_waiting = find_first_rth_level_contact(no_contact_bars, "LONG", PDH, retest_ready=True)
        assert result_waiting["status"] == "WAITING_FOR_RETEST"

        result_found = find_first_rth_level_contact(with_contact_bars, "LONG", PDH, retest_ready=True)
        assert result_found["status"] == "CONTACT_FOUND"

        # Re-evaluating the original (shorter) history again must
        # still return WAITING — nothing was mutated or cached.
        result_waiting_again = find_first_rth_level_contact(no_contact_bars, "LONG", PDH, retest_ready=True)
        assert result_waiting_again["status"] == "WAITING_FOR_RETEST"
        assert result_waiting_again == result_waiting


# ═════════════════════════════════════════════════════════════════════════
# premarket_retest_already_seen=True: neutral status, no RTH search.
# ═════════════════════════════════════════════════════════════════════════

class TestPremarketRetestAlreadySeen:
    def test_neutral_status_no_second_retest_policy(self):
        bars = [
            _bar(1, close=105.10, low=104.80, high=105.30),  # would be a contact
        ]
        out = find_first_rth_level_contact(
            bars, "LONG", PDH, retest_ready=True,
            premarket_retest_already_seen=True,
        )
        assert out["status"] == "PREMARKET_RETEST_ALREADY_SEEN"
        assert "contact_index" not in out
        assert "contact_timestamp_ms" not in out

    def test_takes_precedence_over_not_ready_check_order_is_still_correct(self):
        """Sanity: not_ready still wins if BOTH are somehow false/true
        in a way that would be contradictory — retest_ready False
        always yields NOT_RETEST_READY regardless of the premarket flag."""
        out = find_first_rth_level_contact(
            [], "LONG", PDH, retest_ready=False,
            premarket_retest_already_seen=True,
        )
        assert out["status"] == "NOT_RETEST_READY"


# ═════════════════════════════════════════════════════════════════════════
# Misc guards
# ═════════════════════════════════════════════════════════════════════════

class TestMiscGuards:
    def test_no_rth_candles_yet_is_waiting(self):
        out = find_first_rth_level_contact(None, "LONG", PDH, retest_ready=True)
        assert out["status"] == "WAITING_FOR_RETEST"
        assert out["candles_checked"] == 0

    def test_unsupported_direction(self):
        out = find_first_rth_level_contact(
            [_bar(1, close=200.0)], "SIDEWAYS", PDH, retest_ready=True,
        )
        assert out["status"] == "NOT_RETEST_READY"

    def test_never_mutates_input_list(self):
        bars = [
            _bar(3, close=105.10, low=104.80, high=105.30),
            _bar(1, close=105.80, low=105.50, high=106.00),
            _bar(2, close=105.60, low=105.30, high=105.90),
        ]
        original_order = list(bars)
        find_first_rth_level_contact(bars, "LONG", PDH, retest_ready=True)
        assert bars == original_order

    def test_works_regardless_of_which_upstream_evaluator_produced_ready(self):
        """This function does not care whether retest_ready=True came
        from PREMARKET_OBSERVED or PREMARKET_CARRY_IN — it only
        consumes the boolean."""
        bars = [_bar(1, close=105.10, low=104.80, high=105.30)]
        out_observed_origin = find_first_rth_level_contact(bars, "LONG", PDH, retest_ready=True)
        out_carry_in_origin = find_first_rth_level_contact(bars, "LONG", PDH, retest_ready=True)
        assert out_observed_origin == out_carry_in_origin
        assert out_observed_origin["status"] == "CONTACT_FOUND"
