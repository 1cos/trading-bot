"""Catch-up delle barre dopo una resubscription mid-session.

Bug (micro-task 4): `_resubscribe_symbol()` chiama `_bootstrap_symbol()`,
che marca in `processed_times` TUTTE le barre RTH della nuova
BarDataList — comprese quelle arrivate durante il gap, che non sono mai
state valutate, e compresa l'ultima barra che a mercato aperto è ancora
in formazione.

Al boot la premessa di `_bootstrap_symbol` è corretta (pre-apertura le
barre sono storiche). Dopo un resubscribe mid-session non lo è: le
barre > `rt.last_bar_time_ms` sono barre live mai processate.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from trading_lab.live.bot_runner import MaxBotRunner, ibkr_bar_to_candle
from trading_lab.live.execution_queue import ExecutionQueue
from trading_lab.live.watchlist import SymbolRuntime


ET = ZoneInfo("America/New_York")


def _make_runner():
    runner = MaxBotRunner.__new__(MaxBotRunner)
    runner._symbols = ["QQQ"]
    runner._tz = ET
    runner._tz_str = "America/New_York"
    runner._session_open = "09:30"
    runner._session_close = "16:00"
    runner._execution_mode = MagicMock(__eq__=lambda s, o: True)
    runner._execution_queue = ExecutionQueue()
    runner._runtimes = {}
    runner._ib = MagicMock()
    runner._ib.sleep = lambda *a, **k: None
    return runner


def _bar(dt_utc, close=100.0):
    b = MagicMock()
    b.date = dt_utc
    b.open, b.high, b.low, b.close, b.volume = (
        close - 0.5, close + 0.5, close - 1.0, close, 1000)
    return b


def _rth_utc(minute_offset=0):
    """Oggi (data ET) alle 10:00 ET + offset, restituito in UTC.

    Ancorato alla data ET, non a quella UTC: fra la mezzanotte UTC e la
    mezzanotte ET le due date differiscono, e `_is_live_bar` confronta
    con `datetime.now(ET).date()`. Ancorare a UTC renderebbe il test
    dipendente dall'ora in cui viene eseguito.
    """
    today_et = datetime.now(ET).date()
    base_et = datetime(today_et.year, today_et.month, today_et.day,
                       10, 0, 0, tzinfo=ET)
    return (base_et + timedelta(minutes=minute_offset)).astimezone(timezone.utc)


def _ms(dt_utc):
    return int(dt_utc.timestamp() * 1000)


def _rt_after_gap():
    """SymbolRuntime che ha realmente processato fino a T, poi gap.

    Nuova BarDataList dopo resubscribe: T-2, T-1, T, T+1, T+2, T+3
    dove T+3 e' la barra corrente ancora in formazione.
    """
    t_minus2, t_minus1 = _rth_utc(-2), _rth_utc(-1)
    t = _rth_utc(0)
    t_plus1, t_plus2, t_plus3 = _rth_utc(1), _rth_utc(2), _rth_utc(3)

    rt = SymbolRuntime(symbol="QQQ")
    rt.enabled = True
    rt.session_builder = MagicMock()
    rt.orchestrator = MagicMock()
    rt.orchestrator.has_pending_signal = False
    rt.underlying_contract = MagicMock()
    rt.signal_detector = MagicMock()
    rt.signal_detector.last_result = None

    # Barre gia' realmente processate prima del gap
    rt.processed_times = {_ms(t_minus2), _ms(t_minus1), _ms(t)}
    rt.last_bar_time_ms = _ms(t)

    new_bars = [_bar(t_minus2, 100.0), _bar(t_minus1, 100.5), _bar(t, 101.0),
                _bar(t_plus1, 101.5), _bar(t_plus2, 102.0), _bar(t_plus3, 102.5)]
    return rt, new_bars, {
        "T-2": _ms(t_minus2), "T-1": _ms(t_minus1), "T": _ms(t),
        "T+1": _ms(t_plus1), "T+2": _ms(t_plus2), "T+3": _ms(t_plus3)}


def _resubscribe_with(runner, rt, new_bars):
    """Esegue il vero _resubscribe_symbol con una BarDataList data."""
    import time as _time
    bars_obj = MagicMock()
    bars_obj.__len__ = lambda s: len(new_bars)
    bars_obj.__iter__ = lambda s: iter(new_bars)
    bars_obj.__getitem__ = lambda s, i: new_bars[i]
    bars_obj.__bool__ = lambda s: True
    bars_obj.updateEvent = MagicMock()
    bars_obj.updateEvent.__iadd__ = lambda s, cb: s
    bars_obj.updateEvent.__len__ = lambda s: 1
    runner._ib.reqHistoricalData.return_value = bars_obj

    rt.bars = MagicMock()
    rt.bars.updateEvent = MagicMock()
    rt.bars.updateEvent.__len__ = lambda s: 1

    runner._resubscribe_symbol(rt, _time.monotonic())
    return bars_obj


# ═════════════════════════════════════════════════════════════════════════
# T1 — le barre di catch-up non devono essere pre-marcate come processed
# ═════════════════════════════════════════════════════════════════════════

class TestT1CatchUpNotPreMarked:
    def test_bars_after_boundary_are_not_marked_processed(self):
        runner = _make_runner()
        rt, new_bars, ts = _rt_after_gap()
        runner._runtimes = {"QQQ": rt}

        _resubscribe_with(runner, rt, new_bars)

        # <= T restano deduplicati (contesto), invariato
        assert ts["T-2"] in rt.processed_times
        assert ts["T-1"] in rt.processed_times
        assert ts["T"] in rt.processed_times

        # > T sono barre live mai valutate: NON devono essere pre-marcate
        assert ts["T+1"] not in rt.processed_times, "T+1 pre-marcata: catch-up perso"
        assert ts["T+2"] not in rt.processed_times, "T+2 pre-marcata: catch-up perso"

    def test_forming_bar_is_not_frozen_as_final(self):
        runner = _make_runner()
        rt, new_bars, ts = _rt_after_gap()
        runner._runtimes = {"QQQ": rt}

        _resubscribe_with(runner, rt, new_bars)

        assert ts["T+3"] not in rt.processed_times, (
            "l'ultima barra e' ancora in formazione: marcarla la congela "
            "con valori parziali per il resto della sessione")


# ═════════════════════════════════════════════════════════════════════════
# T2 — le barre di catch-up raggiungono l'orchestrator, una volta e in ordine
# ═════════════════════════════════════════════════════════════════════════

class TestT2CatchUpReachesOrchestrator:
    def test_missed_bars_reach_orchestrator_once_and_in_order(self):
        runner = _make_runner()
        rt, new_bars, ts = _rt_after_gap()
        runner._runtimes = {"QQQ": rt}

        _resubscribe_with(runner, rt, new_bars)
        rt.orchestrator.on_bar.reset_mock()

        # percorso normale, nessuna pipeline separata
        runner._poll_bars_fallback()

        seen = [c.args[0]["time_ms"] for c in rt.orchestrator.on_bar.call_args_list]
        assert seen == [ts["T+1"], ts["T+2"]], (
            f"attese T+1 e T+2 in ordine, ricevute {seen}")


# ═════════════════════════════════════════════════════════════════════════
# T3 — nessun doppio processing
# ═════════════════════════════════════════════════════════════════════════

class TestT3NoDoubleProcessing:
    def test_bars_processed_before_resubscribe_are_not_replayed(self):
        runner = _make_runner()
        rt, new_bars, ts = _rt_after_gap()
        runner._runtimes = {"QQQ": rt}

        _resubscribe_with(runner, rt, new_bars)
        rt.orchestrator.on_bar.reset_mock()
        runner._poll_bars_fallback()

        seen = [c.args[0]["time_ms"] for c in rt.orchestrator.on_bar.call_args_list]
        for label in ("T-2", "T-1", "T"):
            assert ts[label] not in seen, f"{label} rieseguita"

    def test_repeated_polls_do_not_duplicate(self):
        runner = _make_runner()
        rt, new_bars, ts = _rt_after_gap()
        runner._runtimes = {"QQQ": rt}

        _resubscribe_with(runner, rt, new_bars)
        rt.orchestrator.on_bar.reset_mock()
        for _ in range(4):
            runner._poll_bars_fallback()

        seen = [c.args[0]["time_ms"] for c in rt.orchestrator.on_bar.call_args_list]
        assert seen == [ts["T+1"], ts["T+2"]], f"duplicazioni: {seen}"


# ═════════════════════════════════════════════════════════════════════════
# T4 — il boot iniziale resta invariato
# ═════════════════════════════════════════════════════════════════════════

class TestT4BootSafetyUnchanged:
    def test_boot_bootstrap_marks_all_and_never_calls_orchestrator(self):
        runner = _make_runner()
        rt = SymbolRuntime(symbol="QQQ")
        rt.enabled = True
        rt.session_builder = MagicMock()
        rt.orchestrator = MagicMock()
        # boot: nessuna barra mai processata
        rt.processed_times = set()
        rt.last_bar_time_ms = 0

        bars = [_bar(_rth_utc(-2), 100.0), _bar(_rth_utc(-1), 100.5),
                _bar(_rth_utc(0), 101.0)]
        rt.bars = bars

        runner._bootstrap_symbol(rt)

        # semantica di boot invariata: tutte marcate, contesto caricato,
        # orchestrator mai chiamato
        assert len(rt.processed_times) == 3
        assert rt.session_builder.add_bar.call_count == 3
        rt.orchestrator.on_bar.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════
# T5 — la barra in formazione resta aggiornabile
# ═════════════════════════════════════════════════════════════════════════

class TestT5FormingBarStillUpdatable:
    def test_final_version_of_forming_bar_is_still_processed(self):
        runner = _make_runner()
        rt, new_bars, ts = _rt_after_gap()
        runner._runtimes = {"QQQ": rt}

        _resubscribe_with(runner, rt, new_bars)
        rt.orchestrator.on_bar.reset_mock()

        # T+3 si chiude con valori DEFINITIVI diversi dal parziale, e
        # nasce T+4: ora T+3 e' bars[-2] e deve essere processata.
        final_t3 = _bar(_rth_utc(3), 103.9)
        rt.bars = MagicMock()
        full = new_bars[:-1] + [final_t3, _bar(_rth_utc(4), 104.0)]
        rt.bars.__len__ = lambda s: len(full)
        rt.bars.__iter__ = lambda s: iter(full)
        rt.bars.__getitem__ = lambda s, i: full[i]

        runner._poll_bars_fallback()

        seen = [(c.args[0]["time_ms"], c.args[0]["close"])
                for c in rt.orchestrator.on_bar.call_args_list]
        assert (ts["T+3"], 103.9) in seen, (
            f"la versione definitiva di T+3 non e' arrivata: {seen}")
