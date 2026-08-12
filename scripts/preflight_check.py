#!/usr/bin/env python3
"""MaxBot v0.1 IBKR Paper Preflight Check.

Run this on the Mac mini with TWS Paper already running.

Usage:
    cd ~/trading_bot
    PYTHONPATH=backend/src python scripts/preflight_check.py

Verifies everything needed for MaxBot Paper execution WITHOUT
submitting any orders.

ENTRY ORDERS SUBMITTED = 0
EXIT ORDERS SUBMITTED  = 0
"""

import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ── Python version guard ─────────────────────────────────────────────────────
if sys.version_info >= (3, 14):
    print(f"\n  ❌ MaxBot requires Python 3.11–3.13 for ib_insync.")
    print(f"     Current: Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print(f"\n  Fix: recreate venv with Python 3.12:")
    print(f"     brew install python@3.12")
    print(f"     mv venv venv_backup")
    print(f"     /opt/homebrew/bin/python3.12 -m venv venv")
    print(f"     source venv/bin/activate")
    print(f"     pip install -r requirements.txt")
    print(f"     pip install -e backend/\n")
    sys.exit(1)

# ── Configuration ────────────────────────────────────────────────────────────

IB_HOST = "127.0.0.1"
IB_PORT = 7497
CLIENT_ID = 99  # use distinct ID to avoid conflicts

WATCHLIST = ["QQQ", "SPY", "NVDA", "AMD", "GOOGL", "TSLA", "AMZN", "META", "AAPL"]
OPTION_TEST_SYMBOLS = ["QQQ", "SPY", "NVDA", "TSLA"]
ET = ZoneInfo("America/New_York")

# ── Counters ─────────────────────────────────────────────────────────────────

orders_submitted = 0
blockers = []

def ok(msg): print(f"  ✅ {msg}")
def fail(msg): print(f"  ❌ {msg}"); blockers.append(msg)
def warn(msg): print(f"  ⚠️  {msg}")
def info(msg): print(f"  ℹ️  {msg}")

# ══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 60)
print("  MAXBOT v0.1 — IBKR PAPER PREFLIGHT CHECK")
print("=" * 60)
print()

# ── 1. Imports ───────────────────────────────────────────────────────────────
print("1. IMPORTS")
try:
    from ib_insync import IB, Stock, Option
    ok("ib_insync")
except ImportError as e:
    fail(f"ib_insync import failed: {e}")
    sys.exit(1)

try:
    from trading_lab.live.control_api import create_app
    from trading_lab.live.bot_runner import verify_paper_account
    from trading_lab.live.option_selector import (
        OptionContractSelector, select_expiration, select_strike,
        _pick_chain, _fallback_strikes,
    )
    ok("trading_lab.live modules")
except ImportError as e:
    fail(f"trading_lab import failed: {e}")
    sys.exit(1)

# ── 2. TWS Connection ───────────────────────────────────────────────────────
print()
print("2. TWS CONNECTION")
ib = IB()
try:
    ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID)
    ok(f"Connected to {IB_HOST}:{IB_PORT}")
except Exception as e:
    fail(f"Connection failed: {e}")
    print()
    print("RESULT: BLOCKED — cannot connect to TWS")
    print("Ensure TWS Paper is running with API enabled")
    sys.exit(1)

# ── 3. Paper Verification ───────────────────────────────────────────────────
print()
print("3. PAPER VERIFICATION")
try:
    paper_acct = verify_paper_account(ib)
    ok(f"Paper account detected: {paper_acct[:3]}***")
except RuntimeError as e:
    fail(f"Paper verification failed: {e}")
    ib.disconnect()
    print()
    print("RESULT: BLOCKED — not a Paper account")
    sys.exit(1)

accounts = ib.managedAccounts()
info(f"Managed accounts: {len(accounts)}")

# ── 4. Watchlist Qualification ───────────────────────────────────────────────
print()
print("4. WATCHLIST QUALIFICATION")
print(f"   {'Symbol':<8} {'Status':<12} {'conId'}")
print(f"   {'─'*8} {'─'*12} {'─'*10}")

qualified_stocks = {}
for sym in WATCHLIST:
    try:
        stock = Stock(sym, "SMART", "USD")
        result = ib.qualifyContracts(stock)
        if result:
            qualified_stocks[sym] = stock
            print(f"   {sym:<8} {'QUALIFIED':<12} {stock.conId}")
        else:
            print(f"   {sym:<8} {'FAILED':<12} —")
            warn(f"{sym} qualification returned empty")
    except Exception as e:
        print(f"   {sym:<8} {'ERROR':<12} {e}")
        warn(f"{sym}: {e}")

qual_count = len(qualified_stocks)
if qual_count == 0:
    fail("No symbols qualified")
    ib.disconnect()
    sys.exit(1)
else:
    ok(f"{qual_count}/{len(WATCHLIST)} symbols qualified")

# ── 5. Real 1-Minute Data ───────────────────────────────────────────────────
print()
print("5. REAL 1-MINUTE DATA")
print(f"   {'Symbol':<8} {'Bars':<6} {'First Bar':<20} {'Last Bar':<20} {'Status'}")
print(f"   {'─'*8} {'─'*6} {'─'*20} {'─'*20} {'─'*10}")

bar_subscriptions = {}
for sym, stock in list(qualified_stocks.items())[:5]:  # test first 5
    try:
        bars = ib.reqHistoricalData(
            stock,
            endDateTime="",
            durationStr="1 D",
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
            keepUpToDate=True,
        )
        bar_subscriptions[sym] = bars
        if bars and len(bars) > 0:
            first = bars[0].date
            last = bars[-1].date
            print(f"   {sym:<8} {len(bars):<6} {str(first):<20} {str(last):<20} OK")
        else:
            print(f"   {sym:<8} {'0':<6} {'—':<20} {'—':<20} EMPTY")
            warn(f"{sym}: no bars returned")
    except Exception as e:
        print(f"   {sym:<8} {'—':<6} {'—':<20} {'—':<20} ERROR: {e}")
        warn(f"{sym} bars: {e}")

if bar_subscriptions:
    ok(f"{len(bar_subscriptions)} symbols receiving 1m data")
else:
    fail("No 1m data received for any symbol")

# ── 6. Completed-Bar Semantics ──────────────────────────────────────────────
print()
print("6. COMPLETED-BAR SEMANTICS")
now_et = datetime.now(ET)
if 9 <= now_et.hour < 16 and now_et.weekday() < 5:
    info("Market appears open — can observe live bar updates")
    info("Waiting 5 seconds for potential bar update...")
    ib.sleep(5)
    for sym, bars in list(bar_subscriptions.items())[:1]:
        if len(bars) >= 2:
            ok(f"{sym}: {len(bars)} bars, last={bars[-1].date} (potentially forming)")
        else:
            info(f"{sym}: only {len(bars)} bars")
else:
    info("Market closed — completed-bar callback NOT OBSERVABLE OUTSIDE LIVE SESSION")
    info("Bar callback rule (bars[-2] = completed) verified in unit tests")

# ── 7. Option Chain Preflight (RAW + PRODUCTION SELECTION) ───────────────────
print()
print("7. OPTION CHAIN PREFLIGHT")

# Trading date: use market timezone (ET), not UTC
now_et = datetime.now(ET)
trading_date_str = now_et.strftime("%Y%m%d")
trading_date_display = now_et.strftime("%Y-%m-%d")
print(f"   Local time:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"   Market time:   {now_et.strftime('%Y-%m-%d %H:%M:%S')} ET")
print(f"   Trading date:  {trading_date_display} ({trading_date_str})")
print()

raw_chains_data = {}  # sym -> list of raw chain dicts
selected_chains = {}  # sym -> production-selected chain dict

for sym in OPTION_TEST_SYMBOLS:
    if sym not in qualified_stocks:
        print(f"   {sym}: SKIP — not qualified")
        continue
    stock = qualified_stocks[sym]

    # Get underlying price
    und_price = None
    if sym in bar_subscriptions and bar_subscriptions[sym]:
        und_price = float(bar_subscriptions[sym][-1].close)
    if und_price is None:
        info(f"{sym}: no underlying price, skipping chain test")
        continue

    try:
        chains = ib.reqSecDefOptParams(sym, "", "STK", stock.conId)
    except Exception as e:
        warn(f"{sym} chain request: {e}")
        continue

    if not chains:
        warn(f"{sym}: no option chains returned")
        continue

    # Show RAW chains
    print(f"   {sym} (underlying={und_price:.2f})")
    print(f"     RAW CHAINS:")
    raw_list = []
    for c in chains:
        raw_list.append({
            "exchange": c.exchange, "tradingClass": c.tradingClass,
            "multiplier": c.multiplier,
            "expirations": len(list(c.expirations)),
            "strikes": len(list(c.strikes)),
        })
        if c.exchange == "SMART" or c.tradingClass.upper() == sym.upper():
            print(f"       {c.tradingClass:<8} exch={c.exchange:<6} mult={c.multiplier:<4} "
                  f"exp={len(list(c.expirations)):<4} strikes={len(list(c.strikes))}")
    raw_chains_data[sym] = raw_list

    # PRODUCTION selection via _pick_chain
    selected = _pick_chain(chains, sym, und_price, trading_date_str, "SMART")
    if selected:
        selected_chains[sym] = selected
        print(f"     PRODUCTION SELECTED:")
        print(f"       {selected['tradingClass']:<8} exch={selected['exchange']:<6} "
              f"mult={selected['multiplier']:<4} "
              f"exp={len(selected['expirations']):<4} "
              f"strikes={len(selected['strikes'])}")
    else:
        print(f"     PRODUCTION SELECTED: NONE — no valid standard chain")
        warn(f"{sym}: production _pick_chain returned None")

if selected_chains:
    ok(f"Production chains selected for {len(selected_chains)}/{len(OPTION_TEST_SYMBOLS)} symbols")
else:
    fail("No production chains selected for any symbol")

# ── 8. Production Option Selection ───────────────────────────────────────────
print()
print("8. PRODUCTION OPTION SELECTION")
print("   Using OptionContractSelector (same path as PAPER_EXECUTE)")
print()

selector = OptionContractSelector(ib)
selected_options = {}
qualified_options = {}

for sym in OPTION_TEST_SYMBOLS:
    if sym not in selected_chains:
        continue

    und_price = float(bar_subscriptions[sym][-1].close) if sym in bar_subscriptions else None
    if und_price is None:
        continue

    for right, label in [("C", "CALL"), ("P", "PUT")]:
        try:
            result = selector.select(
                underlying_symbol=sym, right=right,
                underlying_price=und_price, trading_date=trading_date_str,
                fetch_market_data=True,
            )

            # DTE calculation with real dates
            from datetime import datetime as dt_cls
            exp_date = dt_cls.strptime(result.expiration, "%Y%m%d").date()
            trade_date = dt_cls.strptime(trading_date_str, "%Y%m%d").date()
            dte = (exp_date - trade_date).days
            dte_label = "0DTE" if dte == 0 else f"+{dte}d"

            fallback_note = ""
            if result.fallback_attempts > 0:
                fallback_note = f" (preferred={result.preferred_strike}, fallbacks={result.fallback_attempts})"

            local_sym = getattr(result.qualified_contract, "localSymbol", "?") if result.qualified_contract else "?"

            print(f"   ✅ {sym} {label}: strike={result.strike}{fallback_note}")
            print(f"      class={result.trading_class} exp={result.expiration} ({dte_label})")
            print(f"      conId={result.con_id} local={local_sym}")
            print(f"      bid={result.bid} ask={result.ask} spread={result.spread}")

            selected_options[(sym, right)] = result
            if result.con_id:
                qualified_options[(sym, right)] = result

        except (ValueError, RuntimeError) as e:
            print(f"   ⚠️  {sym} {label}: {e}")
            warn(f"{sym} {label}: {e}")

if selected_options:
    ok(f"{len(selected_options)} options selected via production path")
    qual_count = sum(1 for r in selected_options.values() if r.con_id)
    ok(f"{qual_count} options qualified with conId")
else:
    now_et_check = datetime.now(ET)
    if now_et_check.weekday() >= 5 or now_et_check.hour < 9 or now_et_check.hour >= 16:
        warn("No options selected — expected outside market hours for some symbols")
    else:
        fail("No options selected during market hours")

# ── 9. Bid/Ask Summary (already obtained by production selector) ─────────────
print()
print("9. OPTION BID/ASK SUMMARY")

if not qualified_options:
    info("No qualified options — skipping bid/ask summary")
    info("This is normal outside market hours")
else:
    for key, result in list(qualified_options.items())[:4]:
        sym, right = key
        label = "CALL" if right == "C" else "PUT"
        bid = result.bid
        ask = result.ask
        spread = result.spread

        if bid and ask:
            status = "AVAILABLE"
        else:
            now_check = datetime.now(ET)
            if now_check.weekday() >= 5 or now_check.hour < 9 or now_check.hour >= 16:
                status = "UNAVAILABLE (market closed — normal)"
            else:
                status = "UNAVAILABLE (check market data subscription)"
                warn(f"{sym} {label}: bid/ask unavailable during market hours")

        print(f"   {sym} {label} {result.strike}: "
              f"bid={bid} ask={ask} spread={spread} [{status}]")

# ── 10. IBKR Warnings ────────────────────────────────────────────────────────
print()
print("10. IBKR WARNINGS/ERRORS")
if hasattr(ib, 'errorList') and ib.errorList:
    mkt_data_warnings = []
    critical_errors = []
    for err in ib.errorList:
        err_str = str(err)
        if "10091" in err_str or "market data" in err_str.lower():
            mkt_data_warnings.append(err_str)
        elif "error" in err_str.lower() or "Error" in str(getattr(err, 'errorCode', '')):
            critical_errors.append(err_str)
    if mkt_data_warnings:
        print("   MARKET DATA PERMISSION WARNINGS:")
        for w in mkt_data_warnings[-5:]:
            print(f"     ⚠️  {w}")
        warn("Market data permission warnings detected (may affect bid/ask availability)")
    if critical_errors:
        for e in critical_errors[-5:]:
            print(f"   ❌ {e}")
    if not mkt_data_warnings and not critical_errors:
        for err in ib.errorList[-5:]:
            print(f"   ℹ️  {err}")
else:
    info("No IBKR errors/warnings captured")

# ── 11. API/PWA Check ───────────────────────────────────────────────────────
print()
print("11. API/PWA VERIFICATION")
try:
    app = create_app()
    client = app.test_client()
    r = client.get("/")
    ok(f"PWA root: HTTP {r.status_code}") if r.status_code == 200 else fail("PWA root failed")

    r = client.get("/api/bot/status")
    ok(f"Status API: HTTP {r.status_code}") if r.status_code == 200 else fail("Status API failed")
    data = r.get_json()
    info(f"Bot state: {data.get('state', '?')}")

    r = client.get("/api/events?since=0")
    ok(f"Events API: HTTP {r.status_code}") if r.status_code == 200 else fail("Events API failed")

    r = client.get("/api/session")
    ok(f"Session API: HTTP {r.status_code}") if r.status_code == 200 else fail("Session API failed")
except Exception as e:
    fail(f"API check error: {e}")

# ── 13. Cancel all subscriptions ─────────────────────────────────────────────
print()
print("13. CLEANUP")
for sym, bars in bar_subscriptions.items():
    try:
        ib.cancelHistoricalData(bars)
    except Exception:
        pass
info("Bar subscriptions cancelled")

# ── Disconnect ───────────────────────────────────────────────────────────────
ib.disconnect()
ok("Disconnected from IBKR")

# ══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 60)
print("  ORDER AUDIT")
print("=" * 60)
print(f"  ENTRY ORDERS SUBMITTED  = {orders_submitted}")
print(f"  EXIT ORDERS SUBMITTED   = {orders_submitted}")
print(f"  TOTAL ORDERS SUBMITTED  = {orders_submitted}")
print()

print("=" * 60)
if blockers:
    print("  RESULT: BLOCKED")
    print()
    for b in blockers:
        print(f"    ❌ {b}")
else:
    now_et = datetime.now(ET)
    is_market_hours = now_et.weekday() < 5 and 9 <= now_et.hour < 16
    has_qualified = len(qualified_options) > 0
    has_quotes = any(r.bid is not None and r.ask is not None
                     for r in qualified_options.values()) if qualified_options else False

    if is_market_hours and has_qualified and has_quotes:
        print("  RESULT: READY FOR PAPER_EXECUTE")
    elif is_market_hours and has_qualified:
        print("  RESULT: READY_FOR_MARKET_HOURS_PREFLIGHT")
        print("  Production selector works but bid/ask unavailable.")
        print("  Check IBKR market data subscriptions.")
    elif not is_market_hours and has_qualified:
        print("  RESULT: READY_FOR_MARKET_HOURS_PREFLIGHT")
        print("  Production selector works outside market hours.")
        print("  Rerun during market hours to verify bid/ask.")
    elif not is_market_hours:
        print("  RESULT: READY_FOR_MARKET_HOURS_PREFLIGHT")
        print("  Full verification requires open market.")
    else:
        print("  RESULT: NEEDS_INVESTIGATION")
        print("  Production selector could not qualify options during market hours.")
print("=" * 60)
print()
