"""Canonical defaults for starting a MaxBot session.

One definition, two consumers: the manual START form in the PWA and the
08:00 CT auto-start scheduler. Before this module they were two
independent copies — the watchlist lived as the value= of an <input> in
dashboard.html and again as a constant in control_api.py. They happened
to agree, which is exactly the failure mode: editing one and not the
other produces a manual START and an auto-start that quietly trade
different books.

Server-side on purpose. The dashboard is a static file with no build
step, so a client-side constant can never be the source of truth for
something the server also needs.

This module holds values only. It does not validate them and does not
start anything — MaxBotController.start() remains the single place
where a session is validated and created.
"""

from __future__ import annotations

# The watchlist a session starts with. Order is preserved end to end:
# it is the subscription order in _subscribe_all(), so changing it
# changes which symbol gets the first historical request.
DEFAULT_SYMBOLS: tuple[str, ...] = (
    "SPY", "QQQ", "AAPL", "TSLA", "NVDA", "AMD", "AMZN", "TSLL", "NFLX",
    "GOOGL", "SOFI", "META", "MU", "INTC", "SNDK", "PLTR", "MSFT",
)

DEFAULT_DIRECTION = "BOTH"

# PAPER_EXECUTE — MaxBot is paper-only and verifies the account before
# submitting anything (verify_paper_account). LIVE is rejected by
# MaxBotController.start().
DEFAULT_EXECUTION_MODE = "PAPER_EXECUTE"

# False means "no daily trade cap" (DailyTradeManager(unlimited=True)).
DEFAULT_TRADE_LIMITS_ENABLED = False


def start_config_defaults() -> dict:
    """The default session, as the JSON the PWA and the scheduler use.

    A fresh dict with a fresh symbol list every call, so a caller that
    mutates the result cannot corrupt the defaults for everyone else.
    """
    return {
        "symbols": list(DEFAULT_SYMBOLS),
        "direction": DEFAULT_DIRECTION,
        "execution_mode": DEFAULT_EXECUTION_MODE,
        "trade_limits_enabled": DEFAULT_TRADE_LIMITS_ENABLED,
    }
