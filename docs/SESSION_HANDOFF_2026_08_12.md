# SESSION HANDOFF — 2026-08-12

## Session Identity
- **Date:** 2026-08-12
- **Focus:** MaxBot v0.1 live trading layer — complete build T11–T17E6A
- **HEAD at end:** `aee1fba15380f900e47427d1d1bfac37dc5e5fe8`
- **Working tree:** clean
- **Test suite:** 507 passed (all live modules)

## What Was Built (T11–T17E6A)

### Core Execution Pipeline (T11–T14)
| Task | Module | Purpose |
|------|--------|---------|
| T11 | `underlying_exit_monitor.py` | Structural stop/target trigger on underlying 1m bars. LONG: low≤stop, high≥target. SHORT: inverse. Same-bar ambiguity → conservative STOP. Activation-time prevents pre-fill triggers. Terminal/idempotent. 31 tests. |
| T12 | `option_exit_executor.py` | SELL MARKET after underlying trigger. Duplicate protection via entry_order_id. No retry. 27 tests. |
| T13 | `exit_fill_monitor.py` | Confirms SELL fill. TARGET→WIN, STOP→LOSS (from structural reason, NOT option P&L). Idempotent via exit_order_id. 33 tests. |
| T14 | `trade_orchestrator.py` | Full lifecycle state machine: WAITING→ENTRY_SUBMITTED→POSITION_OPEN→EXIT_SUBMITTED→DONE. All components injected. 30 tests. |

### Runner & Modes (T15–T16)
| Task | Module | Purpose |
|------|--------|---------|
| T15 | `bot_runner.py` | IBKR Paper runner. Paper verification (acct starts with "D"). `reqHistoricalData(keepUpToDate=True)` for 1m streaming. RTH filtering. Bootstrap + dedup. 25 tests. |
| T16 | `observe_orchestrator.py` | OBSERVE_ONLY mode — full pipeline, zero orders. Detects signals, selects options, builds theoretical orders, monitors theoretical exits. `ExecutionMode` enum. CLI `--execution-mode`. 50 tests. |

### Multi-Symbol & Direction (T17A–T17B)
| Task | Module | Purpose |
|------|--------|---------|
| T17A | `dual_signal_detector.py` | BOTH direction: wraps LONG+SHORT detectors, returns first signal. Resolved direction flows to option mapping (LONG→CALL, SHORT→PUT). 15 tests. |
| T17B | `watchlist.py` + runner refactor | `SymbolRuntime` per-symbol container. `parse_symbols()`. Multi-symbol runner with shared IB. Independent lifecycle. `DailyTradeManager(unlimited=True)` for test phase. 90 tests. |

### Telemetry & Infrastructure (T17C–T17D4)
| Task | Module | Purpose |
|------|--------|---------|
| T17C | `event_stream.py` | `LiveEvent` (frozen, seq-numbered), `EventFactory`, `SessionEventLog`. 28 event types. `build_trade_summary()` for TRADE_COMPLETED (gross P&L). JSON+Markdown export. 36 tests. |
| T17D1 | orchestrator wiring | `emit` callback injected into both orchestrators. Per-trade `_trade_events` dict. `_emitted_terminal` set prevents duplicates. 11 e2e tests. |
| T17D2 | `control_api.py` | Flask local HTTP API. `MaxBotController` with `BotState` enum. 7 endpoints. Token auth. No LIVE mode. 35 tests. |
| T17D3 | `ui/dashboard.html` + manifest + sw.js | iPhone PWA. Dark theme, polling, start/stop, watchlist config, symbol cards, active trades, live event timeline, export. 32 tests. |
| T17D4 | `start_maxbot.sh` + `README_RUN.md` | One-command launcher. Venv activation, PYTHONPATH, LAN URL. `run_server()` in control_api. 24 tests. |

### Preflight & Compatibility (T17E–T17E6A)
| Task | Files | Purpose |
|------|-------|---------|
| T17E | `scripts/preflight_check.py` | Self-contained 13-check IBKR Paper preflight. Zero orders. |
| T17E1 | pyproject.toml, launcher, preflight | Python >=3.11,<3.14 (eventkit incompatible with 3.14). Guards in launcher, preflight, run_server. |
| T17E2 | preflight | Fixed `verify_paper_account` import (bot_runner, not control_api). |
| T17E3 | preflight | Hardened for weekend/after-hours. |
| T17E2B | (audit) | Confirmed ORB-only entry logic. PDH/PDL engine-implemented but not live-wired. PMH/PML not implemented. |
| T17E2C1 | `context_levels.py` | PDH/PDL from `reqHistoricalData(useRTH=True, "2 D")`. Reuses canonical `compute_pdh_pdl`. Exposed in API, PWA, telemetry. 21 tests. |
| T17E2C2 | context_levels extended | PMH/PML from `reqHistoricalData(useRTH=False)` filtered 04:00–09:29 ET. `premarket_final` flag. BUILDING indicator in PWA. 55 tests. |
| T17E3B1 | (audit) | Found 3 option bugs: adjusted chain selection (2QQQ/2SPY), Error 321 from `genericTickList="106"`, no strike fallback. |
| T17E3B2 | `option_selector.py` | Standard-chain ranking (tradingClass match, bracket, multiplier). `_fallback_strikes()`. Quote fix `""` not `"106"`. 89 tests. |
| T17E3B3 | preflight | Aligned with production `_pick_chain` + `OptionContractSelector.select()`. No duplicated chain logic. |
| T17E4 | `control_api.py` | Event-loop isolation: runner constructed INSIDE worker thread with `asyncio.new_event_loop()`. Fixed stuck STARTING state. Error display in PWA. 19 tests. |
| T17E5 | `bot_runner.py` | Hardened bar callbacks (try/except + logging). Heartbeat every 60 iterations. `premarket_final` live update. |
| T17E6 | watchlist + bot_runner + control_api + PWA | Per-symbol feed health (INITIALIZING/LIVE/STALE). Auto-resubscribe with 5-min cooldown. PWA shows LIVE/STALE/INIT badges. 14 tests. |
| T17E6A | bot_runner + watchlist | INITIALIZING timeout: dead feeds that never deliver first bar → STALE → resubscribe. Fixed `subscription_start_time` guard. 18 tests. |

## Current HEAD State

### File Layout (live/ package)
```
backend/src/trading_lab/live/
├── __init__.py
├── bot_runner.py          # MaxBotRunner + verify_paper_account
├── context_levels.py      # PDH/PDL/PMH/PML context
├── control_api.py         # Flask API + MaxBotController + run_server
├── dual_signal_detector.py
├── entry_fill_monitor.py
├── event_stream.py        # LiveEvent + SessionEventLog
├── execution_intent.py    # OptionExecutionIntent
├── exit_fill_monitor.py
├── ibkr_option_executor.py
├── observe_orchestrator.py
├── option_exit_executor.py
├── option_order_builder.py
├── option_selector.py     # _pick_chain + _fallback_strikes + OptionContractSelector
├── order_builder.py       # direct-instrument (NOT for options)
├── session_builder_live.py
├── signal_detector.py
├── trade_manager.py
├── trade_orchestrator.py
├── underlying_exit_monitor.py
├── watchlist.py           # SymbolRuntime + parse_symbols + feed health fields
├── ui/
│   ├── dashboard.html     # iPhone PWA
│   ├── manifest.json
│   └── sw.js
└── README_RUN.md
```

### Launch
```bash
./start_maxbot.sh
# Then from iPhone: http://<mac-mini-ip>:8765 → START MAXBOT
```

### CLI (direct, without PWA)
```bash
python -m trading_lab.live.bot_runner \
    --symbols QQQ,SPY,NVDA,AMD,GOOGL,TSLA,AMZN,META,AAPL \
    --direction BOTH \
    --execution-mode OBSERVE_ONLY
```

## Real Mac Mini Status (2026-08-12)

### Confirmed Working
- Python 3.12 + ib_insync import
- TWS Paper connection
- Paper account verification
- 9 symbols qualified (QQQ, SPY, NVDA, AMD, GOOGL, TSLA, AMZN, META, AAPL)
- PDH/PDL context levels computed from previous session
- PMH/PML premarket context levels (with BUILDING/FINAL state)
- Option chain selection (standard class, not adjusted 2QQQ/2SPY)
- Option strike selection with ITM fallback
- Bid/ask snapshot quotes (no Error 321)
- Live 1m bar callbacks (confirmed for 8/9 symbols)
- Heartbeat logging
- Feed health monitoring (LIVE/STALE/INITIALIZING)
- Auto-resubscribe for stale feeds
- PWA dashboard from iPhone
- Flask control API (start/stop/status/events/session)
- Session event logging and export

### Known Issue — SPY Stale Feed
SPY `reqHistoricalData(keepUpToDate=True)` subscription delivers bootstrap bars but never fires completed-bar callbacks. IBKR-side issue (pacing violation or data availability). The auto-resubscribe mechanism now detects this via INITIALIZING timeout and attempts recovery. Needs live verification that resubscribe resolves SPY specifically.

## Frozen Decisions (Cumulative)

### From prior sessions
- ATR: SMA, `news_threshold >= 2.0` default 3.0
- Displacement: `min_displacement_bars=3`
- PDH/PDL direction: `LONG + PREVIOUS_DAY_HIGH`, `SHORT + PREVIOUS_DAY_LOW`
- LONG near bound = `lower_ticks`, SHORT near bound = `upper_ticks`
- B10 grade split: B vs B+ at `distance_ticks <= risk_ticks`

### From this session
- Entry: CONFIRMATION_CLOSE model, BUY LMT @ ask
- Exit: SELL MKT on underlying trigger
- Options: 0DTE preferred, 1-strike ITM, quantity=1
- Same-bar stop+target → STOP (conservative)
- Strategy result from structural reason, NOT option P&L
- Python >=3.11,<3.14 for ib_insync compatibility
- Trade limits OFF for test phase (`unlimited=True`)
- Default: `--direction BOTH`, `--execution-mode OBSERVE_ONLY`
- LIVE execution mode permanently blocked
- Standard chain: tradingClass must match symbol, multiplier=100, strikes bracket underlying
- Quote request: `reqMktData(option, "", snapshot=True)` — no generic ticks
- Feed stale threshold: 180 seconds during RTH
- Resubscribe cooldown: 300 seconds

## On the Horizon

### Immediate Next
1. **Live verification of SPY auto-resubscribe** — does the INITIALIZING timeout → resubscribe actually recover SPY's feed during market hours?
2. **First OBSERVE_ONLY morning session** — run full session, review event log, compare MaxBot decisions against Max's discretionary calls
3. **First PAPER_EXECUTE session** — after OBSERVE confidence established

### Backlog (not yet approved)
- B11: EARLY_EXIT_REJECTION_WALL_FAILURE (blocked on annotation ground truth)
- B12: CHOP_NO_TRADE classifier
- B13: RETEST_STRUCTURE detector
- B14: Review workspace metadata
- Manual annotation campaign (SPY 2026-03-18 SHORT recommended as next case)
- ATR warm-up from prior session bars
- Multi-timeframe consolidation

## Test Suite Summary

| Test File | Count | Area |
|-----------|-------|------|
| test_underlying_exit_monitor | 31 | T11 structural triggers |
| test_option_exit_executor | 27 | T12 SELL MARKET |
| test_exit_fill_monitor | 33 | T13 WIN/LOSS |
| test_trade_orchestrator | 30 | T14 lifecycle |
| test_bot_runner | 27 | T15+T17B runner |
| test_observe_mode | 25 | T16 observe |
| test_both_direction | 15 | T17A dual detector |
| test_event_stream | 36 | T17C events |
| test_lifecycle_telemetry | 11 | T17D1 e2e |
| test_control_api | 35 | T17D2 API |
| test_pwa_dashboard | 32 | T17D3 PWA |
| test_launcher | 24 | T17D4 launcher |
| test_option_selector | 89 | T7+T17E3B2 |
| test_option_order_builder | 32 | T8 order spec |
| test_context_levels | 55 | T17E2C1+C2 |
| test_event_loop_isolation | 19 | T17E4 thread safety |
| test_feed_health | 18 | T17E6+E6A |
| **TOTAL** | **507** | |
