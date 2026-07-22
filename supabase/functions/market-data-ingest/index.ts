/**
 * market-data-ingest/index.ts
 * Edge Function Supabase — ingestione dati di mercato.
 *
 * FASE ATTUALE: solo mode=dry_run (nessuna scrittura su DB).
 *
 * Richiesta POST:
 * {
 *   "provider":  "yahoo",
 *   "symbols":   ["SPY"],
 *   "timeframe": "5m",
 *   "mode":      "dry_run",
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

import type { FunctionOutput, CompareResult, DatabaseBar } from "../_shared/market-data/types.ts";
import { buildDateRange } from "../_shared/market-data/provider.ts";
import { compareBars, validateBar } from "../_shared/market-data/provider.ts";
import { yahooProvider }     from "../_shared/market-data/providers/yahoo.ts";
import { alpacaIexProvider, alpacaSipProvider } from "../_shared/market-data/providers/alpaca.ts";
import type { MarketDataProvider } from "../_shared/market-data/provider.ts";

// ─── Costanti ────────────────────────────────────────────────────────────────

const SUPPORTED_PROVIDERS = ["yahoo", "alpaca_iex", "alpaca_sip"] as const;
type SupportedProvider = typeof SUPPORTED_PROVIDERS[number];

const SUPPORTED_MODES = ["dry_run"] as const;

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
  const mode         = body["mode"] as string;
  const date         = body["date"] as string;

  // ── Solo dry_run in questa fase ──
  if (mode !== "dry_run") {
    return jsonError(400, `Mode '${mode}' non supportato. Questa funzione accetta solo mode='dry_run' in questa fase.`);
  }

  // ── Seleziona provider ──
  const provider = resolveProvider(providerName);
  if (!provider) {
    return jsonError(400, `Provider '${providerName}' non riconosciuto. Valori accettati: ${SUPPORTED_PROVIDERS.join(", ")}`);
  }

  // ── Client Supabase service role (read-only per ora) ──
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

  const results: CompareResult[] = [];
  const globalErrors: string[] = [];

  // ── Elaborazione per simbolo ──
  for (const symbol of symbols) {
    try {
      const result = await processSymbol({
        symbol,
        timeframe,
        start,
        end,
        provider,
        supabase,
        date,
      });
      results.push(result);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      globalErrors.push(`Errore su ${symbol}: ${msg}`);
      results.push(emptyResult(symbol));
    }
  }

  // ── Status aggregato ──
  const hasErrors   = globalErrors.length > 0 || results.some((r) => r.invalid_bars.length > 0);
  const hasWarnings = results.some((r) => r.warnings.length > 0);
  const status = hasErrors ? (results.some((r) => r.matched_bars > 0) ? "partial" : "error")
               : hasWarnings ? "partial"
               : "success";

  const output: FunctionOutput = {
    status,
    mode:      "dry_run",
    provider:  providerName,
    symbols,
    timeframe,
    date,
    results,
    errors:    globalErrors,
  };

  return new Response(JSON.stringify(output, null, 2), {
    status:  200,
    headers: {
      "Content-Type":                "application/json",
      "Access-Control-Allow-Origin": "*",
    },
  });
});

// ─── Elaborazione singolo simbolo ────────────────────────────────────────────

async function processSymbol(ctx: {
  symbol:    string;
  timeframe: string;
  start:     string;
  end:       string;
  provider:  MarketDataProvider;
  supabase:  ReturnType<typeof createClient>;
  date:      string;
}): Promise<CompareResult> {
  const { symbol, timeframe, start, end, provider, supabase } = ctx;

  // Fetch dal provider
  const fetchResult = await provider.fetchBars({
    symbol,
    timeframe,
    start,
    end,
    regularHoursOnly: true,
    adjustment: "adjusted",
  });

  // Validazione barre provider
  const invalidBars = fetchResult.bars.flatMap(validateBar);

  // Leggi da Supabase — SELECT read-only
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
      ...emptyResult(symbol),
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

  // Propaga warnings dal fetch
  const compareResult = compareBars(symbol, fetchResult.bars, dbBars, invalidBars);

  // Aggiungi warnings del provider
  compareResult.warnings = [
    ...fetchResult.warnings,
    ...fetchResult.errors.map((e) => `[PROVIDER ERROR] ${e}`),
    ...compareResult.warnings,
  ];

  // Metadati extra utili
  if (fetchResult.raw_count !== fetchResult.filtered_count) {
    compareResult.warnings.push(
      `Provider: ${fetchResult.raw_count} barre raw → ${fetchResult.filtered_count} dopo filtro regular hours`,
    );
  }

  return compareResult;
}

// ─── Sicurezza ───────────────────────────────────────────────────────────────

function verifySecret(req: Request): Response | null {
  const envSecret = Deno.env.get("MARKET_DATA_FUNCTION_SECRET") ?? "";

  if (!envSecret) {
    // Secret non configurato: blocca tutto
    return jsonError(503, "Funzione non disponibile: MARKET_DATA_FUNCTION_SECRET non configurato.");
  }

  const headerSecret = req.headers.get("X-Function-Secret") ?? "";

  if (!headerSecret) {
    return jsonError(401, "Header X-Function-Secret mancante.");
  }

  // Confronto a tempo costante per prevenire timing attacks
  if (!timingSafeEqual(envSecret, headerSecret)) {
    return jsonError(403, "Secret non valido.");
  }

  return null;
}

/** Confronto stringhe a tempo costante (previene timing attacks). */
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
  if (!body["date"] || typeof body["date"] !== "string") {
    return jsonError(400, "Campo 'date' mancante.");
  }

  // Valida formato data YYYY-MM-DD
  if (!/^\d{4}-\d{2}-\d{2}$/.test(body["date"] as string)) {
    return jsonError(400, "Campo 'date' deve essere in formato YYYY-MM-DD.");
  }

  // Simboli: max 10 per request
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
      headers: {
        "Content-Type":                "application/json",
        "Access-Control-Allow-Origin": "*",
      },
    },
  );
}

function emptyResult(symbol: string): CompareResult {
  return {
    symbol,
    provider_bars:       0,
    database_bars:       0,
    matched_bars:        0,
    missing_in_provider: [],
    missing_in_database: [],
    invalid_bars:        [],
    ohlc_differences: {
      open:  { mean_abs: 0, max_abs: 0, mean_pct: 0, within_001pct: 0, within_005pct: 0, within_010pct: 0, above_010pct: 0 },
      high:  { mean_abs: 0, max_abs: 0, mean_pct: 0, within_001pct: 0, within_005pct: 0, within_010pct: 0, above_010pct: 0 },
      low:   { mean_abs: 0, max_abs: 0, mean_pct: 0, within_001pct: 0, within_005pct: 0, within_010pct: 0, above_010pct: 0 },
      close: { mean_abs: 0, max_abs: 0, mean_pct: 0, within_001pct: 0, within_005pct: 0, within_010pct: 0, above_010pct: 0 },
    },
    volume_differences: {
      ratio_median: 0, ratio_mean: 0, ratio_std: 0,
      ratio_min: 0, ratio_max: 0, correlation: null,
      zero_in_provider: 0, zero_in_database: 0,
    },
    warnings: [],
  };
}
