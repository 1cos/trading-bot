/**
 * providers/yahoo.ts
 * Provider Yahoo Finance via endpoint HTTP non ufficiale.
 *
 * Caratteristiche dataset esistente (da audit):
 * - Prezzi adjusted (auto_adjust=True equivalente)
 * - Regular hours solo: 13:30–19:55 UTC (09:30–15:55 ET)
 * - Float IEEE 754, arrotondati a 4dp in Supabase
 * - Volume può essere 0 (comportamento noto Yahoo)
 * - Nessun pre-market / after-hours
 */

import type { MarketDataProvider, FetchRequest, FetchResult, NormalizedBar } from "../provider.ts";
import {
  filterRegularHours,
  toUtcIso,
  deduplicate,
  validateBar,
} from "../provider.ts";
import { resolveTimeframeMinutes } from "../provider.ts";

// Mappa timeframe → stringa accettata da Yahoo Finance
const YAHOO_INTERVAL_MAP: Record<string, string> = {
  "1m":  "1m",
  "5m":  "5m",
  "15m": "15m",
  "30m": "30m",
  "1h":  "60m",
  "1d":  "1d",
};

// Range massimi supportati da Yahoo per ogni intervallo
const YAHOO_MAX_RANGE: Record<string, string> = {
  "1m":  "7d",
  "5m":  "60d",
  "15m": "60d",
  "30m": "60d",
  "1h":  "730d",
  "1d":  "max",
};

export class YahooProvider implements MarketDataProvider {
  readonly name = "yahoo";

  async fetchBars(request: FetchRequest): Promise<FetchResult> {
    const warnings: string[] = [];
    const errors: string[] = [];

    const interval = YAHOO_INTERVAL_MAP[request.timeframe];
    if (!interval) {
      return {
        symbol: request.symbol,
        provider: this.name,
        bars: [],
        warnings,
        errors: [`Timeframe non supportato da Yahoo: ${request.timeframe}`],
        raw_count: 0,
        filtered_count: 0,
      };
    }

    // Converte start/end in Unix epoch (Yahoo usa secondi)
    const startEpoch = Math.floor(new Date(request.start).getTime() / 1000);
    const endEpoch   = Math.floor(new Date(request.end).getTime() / 1000);

    const url = new URL(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(request.symbol)}`,
    );
    url.searchParams.set("interval",    interval);
    url.searchParams.set("period1",     String(startEpoch));
    url.searchParams.set("period2",     String(endEpoch));
    url.searchParams.set("includePrePost", request.regularHoursOnly ? "false" : "true");
    url.searchParams.set("events",      "div,splits");

    let raw: Record<string, unknown>;
    try {
      const resp = await fetch(url.toString(), {
        headers: {
          "User-Agent": "Mozilla/5.0 (compatible; trading-bot/1.0)",
          "Accept":     "application/json",
        },
        signal: AbortSignal.timeout(15_000),
      });

      if (!resp.ok) {
        const body = await resp.text();
        if (resp.status === 404) {
          errors.push(`Simbolo non trovato su Yahoo: ${request.symbol}`);
        } else if (resp.status === 429) {
          errors.push("Yahoo Finance rate limit raggiunto — riprova tra qualche minuto");
        } else {
          errors.push(`Yahoo HTTP ${resp.status}: ${body.slice(0, 200)}`);
        }
        return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors, raw_count: 0, filtered_count: 0 };
      }

      raw = await resp.json() as Record<string, unknown>;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      errors.push(`Errore fetch Yahoo: ${msg}`);
      return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors, raw_count: 0, filtered_count: 0 };
    }

    // Parse risposta Yahoo
    const result = (raw as { chart?: { result?: unknown[]; error?: unknown } })?.chart?.result;
    const chartError = (raw as { chart?: { error?: unknown } })?.chart?.error;

    if (chartError) {
      errors.push(`Yahoo error: ${JSON.stringify(chartError)}`);
      return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors, raw_count: 0, filtered_count: 0 };
    }

    if (!Array.isArray(result) || result.length === 0) {
      warnings.push(`Yahoo: nessun risultato per ${request.symbol} nell'intervallo richiesto`);
      return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors, raw_count: 0, filtered_count: 0 };
    }

    const chartResult = result[0] as Record<string, unknown>;
    const timestamps  = chartResult["timestamp"] as number[] | undefined;
    const indicators  = chartResult["indicators"] as Record<string, unknown> | undefined;
    const quote       = (indicators?.["quote"] as Record<string, unknown>[])?.[0];
    const adjClose    = (indicators?.["adjclose"] as Record<string, unknown>[])?.[0];

    if (!timestamps || !quote) {
      warnings.push("Yahoo: struttura risposta inattesa — mancano timestamps o quote");
      return { symbol: request.symbol, provider: this.name, bars: [], warnings, errors, raw_count: 0, filtered_count: 0 };
    }

    const opens   = quote["open"]   as (number | null)[];
    const highs   = quote["high"]   as (number | null)[];
    const lows    = quote["low"]    as (number | null)[];
    const closes  = quote["close"]  as (number | null)[];
    const volumes = quote["volume"] as (number | null)[];
    // Yahoo restituisce adjclose separatamente; usalo se disponibile (auto_adjust=True equivalent)
    const adjCloses = adjClose?.["adjclose"] as (number | null)[] | undefined;

    const rawBars: NormalizedBar[] = [];

    for (let i = 0; i < timestamps.length; i++) {
      const normalized = this.normalizeBar(
        {
          ts_raw:    timestamps[i],
          open:      opens?.[i],
          high:      highs?.[i],
          low:       lows?.[i],
          close:     adjCloses?.[i] ?? closes?.[i],  // preferisce adjusted
          volume:    volumes?.[i],
        },
        request.symbol,
        request.timeframe,
      );
      if (normalized) rawBars.push(normalized);
    }

    const rawCount = rawBars.length;

    // Filtra regular hours
    const tfMinutes = resolveTimeframeMinutes(request.timeframe);
    const filtered  = request.regularHoursOnly
      ? filterRegularHours(rawBars, tfMinutes)
      : rawBars;

    // Deduplica
    const { unique, duplicates } = deduplicate(filtered);
    if (duplicates.length > 0) {
      warnings.push(`Yahoo: ${duplicates.length} timestamp duplicati rimossi`);
    }

    // Valida
    const allInvalid = unique.flatMap(validateBar);
    if (allInvalid.length > 0) {
      warnings.push(`Yahoo: ${allInvalid.length} errori OHLCV trovati (vedi invalid_bars)`);
    }

    // Filtra le barre invalide dall'output finale
    const validBars = unique.filter((b) => validateBar(b).length === 0);

    return {
      symbol:          request.symbol,
      provider:        this.name,
      bars:            validBars,
      warnings,
      errors,
      raw_count:       rawCount,
      filtered_count:  validBars.length,
    };
  }

  normalizeBar(
    raw: Record<string, unknown>,
    symbol: string,
    timeframe: string,
  ): NormalizedBar | null {
    try {
      const tsRaw  = raw["ts_raw"];
      const open   = raw["open"];
      const high   = raw["high"];
      const low    = raw["low"];
      const close  = raw["close"];
      const volume = raw["volume"];

      // Salta barre con OHLC null (Yahoo li emette per barre senza trading)
      if (open === null || open === undefined) return null;
      if (high === null || high === undefined) return null;
      if (low  === null || low  === undefined) return null;
      if (close === null || close === undefined) return null;

      const ts = toUtcIso(tsRaw as string | number);

      return {
        simbolo:    symbol.toUpperCase(),
        timeframe:  timeframe.toLowerCase(),
        ts,
        open:       Number(open),
        high:       Number(high),
        low:        Number(low),
        close:      Number(close),
        volume:     volume !== null && volume !== undefined ? Number(volume) : null,
        provider:   "yahoo",
        adjustment: "adjusted",
        session:    "regular", // Yahoo non distingue nella risposta; filtriamo già per orario
      };
    } catch {
      return null;
    }
  }
}

/** Singleton esportato */
export const yahooProvider = new YahooProvider();
