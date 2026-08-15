# ISSUE-001: SPY BarDataList grows but updateEvent callback never fires

**Status:** OPEN
**Severity:** Critical — SPY completely non-functional for entire session
**Session:** 2026-08-14
**Code version:** commit `a843ec8` (T19A + T19B fixes applied)
**Category:** IBKR/ib_insync subscription lifecycle

## Summary

SPY's `BarDataList` accumulates bars from IBKR (visible in heartbeat
bar counts), but `updateEvent` never fires, so `_on_bar_update` is
never called and SPY never processes a single bar. This persists
through 76+ resubscribe attempts across the full trading session.

## Evidence

### Initial subscription failure (pre-RTH)

```
08:36:58 [ERROR] Error 162, reqId 88: Historical Market Data Service error message:
  HMDS query returned no data: SPY@SMART Trades
08:36:58 [INFO] STREAM ACTIVE: SPY (2/9, 0 bars, obj=4460831808, listeners=1)
```

All other 8 symbols received 7 bars and bootstrapped normally.

### Resubscribe cycle (76+ attempts, all failed the same way)

| Time (CT) | Attempt | Old obj | New obj | Bars received | Bars processed |
|-----------|---------|---------|---------|---------------|----------------|
| 08:40:05  | #1      | 4460831808 | 4461191536 | 0   | 0 |
| 08:45:06  | #2      | 4461191536 | 4461000928 | 0   | 0 |
| 08:50:07  | #3      | 4461000928 | 4461001808 | 5   | 0 |
| 08:55:11  | #4      | 4461001808 | 4461654448 | 10  | 0 |
| 09:00:13  | #5      | 4461654448 | 4461653328 | 15  | 0 |
| ...       | ...     | ...        | ...        | ... | 0 |
| 14:57:04  | #76+    | ...        | 4461655008 | 372 | 0 |

Key observation: from attempt #3 onward, new BarDataList objects
arrive with bars (5, 10, 15, ..., 372), proving IBKR IS sending
data. But `updateEvent` never fires on any of them.

### Heartbeat bar count progression

```
08:38:05 bars={'SPY': 0, ...}     # pre-RTH, no bars
08:51:08 bars={'SPY': 5, ...}     # after resubscribe #3
08:55:12 bars={'SPY': 10, ...}    # growing
14:59:04 bars={'SPY': 372, ...}   # 372 bars by end of day
```

All other symbols: 385-389 bars by end of day, all processing normally.

### Zero bar processing

No `[SPY] HH:MM C=xxx` log line exists anywhere in the session.
SPY never transitioned from INITIALIZING to LIVE.

## Why pacing is now ruled out

T19B added 0.6s delays between subscriptions and 0.5s between
context-level fetches. The log confirms proper staggering:

```
08:36:58 STREAM ACTIVE: QQQ (1/9, ...)
08:36:58 STREAM ACTIVE: SPY (2/9, ...)  ← Error 162
08:36:59 STREAM ACTIVE: NVDA (3/9, ...)
08:37:00 STREAM ACTIVE: AMD (4/9, ...)
...
```

Timestamps show ~1s between each subscription. All 8 other symbols
work perfectly. SPY's bars grow to 372 — IBKR is delivering data.
The issue is that `updateEvent` on the BarDataList never emits.

## Hypotheses (open)

### H1: Error 162 poisons the subscription lifecycle

The initial `reqHistoricalData` with `useRTH=True` before market
open returns Error 162 "no data" for SPY (but not for other symbols
which have residual yesterday bars). This may leave the BarDataList
in a state where `keepUpToDate=True` receives bars but
`updateEvent` is never connected or fires.

**Test:** Subscribe SPY AFTER market open (09:30+ ET) and check
if `updateEvent` works.

### H2: ib_insync bug with empty initial BarDataList + keepUpToDate

When `reqHistoricalData` returns 0 bars initially but
`keepUpToDate=True` causes bars to arrive later, ib_insync may
not properly wire up the `updateEvent` emission. This could be
specific to the transition from empty→populated BarDataList.

**Test:** Check ib_insync source code for how `updateEvent` is
connected when the initial response is empty.

### H3: SPY-specific IBKR behavior

SPY (conId=756733) is the most heavily subscribed instrument
on IBKR. There may be server-side throttling or routing
differences that affect the `keepUpToDate` stream.

**Test:** Try subscribing SPY with `useRTH=False` to get
pre-market bars initially, avoiding the Error 162.

### H4: Listener attachment timing

The callback is attached via `bars.updateEvent += callback`
AFTER the `reqHistoricalData` call. If bars arrive between
the request and the callback registration, the event
connection may be in an inconsistent state.

**Test:** Log `id(bars.updateEvent)` before and after the
`+=` to verify the event object identity is stable.

## Recommended next steps

1. Try `useRTH=False` for the initial subscription (avoids
   Error 162 entirely, bars can still be filtered in
   `_on_bar_update`)
2. Add diagnostic logging inside `_on_bar_update` to detect
   if callbacks arrive but fail silently
3. Examine ib_insync source: `BarDataList.updateEvent` emission
   path when initial result is empty
4. Consider subscribing SPY last (or after RTH open) as a
   workaround

## Impact

SPY is non-functional for the entire session. No BDRR pipeline
runs on SPY. Other 8 symbols unaffected.
