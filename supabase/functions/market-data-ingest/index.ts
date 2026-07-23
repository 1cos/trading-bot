/**
 * market-data-ingest/index.ts
 * Edge Function Supabase — ingestione dati di mercato.
 *
 * MODALITÀ SUPPORTATE:
 *   dry_run  — solo lettura + confronto, zero scritture DB.
 *   write    — upsert idempotente su public.candles (service role).
 *
 * Richiesta POST:
 * {
 *   "provider":  "alpaca_iex",
 *   "symbols":   ["SPY"],
 *   "timeframe": "5m",
 *   "mode":      "dry_run" | "write",
 *   "date":      "YYYY-MM-DD"
 * }
 *
 * Sicurezza: header obbligatorio
 *   X-Function-Secret: <MARKET_DATA_FUNCTION_SECRET>
 *
 * Env richiesti (Supabase Secrets):
 *   SUPABASE_URL
 *   SUPABASE_SERVICE_ROLE_KEY
 *   MARKET_DATA_FUNCTION_SECRET
 *   ALPACA_API_KEY          (opzionale — solo per provider alpaca)
 *   ALPACA_API_SECRET       (opzionale — solo per provider alpaca)
 *
 * Idempotenza write:
 *   ON CONFLICT (simbolo, timeframe, ts) DO UPDATE solo se OHLCV/volume è
 *   effettivamente cambiato. Prima esecuzione → inserted = N.
 *   Esecuzioni successive → unchanged = N, inserted = 0, updated = 0.
 */

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

import type {
  FunctionOutput,
  DryRunOutput,
  WriteOutput,
  WriteResult,
  CompareResult,
  DatabaseBar,
  ValidationError,
} from "../_shared/market-data/types.ts";
import { buildDateRange } from "../_shared/market-data/provider.ts";
import { compareBars, validateBar } from "../_shared/market-data/provider.ts";
import { yahooProvider } from "../_shared/market-data/providers/yahoo.ts";
import { alpacaIexProvider, alpacaSipProvider } from "../_shared/market-data/providers/alpaca.ts";
import type { MarketDataProvider, NormalizedBar } from "../_shared/market-data/provider.ts";

// ─── Costanti ────────────────────────────────────────────────────────────────

const SUPPORTED_PROVIDERS = ["yahoo", "alpaca_iex", "alpaca_sip"] as const;
type SupportedProvider = typeof SUPPORTED_PROVIDERS[number];

const SUPPORTED_MODES = ["dry_run", "write"] as const;
type SupportedMode    = typeof SUPPORTED_MODES[number];

/** Numero massimo di righe per singolo upsert batch (Supabase PostgREST limit). */
const UPSERT_BATCH_SIZE = 500;

// ─── Handler principale ──────────────────────────────────────────────────────

Deno.serve(async (req: Request): Promise<Response> => {
  // ── CORS preflight ──
  if (req.method === "OPTIONS") {
    return new Response(null, {
      headers: {
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Function-Secret",
      },
    });
  }

  if (req.method !== "POST") {
    return jsonError(405, "Method not allowed. Usa POST.");
  }

  // ── Verifica secret header ──
  const secretError = verifySecret(req);
  if (secretError) return secretError;

  // ── Parse body ──
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return jsonError(400, "Body non valido: JSON richiesto.");
  }

  // ── Validazione input ──
  const validationError = validateInput(body);
  if (validationError) return validationError;

  const providerName = body["provider"] as SupportedProvider;
  const symbols      = (body["symbols"] as string[]).map((s) => s.toUpperCase());
  const timeframe    = (body["timeframe"] as string).toLowerCase();
  const mode         = body["mode"] as SupportedMode;
  const date         = body["date"] as string;

  // ── Seleziona provider ──
  const provider = resolveProvider(providerName);
  if (!provider) {
    return jsonError(400, `Provider '${providerName}' non riconosciuto. Valori accettati: ${SUPPORTED_PROVIDERS.join(", ")}`);
  }

  // ── Client Supabase (service role) ──
  const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
  const serviceKey  = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

  if (!supabaseUrl || !serviceKey) {
    return jsonError(500, "Configurazione server incompleta: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY mancanti.");
  }

  const supabase = createClient(supabaseUrl, serviceKey, {
    auth: { persistSession: false },
  });

  // ── Range temporale ──
  const { start, end } = buildDateRange(date);

  // ──────────────────────────────────────────────────────────────────────────
  // MODE: dry_run
  // ──────────────────────────────────────────────────────────────────────────
  if (mode === "dry_run") {
    const results: CompareResult[] = [];
    const globalErrors: string[]   = [];

    for (const symbol of symbols) {
      try {
        const result = await processSymbolDryRun({
          symbol, timeframe, start, end, provider, supabase, date,
        });
        results.push(result);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        globalErrors.push(`Errore su ${symbol}: ${msg}`);
        results.push(emptyCompareResult(symbol));
      }
    }

    const hasErrors   = globalErrors.length > 0 || results.some((r) => r.invalid_bars.length > 0);
    const hasWarnings = results.some((r) => r.warnings.length > 0);
    const status = hasErrors
      ? (results.some((r) => r.matched_bars > 0) ? "partial" : "error")
      : hasWarnings ? "partial" : "success";

    const output: DryRunOutput = {
      status,
      mode:      "dry_run",
      provider:  providerName,
      symbols,
      timeframe,
      date,
      results,
      errors:    globalErrors,
    };

    return jsonOk(output);
  }

  // ──────────────────────────────────────────────────────────────────────────
  // MODE: write
  // ──────────────────────────────────────────────────────────────────────────

  // Conta righe totali prima dell'operazione
  const { count: countBefore, error: countBeforeErr } = await supabase
    .from("candles")
    .select("*", { count: "exact", head: true });

  if (countBeforeErr) {
    return jsonError(500, `Errore conteggio pre-upsert: ${countBeforeErr.message}`);
  }

  const rowsBefore = countBefore ?? 0;
  const writeResults: WriteResult[] = [];
  const globalErrors: string[]      = [];

  for (const symbol of symbols) {
    try {
      const result = await processSymbolWrite({
        symbol, timeframe, start, end, provider, supabase, date,
      });
      writeResults.push(result);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      globalErrors.push(`Errore su ${symbol}: ${msg}`);
      writeResults.push(emptyWriteResult(symbol));
    }
  }

  // Conta righe totali dopo l'operazione
  const { count: countAfter, error: countAfterErr } = await supabase
    .from("candles")
    .select("*", { count: "exact", head: true });

  if (countAfterErr) {
    return jsonError(500, `Errore conteggio post-upsert: ${countAfterErr.message}`);
  }

  const rowsAfter = countAfter ?? 0;

  const hasErrors   = globalErrors.length > 0 || writeResults.some((r) => r.skipped > 0);
  const hasWarnings = writeResults.some((r) => r.warnings.length > 0);
  const status = hasErrors
    ? (writeResults.some((r) => r.inserted > 0 || r.unchanged > 0) ? "partial" : "error")
    : hasWarnings ? "partial" : "success";

  const output: WriteOutput = {
    status,
    mode:        "write",
    provider:    providerName,
    symbols,
    timeframe,
    date,
    rows_before: rowsBefore,
    rows_after:  rowsAfter,
    results:     writeResults,
    errors:      globalErrors,
  };

  return jsonOk(output);
});

// ─── Elaborazione singolo simbolo: DRY RUN ──────────────────────────────────

async function processSymbolDryRun(ctx: {
  symbol:    string;
  timeframe: string;
  start:     string;
  end:       string;
  provider:  MarketDataProvider;
  supabase:  ReturnType<typeof createClient>;
  date:      string;
}): Promise<CompareResult> {
  const { symbol, timeframe, start, end, provider, supabase } = ctx;

  const fetchResult = await provider.fetchBars({
    symbol, timeframe, start, end,
    regularHoursOnly: true,
    adjustment: "adjusted",
  });

  const invalidBars = fetchResult.bars.flatMap(validateBar);

  const { data: dbData, error: dbError } = await supabase
    .from("candles")
    .select("simbolo, timeframe, ts, open, high, low, close, volume")
    .eq("simbolo",   symbol)
    .eq("timeframe", timeframe)
    .gte("ts",       start)
    .lt("ts",        end)
    .order("ts", { ascending: true });

  if (dbError) {
    return {
      ...emptyCompareResult(symbol),
      warnings: [`Errore lettura DB: ${dbError.message}`],
    };
  }

  const dbBars: DatabaseBar[] = (dbData ?? []).map((r) => ({
    simbolo:   r.simbolo,
    timeframe: r.timeframe,
    ts:        new Date(r.ts).toISOString(),
    open:      Number(r.open),
    high:      Number(r.high),
    low:       Number(r.low),
    close:     Number(r.close),
    volume:    r.volume !== null ? Number(r.volume) : null,
  }));

  const compareResult = compareBars(symbol, fetchResult.bars, dbBars, invalidBars);

  compareResult.warnings = [
    ...fetchResult.warnings,
    ...fetchResult.errors.map((e) => `[PROVIDER ERROR] ${e}`),
    ...compareResult.warnings,
  ];

  if (fetchResult.raw_count !== fetchResult.filtered_count) {
    compareResult.warnings.push(
      `Provider: ${fetchResult.raw_count} barre raw → ${fetchResult.filtered_count} dopo filtro regular hours`,
    );
  }

  return compareResult;
}

// ─── Elaborazione singolo simbolo: WRITE ─────────────────────────────────────
//
// Strategia idempotente in tre passi:
//   1. Fetch barre dal provider (già filtrate, validate, deduplicate)
//   2. Leggi righe esistenti dal DB per lo stesso (simbolo, timeframe, finestra)
//   3. Classifica ogni barra provider in: insert / update / unchanged / skip
//   4. Upsert solo le righe che cambiano (insert + update); unchanged → nessuna scrittura
//
// La colonna `volume` in DB è bigint; la arrotondiamo a intero prima di scrivere.
// ─────────────────────────────────────────────────────────────────────────────

async function processSymbolWrite(ctx: {
  symbol:    string;
  timeframe: string;
  start:     string;
  end:       string;
  provider:  MarketDataProvider;
  supabase:  ReturnType<typeof createClient>;
  date:      string;
}): Promise<WriteResult> {
  const { symbol, timeframe, start, end, provider, supabase } = ctx;
  const warnings: string[] = [];

  // ── 1. Fetch dal provider ──
  const fetchResult = await provider.fetchBars({
    symbol, timeframe, start, end,
    regularHoursOnly: true,
    adjustment: "adjusted",
  });

  if (fetchResult.errors.length > 0) {
    fetchResult.errors.forEach((e) => warnings.push(`[PROVIDER ERROR] ${e}`));
  }
  if (fetchResult.warnings.length > 0) {
    warnings.push(...fetchResult.warnings);
  }

  // Separa barre valide da invalide
  const validBars:   NormalizedBar[]   = [];
  const invalidBars: ValidationError[] = [];

  for (const bar of fetchResult.bars) {
    const errs = validateBar(bar);
    if (errs.length === 0) {
      validBars.push(bar);
    } else {
      invalidBars.push(...errs);
    }
  }

  if (invalidBars.length > 0) {
    warnings.push(`${invalidBars.length} barre invalide escluse dall'upsert`);
  }

  // ── 2. Leggi esistenti dal DB ──
  const { data: existingData, error: readErr } = await supabase
    .from("candles")
    .select("simbolo, timeframe, ts, open, high, low, close, volume")
    .eq("simbolo",   symbol)
    .eq("timeframe", timeframe)
    .gte("ts",       start)
    .lt("ts",        end)
    .order("ts", { ascending: true });

  if (readErr) {
    warnings.push(`Errore lettura DB pre-upsert: ${readErr.message}`);
    return {
      ...emptyWriteResult(symbol),
      invalid_bars: invalidBars,
      warnings,
    };
  }

  const rowsBefore = (existingData ?? []).length;

  // Mappa ts → row esistente per lookup O(1)
  const existingMap = new Map<string, {
    open: number; high: number; low: number; close: number; volume: number | null;
  }>();

  for (const row of (existingData ?? [])) {
    const tsKey = new Date(row.ts).toISOString();
    existingMap.set(tsKey, {
      open:   Number(row.open),
      high:   Number(row.high),
      low:    Number(row.low),
      close:  Number(row.close),
      volume: row.volume !== null ? Number(row.volume) : null,
    });
  }

  // ── 3. Classifica barre ──
  type UpsertRow = {
    simbolo:   string;
    timeframe: string;
    ts:        string;
    open:      number;
    high:      number;
    low:       number;
    close:     number;
    volume:    number | null;
  };

  const toUpsert:  UpsertRow[] = [];   // insert + update (barre che effettivamente cambiano)
  const insertedTs: string[]   = [];   // ts delle nuove righe
  const updatedTs:  string[]   = [];   // ts delle righe aggiornate
  let   unchanged               = 0;

  for (const bar of validBars) {
    const tsKey  = bar.ts;  // già ISO UTC da toUtcIso()
    const volInt = bar.volume !== null ? Math.round(bar.volume) : null;

    const row: UpsertRow = {
      simbolo:   bar.simbolo,
      timeframe: bar.timeframe,
      ts:        tsKey,
      open:      round4(bar.open),
      high:      round4(bar.high),
      low:       round4(bar.low),
      close:     round4(bar.close),
      volume:    volInt,
    };

    const existing = existingMap.get(tsKey);

    if (!existing) {
      // Riga non presente in DB → INSERT
      toUpsert.push(row);
      insertedTs.push(tsKey);
    } else {
      // Riga già presente → confronta OHLCV
      const changed =
        round4(existing.open)  !== row.open  ||
        round4(existing.high)  !== row.high  ||
        round4(existing.low)   !== row.low   ||
        round4(existing.close) !== row.close ||
        (existing.volume !== null ? Math.round(existing.volume) : null) !== row.volume;

      if (changed) {
        toUpsert.push(row);
        updatedTs.push(tsKey);
      } else {
        unchanged++;
      }
    }
  }

  // ── 4. Upsert in batch ──
  let upsertError: string | null = null;

  if (toUpsert.length > 0) {
    // Suddividi in batch per non superare limiti PostgREST
    for (let i = 0; i < toUpsert.length; i += UPSERT_BATCH_SIZE) {
      const batch = toUpsert.slice(i, i + UPSERT_BATCH_SIZE);

      const { error: batchErr } = await supabase
        .from("candles")
        .upsert(batch, {
          onConflict:        "simbolo,timeframe,ts",
          ignoreDuplicates:  false,   // vogliamo DO UPDATE (non ignore)
        });

      if (batchErr) {
        upsertError = batchErr.message;
        warnings.push(`Errore upsert batch ${Math.floor(i / UPSERT_BATCH_SIZE) + 1}: ${batchErr.message}`);
        break;
      }
    }
  }

  // ── 5. Conta righe dopo ──
  const { data: afterData, error: afterErr } = await supabase
    .from("candles")
    .select("simbolo, timeframe, ts, open, high, low, close, volume", { count: "exact" })
    .eq("simbolo",   symbol)
    .eq("timeframe", timeframe)
    .gte("ts",       start)
    .lt("ts",        end);

  if (afterErr) {
    warnings.push(`Errore conteggio post-upsert: ${afterErr.message}`);
  }

  const rowsAfter = (afterData ?? []).length;

  // ── 6. Esempio riga scritta (prima in ordine temporale) ──
  let exampleRow: DatabaseBar | null = null;

  if (toUpsert.length > 0) {
    // Prendi il primo ts inserito/aggiornato e recupera la riga dal DB
    const firstTs = toUpsert
      .map((r) => r.ts)
      .sort()[0];

    const { data: exRows } = await supabase
      .from("candles")
      .select("simbolo, timeframe, ts, open, high, low, close, volume")
      .eq("simbolo",   symbol)
      .eq("timeframe", timeframe)
      .eq("ts",        firstTs)
      .limit(1);

    if (exRows && exRows.length > 0) {
      const r = exRows[0];
      exampleRow = {
        simbolo:   r.simbolo,
        timeframe: r.timeframe,
        ts:        new Date(r.ts).toISOString(),
        open:      Number(r.open),
        high:      Number(r.high),
        low:       Number(r.low),
        close:     Number(r.close),
        volume:    r.volume !== null ? Number(r.volume) : null,
      };
    }
  }

  if (upsertError) {
    // Ritorna partial result se l'upsert è fallito
    return {
      symbol,
      rows_before:  rowsBefore,
      inserted:     0,
      updated:      0,
      unchanged,
      skipped:      invalidBars.length,
      rows_after:   rowsBefore,   // nessuna modifica applicata
      invalid_bars: invalidBars,
      warnings,
      example_row:  null,
    };
  }

  return {
    symbol,
    rows_before:  rowsBefore,
    inserted:     insertedTs.length,
    updated:      updatedTs.length,
    unchanged,
    skipped:      invalidBars.length,
    rows_after:   rowsAfter,
    invalid_bars: invalidBars,
    warnings,
    example_row:  exampleRow,
  };
}

// ─── Sicurezza ───────────────────────────────────────────────────────────────

function verifySecret(req: Request): Response | null {
  const envSecret = Deno.env.get("MARKET_DATA_FUNCTION_SECRET") ?? "";

  if (!envSecret) {
    return jsonError(503, "Funzione non disponibile: MARKET_DATA_FUNCTION_SECRET non configurato.");
  }

  const headerSecret = req.headers.get("X-Function-Secret") ?? "";

  if (!headerSecret) {
    return jsonError(401, "Header X-Function-Secret mancante.");
  }

  if (!timingSafeEqual(envSecret, headerSecret)) {
    return jsonError(403, "Secret non valido.");
  }

  return null;
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

// ─── Validazione input ────────────────────────────────────────────────────────

function validateInput(body: Record<string, unknown>): Response | null {
  if (!body["provider"] || typeof body["provider"] !== "string") {
    return jsonError(400, "Campo 'provider' mancante o non stringa.");
  }
  if (!Array.isArray(body["symbols"]) || body["symbols"].length === 0) {
    return jsonError(400, "Campo 'symbols' mancante o array vuoto.");
  }
  if (body["symbols"].some((s) => typeof s !== "string" || !s.trim())) {
    return jsonError(400, "Tutti i simboli devono essere stringhe non vuote.");
  }
  if (!body["timeframe"] || typeof body["timeframe"] !== "string") {
    return jsonError(400, "Campo 'timeframe' mancante.");
  }
  if (!body["mode"] || typeof body["mode"] !== "string") {
    return jsonError(400, "Campo 'mode' mancante.");
  }
  if (!SUPPORTED_MODES.includes(body["mode"] as SupportedMode)) {
    return jsonError(400, `Mode '${body["mode"]}' non supportato. Valori accettati: ${SUPPORTED_MODES.join(", ")}`);
  }
  if (!body["date"] || typeof body["date"] !== "string") {
    return jsonError(400, "Campo 'date' mancante.");
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(body["date"] as string)) {
    return jsonError(400, "Campo 'date' deve essere in formato YYYY-MM-DD.");
  }
  if ((body["symbols"] as string[]).length > 10) {
    return jsonError(400, "Massimo 10 simboli per richiesta.");
  }
  return null;
}

// ─── Resolver provider ────────────────────────────────────────────────────────

function resolveProvider(name: string): MarketDataProvider | null {
  switch (name) {
    case "yahoo":      return yahooProvider;
    case "alpaca_iex": return alpacaIexProvider;
    case "alpaca_sip": return alpacaSipProvider;
    default:           return null;
  }
}

// ─── Utility ─────────────────────────────────────────────────────────────────

function jsonError(status: number, message: string): Response {
  return new Response(
    JSON.stringify({ status: "error", error: message }),
    {
      status,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
    },
  );
}

function jsonOk(data: FunctionOutput): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status:  200,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
  });
}

/** Arrotonda a 4 decimali — allinea al formato numeric(x,4) in DB. */
function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}

function emptyCompareResult(symbol: string): CompareResult {
  const emptyDiff = {
    mean_abs: 0, max_abs: 0, mean_pct: 0,
    within_001pct: 0, within_005pct: 0, within_010pct: 0, above_010pct: 0,
  };
  return {
    symbol,
    provider_bars:       0,
    database_bars:       0,
    matched_bars:        0,
    missing_in_provider: [],
    missing_in_database: [],
    invalid_bars:        [],
    ohlc_differences: { open: emptyDiff, high: emptyDiff, low: emptyDiff, close: emptyDiff },
    volume_differences: {
      ratio_median: 0, ratio_mean: 0, ratio_std: 0,
      ratio_min: 0, ratio_max: 0, correlation: null,
      zero_in_provider: 0, zero_in_database: 0,
    },
    warnings: [],
  };
}

function emptyWriteResult(symbol: string): WriteResult {
  return {
    symbol,
    rows_before:  0,
    inserted:     0,
    updated:      0,
    unchanged:    0,
    skipped:      0,
    rows_after:   0,
    invalid_bars: [],
    warnings:     [],
    example_row:  null,
  };
}
