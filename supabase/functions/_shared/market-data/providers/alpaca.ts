/**
 * providers/alpaca.ts
 * Provider Alpaca Markets (IEX e SIP).
 *
 * Stato: adapter completo, INATTIVO se ALPACA_API_KEY / ALPACA_API_SECRET
 * non sono configurati come Supabase Secrets.
 *
 * Non blocca Yahoo. Non lancia eccezioni non gestite.
 * Non hardcoda mai chiavi.
 *
 * Differenze note rispetto al dataset Yahoo esistente:
 * - IEX: ~2-5% del volume totale mercato, OHLCV può differire da SIP
 * - SIP: feed consolidato, richiede piano Alpaca Unlimited (~$99/mese)
 * - Entrambi richiedono adjustment=all per compatibility con dataset adjusted
 */

import type { MarketDataProvider, FetchRequest, FetchResult, NormalizedBar } from "../provider.ts";
import {
  filterRegularHours,
  toUtcIso,
  deduplicate,
  validateBar,
} from "../provider.ts";
import { resolveTimeframeMinutes } from "../provider.ts";

const ALPACA_BASE_URL = "https://data.alpaca.markets/v2";

// Mappa timeframe → stringa Alpaca
const ALPACA_TIMEFRAME_MAP: Record<string, string> = {
  "1m":  "1Min",
  "5m":  "5Min",
  "15m": "15Min",
  "30m": "30Min",
  "1h":  "1Hour",
  "1d":  "1Day",
};

export type AlpacaFeed = "iex" | "sip";

export class AlpacaProvider implements MarketDataProvider {
  readonly name: string;
  private readonly feed: AlpacaFeed;

  constructor(feed: AlpacaFeed = "iex") {
    this.feed = feed;
    this.name = `alpaca_${feed}`;
  }

  /**
   * Verifica la presenza delle credenziali Alpaca nell'ambiente.
   * Restituisce un errore controllato se mancano — non lancia eccezioni.
   */
  private checkCredentials(): { key: string; secret: string } | { error: string } {
    const key    = Deno.env.get("ALPACA_API_KEY")    ?? "";
    const secret = Deno.env.get("ALPACA_API_SECRET") ?? "";

    if (!key || !secret) {
      const missing: string[] = [];
      if (!key)    missing.push("ALPACA_API_KEY");
      if (!secret) missing.push("ALPACA_API_SECRET");
      return {
        error: `Alpaca non configurato: secrets mancanti [${missing.join(", ")}]. ` +
               "Aggiungili in Supabase Dashboard → Settings → Edge Functions → Secrets.",
      };
    }
    return { key, secret };
  }

  async fetchBars(request: FetchRequest): Promise<FetchResult> {
    const warnings: string[] = [];
    const errors:   string[] = [];

    // Verifica credenziali prima di qualsiasi chiamata HTTP
    const creds = this.checkCredentials();
    if ("error" in creds) {
      return {
        symbol:          request.symbol,
        provider:        this.name,
        bars:            [],
        warnings,
        errors:          [creds.error],
        raw_count:       0,
        filtered_count:  0,
      };
    }

    const alpacaTf = ALPACA_TIMEFRAME_MAP[request.timeframe];
    if (!alpacaTf) {
      return {
        symbol:         request.symbol,
        provider:       this.name,
        bars:           [],
        warnings,
        errors:         [`Timeframe non supportato da Alpaca: ${request.timeframe}`],
        raw_count:      0,
        filtered_count: 0,
      };
    }

    const url = new URL(`${ALPACA_BASE_URL}/stocks/${encodeURIComponent(request.symbol)}/bars`);
    url.searchParams.set("timeframe",  alpacaTf);
    url.searchParams.set("start",      request.start);
    url.searchParams.set("end",        request.end);
    url.searchParams.set("feed",       this.feed);
    url.searchParams.set("adjustment", "all"); // adjusted per split e dividendi
    url.searchParams.set("limit",      "1000");

    let resp: Response;
    try {
      resp = await fetch(url.toString(), {
        headers: {
          "APCA-API-KEY-ID":     creds.key,
          "APCA-API-SECRET-KEY": creds.secret,
          "Accept": "application/json",
        },
        signal: AbortSignal.timeout(15_000),
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors: [`Connessione Alpaca fallita: ${msg}`], raw_count: 0, filtered_count: 0 };
    }

    if (resp.status === 401) {
      // Non loggare mai le credenziali
      return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors: ["Alpaca: autenticazione fallita. Verifica ALPACA_API_KEY e ALPACA_API_SECRET."], raw_count: 0, filtered_count: 0 };
    }
    if (resp.status === 403) {
      const detail = this.feed === "sip"
        ? "Il feed SIP richiede Alpaca Unlimited (~$99/mese). Usa feed IEX per il piano gratuito."
        : "Accesso negato al feed Alpaca IEX.";
      return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors: [`Alpaca 403: ${detail}`], raw_count: 0, filtered_count: 0 };
    }
    if (resp.status === 422) {
      const body = await resp.text();
      return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors: [`Alpaca parametri non validi: ${body.slice(0, 200)}`], raw_count: 0, filtered_count: 0 };
    }
    if (!resp.ok) {
      const body = await resp.text();
      return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors: [`Alpaca HTTP ${resp.status}: ${body.slice(0, 200)}`], raw_count: 0, filtered_count: 0 };
    }

    const payload = await resp.json() as { bars?: Record<string, unknown>[]; next_page_token?: string };
    const rawBars = payload.bars ?? [];

    if (rawBars.length === 0) {
      warnings.push(`Alpaca ${this.feed}: nessuna barra per ${request.symbol} nell'intervallo`);
      return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors, raw_count: 0, filtered_count: 0 };
    }

    // Gestione paginazione (next_page_token) — limitata a 1 pagina in dry_run
    if (payload.next_page_token) {
      warnings.push("Alpaca: risposta paginata, recuperata solo la prima pagina (max 1000 barre)");
    }

    const normalized: NormalizedBar[] = [];
    for (const bar of rawBars) {
      const n = this.normalizeBar(bar, request.symbol, request.timeframe);
      if (n) normalized.push(n);
    }

    const rawCount  = normalized.length;
    const tfMinutes = resolveTimeframeMinutes(request.timeframe);
    const filtered  = request.regularHoursOnly
      ? filterRegularHours(normalized, tfMinutes)
      : normalized;

    const { unique, duplicates } = deduplicate(filtered);
    if (duplicates.length > 0) {
      warnings.push(`Alpaca ${this.feed}: ${duplicates.length} duplicati rimossi`);
    }

    const allInvalid = unique.flatMap(validateBar);
    if (allInvalid.length > 0) {
      warnings.push(`Alpaca ${this.feed}: ${allInvalid.length} errori OHLCV trovati`);
    }

    const validBars = unique.filter((b) => validateBar(b).length === 0);

    return {
      symbol:         request.symbol,
      provider:       this.name,
      bars:           validBars,
      warnings,
      errors,
      raw_count:      rawCount,
      filtered_count: validBars.length,
    };
  }

  normalizeBar(
    raw: Record<string, unknown>,
    symbol: string,
    timeframe: string,
  ): NormalizedBar | null {
    try {
      // Alpaca usa: t (timestamp), o, h, l, c, v
      const tsRaw = raw["t"];
      if (!tsRaw) return null;

      const o = raw["o"];
      const h = raw["h"];
      const l = raw["l"];
      const c = raw["c"];
      const v = raw["v"];

      if (o === null || o === undefined) return null;
      if (h === null || h === undefined) return null;
      if (l === null || l === undefined) return null;
      if (c === null || c === undefined) return null;

      return {
        simbolo:    symbol.toUpperCase(),
        timeframe:  timeframe.toLowerCase(),
        ts:         toUtcIso(tsRaw as string),
        open:       Number(o),
        high:       Number(h),
        low:        Number(l),
        close:      Number(c),
        volume:     v !== null && v !== undefined ? Number(v) : null,
        provider:   this.name,
        adjustment: "adjusted",
        session:    "regular",
      };
    } catch {
      return null;
    }
  }
}

/** Singleton IEX (gratuito) */
export const alpacaIexProvider = new AlpacaProvider("iex");

/** Singleton SIP (richiede piano a pagamento) */
export const alpacaSipProvider = new AlpacaProvider("sip");
