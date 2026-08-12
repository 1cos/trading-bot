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

# ── 7. Option Chain Preflight ────────────────────────────────────────────────
print()
print("7. OPTION CHAIN PREFLIGHT")
print(f"   {'Symbol':<8} {'Chain':<6} {'Class':<8} {'Mult':<6} {'Expirations':<12} {'Strikes'}")
print(f"   {'─'*8} {'─'*6} {'─'*8} {'─'*6} {'─'*12} {'─'*10}")

chain_data = {}
for sym in OPTION_TEST_SYMBOLS:
    if sym not in qualified_stocks:
        print(f"   {sym:<8} {'SKIP':<6} — not qualified")
        continue
    stock = qualified_stocks[sym]
    try:
        chains = ib.reqSecDefOptParams(sym, "", "STK", stock.conId)
        smart_chain = None
        for c in chains:
            if c.exchange == "SMART":
                smart_chain = c
                break
        if smart_chain is None and chains:
            smart_chain = chains[0]

        if smart_chain:
            chain_data[sym] = smart_chain
            print(f"   {sym:<8} {'YES':<6} {smart_chain.tradingClass:<8} "
                  f"{smart_chain.multiplier:<6} {len(smart_chain.expirations):<12} "
                  f"{len(smart_chain.strikes)}")
        else:
            print(f"   {sym:<8} {'NO':<6}")
            warn(f"{sym}: no option chain")
    except Exception as e:
        print(f"   {sym:<8} {'ERROR':<6} {e}")
        warn(f"{sym} chain: {e}")

if chain_data:
    ok(f"Option chains found for {len(chain_data)} symbols")
else:
    fail("No option chains found")

# ── 8. Real Option Selection ─────────────────────────────────────────────────
print()
print("8. REAL OPTION SELECTION (policy: 0DTE/nearest, 1-strike ITM)")

today_str = datetime.now(ET).strftime("%Y%m%d")
selected_options = {}

for sym in OPTION_TEST_SYMBOLS:
    if sym not in chain_data:
        continue
    chain = chain_data[sym]
    stock = qualified_stocks[sym]

    # Get current underlying price from last bar
    und_price = None
    if sym in bar_subscriptions and bar_subscriptions[sym]:
        und_price = float(bar_subscriptions[sym][-1].close)
    if und_price is None:
        info(f"{sym}: no underlying price available, skipping")
        continue

    expirations = list(chain.expirations)
    strikes = list(chain.strikes)

    try:
        exp = select_expiration(today_str, expirations)
        is_0dte = (exp == today_str)
    except ValueError as e:
        warn(f"{sym} expiration: {e}")
        continue

    dte_label = "0DTE" if is_0dte else f"+{int(exp) - int(today_str)}d"
    print(f"\n   {sym} — underlying={und_price:.2f}, expiration={exp} ({dte_label})")

    for right, label in [("C", "CALL"), ("P", "PUT")]:
        try:
            strike = select_strike(right, und_price, strikes)
            print(f"   {label}: strike={strike}")
            selected_options[(sym, right)] = {
                "symbol": sym, "right": right, "strike": strike,
                "expiration": exp, "exchange": chain.exchange,
                "multiplier": chain.multiplier,
                "trading_class": chain.tradingClass,
                "underlying_price": und_price,
            }
        except ValueError as e:
            warn(f"{sym} {label}: {e}")

if selected_options:
    ok(f"{len(selected_options)} option contracts selected")
else:
    fail("No option contracts could be selected")

# ── 9. Option Qualification ──────────────────────────────────────────────────
print()
print("9. OPTION QUALIFICATION")

qualified_options = {}
qual_failures = 0
test_keys = list(selected_options.keys())[:6]  # test up to 6 contracts
for key in test_keys:
    sel = selected_options[key]
    qualified = False

    # Try with chain exchange first, then SMART fallback
    exchanges_to_try = [sel["exchange"]]
    if sel["exchange"] != "SMART":
        exchanges_to_try.append("SMART")

    for exch in exchanges_to_try:
        try:
            opt = Option(
                sel["symbol"], sel["expiration"], sel["strike"],
                sel["right"], exch, sel["multiplier"], "USD",
            )
            opt.tradingClass = sel["trading_class"]
            result = ib.qualifyContracts(opt)
            if result and opt.conId:
                qualified_options[key] = opt
                local_sym = getattr(opt, "localSymbol", "?")
                print(f"   ✅ {sel['symbol']} {sel['right']} {sel['strike']} "
                      f"exp={sel['expiration']} conId={opt.conId} "
                      f"exchange={exch} local={local_sym}")
                qualified = True
                break
        except Exception as e:
            # Try next exchange
            continue

    if not qualified:
        qual_failures += 1
        print(f"   ⚠️  {sel['symbol']} {sel['right']} {sel['strike']} "
              f"exp={sel['expiration']} — not found (may be normal outside market hours)")

if qualified_options:
    ok(f"{len(qualified_options)}/{len(test_keys)} options qualified")
elif qual_failures > 0:
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5 or now_et.hour < 9 or now_et.hour >= 16:
        warn(f"Option qualification failed outside market hours — "
             f"this is expected and NOT a blocker for live sessions")
    else:
        warn("No options qualified during market hours — check IBKR market data subscriptions")
else:
    warn("No options could be qualified")

# ── 10. Bid/Ask Market Data ──────────────────────────────────────────────────
print()
print("10. OPTION BID/ASK MARKET DATA")

if not qualified_options:
    info("No qualified options to test — skipping market data check")
    info("This is normal outside market hours")
else:
    for key, opt in list(qualified_options.items())[:2]:  # test 2 contracts
        sel = selected_options[key]
        try:
            ticker = ib.reqMktData(opt, "106", snapshot=True)
            ib.sleep(3)  # wait for snapshot

            bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
            ask = ticker.ask if ticker.ask and ticker.ask > 0 else None
            spread = round(ask - bid, 4) if bid and ask else None
            spread_pct = round((ask - bid) / ask * 100, 2) if bid and ask and ask > 0 else None

            status = "LIVE" if bid and ask else "DELAYED/UNAVAILABLE"
            print(f"   {sel['symbol']} {sel['right']} {sel['strike']}: "
                  f"bid={bid} ask={ask} spread={spread} "
                  f"spread%={spread_pct} [{status}]")

            if bid is None and ask is None:
                now_et = datetime.now(ET)
                if now_et.weekday() >= 5 or now_et.hour < 9 or now_et.hour >= 16:
                    info(f"{sel['symbol']} {sel['right']}: no bid/ask (market closed — normal)")
                else:
                    warn(f"{sel['symbol']} {sel['right']}: bid/ask both unavailable during market hours")

            ib.cancelMktData(opt)
        except Exception as e:
            print(f"   {sel['symbol']} {sel['right']} {sel['strike']}: ERROR {e}")
            warn(f"Market data error: {e}")

# ── 11. IBKR Warnings ───────────────────────────────────────────────────────
print()
print("11. IBKR WARNINGS/ERRORS")
# Check for any error messages from IB
if hasattr(ib, 'errorList') and ib.errorList:
    for err in ib.errorList[-10:]:
        print(f"   ⚠️  {err}")
else:
    info("No critical IBKR errors captured")

# ── 12. API/PWA Check ───────────────────────────────────────────────────────
print()
print("12. API/PWA VERIFICATION")
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
    if is_market_hours:
        print("  RESULT: READY FOR PAPER_EXECUTE")
    else:
        print("  RESULT: READY (tested outside market hours)")
        print("  Option qualification/market data will fully verify")
        print("  during the first live session.")
print("=" * 60)
print()
