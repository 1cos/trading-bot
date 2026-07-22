#!/usr/bin/env python3
"""
compare_market_feeds.py
-----------------------
Confronta le barre OHLCV presenti in Supabase (public.candles) con le barre
recuperate da Alpaca Markets (feed IEX e/o SIP) per lo stesso simbolo e data.

Scopo: determinare quantitativamente quale feed Alpaca è compatibile con il
dataset storico esistente (provenienza presunta: Yahoo Finance via yfinance).

READ-ONLY: non modifica mai il database Supabase.

Credenziali richieste (variabili d'ambiente o file .env):
  SUPABASE_URL        https://<project>.supabase.co
  SUPABASE_ANON_KEY   eyJ...
  ALPACA_API_KEY      PK...
  ALPACA_API_SECRET   ...

Uso:
  python tools/compare_market_feeds.py \\
    --symbol SPY \\
    --date 2026-07-21 \\
    --timeframe 5Min \\
    --feeds iex,sip \\
    --output risultati.csv
"""

import argparse
import os
import sys
import json
import csv
import math
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Dipendenze opzionali — segnalate chiaramente se mancanti
# ---------------------------------------------------------------------------
try:
    import requests
except ImportError:
    print("ERRORE: libreria 'requests' non installata. Esegui: pip install requests")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("ERRORE: libreria 'pandas' non installata. Esegui: pip install pandas")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv opzionale: se assente usa solo variabili d'ambiente di sistema


# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------
ALPACA_BASE_URL = "https://data.alpaca.markets/v2"
MARKET_OPEN_UTC  = "13:30:00"   # 09:30 ET
MARKET_CLOSE_UTC = "20:00:00"   # 16:00 ET (barra 15:55 + 5m = fine finestra)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _redact(value: str) -> str:
    """Restituisce i primi 8 caratteri seguiti da '...' per non esporre secrets nei log."""
    if not value:
        return "<vuoto>"
    return value[:8] + "..." if len(value) > 8 else "***"


def load_env() -> dict:
    """
    Carica e valida le variabili d'ambiente richieste.
    Restituisce un dict con le chiavi; termina con messaggio chiaro se mancano.
    """
    required = {
        "SUPABASE_URL":      "URL del progetto Supabase (es. https://xyz.supabase.co)",
        "SUPABASE_ANON_KEY": "Chiave anon di Supabase (JWT eyJ...)",
        "ALPACA_API_KEY":    "API key Alpaca (PK...)",
        "ALPACA_API_SECRET": "API secret Alpaca",
    }
    env = {}
    missing = []
    for key, desc in required.items():
        val = os.environ.get(key, "").strip()
        if not val:
            missing.append(f"  {key:25s}  # {desc}")
        else:
            env[key] = val

    if missing:
        print("\n[ERRORE] Variabili d'ambiente mancanti:")
        for m in missing:
            print(m)
        print("\nCrea un file .env nella root del repo (vedi .env.example) oppure esporta le variabili:")
        print("  export SUPABASE_URL=https://<project>.supabase.co")
        print("  export SUPABASE_ANON_KEY=eyJ...")
        print("  export ALPACA_API_KEY=PK...")
        print("  export ALPACA_API_SECRET=...")
        sys.exit(1)

    # Stampa parziale per conferma senza esporre i valori
    print("[ENV] Credenziali caricate:")
    for key, val in env.items():
        print(f"  {key:25s} = {_redact(val)}")
    print()
    return env


# ---------------------------------------------------------------------------
# Supabase — lettura candele
# ---------------------------------------------------------------------------

def fetch_supabase_candles(env: dict, symbol: str, date: str, timeframe: str) -> pd.DataFrame:
    """
    Legge le barre dalla tabella public.candles per il simbolo e la data richiesti.
    Usa l'API REST PostgREST di Supabase.
    Operazione: SELECT solo — nessuna scrittura.
    """
    url = env["SUPABASE_URL"].rstrip("/") + "/rest/v1/candles"
    date_start = f"{date}T{MARKET_OPEN_UTC}+00:00"
    date_end   = f"{date}T{MARKET_CLOSE_UTC}+00:00"

    params = {
        "simbolo":   f"eq.{symbol}",
        "timeframe": f"eq.{timeframe.replace('Min', 'm')}",   # Supabase usa '5m'
        "ts":        f"gte.{date_start}",
        "order":     "ts.asc",
        "select":    "ts,open,high,low,close,volume",
        "limit":     "500",
    }
    # Aggiunge il filtro sulla data fine
    # PostgREST non supporta due filtri sullo stesso campo via params dict standard
    # usiamo l'header Range oppure due parametri con chiave identica tramite lista
    headers = {
        "apikey":        env["SUPABASE_ANON_KEY"],
        "Authorization": "Bearer " + env["SUPABASE_ANON_KEY"],
        "Accept":        "application/json",
    }

    # Filtro combinato: ts >= start AND ts < end
    # PostgREST supporta multipli filtri sullo stesso campo via query string ripetuta
    full_url = (
        url
        + f"?simbolo=eq.{symbol}"
        + f"&timeframe=eq.{timeframe.replace('Min', 'm')}"
        + f"&ts=gte.{date_start}"
        + f"&ts=lt.{date_end}"
        + f"&order=ts.asc"
        + f"&select=ts,open,high,low,close,volume"
        + f"&limit=500"
    )

    try:
        resp = requests.get(full_url, headers=headers, timeout=15)
    except requests.exceptions.ConnectionError as e:
        print(f"[ERRORE] Impossibile raggiungere Supabase: {e}")
        sys.exit(1)

    if resp.status_code == 401:
        print("[ERRORE] Supabase: autenticazione fallita. Verifica SUPABASE_ANON_KEY.")
        sys.exit(1)
    if resp.status_code == 403:
        print("[ERRORE] Supabase: accesso negato. Verifica RLS sulla tabella candles.")
        sys.exit(1)
    if resp.status_code != 200:
        print(f"[ERRORE] Supabase HTTP {resp.status_code}: {resp.text[:200]}")
        sys.exit(1)

    data = resp.json()
    if not isinstance(data, list):
        print(f"[ERRORE] Risposta Supabase inattesa: {str(data)[:200]}")
        sys.exit(1)

    if not data:
        print(f"[AVVISO] Nessuna candela trovata in Supabase per {symbol} {date} ({timeframe})")
        print("  Verifica che la data sia un giorno di trading e che il simbolo esista.")
        return pd.DataFrame()

    df = pd.DataFrame(data)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)

    # Validazione OHLC base
    invalid = df[
        (df["high"] < df["low"]) |
        (df["high"] < df["open"]) |
        (df["high"] < df["close"]) |
        (df["low"]  > df["open"]) |
        (df["low"]  > df["close"])
    ]
    if not invalid.empty:
        print(f"[AVVISO] {len(invalid)} barre Supabase con OHLC non valido:")
        print(invalid[["ts", "open", "high", "low", "close"]].to_string(index=False))

    # Controllo duplicati
    dupes = df[df.duplicated("ts")]
    if not dupes.empty:
        print(f"[AVVISO] {len(dupes)} timestamp duplicati in Supabase — verranno rimossi")
        df = df.drop_duplicates("ts")

    df = df.set_index("ts").sort_index()
    print(f"[SUPABASE] {len(df)} barre caricate per {symbol} {date}")
    return df


# ---------------------------------------------------------------------------
# Alpaca — recupero barre
# ---------------------------------------------------------------------------

def fetch_alpaca_bars(env: dict, symbol: str, date: str, timeframe: str, feed: str) -> tuple[Optional[pd.DataFrame], int, str]:
    """
    Recupera barre da Alpaca per il feed specificato (iex o sip).
    Restituisce (DataFrame | None, http_status, messaggio_errore).
    Non termina il programma: gli errori vengono restituiti al chiamante.
    """
    url = f"{ALPACA_BASE_URL}/stocks/{symbol}/bars"
    params = {
        "timeframe":  timeframe,        # es. "5Min"
        "start":      f"{date}T{MARKET_OPEN_UTC}Z",
        "end":        f"{date}T{MARKET_CLOSE_UTC}Z",
        "feed":       feed,
        "adjustment": "all",            # adjusted per split e dividendi
        "limit":      "1000",
    }
    headers = {
        "APCA-API-KEY-ID":     env["ALPACA_API_KEY"],
        "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET"],
        "Accept":              "application/json",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
    except requests.exceptions.ConnectionError as e:
        return None, 0, f"Connessione fallita: {e}"

    if resp.status_code == 401:
        return None, 401, "Alpaca: autenticazione fallita. Verifica ALPACA_API_KEY e ALPACA_API_SECRET."
    if resp.status_code == 403:
        return None, 403, (
            f"Alpaca: accesso negato al feed '{feed}'. "
            "Il feed SIP richiede un piano a pagamento. "
            "Prova con --feeds iex oppure sottoscrivi Alpaca Unlimited."
        )
    if resp.status_code == 422:
        return None, 422, f"Alpaca: parametri non validi — {resp.text[:200]}"
    if resp.status_code != 200:
        return None, resp.status_code, f"Alpaca HTTP {resp.status_code}: {resp.text[:200]}"

    payload = resp.json()
    bars = payload.get("bars", [])

    if not bars:
        return None, 200, f"Alpaca feed={feed}: nessuna barra restituita per {symbol} {date}"

    records = []
    for b in bars:
        records.append({
            "ts":     b["t"],
            "open":   b["o"],
            "high":   b["h"],
            "low":    b["l"],
            "close":  b["c"],
            "volume": b["v"],
        })

    df = pd.DataFrame(records)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    # Controllo duplicati
    dupes = df[df.duplicated("ts")]
    if not dupes.empty:
        print(f"  [AVVISO] {len(dupes)} timestamp duplicati da Alpaca {feed} — rimossi")
        df = df.drop_duplicates("ts")

    df = df.set_index("ts").sort_index()

    # Filtra solo regular hours (13:30–19:55 UTC)
    open_dt  = pd.Timestamp(f"{date}T{MARKET_OPEN_UTC}", tz="UTC")
    close_dt = pd.Timestamp(f"{date}T19:55:00", tz="UTC")
    df = df[(df.index >= open_dt) & (df.index <= close_dt)]

    return df, 200, "ok"


# ---------------------------------------------------------------------------
# Metriche di confronto
# ---------------------------------------------------------------------------

def compute_metrics(supa: pd.DataFrame, alpaca: pd.DataFrame, feed: str) -> dict:
    """
    Allinea le barre per timestamp e calcola le metriche di confronto.
    Non modifica i DataFrame originali.
    """
    # Allineamento inner join sul timestamp
    common_idx = supa.index.intersection(alpaca.index)
    only_supa  = supa.index.difference(alpaca.index)
    only_alp   = alpaca.index.difference(supa.index)

    s = supa.loc[common_idx]
    a = alpaca.loc[common_idx]

    n_common = len(common_idx)
    results = {
        "feed":            feed,
        "n_supabase":      len(supa),
        "n_alpaca":        len(alpaca),
        "n_common":        n_common,
        "n_only_supabase": len(only_supa),
        "n_only_alpaca":   len(only_alp),
        "ts_only_supabase": [str(t) for t in only_supa[:5]],
        "ts_only_alpaca":   [str(t) for t in only_alp[:5]],
    }

    if n_common == 0:
        results["error"] = "Nessun timestamp comune — impossibile calcolare metriche"
        return results

    # Differenze assolute OHLC
    for col in ["open", "high", "low", "close"]:
        diff = (s[col] - a[col]).abs()
        results[f"mad_{col}"]  = round(float(diff.mean()), 6)   # mean absolute diff
        results[f"maxd_{col}"] = round(float(diff.max()),  6)   # max absolute diff

    # Differenza percentuale sul close
    pct_diff = ((s["close"] - a["close"]).abs() / s["close"] * 100)
    results["close_pct_mean"]   = round(float(pct_diff.mean()), 6)
    results["close_pct_max"]    = round(float(pct_diff.max()),  6)

    # Distribuzione differenze close
    results["close_within_001pct"] = int((pct_diff <= 0.01).sum())
    results["close_within_005pct"] = int((pct_diff <= 0.05).sum())
    results["close_within_010pct"] = int((pct_diff <= 0.10).sum())
    results["close_above_010pct"]  = int((pct_diff  > 0.10).sum())
    results["close_within_001pct_pct"] = round(results["close_within_001pct"] / n_common * 100, 1)
    results["close_within_005pct_pct"] = round(results["close_within_005pct"] / n_common * 100, 1)
    results["close_within_010pct_pct"] = round(results["close_within_010pct"] / n_common * 100, 1)
    results["close_above_010pct_pct"]  = round(results["close_above_010pct"]  / n_common * 100, 1)

    # Volume ratio (alpaca / supabase)
    supa_vol  = s["volume"].replace(0, float("nan"))
    alpca_vol = a["volume"].replace(0, float("nan"))
    vol_ratio = alpca_vol / supa_vol

    results["vol_ratio_median"] = round(float(vol_ratio.median()), 4)
    results["vol_ratio_mean"]   = round(float(vol_ratio.mean()),   4)
    results["vol_ratio_std"]    = round(float(vol_ratio.std()),    4)
    results["vol_ratio_min"]    = round(float(vol_ratio.min()),    4)
    results["vol_ratio_max"]    = round(float(vol_ratio.max()),    4)

    # Correlazione volume (solo se n >= 3)
    if n_common >= 3:
        valid = vol_ratio.dropna()
        if len(valid) >= 3:
            corr = s["volume"].corr(a["volume"])
            results["vol_correlation"] = round(float(corr), 4) if not math.isnan(corr) else None
        else:
            results["vol_correlation"] = None
    else:
        results["vol_correlation"] = None

    # DataFrame allineato per export CSV
    merged = s.copy().rename(columns={c: f"supa_{c}" for c in ["open","high","low","close","volume"]})
    for col in ["open","high","low","close","volume"]:
        merged[f"alp_{col}"] = a[col]
    merged["close_abs_diff"] = (s["close"] - a["close"]).abs().round(4)
    merged["close_pct_diff"] = pct_diff.round(4)
    merged["vol_ratio"]      = vol_ratio.round(4)
    results["_merged_df"]    = merged   # privato, usato solo per CSV export

    return results


# ---------------------------------------------------------------------------
# Stampa report
# ---------------------------------------------------------------------------

def print_report(results_list: list, symbol: str, date: str, timeframe: str) -> None:
    sep = "=" * 68

    print(f"\n{sep}")
    print(f"  CONFRONTO FEED DI MERCATO")
    print(f"  Simbolo: {symbol}   Data: {date}   Timeframe: {timeframe}")
    print(sep)

    for r in results_list:
        feed = r["feed"].upper()
        print(f"\n{'─'*68}")
        print(f"  FEED: {feed}")
        print(f"{'─'*68}")

        if "http_status" in r:
            print(f"  STATUS HTTP: {r['http_status']}")
            print(f"  MESSAGGIO:   {r.get('message','')}")
            continue

        if "error" in r:
            print(f"  ERRORE: {r['error']}")
            continue

        print(f"\n  Barre allineamento:")
        print(f"    Supabase:           {r['n_supabase']:>6}")
        print(f"    Alpaca {feed}:      {r['n_alpaca']:>6}")
        print(f"    Timestamp comuni:   {r['n_common']:>6}")
        if r["n_only_supabase"]:
            ts_list = ", ".join(r["ts_only_supabase"])
            more = f" (+{r['n_only_supabase']-len(r['ts_only_supabase'])} altri)" if r["n_only_supabase"] > 5 else ""
            print(f"    Solo in Supabase:  {r['n_only_supabase']:>5}  [{ts_list}{more}]")
        if r["n_only_alpaca"]:
            ts_list = ", ".join(r["ts_only_alpaca"])
            more = f" (+{r['n_only_alpaca']-len(r['ts_only_alpaca'])} altri)" if r["n_only_alpaca"] > 5 else ""
            print(f"    Solo in Alpaca:    {r['n_only_alpaca']:>5}  [{ts_list}{more}]")

        print(f"\n  Differenze assolute OHLC (MAD = Mean Absolute Diff):")
        print(f"    {'':6}  {'MAD':>10}  {'MAX':>10}")
        for col in ["open", "high", "low", "close"]:
            print(f"    {col:6}  {r[f'mad_{col}']:>10.4f}  {r[f'maxd_{col}']:>10.4f}")

        print(f"\n  Differenza % sul close:")
        print(f"    Media:              {r['close_pct_mean']:>8.4f}%")
        print(f"    Massimo:            {r['close_pct_max']:>8.4f}%")

        print(f"\n  Distribuzione differenze close:")
        n = r["n_common"]
        print(f"    <= 0.01%:  {r['close_within_001pct']:>5} barre  ({r['close_within_001pct_pct']:>5.1f}%)")
        print(f"    <= 0.05%:  {r['close_within_005pct']:>5} barre  ({r['close_within_005pct_pct']:>5.1f}%)")
        print(f"    <= 0.10%:  {r['close_within_010pct']:>5} barre  ({r['close_within_010pct_pct']:>5.1f}%)")
        print(f"    >  0.10%:  {r['close_above_010pct']:>5} barre  ({r['close_above_010pct_pct']:>5.1f}%)")

        print(f"\n  Volume ratio (Alpaca / Supabase):")
        print(f"    Mediana:            {r['vol_ratio_median']:>8.4f}x")
        print(f"    Media:              {r['vol_ratio_mean']:>8.4f}x")
        print(f"    Deviazione std:     {r['vol_ratio_std']:>8.4f}")
        print(f"    Min:                {r['vol_ratio_min']:>8.4f}x")
        print(f"    Max:                {r['vol_ratio_max']:>8.4f}x")
        if r.get("vol_correlation") is not None:
            print(f"    Correlazione:       {r['vol_correlation']:>8.4f}")
        else:
            print(f"    Correlazione:       N/D")

        # Verdetto sintetico
        print(f"\n  ── VERDETTO {feed} ──")
        pct_mean = r["close_pct_mean"]
        pct_ok   = r["close_within_001pct_pct"]
        vol_med  = r["vol_ratio_median"]

        if pct_mean < 0.01 and pct_ok >= 90:
            verdict = "✅ COMPATIBILE — prezzi quasi identici a Supabase"
        elif pct_mean < 0.05 and pct_ok >= 70:
            verdict = "⚠️  ACCETTABILE — differenze contenute, verifica volume"
        else:
            verdict = "❌ INCOMPATIBILE — differenze significative, NON usare per continuare la serie"

        print(f"  {verdict}")
        print(f"  Volume ratio mediano: {vol_med:.2f}x Supabase")
        if vol_med < 0.50:
            print("  ⚠️  Volume sistematicamente inferiore — feed parziale (IEX ~2% mercato)")

    print(f"\n{sep}\n")


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

def export_csv(results_list: list, output_path: str) -> None:
    frames = []
    for r in results_list:
        df = r.get("_merged_df")
        if df is not None:
            df = df.copy()
            df.insert(0, "feed", r["feed"])
            frames.append(df)

    if not frames:
        print(f"[CSV] Nessun dato da esportare.")
        return

    combined = pd.concat(frames)
    combined.index.name = "ts"
    combined.to_csv(output_path)
    print(f"[CSV] Report barra-per-barra salvato in: {output_path}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Confronta barre Supabase con feed Alpaca (IEX/SIP). Read-only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--symbol",    required=True,           help="Ticker (es. SPY)")
    parser.add_argument("--date",      required=True,           help="Data YYYY-MM-DD")
    parser.add_argument("--timeframe", default="5Min",          help="Timeframe Alpaca (default: 5Min)")
    parser.add_argument("--feeds",     default="iex",           help="Feed da testare: iex,sip o entrambi (default: iex)")
    parser.add_argument("--output",    default=None,            help="Percorso CSV opzionale per report barra-per-barra")
    return parser.parse_args()


def validate_date(date_str: str) -> None:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"[ERRORE] Formato data non valido: '{date_str}'. Usa YYYY-MM-DD.")
        sys.exit(1)


def main() -> None:
    args = parse_args()
    validate_date(args.date)

    symbol    = args.symbol.upper()
    date      = args.date
    timeframe = args.timeframe
    feeds     = [f.strip().lower() for f in args.feeds.split(",")]

    valid_feeds = {"iex", "sip"}
    invalid = set(feeds) - valid_feeds
    if invalid:
        print(f"[ERRORE] Feed non riconosciuti: {invalid}. Usa 'iex', 'sip' o 'iex,sip'.")
        sys.exit(1)

    print(f"\n[START] compare_market_feeds.py")
    print(f"  Simbolo:    {symbol}")
    print(f"  Data:       {date}")
    print(f"  Timeframe:  {timeframe}")
    print(f"  Feed:       {feeds}")
    print()

    # Carica credenziali
    env = load_env()

    # Leggi da Supabase (read-only SELECT)
    supa_df = fetch_supabase_candles(env, symbol, date, timeframe)
    if supa_df.empty:
        sys.exit(0)

    # Per ogni feed richiesto
    results_list = []
    for feed in feeds:
        print(f"[ALPACA] Recupero barre feed={feed} ...")
        alp_df, status, msg = fetch_alpaca_bars(env, symbol, date, timeframe, feed)

        if alp_df is None:
            print(f"  [AVVISO] feed={feed}: {msg}")
            results_list.append({
                "feed":        feed,
                "http_status": status,
                "message":     msg,
            })
            continue

        print(f"  {len(alp_df)} barre ricevute (feed={feed})")
        metrics = compute_metrics(supa_df, alp_df, feed)
        results_list.append(metrics)

    # Stampa report
    print_report(results_list, symbol, date, timeframe)

    # Export CSV opzionale
    if args.output:
        export_csv(results_list, args.output)


if __name__ == "__main__":
    main()
