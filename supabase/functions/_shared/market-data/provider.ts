/**
 * provider.ts
 * Interfaccia astratta MarketDataProvider e logica condivisa:
 * - validazione OHLCV
 * - filtro regular hours
 * - normalizzazione timestamp UTC
 * - deduplicazione
 * - confronto con database
 */

import type {
  FetchRequest,
  FetchResult,
  NormalizedBar,
  ValidationError,
  DatabaseBar,
  CompareResult,
  OhlcDiffSummary,
  VolumeDiffSummary,
  FieldDiff,
} from "./types.ts";

export type {
  FetchRequest,
  FetchResult,
  NormalizedBar,
  ValidationError,
  DatabaseBar,
  CompareResult,
  OhlcDiffSummary,
  VolumeDiffSummary,
  FieldDiff,
};

import {
  MARKET_OPEN_UTC,
  MARKET_CLOSE_UTC,
  TIMEFRAME_MINUTES,
} from "./types.ts";

// ─── Interfaccia provider ────────────────────────────────────────────────────

export interface MarketDataProvider {
  /** Identificatore univoco del provider, es. "yahoo", "alpaca_iex" */
  readonly name: string;

  /**
   * Recupera barre OHLCV grezze dal provider e le restituisce normalizzate.
   * Non scrive mai sul database.
   */
  fetchBars(request: FetchRequest): Promise<FetchResult>;

  /**
   * Normalizza una barra grezza nel formato NormalizedBar.
   * Separato da fetchBars per testabilità.
   */
  normalizeBar(
    raw: Record<string, unknown>,
    symbol: string,
    timeframe: string,
  ): NormalizedBar | null;
}

// ─── Validazione OHLCV ──────────────────────────────────────────────────────

export function validateBar(bar: NormalizedBar): ValidationError[] {
  const errors: ValidationError[] = [];
  const ts = bar.ts;

  const fields = ["open", "high", "low", "close"] as const;
  for (const f of fields) {
    const v = bar[f];
    if (v === null || v === undefined || !isFinite(v)) {
      errors.push({ ts, field: f, reason: "non numerico o null", value: v });
    }
  }

  if (errors.length > 0) return errors; // non ha senso proseguire senza OHLC validi

  if (bar.high < bar.low)   errors.push({ ts, field: "high/low", reason: "high < low", value: `${bar.high}/${bar.low}` });
  if (bar.high < bar.open)  errors.push({ ts, field: "high/open", reason: "high < open", value: `${bar.high}/${bar.open}` });
  if (bar.high < bar.close) errors.push({ ts, field: "high/close", reason: "high < close", value: `${bar.high}/${bar.close}` });
  if (bar.low  > bar.open)  errors.push({ ts, field: "low/open", reason: "low > open", value: `${bar.low}/${bar.open}` });
  if (bar.low  > bar.close) errors.push({ ts, field: "low/close", reason: "low > close", value: `${bar.low}/${bar.close}` });

  if (bar.volume !== null && bar.volume !== undefined) {
    if (!isFinite(bar.volume) || bar.volume < 0) {
      errors.push({ ts, field: "volume", reason: "volume non valido", value: bar.volume });
    }
  }

  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) {
      errors.push({ ts, field: "ts", reason: "timestamp non valido", value: ts });
    }
  } catch {
    errors.push({ ts, field: "ts", reason: "timestamp non parsabile", value: ts });
  }

  return errors;
}

// ─── Filtro regular market hours USA ────────────────────────────────────────

/**
 * Mantiene solo barre che cadono nell'intervallo regular hours USA.
 * Per 5m: 13:30 UTC (09:30 ET) → 19:55 UTC (15:55 ET) inclusi.
 * Ignora sabato, domenica.
 */
export function filterRegularHours(
  bars: NormalizedBar[],
  timeframeMinutes: number,
): NormalizedBar[] {
  return bars.filter((bar) => {
    const d = new Date(bar.ts);
    const dow = d.getUTCDay();
    if (dow === 0 || dow === 6) return false; // weekend

    const hhmm = d.getUTCHours() * 60 + d.getUTCMinutes();
    const openMin  = 13 * 60 + 30; // 13:30 UTC
    // last bar open = 19:55 per 5m (chiusura 20:00)
    const closeMin = 20 * 60 - timeframeMinutes; // 19:55 per 5m

    return hhmm >= openMin && hhmm <= closeMin;
  });
}

// ─── Normalizzazione timestamp UTC ──────────────────────────────────────────

/** Converte qualsiasi timestamp in stringa ISO 8601 UTC con millisecondi zero. */
export function toUtcIso(ts: string | number): string {
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (isNaN(d.getTime())) throw new Error(`Timestamp non valido: ${ts}`);
  // Tronca ai secondi (nessun millisecondo)
  d.setUTCMilliseconds(0);
  return d.toISOString();
}

// ─── Deduplicazione ─────────────────────────────────────────────────────────

/** Rimuove duplicati per (simbolo, timeframe, ts). Mantiene il primo. */
export function deduplicate(bars: NormalizedBar[]): {
  unique: NormalizedBar[];
  duplicates: string[];
} {
  const seen = new Set<string>();
  const unique: NormalizedBar[] = [];
  const duplicates: string[] = [];

  for (const bar of bars) {
    const key = `${bar.simbolo}|${bar.timeframe}|${bar.ts}`;
    if (seen.has(key)) {
      duplicates.push(bar.ts);
    } else {
      seen.add(key);
      unique.push(bar);
    }
  }
  return { unique, duplicates };
}

// ─── Confronto provider vs database ─────────────────────────────────────────

export function compareBars(
  symbol: string,
  providerBars: NormalizedBar[],
  dbBars: DatabaseBar[],
  invalidBars: ValidationError[],
): CompareResult {
  const provMap = new Map<string, NormalizedBar>();
  for (const b of providerBars) provMap.set(b.ts, b);

  const dbMap = new Map<string, DatabaseBar>();
  for (const b of dbBars) dbMap.set(b.ts, b);

  const allTs = new Set([...provMap.keys(), ...dbMap.keys()]);
  const matched: Array<{ prov: NormalizedBar; db: DatabaseBar }> = [];
  const missingInProvider: string[] = [];
  const missingInDatabase: string[] = [];

  for (const ts of allTs) {
    const p = provMap.get(ts);
    const d = dbMap.get(ts);
    if (p && d) matched.push({ prov: p, db: d });
    else if (!p) missingInProvider.push(ts);
    else missingInDatabase.push(ts);
  }

  const ohlc = computeOhlcDiff(matched);
  const vol  = computeVolumeDiff(matched);

  return {
    symbol,
    provider_bars:      providerBars.length,
    database_bars:      dbBars.length,
    matched_bars:       matched.length,
    missing_in_provider: missingInProvider.sort(),
    missing_in_database: missingInDatabase.sort(),
    invalid_bars:       invalidBars,
    ohlc_differences:   ohlc,
    volume_differences: vol,
    warnings: buildWarnings(symbol, providerBars.length, dbBars.length, matched.length),
  };
}

// ─── Metriche OHLC ──────────────────────────────────────────────────────────

function computeOhlcDiff(
  matched: Array<{ prov: NormalizedBar; db: DatabaseBar }>,
): OhlcDiffSummary {
  const empty: FieldDiff = {
    mean_abs: 0, max_abs: 0, mean_pct: 0,
    within_001pct: 0, within_005pct: 0, within_010pct: 0, above_010pct: 0,
  };
  if (matched.length === 0) {
    return { open: empty, high: empty, low: empty, close: empty };
  }

  const result = {} as OhlcDiffSummary;
  for (const field of ["open", "high", "low", "close"] as const) {
    const absDiffs: number[] = [];
    const pctDiffs: number[] = [];

    for (const { prov, db } of matched) {
      const p = prov[field];
      const d = (db as unknown as Record<string, number>)[field];
      if (!isFinite(p) || !isFinite(d)) continue;
      const abs = Math.abs(p - d);
      const pct = d !== 0 ? (abs / Math.abs(d)) * 100 : 0;
      absDiffs.push(abs);
      pctDiffs.push(pct);
    }

    const n = absDiffs.length;
    const meanAbs = n > 0 ? absDiffs.reduce((a, b) => a + b, 0) / n : 0;
    const maxAbs  = n > 0 ? Math.max(...absDiffs) : 0;
    const meanPct = n > 0 ? pctDiffs.reduce((a, b) => a + b, 0) / n : 0;

    result[field] = {
      mean_abs:       round4(meanAbs),
      max_abs:        round4(maxAbs),
      mean_pct:       round6(meanPct),
      within_001pct:  pctDiffs.filter((p) => p <= 0.01).length,
      within_005pct:  pctDiffs.filter((p) => p <= 0.05).length,
      within_010pct:  pctDiffs.filter((p) => p <= 0.10).length,
      above_010pct:   pctDiffs.filter((p) => p  > 0.10).length,
    };
  }
  return result;
}

// ─── Metriche volume ─────────────────────────────────────────────────────────

function computeVolumeDiff(
  matched: Array<{ prov: NormalizedBar; db: DatabaseBar }>,
): VolumeDiffSummary {
  const empty: VolumeDiffSummary = {
    ratio_median: 0, ratio_mean: 0, ratio_std: 0,
    ratio_min: 0, ratio_max: 0, correlation: null,
    zero_in_provider: 0, zero_in_database: 0,
  };
  if (matched.length === 0) return empty;

  const ratios: number[] = [];
  const provVols: number[] = [];
  const dbVols: number[] = [];
  let zeroProvider = 0, zeroDatabase = 0;

  for (const { prov, db } of matched) {
    const pv = prov.volume ?? 0;
    const dv = db.volume ?? 0;
    if (pv === 0) zeroProvider++;
    if (dv === 0) zeroDatabase++;
    if (dv > 0) ratios.push(pv / dv);
    provVols.push(pv);
    dbVols.push(dv);
  }

  if (ratios.length === 0) return { ...empty, zero_in_provider: zeroProvider, zero_in_database: zeroDatabase };

  const sorted  = [...ratios].sort((a, b) => a - b);
  const n       = ratios.length;
  const mean    = ratios.reduce((a, b) => a + b, 0) / n;
  const median  = n % 2 === 0
    ? (sorted[n / 2 - 1] + sorted[n / 2]) / 2
    : sorted[Math.floor(n / 2)];
  const variance = ratios.reduce((a, b) => a + (b - mean) ** 2, 0) / n;
  const std     = Math.sqrt(variance);

  // Correlazione di Pearson
  let corr: number | null = null;
  if (n >= 3) {
    const meanP = provVols.reduce((a, b) => a + b, 0) / n;
    const meanD = dbVols.reduce((a, b) => a + b, 0) / n;
    const num   = provVols.reduce((s, p, i) => s + (p - meanP) * (dbVols[i] - meanD), 0);
    const denP  = Math.sqrt(provVols.reduce((s, p) => s + (p - meanP) ** 2, 0));
    const denD  = Math.sqrt(dbVols.reduce((s, d) => s + (d - meanD) ** 2, 0));
    corr = denP > 0 && denD > 0 ? round4(num / (denP * denD)) : null;
  }

  return {
    ratio_median:     round4(median),
    ratio_mean:       round4(mean),
    ratio_std:        round4(std),
    ratio_min:        round4(Math.min(...ratios)),
    ratio_max:        round4(Math.max(...ratios)),
    correlation:      corr,
    zero_in_provider: zeroProvider,
    zero_in_database: zeroDatabase,
  };
}

// ─── Avvisi ─────────────────────────────────────────────────────────────────

function buildWarnings(
  symbol: string,
  pBars: number,
  dBars: number,
  matched: number,
): string[] {
  const w: string[] = [];
  if (pBars === 0)  w.push(`Nessuna barra dal provider per ${symbol}`);
  if (dBars === 0)  w.push(`Nessuna barra in database per ${symbol}`);
  if (matched === 0 && pBars > 0 && dBars > 0) {
    w.push(`Nessun timestamp comune: provider=${pBars}, database=${dBars}`);
  }
  const missing = dBars - matched;
  if (missing > 0) w.push(`${missing} barre presenti in DB ma assenti nel provider`);
  return w;
}

// ─── Utility ─────────────────────────────────────────────────────────────────

function round4(n: number): number { return Math.round(n * 10000) / 10000; }
function round6(n: number): number { return Math.round(n * 1000000) / 1000000; }

/** Costruisce l'intervallo UTC per una data (fetch window completa). */
export function buildDateRange(date: string): { start: string; end: string } {
  return {
    start: `${date}T${MARKET_OPEN_UTC}Z`,
    end:   `${date}T${MARKET_CLOSE_UTC}Z`,
  };
}

/** Risolve il timeframe string in minuti. */
export function resolveTimeframeMinutes(tf: string): number {
  return TIMEFRAME_MINUTES[tf.toLowerCase()] ?? 5;
}
