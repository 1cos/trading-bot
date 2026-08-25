"""Auto-start della trading session, finestra 08:00-14:00 America/Chicago.

Regola: una sessione al giorno nei giorni feriali, avviata in un
qualunque momento della finestra se non ce n'e' gia' una attiva. Un
tentativo fallito (TWS spento) e' ritentabile ogni 5 minuti; una
sessione che e' davvero partita non viene mai riavviata, nemmeno dopo
essere finita normalmente.

Ogni test inietta l'orario: nessuno sleep reale, nessuna connessione
IBKR, `ctrl.start()` sempre spiato.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from trading_lab.live.autostart import (
    AUTOSTART_RETRY_SECS,
    AUTOSTART_TZ,
    AUTOSTART_WINDOW_END,
    AUTOSTART_WINDOW_START,
    AutoStartConfig,
    AutoStartScheduler,
)
from trading_lab.live.control_api import BotState


CT = ZoneInfo("America/Chicago")

# 2026-08-19 mercoledi, 20 giovedi, 22 sabato, 23 domenica.
WED = dict(y=2026, mo=8, d=19)
THU = dict(y=2026, mo=8, d=20)
SAT = dict(y=2026, mo=8, d=22)
SUN = dict(y=2026, mo=8, d=23)


def ct(y=2026, mo=8, d=19, h=8, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=CT)


class FakeController:
    """Spia su start(). Non crea alcun runner, non tocca IBKR."""

    def __init__(self, state=BotState.STOPPED, raises=None,
                 state_after_start=BotState.STARTING):
        self._state = state
        self.calls = []
        self._raises = raises
        self._after = state_after_start

    @property
    def state(self):
        return self._state

    def start(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        self._state = self._after


def _sched(ctrl=None, config=None):
    ctrl = ctrl or FakeController()
    cfg = config or AutoStartConfig(
        symbols=["QQQ", "SPY"], direction="BOTH",
        execution_mode="PAPER_EXECUTE", trade_limits_enabled=False,
    )
    return AutoStartScheduler(ctrl, cfg), ctrl


# ═════════════════════════════════════════════════════════════════════════
# T1-T7 — finestra 08:00 <= now < 14:00
# ═════════════════════════════════════════════════════════════════════════

class TestWindow:
    def test_t1_0759_no_start(self):
        s, c = _sched()
        s.tick(now=ct(**WED, h=7, mi=59, s=59))
        assert c.calls == []

    def test_t2_0800_starts(self):
        s, c = _sched()
        s.tick(now=ct(**WED, h=8, mi=0, s=0))
        assert len(c.calls) == 1

    def test_t3_0830_fresh_process_starts(self):
        s, c = _sched()
        s.tick(now=ct(**WED, h=8, mi=30))
        assert len(c.calls) == 1

    def test_t4_1000_fresh_process_starts(self):
        s, c = _sched()
        s.tick(now=ct(**WED, h=10, mi=0))
        assert len(c.calls) == 1

    def test_t5_1359_starts(self):
        s, c = _sched()
        s.tick(now=ct(**WED, h=13, mi=59, s=59))
        assert len(c.calls) == 1

    def test_t6_1400_exact_does_not_start(self):
        """Confine esplicito: 14:00:00 e' FUORI (finestra semiaperta)."""
        s, c = _sched()
        s.tick(now=ct(**WED, h=14, mi=0, s=0))
        assert c.calls == []

    def test_t7_1500_no_start(self):
        s, c = _sched()
        s.tick(now=ct(**WED, h=15))
        assert c.calls == []

    def test_window_constants(self):
        assert (AUTOSTART_WINDOW_START.hour, AUTOSTART_WINDOW_START.minute) == (8, 0)
        assert (AUTOSTART_WINDOW_END.hour, AUTOSTART_WINDOW_END.minute) == (14, 0)

    def test_midwindow_hours_all_allowed(self):
        """La vecchia regola 08:15 non esiste piu'."""
        for h, mi in ((8, 16), (9, 0), (11, 30), (12, 45), (13, 0)):
            s, c = _sched()
            s.tick(now=ct(**WED, h=h, mi=mi))
            assert len(c.calls) == 1, f"{h:02d}:{mi:02d} avrebbe dovuto avviare"


# ═════════════════════════════════════════════════════════════════════════
# T8 / T9 — sessione gia' attiva
# ═════════════════════════════════════════════════════════════════════════

class TestAlreadyActive:
    def test_t8_running_no_second_runner(self):
        s, c = _sched(FakeController(state=BotState.RUNNING))
        s.tick(now=ct(**WED, h=8))
        assert c.calls == []
        assert s.started_date == ct(**WED).date()

    def test_t9_starting_no_second_attempt(self):
        ctrl = FakeController(state=BotState.STARTING)
        s, c = _sched(ctrl)
        for mi in (0, 1, 6, 30):
            s.tick(now=ct(**WED, h=8, mi=mi))
        assert c.calls == []
        # STARTING non e' successo: la giornata non e' ancora risolta
        assert s.started_date is None

    def test_running_session_is_never_stopped(self):
        ctrl = MagicMock()
        ctrl.state = BotState.RUNNING
        cfg = AutoStartConfig(symbols=["QQQ"])
        AutoStartScheduler(ctrl, cfg).tick(now=ct(**WED, h=10))
        ctrl.start.assert_not_called()
        ctrl.stop.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════
# T10-T13 — retry controllato
# ═════════════════════════════════════════════════════════════════════════

class TestRetry:
    def _failing(self):
        return FakeController(state_after_start=BotState.ERROR)

    def test_t10_no_retry_before_five_minutes(self):
        ctrl = self._failing()
        s, c = _sched(ctrl)
        s.tick(now=ct(**WED, h=9, mi=0))
        assert len(c.calls) == 1
        for sec in (30, 60, 120, 299):
            s.tick(now=ct(**WED, h=9, mi=0) + timedelta(seconds=sec))
        assert len(c.calls) == 1

    def test_t11_retry_after_five_minutes(self):
        ctrl = self._failing()
        s, c = _sched(ctrl)
        s.tick(now=ct(**WED, h=9, mi=0))
        s.tick(now=ct(**WED, h=9, mi=5))
        assert len(c.calls) == 2

    def test_t12_one_attempt_per_five_minute_interval(self):
        ctrl = self._failing()
        s, c = _sched(ctrl)
        # tick fitti da 09:00 a 09:12
        t0 = ct(**WED, h=9, mi=0)
        for sec in range(0, 12 * 60 + 1, 20):
            s.tick(now=t0 + timedelta(seconds=sec))
        assert len(c.calls) == 3, f"attesi 3 tentativi (09:00/09:05/09:10), ottenuti {len(c.calls)}"

    def test_t13_error_at_1358_no_retry_at_1403(self):
        ctrl = self._failing()
        s, c = _sched(ctrl)
        s.tick(now=ct(**WED, h=13, mi=58))
        assert len(c.calls) == 1
        s.tick(now=ct(**WED, h=14, mi=3))
        assert len(c.calls) == 1, "nessun tentativo fuori finestra"

    def test_synchronous_start_failure_is_retryable(self):
        ctrl = FakeController(raises=RuntimeError("TWS non raggiungibile"))
        s, c = _sched(ctrl)
        s.tick(now=ct(**WED, h=9))
        assert len(c.calls) == 1
        s.tick(now=ct(**WED, h=9, mi=2))
        assert len(c.calls) == 1
        s.tick(now=ct(**WED, h=9, mi=5))
        assert len(c.calls) == 2

    def test_retry_cadence_constant(self):
        assert AUTOSTART_RETRY_SECS == 300


# ═════════════════════════════════════════════════════════════════════════
# T14 / T15 — successo e fine sessione normale
# ═════════════════════════════════════════════════════════════════════════

class TestSuccessAndNormalEnd:
    def test_t14_running_marks_the_day_started(self):
        ctrl = FakeController()
        s, c = _sched(ctrl)
        s.tick(now=ct(**WED, h=8))          # -> STARTING
        assert len(c.calls) == 1
        assert s.started_date is None

        ctrl._state = BotState.RUNNING       # connessione riuscita
        s.tick(now=ct(**WED, h=8, mi=1))
        assert s.started_date == ct(**WED).date()
        assert len(c.calls) == 1

    def test_t15_normal_completion_is_not_restarted(self):
        """DONE_FOR_DAY / max trades / tutti i simboli done: la sessione
        finisce e il controller torna STOPPED. Non va riavviata."""
        ctrl = FakeController()
        s, c = _sched(ctrl)
        s.tick(now=ct(**WED, h=8))
        ctrl._state = BotState.RUNNING
        s.tick(now=ct(**WED, h=8, mi=5))     # osserva RUNNING
        ctrl._state = BotState.STOPPED       # sessione conclusa alle 11:00

        for h, mi in ((11, 0), (11, 30), (12, 0), (13, 59)):
            s.tick(now=ct(**WED, h=h, mi=mi))
        assert len(c.calls) == 1, "una giornata gia' avviata non si riapre"

    def test_manual_session_also_satisfies_the_day(self):
        """Se l'utente avvia a mano, lo scheduler non ne apre una seconda."""
        ctrl = FakeController(state=BotState.RUNNING)
        s, c = _sched(ctrl)
        s.tick(now=ct(**WED, h=8, mi=30))
        ctrl._state = BotState.STOPPED
        s.tick(now=ct(**WED, h=12))
        assert c.calls == []


# ═════════════════════════════════════════════════════════════════════════
# T16 — giorno successivo
# ═════════════════════════════════════════════════════════════════════════

class TestNextDay:
    def test_t16_next_weekday_starts_again(self):
        ctrl = FakeController()
        s, c = _sched(ctrl)
        s.tick(now=ct(**WED, h=8))
        ctrl._state = BotState.RUNNING
        s.tick(now=ct(**WED, h=8, mi=5))
        ctrl._state = BotState.STOPPED
        s.tick(now=ct(**THU, h=8))
        assert len(c.calls) == 2

    def test_retry_gate_resets_across_days(self):
        ctrl = FakeController(state_after_start=BotState.ERROR)
        s, c = _sched(ctrl)
        s.tick(now=ct(**WED, h=13, mi=59))   # fallisce a fine finestra
        assert len(c.calls) == 1
        s.tick(now=ct(**THU, h=8))           # nuovo giorno, subito
        assert len(c.calls) == 2

    def test_session_still_running_next_morning_not_duplicated(self):
        ctrl = FakeController()
        s, c = _sched(ctrl)
        s.tick(now=ct(**WED, h=8))
        ctrl._state = BotState.RUNNING
        s.tick(now=ct(**WED, h=8, mi=5))
        s.tick(now=ct(**THU, h=8))           # mai fermata
        assert len(c.calls) == 1
        assert s.started_date == ct(**THU).date()


# ═════════════════════════════════════════════════════════════════════════
# T17 / T18 — restart del control server
# ═════════════════════════════════════════════════════════════════════════

class TestRestart:
    def test_t17_fresh_scheduler_at_1000_starts(self):
        s, c = _sched()                      # nessuna memoria
        s.tick(now=ct(**WED, h=10))
        assert len(c.calls) == 1

    def test_t18_fresh_scheduler_at_1430_does_not_start(self):
        s, c = _sched()
        s.tick(now=ct(**WED, h=14, mi=30))
        assert c.calls == []

    def test_fresh_scheduler_sees_running_session(self):
        """Restart alle 10:00 con un runner ancora attivo e visibile:
        il guard normale impedisce il duplicato."""
        s, c = _sched(FakeController(state=BotState.RUNNING))
        s.tick(now=ct(**WED, h=10))
        assert c.calls == []


# ═════════════════════════════════════════════════════════════════════════
# T19 — weekend
# ═════════════════════════════════════════════════════════════════════════

class TestWeekend:
    def test_t19_saturday_and_sunday_zero(self):
        for day in (SAT, SUN):
            s, c = _sched()
            for h in (8, 10, 13):
                s.tick(now=ct(**day, h=h))
            assert c.calls == []

    def test_weekend_does_not_consume_state(self):
        s, c = _sched()
        s.tick(now=ct(**SAT, h=10))
        assert s.started_date is None
        assert s.last_attempt_at is None


# ═════════════════════════════════════════════════════════════════════════
# T20 — timezone
# ═════════════════════════════════════════════════════════════════════════

class TestTimezone:
    def test_t20_declares_chicago(self):
        assert AUTOSTART_TZ == ZoneInfo("America/Chicago")

    def test_utc_input_converted(self):
        from datetime import timezone as _tz
        s, c = _sched()
        utc = datetime(2026, 8, 19, 15, 0, 0, tzinfo=_tz.utc)   # 10:00 CT
        assert utc.astimezone(CT).hour == 10
        s.tick(now=utc)
        assert len(c.calls) == 1

    def test_naive_read_as_chicago(self):
        s, c = _sched()
        s.tick(now=datetime(2026, 8, 19, 10, 0, 0))
        assert len(c.calls) == 1

    def test_new_york_1430_is_1330_chicago(self):
        """14:30 a New York sono le 13:30 CT: dentro finestra."""
        s, c = _sched()
        ny = datetime(2026, 8, 19, 14, 30, tzinfo=ZoneInfo("America/New_York"))
        s.tick(now=ny)
        assert len(c.calls) == 1

    def test_new_york_0730_is_0630_chicago(self):
        s, c = _sched()
        ny = datetime(2026, 8, 19, 7, 30, tzinfo=ZoneInfo("America/New_York"))
        s.tick(now=ny)
        assert c.calls == []

    def test_process_timezone_irrelevant(self, monkeypatch):
        import time as _time
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        try:
            _time.tzset()
        except AttributeError:
            pass
        s, c = _sched()
        s.tick(now=ct(**WED, h=10))
        assert len(c.calls) == 1
        s2, c2 = _sched()
        s2.tick(now=ct(**WED, h=15))
        assert c2.calls == []


# ═════════════════════════════════════════════════════════════════════════
# T21 / T22 — endpoint manuale e assenza di runner reali
# ═════════════════════════════════════════════════════════════════════════

class TestManualAndIsolation:
    def test_t21_manual_start_unchanged(self, tmp_path):
        from trading_lab.live.control_api import MaxBotController, create_app
        ctrl = MaxBotController(trade_state_dir=tmp_path)
        ctrl.start = MagicMock()
        app = create_app(ctrl)
        app.config["TESTING"] = True
        r = app.test_client().post("/api/bot/start", json={
            "symbols": ["QQQ"], "direction": "LONG",
            "execution_mode": "OBSERVE_ONLY"})
        assert r.status_code == 200
        assert r.get_json()["status"] == "starting"
        ctrl.start.assert_called_once()

    def test_t21_manual_start_still_rejects_live(self, tmp_path):
        from trading_lab.live.control_api import MaxBotController, create_app
        ctrl = MaxBotController(trade_state_dir=tmp_path)
        app = create_app(ctrl)
        app.config["TESTING"] = True
        r = app.test_client().post("/api/bot/start", json={
            "symbols": ["QQQ"], "execution_mode": "LIVE"})
        assert r.status_code == 400

    def test_create_app_spawns_no_thread(self, tmp_path):
        import threading
        from trading_lab.live.control_api import MaxBotController, create_app
        before = threading.active_count()
        create_app(MaxBotController(trade_state_dir=tmp_path))
        assert threading.active_count() == before

    def test_t22_scheduler_only_calls_start(self):
        ctrl = MagicMock()
        ctrl.state = BotState.STOPPED
        AutoStartScheduler(ctrl, AutoStartConfig(symbols=["QQQ"])).tick(
            now=ct(**WED, h=10))
        assert ctrl.start.call_count == 1
        ctrl.stop.assert_not_called()

    def test_t22_config_passed_through(self):
        s, c = _sched()
        s.tick(now=ct(**WED, h=10))
        assert c.calls[0] == {
            "symbols": ["QQQ", "SPY"], "direction": "BOTH",
            "execution_mode": "PAPER_EXECUTE", "trade_limits_enabled": False,
        }
