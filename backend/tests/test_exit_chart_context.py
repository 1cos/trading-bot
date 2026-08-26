"""Contesto grafico fino all'uscita.

`chart_context` congela cio' che MaxBot vedeva all'ingresso. Il
percorso DOPO l'entry — il movimento che ha portato a stop, target o
alla perdita di controllo dell'uscita — vive solo in memoria: dopo un
restart non e' ricostruibile.

Nessuna chiamata IBKR, nessun rendering.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
from datetime import datetime as dt_cls, timezone

import pytest

from trading_lab.live.trade_orchestrator import LifecycleState

from test_entry_chart_context import (
    _BASE, _build, _c, _entry_bars, _ms, _open_record,
    _CancelledExitExecutor, _FilledExitExecutor, DEFAULT_LEVELS,
)


def _stop_bar(m=11):
    """Barra che colpisce lo stop (low <= 100.80)."""
    return _c(m, 101.15, 101.20, 100.60, 100.70)


def _closed(tmp_path, extra_bars=(), levels=DEFAULT_LEVELS):
    """Percorso reale fino a CLOSED. Ritorna (open_rec, final_rec, orch, sb)."""
    open_rec, orch, sb, state = _open_record(
        tmp_path, bars=_entry_bars(pad=8), levels=levels)
    for b in extra_bars:
        orch.on_bar(b)
    orch.on_bar(_stop_bar(11 + len(extra_bars)))
    orch.refresh_exit_status()
    final = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
    return open_rec, final, orch, sb


def _terminal(tmp_path):
    """Percorso reale fino a REQUIRES_ATTENTION."""
    ex = _CancelledExitExecutor()
    open_rec, orch, sb, _ = _open_record(
        tmp_path, bars=_entry_bars(pad=8), exit_executor=ex)
    orch._exit_max_retries = 2
    orch._exit_retry_cooldown_secs = 0.0
    orch.on_bar(_stop_bar())
    for _ in range(12):
        orch.refresh_exit_status()
        if orch.lifecycle == LifecycleState.REQUIRES_ATTENTION:
            break
    final = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
    return open_rec, final, orch, sb


# ═════════════════════════════════════════════════════════════════════════
# T1 / T2 / T3 / T4 / T5 — forma e finestra
# ═════════════════════════════════════════════════════════════════════════

class TestT1Presence:
    def test_closed_has_both_contexts(self, tmp_path):
        _, final, *_ = _closed(tmp_path)
        assert final["state"] == "CLOSED"
        assert "chart_context" in final
        assert "exit_chart_context" in final, (
            "il percorso post-entry non e' persistito: dopo un restart "
            "il movimento che ha causato l'uscita e' irricostruibile")

    def test_metadata(self, tmp_path):
        _, final, *_ = _closed(tmp_path)
        ecc = final["exit_chart_context"]
        assert ecc["timeframe_seconds"] == 60
        assert ecc["market_timezone"] == "America/New_York"
        assert isinstance(ecc["candles"], list) and ecc["candles"]


class TestT2SameStart:
    def test_start_matches_entry_context(self, tmp_path):
        _, final, *_ = _closed(tmp_path)
        assert (final["exit_chart_context"]["window"]["start_time_ms"]
                == final["chart_context"]["window"]["start_time_ms"])

    def test_first_candle_is_that_start(self, tmp_path):
        _, final, *_ = _closed(tmp_path)
        ecc = final["exit_chart_context"]
        assert ecc["candles"][0]["time_ms"] == ecc["window"]["start_time_ms"]


class TestT3EntryIncluded:
    def test_entry_candle_present(self, tmp_path):
        _, final, *_ = _closed(tmp_path)
        ecc = final["exit_chart_context"]
        ts = [c["time_ms"] for c in ecc["candles"]]
        assert final["entry_timestamp_ms"] in ts
        assert ecc["window"]["entry_time_ms"] == final["entry_timestamp_ms"]

    def test_exit_window_extends_past_entry(self, tmp_path):
        _, final, *_ = _closed(tmp_path)
        entry_end = final["chart_context"]["window"]["end_time_ms"]
        exit_end = final["exit_chart_context"]["window"]["end_time_ms"]
        assert exit_end > entry_end, "la finestra EXIT deve andare oltre l'entry"


class TestT4ExitMovement:
    def test_trigger_bar_is_in_the_window(self, tmp_path):
        _, final, orch, _ = _closed(tmp_path)
        ecc = final["exit_chart_context"]
        ts = [c["time_ms"] for c in ecc["candles"]]
        trigger_bar_ms = _ms(11)          # la barra che ha colpito lo stop
        assert trigger_bar_ms in ts
        assert ts[-1] == trigger_bar_ms, "la barra di trigger e' l'ultima"
        assert ecc["window"]["exit_time_ms"] == trigger_bar_ms

    def test_intermediate_bars_kept(self, tmp_path):
        extra = [_c(m, 101.3, 101.5, 101.1, 101.4) for m in (10, 11, 12)]
        _, final, *_ = _closed(tmp_path, extra_bars=extra)
        ts = [c["time_ms"] for c in final["exit_chart_context"]["candles"]]
        for m in (10, 11, 12):
            assert _ms(m) in ts, f"barra {m} persa nel percorso della trade"

    def test_exit_bar_time_is_a_bar_not_a_wall_clock(self, tmp_path):
        """trigger_time_ms nell'outcome e' l'ora dell'EVENTO; la finestra
        deve invece usare l'ora della BARRA."""
        _, final, *_ = _closed(tmp_path)
        exit_ms = final["exit_chart_context"]["window"]["exit_time_ms"]
        ts = [c["time_ms"] for c in final["exit_chart_context"]["candles"]]
        assert exit_ms in ts, "exit_time_ms deve corrispondere a una barra reale"


class TestT5Order:
    def test_strictly_chronological(self, tmp_path):
        _, final, *_ = _closed(tmp_path)
        ts = [c["time_ms"] for c in final["exit_chart_context"]["candles"]]
        assert ts == sorted(ts)
        assert len(set(ts)) == len(ts)

    def test_candle_shape(self, tmp_path):
        _, final, *_ = _closed(tmp_path)
        for c in final["exit_chart_context"]["candles"]:
            for k in ("time_ms", "open", "high", "low", "close", "volume"):
                assert k in c


# ═════════════════════════════════════════════════════════════════════════
# T6 — ENTRY context immutabile
# ═════════════════════════════════════════════════════════════════════════

class TestT6EntryImmutable:
    def test_closed_leaves_entry_context_identical(self, tmp_path):
        open_rec, final, *_ = _closed(tmp_path)
        assert final["chart_context"] == open_rec["chart_context"]

    def test_terminal_leaves_entry_context_identical(self, tmp_path):
        open_rec, final, *_ = _terminal(tmp_path)
        assert final["chart_context"] == open_rec["chart_context"]

    def test_entry_window_does_not_grow(self, tmp_path):
        open_rec, final, *_ = _closed(tmp_path)
        assert (final["chart_context"]["window"]["end_time_ms"]
                == open_rec["chart_context"]["window"]["end_time_ms"])
        assert (len(final["chart_context"]["candles"])
                == len(open_rec["chart_context"]["candles"]))


# ═════════════════════════════════════════════════════════════════════════
# T7 — livelli
# ═════════════════════════════════════════════════════════════════════════

class TestT7Levels:
    def test_all_levels_persisted(self, tmp_path):
        _, final, *_ = _closed(tmp_path)
        lv = final["exit_chart_context"]["levels"]
        assert lv["orb_high"] == 101.00 and lv["orb_low"] == 99.00
        assert lv["pdh"] == 103.50 and lv["pdl"] == 98.20
        assert lv["pmh"] == 102.10 and lv["pml"] == 99.40

    def test_missing_levels_are_null(self, tmp_path):
        _, final, *_ = _closed(tmp_path, levels={"orb_high": 101.0})
        lv = final["exit_chart_context"]["levels"]
        assert lv["orb_high"] == 101.0
        for k in ("orb_low", "pdh", "pdl", "pmh", "pml"):
            assert lv[k] is None


# ═════════════════════════════════════════════════════════════════════════
# T8 / T9 — REQUIRES_ATTENTION
# ═════════════════════════════════════════════════════════════════════════

class TestT8Terminal:
    def test_terminal_has_exit_chart_context(self, tmp_path):
        _, final, *_ = _terminal(tmp_path)
        assert final["state"] == "REQUIRES_ATTENTION"
        assert "exit_chart_context" in final
        assert final["exit_chart_context"]["candles"]

    def test_no_fake_exit_fill_or_pnl(self, tmp_path):
        _, final, *_ = _terminal(tmp_path)
        assert "outcome" not in final
        blob = json.dumps(final["exit_chart_context"])
        for forbidden in ("gross_pnl", "exit_fill_premium", "result"):
            assert forbidden not in blob

    def test_terminal_timestamp_still_in_terminal_block(self, tmp_path):
        _, final, *_ = _terminal(tmp_path)
        assert final["terminal"]["terminal_timestamp_ms"] is not None


class TestT9TerminalExitTime:
    def test_exit_time_reflects_the_trigger_bar_not_a_fill(self, tmp_path):
        """Non c'e' exit fill: exit_time_ms puo' essere la barra di
        trigger (il movimento c'e' stato) ma MAI un fill inventato."""
        _, final, *_ = _terminal(tmp_path)
        w = final["exit_chart_context"]["window"]
        ts = [c["time_ms"] for c in final["exit_chart_context"]["candles"]]
        assert w["exit_time_ms"] is None or w["exit_time_ms"] in ts

    def test_no_exit_fill_fields_invented(self, tmp_path):
        _, final, *_ = _terminal(tmp_path)
        assert "exit_fill_time_ms" not in final["exit_chart_context"]["window"]


# ═════════════════════════════════════════════════════════════════════════
# T10 — fallback legacy
# ═════════════════════════════════════════════════════════════════════════

class TestT10LegacyFallback:
    def test_builds_without_entry_chart_context(self, tmp_path):
        """Un orchestrator senza chart_context ENTRY deve comunque
        produrre il contesto EXIT, senza eccezioni."""
        runner, orch, sb, _ = _build(tmp_path)
        for b in _entry_bars(pad=8) + [_stop_bar()]:
            sb.add_bar(b)
        orch._active_entry_timestamp_ms = _ms(9)
        orch._active_setup_snapshot = {"break_bar": {"bar_utc_ms": _ms(5)}}
        orch._active_chart_start_ms = None          # nessun contesto ENTRY
        ecc = orch._build_exit_chart_context()
        assert ecc is not None and ecc["candles"]
        assert ecc["window"]["start_time_ms"] == _ms(0), "fallback: break - 5"

    def test_builds_without_setup_snapshot_either(self, tmp_path):
        runner, orch, sb, _ = _build(tmp_path)
        for b in _entry_bars(pad=8) + [_stop_bar()]:
            sb.add_bar(b)
        orch._active_entry_timestamp_ms = None
        orch._active_setup_snapshot = None
        orch._active_chart_start_ms = None
        ecc = orch._build_exit_chart_context()
        assert ecc is not None and ecc["candles"]

    def test_empty_session_returns_none(self, tmp_path):
        runner, orch, sb, _ = _build(tmp_path)
        assert orch._build_exit_chart_context() is None


# ═════════════════════════════════════════════════════════════════════════
# T11 / T12 — JSON e copy safety
# ═════════════════════════════════════════════════════════════════════════

class TestT11Json:
    def test_round_trip(self, tmp_path):
        _, final, *_ = _closed(tmp_path)
        assert json.loads(json.dumps(final)) == final

    def test_only_json_types(self, tmp_path):
        _, final, *_ = _closed(tmp_path)

        def _check(v, path="exit_chart_context"):
            assert isinstance(v, (dict, list, str, int, float, bool,
                                  type(None))), f"{path}: {type(v).__name__}"
            if isinstance(v, dict):
                for k, s in v.items():
                    _check(s, f"{path}.{k}")
            elif isinstance(v, list):
                for i, s in enumerate(v):
                    _check(s, f"{path}[{i}]")

        _check(final["exit_chart_context"])


class TestT12CopySafety:
    def test_later_bars_do_not_mutate_the_record(self, tmp_path):
        _, final, orch, sb = _closed(tmp_path)
        before = json.dumps(final["exit_chart_context"], sort_keys=True)
        sb.add_bar(_c(30, 999.0, 999.0, 999.0, 999.0))
        again = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
        assert json.dumps(again["exit_chart_context"], sort_keys=True) == before


# ═════════════════════════════════════════════════════════════════════════
# T13 / T14 / T15 — nessun IBKR, nessun cambio execution, preservazione
# ═════════════════════════════════════════════════════════════════════════

class TestT13NoIbkr:
    def test_exit_capture_never_calls_ibkr(self, tmp_path):
        import inspect
        from trading_lab.live.trade_orchestrator import MaxBotTradeOrchestrator
        src = inspect.getsource(MaxBotTradeOrchestrator._build_exit_chart_context)
        for m in ("reqHistoricalData", "reqMktData", "reqRealTimeBars",
                  "self._ib", "IB("):
            assert m not in src, f"chiamata IBKR nella cattura EXIT: {m}"


class TestT14NoExecutionChange:
    def test_exit_and_pnl_unchanged(self, tmp_path):
        _, final, orch, _ = _closed(tmp_path)
        assert final["outcome"]["exit_reason"] == "STOP"
        assert final["outcome"]["result"] == "LOSS"
        assert final["outcome"]["exit_fill_premium"] == 1.90
        assert final["outcome"]["gross_pnl"] == round((1.90 - 2.65) * 100, 2)
        assert orch.lifecycle in (LifecycleState.WAITING_FOR_SIGNAL,
                                  LifecycleState.DONE_FOR_DAY)

    def test_single_exit_submission(self, tmp_path):
        ex = _FilledExitExecutor()
        _open, orch, sb, _ = _open_record(tmp_path, bars=_entry_bars(pad=8),
                                          exit_executor=ex)
        orch.on_bar(_stop_bar())
        orch.refresh_exit_status()
        assert len(ex.submissions) == 1

    def test_terminal_retries_unchanged(self, tmp_path):
        _, final, orch, _ = _terminal(tmp_path)
        assert final["terminal"]["retry_count"] == 2
        assert orch.lifecycle == LifecycleState.REQUIRES_ATTENTION


class TestT15RecordPreservation:
    def test_closed_preserves_everything(self, tmp_path):
        open_rec, final, *_ = _closed(tmp_path)
        assert final["setup_snapshot"] == open_rec["setup_snapshot"]
        assert final["chart_context"] == open_rec["chart_context"]
        assert final["setup_snapshot"]["session"]["market_timezone"] == \
            "America/New_York"
        assert final["setup_snapshot"]["entry_pattern_type"] == \
            "SINGLE_CANDLE_REJECTION"

    def test_terminal_preserves_everything(self, tmp_path):
        open_rec, final, *_ = _terminal(tmp_path)
        assert final["setup_snapshot"] == open_rec["setup_snapshot"]
        assert final["chart_context"] == open_rec["chart_context"]
        assert final["setup_snapshot"]["session"]["date"]
