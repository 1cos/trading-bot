"""Automatic start of the trading session at 08:00 America/Chicago.

Lives inside the control server process, next to the controller it
drives. It calls MaxBotController.start() directly — never an HTTP
request to itself — so it inherits the endpoint's lock, validation,
duplicate guard and runner lifecycle instead of re-implementing them.

Timezone is declared, not inherited. The Mac happens to be on
America/Chicago today, but launchd cannot express a timezone and the
host's zone can change; a schedule that silently follows it would fire
at the wrong hour with nothing to show for it. Every decision here goes
through ZoneInfo("America/Chicago") explicitly.

The decision is a pure function of (now, scheduler memory, controller
state), so it is testable without sleeping and without a broker.

Two facts are tracked separately, because conflating them is what makes
a scheduler either miss a session or start a second one:

    _started_date        a session was observed RUNNING today
    _last_attempt_at     when start() was last attempted

"Attempted" is not "succeeded". ctrl.start() returns as soon as the
worker thread is spawned — the IBKR connection has not happened yet —
so an attempt that ends in ERROR must be retryable, while a session
that actually came up must never be restarted, not even after it ends
normally at DONE_FOR_DAY.

Deliberately NOT handled: market holidays. No calendar exists anywhere
in this project, and adding one is a dependency decision of its own. On
a holiday the session starts and simply finds no bars — the same thing
that happens today when START is pressed by hand.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

log = logging.getLogger("maxbot.autostart")


# The schedule's own timezone. Never the host's.
AUTOSTART_TZ = ZoneInfo("America/Chicago")

# Auto-start window, half-open: [08:00:00, 14:00:00) CT.
#
# The whole window is live, not just the opening minutes: a control
# server that comes up at 10:00 with no session running should start
# one, because a missing session is the expensive failure. 14:00 is
# exclusive — at 14:00:00 sharp there is not enough session left to be
# worth opening, so a start there would be worse than none.
AUTOSTART_WINDOW_START = time(8, 0, 0)
AUTOSTART_WINDOW_END = time(14, 0, 0)

# Kept as the canonical "normal" start time for logging/config display.
AUTOSTART_TIME = AUTOSTART_WINDOW_START

# Minimum gap between two start attempts. A failed attempt (TWS down,
# IBKR refusing) is worth retrying, but not every 20 seconds: that
# would hammer a broker that is already unhappy and flood the log.
AUTOSTART_RETRY_SECS = 300  # 5 minutes

# How often the background thread re-evaluates. Nothing here needs
# second precision; the window is six hours wide and the retry gate is
# five minutes.
AUTOSTART_POLL_SECS = 20

# Controller states that mean "a session is already up". Matches
# MaxBotController.start()'s own guard.
_ACTIVE_STATES = ("RUNNING", "STARTING")


@dataclass(frozen=True)
class AutoStartConfig:
    """The session the scheduler will start.

    There is no canonical config source in the project today: the
    watchlist lives only as the value= of an <input> in dashboard.html,
    and the endpoint's own defaults (empty symbols, OBSERVE_ONLY) are
    not what anyone actually starts. This carries the same session a
    human would start by hand, and run_server() fills it from
    MAXBOT_AUTOSTART_* environment variables so there is one place to
    change it.
    """

    symbols: list[str]
    direction: str = "BOTH"
    execution_mode: str = "OBSERVE_ONLY"
    trade_limits_enabled: bool = False

    def as_start_kwargs(self) -> dict:
        return {
            "symbols": list(self.symbols),
            "direction": self.direction,
            "execution_mode": self.execution_mode,
            "trade_limits_enabled": self.trade_limits_enabled,
        }


def to_chicago(now: datetime | None = None) -> datetime:
    """Coerce any instant to America/Chicago.

    A naive datetime is read AS Chicago (that is what a caller passing
    a wall-clock time means); an aware one is converted, so 13:00 UTC
    correctly becomes 08:00 CT.
    """
    if now is None:
        return datetime.now(AUTOSTART_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=AUTOSTART_TZ)
    return now.astimezone(AUTOSTART_TZ)


def in_autostart_window(now_ct: datetime) -> bool:
    """True inside [08:00:00, 14:00:00) CT — end exclusive."""
    t = now_ct.timetz().replace(tzinfo=None)
    return AUTOSTART_WINDOW_START <= t < AUTOSTART_WINDOW_END


def is_trading_weekday(now_ct: datetime) -> bool:
    """Monday–Friday. Market holidays are out of scope (see module doc)."""
    return now_ct.weekday() < 5


class AutoStartScheduler:
    """Starts the session once per trading weekday, inside the window.

    Memory is in-process only. That is honest rather than limiting: a
    fresh process cannot know whether today's session already ran, and
    the window is what bounds the consequence — a restart at 10:00
    starts a session, one at 14:30 does not, memory or no memory.
    """

    def __init__(self, controller, config: AutoStartConfig):
        self._ctrl = controller
        self._config = config
        # A session was OBSERVED RUNNING on this date. Set once; it is
        # what stops a normally-finished session (DONE_FOR_DAY, max
        # trades, all symbols done) from being restarted at 11:00.
        self._started_date: date | None = None
        # When start() was last attempted, and on which date, so the
        # 5-minute gate resets cleanly at midnight.
        self._last_attempt_at: datetime | None = None
        self._attempt_date: date | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def started_date(self) -> date | None:
        """Date whose session was seen RUNNING. No further auto-start."""
        return self._started_date

    @property
    def last_attempt_at(self) -> datetime | None:
        """Last start() attempt (CT). Gates the retry, nothing else."""
        return self._last_attempt_at

    def _retry_ready(self, now_ct: datetime) -> bool:
        if self._attempt_date != now_ct.date() or self._last_attempt_at is None:
            return True                      # first attempt of the day
        elapsed = (now_ct - self._last_attempt_at).total_seconds()
        return elapsed >= AUTOSTART_RETRY_SECS

    def tick(self, now: datetime | None = None) -> bool:
        """Evaluate the schedule once. True if start() was called.

        Safe to call as often as wanted: every step below is a guard,
        and only the last one has an effect.
        """
        now_ct = to_chicago(now)
        today = now_ct.date()

        with self._lock:
            if not is_trading_weekday(now_ct):
                return False
            if not in_autostart_window(now_ct):
                # Quiet by design: true on most ticks of the day.
                return False
            if self._started_date == today:
                # A session already came up today. If it has since
                # ended, it ended for a reason — DONE_FOR_DAY, max
                # trades, session close — and restarting it would open
                # a second trading day inside one calendar day.
                return False

            state = str(getattr(self._ctrl, "state", "") or "")

            if state == "RUNNING":
                # The session is up: today is settled, whether we
                # started it or a human did.
                self._started_date = today
                log.info(f"AUTOSTART satisfied {today} — session RUNNING")
                return False

            if state == "STARTING":
                # start() has been called and the runner is still
                # connecting. Not a success yet, and definitely not a
                # reason to try again.
                return False

            # STOPPED or ERROR — a session could be started. ERROR means
            # a previous attempt failed (TWS down, IBKR refused); it is
            # worth retrying, but on the 5-minute gate, not every tick.
            if not self._retry_ready(now_ct):
                return False

            first = self._attempt_date != today
            self._attempt_date = today
            self._last_attempt_at = now_ct
            kind = "start" if first else "retry"

            try:
                self._ctrl.start(**self._config.as_start_kwargs())
            except Exception as e:
                log.error(
                    f"AUTOSTART {kind} failed {today} {now_ct:%H:%M:%S} CT: "
                    f"{e} — next attempt in "
                    f"{AUTOSTART_RETRY_SECS // 60}m if still in window"
                )
                return False

            log.info(
                f"AUTOSTART {kind} {today} {now_ct:%H:%M:%S} CT — "
                f"{len(self._config.symbols)} symbols, "
                f"{self._config.execution_mode}"
            )
            return True

    # ── Background thread ────────────────────────────────────────────

    def start_background(self) -> None:
        """Run tick() every AUTOSTART_POLL_SECS in a daemon thread.

        Not called by create_app(): tests and any embedding of the app
        must stay free of stray threads and must never start a session
        as a side effect of importing. run_server() starts it.
        """
        if self._thread is not None:
            return
        log.info(
            f"AUTOSTART armed — {AUTOSTART_WINDOW_START:%H:%M}"
            f"-{AUTOSTART_WINDOW_END:%H:%M} America/Chicago, Mon-Fri, "
            f"retry every {AUTOSTART_RETRY_SECS // 60}m, "
            f"{self._config.execution_mode}"
        )
        self._thread = threading.Thread(
            target=self._loop, name="maxbot-autostart", daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(AUTOSTART_POLL_SECS):
            try:
                self.tick()
            except Exception as e:
                # A scheduling bug must never take down the control
                # server; the dashboard and manual START stay usable.
                log.error(f"AUTOSTART tick error: {e}")
