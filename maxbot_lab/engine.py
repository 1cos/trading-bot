"""
Max Bot Lab — Session Processor v2

Follows the V0.3 sequence strictly:
  1. Calculate levels (PDH/PDL, PMH/PML, ORB)
  2. Wait for ORB break
  3. Require displacement (consecutive closes on break side)
  4. After displacement, wait for retest of broken levels
  5. At retest, check for Max Entry Candle geometry
  6. Evaluate trade outcome (2R target, stop at candle extreme)

Trading window: 09:35-15:00 ET (= 08:35-14:00 CT)
Max 2 trades per day. Stop after first win.

Entry candle geometry from BDRR engine:
  - rejection_wick_ratio >= 0.47
  - body_ratio <= 0.40
  - wick must penetrate the level
  - close must be on the correct side
  - close near the level (not too far)
"""

import csv
import json
import sys
from datetime import datetime
from pathlib import Path


# ─── Constants ───────────────────────────────────────────────────

ORB_START = "09:30"
ORB_END = "09:34"
PM_START = "04:00"
PM_END = "09:29"
REG_START = "09:30"
REG_END = "15:59"
TRADE_START = "09:35"
TRADE_END = "15:00"

# Displacement: min consecutive candles closing on break side
DISP_MIN_CANDLES = 3

# Max Entry Candle geometry (from BDRR frozen constants)
REJECTION_WICK_RATIO_MIN = 0.47  # wick must be >= 47% of range
BODY_RATIO_MAX = 0.40            # body must be <= 40% of range

# How close the close must be to the level (as fraction of range)
# Max said: "close just outside, not far"
CLOSE_PROXIMITY_MAX = 0.50  # close within 50% of range from level


# ─── Data Loading ────────────────────────────────────────────────

def load_csv(filepath):
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
    dates = set()
    for r in rows:
        if REG_START <= r['time'][11:16] <= REG_END:
            dates.add(r['time'][:10])
    return sorted(dates)


def rows_for_date(rows, date):
    return [r for r in rows if r['time'].startswith(date)]


def rows_in_range(rows, t_start, t_end):
    return [r for r in rows if t_start <= r['time'][11:16] <= t_end]


# ─── Level Calculation ───────────────────────────────────────────

def calc_pdh_pdl(all_rows, current_date):
    dates = get_dates(all_rows)
    if current_date not in dates:
        return None, None
    idx = dates.index(current_date)
    if idx == 0:
        return None, None
    prev = dates[idx - 1]
    reg = rows_in_range(rows_for_date(all_rows, prev), REG_START, REG_END)
    if not reg:
        return None, None
    return max(r['high'] for r in reg), min(r['low'] for r in reg)


def calc_pmh_pml(day_rows):
    pm = rows_in_range(day_rows, PM_START, PM_END)
    if not pm:
        return None, None
    return max(r['high'] for r in pm), min(r['low'] for r in pm)


def calc_orb(day_rows):
    orb = rows_in_range(day_rows, ORB_START, ORB_END)
    if not orb:
        return None, None
    return max(r['high'] for r in orb), min(r['low'] for r in orb)


# ─── Story State Machine ────────────────────────────────────────

class MaxBotSession:
    """
    Stateful session processor that follows the V0.3 sequence:
    WAITING_FOR_BREAK → BREAK_OBSERVED → DISPLACEMENT_PENDING →
    DISPLACEMENT_CONFIRMED → watching for retests → ENTRY_ELIGIBLE
    """

    def __init__(self, levels):
        self.levels = levels  # dict: orb_high, orb_low, pdh, pdl, pmh, pml
        self.state = 'WAITING_FOR_BREAK'
        self.direction = None
        self.break_candle = None
        self.displacement_count = 0
        self.displacement_candle = None
        self.retest_levels = []  # levels eligible for retest
        self.trades = []
        self.all_entries = []  # all detected entry opportunities
        self.events = []  # timeline of events

    def _add_event(self, time, event_type, detail=''):
        self.events.append({'time': time, 'type': event_type, 'detail': detail})

    def _orb_high(self):
        return self.levels['orb_high']

    def _orb_low(self):
        return self.levels['orb_low']

    def _in_trading_window(self, candle):
        t = candle['time'][11:16]
        return TRADE_START <= t <= TRADE_END

    def _max_trades_reached(self):
        if len(self.trades) >= 2:
            return True
        return False

    def _is_max_entry_candle(self, candle, level_price, direction):
        """
        Check Max Entry Candle geometry.
        Uses frozen BDRR thresholds: wick >= 47%, body <= 40%.
        """
        o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']
        rng = h - l
        if rng == 0:
            return False

        body = abs(c - o)
        body_ratio = body / rng

        if body_ratio > BODY_RATIO_MAX:
            return False

        if direction == 'LONG':
            # For LONG: rejection wick is below the body
            wick_into_level = min(o, c) - l  # lower wick
            wick_ratio = wick_into_level / rng

            if wick_ratio < REJECTION_WICK_RATIO_MIN:
                return False

            # Must wick below or into the level
            if l >= level_price:
                return False

            # Must close above the level
            if c <= level_price:
                return False

            # Close should be near the level (not too far above)
            dist = c - level_price
            if dist > rng * CLOSE_PROXIMITY_MAX:
                return False

            return True

        elif direction == 'SHORT':
            # For SHORT: rejection wick is above the body
            wick_into_level = h - max(o, c)  # upper wick
            wick_ratio = wick_into_level / rng

            if wick_ratio < REJECTION_WICK_RATIO_MIN:
                return False

            # Must wick above or into the level
            if h <= level_price:
                return False

            # Must close below the level
            if c >= level_price:
                return False

            # Close should be near the level
            dist = level_price - c
            if dist > rng * CLOSE_PROXIMITY_MAX:
                return False

            return True

        return False

    def _build_retest_levels(self):
        """Build the list of levels eligible for retest after displacement."""
        levels = []
        if self.direction == 'LONG':
            # Price broke upward — these become supports to retest from above
            levels.append({'name': 'ORB_HIGH', 'price': self._orb_high(), 'family': 'STRUCTURAL'})
            if self.levels['pmh']:
                levels.append({'name': 'PMH', 'price': self.levels['pmh'], 'family': 'STRUCTURAL'})
            if self.levels['pdh']:
                levels.append({'name': 'PDH', 'price': self.levels['pdh'], 'family': 'STRUCTURAL'})
        else:
            # Price broke downward — these become resistances to retest from below
            levels.append({'name': 'ORB_LOW', 'price': self._orb_low(), 'family': 'STRUCTURAL'})
            if self.levels['pml']:
                levels.append({'name': 'PML', 'price': self.levels['pml'], 'family': 'STRUCTURAL'})
            if self.levels['pdl']:
                levels.append({'name': 'PDL', 'price': self.levels['pdl'], 'family': 'STRUCTURAL'})
        return levels

    def process_candle(self, candle):
        """Process one candle through the state machine."""

        if not self._in_trading_window(candle):
            return

        if self._max_trades_reached():
            return

        c = candle['close']
        orb_h = self._orb_high()
        orb_l = self._orb_low()

        # ── WAITING_FOR_BREAK ────────────────────────────────────
        if self.state == 'WAITING_FOR_BREAK':
            if c > orb_h:
                self.state = 'DISPLACEMENT_PENDING'
                self.direction = 'LONG'
                self.break_candle = candle
                self.displacement_count = 1
                self._add_event(candle['time'], 'BREAK_OBSERVED', 'LONG')
            elif c < orb_l:
                self.state = 'DISPLACEMENT_PENDING'
                self.direction = 'SHORT'
                self.break_candle = candle
                self.displacement_count = 1
                self._add_event(candle['time'], 'BREAK_OBSERVED', 'SHORT')
            return

        # ── DISPLACEMENT_PENDING ─────────────────────────────────
        if self.state == 'DISPLACEMENT_PENDING':
            # Check if candle continues on the break side
            if self.direction == 'LONG' and c > orb_h:
                self.displacement_count += 1
            elif self.direction == 'SHORT' and c < orb_l:
                self.displacement_count += 1
            else:
                # Re-entered ORB — break failed, reset
                self._add_event(candle['time'], 'BREAK_FAILED',
                                f'Re-entered ORB after {self.displacement_count} candles')
                self.state = 'WAITING_FOR_BREAK'
                self.direction = None
                self.break_candle = None
                self.displacement_count = 0
                return

            if self.displacement_count >= DISP_MIN_CANDLES:
                self.state = 'DISPLACEMENT_CONFIRMED'
                self.displacement_candle = candle
                self.retest_levels = self._build_retest_levels()
                self._add_event(candle['time'], 'DISPLACEMENT_CONFIRMED',
                                f'{self.direction} after {self.displacement_count} candles')
            return

        # ── DISPLACEMENT_CONFIRMED — watching for retests ────────
        if self.state == 'DISPLACEMENT_CONFIRMED':
            # Check if story is invalidated (material re-entry)
            if self.direction == 'LONG' and c < orb_l:
                self._add_event(candle['time'], 'STORY_INVALIDATED',
                                'Close below ORB Low')
                self.state = 'WAITING_FOR_BREAK'
                self.direction = None
                return
            elif self.direction == 'SHORT' and c > orb_h:
                self._add_event(candle['time'], 'STORY_INVALIDATED',
                                'Close above ORB High')
                self.state = 'WAITING_FOR_BREAK'
                self.direction = None
                return

            # Check for Max Entry Candle at each retest level
            for lvl in self.retest_levels:
                if self._is_max_entry_candle(candle, lvl['price'], self.direction):
                    entry = {
                        'time': candle['time'],
                        'direction': self.direction,
                        'level_name': lvl['name'],
                        'level_price': lvl['price'],
                        'level_family': lvl['family'],
                        'entry_price': candle['close'],
                        'candle': {
                            'open': candle['open'],
                            'high': candle['high'],
                            'low': candle['low'],
                            'close': candle['close'],
                        },
                        'stop_price': candle['low'] if self.direction == 'LONG' else candle['high'],
                    }
                    self.all_entries.append(entry)

                    # Check dedup: don't enter within 5 min of last trade
                    if self.trades:
                        last_time = datetime.strptime(self.trades[-1]['time'], '%Y-%m-%d %H:%M:%S')
                        this_time = datetime.strptime(candle['time'], '%Y-%m-%d %H:%M:%S')
                        if (this_time - last_time).seconds < 300:
                            continue

                    if not self._max_trades_reached():
                        self._add_event(candle['time'], 'ENTRY',
                                        f'{self.direction} @{lvl["name"]} {candle["close"]:.2f}')
                        self.trades.append(entry)
                    break  # only one entry per candle
            return

    def evaluate_trades(self, all_candles):
        """Evaluate all trades against subsequent price action."""
        evaluated = []
        for trade in self.trades:
            entry_price = trade['entry_price']
            stop_price = trade['stop_price']
            risk = abs(entry_price - stop_price)

            if risk == 0:
                evaluated.append({**trade, 'outcome': 'INVALID',
                                  'exit_price': None, 'exit_time': None,
                                  'target': None, 'risk': 0, 'rr': 0})
                continue

            if trade['direction'] == 'LONG':
                target = entry_price + 2 * risk
            else:
                target = entry_price - 2 * risk

            after = [c for c in all_candles if c['time'] > trade['time']]
            outcome = 'OPEN'
            exit_price = None
            exit_time = None

            for c in after:
                if trade['direction'] == 'LONG':
                    if c['low'] <= stop_price:
                        outcome, exit_price, exit_time = 'LOSS', stop_price, c['time']
                        break
                    if c['high'] >= target:
                        outcome, exit_price, exit_time = 'WIN', target, c['time']
                        break
                else:
                    if c['high'] >= stop_price:
                        outcome, exit_price, exit_time = 'LOSS', stop_price, c['time']
                        break
                    if c['low'] <= target:
                        outcome, exit_price, exit_time = 'WIN', target, c['time']
                        break

            evaluated.append({**trade, 'outcome': outcome,
                              'exit_price': exit_price, 'exit_time': exit_time,
                              'target': target, 'risk': risk, 'rr': 2 if outcome == 'WIN' else -1 if outcome == 'LOSS' else 0})
        return evaluated


# ─── Session Processor ──────────────────────────────────────────

def process_session(all_rows, symbol, date):
    day_rows = rows_for_date(all_rows, date)
    if not day_rows:
        return None

    pdh, pdl = calc_pdh_pdl(all_rows, date)
    pmh, pml = calc_pmh_pml(day_rows)
    orb_high, orb_low = calc_orb(day_rows)

    if orb_high is None:
        return None

    levels = {
        'pdh': pdh, 'pdl': pdl,
        'pmh': pmh, 'pml': pml,
        'orb_high': orb_high, 'orb_low': orb_low,
    }

    session = MaxBotSession(levels)

    # Process all trading window candles
    trading_candles = [c for c in day_rows if TRADE_START <= c['time'][11:16] <= TRADE_END]
    for candle in trading_candles:
        session.process_candle(candle)

    # Evaluate trades
    evaluated_trades = session.evaluate_trades(day_rows)

    # Build result
    orb_break = None
    for e in session.events:
        if e['type'] == 'DISPLACEMENT_CONFIRMED':
            orb_break = {
                'direction': session.direction or e['detail'].split()[0],
                'break_time': next((ev['time'] for ev in session.events if ev['type'] == 'BREAK_OBSERVED'), ''),
                'displacement_time': e['time'],
            }
            break

    n_entries = len(session.all_entries)
    status = 'NO_BREAK' if not orb_break else ('TRADED' if evaluated_trades else 'NO_ENTRY')

    return {
        'symbol': symbol,
        'date': date,
        'levels': levels,
        'orb_break': orb_break,
        'entries': [{'time': e['time'], 'level': e['level_name'],
                     'direction': e['direction']} for e in session.all_entries],
        'trades': evaluated_trades,
        'events': session.events,
        'status': status,
    }


# ─── Main ────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 maxbot_lab/engine.py <symbol> [date]")
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
    print(f"Trading days: {dates}")

    if target_date:
        dates = [target_date]

    results = []
    for date in dates:
        result = process_session(all_rows, symbol, date)
        if result:
            results.append(result)
            n_trades = len(result['trades'])
            wins = sum(1 for t in result['trades'] if t['outcome'] == 'WIN')
            losses = sum(1 for t in result['trades'] if t['outcome'] == 'LOSS')
            brk = result['orb_break']
            brk_dir = brk['direction'] if brk else 'NONE'

            # Show events
            print(f"\n  {date}: {result['status']} | Break: {brk_dir}")
            for ev in result['events']:
                print(f"    {ev['time'][11:16]} | {ev['type']}: {ev['detail']}")
            if n_trades:
                print(f"    Trades: {n_trades} (W:{wins} L:{losses})")
                for t in result['trades']:
                    print(f"      {t['time'][11:16]} {t['direction']} @{t['level_name']} "
                          f"E:{t['entry_price']:.2f} S:{t['stop_price']:.2f} "
                          f"T:{t.get('target', 0):.2f} → {t['outcome']}")

    # Save
    out_dir = Path("maxbot_lab/output")
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / f"{symbol}_sessions.json", 'w') as f:
        json.dump(results, f, indent=2)

    candle_data = {}
    for date in [r['date'] for r in results]:
        day = rows_for_date(all_rows, date)
        candle_data[date] = [r for r in day if r['time'][11:16] >= '09:00']
    with open(out_dir / f"{symbol}_candles.json", 'w') as f:
        json.dump(candle_data, f)

    print(f"\nSaved to {out_dir}")


if __name__ == '__main__':
    main()
