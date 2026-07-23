/**
 * types.ts
 * Tipi condivisi per il sistema provider market data.
 * Nessuna dipendenza esterna — importabile da qualsiasi Edge Function.
 */

// ─── Formato normalizzato barra OHLCV ───────────────────────────────────────

export interface NormalizedBar {
  simbolo: string;
  timeframe: string;
  ts: string;         // ISO 8601 UTC, es. "2026-07-21T13:30:00.000Z"
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  provider: string;   // "yahoo" | "alpaca_iex" | "alpaca_sip"
  adjustment: string; // "adjusted" | "unadjusted"
  session: string;    // "regular" | "extended" | "unknown"
}

// ─── Barra grezza proveniente da provider (prima della normalizzazione) ──────

export interface RawBar {
  ts_raw: string;      // timestamp originale dal provider
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

// ─── Barra come presente in public.candles ───────────────────────────────────

export interface DatabaseBar {
  simbolo: string;
  timeframe: string;
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

// ─── Errore di validazione OHLCV ────────────────────────────────────────────

export interface ValidationError {
  ts: string;
  field: string;
  reason: string;
  value: unknown;
}

// ─── Configurazione richiesta al provider ────────────────────────────────────

export interface FetchRequest {
  symbol: string;
  timeframe: string;   // "5m" | "1m" | "1h" | "1d"
  start: string;       // ISO 8601 UTC
  end: string;         // ISO 8601 UTC
  regularHoursOnly: boolean;
  adjustment: "adjusted" | "unadjusted";
}

// ─── Risposta del provider ───────────────────────────────────────────────────

export interface FetchResult {
  symbol: string;
  provider: string;
  bars: NormalizedBar[];
  warnings: string[];
  errors: string[];
  raw_count: number;    // barre prima del filtro
  filtered_count: number; // barre dopo filtro regular hours
}

// ─── Risultato confronto per simbolo (dry_run) ──────────────────────────────

export interface CompareResult {
  symbol: string;
  provider_bars: number;
  database_bars: number;
  matched_bars: number;
  missing_in_provider: string[];   // ts presenti in DB ma non nel provider
  missing_in_database: string[];   // ts presenti nel provider ma non in DB
  invalid_bars: ValidationError[];
  ohlc_differences: OhlcDiffSummary;
  volume_differences: VolumeDiffSummary;
  warnings: string[];
}

export interface OhlcDiffSummary {
  open:  FieldDiff;
  high:  FieldDiff;
  low:   FieldDiff;
  close: FieldDiff;
}

export interface FieldDiff {
  mean_abs: number;
  max_abs: number;
  mean_pct: number;
  within_001pct: number;
  within_005pct: number;
  within_010pct: number;
  above_010pct: number;
}

export interface VolumeDiffSummary {
  ratio_median: number;
  ratio_mean: number;
  ratio_std: number;
  ratio_min: number;
  ratio_max: number;
  correlation: number | null;
  zero_in_provider: number;
  zero_in_database: number;
}

// ─── Risultato scrittura per simbolo (write mode) ───────────────────────────

export interface WriteResult {
  symbol:        string;
  rows_before:   number;   // righe in DB prima dell'upsert (nella finestra data)
  inserted:      number;   // righe nuove scritte
  updated:       number;   // righe esistenti con almeno un campo OHLCV/volume cambiato
  unchanged:     number;   // righe già identiche, nessuna modifica applicata
  skipped:       number;   // barre invalide non scritte
  rows_after:    number;   // righe in DB dopo l'upsert (nella finestra data)
  invalid_bars:  ValidationError[];
  warnings:      string[];
  example_row:   DatabaseBar | null;  // prima riga scritta o aggiornata (per verifica)
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
  status:       FunctionStatus;
  mode:         "write";
  provider:     string;
  symbols:      string[];
  timeframe:    string;
  date:         string;
  rows_before:  number;   // COUNT(*) totale prima
  rows_after:   number;   // COUNT(*) totale dopo
  results:      WriteResult[];
  errors:       string[];
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
