"""Session-end and expiry safety policy.

Written after the QQQ trade of 2026-08-26, which exposed three gaps at
once. A 0DTE call was opened at 15:56 ET, four minutes before the close.
Neither stop nor target was reached by 16:00, so no exit was ever sent.
The runner then refused to shut down while a position was open and spun
for six hours until TWS dropped the socket. The option expired in the
money, IBKR auto-exercised it, and the account woke up holding 100
shares of QQQ that MaxBot did not know about.

Every rule here exists to break one link in that chain, and each is a
time comparison with no market opinion in it.

Two different questions, four cutoffs
------------------------------------
The strategy cutoff answers "should MaxBot still be opening trades at
this hour?" The other three answer "is it too late for this to end
safely before the bell?" They are not the same question and must never
be collapsed into one number.

    STRATEGY_ENTRY_CUTOFF   T-60   the trading window closes
    LATE_0DTE_CUTOFF        T-30   safety: assignment risk
    SAFETY_ENTRY_CUTOFF     T-15   safety: last line of defence
    FORCED_EXIT_CUTOFF      T-5    safety: close what is still open

The strategy cutoff is the only one a trader should ever think about.
The three below it are floors: on a normal day they are unreachable
through the standard path, because the strategy cutoff has already
stopped everything. They are kept precisely for the day something goes
wrong above them — a misconfiguration, a bypass, a future caller that
forgets. Defence in depth is worth the redundancy.

None of these is the RTH session itself. AUTOSTART_WINDOW_END, which
happens to also read 14:00 CT, governs when the scheduler may START a
session and is unrelated: it is not imported here and must never be
reused as an entry cutoff.

Why these numbers
-----------------
# Strategy: the trading window. 60 minutes before the close is 15:00 ET
# / 14:00 CT on a normal RTH day — the stated MaxBot window of
# 08:30-14:00 CT. Expressed relative to the session close rather than as
# a wall-clock time so it stays meaningful if the session itself is
# shorter (see the half-day note below).
STRATEGY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE = 60

SAFETY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE = 15
    Measured on the real trades of 2026-08-26, entry to exit ran 2, 3,
    4, 5, 8 and 26 minutes — median under 5. A trade opened at the last
    permitted moment therefore has 10 minutes to resolve on its own
    before the forced exit, which covers the large majority of them.
    Not a market view: just the observed distribution. And a floor, not
    a window — the window is STRATEGY_ENTRY_CUTOFF above.

FORCED_EXIT_MINUTES_BEFORE_CLOSE = 5
    A market exit filled in 0.3 to 4.6 seconds on every trade measured,
    so five minutes is generous by two orders of magnitude, and it keeps
    the order clear of the closing auction.

SESSION_END_GRACE_SECONDS = 120
    How long the runner keeps working after the close for a forced exit
    to confirm. Past it the runner shuts down anyway and says what was
    left unresolved. The whole point of this module is that the runner
    must always reach shutdown; waiting forever is the bug being fixed.

LATE_0DTE_CUTOFF_MINUTES = 30
    An option expiring today stops being a position and becomes an
    exercise risk the moment it is still held at the bell. Thirty
    minutes is deliberately wider than the entry cutoff: a 0DTE contract
    is refused execution well before ordinary contracts are, because the
    consequence of being wrong is assignment rather than a bad price.

These are defaults, not laws — every function takes the thresholds as
arguments so a caller can tighten them.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


# Strategy: the trading window. 60 minutes before the close is 15:00 ET
# / 14:00 CT on a normal RTH day — the stated MaxBot window of
# 08:30-14:00 CT. Expressed relative to the session close rather than as
# a wall-clock time so it stays meaningful if the session itself is
# shorter (see the half-day note below).
STRATEGY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE = 60

SAFETY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE = 15
FORCED_EXIT_MINUTES_BEFORE_CLOSE = 5
SESSION_END_GRACE_SECONDS = 120
LATE_0DTE_CUTOFF_MINUTES = 30

# Reasons an execution can be refused. Persisted and logged verbatim, so
# a blocked trade says why in the journal rather than just not existing.
REASON_STRATEGY_CUTOFF = "STRATEGY_ENTRY_CUTOFF"
REASON_ENTRY_CUTOFF = "SESSION_CLOSE_SAFETY_CUTOFF"
REASON_LATE_0DTE = "LATE_0DTE_CUTOFF"

# Exit reason for a position closed because the session is ending. Not
# TARGET and not STOP: neither was reached, and recording either would
# be a lie the statistics would then repeat.
EXIT_REASON_SESSION_END = "SESSION_END"


def minutes_to_close(now: datetime, session_close: str,
                     market_timezone: str = "America/New_York") -> float | None:
    """Minutes from `now` to today's session close. None if unusable.

    Negative after the close, which callers rely on: "past the cutoff"
    and "past the close" are the same comparison.
    """
    try:
        hh, mm = int(session_close[:2]), int(session_close[3:5])
    except (TypeError, ValueError, IndexError):
        return None
    if now is None:
        return None
    try:
        local = now.astimezone(ZoneInfo(market_timezone))
    except Exception:
        return None
    close = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return (close - local).total_seconds() / 60.0


def strategy_entry_allowed(
    minutes_left: float | None,
    cutoff: int = STRATEGY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE,
) -> bool:
    """False once the trading window has closed for the day.

    This is the rule a trader would state: no new trades after 14:00 CT.
    It stops NEW entries only — a position already open runs its normal
    course to target, stop, or the forced session-end exit, and its R
    probe keeps observing until the bell. Closing at the window edge
    would be a different (and much stronger) rule that nobody asked for.

    An unknown time is permissive, as everywhere in this module: being
    unable to read a clock is its own failure and must look like one
    rather than silently halting the bot.
    """
    if minutes_left is None:
        return True
    return minutes_left > cutoff


def entry_allowed(minutes_left: float | None,
                  cutoff: int = SAFETY_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE) -> bool:
    """False once the session is too close to open anything new.

    An unknown time is treated as allowed: this guard exists to stop a
    late entry, not to halt trading whenever a clock cannot be read.
    Being unable to tell the time is a separate failure and should look
    like one, rather than silently disabling the bot.
    """
    if minutes_left is None:
        return True
    return minutes_left > cutoff


def forced_exit_due(minutes_left: float | None,
                    cutoff: int = FORCED_EXIT_MINUTES_BEFORE_CLOSE) -> bool:
    """True once an open position must be closed for the session end."""
    if minutes_left is None:
        return False
    return minutes_left <= cutoff


def is_zero_dte(expiration: str | None, trading_date: str | None) -> bool:
    """True when the contract expires on the session's own date.

    Both are YYYYMMDD, the format the option chain and the session
    already use. Anything unparseable is not treated as 0DTE — the
    late-0DTE guard must never fire on a contract it cannot identify.
    """
    if not expiration or not trading_date:
        return False
    return str(expiration).strip() == str(trading_date).strip()


def zero_dte_execution_allowed(
    expiration: str | None,
    trading_date: str | None,
    minutes_left: float | None,
    cutoff: int = LATE_0DTE_CUTOFF_MINUTES,
) -> bool:
    """False for a 0DTE contract too close to expiry to hold safely.

    Only 0DTE is gated. A contract expiring on a later date can be held
    overnight without becoming an exercise: it is a position, not a
    deadline.
    """
    if not is_zero_dte(expiration, trading_date):
        return True
    if minutes_left is None:
        return True
    return minutes_left > cutoff


def shutdown_allowed(minutes_left: float | None, has_active: bool,
                     seconds_since_close: float | None,
                     grace: int = SESSION_END_GRACE_SECONDS) -> bool:
    """Whether the runner may stop now.

    Before the close, only when nothing is active. After it, when
    nothing is active OR the grace period has elapsed — the runner
    always gets to shut down, so the session log is written, the R
    probes are closed, and whatever is still unresolved is reported
    instead of being spun on until the broker hangs up.
    """
    if minutes_left is None:
        return not has_active
    if minutes_left > 0:
        return not has_active
    if not has_active:
        return True
    return (seconds_since_close or 0) >= grace


# ── Half-days ────────────────────────────────────────────────────────────────
#
# Every cutoff here is relative to the session close, so a shorter
# session shifts all four automatically: a 13:00 ET close puts the
# strategy cutoff at 12:00 ET and the forced exit at 12:55 ET, with no
# special case and no extra code.
#
# What this module CANNOT do is notice that today is a half-day. There
# is no market calendar in the codebase — `session_close` is a plain
# string handed to MaxBotRunner, defaulting to "16:00". On an early
# close, started with that default, every cutoff here would be an hour
# late and the forced exit would fire after the market had already shut.
#
# The policy is half-day-correct; the configuration feeding it is not
# half-day-aware. Detecting early closes is a separate problem and is
# deliberately not solved by pretending here.
