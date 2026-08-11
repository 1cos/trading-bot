"""Tests for DailyTradeManager — daily trade state for MaxBot v0.1.

Covers:
  1.  Fresh day → can trade.
  2.  First trade opened → active, cannot accept another.
  3.  First trade LOSS → second trade allowed.
  4.  Second trade LOSS → day finished.
  5.  First trade WIN → day finished immediately.
  6.  LOSS then WIN → day finished.
  7.  Signal/evaluation alone does not increment trade count.
  8.  New trading date resets state.
  9.  Cannot open second simultaneous trade.
  10. Cannot record result without an active trade.
  11. State snapshot/counters correct throughout.
"""

import pytest

from trading_lab.live.trade_manager import (
    DailyTradeManager,
    DailyState,
    TradeResult,
)


# ── Test 1: Fresh day → can trade ────────────────────────────────────────────

class TestFreshDay:
    def test_can_trade_after_ensure_date(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        assert m.can_trade is True

    def test_cannot_trade_without_date(self):
        m = DailyTradeManager()
        assert m.can_trade is False

    def test_fresh_state(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        s = m.state
        assert s.trading_date == "2026-08-11"
        assert s.trades_used == 0
        assert s.wins == 0
        assert s.losses == 0
        assert s.has_active_trade is False
        assert s.day_finished is False
        assert s.can_trade is True


# ── Test 2: First trade opened → active ──────────────────────────────────────

class TestTradeOpen:
    def test_active_after_open(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        assert m.state.has_active_trade is True
        assert m.state.trades_used == 1

    def test_cannot_trade_while_active(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        assert m.can_trade is False


# ── Test 3: First trade LOSS → second trade allowed ──────────────────────────

class TestFirstLoss:
    def test_second_trade_allowed(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        m.record_trade_result(TradeResult.LOSS)
        assert m.can_trade is True
        assert m.state.trades_used == 1
        assert m.state.losses == 1
        assert m.state.has_active_trade is False
        assert m.state.day_finished is False


# ── Test 4: Second trade LOSS → day finished ─────────────────────────────────

class TestDoubleLoss:
    def test_day_finished_after_two_losses(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        m.record_trade_result(TradeResult.LOSS)
        m.record_trade_open()
        m.record_trade_result(TradeResult.LOSS)
        assert m.can_trade is False
        assert m.state.day_finished is True
        assert m.state.trades_used == 2
        assert m.state.losses == 2
        assert m.state.wins == 0


# ── Test 5: First trade WIN → day finished immediately ───────────────────────

class TestFirstWin:
    def test_day_finished_after_win(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        m.record_trade_result(TradeResult.WIN)
        assert m.can_trade is False
        assert m.state.day_finished is True
        assert m.state.wins == 1
        assert m.state.trades_used == 1


# ── Test 6: LOSS then WIN → day finished ─────────────────────────────────────

class TestLossThenWin:
    def test_day_finished(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        m.record_trade_result(TradeResult.LOSS)
        assert m.can_trade is True

        m.record_trade_open()
        m.record_trade_result(TradeResult.WIN)
        assert m.can_trade is False
        assert m.state.day_finished is True
        assert m.state.trades_used == 2
        assert m.state.wins == 1
        assert m.state.losses == 1


# ── Test 7: Signal alone does not increment trade count ──────────────────────

class TestSignalDoesNotCount:
    def test_can_trade_unchanged_by_queries(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        # Repeatedly query state — no increment
        for _ in range(10):
            _ = m.can_trade
            _ = m.state
        assert m.state.trades_used == 0
        assert m.can_trade is True


# ── Test 8: New trading date resets state ─────────────────────────────────────

class TestDateRollover:
    def test_reset_on_new_date(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        m.record_trade_result(TradeResult.WIN)
        assert m.can_trade is False

        m.ensure_date("2026-08-12")
        assert m.can_trade is True
        assert m.state.trades_used == 0
        assert m.state.wins == 0
        assert m.state.losses == 0
        assert m.state.has_active_trade is False
        assert m.state.day_finished is False

    def test_same_date_is_noop(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        m.ensure_date("2026-08-11")  # same date
        assert m.state.trades_used == 1
        assert m.state.has_active_trade is True

    def test_rollover_clears_active_trade(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        # Active trade exists, but new day resets everything
        m.ensure_date("2026-08-12")
        assert m.state.has_active_trade is False
        assert m.can_trade is True


# ── Test 9: Cannot open second simultaneous trade ────────────────────────────

class TestNoSimultaneous:
    def test_raises_on_double_open(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        with pytest.raises(RuntimeError, match="another is active"):
            m.record_trade_open()


# ── Test 10: Cannot record result without active trade ───────────────────────

class TestNoResultWithoutActive:
    def test_raises_on_result_no_active(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        with pytest.raises(RuntimeError, match="No active trade"):
            m.record_trade_result(TradeResult.LOSS)

    def test_raises_on_double_result(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        m.record_trade_result(TradeResult.LOSS)
        with pytest.raises(RuntimeError, match="No active trade"):
            m.record_trade_result(TradeResult.LOSS)


# ── Test 11: State snapshot consistency ──────────────────────────────────────

class TestStateSnapshot:
    def test_full_sequence_counters(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")

        s0 = m.state
        assert s0.trades_used == 0 and s0.can_trade is True

        m.record_trade_open()
        s1 = m.state
        assert s1.trades_used == 1 and s1.has_active_trade is True
        assert s1.can_trade is False

        m.record_trade_result(TradeResult.LOSS)
        s2 = m.state
        assert s2.trades_used == 1 and s2.losses == 1
        assert s2.has_active_trade is False and s2.can_trade is True

        m.record_trade_open()
        s3 = m.state
        assert s3.trades_used == 2 and s3.has_active_trade is True
        assert s3.can_trade is False

        m.record_trade_result(TradeResult.LOSS)
        s4 = m.state
        assert s4.trades_used == 2 and s4.losses == 2
        assert s4.day_finished is True and s4.can_trade is False

    def test_snapshot_is_immutable(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        s = m.state
        with pytest.raises(AttributeError):
            s.trades_used = 99


# ── Test: Invalid date ───────────────────────────────────────────────────────

class TestInvalidDate:
    def test_empty_string(self):
        m = DailyTradeManager()
        with pytest.raises(ValueError):
            m.ensure_date("")

    def test_not_a_string(self):
        m = DailyTradeManager()
        with pytest.raises(ValueError):
            m.ensure_date(None)


# ── Test: Cannot open after day finished ─────────────────────────────────────

class TestCannotOpenAfterDayFinished:
    def test_raises_after_win(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        m.record_trade_result(TradeResult.WIN)
        with pytest.raises(RuntimeError, match="finished"):
            m.record_trade_open()

    def test_raises_after_max_trades(self):
        m = DailyTradeManager()
        m.ensure_date("2026-08-11")
        m.record_trade_open()
        m.record_trade_result(TradeResult.LOSS)
        m.record_trade_open()
        m.record_trade_result(TradeResult.LOSS)
        with pytest.raises(RuntimeError):
            m.record_trade_open()


# ── Test: No date set ────────────────────────────────────────────────────────

class TestNoDate:
    def test_open_without_date_raises(self):
        m = DailyTradeManager()
        with pytest.raises(RuntimeError, match="No trading date"):
            m.record_trade_open()
