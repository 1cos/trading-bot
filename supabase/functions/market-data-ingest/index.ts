/**
 * market-data-ingest/index.ts
 * Edge Function Supabase — ingestione dati di mercato.
 *
 * MODALITÀ SUPPORTATE:
 *   dry_run  — lettura + confronto, zero scritture DB.
 *   write    — INSERT-ONLY su public.candles (service role).
 *              ON CONFLICT (simbolo, timeframe, ts) DO NOTHING.
 *              Nessun DO UPDATE. Le righe esistenti non vengono mai toccate.
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
 */

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

import type {
  FunctionOutput,
  DryRunOutput,
  WriteOutput,
  WriteResult,
  ConflictSummary,
  ConflictExample,
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

/** Righe per batch INSERT — rimane sotto il limite PostgREST. */
const INSERT_BATCH_SIZE = 500;

/** Massimo esempi di conflitto restituiti nella risposta. */
const MAX_CONFLICT_EXAMPLES = 5;

// ─── Handler principale ──────────────────────────────────────────────────────

Deno.serve(async (req: Request): Promise<Response> => {
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

  const secretError = verifySecret(req);
  if (secretError) return secretError;

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return jsonError(400, "Body non valido: JSON richiesto.");
  }

  const validationError = validateInput(body);
  if (validationError) return validationError;

  const providerName = body["provider"] as SupportedProvider;
  const symbols      = (body["symbols"] as string[]).map((s) => s.toUpperCase());
  const timeframe    = (body["timeframe"] as string).toLowerCase();
  const mode         = body["mode"] as SupportedMode;
  const date         = body["date"] as string;

  const provider = resolveProvider(providerName);
  if (!provider) {
    return jsonError(400, `Provider '${providerName}' non riconosciuto. Valori accettati: ${SUPPORTED_PROVIDERS.join(", ")}`);
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
  const serviceKey  = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

  if (!supabaseUrl || !serviceKey) {
    return jsonError(500, "Configurazione server incompleta: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY mancanti.");
  }

  const supabase = createClient(supabaseUrl, serviceKey, {
    auth: { persistSession: false },
  });

  const { start, end } = buildDateRange(date);

  // ──────────────────────────────────────────────────────────────────────────
  // MODE: dry_run
  // ──────────────────────────────────────────────────────────────────────────
  if (mode === "dry_run") {
    const results: CompareResult[] = [];
    const globalErrors: string[]   = [];

    for (const symbol of symbols) {
      try {
        results.push(await processSymbolDryRun({ symbol, timeframe, start, end, provider, supabase }));
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

    const output: DryRunOutput = { status, mode: "dry_run", provider: providerName, symbols, timeframe, date, results, errors: globalErrors };
    return jsonOk(output);
  }

  // ──────────────────────────────────────────────────────────────────────────
  // MODE: write  (INSERT-ONLY — ON CONFLICT DO NOTHING)
  // ──────────────────────────────────────────────────────────────────────────

  // ── Query globale fresca PRIMA dell'insert ──────────────────────────────────
  const { count: globalCountBefore, error: globalBeforeErr } = await supabase
    .from("candles")
    .select("*", { count: "exact", head: true });

  if (globalBeforeErr) {
    return jsonError(500, `Errore conteggio globale pre-insert: ${globalBeforeErr.message}`);
  }

  const globalRowsBefore = globalCountBefore ?? 0;
  const writeResults:    WriteResult[] = [];
  const globalErrors:    string[]      = [];

  for (const symbol of symbols) {
    try {
      writeResults.push(await processSymbolWrite({
        symbol, timeframe, start, end, provider, supabase,
        globalRowsBefore,
      }));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      globalErrors.push(`Errore su ${symbol}: ${msg}`);
      writeResults.push(emptyWriteResult(symbol));
    }
  }

  // ── Query globale fresca DOPO l'insert ─────────────────────────────────────
  const { count: globalCountAfter, error: globalAfterErr } = await supabase
    .from("candles")
    .select("*", { count: "exact", head: true });

  if (globalAfterErr) {
    return jsonError(500, `Errore conteggio globale post-insert: ${globalAfterErr.message}`);
  }

  const globalRowsAfter = globalCountAfter ?? 0;

  const hasErrors   = globalErrors.length > 0
    || writeResults.some((r) => !r.invariant_ok)
    || writeResults.some((r) => r.skipped_invalid > 0);
  const hasWarnings = writeResults.some((r) => r.warnings.length > 0);
  const status = hasErrors
    ? (writeResults.some((r) => r.inserted > 0) ? "partial" : "error")
    : hasWarnings ? "partial" : "success";

  const output: WriteOutput = {
    status, mode: "write", provider: providerName, symbols, timeframe, date,
    global_rows_before: globalRowsBefore,
    global_rows_after:  globalRowsAfter,
    results: writeResults, errors: globalErrors,
  };
  return jsonOk(output);
});

// ─── processSymbolDryRun ─────────────────────────────────────────────────────

async function processSymbolDryRun(ctx: {
  symbol:    string;
  timeframe: string;
  start:     string;
  end:       string;
  provider:  MarketDataProvider;
  supabase:  ReturnType<typeof createClient>;
}): Promise<CompareResult> {
  const { symbol, timeframe, start, end, provider, supabase } = ctx;

  const fetchResult = await provider.fetchBars({
    symbol, timeframe, start, end, regularHoursOnly: true, adjustment: "adjusted",
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
    return { ...emptyCompareResult(symbol), warnings: [`Errore lettura DB: ${dbError.message}`] };
  }

  const dbBars: DatabaseBar[] = (dbData ?? []).map((r) => ({
    simbolo: r.simbolo, timeframe: r.timeframe,
    ts:      new Date(r.ts).toISOString(),
    open:    Number(r.open), high: Number(r.high),
    low:     Number(r.low),  close: Number(r.close),
    volume:  r.volume !== null ? Number(r.volume) : null,
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

// ─── processSymbolWrite ───────────────────────────────────────────────────────
//
// INSERT-ONLY policy:
//   - Ogni barra provider viene classificata come NEW o EXISTING.
//   - NEW  → entra nel batch INSERT con ignoreDuplicates: true
//             (PostgREST: ON CONFLICT (simbolo,timeframe,ts) DO NOTHING)
//   - EXISTING → MAI scritta. Classificata in quattro categorie mutuamente esclusive:
//       exact_match    : OHLC identico  AND volume identico
//       ohlc_conflict  : OHLC diverso   AND volume identico
//       volume_conflict: OHLC identico  AND volume diverso
//       both_conflict  : OHLC diverso   AND volume diverso
//   - Le barre invalide sono escluse completamente (skipped_invalid).
//
// inserted è calcolato come symbol_date_rows_after − symbol_date_rows_before
// (confermato dal DB, non come toInsert.length).
// attempted_insert_count = toInsert.length (barre inviate all'INSERT).
//
// Nessun percorso DO UPDATE esiste in questa funzione.
// ─────────────────────────────────────────────────────────────────────────────

async function processSymbolWrite(ctx: {
  symbol:          string;
  timeframe:       string;
  start:           string;
  end:             string;
  provider:        MarketDataProvider;
  supabase:        ReturnType<typeof createClient>;
  globalRowsBefore: number;   // passato dall'handler (query fresca prima del loop)
}): Promise<WriteResult> {
  const { symbol, timeframe, start, end, provider, supabase, globalRowsBefore } = ctx;
  const warnings: string[] = [];

  // ── 1. Fetch + validazione ──────────────────────────────────────────────────
  const fetchResult = await provider.fetchBars({
    symbol, timeframe, start, end, regularHoursOnly: true, adjustment: "adjusted",
  });

  if (fetchResult.errors.length > 0) {
    fetchResult.errors.forEach((e) => warnings.push(`[PROVIDER ERROR] ${e}`));
  }
  warnings.push(...fetchResult.warnings);

  const validBars:   NormalizedBar[]   = [];
  const invalidBars: ValidationError[] = [];

  for (const bar of fetchResult.bars) {
    const errs = validateBar(bar);
    if (errs.length === 0) validBars.push(bar);
    else invalidBars.push(...errs);
  }

  if (invalidBars.length > 0) {
    warnings.push(`${invalidBars.length} barre invalide escluse dall'insert`);
  }

  // ── 2. Query fresca: symbol_date_rows_before ────────────────────────────────
  // Eseguita immediatamente prima dell'INSERT per avere un baseline fresco.
  const { data: beforeData, error: beforeErr } = await supabase
    .from("candles")
    .select("simbolo, timeframe, ts, open, high, low, close, volume")
    .eq("simbolo",   symbol)
    .eq("timeframe", timeframe)
    .gte("ts",       start)
    .lt("ts",        end)
    .order("ts", { ascending: true });

  if (beforeErr) {
    warnings.push(`Errore lettura DB pre-insert: ${beforeErr.message}`);
    return {
      ...emptyWriteResult(symbol),
      skipped_invalid: invalidBars.length,
      global_rows_before: globalRowsBefore,
      warnings,
    };
  }

  const symbolDateRowsBefore = (beforeData ?? []).length;

  // Mappa ts → valori OHLCV esistenti — lookup O(1)
  type ExistingRow = { open: number; high: number; low: number; close: number; volume: number | null };
  const existingMap = new Map<string, ExistingRow>();

  for (const row of (beforeData ?? [])) {
    existingMap.set(new Date(row.ts).toISOString(), {
      open:   Number(row.open),
      high:   Number(row.high),
      low:    Number(row.low),
      close:  Number(row.close),
      volume: row.volume !== null ? Number(row.volume) : null,
    });
  }

  // ── 3. Classifica ogni barra: NEW vs EXISTING ───────────────────────────────
  type InsertRow = {
    simbolo: string; timeframe: string; ts: string;
    open: number; high: number; low: number; close: number;
    volume: number | null;
  };

  const toInsert: InsertRow[] = [];   // barre nuove → INSERT

  // Accumulatori conflitti — categorie mutuamente esclusive
  let exactMatchCount    = 0;
  let ohlcConflictCount  = 0;
  let volConflictCount   = 0;
  let bothConflictCount  = 0;
  const conflictExamples: ConflictExample[] = [];

  for (const bar of validBars) {
    const tsKey  = bar.ts;                              // già ISO UTC da toUtcIso()
    const volInt = bar.volume !== null ? Math.round(bar.volume) : null;

    const existing = existingMap.get(tsKey);

    if (!existing) {
      // ── NEW: non presente in DB → entra nel batch INSERT ──
      toInsert.push({
        simbolo:   bar.simbolo,
        timeframe: bar.timeframe,
        ts:        tsKey,
        open:      round4(bar.open),
        high:      round4(bar.high),
        low:       round4(bar.low),
        close:     round4(bar.close),
        volume:    volInt,
      });
    } else {
      // ── EXISTING: già in DB → non scritta, classificata ──
      const dbOpen  = round4(existing.open);
      const dbHigh  = round4(existing.high);
      const dbLow   = round4(existing.low);
      const dbClose = round4(existing.close);
      const dbVol   = existing.volume !== null ? Math.round(existing.volume) : null;

      const pOpen  = round4(bar.open);
      const pHigh  = round4(bar.high);
      const pLow   = round4(bar.low);
      const pClose = round4(bar.close);

      // ohlcDiffers and volumeDiffers are mutually independent booleans;
      // combining them produces four mutually exclusive categories.
      const ohlcDiffers   = dbOpen !== pOpen || dbHigh !== pHigh || dbLow !== pLow || dbClose !== pClose;
      const volumeDiffers = dbVol  !== volInt;

      if (!ohlcDiffers && !volumeDiffers) {
        exactMatchCount++;
      } else {
        // Build conflict example (capped at MAX_CONFLICT_EXAMPLES)
        if (conflictExamples.length < MAX_CONFLICT_EXAMPLES) {
          const differingFields: string[] = [];
          if (dbOpen  !== pOpen)  differingFields.push("open");
          if (dbHigh  !== pHigh)  differingFields.push("high");
          if (dbLow   !== pLow)   differingFields.push("low");
          if (dbClose !== pClose) differingFields.push("close");
          if (volumeDiffers)      differingFields.push("volume");

          const volRatio =
            volumeDiffers && dbVol !== null && dbVol !== 0 && volInt !== null
              ? round4(volInt / dbVol)
              : null;

          conflictExamples.push({
            ts:               tsKey,
            differing_fields: differingFields,
            db_open:          dbOpen,  db_high:  dbHigh,
            db_low:           dbLow,   db_close: dbClose,
            db_volume:        dbVol,
            provider_open:    pOpen,   provider_high:  pHigh,
            provider_low:     pLow,    provider_close: pClose,
            provider_volume:  volInt,
            volume_ratio:     volRatio,
          });
        }

        // Mutually exclusive classification
        if      ( ohlcDiffers && !volumeDiffers) ohlcConflictCount++;
        else if (!ohlcDiffers &&  volumeDiffers) volConflictCount++;
        else                                     bothConflictCount++;
      }
    }
  }

  const attemptedInsertCount = toInsert.length;

  // ── 4. INSERT con ON CONFLICT DO NOTHING ────────────────────────────────────
  //
  // ignoreDuplicates: true  →  PostgREST emette:
  //   INSERT INTO candles (...) VALUES (...) ON CONFLICT (simbolo,timeframe,ts) DO NOTHING
  //
  // Non esiste nessun percorso DO UPDATE in questo codice.
  // ─────────────────────────────────────────────────────────────────────────────
  let insertError: string | null = null;

  if (toInsert.length > 0) {
    for (let i = 0; i < toInsert.length; i += INSERT_BATCH_SIZE) {
      const batch = toInsert.slice(i, i + INSERT_BATCH_SIZE);

      const { error: batchErr } = await supabase
        .from("candles")
        .upsert(batch, {
          onConflict:       "simbolo,timeframe,ts",
          ignoreDuplicates: true,    // DO NOTHING — nessun aggiornamento
        });

      if (batchErr) {
        insertError = batchErr.message;
        warnings.push(`Errore insert batch ${Math.floor(i / INSERT_BATCH_SIZE) + 1}: ${batchErr.message}`);
        break;
      }
    }
  }

  // ── 5. Query fresca: symbol_date_rows_after ─────────────────────────────────
  // Eseguita immediatamente dopo l'INSERT.
  // inserted = symbol_date_rows_after − symbol_date_rows_before  (confermato da DB)
  const { data: afterData, error: afterErr } = await supabase
    .from("candles")
    .select("simbolo", { count: "exact" })
    .eq("simbolo",   symbol)
    .eq("timeframe", timeframe)
    .gte("ts",       start)
    .lt("ts",        end);

  if (afterErr) {
    warnings.push(`Errore conteggio post-insert: ${afterErr.message}`);
  }

  const symbolDateRowsAfter = (afterData ?? []).length;

  // Confirmed insert count: DB-side delta (accounts for any DO NOTHING silences)
  const insertedConfirmed = insertError ? 0 : (symbolDateRowsAfter - symbolDateRowsBefore);

  // ── 6. Query globale fresca dopo l'insert ───────────────────────────────────
  const { count: globalCountAfter, error: globalAfterErr } = await supabase
    .from("candles")
    .select("*", { count: "exact", head: true });

  if (globalAfterErr) {
    warnings.push(`Errore conteggio globale post-insert: ${globalAfterErr.message}`);
  }

  const globalRowsAfter = globalCountAfter ?? 0;

  // ── 7. Assembla conflitti e verifica invarianti ─────────────────────────────
  const existingTotal = exactMatchCount + ohlcConflictCount + volConflictCount + bothConflictCount;

  const conflicts: ConflictSummary = {
    existing_count:        existingTotal,
    exact_match_count:     exactMatchCount,
    ohlc_conflict_count:   ohlcConflictCount,
    volume_conflict_count: volConflictCount,
    both_conflict_count:   bothConflictCount,
    examples:              conflictExamples,
  };

  // Invariant 1: existing = sum of four conflict categories (always true by construction,
  //              but checked explicitly so any future bug surfaces immediately)
  const inv1ok  = existingTotal === (exactMatchCount + ohlcConflictCount + volConflictCount + bothConflictCount);
  // Invariant 2: provider_bars = attempted_insert_count + existing + skipped_invalid
  const inv2ok  = validBars.length === (attemptedInsertCount + existingTotal + invalidBars.length);

  const invariantErrors: string[] = [];
  if (!inv1ok) {
    invariantErrors.push(
      `INV1 FAIL: existing(${existingTotal}) ≠ exactMatch(${exactMatchCount}) + ohlcConflict(${ohlcConflictCount}) + volConflict(${volConflictCount}) + bothConflict(${bothConflictCount})`,
    );
  }
  if (!inv2ok) {
    invariantErrors.push(
      `INV2 FAIL: provider_bars(${validBars.length}) ≠ attempted_insert_count(${attemptedInsertCount}) + existing(${existingTotal}) + skipped_invalid(${invalidBars.length})`,
    );
  }
  if (invariantErrors.length > 0) {
    warnings.push(...invariantErrors);
  }

  return {
    symbol,
    provider_bars:            validBars.length,
    attempted_insert_count:   attemptedInsertCount,
    inserted:                 insertedConfirmed,
    existing:                 existingTotal,
    skipped_invalid:          invalidBars.length,
    symbol_date_rows_before:  symbolDateRowsBefore,
    symbol_date_rows_after:   symbolDateRowsAfter,
    global_rows_before:       globalRowsBefore,
    global_rows_after:        globalRowsAfter,
    conflicts,
    invariant_ok:     invariantErrors.length === 0,
    invariant_errors: invariantErrors,
    warnings,
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
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// ─── Validazione input ────────────────────────────────────────────────────────

function validateInput(body: Record<string, unknown>): Response | null {
  if (!body["provider"] || typeof body["provider"] !== "string")
    return jsonError(400, "Campo 'provider' mancante o non stringa.");
  if (!Array.isArray(body["symbols"]) || body["symbols"].length === 0)
    return jsonError(400, "Campo 'symbols' mancante o array vuoto.");
  if (body["symbols"].some((s) => typeof s !== "string" || !s.trim()))
    return jsonError(400, "Tutti i simboli devono essere stringhe non vuote.");
  if (!body["timeframe"] || typeof body["timeframe"] !== "string")
    return jsonError(400, "Campo 'timeframe' mancante.");
  if (!body["mode"] || typeof body["mode"] !== "string")
    return jsonError(400, "Campo 'mode' mancante.");
  if (!SUPPORTED_MODES.includes(body["mode"] as SupportedMode))
    return jsonError(400, `Mode '${body["mode"]}' non supportato. Valori accettati: ${SUPPORTED_MODES.join(", ")}`);
  if (!body["date"] || typeof body["date"] !== "string")
    return jsonError(400, "Campo 'date' mancante.");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(body["date"] as string))
    return jsonError(400, "Campo 'date' deve essere in formato YYYY-MM-DD.");
  if ((body["symbols"] as string[]).length > 10)
    return jsonError(400, "Massimo 10 simboli per richiesta.");
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
    { status, headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" } },
  );
}

function jsonOk(data: FunctionOutput): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status: 200,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
  });
}

/** Arrotonda a 4 decimali — allinea al formato numeric in DB. */
function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}

function emptyCompareResult(symbol: string): CompareResult {
  const emptyDiff = {
    mean_abs: 0, max_abs: 0, mean_pct: 0,
    within_001pct: 0, within_005pct: 0, within_010pct: 0, above_010pct: 0,
  };
  return {
    symbol, provider_bars: 0, database_bars: 0, matched_bars: 0,
    missing_in_provider: [], missing_in_database: [], invalid_bars: [],
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
    provider_bars:            0,
    attempted_insert_count:   0,
    inserted:                 0,
    existing:                 0,
    skipped_invalid:          0,
    symbol_date_rows_before:  0,
    symbol_date_rows_after:   0,
    global_rows_before:       0,
    global_rows_after:        0,
    conflicts: {
      existing_count:        0,
      exact_match_count:     0,
      ohlc_conflict_count:   0,
      volume_conflict_count: 0,
      both_conflict_count:   0,
      examples:              [],
    },
    invariant_ok:     true,
    invariant_errors: [],
    warnings:         [],
  };
}
