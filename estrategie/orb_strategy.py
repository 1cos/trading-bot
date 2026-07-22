import pandas as pd
import numpy as np
import os

# ============================================================
# CONFIGURAZIONE
# ============================================================

SIMBOLO = "SPY"
CARTELLA_DATI = os.path.expanduser("~/trading_bot/dati")

# ORB — Opening Range Breakout
ORB_INIZIO = "09:30"
ORB_FINE = "09:35"
SESSIONE_FINE = "15:30"

# Risk/Reward
RR_ORB = 4.0
RR_OB = 2.0

# Filtro wick minima (il wick deve entrare almeno X% dentro la zona)
WICK_MIN_PERCENT = 0.30

# Filtro momentum OB — il body deve essere almeno X% della candela
MOMENTUM_MIN = 0.55

# ============================================================
# CARICA DATI
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

# ============================================================
# FUNZIONI DI ANALISI
# ============================================================

def calcola_orb(df, data):
    """Calcola ORB High e ORB Low per un giorno specifico"""
    giorno = df[df.index.date == data]
    orb = giorno.between_time(ORB_INIZIO, ORB_FINE)
    if orb.empty:
        return None, None
    return orb['High'].max(), orb['Low'].min()

def body_ratio(row):
    """Dimensione body rispetto alla candela intera"""
    size = row['High'] - row['Low']
    if size == 0:
        return 0
    return abs(row['Close'] - row['Open']) / size

def wick_entra_in_zona(row, zona_high, zona_low):
    """Il wick della candela entra nella zona"""
    return row['Low'] <= zona_high and row['High'] >= zona_low

def chiude_sopra_zona(row, zona_high):
    """Il body chiude sopra la zona"""
    return row['Close'] > zona_high

def chiude_sotto_zona(row, zona_low):
    """Il body chiude sotto la zona"""
    return row['Close'] < zona_low

def e_engulfing_rialzista(row, prev):
    """La candela attuale ingoia completamente quella precedente"""
    return (row['Close'] > prev['Open'] and 
            row['Open'] < prev['Close'] and
            row['Close'] > row['Open'] and
            prev['Close'] < prev['Open'])

def trova_order_blocks(df, finestra=10):
    """
    Identifica Order Block rialzisti:
    - Candela rossa con momentum
    - Seguita da movimento rialzista forte
    """
    obs = []
    for i in range(1, len(df) - finestra):
        row = df.iloc[i]
        
        # Candela rossa con momentum
        if row['Close'] >= row['Open']:
            continue
        if body_ratio(row) < MOMENTUM_MIN:
            continue
            
        # Controlla che dopo ci sia momentum rialzista
        future = df.iloc[i+1:i+finestra]
        if future.empty:
            continue
            
        max_future = future['High'].max()
        if max_future > row['High'] * 1.002:  # almeno 0.2% sopra l'OB
            obs.append({
                'index': i,
                'timestamp': df.index[i],
                'ob_high': row['High'],
                'ob_low': row['Low'],
                'ob_body_top': row['Open'],
                'ob_body_bottom': row['Close']
            })
    
    return obs

# ============================================================
# STRATEGIA 1 — ORB Break & Retest
# ============================================================

def trova_segnali_orb(df):
    segnali = []
    date_uniche = list(set(df.index.date))
    date_uniche.sort()
    
    for data in date_uniche:
        orb_high, orb_low = calcola_orb(df, data)
        if orb_high is None:
            continue
        
        giorno = df[df.index.date == data]
        post_orb = giorno.between_time("09:35", SESSIONE_FINE)
        
        breakout_avvenuto = False
        segnali_oggi = 0  # ← MAX 1 segnale al giorno
        
        for i in range(1, len(post_orb)):
            
            if segnali_oggi >= 1:  # ← solo il primo setup pulito
                break
                
            row = post_orb.iloc[i]
            prev = post_orb.iloc[i-1]
            ts = post_orb.index[i]
            
            # Breakout confermato — solo nelle prime 2 ore
            orario = ts.strftime('%H:%M')
            if orario > "11:30":  # ← solo entrate mattutine
                break
                
            if prev['Close'] > orb_high:
                breakout_avvenuto = True
            
            if not breakout_avvenuto:
                continue
            
            # Filtro momentum — il breakout deve essere forte
            breakout_body = body_ratio(prev)
            if breakout_body < 0.30:  # ← candela di breakout con momentum
                continue
            
            # SETUP 1 — wick dentro ORB, close sopra
            wick_ok = wick_entra_in_zona(row, orb_high, orb_low)
            close_ok = chiude_sopra_zona(row, orb_high)
            momentum_ok = body_ratio(row) >= 0.40
            
            if wick_ok and close_ok and momentum_ok:
                stop = row['Low']
                rischio = row['Close'] - stop
                if rischio <= 0 or rischio > 2.0:  # ← stop max 2$ su SPY
                    continue
                target = row['Close'] + (rischio * RR_ORB)
                segnali.append({
                    'timestamp': ts,
                    'setup': 'ORB_RETEST',
                    'tipo': 'LONG',
                    'entry': round(row['Close'], 2),
                    'stop': round(stop, 2),
                    'target': round(target, 2),
                    'rischio': round(rischio, 2),
                    'rr': RR_ORB,
                    'trigger': 'WICK+CLOSE'
                })
                segnali_oggi += 1
                breakout_avvenuto = False
                continue
            
            # SETUP 2 — engulfing
            if e_engulfing_rialzista(row, prev) and chiude_sopra_zona(row, orb_high):
                stop = row['Low']
                rischio = row['Close'] - stop
                if rischio <= 0 or rischio > 2.0:
                    continue
                target = row['Close'] + (rischio * RR_ORB)
                segnali.append({
                    'timestamp': ts,
                    'setup': 'ORB_ENGULFING',
                    'tipo': 'LONG',
                    'entry': round(row['Close'], 2),
                    'stop': round(stop, 2),
                    'target': round(target, 2),
                    'rischio': round(rischio, 2),
                    'rr': RR_ORB,
                    'trigger': 'ENGULFING'
                })
                segnali_oggi += 1
                breakout_avvenuto = False
    
    return pd.DataFrame(segnali)

# ============================================================
# STRATEGIA 2 — Order Block con Momentum
# ============================================================

def trova_segnali_ob(df):
    segnali = []
    obs = trova_order_blocks(df)
    
    for ob in obs:
        ob_idx = ob['index']
        ob_high = ob['ob_high']
        ob_low = ob['ob_low']
        
        # Cerca retest nelle candele successive
        future_df = df.iloc[ob_idx+1:ob_idx+50]
        
        for i in range(1, len(future_df)):
            row = future_df.iloc[i]
            prev = future_df.iloc[i-1]
            ts = future_df.index[i]
            
            # Controlla sessione
            orario = ts.strftime('%H:%M')
            if not ("09:35" <= orario <= SESSIONE_FINE):
                continue
            
            # SETUP 1 — wick dentro OB, close sopra
            wick_ok = wick_entra_in_zona(row, ob_high, ob_low)
            close_ok = chiude_sopra_zona(row, ob_high)
            momentum_ok = body_ratio(row) >= MOMENTUM_MIN
            
            if wick_ok and close_ok and momentum_ok:
                stop = ob_low
                rischio = row['Close'] - stop
                if rischio <= 0:
                    continue
                target = row['Close'] + (rischio * RR_OB)
                segnali.append({
                    'timestamp': ts,
                    'setup': 'OB_RETEST',
                    'tipo': 'LONG',
                    'entry': round(row['Close'], 2),
                    'stop': round(stop, 2),
                    'target': round(target, 2),
                    'rischio': round(rischio, 2),
                    'rr': RR_OB,
                    'trigger': 'WICK+CLOSE'
                })
                break
            
            # SETUP 2 — engulfing con momentum
            if e_engulfing_rialzista(row, prev) and chiude_sopra_zona(row, ob_high):
                stop = ob_low
                rischio = row['Close'] - stop
                if rischio <= 0:
                    continue
                target = row['Close'] + (rischio * RR_OB)
                segnali.append({
                    'timestamp': ts,
                    'setup': 'OB_ENGULFING',
                    'tipo': 'LONG',
                    'entry': round(row['Close'], 2),
                    'stop': round(stop, 2),
                    'target': round(target, 2),
                    'rischio': round(rischio, 2),
                    'rr': RR_OB,
                    'trigger': 'ENGULFING'
                })
                break
    
    return pd.DataFrame(segnali)

# ============================================================
# BACKTESTING
# ============================================================

def calcola_risultati(segnali, df):
    risultati = []
    
    for _, s in segnali.iterrows():
        ts = s['timestamp']
        tipo = s['tipo']
        entry = s['entry']
        stop = s['stop']
        target = s['target']
        
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
        
        if esito is None:
            esito = 'APERTO'
            uscita = future.iloc[-1]['Close'] if len(future) > 0 else entry
        
        pnl = (uscita - entry) if tipo == 'LONG' else (entry - uscita)
        
        risultati.append({
            'timestamp': ts,
            'setup': s['setup'],
            'trigger': s['trigger'],
            'tipo': tipo,
            'entry': entry,
            'stop': stop,
            'target': target,
            'uscita': round(uscita, 2),
            'esito': esito,
            'pnl': round(pnl, 2)
        })
    
    return pd.DataFrame(risultati)

def stampa_risultati(risultati, nome_setup):
    print(f"\n{'='*50}")
    print(f"📊 {nome_setup}")
    print(f"{'='*50}")
    
    if risultati.empty:
        print("⚠️  Nessun segnale trovato")
        return
    
    chiusi = risultati[risultati['esito'] != 'APERTO']
    if chiusi.empty:
        print("⚠️  Nessun trade chiuso")
        return
    
    win = chiusi[chiusi['esito'] == 'TARGET']
    loss = chiusi[chiusi['esito'] == 'STOP']
    win_rate = len(win) / len(chiusi) * 100
    
    print(f"📊 Trade totali:     {len(risultati)}")
    print(f"📊 Trade chiusi:     {len(chiusi)}")
    print(f"✅ Target:           {len(win)}")
    print(f"❌ Stop:             {len(loss)}")
    print(f"🎯 Win Rate:         {win_rate:.1f}%")
    print(f"💰 Profitto totale:  ${chiusi['pnl'].sum():.2f}")
    print(f"📉 Profitto medio:   ${chiusi['pnl'].mean():.2f}")
    print(f"⬆️  Max vincita:      ${chiusi['pnl'].max():.2f}")
    print(f"⬇️  Max perdita:      ${chiusi['pnl'].min():.2f}")

# ============================================================
# ESECUZIONE
# ============================================================

print(f"🚀 ORB + Order Block Strategy su {SIMBOLO}")
print(f"{'='*50}")

df = carica_dati(SIMBOLO)
print(f"✅ Dati caricati: {len(df)} candele")
print(f"📅 Dal {df.index[0].date()} al {df.index[-1].date()}")

# Strategia 1 — ORB
print("\n🔍 Cerco segnali ORB...")
segnali_orb = trova_segnali_orb(df)
risultati_orb = calcola_risultati(segnali_orb, df) if not segnali_orb.empty else pd.DataFrame()
stampa_risultati(risultati_orb, "STRATEGIA 1 — ORB Break & Retest")

# Strategia 2 — OB
print("\n🔍 Cerco segnali Order Block...")
segnali_ob = trova_segnali_ob(df)
risultati_ob = calcola_risultati(segnali_ob, df) if not segnali_ob.empty else pd.DataFrame()
stampa_risultati(risultati_ob, "STRATEGIA 2 — Order Block + Momentum")

# Salva tutto
if not risultati_orb.empty:
    risultati_orb.to_csv(os.path.join(CARTELLA_DATI, f"{SIMBOLO}_orb_backtest.csv"), index=False)
if not risultati_ob.empty:
    risultati_ob.to_csv(os.path.join(CARTELLA_DATI, f"{SIMBOLO}_ob_backtest.csv"), index=False)

print(f"\n💾 Risultati salvati in ~/trading_bot/dati/")