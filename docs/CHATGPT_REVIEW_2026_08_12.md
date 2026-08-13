# REPORT PER TECHNICAL ARCHITECT — Sessione 2026-08-12/13
## MaxBot v0.1 — Build Completo Live Trading Layer + Prima Sessione Reale

---

## 1. Panoramica

Sessione di build completa del **live trading layer** di MaxBot v0.1, dalla creazione di moduli isolati fino al **primo bot live funzionante su Mac mini con dashboard iPhone**.

Il bot è stato testato in **produzione reale** (IBKR TWS Paper) durante la sessione di mercato del 12-13 agosto 2026, rivelando e risolvendo **7 bug critici** che erano impossibili da trovare nei test unitari.

| Metrica | Valore |
|---------|--------|
| Task completati | 30+ (T11–T17F) |
| Moduli Python creati | 22 |
| File UI (PWA) | 3 |
| Test totali | 528 |
| Commit | 30 |
| Bug critici risolti in produzione | 7 |
| HEAD finale | `6711ebb55ece37b52b006ec6eb83321e3efa4691` |

---

## 2. Architettura — Flusso Completo

```
iPhone PWA (START) → Flask API → Worker Thread (event loop dedicato) → IBKR Paper
                                        │
                         ┌──────────────┼──────────────────────────┐
                         │   per ogni simbolo (9, indipendenti):   │
                         │                                         │
                         │   reqHistoricalData(keepUpToDate=True)   │
                         │              ↓ 1m bar                   │
                         │   LiveSessionBuilder                    │
                         │              ↓                          │
                         │   LiveSignalDetector (LONG / SHORT)     │
                         │              ↓                          │
                         │   ┌── Stage 1: ORB (5 bars)             │
                         │   ├── Stage 2: Break                    │
                         │   ├── Stage 3: Displacement (≥3 bars)   │
                         │   ├── Stage 3b: Sequence validation     │
                         │   ├── Stage 4: Retest window            │
                         │   └── Stage 5: Rejection / Entry candle │
                         │              ↓ SIGNAL                   │
                         │   OptionExecutionIntent (LONG→CALL)     │
                         │              ↓                          │
                         │   OptionContractSelector                │
                         │   (chain ranking + strike fallback)     │
                         │              ↓                          │
                         │   BUY LMT @ ask                         │
                         │              ↓                          │
                         │   ┌── OBSERVE: log, no placeOrder       │
                         │   └── PAPER:   IBKROptionExecutor       │
                         │                    ↓ FILL               │
                         │              UnderlyingExitMonitor      │
                         │              (structural stop/target)   │
                         │                    ↓ TRIGGER            │
                         │              SELL MKT                   │
                         │                    ↓ FILL               │
                         │              WIN/LOSS (strutturale)     │
                         │                    ↓                    │
                         │         TRADE_COMPLETED (gross P&L)     │
                         └─────────────────────────────────────────┘
```

### Principio Architetturale Chiave

Il bot **analizza l'UNDERLYING** (QQQ, SPY, NVDA...) ma **esegue tramite OPTIONS** (CALL/PUT).
- Stop/target = livelli strutturali dell'underlying, MAI premi opzione
- WIN/LOSS = motivo strutturale (TARGET/STOP), MAI P&L del premium
- Entry = BUY LMT @ ask sull'opzione
- Exit = SELL MKT quando il trigger strutturale scatta

---

## 3. Componenti Costruiti

### 3.1 Pipeline di Esecuzione (T11–T14)
| Modulo | Funzione | Test |
|--------|----------|------|
| `underlying_exit_monitor.py` | Monitor stop/target su barre 1m underlying. Same-bar ambiguity → STOP conservativo. | 31 |
| `option_exit_executor.py` | SELL MKT dopo trigger. Duplicate protection via entry_order_id. | 27 |
| `exit_fill_monitor.py` | Conferma fill. TARGET→WIN, STOP→LOSS. Idempotente. | 33 |
| `trade_orchestrator.py` | State machine: WAITING→ENTRY_SUBMITTED→POSITION_OPEN→EXIT_SUBMITTED→DONE. | 30 |

### 3.2 Runner e Modi (T15–T16)
| Modulo | Funzione | Test |
|--------|----------|------|
| `bot_runner.py` | IBKR Paper runner. Paper verification fail-closed. 1m streaming. RTH filter. Bootstrap + dedup. | 27 |
| `observe_orchestrator.py` | OBSERVE_ONLY — pipeline completa, zero ordini. Default mode. | 25 |

### 3.3 Multi-Symbol & Direction (T17A–T17B)
| Modulo | Funzione | Test |
|--------|----------|------|
| `dual_signal_detector.py` | `--direction BOTH`: wrappa LONG+SHORT detector. LONG→CALL, SHORT→PUT. | 15 |
| `watchlist.py` | `SymbolRuntime` per simbolo. Lifecycle indipendente. `unlimited=True` per test. | 90 |

### 3.4 Telemetria (T17C–T17D1)
| Modulo | Funzione | Test |
|--------|----------|------|
| `event_stream.py` | 28 tipi evento, seq monotona, JSON/MD export. `build_trade_summary()`. | 36 |
| Orchestrator wiring | `emit` callback iniettato. Duplicate prevention per eventi terminali. | 11 |

### 3.5 Infrastruttura (T17D2–T17D4)
| Modulo | Funzione | Test |
|--------|----------|------|
| `control_api.py` | Flask HTTP API. 7 endpoint. Token auth. LIVE bloccato permanentemente. | 35 |
| `ui/dashboard.html` + manifest + sw.js | PWA iPhone dark-theme. Polling real-time. Start/stop. Export. | 32 |
| `start_maxbot.sh` + `README_RUN.md` | Un comando. LAN URL discovery. | 24 |

### 3.6 Context Levels (T17E2C1–T17E2C2)
| Modulo | Funzione | Test |
|--------|----------|------|
| `context_levels.py` | PDH/PDL (da sessione RTH precedente) + PMH/PML (premarket 04:00–09:29 ET). Flag BUILDING/FINAL. | 55 |

### 3.7 Option Hardening (T17E3B2)
| Modulo | Funzione | Test |
|--------|----------|------|
| `option_selector.py` | Chain ranking (tradingClass match, bracket, multiplier=100). `_fallback_strikes()`. Quote fix. | 89 |

### 3.8 Pipeline Observability (T17F)

Ogni simbolo mostra nella PWA e nei log:
```
QQQ  LIVE                    WAITING FOR SIGNAL
ORB H 727.80 · L 722.50
PDH 727.25 · PDL 722.92 | PMH 725.46 · PML 722.80
ORB COMPLETE — NO BREAK
```

Progressione stage visibile barra per barra:
```
[QQQ] 08:34 C=719.00 → WAITING [BUILDING ORB]
[QQQ] 08:35 C=718.60 → WAITING [ORB COMPLETE — NO BREAK] H=719.00 L=715.20
[QQQ] 08:37 C=719.30 → WAITING [BREAK — DISPLACEMENT BUILDING] (1/3 bars)
[QQQ] 08:39 C=719.80 → WAITING [DISPLACEMENT — NO RETEST] disp=3 bars
[QQQ] 08:42 C=719.10 → WAITING [RETEST — NO ENTRY CANDLE]
[SPY] 08:45 C=540.30 → WAITING [SEQUENCE INVALIDATED]
```

---

## 4. Bug Critici Trovati e Risolti in Produzione

### Bug 1: Python 3.14 Incompatibilità (T17E1)
**Sintomo:** `RuntimeError: There is no current event loop` — import di `ib_insync` fallisce.
**Causa:** eventkit chiama `asyncio.get_event_loop()` che in 3.14 non auto-crea il loop.
**Fix:** `requires-python = ">=3.11,<3.14"` + guard espliciti in launcher/preflight/run_server.

### Bug 2: Chain Selection 2QQQ/2SPY (T17E3B2)
**Sintomo:** Opzione selezionata con strike a 625 su underlying a 718 (classe adjusted).
**Causa:** `_pick_chain()` prendeva il primo chain SMART, ignorando tradingClass.
**Fix:** Ranking multi-criterio con 5 filtri hard + strike fallback ITM progressivo.

### Bug 3: IBKR Error 321 (T17E3B2)
**Sintomo:** Snapshot quote ritorna sempre `bid=null, ask=null`.
**Causa:** `genericTickList="106"` incompatibile con `snapshot=True`.
**Fix:** `reqMktData(option, "", snapshot=True)`.

### Bug 4: Event Loop Flask ↔ IBKR (T17E4)
**Sintomo:** Stato bloccato su STARTING, bot non parte mai.
**Causa:** `MaxBotRunner` costruito nel Flask request thread (senza event loop asyncio).
**Fix:** Runner costruito INSIDE worker thread con `asyncio.new_event_loop()`. State machine: STOPPED→STARTING→RUNNING/ERROR, mai stuck.

### Bug 5: Callback Exception Swallowing (T17E5)
**Sintomo:** Bot sembra funzionare (RUNNING) ma non processa nessuna barra.
**Causa:** eventkit swallowa silenziosamente le eccezioni dai callback.
**Fix:** try/except con `log.error(exc_info=True)` + heartbeat diagnostico ogni 60s.

### Bug 6: SPY Dead Feed (T17E6 + T17E6A)
**Sintomo:** 8 simboli ricevono 32+ bars, SPY resta a 7 bars con zero callbacks.
**Causa:** IBKR `keepUpToDate=True` smette silenziosamente di inviare update (pacing violation).
**Fix:** Feed health model (INIT/LIVE/STALE) con timeout 180s + auto-resubscribe con cooldown 5 min.

### Bug 7: Signal su Barre Bootstrap (prima sessione live 13 agosto)
**Sintomo:** NVDA SIGNAL LONG triggerato alle 08:20 CT (10 min prima dell'apertura). Option quote `bid=null/ask=null` → simbolo DISABLED permanente.
**Causa:** `_bootstrap_symbol()` chiamava `orchestrator.on_bar()` su barre storiche di ieri → signal detector trovava setup BDRR completato.
**Fix:** Bootstrap chiama solo `session_builder.add_bar()` per contesto. Zero `orchestrator.on_bar()` durante bootstrap.

---

## 5. Stato Mac Mini Reale (13 agosto 2026)

### Confermato Funzionante
- ✅ Python 3.12 + ib_insync
- ✅ TWS Paper + Paper account verification
- ✅ 9 simboli qualificati e streaming 1m
- ✅ 8/9 simboli con live bar callbacks
- ✅ PDH/PDL + PMH/PML + ORB levels nella PWA
- ✅ Pipeline stage progression visibile per simbolo
- ✅ Option chain standard (non adjusted 2QQQ/2SPY)
- ✅ PWA iPhone con orari CT
- ✅ Feed health con auto-resubscribe
- ✅ Bootstrap non triggera segnali falsi
- ✅ Ordini Paper piazzati correttamente (GOOGL PUT, AMD CALL visti nel portfolio IBKR)

### Issue Aperto
**SPY**: subscription bootstrap OK ma callback morto. Auto-resubscribe implementato, da verificare nella prossima sessione.

---

## 6. Decisioni Frozen

### Esecuzione
| Decisione | Valore |
|-----------|--------|
| Entry | BUY LMT @ ask |
| Exit | SELL MKT su trigger underlying |
| Options | 0DTE preferred, 1-strike ITM, qty=1 |
| Same-bar | stop+target → STOP conservativo |
| Risultato | Strutturale (TARGET/STOP), non P&L opzione |
| Trade limits | OFF per test |
| Default | BOTH + OBSERVE_ONLY |
| LIVE mode | Bloccato permanentemente |
| Python | >=3.11, <3.14 |
| Chain | tradingClass==symbol, mult=100, bracket |
| Quote | `reqMktData(option, "", snapshot=True)` |
| Feed stale | 180s RTH, cooldown 300s |
| PWA timezone | CT (America/Chicago) |
| Bootstrap | Context only, no signals |

---

## 7. Prossimi Passi

1. **Verifica SPY auto-resubscribe** durante mercato aperto
2. **Sessione OBSERVE_ONLY completa** — verificare pipeline vs lettura discrezionale Max
3. **Sessione PAPER_EXECUTE** — dopo confidenza OBSERVE
4. **T18 (proposto):** ulteriore observability — stage progression negli eventi PWA (non solo log terminal)

---

## 8. Test Suite — 528 Passed

| File | Count |
|------|-------|
| test_underlying_exit_monitor | 31 |
| test_option_exit_executor | 27 |
| test_exit_fill_monitor | 33 |
| test_trade_orchestrator | 30 |
| test_bot_runner | 27 |
| test_observe_mode | 25 |
| test_both_direction | 15 |
| test_event_stream | 36 |
| test_lifecycle_telemetry | 11 |
| test_control_api | 35 |
| test_pwa_dashboard | 32 |
| test_launcher | 24 |
| test_signal_detector | 21 |
| test_option_selector | 89 |
| test_option_order_builder | 32 |
| test_context_levels | 55 |
| test_event_loop_isolation | 19 |
| test_feed_health | 18 |
| **TOTALE** | **528** |
