"""Post-exit underlying observation — the R-multiple probe.

The question a closed trade record cannot answer today: a trade exited
at its 2R target, but would it also have reached 3R, or 4R? Once the
position closes the orchestrator stops watching that symbol, so the path
that would answer it is never recorded and dies with the process.

These tests pin the probe's two hard requirements:

  1. it keeps observing after the real trade is CLOSED, to session end;
  2. it is purely observational — no orders, no lifecycle, no change to
     the trade's real outcome, and no interference with a second trade
     on the same symbol.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from trading_lab.live.r_probe import R_LEVELS, RProbe
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector
from trading_lab.live.trade_manager import DailyTradeManager
from trading_lab.live.trade_orchestrator import (
    LifecycleState,
    MaxBotTradeOrchestrator,
)
from trading_lab.live.trade_state_store import persist_r_probe


MS_0930 = 1786455000000


def _ms(minute: int) -> int:
    return MS_0930 + minute * 60_000


def _bar(minute: int, high: float, low: float, close: float | None = None) -> dict:
    return {"time_ms": _ms(minute), "open": low, "high": high,
            "low": low, "close": close if close is not None else high,
            "volume": 1000}


def _probe(direction="LONG", entry=100.0, stop=99.0, target=102.0,
           entry_min=5, fill_ms=None):
    """Default: filled at the close of the entry candle, i.e. the very
    start of the next minute — the ordinary entry-at-close case."""
    return RProbe.create(
        trade_id="SPY_LONG_ORB_HIGH_1", symbol="SPY", direction=direction,
        entry_price=entry, stop_price=stop, target_price=target,
        entry_timestamp_ms=_ms(entry_min), fill_timestamp_ms=fill_ms,
    )


# ═════════════════════════════════════════════════════════════════════
# The core question: observation continues past the real exit
# ═════════════════════════════════════════════════════════════════════


class TestObservationContinuesPastExit:
    def test_trade_closed_at_2r_then_3r_is_still_recorded(self):
        """The whole point. The real trade ends at 2R (minute 6); the
        probe must still see 3R arrive at minute 9."""
        p = _probe()
        p.observe(_bar(6, high=102.0, low=100.0))    # 2R — real exit here
        p.observe(_bar(7, high=102.2, low=101.0))
        p.observe(_bar(8, high=102.6, low=101.5))
        p.observe(_bar(9, high=103.0, low=102.0))    # 3R, long after the exit

        assert p.first_touch["2r"] == _ms(6)
        assert p.first_touch["3r"] == _ms(9), (
            "3R was reached after the trade closed and must still be recorded"
        )
        assert p.mfe_r == pytest.approx(3.0)

    def test_4r_not_reached_is_reported_as_absent(self):
        p = _probe()
        p.observe(_bar(6, high=102.0, low=100.0))
        p.observe(_bar(9, high=103.0, low=102.0))
        assert "3r" in p.first_touch
        assert "3_5r" not in p.first_touch
        assert "4r" not in p.first_touch

    def test_every_level_is_reported(self):
        p = _probe()
        p.observe(_bar(6, high=105.0, low=100.0))    # 5R in one bar
        for m in R_LEVELS:
            key = f"{m:g}".replace(".", "_") + "r"
            assert key in p.first_touch, key

    def test_mfe_and_mae_are_tracked_independently(self):
        p = _probe()
        p.observe(_bar(6, high=103.0, low=97.0))     # +3R and -3R in one bar
        assert p.mfe_r == pytest.approx(3.0)
        assert p.mae_r == pytest.approx(-3.0)

    def test_stop_first_touch_is_recorded_without_ending_observation(self):
        """A probe is not a trade: touching the stop does not stop it."""
        p = _probe()
        p.observe(_bar(6, high=100.5, low=99.0))     # stop touched
        p.observe(_bar(7, high=104.0, low=100.0))    # 4R afterwards
        assert p.stop_first_touch_ms == _ms(6)
        assert p.first_touch["4r"] == _ms(7)
        assert p.is_open

    def test_same_bar_ambiguity_is_flagged_not_guessed(self):
        """1m OHLC cannot order a target and a stop inside one bar, so
        the probe records both and says the order is unknown."""
        p = _probe()
        p.observe(_bar(6, high=103.0, low=99.0))
        assert p.same_bar["2r"] is True
        assert p.stop_first_touch_ms == _ms(6)


# ═════════════════════════════════════════════════════════════════════
# Boundaries, ordering, and refusal to measure nonsense
# ═════════════════════════════════════════════════════════════════════


class TestObservationBoundaries:
    def test_bars_entirely_before_the_fill_are_ignored(self):
        p = _probe(entry_min=5)          # filled at the close of bar 5
        p.observe(_bar(4, high=110.0, low=90.0))
        p.observe(_bar(5, high=110.0, low=90.0))
        assert p.bars_observed == 0
        assert p.first_touch == {}

    def test_replayed_bar_is_ignored(self):
        p = _probe()
        p.observe(_bar(6, high=102.0, low=100.0))
        p.observe(_bar(6, high=109.0, low=100.0))
        assert p.bars_observed == 1
        assert p.mfe_r == pytest.approx(2.0)

    def test_short_direction_is_mirrored(self):
        p = _probe(direction="SHORT", entry=100.0, stop=101.0, target=98.0)
        p.observe(_bar(6, high=100.5, low=97.0))    # 3R down
        assert p.mfe_r == pytest.approx(3.0)
        assert p.first_touch["3r"] == _ms(6)

    def test_zero_r_distance_builds_no_probe(self):
        """An R of zero would turn every excursion into nonsense."""
        assert RProbe.create(
            trade_id="X", symbol="SPY", direction="LONG", entry_price=100.0,
            stop_price=100.0, target_price=102.0, entry_timestamp_ms=0) is None

    def test_bad_direction_builds_no_probe(self):
        assert RProbe.create(
            trade_id="X", symbol="SPY", direction="BOTH", entry_price=100.0,
            stop_price=99.0, target_price=102.0, entry_timestamp_ms=0) is None

    def test_malformed_bar_is_survived(self):
        p = _probe()
        for bad in ({}, {"time_ms": _ms(6)}, {"time_ms": None, "high": 1, "low": 1}):
            p.observe(bad)
        assert p.bars_observed == 0


# ═════════════════════════════════════════════════════════════════════
# Termination
# ═════════════════════════════════════════════════════════════════════


class TestTermination:
    def test_close_stops_observation(self):
        p = _probe()
        p.close("SESSION_END")
        p.observe(_bar(9, high=110.0, low=100.0))
        assert p.bars_observed == 0
        assert p.closed_reason == "SESSION_END"

    def test_close_is_idempotent(self):
        p = _probe()
        p.close("SESSION_END")
        p.close("SOMETHING_ELSE")
        assert p.closed_reason == "SESSION_END"


# ═════════════════════════════════════════════════════════════════════
# Persistence — additive, never destructive
# ═════════════════════════════════════════════════════════════════════


class TestPersistence:
    def test_block_merges_without_touching_the_rest(self, tmp_path):
        record = {
            "trade_id": "SPY_LONG_ORB_HIGH_1", "state": "CLOSED",
            "outcome": {"result": "WIN", "gross_pnl": 60.0},
            "setup_snapshot": {"level_source": "ORB_HIGH"},
            "chart_context": {"candles": [1, 2, 3]},
            "exit_chart_context": {"candles": [4, 5]},
        }
        (tmp_path / "SPY_LONG_ORB_HIGH_1.json").write_text(json.dumps(record))

        p = _probe()
        p.observe(_bar(9, high=103.0, low=100.0))
        persist_r_probe("SPY_LONG_ORB_HIGH_1", p.to_block(), base_dir=tmp_path)

        after = json.loads((tmp_path / "SPY_LONG_ORB_HIGH_1.json").read_text())
        assert after["outcome"] == record["outcome"], "real outcome must survive"
        assert after["state"] == "CLOSED"
        assert after["setup_snapshot"] == record["setup_snapshot"]
        assert after["chart_context"] == record["chart_context"]
        assert after["exit_chart_context"] == record["exit_chart_context"]
        assert after["r_probe"]["first_touch"]["3r"] == _ms(9)

    def test_no_record_means_no_file_is_created(self, tmp_path):
        """A probe describes a trade; it never invents one."""
        p = _probe()
        assert persist_r_probe("NOPE", p.to_block(), base_dir=tmp_path) is None
        assert list(tmp_path.glob("*.json")) == []

    def test_block_is_json_serialisable(self):
        p = _probe()
        p.observe(_bar(6, high=102.0, low=100.0))
        json.dumps(p.to_block())

    def test_path_is_kept_so_other_multiples_stay_answerable(self):
        p = _probe()
        p.observe(_bar(6, high=101.5, low=100.0))
        block = p.to_block()
        assert block["path"][0]["high"] == 101.5
        assert block["path"][0]["time_ms"] == _ms(6)


# ═════════════════════════════════════════════════════════════════════
# Orchestrator integration
# ═════════════════════════════════════════════════════════════════════


def _make_orchestrator(tmp_path, direction="LONG"):
    sb = LiveSessionBuilder("SPY", "America/New_York")
    return MaxBotTradeOrchestrator(
        underlying_symbol="SPY", direction=direction, tick_size=0.01,
        session_builder=sb,
        signal_detector=LiveSignalDetector(
            symbol="SPY", direction=direction, tick_size=0.01),
        trade_manager=DailyTradeManager(),
        option_selector=MagicMock(), entry_executor=MagicMock(),
        exit_executor=MagicMock(), trade_state_dir=tmp_path,
    ), sb


def _install_probe(orch, setup_key, entry_min, *, direction="LONG",
                   entry=100.0, stop=99.0, target=102.0):
    probe = RProbe.create(
        trade_id=f"SPY_{setup_key.replace(':', '_')}", symbol="SPY",
        direction=direction, entry_price=entry, stop_price=stop,
        target_price=target, entry_timestamp_ms=_ms(entry_min))
    orch._r_probes[setup_key] = probe
    return probe


class TestOrchestratorIntegration:
    def test_probe_keeps_observing_while_waiting_for_a_new_signal(self, tmp_path):
        """After the exit the orchestrator is back in WAITING_FOR_SIGNAL.
        The probe must not care."""
        orch, _ = _make_orchestrator(tmp_path)
        probe = _install_probe(orch, "LONG:ORB_HIGH:1", entry_min=5)
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        orch.on_bar(_bar(9, high=103.0, low=100.0))
        assert probe.bars_observed == 1
        assert probe.first_touch["3r"] == _ms(9)

    def test_two_trades_on_one_symbol_stay_separate(self, tmp_path):
        orch, _ = _make_orchestrator(tmp_path)
        first = _install_probe(orch, "LONG:ORB_HIGH:1", entry_min=5)
        second = _install_probe(orch, "LONG:ORB_HIGH:20", entry_min=20,
                                entry=200.0, stop=199.0, target=202.0)
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        orch.on_bar(_bar(25, high=103.0, low=100.0))

        assert first.bars_observed == 1
        assert second.bars_observed == 1
        assert first.trade_id != second.trade_id
        # Same bar, different entry prices -> different R readings.
        assert first.mfe_r != second.mfe_r

    def test_probe_survives_clear_active_trade(self, tmp_path):
        orch, _ = _make_orchestrator(tmp_path)
        probe = _install_probe(orch, "LONG:ORB_HIGH:1", entry_min=5)
        orch._clear_active_trade()
        assert orch._r_probes.get("LONG:ORB_HIGH:1") is probe

    def test_probe_never_places_an_order(self, tmp_path):
        orch, _ = _make_orchestrator(tmp_path)
        _install_probe(orch, "LONG:ORB_HIGH:1", entry_min=5)
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        for m in (6, 7, 8, 9):
            orch.on_bar(_bar(m, high=110.0, low=90.0))

        assert orch._exit_executor.submit_exit.call_count == 0
        assert orch._entry_executor.mock_calls == []
        assert orch._option_selector.mock_calls == []

    def test_probe_does_not_change_lifecycle(self, tmp_path):
        orch, _ = _make_orchestrator(tmp_path)
        _install_probe(orch, "LONG:ORB_HIGH:1", entry_min=5)
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
        orch.on_bar(_bar(9, high=110.0, low=90.0))
        assert orch._lifecycle == LifecycleState.WAITING_FOR_SIGNAL

    def test_close_r_probes_terminates_and_persists(self, tmp_path):
        orch, _ = _make_orchestrator(tmp_path)
        probe = _install_probe(orch, "LONG:ORB_HIGH:1", entry_min=5)
        (tmp_path / f"{probe.trade_id}.json").write_text(
            json.dumps({"trade_id": probe.trade_id, "state": "CLOSED"}))
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
        orch.on_bar(_bar(9, high=103.0, low=100.0))

        orch.close_r_probes("SESSION_END")

        assert not probe.is_open
        saved = json.loads((tmp_path / f"{probe.trade_id}.json").read_text())
        assert saved["r_probe"]["observation_closed_reason"] == "SESSION_END"
        assert saved["r_probe"]["first_touch"]["3r"] == _ms(9)

    def test_observation_written_to_disk_as_it_goes(self, tmp_path):
        """Crash safety: the path must not live only in memory."""
        orch, _ = _make_orchestrator(tmp_path)
        probe = _install_probe(orch, "LONG:ORB_HIGH:1", entry_min=5)
        (tmp_path / f"{probe.trade_id}.json").write_text(
            json.dumps({"trade_id": probe.trade_id, "state": "CLOSED"}))
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        orch.on_bar(_bar(9, high=103.0, low=100.0))

        saved = json.loads((tmp_path / f"{probe.trade_id}.json").read_text())
        assert saved["r_probe"]["bars_observed"] == 1

    def test_start_r_probe_arms_from_the_real_trade_state(self, tmp_path):
        """The actual arming point, not a hand-installed probe."""
        from decimal import Decimal
        from trading_lab.live.execution_intent import UnderlyingTriggerLevels

        orch, _ = _make_orchestrator(tmp_path)
        orch._active_setup_key = "LONG:ORB_HIGH:1"
        orch._active_entry_timestamp_ms = _ms(5)
        orch._resolved_direction = "LONG"
        orch._underlying_triggers = UnderlyingTriggerLevels(
            entry_price=Decimal("100.00"), stop_price=Decimal("99.00"),
            target_price=Decimal("102.00"))

        orch._start_r_probe()

        probe = orch._r_probes["LONG:ORB_HIGH:1"]
        assert probe.trade_id == "SPY_LONG_ORB_HIGH_1"
        assert probe.entry_price == 100.0
        assert probe.stop_price == 99.0
        assert probe.r_distance == pytest.approx(1.0)
        assert probe.entry_timestamp_ms == _ms(5)

    def test_start_r_probe_is_idempotent(self, tmp_path):
        from decimal import Decimal
        from trading_lab.live.execution_intent import UnderlyingTriggerLevels
        orch, _ = _make_orchestrator(tmp_path)
        orch._active_setup_key = "LONG:ORB_HIGH:1"
        orch._active_entry_timestamp_ms = _ms(5)
        orch._resolved_direction = "LONG"
        orch._underlying_triggers = UnderlyingTriggerLevels(
            entry_price=Decimal("100.00"), stop_price=Decimal("99.00"),
            target_price=Decimal("102.00"))
        orch._start_r_probe()
        first = orch._r_probes["LONG:ORB_HIGH:1"]
        orch._start_r_probe()
        assert orch._r_probes["LONG:ORB_HIGH:1"] is first

    def test_missing_record_does_not_break_observation(self, tmp_path):
        """No file on disk yet: the probe keeps its in-memory path."""
        orch, _ = _make_orchestrator(tmp_path)
        probe = _install_probe(orch, "LONG:ORB_HIGH:1", entry_min=5)
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
        orch.on_bar(_bar(9, high=103.0, low=100.0))
        assert probe.bars_observed == 1


# ═════════════════════════════════════════════════════════════════════
# The fill minute — neither discarded nor taken whole
# ═════════════════════════════════════════════════════════════════════
#
# Replays the real MSFT trade of 2026-08-26:
#   entry candle 14:13, filled 14:14:09.960, stop touched 14:14:15.
# The bar's own low (494.23) is post-fill here, but its earlier low at
# 14:14:05 was not, so neither "use the bar" nor "skip the bar" is right.

MSFT_ENTRY_MIN = 13
MSFT_FILL_MS = _ms(14) + 9_960


def _msft_probe():
    return RProbe.create(
        trade_id="MSFT_LONG_ORB_HIGH_1", symbol="MSFT", direction="LONG",
        entry_price=494.34, stop_price=494.25, target_price=494.52,
        entry_timestamp_ms=_ms(MSFT_ENTRY_MIN), fill_timestamp_ms=MSFT_FILL_MS)


class TestFillMinute:
    def test_pre_fill_live_price_below_stop_does_not_count(self):
        """14:14:05 traded at 494.27, before the position existed."""
        p = _msft_probe()
        p.observe_price(494.27, _ms(14) + 5_000)
        assert p.live_samples == 0
        assert p.stop_first_touch_ms is None
        assert p.mae_r == 0.0

    def test_post_fill_live_price_below_stop_is_recorded(self):
        """14:14:15 traded at 494.23 — five seconds after the fill."""
        p = _msft_probe()
        p.observe_price(494.23, _ms(14) + 15_000)
        assert p.stop_first_touch_ms == _ms(14) + 15_000
        assert p.stop_first_touch_source == "PRICE"
        assert p.mae_r < -1.0

    def test_short_is_mirrored(self):
        p = RProbe.create(
            trade_id="X", symbol="SPY", direction="SHORT", entry_price=100.0,
            stop_price=101.0, target_price=98.0,
            entry_timestamp_ms=_ms(13), fill_timestamp_ms=_ms(14) + 9_960)
        p.observe_price(101.5, _ms(14) + 5_000)      # pre-fill
        assert p.stop_first_touch_ms is None
        p.observe_price(101.5, _ms(14) + 15_000)     # post-fill
        assert p.stop_first_touch_ms == _ms(14) + 15_000

    def test_r_target_reached_inside_the_fill_minute(self):
        p = _msft_probe()                            # R = 0.09, 2R = 494.52
        p.observe_price(494.52, _ms(14) + 20_000)
        assert p.first_touch["2r"] == _ms(14) + 20_000
        assert p.first_touch_source["2r"] == "PRICE"

    def test_fill_bar_high_low_never_move_mfe_or_mae(self):
        """The decisive one: the bar's extremes may predate the fill."""
        p = _msft_probe()
        p.observe(_bar(14, high=499.00, low=489.00, close=494.36))
        assert p.mfe_r == pytest.approx((494.36 - 494.34) / 0.09)
        assert p.mae_r == 0.0, "a pre-fill low must never become the MAE"
        assert p.bars_observed == 1, "the fill minute must not be discarded"

    def test_fill_bar_close_still_counts(self):
        """Belt and braces: if no live sample arrived, the close alone
        still carries that minute."""
        p = _msft_probe()
        p.observe(_bar(14, high=499.0, low=489.0, close=494.60))
        assert p.first_touch["2r"] == _ms(14)
        assert p.first_touch_source["2r"] == "BAR"

    def test_next_bar_uses_high_low_normally(self):
        p = _msft_probe()
        p.observe(_bar(14, high=499.0, low=489.0, close=494.36))
        p.observe(_bar(15, high=494.70, low=494.10, close=494.40))
        assert p.mfe_r == pytest.approx((494.70 - 494.34) / 0.09)
        assert p.mae_r == pytest.approx((494.10 - 494.34) / 0.09)

    def test_live_samples_are_confined_to_the_fill_minute(self):
        """The probe stays bar-based afterwards, not tick-based."""
        p = _msft_probe()
        p.observe_price(494.90, _ms(15) + 10_000)
        p.observe_price(494.90, _ms(20))
        assert p.live_samples == 0

    def test_both_paths_reach_the_same_extremes(self):
        p = _msft_probe()
        p.observe_price(494.23, _ms(14) + 15_000)
        p.observe(_bar(14, high=499.0, low=489.0, close=494.36))
        p.observe(_bar(15, high=494.80, low=494.30, close=494.50))
        assert p.stop_first_touch_ms == _ms(14) + 15_000
        assert p.first_touch["2r"] == _ms(15)

    def test_live_samples_are_kept_in_the_path(self):
        p = _msft_probe()
        p.observe_price(494.23, _ms(14) + 15_000)
        block = p.to_block()
        live = [x for x in block["path"] if x["source"] == "PRICE"]
        assert live and live[0]["price"] == 494.23
        assert block["fill_timestamp_ms"] == MSFT_FILL_MS

    def test_fill_bar_is_flagged_as_partial(self):
        """A reader must not naively use that bar's high/low."""
        p = _msft_probe()
        p.observe(_bar(14, high=499.0, low=489.0, close=494.36))
        bars = [x for x in p.to_block()["path"] if x["source"] == "BAR"]
        assert bars[0]["partial_post_fill"] is True

    def test_ambiguity_is_not_invented_between_samples(self):
        """Two touches at two different samples ARE ordered — one price
        cannot be on both sides at once. No same-bar flag."""
        p = _msft_probe()
        p.observe_price(494.23, _ms(14) + 15_000)    # stop
        p.observe_price(494.52, _ms(14) + 25_000)    # 2R, later
        assert p.same_bar["2r"] is False
        assert p.stop_first_touch_ms < p.first_touch["2r"]

    def test_ambiguity_is_kept_when_one_bar_holds_both(self):
        p = _probe()                                  # entry 100, stop 99
        p.observe(_bar(7, high=103.0, low=99.0))
        assert p.same_bar["2r"] is True

    def test_bad_live_price_is_survived(self):
        p = _msft_probe()
        for bad in (None, float("nan"), 0.0, -1.0, "x"):
            p.observe_price(bad, _ms(14) + 15_000)
        assert p.live_samples == 0

    def test_closed_probe_ignores_live_prices(self):
        p = _msft_probe()
        p.close("SESSION_END")
        p.observe_price(494.23, _ms(14) + 15_000)
        assert p.live_samples == 0


class TestOrchestratorLivePriceFeed:
    def test_on_price_feeds_only_probes_inside_their_fill_minute(self, tmp_path):
        """Two trades on one symbol: only the one whose fill minute is
        current may take the sample."""
        import time
        orch, _ = _make_orchestrator(tmp_path)
        now_ms = int(time.time() * 1000)
        minute = (now_ms // 60_000) * 60_000

        current = RProbe.create(
            trade_id="SPY_A", symbol="SPY", direction="LONG", entry_price=100.0,
            stop_price=99.0, target_price=102.0,
            entry_timestamp_ms=minute - 60_000, fill_timestamp_ms=minute)
        old = RProbe.create(
            trade_id="SPY_B", symbol="SPY", direction="LONG", entry_price=100.0,
            stop_price=99.0, target_price=102.0,
            entry_timestamp_ms=minute - 600_000, fill_timestamp_ms=minute - 540_000)
        orch._r_probes = {"A": current, "B": old}
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL

        orch.on_price(98.5)

        assert current.live_samples == 1
        assert current.stop_first_touch_ms is not None
        assert old.live_samples == 0, "an older probe must not take the sample"

    def test_live_feed_places_no_order(self, tmp_path):
        import time
        orch, _ = _make_orchestrator(tmp_path)
        minute = (int(time.time() * 1000) // 60_000) * 60_000
        orch._r_probes = {"A": RProbe.create(
            trade_id="SPY_A", symbol="SPY", direction="LONG", entry_price=100.0,
            stop_price=99.0, target_price=102.0,
            entry_timestamp_ms=minute - 60_000, fill_timestamp_ms=minute)}
        orch._lifecycle = LifecycleState.WAITING_FOR_SIGNAL
        for px in (98.0, 105.0, 99.5):
            orch.on_price(px)
        assert orch._exit_executor.submit_exit.call_count == 0
        assert orch._option_selector.mock_calls == []
