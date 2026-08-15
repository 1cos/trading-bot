# ISSUE-003: EXIT_FAILED state has no recovery or escalation path

**Status:** OPEN
**Severity:** Critical — position stuck unmanaged for 5+ hours
**Session:** 2026-08-14
**Code version:** commit `a843ec8` (T19A + T19B)
**Category:** Lifecycle / position management

## Summary

NVDA's 5th trade entered EXIT_SUBMITTED at 10:20 ET but
transitioned to EXIT_FAILED. The symbol remained stuck in
EXIT_FAILED from 10:20 ET until session close at 16:00 ET
(5 hours 40 minutes) with no retry, recovery, or escalation.
The position was left unresolved at shutdown.

## Exact timeline

```
09:16:05 [CT] EXECUTION_WORK_ENQUEUED symbol=NVDA (trade #5)
09:16:08 [CT] EXECUTION_WORK_COMPLETED symbol=NVDA
09:16:09 [CT] [NVDA] Entry: ENTRY_SUBMITTED → POSITION_OPEN

09:17:05 [CT] [NVDA] 10:16 C=225.52 → POSITION_OPEN
09:18:05 [CT] [NVDA] 10:17 C=225.58 → POSITION_OPEN
09:19:05 [CT] [NVDA] 10:18 C=225.25 → POSITION_OPEN
09:20:05 [CT] [NVDA] 10:19 C=225.08 → POSITION_OPEN
09:21:05 [CT] [NVDA] 10:20 C=224.99 → EXIT_SUBMITTED
09:21:05 [CT] [NVDA] Exit: EXIT_SUBMITTED → EXIT_FAILED   ← STUCK

09:22:05 [CT] [NVDA] 10:21 C=224.93 → EXIT_FAILED
09:23:05 [CT] [NVDA] 10:22 C=224.71 → EXIT_FAILED
...
14:59:05 [CT] [NVDA] 15:58 C=225.01 → EXIT_FAILED
15:00:00 [CT] [WARNING] UNRESOLVED NVDA: EXIT_FAILED
```

## State analysis

### Before failure

- Lifecycle: POSITION_OPEN
- Direction: SHORT
- Price action: 225.52 → 225.08 → 224.99 (moving in favor)
- Exit trigger: likely stop price hit at 10:20 (C=224.99)

### Transition to EXIT_FAILED

The exit order was submitted and immediately failed. Possible
causes (not logged in detail):
- IBKR rejected the exit order (insufficient margin, invalid
  contract, order precaution block)
- The option contract expired or became untradeable
- The exit executor encountered an exception

### After failure

- State: EXIT_FAILED — no recovery logic exists
- Duration: 5 hours 40 minutes
- Bar processing: continued (candles logged every minute)
- Position management: NONE — no retry, no manual intervention,
  no escalation, no timeout
- Shutdown: logged as UNRESOLVED

## Missing recovery mechanisms

The current code has NO handling for EXIT_FAILED:

1. **No retry**: the exit order is not re-submitted
2. **No alternative exit**: no market-order fallback
3. **No timeout**: no "if stuck for N minutes, try again"
4. **No escalation**: no alert, no notification, no forced
   transition to a recoverable state
5. **No position reconciliation**: bot doesn't check IBKR
   portfolio to see if the position still exists
6. **No manual override**: no PWA button to force-close or
   force-reset the position

## IBKR error details

The log does not capture the specific IBKR error code or
rejection reason for the failed exit. This is itself a gap —
the exit executor should log:
- Order ID
- IBKR error code
- IBKR error message
- Contract details
- Order type and quantity

## Impact

In live trading, this would mean an unmanaged SHORT position
left open for the entire day. Price moved from 224.99 to 225.01
(roughly flat in this case), but in a volatile scenario this
could cause significant losses.

## Possible fixes (not implemented)

### Immediate (lifecycle)

1. **EXIT_FAILED retry**: after N seconds, re-submit the exit
   order (with configurable max retries)
2. **Market order fallback**: after limit exit fails, try
   market order
3. **Timeout to DONE_FOR_DAY**: after M minutes in EXIT_FAILED,
   log the loss and transition to DONE_FOR_DAY (prevents
   the symbol from being stuck all day)

### Diagnostic

4. **Log IBKR exit error**: capture and log the specific IBKR
   error code/message when exit submission fails
5. **Position reconciliation**: periodically check IBKR
   portfolio to verify position still exists

### Operational

6. **PWA alert**: highlight EXIT_FAILED in red on the dashboard
   with a manual FORCE_EXIT button
7. **Notification**: push notification to iPhone when a position
   enters EXIT_FAILED

## Spec reference

The MAXBOT spec does not currently define EXIT_FAILED recovery
behavior. This needs to be added as part of the trade management
specification.
