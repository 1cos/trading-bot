/**
 * types.ts
 * Tipi condivisi per il sistema provider market data.
 * Nessuna dipendenza esterna — importabile da qualsiasi Edge Function.
 */

// ─── Formato normalizzato barra OHLCV ───────────────────────────────────────

export interface NormalizedBar {
  simbolo:    string;
  timeframe:  string;
  ts:         string;         // ISO 8601 UTC, es. "2026-07-21T13:30:00.000Z"
  open:       number;
  high:       number;
  low:        number;
  close:      number;
  volume:     number | null;
  provider:   string;         // "yahoo" | "alpaca_iex" | "alpaca_sip"
  adjustment: string;         // "adjusted" | "unadjusted"
  session:    string;         // "regular" | "extended" | "unknown"
}

// ─── Barra grezza proveniente da provider (prima della normalizzazione) ──────

export interface RawBar {
  ts_raw: string;
  open:   number;
  high:   number;
  low:    number;
  close:  number;
  volume: number | null;
}

// ─── Barra come presente in public.candles ───────────────────────────────────

export interface DatabaseBar {
  simbolo:   string;
  timeframe: string;
  ts:        string;
  open:      number;
  high:      number;
  low:       number;
  close:     number;
  volume:    number | null;
}

// ─── Errore di validazione OHLCV ────────────────────────────────────────────

export interface ValidationError {
  ts:     string;
  field:  string;
  reason: string;
  value:  unknown;
}

// ─── Configurazione richiesta al provider ────────────────────────────────────

export interface FetchRequest {
  symbol:           string;
  timeframe:        string;                   // "5m" | "1m" | "1h" | "1d"
  start:            string;                   // ISO 8601 UTC
  end:              string;                   // ISO 8601 UTC
  regularHoursOnly: boolean;
  adjustment:       "adjusted" | "unadjusted";
}

// ─── Risposta del provider ───────────────────────────────────────────────────

export interface FetchResult {
  symbol:         string;
  provider:       string;
  bars:           NormalizedBar[];
  warnings:       string[];
  errors:         string[];
  raw_count:      number;   // barre prima del filtro regular hours
  filtered_count: number;   // barre dopo filtro regular hours
}

// ─── Risultato confronto per simbolo (dry_run) ──────────────────────────────

export interface CompareResult {
  symbol:              string;
  provider_bars:       number;
  database_bars:       number;
  matched_bars:        number;
  missing_in_provider: string[];       // ts in DB ma non nel provider
  missing_in_database: string[];       // ts nel provider ma non in DB
  invalid_bars:        ValidationError[];
  ohlc_differences:    OhlcDiffSummary;
  volume_differences:  VolumeDiffSummary;
  warnings:            string[];
}

export interface OhlcDiffSummary {
  open:  FieldDiff;
  high:  FieldDiff;
  low:   FieldDiff;
  close: FieldDiff;
}

export interface FieldDiff {
  mean_abs:      number;
  max_abs:       number;
  mean_pct:      number;
  within_001pct: number;
  within_005pct: number;
  within_010pct: number;
  above_010pct:  number;
}

export interface VolumeDiffSummary {
  ratio_median:     number;
  ratio_mean:       number;
  ratio_std:        number;
  ratio_min:        number;
  ratio_max:        number;
  correlation:      number | null;
  zero_in_provider: number;
  zero_in_database: number;
}

// ─── Risultato scrittura per simbolo (write mode, INSERT-ONLY) ──────────────
//
// Policy approvata:
//   - INSERT barre mancanti con ON CONFLICT (simbolo,timeframe,ts) DO NOTHING
//   - MAI aggiornare righe esistenti (nessun DO UPDATE)
//   - Righe esistenti: classificate, riportate, non toccate
//
// Invarianti obbligatori (verificati a runtime):
//   existing = exact_match_count + ohlc_conflict_count
//              + volume_conflict_count + both_conflict_count
//   provider_bars = attempted_insert_count + existing + skipped_invalid
// ─────────────────────────────────────────────────────────────────────────────

/** Dettaglio di un singolo conflitto (barra già in DB con valori diversi). */
export interface ConflictExample {
  ts:               string;
  differing_fields: string[];        // es. ["volume"] o ["open","volume"]
  db_open:          number;
  db_high:          number;
  db_low:           number;
  db_close:         number;
  db_volume:        number | null;
  provider_open:    number;
  provider_high:    number;
  provider_low:     number;
  provider_close:   number;
  provider_volume:  number | null;
  volume_ratio:     number | null;   // provider_volume / db_volume; null se db_volume = 0
}

/**
 * Riepilogo dei conflitti.
 * Le quattro categorie sono mutualmente esclusive:
 *   exact_match_count   : OHLC identico  AND volume identico
 *   ohlc_conflict_count : OHLC diverso   AND volume identico
 *   volume_conflict_count: OHLC identico AND volume diverso
 *   both_conflict_count : OHLC diverso   AND volume diverso
 * La somma delle quattro deve sempre eguagliare existing_count.
 */
export interface ConflictSummary {
  existing_count:        number;   // totale barre già in DB (non toccate)
  exact_match_count:     number;   // OHLC identico, volume identico
  ohlc_conflict_count:   number;   // OHLC diverso,  volume identico
  volume_conflict_count: number;   // OHLC identico, volume diverso
  both_conflict_count:   number;   // OHLC diverso,  volume diverso
  examples:              ConflictExample[];  // max 5, ordinate per ts
}

/** Risultato completo per un singolo simbolo in mode=write. */
export interface WriteResult {
  symbol:                string;
  // ── Conteggi provider ──────────────────────────────────────────────────────
  provider_bars:         number;   // barre valide ricevute dal provider
  attempted_insert_count: number;  // barre classificate NEW e inviate all'INSERT
  // ── Conteggi DB confermati dalla query post-insert ─────────────────────────
  inserted:              number;   // rows_after - rows_before (confermato da DB)
  existing:              number;   // barre già presenti (non toccate)
  skipped_invalid:       number;   // barre invalide escluse
  // ── Conteggi riga (simbolo+timeframe+finestra) ─────────────────────────────
  symbol_date_rows_before: number; // query fresca prima dell'INSERT
  symbol_date_rows_after:  number; // query fresca dopo  l'INSERT
  // ── Conteggi globali (intera tabella candles) ──────────────────────────────
  global_rows_before:    number;   // COUNT(*) candles prima
  global_rows_after:     number;   // COUNT(*) candles dopo
  // ── Conflitti ──────────────────────────────────────────────────────────────
  conflicts:             ConflictSummary;
  // ── Invarianti ─────────────────────────────────────────────────────────────
  invariant_ok:          boolean;  // true se entrambi gli invarianti passano
  invariant_errors:      string[]; // descrizione degli invarianti violati
  // ── Avvisi ─────────────────────────────────────────────────────────────────
  warnings:              string[];
}

// ─── Output finale della Edge Function ──────────────────────────────────────

export type FunctionStatus = "success" | "partial" | "error";
export type FunctionMode   = "dry_run" | "write";

/** Output per mode=dry_run */
export interface DryRunOutput {
  status:    FunctionStatus;
  mode:      "dry_run";
  provider:  string;
  symbols:   string[];
  timeframe: string;
  date:      string;
  results:   CompareResult[];
  errors:    string[];
}

/** Output per mode=write */
export interface WriteOutput {
  status:             FunctionStatus;
  mode:               "write";
  provider:           string;
  symbols:            string[];
  timeframe:          string;
  date:               string;
  global_rows_before: number;   // COUNT(*) intera tabella candles, query fresca prima
  global_rows_after:  number;   // COUNT(*) intera tabella candles, query fresca dopo
  results:            WriteResult[];
  errors:             string[];
}

/** Union — usata nella firma dell'handler */
export type FunctionOutput = DryRunOutput | WriteOutput;

// ─── Costanti mercato USA ────────────────────────────────────────────────────

export const MARKET_OPEN_UTC  = "13:30:00"; // 09:30 ET
export const MARKET_CLOSE_UTC = "20:00:00"; // 16:00 ET (fine finestra fetch)
export const MARKET_LAST_BAR  = "19:55:00"; // 15:55 ET ultima barra 5m

/** Timeframe supportati e corrispondenza in minuti */
export const TIMEFRAME_MINUTES: Record<string, number> = {
  "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440,
};
