# REPORT PER TECHNICAL ARCHITECT — Sessione 2026-08-12

## Panoramica Sessione
Sessione di build completa del **live trading layer** di MaxBot v0.1.
28 task completati (T11–T17E6A), da "moduli core isolati" a "bot live funzionante su Mac mini con PWA iPhone".

**HEAD finale:** `51337d3cb212263a7ed9136d269e1a0b9b5bb32b`
**Test totali:** 507 passed
**Working tree:** clean

---

## Architettura Costruita

### Flusso Completo
```
iPhone PWA (start) → Flask API → Worker Thread → IBKR Paper
                                        ↓
                         per ogni simbolo (indipendente):
                         1m bar stream (keepUpToDate=True)
                              ↓
                         LiveSessionBuilder → ORB
                              ↓
                         LiveSignalDetector (LONG/SHORT/BOTH)
                              ↓ SIGNAL
                         OptionExecutionIntent (CALL/PUT mapping)
                              ↓
                         OptionContractSelector (chain + strike + quote)
                              ↓
                         build_option_entry_order (BUY LMT @ ask)
                              ↓
                    ┌── OBSERVE: log theoretical, no placeOrder
                    └── PAPER:   IBKROptionExecutor.submit_entry()
                                      ↓ FILL
                                 UnderlyingExitMonitor (structural stop/target)
                                      ↓ TRIGGER
                                 OptionExitExecutor (SELL MKT)
                                      ↓ FILL
                                 ExitResultActivator → WIN/LOSS
                                      ↓
                                 TRADE_COMPLETED event (gross P&L)
```

### Principio Architetturale Chiave
Il bot **analizza l'UNDERLYING** (QQQ, SPY, etc.) ma **esegue tramite OPTIONS** (CALL/PUT).
Lo stop/target sono livelli strutturali dell'underlying, NON premi dell'opzione.
Il SELL è MARKET (exit immediata quando il trigger structurale scatta).
Il WIN/LOSS è determinato dal motivo strutturale (TARGET/STOP), NON dal P&L del premium.

---

## Componenti Core Costruiti

### Pipeline di Esecuzione (T11–T14)
- **T11 UnderlyingExitMonitor**: Monitora barre 1m underlying per stop/target. LONG: low≤stop, high≥target. Ambiguità same-bar → conservativo STOP. Terminale/idempotente. Activation-time previene trigger pre-fill.
- **T12 OptionExitExecutor**: SELL MARKET dopo trigger underlying. Protezione duplicati via entry_order_id.
- **T13 ExitFillMonitor**: Conferma fill SELL. TARGET→WIN, STOP→LOSS. Idempotente via exit_order_id.
- **T14 TradeOrchestrator**: State machine completa: WAITING→ENTRY_SUBMITTED→POSITION_OPEN→EXIT_SUBMITTED→DONE/WAITING. Tutte le dipendenze iniettate.

### Runner e Modi (T15–T16)
- **T15 MaxBotRunner**: Connessione IBKR Paper, verifica account Paper (fail-closed), streaming 1m via `reqHistoricalData(keepUpToDate=True)`, filtro RTH, bootstrap barre storiche, deduplicazione.
- **T16 ObserveOrchestrator**: OBSERVE_ONLY — pipeline completa senza ordini. Rileva segnali reali, seleziona opzioni reali, costruisce ordini teorici, monitora esiti teorici. Default mode.

### Multi-Symbol (T17A–T17B)
- **T17A DualSignalDetector**: Wrappa LONG+SHORT detector per `--direction BOTH`. La direzione risolta fluisce al mapping opzioni.
- **T17B Watchlist**: `SymbolRuntime` per-symbol con lifecycle indipendente. Shared IB connection. Trade limits OFF per fase test.

### Telemetria e Infrastruttura (T17C–T17D4)
- **T17C EventStream**: 28 tipi evento, sequenza monotona, JSON/Markdown export. `build_trade_summary()` con gross P&L.
- **T17D1**: Emit callback iniettato negli orchestrator. Duplicate prevention per eventi terminali.
- **T17D2 ControlAPI**: Flask HTTP API locale. 7 endpoint. Token auth. LIVE mode bloccato permanentemente.
- **T17D3 PWA**: Dashboard iPhone dark-theme. Polling real-time. Start/stop. Watchlist config. Symbol cards con stato. Event timeline live. Export sessione.
- **T17D4 Launcher**: `./start_maxbot.sh` — un comando. Stampa URL LAN per iPhone.

### Context Levels (T17E2C1–T17E2C2)
- **PDH/PDL**: Calcolati dal canonical `compute_pdh_pdl` usando barre RTH della sessione precedente via IBKR.
- **PMH/PML**: Calcolati da barre premarket (04:00–09:29 ET, `useRTH=False`). Flag `premarket_final` (BUILDING prima dell'apertura, FINAL dopo). Esposti in API e PWA.
- Entrambi sono **CONTEXT LEVELS** — non generano entry. La logica di ingresso rimane esclusivamente ORB_HIGH/ORB_LOW.

---

## Bug Critici Trovati e Risolti

### 1. Python 3.14 Incompatibilità (T17E1)
**Causa:** `eventkit` (dipendenza di `ib_insync`) chiama `asyncio.get_event_loop()` che in Python 3.14 non crea più automaticamente un loop → RuntimeError all'import.
**Fix:** Constraint `>=3.11,<3.14` in pyproject.toml + guard espliciti in launcher/preflight/run_server.

### 2. Chain Selection Seleziona Classi Adjusted (T17E3B2)
**Causa:** `_pick_chain()` prendeva il primo chain SMART → `2QQQ` (classe adjusted, 1 strike a 625.0) invece di `QQQ` (classe standard, centinaia di strike).
**Fix:** Ranking multi-criterio: tradingClass match, multiplier=100, strikes bracketano il prezzo, expirations future. Strike fallback progressivo ITM se la qualifica fallisce.

### 3. Error 321 Quote Request (T17E3B2)
**Causa:** `reqMktData(option, "106", snapshot=True)` — IBKR Error 321: snapshot incompatibile con generic tick types.
**Fix:** `reqMktData(option, "", snapshot=True)` — nessun generic tick.

### 4. Event Loop Isolation Flask ↔ IBKR (T17E4)
**Causa:** `MaxBotRunner` costruito nel Flask request thread che non ha asyncio event loop → `eventkit` crash.
**Fix:** Runner costruito INSIDE worker thread con `asyncio.new_event_loop()` + `set_event_loop()`. Flask fa solo validazione config + spawn thread.

### 5. Bar Callback Error Swallowing (T17E5)
**Causa:** Eccezioni nelle callback `_on_bar_update` swallowed silenziosamente da eventkit → bars processate ma errori invisibili.
**Fix:** try/except con `log.error(exc_info=True)` in ogni callback. Heartbeat ogni 60 iterazioni logga bar count per simbolo.

### 6. SPY Dead Feed / INITIALIZING Forever (T17E6 + T17E6A)
**Causa:** IBKR `keepUpToDate=True` può silenziosamente smettere di inviare update per un singolo contratto (probabilmente pacing violation per 9 richieste simultanee). SPY non riceveva mai la prima bar completata → restava INITIALIZING senza triggerare resubscribe.
**Fix:** Feed health model (INITIALIZING/LIVE/STALE) con timeout 180s durante RTH. Auto-resubscribe per-symbol con cooldown 5 min. INITIALIZING timeout per feed che non consegnano mai la prima bar.

---

## Stato Mac Mini Reale (2026-08-12)

### Confermato Funzionante
- Connessione TWS Paper + verifica Paper account
- 9 simboli qualificati e streaming
- 8/9 simboli con live bar callbacks funzionanti
- PDH/PDL + PMH/PML context levels calcolati
- Option chain selection corretta (standard, non adjusted)
- PWA dashboard operativa da iPhone
- Session event logging con export

### Issue Aperto
**SPY**: subscription bootstrap OK (6 bars) ma nessun completed-bar callback. IBKR-side issue. Meccanismo auto-resubscribe implementato, da verificare live.

---

## Decisioni Frozen (Nuove in Questa Sessione)
- Entry: CONFIRMATION_CLOSE, BUY LMT @ ask
- Exit: SELL MKT su trigger underlying
- Options: 0DTE preferred, 1-strike ITM, quantity=1
- Same-bar stop+target → STOP (conservativo)
- Risultato strategia da motivo strutturale, NON P&L opzione
- Python >=3.11,<3.14
- Trade limits OFF per test
- Default: `--direction BOTH`, `--execution-mode OBSERVE_ONLY`
- LIVE execution mode bloccato permanentemente
- Chain: tradingClass=symbol, multiplier=100, bracket underlying
- Quote: `reqMktData(option, "", snapshot=True)`
- Feed stale: 180s RTH, resubscribe cooldown 300s

---

## Prossimi Passi
1. **Verifica live SPY auto-resubscribe** durante mercato aperto
2. **Prima sessione OBSERVE_ONLY completa** — confronto MaxBot vs decisioni discrezionali Max
3. **Prima sessione PAPER_EXECUTE** — dopo confidenza OBSERVE

---

## Metriche Sessione
- **Task completati:** 28 (T11–T17E6A)
- **Moduli creati:** 22 file Python + 3 file UI + 1 launcher + 1 preflight
- **Test totali nuova suite live:** 507
- **Commit:** 28
- **Bug critici risolti in produzione reale:** 6
