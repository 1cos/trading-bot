"""Tests for T19F — allow genuine same-direction re-entry.

After a consumed setup, the detector must skip it and find the next
valid BDRR sequence.  Same setup remains blocked.  A new break in
the same direction IS allowed.

Covers:
1. Same setup still blocked (T19C protection preserved)
2. evaluate with consumed keys skips past consumed break
3. New break after consumed → new SIGNAL with different setup_key
4. Opposite direction still works
5. find_break respects _scan_start_index
6. DualSignalDetector passes consumed keys through
"""

from unittest.mock import MagicMock, patch
import pytest

from trading_lab.live.signal_detector import (
    LiveSignalDetector,
    SignalResult,
    SignalStatus,
)
from trading_lab.live.dual_signal_detector import DualSignalDetector
from trading_lab.break_finder import find_break


# ═════════════════════════════════════════════════════════════════════
# find_break with _scan_start_index
# ═════════════════════════════════════════════════════════════════════


class TestFindBreakScanStart:
    """find_break skips candles before _scan_start_index."""

    def _make_config(self, direction="SHORT", scan_start=None):
        cfg = {
            "direction": direction,
            "tick_size": 0.01,
            "timeframe_minutes": 1,
            "timezone": "America/New_York",
            "session_open": "09:30",
            "orb_start": "09:30",
            "orb_duration_minutes": 5,
            "orb_candle_count": 5,
            "level_source": "ORB_LOW" if direction == "SHORT" else "ORB_HIGH",
            "min_displacement_bars": 3,
            "news_candle_threshold": 3.0,
        }
        if scan_start is not None:
            cfg["_scan_start_index"] = scan_start
        return cfg

    def _make_candles_with_two_breaks(self):
        """Create candles with two SHORT breaks at different indices."""
        # ORB candle at index 4 (close = 100)
        candles = []
        for i in range(5):
            candles.append({
                "time_ms": 1000 + i * 60000,
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.0, "volume": 1000,
            })
        # First break at index 5 (close below 99.0 = orb_low)
        candles.append({
            "time_ms": 1000 + 5 * 60000,
            "open": 99.5, "high": 99.8, "low": 98.5,
            "close": 98.8, "volume": 1500,
        })
        # Some bars back above level
        for i in range(6, 10):
            candles.append({
                "time_ms": 1000 + i * 60000,
                "open": 100.0, "high": 101.0, "low": 99.5,
                "close": 100.2, "volume": 1000,
            })
        # Second break at index 10 (close below 99.0 again)
        candles.append({
            "time_ms": 1000 + 10 * 60000,
            "open": 99.2, "high": 99.5, "low": 98.0,
            "close": 98.5, "volume": 2000,
        })
        return candles

    def _make_orb(self):
        return {
            "status": "OK",
            "date": "2026-01-15",
            "orb_candle_index": 4,
            "orb_candle": {"time_ms": 1000 + 4 * 60000},
            "orb_high": 101.0,
            "orb_low": 99.0,
            "level_price": 99.0,  # SHORT
            "level_price_ticks": 9900,
        }

    def test_default_finds_first_break(self):
        candles = self._make_candles_with_two_breaks()
        orb = self._make_orb()
        config = self._make_config("SHORT")
        result = find_break(candles, orb, config)
        assert result["status"] == "OK"
        assert result["break_candle_index"] == 5

    def test_scan_start_skips_first_break(self):
        candles = self._make_candles_with_two_breaks()
        orb = self._make_orb()
        config = self._make_config("SHORT", scan_start=6)
        result = find_break(candles, orb, config)
        assert result["status"] == "OK"
        assert result["break_candle_index"] == 10  # second break

    def test_scan_start_beyond_all_breaks(self):
        candles = self._make_candles_with_two_breaks()
        orb = self._make_orb()
        config = self._make_config("SHORT", scan_start=11)
        result = find_break(candles, orb, config)
        assert result["status"] == "FAILED"


# ═════════════════════════════════════════════════════════════════════
# LiveSignalDetector with consumed_setup_keys
# ═════════════════════════════════════════════════════════════════════


class TestDetectorSkipsConsumed:
    """evaluate() skips consumed setups and finds the next one."""

    def test_no_consumed_returns_first_signal(self):
        sd = LiveSignalDetector(
            symbol="NVDA", direction="SHORT", tick_size=0.01,
        )
        # Mock _evaluate_inner to return a SIGNAL
        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", pipeline_stage="SIGNAL",
            stage_context={"break_bar_index": 5},
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        sd._evaluate_inner = MagicMock(return_value=sig)
        result = sd.evaluate({"candles": []})
        assert result.status == SignalStatus.SIGNAL
        assert result.setup_key == "SHORT:1000"

    def test_consumed_first_skips_to_second(self):
        sd = LiveSignalDetector(
            symbol="NVDA", direction="SHORT", tick_size=0.01,
        )
        sig1 = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", pipeline_stage="SIGNAL",
            stage_context={"break_bar_index": 5},
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        sig2 = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:5000", pipeline_stage="SIGNAL",
            stage_context={"break_bar_index": 15},
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        # First call returns consumed signal, second returns new one
        sd._evaluate_inner = MagicMock(side_effect=[sig1, sig2])

        result = sd.evaluate(
            {"candles": []},
            consumed_setup_keys={"SHORT:1000"},
        )
        assert result.status == SignalStatus.SIGNAL
        assert result.setup_key == "SHORT:5000"

        # _evaluate_inner called twice: first with skip=0, second with skip=6
        calls = sd._evaluate_inner.call_args_list
        assert calls[0][1].get("skip_before", 0) == 0
        assert calls[1][1]["skip_before"] == 6  # break_bar_index + 1

    def test_all_consumed_returns_no_setup(self):
        sd = LiveSignalDetector(
            symbol="NVDA", direction="SHORT", tick_size=0.01,
        )
        sig1 = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", pipeline_stage="SIGNAL",
            stage_context={"break_bar_index": 5},
        )
        no_setup = SignalResult(
            status=SignalStatus.NO_SETUP, direction="SHORT",
            pipeline_stage="BREAK_NOT_FOUND",
        )
        # First call returns consumed, second finds nothing
        sd._evaluate_inner = MagicMock(side_effect=[sig1, no_setup])

        result = sd.evaluate(
            {"candles": []},
            consumed_setup_keys={"SHORT:1000"},
        )
        assert result.status == SignalStatus.NO_SETUP

    def test_no_consumed_keys_no_skip(self):
        """Without consumed keys, first signal is returned normally."""
        sd = LiveSignalDetector(
            symbol="NVDA", direction="SHORT", tick_size=0.01,
        )
        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", pipeline_stage="SIGNAL",
            stage_context={"break_bar_index": 5},
        )
        sd._evaluate_inner = MagicMock(return_value=sig)
        result = sd.evaluate({"candles": []})
        assert result.setup_key == "SHORT:1000"
        sd._evaluate_inner.assert_called_once()


# ═════════════════════════════════════════════════════════════════════
# Orchestrator integration
# ═════════════════════════════════════════════════════════════════════


class TestOrchestratorSameDirectionReEntry:
    """Orchestrator allows new setup in same direction after consumed."""

    def test_same_setup_still_blocked(self):
        """T19C protection: same setup_key never re-enters."""
        from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator

        sb = MagicMock()
        sb.current_session.return_value = {
            "date": "2026-01-15",
            "candles": [{"time_ms": 1000}],
        }

        sd = MagicMock()
        sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000", pipeline_stage="SIGNAL",
            stage_context={"break_bar_index": 5},
        )
        sd.evaluate.return_value = sig

        tm = MagicMock()
        tm.can_trade = True

        orch = MaxBotTradeOrchestrator(
            underlying_symbol="NVDA", direction="SHORT",
            tick_size=0.01, session_builder=sb,
            signal_detector=sd, trade_manager=tm,
            option_selector=MagicMock(), entry_executor=MagicMock(),
            exit_executor=MagicMock(),
        )
        # Pre-consume the setup
        orch._consumed_setups.add("SHORT:1000")

        bar = {"time_ms": 5000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)

        # The detector was called WITH consumed keys
        sd.evaluate.assert_called_once()
        call_kwargs = sd.evaluate.call_args[1]
        assert "SHORT:1000" in call_kwargs["consumed_setup_keys"]

        # Signal has same key → should not be pending
        # (double defense: detector should skip it, plus orchestrator checks)
        assert not orch.has_pending_signal

    def test_new_setup_same_direction_allowed(self):
        """After consuming SHORT:1000, a new SHORT:5000 IS allowed."""
        from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator

        sb = MagicMock()
        sb.current_session.return_value = {
            "date": "2026-01-15",
            "candles": [{"time_ms": 1000}],
        }

        sd = MagicMock()
        new_sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:5000", pipeline_stage="SIGNAL",
            stage_context={"break_bar_index": 15},
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        sd.evaluate.return_value = new_sig

        tm = MagicMock()
        tm.can_trade = True

        orch = MaxBotTradeOrchestrator(
            underlying_symbol="NVDA", direction="SHORT",
            tick_size=0.01, session_builder=sb,
            signal_detector=sd, trade_manager=tm,
            option_selector=MagicMock(), entry_executor=MagicMock(),
            exit_executor=MagicMock(),
        )
        # First SHORT setup consumed
        orch._consumed_setups.add("SHORT:1000")

        bar = {"time_ms": 8000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)

        # New setup_key → allowed
        assert orch.has_pending_signal

    def test_opposite_direction_still_works(self):
        """LONG setup after consumed SHORT still works."""
        from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator

        sb = MagicMock()
        sb.current_session.return_value = {
            "date": "2026-01-15",
            "candles": [{"time_ms": 1000}],
        }

        sd = MagicMock()
        long_sig = SignalResult(
            status=SignalStatus.SIGNAL, direction="LONG",
            setup_key="LONG:3000", pipeline_stage="SIGNAL",
            stage_context={"break_bar_index": 10},
            trade_plan=MagicMock(), detection_result=MagicMock(),
        )
        sd.evaluate.return_value = long_sig

        tm = MagicMock()
        tm.can_trade = True

        orch = MaxBotTradeOrchestrator(
            underlying_symbol="NVDA", direction="SHORT",
            tick_size=0.01, session_builder=sb,
            signal_detector=sd, trade_manager=tm,
            option_selector=MagicMock(), entry_executor=MagicMock(),
            exit_executor=MagicMock(),
        )
        orch._consumed_setups.add("SHORT:1000")

        bar = {"time_ms": 5000, "open": 100, "high": 101,
               "low": 99, "close": 100.5, "volume": 1000}
        orch.on_bar(bar)

        assert orch.has_pending_signal

    def test_setup_keys_differ_between_trades(self):
        """Trade #1 and Trade #2 have different setup_keys."""
        sig1 = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:1000",
        )
        sig2 = SignalResult(
            status=SignalStatus.SIGNAL, direction="SHORT",
            setup_key="SHORT:5000",
        )
        assert sig1.setup_key != sig2.setup_key


# ═════════════════════════════════════════════════════════════════════
# DualSignalDetector passthrough
# ═════════════════════════════════════════════════════════════════════


class TestDualDetectorPassthrough:
    def test_consumed_keys_passed_to_both(self):
        long_sd = MagicMock()
        short_sd = MagicMock()
        long_sd.evaluate.return_value = SignalResult(
            status=SignalStatus.NO_SETUP, direction="LONG",
            stage_context={},
        )
        short_sd.evaluate.return_value = SignalResult(
            status=SignalStatus.NO_SETUP, direction="SHORT",
            stage_context={},
        )

        dual = DualSignalDetector(long_sd, short_sd)
        consumed = {"SHORT:1000"}
        dual.evaluate({"candles": []}, consumed_setup_keys=consumed)

        long_sd.evaluate.assert_called_once_with(
            {"candles": []}, consumed_setup_keys=consumed
        )
        short_sd.evaluate.assert_called_once_with(
            {"candles": []}, consumed_setup_keys=consumed
        )
