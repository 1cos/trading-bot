# Detector V2 — Session Handoff

## Prompt per nuova sessione

Copia e incolla questo come primo messaggio della nuova sessione.

---

Continua a lavorare sul repository GitHub 1cos/trading-bot.

Auth token per push: [INSERISCI TOKEN]

Git config: claude@anthropic.com / Claude

## STATO ATTUALE DEL PROGETTO

Repository: /Users/massimilianozubboli/trading_bot
Ultimo commit: 37cdef4
Python tests: 1780 passed, 1 failed (pre-existing)
JS tests: 16/16 pass

## COSA È STATO FATTO

**Detector V1 (frozen):**
- BDRR pipeline completo: Break → Displacement → Retest → Rejection
- Sequence invalidation (consecutive_orb_closes=2)
- Multi-timeframe infrastructure (1m/2m/3m/5m/10m)
- Python-to-JavaScript parity (1780 Python tests, 16 JS suites)
- Training Workspace 8 UI (frozen, non modificare)
- Backtest Lab separato dal Training Workspace

**Detector Audit System (completato questa sessione):**
- DetectorAuditRecord/v1 contract (59 tests)
- Audit record builder — runner result → audit record (52 tests)
- Audit candidate selector — is_audit_worthy() (28 tests)
- Audit visual exporter — chart-ready events (30 tests)
- Audit batch generator con HTML review interface (38 tests)
- Stratified sampling --balanced-failed-stage
- SEQUENCE_INVALIDATED aggiunto a FailedStage enum

**Distribution Analysis (5m, 9 simboli, 60 sessioni):**
- 1080 runner results → 31 VALID, 724 audit-worthy rejected
- RETEST_BEFORE_DISPLACEMENT: 438 (60.5%)
- NO_QUALIFYING_REJECTION_CANDLE: 200 (27.6%)
- RETEST_NOT_FOUND: 86 (11.9%)
- DISPLACEMENT_MINIMUM_NOT_MET: 0 (min_displacement_ticks è None)
- SEQUENCE_INVALIDATED: 0 (raro su 5m)

**Documenti di progetto creati:**
- BDRR_ARCHITECTURE_PHILOSOPHY.md — 13 principi architetturali
- TRADING_JOURNAL_DISCOVERIES.md — 5 discoveries registrate
- SESSION_LOG_2026-07-31.md

## LIMITAZIONI NOTE DEL DETECTOR V1

1. Max 1 setup per sessione — non ri-scanna dopo invalidazione
2. Richiede almeno 1 bar di displacement — rifiuta RETEST_BEFORE_DISPLACEMENT
3. No Order Block detection
4. No premarket data
5. Rejection candle geometry: hardcoded (wick ≥ 50%, body ≤ 50%, favorable close ≥ 80%)
6. DISPLACEMENT_MINIMUM_NOT_MET mai osservato (soglia non configurata)

## FILE DA NON MODIFICARE

- training_workspace_8.html (review data frozen)
- Tutti i *_finder.py, sequence_validator.py (detector V1 frozen)
- detection_result_builder.py (contract frozen)
- Test expectations esistenti
- dati/*_5m.csv (git-tracked)

## TASK — Detector V2

Il prossimo milestone è definire cosa cambia nel detector basandosi sulle evidenze raccolte dal sistema di audit.

**Prima di modificare qualsiasi codice del detector:**

1. Leggere BDRR_ARCHITECTURE_PHILOSOPHY.md
2. Leggere TRADING_JOURNAL_DISCOVERIES.md (DISCOVERY-004 e DISCOVERY-005)
3. Seguire il Decision Making Order (Sezione 11 della Philosophy)

**Domande aperte per la review dell'audit batch:**
- I 30 RETEST_BEFORE_DISPLACEMENT sono correttamente rifiutati?
- I 30 NO_QUALIFYING_REJECTION_CANDLE hanno geometria effettivamente insufficiente?
- I 30 RETEST_NOT_FOUND mostrano setup che Max avrebbe preso?
- Quali rejection rules falliscono più spesso? (FAVORABLE_CLOSE_LOCATION_TOO_LOW?)

**Potenziali Lab parameters da esplorare:**
- `allow_immediate_retest` — permetti un retest prima del displacement
- `min_break_distance_ticks` — richiedi break più forte
- `favorable_close_location_min` — attualmente hardcoded a 0.80
- `rejection_wick_ratio_min` — attualmente hardcoded a 0.50
- `max_body_ratio` — attualmente hardcoded a 0.50

**Regola fondamentale:** nessun parametro diventa detector rule senza evidenze da almeno 50 review. Vedi Architecture Philosophy Sezione 6.

## COME AVVIARE

```bash
cd /Users/massimilianozubboli/trading_bot
source venv/bin/activate
python -m pytest backend/tests/ -q --tb=no  # deve dare 1780 passed
python backend/generate_audit_batches.py --balanced-failed-stage --max-records 90  # audit batch
open backend/output/audit_batch_*.html  # review interface
```

---
