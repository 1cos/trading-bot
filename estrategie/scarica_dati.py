import yfinance as yf
import pandas as pd
import os

# Cartella dove salvare i dati
CARTELLA_DATI = os.path.expanduser("~/trading_bot/dati")

# Strumenti da scaricare
strumenti = {
    "SPY": "SPY",      # S&P 500 ETF
    "QQQ": "QQQ",      # Nasdaq ETF (proxy per NQ)
    "AMZN": "AMZN",
    "TSLA": "TSLA",
    "NVDA": "NVDA",
    "META": "META",
    "MSFT": "MSFT",
    "GOOGL": "GOOGL",
    "MU": "MU"
}

# Periodo e timeframe
PERIODO = "60d"        # ultimi 60 giorni
INTERVALLO = "5m"      # candele da 1 minuto

print("🚀 Inizio download dati storici...")

for nome, ticker in strumenti.items():
    print(f"  ⬇️  Scarico {nome}...")
    try:
        df = yf.download(
            ticker,
            period=PERIODO,
            interval=INTERVALLO,
            progress=False,
            auto_adjust=True
        )
        if df.empty:
            print(f"  ⚠️  Nessun dato per {nome}")
            continue
        
        # Salva come CSV
        path = os.path.join(CARTELLA_DATI, f"{nome}_1m.csv")
        df.to_csv(path)
        print(f"  ✅ {nome} salvato — {len(df)} candele")
    
    except Exception as e:
        print(f"  ❌ Errore su {nome}: {e}")

print("\n✅ Download completato! Dati salvati in ~/trading_bot/dati/")