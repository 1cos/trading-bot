import pandas as pd
import numpy as np
import os

# ============================================================
# CONFIGURAZIONE
# ============================================================

SIMBOLO = "SPY"
CARTELLA_DATI = os.path.expanduser("~/trading_bot/dati")

BODY_MIN = 0.20
BODY_MAX = 0.70

STOP_LOSS_TICKS = 4
TAKE_PROFIT_MULTIPLIER = 2.0

SESSIONE_INIZIO = "09:30"
SESSIONE_FINE = "16:00"

# ============================================================
# FUNZIONI
# ============================================================

def carica_dati(simbolo):
    path = os.path.join(CARTELLA_DATI, f"{simbolo}_5m.csv")
    df = pd.read_csv(path, skiprows=3, header=None)
    df.columns = ['Datetime', 'Close', 'High', 'Low', 'Open', 'Volume']
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    df = df.set_index('Datetime')
    df.index = df.index.tz_convert('America/New_York')
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)
    return df

def calcola_pdh_pdl(df):
    df = df.copy()
    df['Date'] = df.index.date
    df['Datetime'] = df.index  # salviamo l'indice prima del merge
    
    daily = df.groupby('Date').agg(
        Day_High=('High', 'max'),
        Day_Low=('Low', 'min')
    ).reset_index()
    
    daily['PDH'] = daily['Day_High'].shift(1)
    daily['PDL'] = daily['Day_Low'].shift(1)
    
    df = df.merge(daily[['Date', 'PDH', 'PDL']], on='Date', how='left')
    df = df.set_index('Datetime')  # ripristiniamo l'indice datetime
    df.index.name = 'Datetime'
    return df

def analizza_body(row):
    candela_size = row['High'] - row['Low']
    if candela_size == 0:
        return 0
    body_size = abs(row['Close'] - row['Open'])
    return body_size / candela_size

def body_valido(row):
    ratio = analizza_body(row)
    return BODY_MIN <= ratio <= BODY_MAX

def in_sessione(timestamp):
    orario = timestamp.strftime('%H:%M')
    return SESSIONE_INIZIO <= orario <= SESSIONE_FINE

def trova_segnali(df):
    segnali = []
    
    broken_pdh = False
    broken_pdl = False
    pdh_rotto = None
    pdl_rotto = None
    data_corrente = None
    
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        ts = df.index[i]

        if pd.isna(row['PDH']) or pd.isna(row['PDL']):
            continue

        # Reset a inizio nuovo giorno
        if data_corrente != ts.date():
            data_corrente = ts.date()
            broken_pdh = False
            broken_pdl = False
            pdh_rotto = None
            pdl_rotto = None

        if not in_sessione(ts):
            continue

        pdh = row['PDH']
        pdl = row['PDL']

        # BREAKOUT rialzista
        if prev['Close'] < pdh and row['Close'] > pdh:
            broken_pdh = True
            pdh_rotto = pdh
            broken_pdl = False

        # BREAKOUT ribassista
        if prev['Close'] > pdl and row['Close'] < pdl:
            broken_pdl = True
            pdl_rotto = pdl
            broken_pdh = False

        # RETEST LONG
        if broken_pdh and pdh_rotto:
            if row['Low'] <= pdh_rotto <= row['High']:
                if row['Close'] > pdh_rotto:
                    if body_valido(row):
                        entry = row['Close']
                        stop = entry - (STOP_LOSS_TICKS * 0.25)
                        target = entry + (STOP_LOSS_TICKS * 0.25 * TAKE_PROFIT_MULTIPLIER)
                        segnali.append({
                            'timestamp': ts,
                            'tipo': 'LONG',
                            'livello': pdh_rotto,
                            'entry': round(entry, 2),
                            'stop': round(stop, 2),
                            'target': round(target, 2),
                            'body_ratio': round(analizza_body(row), 2)
                        })
                        broken_pdh = False

        # RETEST SHORT
        if broken_pdl and pdl_rotto:
            if row['Low'] <= pdl_rotto <= row['High']:
                if row['Close'] < pdl_rotto:
                    if body_valido(row):
                        entry = row['Close']
                        stop = entry + (STOP_LOSS_TICKS * 0.25)
                        target = entry - (STOP_LOSS_TICKS * 0.25 * TAKE_PROFIT_MULTIPLIER)
                        segnali.append({
                            'timestamp': ts,
                            'tipo': 'SHORT',
                            'livello': pdl_rotto,
                            'entry': round(entry, 2),
                            'stop': round(stop, 2),
                            'target': round(target, 2),
                            'body_ratio': round(analizza_body(row), 2)
                        })
                        broken_pdl = False

    return pd.DataFrame(segnali)

# ============================================================
# ESECUZIONE
# ============================================================

print(f"📊 Analisi PDH/PDL Break & Retest su {SIMBOLO}")
print("=" * 50)

df = carica_dati(SIMBOLO)

print(f"✅ Dati caricati: {len(df)} candele")
print(f"📅 Dal {df.index[0].date()} al {df.index[-1].date()}")
print()

df = calcola_pdh_pdl(df)
segnali = trova_segnali(df)

if segnali.empty:
    print("⚠️  Nessun segnale trovato nel periodo")
else:
    print(f"🎯 Segnali trovati: {len(segnali)}")
    print(f"   📈 LONG:  {len(segnali[segnali['tipo']=='LONG'])}")
    print(f"   📉 SHORT: {len(segnali[segnali['tipo']=='SHORT'])}")
    print()
    print("Ultimi 5 segnali:")
    print(segnali.tail(5).to_string(index=False))

    path_out = os.path.join(CARTELLA_DATI, f"{SIMBOLO}_segnali_pdh_pdl.csv")
    segnali.to_csv(path_out, index=False)
    print(f"\n💾 Segnali salvati in: {path_out}")
    # ============================================================
# BACKTESTING — calcolo risultati
# ============================================================

def calcola_risultati(segnali, df):
    risultati = []
    
    for _, segnale in segnali.iterrows():
        ts = segnale['timestamp']
        tipo = segnale['tipo']
        entry = segnale['entry']
        stop = segnale['stop']
        target = segnale['target']
        
        # Prendi tutte le candele DOPO il segnale
        future = df[df.index > ts]
        
        esito = None
        uscita = None
        
        for i, row in future.iterrows():
            if tipo == 'LONG':
                if row['Low'] <= stop:
                    esito = 'STOP'
                    uscita = stop
                    break
                if row['High'] >= target:
                    esito = 'TARGET'
                    uscita = target
                    break
            elif tipo == 'SHORT':
                if row['High'] >= stop:
                    esito = 'STOP'
                    uscita = stop
                    break
                if row['Low'] <= target:
                    esito = 'TARGET'
                    uscita = target
                    break
        
        if esito is None:
            esito = 'APERTO'
            uscita = future.iloc[-1]['Close'] if len(future) > 0 else entry
        
        pnl = (uscita - entry) if tipo == 'LONG' else (entry - uscita)
        
        risultati.append({
            'timestamp': ts,
            'tipo': tipo,
            'entry': entry,
            'uscita': round(uscita, 2),
            'esito': esito,
            'pnl': round(pnl, 2)
        })
    
    return pd.DataFrame(risultati)

print("\n" + "=" * 50)
print("📈 BACKTESTING RISULTATI")
print("=" * 50)

risultati = calcola_risultati(segnali, df)

chiusi = risultati[risultati['esito'] != 'APERTO']
win = chiusi[chiusi['esito'] == 'TARGET']
loss = chiusi[chiusi['esito'] == 'STOP']

win_rate = len(win) / len(chiusi) * 100 if len(chiusi) > 0 else 0
profitto_totale = chiusi['pnl'].sum()
profitto_medio = chiusi['pnl'].mean()
max_win = chiusi['pnl'].max()
max_loss = chiusi['pnl'].min()

print(f"📊 Trade chiusi:     {len(chiusi)}")
print(f"✅ Target raggiunti: {len(win)}")
print(f"❌ Stop colpiti:     {len(loss)}")
print(f"🎯 Win Rate:         {win_rate:.1f}%")
print(f"💰 Profitto totale:  ${profitto_totale:.2f}")
print(f"📉 Profitto medio:   ${profitto_medio:.2f}")
print(f"⬆️  Max vincita:      ${max_win:.2f}")
print(f"⬇️  Max perdita:      ${max_loss:.2f}")

# Salva risultati
path_risultati = os.path.join(CARTELLA_DATI, f"{SIMBOLO}_backtest.csv")
risultati.to_csv(path_risultati, index=False)
print(f"\n💾 Risultati salvati in: {path_risultati}")