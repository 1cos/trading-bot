"""
Max Bot Lab — Session Processor

Reads 1-minute CSV data, calculates levels, detects breaks,
displacement, retests, and entry candles. Outputs JSON for the viewer.

This is a FIRST DRAFT with simple heuristics. Max will review the
output and tell us what to fix.
"""

import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path


# ─── Configuration ───────────────────────────────────────────────

# ORB: first 5 minutes of regular session (09:30-09:34 ET)
ORB_START = "09:30"
ORB_END = "09:34"

# Pre-market: 04:00 - 09:29 ET
PM_START = "04:00"
PM_END = "09:29"

# Regular session for PDH/PDL
REG_START = "09:30"
REG_END = "15:59"

# Trading window: 09:35 - 15:00 ET (= 08:35 - 14:00 CT)
TRADE_START = "09:35"
TRADE_END = "15:00"

# Displacement: minimum consecutive candles closing on the break side
DISPLACEMENT_MIN_CANDLES = 2

# Entry candle: max body/range ratio (body too big = not valid)
ENTRY_MAX_BODY_RATIO = 0.55  # body <= 55% of total range


# ─── Data Loading ────────────────────────────────────────────────

def load_csv(filepath):
    """Load 1m CSV into list of dicts with float values."""
    rows = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                'time': row['time_et'],
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': int(row['volume']),
            })
    return rows


def get_dates(rows):
    """Get unique trading dates from rows."""
    dates = set()
    for r in rows:
        d = r['time'][:10]
        # Only include dates with regular session data
        t = r['time'][11:16]
        if REG_START <= t <= REG_END:
            dates.add(d)
    return sorted(dates)


def rows_for_date(rows, date):
    """Filter rows for a specific date."""
    return [r for r in rows if r['time'].startswith(date)]


def rows_in_range(rows, time_start, time_end):
    """Filter rows by time-of-day range."""
    return [r for r in rows if time_start <= r['time'][11:16] <= time_end]


# ─── Level Calculation ───────────────────────────────────────────

def calc_pdh_pdl(all_rows, current_date):
    """Calculate PDH/PDL from the previous trading day."""
    dates = get_dates(all_rows)
    idx = dates.index(current_date) if current_date in dates else -1
    if idx <= 0:
        return None, None

    prev_date = dates[idx - 1]
    prev_rows = rows_for_date(all_rows, prev_date)
    reg = rows_in_range(prev_rows, REG_START, REG_END)
    if not reg:
        return None, None

    pdh = max(r['high'] for r in reg)
    pdl = min(r['low'] for r in reg)
    return pdh, pdl


def calc_pmh_pml(day_rows):
    """Calculate PMH/PML from pre-market session."""
    pm = rows_in_range(day_rows, PM_START, PM_END)
    if not pm:
        return None, None
    pmh = max(r['high'] for r in pm)
    pml = min(r['low'] for r in pm)
    return pmh, pml


def calc_orb(day_rows):
    """Calculate ORB from first 5 minutes."""
    orb = rows_in_range(day_rows, ORB_START, ORB_END)
    if not orb:
        return None, None
    orb_high = max(r['high'] for r in orb)
    orb_low = min(r['low'] for r in orb)
    return orb_high, orb_low


# ─── Break & Displacement Detection ─────────────────────────────

def detect_orb_break(candles, orb_high, orb_low):
    """
    Detect first ORB break with displacement.
    Returns: dict with direction, break_time, displacement_time, or None.
    """
    trading = [c for c in candles if TRADE_START <= c['time'][11:16] <= TRADE_END]

    # Track consecutive closes above/below ORB
    consec_above = 0
    consec_below = 0
    break_candle_above = None
    break_candle_below = None

    for c in trading:
        # Check for close above ORB High
        if c['close'] > orb_high:
            consec_above += 1
            if consec_above == 1:
                break_candle_above = c
            if consec_above >= DISPLACEMENT_MIN_CANDLES:
                return {
                    'direction': 'LONG',
                    'break_time': break_candle_above['time'],
                    'displacement_time': c['time'],
                    'break_price': break_candle_above['close'],
                }
        else:
            consec_above = 0
            break_candle_above = None

        # Check for close below ORB Low
        if c['close'] < orb_low:
            consec_below += 1
            if consec_below == 1:
                break_candle_below = c
            if consec_below >= DISPLACEMENT_MIN_CANDLES:
                return {
                    'direction': 'SHORT',
                    'break_time': break_candle_below['time'],
                    'displacement_time': c['time'],
                    'break_price': break_candle_below['close'],
                }
        else:
            consec_below = 0
            break_candle_below = None

    return None


# ─── Retest & Entry Detection ────────────────────────────────────

def is_max_entry_candle(candle, level, direction):
    """
    Check if a candle is a valid Max Entry Candle at a level.

    For LONG: wick goes below level, close above level, close near level.
    For SHORT: wick goes above level, close below level, close near level.

    Body must not be too large relative to total range.
    """
    o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']
    rng = h - l
    if rng == 0:
        return False

    body = abs(c - o)
    body_ratio = body / rng

    # Body too large = not a rejection, might be momentum candle
    if body_ratio > ENTRY_MAX_BODY_RATIO:
        return False

    if direction == 'LONG':
        # Wick must go below level (into the zone)
        if l >= level:
            return False
        # Close must be above level
        if c <= level:
            return False
        # Close should be near the level (not too far above)
        # "just outside" = within 40% of candle range from level
        dist_from_level = c - level
        if dist_from_level > rng * 0.6:
            return False
        # Must be bullish or near-doji closing above
        return True

    elif direction == 'SHORT':
        # Wick must go above level (into the zone)
        if h <= level:
            return False
        # Close must be below level
        if c >= level:
            return False
        # Close should be near the level
        dist_from_level = level - c
        if dist_from_level > rng * 0.6:
            return False
        return True

    return False


def detect_retests_and_entries(candles, levels, direction, start_after):
    """
    After displacement, scan for retests of broken levels.

    levels: list of dicts {name, price, family}
    direction: 'LONG' or 'SHORT'
    start_after: timestamp string — only look at candles after this
    """
    entries = []

    eligible = [c for c in candles
                if c['time'] > start_after
                and TRADE_START <= c['time'][11:16] <= TRADE_END]

    for c in eligible:
        for lvl in levels:
            if is_max_entry_candle(c, lvl['price'], direction):
                entries.append({
                    'time': c['time'],
                    'direction': direction,
                    'level_name': lvl['name'],
                    'level_price': lvl['price'],
                    'level_family': lvl['family'],
                    'entry_price': c['close'],
                    'candle': {
                        'open': c['open'],
                        'high': c['high'],
                        'low': c['low'],
                        'close': c['close'],
                    },
                    'stop_price': c['low'] if direction == 'LONG' else c['high'],
                })

    return entries


# ─── Trade Outcome ───────────────────────────────────────────────

def evaluate_trade(candles, entry, max_trades=1):
    """
    Evaluate a trade outcome: 2R target or stop.
    """
    direction = entry['direction']
    entry_price = entry['entry_price']
    stop_price = entry['stop_price']
    risk = abs(entry_price - stop_price)

    if risk == 0:
        return {**entry, 'outcome': 'INVALID', 'exit_price': None, 'exit_time': None, 'rr': 0}

    if direction == 'LONG':
        target = entry_price + 2 * risk
    else:
        target = entry_price - 2 * risk

    # Scan forward from entry
    after = [c for c in candles if c['time'] > entry['time']]

    for c in after:
        if direction == 'LONG':
            # Check stop first (conservative)
            if c['low'] <= stop_price:
                return {**entry, 'outcome': 'LOSS', 'exit_price': stop_price,
                        'exit_time': c['time'], 'target': target, 'risk': risk, 'rr': -1}
            if c['high'] >= target:
                return {**entry, 'outcome': 'WIN', 'exit_price': target,
                        'exit_time': c['time'], 'target': target, 'risk': risk, 'rr': 2}
        else:
            if c['high'] >= stop_price:
                return {**entry, 'outcome': 'LOSS', 'exit_price': stop_price,
                        'exit_time': c['time'], 'target': target, 'risk': risk, 'rr': -1}
            if c['low'] <= target:
                return {**entry, 'outcome': 'WIN', 'exit_price': target,
                        'exit_time': c['time'], 'target': target, 'risk': risk, 'rr': 2}

    return {**entry, 'outcome': 'OPEN', 'exit_price': None,
            'exit_time': None, 'target': target, 'risk': risk, 'rr': 0}


# ─── Session Processor ──────────────────────────────────────────

def process_session(all_rows, symbol, date):
    """Process one trading session and return results dict."""
    day_rows = rows_for_date(all_rows, date)
    if not day_rows:
        return None

    # Calculate levels
    pdh, pdl = calc_pdh_pdl(all_rows, date)
    pmh, pml = calc_pmh_pml(day_rows)
    orb_high, orb_low = calc_orb(day_rows)

    if orb_high is None:
        return None

    result = {
        'symbol': symbol,
        'date': date,
        'levels': {
            'pdh': pdh, 'pdl': pdl,
            'pmh': pmh, 'pml': pml,
            'orb_high': orb_high, 'orb_low': orb_low,
        },
        'orb_break': None,
        'entries': [],
        'trades': [],
    }

    # Detect ORB break with displacement
    orb_break = detect_orb_break(day_rows, orb_high, orb_low)
    if not orb_break:
        result['status'] = 'NO_BREAK'
        return result

    result['orb_break'] = orb_break
    direction = orb_break['direction']

    # Build retest levels based on direction
    levels = []
    if direction == 'LONG':
        # Broken upward — these become support to retest
        levels.append({'name': 'ORB_HIGH', 'price': orb_high, 'family': 'STRUCTURAL'})
        if pmh and pmh < orb_high * 1.01:  # PMH near or below ORB High
            levels.append({'name': 'PMH', 'price': pmh, 'family': 'STRUCTURAL'})
        if pmh and pmh > orb_high:
            levels.append({'name': 'PMH', 'price': pmh, 'family': 'STRUCTURAL'})
        if pdh:
            levels.append({'name': 'PDH', 'price': pdh, 'family': 'STRUCTURAL'})
    else:
        # Broken downward — these become resistance to retest
        levels.append({'name': 'ORB_LOW', 'price': orb_low, 'family': 'STRUCTURAL'})
        if pml:
            levels.append({'name': 'PML', 'price': pml, 'family': 'STRUCTURAL'})
        if pdl:
            levels.append({'name': 'PDL', 'price': pdl, 'family': 'STRUCTURAL'})

    # Detect entries
    entries = detect_retests_and_entries(
        day_rows, levels, direction, orb_break['displacement_time']
    )

    # Apply Max Bot rules: max 2 trades, stop after win
    trades = []
    for entry in entries:
        if len(trades) >= 2:
            break
        # Check if previous trade was a win
        if trades and trades[-1]['outcome'] == 'WIN':
            break
        # Don't enter too close to a previous entry (min 5 min gap)
        if trades:
            prev_time = trades[-1]['time']
            if entry['time'] <= prev_time:
                continue
            # Simple dedup: at least 5 candles apart
            prev_dt = datetime.strptime(prev_time, '%Y-%m-%d %H:%M:%S')
            entry_dt = datetime.strptime(entry['time'], '%Y-%m-%d %H:%M:%S')
            if (entry_dt - prev_dt).seconds < 300:
                continue

        trade = evaluate_trade(day_rows, entry)
        trades.append(trade)

    result['entries'] = [{'time': e['time'], 'level': e['level_name'],
                          'direction': e['direction']} for e in entries]
    result['trades'] = trades
    result['status'] = 'TRADED' if trades else 'NO_ENTRY'

    return result


# ─── Main ────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 maxbot_lab/engine.py <symbol> [date]")
        print("Example: python3 maxbot_lab/engine.py MNQ")
        print("         python3 maxbot_lab/engine.py MNQ 2026-07-31")
        sys.exit(1)

    symbol = sys.argv[1].upper()
    target_date = sys.argv[2] if len(sys.argv) > 2 else None

    csv_path = Path(f"dati/1m/{symbol}_1m.csv")
    if not csv_path.exists():
        print(f"Error: {csv_path} not found")
        sys.exit(1)

    print(f"Loading {csv_path}...")
    all_rows = load_csv(csv_path)
    dates = get_dates(all_rows)
    print(f"Found {len(dates)} trading days: {dates}")

    if target_date:
        dates = [target_date]

    results = []
    for date in dates:
        result = process_session(all_rows, symbol, date)
        if result:
            results.append(result)
            status = result['status']
            n_trades = len(result['trades'])
            wins = sum(1 for t in result['trades'] if t['outcome'] == 'WIN')
            losses = sum(1 for t in result['trades'] if t['outcome'] == 'LOSS')
            brk = result['orb_break']
            brk_dir = brk['direction'] if brk else 'NONE'
            print(f"  {date}: {status} | Break: {brk_dir} | "
                  f"Entries found: {len(result['entries'])} | "
                  f"Trades: {n_trades} (W:{wins} L:{losses})")

    # Save results
    out_dir = Path("maxbot_lab/output")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / f"{symbol}_sessions.json"
    with open(out_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_file}")

    # Also save candle data for the viewer
    candle_file = out_dir / f"{symbol}_candles.json"
    candle_data = {}
    for date in [r['date'] for r in results]:
        day_rows = rows_for_date(all_rows, date)
        # Only regular session + a bit of pre-market for context
        relevant = [r for r in day_rows if r['time'][11:16] >= '09:00']
        candle_data[date] = relevant
    with open(candle_file, 'w') as f:
        json.dump(candle_data, f)
    print(f"Candle data saved to {candle_file}")


if __name__ == '__main__':
    main()
