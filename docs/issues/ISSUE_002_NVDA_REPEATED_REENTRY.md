# ISSUE-002: Repeated re-entry after exit without cooldown (NVDA)

**Status:** OPEN
**Severity:** High — 5 trades in 30 minutes on one symbol
**Session:** 2026-08-14
**Code version:** commit `a843ec8` (T19A + T19B)
**Category:** Lifecycle / re-entry control (NOT strategy bug unless proven)

## Summary

After NVDA's first trade exits (stop or target hit), the lifecycle
immediately transitions back to WAITING_FOR_SIGNAL, the pipeline
re-evaluates the same session data, and immediately finds a new
valid SIGNAL. This produces 5 consecutive trades in 30 minutes on
the same symbol, with entries at 09:52, 10:06, 10:09, 10:12, 10:15.

## Exact timeline

| # | Event | Time (ET) | CT | Price | Details |
|---|-------|-----------|----|-------|---------|
| 1 | SIGNAL SHORT | 09:52 | 08:52 | 225.70 | First valid BDRR signal |
| 1 | ENQUEUED | 09:52 | 08:53 | — | bar_time_ms=1786715520000 |
| 1 | ENTRY → POSITION_OPEN | 09:52 | 08:53 | — | Fill confirmed |
| 1 | POSITION_OPEN | 09:53–10:04 | — | 225.50–225.83 | Holding 12 bars |
| 1 | EXIT_SUBMITTED | 10:05 | 09:06 | 226.08 | Stop/target hit |
| 1 | EXIT → WAITING_FOR_SIGNAL | 10:05 | 09:06 | — | **Immediate reset** |
| 2 | ENQUEUED | 10:06 | 09:07 | — | bar_time_ms=1786716360000 |
| 2 | ENTRY → POSITION_OPEN | 10:06 | 09:07 | — | 2nd trade, 1 bar later |
| 2 | EXIT → WAITING_FOR_SIGNAL | 10:08 | 09:09 | 226.29 | 2 bars held |
| 3 | ENQUEUED | 10:09 | 09:10 | — | bar_time_ms=1786716540000 |
| 3 | ENTRY → POSITION_OPEN | 10:09 | 09:10 | — | 3rd trade, 1 bar later |
| 3 | EXIT → WAITING_FOR_SIGNAL | 10:11 | 09:12 | 226.04 | 2 bars held |
| 4 | ENQUEUED | 10:12 | 09:13 | — | bar_time_ms=1786716720000 |
| 4 | ENTRY → POSITION_OPEN | 10:12 | 09:13 | — | 4th trade, 1 bar later |
| 4 | EXIT → WAITING_FOR_SIGNAL | 10:14 | 09:15 | 225.83 | 2 bars held |
| 5 | ENQUEUED | 10:15 | 09:16 | — | bar_time_ms=1786716900000 |
| 5 | ENTRY → POSITION_OPEN | 10:15 | 09:16 | — | 5th trade |
| 5 | EXIT_SUBMITTED | 10:20 | 09:21 | 224.99 | |
| 5 | EXIT → **EXIT_FAILED** | 10:20 | 09:21 | — | See ISSUE-003 |

## Analysis

### Each re-entry is a NEW valid signal

Each EXECUTION_WORK_ENQUEUED has a distinct `bar_time_ms`, meaning
each is produced by a different completed bar's signal evaluation.
This is NOT stale/reused state — the pipeline genuinely finds a new
valid BDRR setup on each bar after the exit.

This happens because:
1. The EXIT transitions lifecycle back to WAITING_FOR_SIGNAL
2. The session builder still contains all bars from the session
3. The BDRR pipeline re-evaluates from scratch on the next bar
4. The break/displacement/retest are still present in the session
5. The new bar is a valid rejection candle → SIGNAL

### Trade limits are OFF

The `DailyTradeManager` is in unlimited mode (`--trade-limits`
flag not set). With trade limits enabled (e.g., max 2 per symbol
per day), trades 3-5 would have been blocked.

### This is a lifecycle/re-entry control issue

The strategy correctly identifies each setup as valid according
to the BDRR rules. The issue is that the lifecycle allows
immediate re-entry into the same structural zone without:
- A cooldown period after exit
- A requirement for a completely new break/displacement sequence
- A per-symbol daily trade counter

## Contributing factors

1. **No post-exit cooldown**: lifecycle goes directly from
   EXIT_SUBMITTED → WAITING_FOR_SIGNAL with no delay
2. **Session builder retains all bars**: the same break,
   displacement, and retest pattern persists
3. **Trade limits disabled**: test mode has no per-symbol cap
4. **Same structural zone**: all 5 entries target the same
   ORB low break / retest zone

## Possible fixes (not implemented)

1. **Enable trade limits**: `--trade-limits` flag would cap
   trades per symbol per day
2. **Post-exit cooldown**: after exit, require N bars before
   allowing re-evaluation
3. **Require new sequence**: after exit, invalidate the current
   break/displacement and require a completely fresh one
4. **Per-symbol trade counter**: independent of DailyTradeManager,
   track entries per symbol and cap
5. **Zone exhaustion**: after trading a zone, mark it as
   "traded" and skip it on re-evaluation

## Spec reference

Per MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md:
- "Retest failure requires completely new sequence
  (recovery ≠ continuation)"
- This principle should extend to post-exit: a completed
  trade on a zone should require a new sequence

## Impact

5 trades on one symbol in 30 minutes. In live trading with
real money, this would multiply commission costs and risk
exposure. The 5th trade ended in EXIT_FAILED (see ISSUE-003),
compounding the problem.
