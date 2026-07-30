"""Sanity check: SPY 2026-07-22 across all timeframes.

Run from the trading_bot directory with the venv activated:
    python backend/sanity_check.py
"""

import csv
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_lab.multi_timeframe_runner import run_multi_timeframe
from trading_lab.timeframe_aggregation import aggregate_post_orb

ET = ZoneInfo("America/New_York")

# Load SPY 1m data
candles_1m = []
with open('dati/SPY_1m.csv') as f:
    reader = csv.reader(f)
    for i, row in enumerate(reader):
        if i < 3: continue
        if not row[0].strip(): continue
        dt = datetime.fromisoformat(row[0])
        candles_1m.append({
            'time_ms': int(dt.timestamp() * 1000),
            'open': float(row[4]), 'high': float(row[2]),
            'low': float(row[3]), 'close': float(row[1]),
            'volume': int(float(row[5])),
        })

# Split into sessions by date
sessions = {}
for c in candles_1m:
    dt = datetime.fromtimestamp(c['time_ms']/1000, tz=timezone.utc).astimezone(ET)
    date = dt.strftime('%Y-%m-%d')
    if date not in sessions: sessions[date] = []
    sessions[date].append(c)

date = '2026-07-22'
bars = sessions[date]
print(f"SPY {date}: {len(bars)} 1m bars")

# Canonical ORB
orb_summary, _ = aggregate_post_orb(bars, 5)
print(f"\nCanonical ORB (09:30-09:34):")
print(f"  High: {orb_summary['high']:.4f}")
print(f"  Low:  {orb_summary['low']:.4f}")

# ORB equality
print(f"\n{'='*60}")
print("ORB EQUALITY")
orb_ref = (orb_summary['high'], orb_summary['low'])
for tf in [1, 2, 3, 5, 10]:
    o, _ = aggregate_post_orb(bars, tf)
    match = "OK" if (o['high'], o['low']) == orb_ref else "MISMATCH"
    print(f"  {tf:>3}m: H={o['high']:.4f} L={o['low']:.4f}  {match}")

# First post-ORB candle
print(f"\n{'='*60}")
print("FIRST POST-ORB CANDLE")
for tf in [1, 2, 3, 5, 10]:
    _, post = aggregate_post_orb(bars, tf)
    first_dt = datetime.fromtimestamp(post[0]['time_ms']/1000, tz=timezone.utc).astimezone(ET)
    print(f"  {tf:>3}m: {first_dt.strftime('%H:%M')}  bars={len(post)}  {'OK' if first_dt.strftime('%H:%M')=='09:35' else 'FAIL'}")

# Detector results
print(f"\n{'='*60}")
print("DETECTOR RESULTS")
by_date = {date: bars}
for tf in [1, 2, 3, 5, 10]:
    for direction in ['LONG', 'SHORT']:
        results = run_multi_timeframe(by_date, 'SPY', tf, direction)
        if not results: 
            print(f"  {tf:>3}m {direction}: no result")
            continue
        r = results[0]
        status = r['detection_status']
        if status == 'VALID':
            dr = r['detection_result']
            brk_t = datetime.fromtimestamp(dr.break_bar.bar_utc_ms/1000, tz=timezone.utc).astimezone(ET).strftime('%H:%M')
            conf_t = datetime.fromtimestamp(dr.confirmation_bar.bar_utc_ms/1000, tz=timezone.utc).astimezone(ET).strftime('%H:%M')
            print(f"  {tf:>3}m {direction}: VALID -> {r['outcome']}  break={brk_t}  conf={conf_t}")
        else:
            fs = r.get('failure_stage', '?')
            print(f"  {tf:>3}m {direction}: INVALID  stage={fs}")

print(f"\n{'='*60}")
print("SANITY CHECK COMPLETE")
