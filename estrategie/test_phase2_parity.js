/**
 * test_phase2_parity.js
 *
 * Repository-data parity test for PDH/PDL Phase 2 engine.
 *
 * Reads the actual SPY_5m.csv, runs the Phase 2 strategyPDHPDL,
 * and compares trade-by-trade against:
 *   - dati/SPY_segnali_pdh_pdl.csv  (signals: 80 rows)
 *   - dati/SPY_backtest.csv          (outcomes: 80 rows, 79 closed + 1 APERTO)
 *
 * Run: node estrategie/test_phase2_parity.js
 */

'use strict';

const fs   = require('fs');
const path = require('path');

// ── Load definition ────────────────────────────────────────────────────────
require('./pdh_pdl_definition.js');

// ── Parse CSV ──────────────────────────────────────────────────────────────
function parseSignalCSV(filepath) {
  const lines = fs.readFileSync(filepath, 'utf8').trim().split('\n');
  const header = lines[0].split(',');
  return lines.slice(1).map(line => {
    const cols = line.split(',');
    const obj = {};
    header.forEach((h, i) => obj[h.trim()] = cols[i] ? cols[i].trim() : '');
    return obj;
  });
}

function parseBacktestCSV(filepath) {
  return parseSignalCSV(filepath); // same structure
}

// Parse SPY_5m.csv (3 header rows, then data)
function parseCandlesCSV(filepath) {
  const lines = fs.readFileSync(filepath, 'utf8').trim().split('\n');
  // Skip 3 header lines: "Price,...", "Ticker,...", "Datetime,..."
  const candles = [];
  for (let i = 3; i < lines.length; i++) {
    const cols = lines[i].split(',');
    if (!cols[0] || !cols[0].trim()) continue;
    const tsStr = cols[0].trim(); // e.g. "2026-04-24 09:30:00-04:00"
    // Parse as UTC: the offset is always -04:00 (EDT) or -05:00 (EST)
    // Replace space with T and keep the offset so Date.parse handles it
    const time = new Date(tsStr.replace(' ', 'T'));
    candles.push({
      time,
      tsStr,
      close:  parseFloat(cols[1]),
      high:   parseFloat(cols[2]),
      low:    parseFloat(cols[3]),
      open:   parseFloat(cols[4]),
      volume: parseFloat(cols[5]) || 0,
    });
  }
  return candles;
}

// ── ET timezone helpers ────────────────────────────────────────────────────
// Use Intl.DateTimeFormat with America/New_York — correct across EDT and EST.

const _etHHMMFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  hour: '2-digit', minute: '2-digit', hour12: false,
});
const _etDateFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York',
  year: 'numeric', month: '2-digit', day: '2-digit',
});

function getETHHMM(t) {
  const parts = _etHHMMFmt.formatToParts(t);
  const h = parseInt(parts.find(p => p.type === 'hour').value,   10);
  const m = parseInt(parts.find(p => p.type === 'minute').value, 10);
  return h * 100 + m;
}

function getETDateStr(t) {
  return _etDateFmt.format(t); // "YYYY-MM-DD"
}

// ── getDailyHL ────────────────────────────────────────────────────────────
// Keyed by ET date string. Matches Python calcola_pdh_pdl (groupby Date,
// max High, min Low of all candles in that calendar day).
function getDailyHL(candles) {
  const d = {};
  candles.forEach(c => {
    const k = getETDateStr(c.time);
    if (!d[k]) d[k] = { high: -Infinity, low: Infinity };
    if (c.high > d[k].high) d[k].high = c.high;
    if (c.low  < d[k].low)  d[k].low  = c.low;
  });
  return d;
}

// ── Phase 2 strategyPDHPDL ────────────────────────────────────────────────
//
// Exact replica of the engine in index.html post-Phase-2.
// Must be kept byte-for-byte identical to that copy.
function strategyPDHPDL(candles, params) {
  // ── Resolve all seven fields from the certified definition ─────────────
  const _def = (typeof STRATEGY_DEFINITIONS !== 'undefined' && STRATEGY_DEFINITIONS['pdh_pdl_v1'])
               || { parameters: {} };
  const dp = _def.parameters;

  const BODY_MIN = dp.body_min !== undefined ? dp.body_min : 0.20;
  const BODY_MAX = dp.body_max !== undefined ? dp.body_max : 0.70;

  // Session window — Python in_sessione: '09:30' <= hhmm_str <= '16:00'
  // As integers: 930 <= etHHMM <= 1600. Both bounds inclusive.
  const SESSION_START_ET = dp.session_start !== undefined
    ? (function(s) { const p=s.split(':'); return parseInt(p[0],10)*100+parseInt(p[1],10); })(dp.session_start)
    : 930;
  const SESSION_END_ET = dp.session_end !== undefined
    ? (function(s) { const p=s.split(':'); return parseInt(p[0],10)*100+parseInt(p[1],10); })(dp.session_end)
    : 1600;

  // allow_reentry from definition — true = no inTrade lock
  const DEF_ALLOW_REENTRY = dp.allow_reentry !== undefined ? dp.allow_reentry : true;

  // sl_ticks / rr: UI params override certified baseline
  const SL_TICKS = (params.sl_ticks !== undefined) ? params.sl_ticks : (dp.sl_ticks || 4);
  const RR       = (params.rr       !== undefined) ? params.rr       : (dp.rr       || 2);
  const slPts    = SL_TICKS * 0.25;

  // ── PDH/PDL from previous calendar day (ET date grouping) ─────────────
  const dailyHL  = getDailyHL(candles);
  const etDates  = Object.keys(dailyHL).sort(); // sorted calendar day strings

  const signals  = [];

  // State machine — mirrors Python trova_segnali exactly:
  //   broken_pdh, broken_pdl, pdh_rotto, pdl_rotto: persist across session gaps,
  //   reset only on calendar day boundary.
  //   No inTrade lock (allow_reentry controls this; currently always true).
  let broken_pdh = false, broken_pdl = false;
  let pdh_rotto  = null,  pdl_rotto  = null;
  let lastDate   = null;

  candles.forEach((c, i) => {
    if (i === 0) return; // need prev candle

    const etDate = getETDateStr(c.time);
    const di     = etDates.indexOf(etDate);
    if (di < 1) return; // no previous day data

    const PDH = dailyHL[etDates[di - 1]].high;
    const PDL = dailyHL[etDates[di - 1]].low;
    const prev = candles[i - 1];

    // ── New calendar day: reset breakout state ──────────────────────────
    // Python: if data_corrente != ts.date(): reset broken_pdh, broken_pdl, etc.
    if (etDate !== lastDate) {
      lastDate   = etDate;
      broken_pdh = false; broken_pdl = false;
      pdh_rotto  = null;  pdl_rotto  = null;
    }

    // ── Session gate ─────────────────────────────────────────────────────
    // Python in_sessione: '09:30' <= hhmm <= '16:00' (both inclusive)
    const etHHMM = getETHHMM(c.time);
    if (etHHMM < SESSION_START_ET || etHHMM > SESSION_END_ET) return;

    // ── Breakout detection ────────────────────────────────────────────────
    // Python: prev['Close'] < pdh and row['Close'] > pdh
    // Note: PDH/PDL used here is the PREVIOUS day's high/low
    if (prev.close < PDH && c.close > PDH) {
      broken_pdh = true;  pdh_rotto = PDH;
      broken_pdl = false; pdl_rotto = null;
    }
    if (prev.close > PDL && c.close < PDL) {
      broken_pdl = true;  pdl_rotto = PDL;
      broken_pdh = false; pdh_rotto = null;
    }

    // ── Body filter ───────────────────────────────────────────────────────
    const tot = c.high - c.low;
    if (tot === 0) return;
    const bodyRatio = Math.abs(c.close - c.open) / tot;
    if (bodyRatio < BODY_MIN || bodyRatio > BODY_MAX) return;

    // ── Retest LONG ───────────────────────────────────────────────────────
    // Python: broken_pdh AND pdh_rotto AND Low≤pdh_rotto≤High AND Close>pdh_rotto
    if (broken_pdh && pdh_rotto !== null) {
      if (c.low <= pdh_rotto && pdh_rotto <= c.high && c.close > pdh_rotto) {
        const entry  = Math.round(c.close    * 100) / 100;
        const stop   = Math.round((entry - slPts)    * 100) / 100;
        const target = Math.round((entry + slPts * RR) * 100) / 100;
        signals.push({
          time:    c.time,
          type:    'LONG',
          setup:   'PDH',
          level:   pdh_rotto,
          entry,
          stop,
          target,
          trigger: 'BODY',
        });
        broken_pdh = false; // reset THIS flag; broken_pdl state unchanged
        // NOTE: DEF_ALLOW_REENTRY=true → no inTrade lock; function continues
        return; // match Python: signal on this candle means stop processing it
      }
    }

    // ── Retest SHORT ──────────────────────────────────────────────────────
    if (broken_pdl && pdl_rotto !== null) {
      if (c.low <= pdl_rotto && pdl_rotto <= c.high && c.close < pdl_rotto) {
        const entry  = Math.round(c.close    * 100) / 100;
        const stop   = Math.round((entry + slPts)    * 100) / 100;
        const target = Math.round((entry - slPts * RR) * 100) / 100;
        signals.push({
          time:    c.time,
          type:    'SHORT',
          setup:   'PDL',
          level:   pdl_rotto,
          entry,
          stop,
          target,
          trigger: 'BODY',
        });
        broken_pdl = false;
      }
    }
  });

  return signals;
}

// ── Baseline-parity simulation (Python-compatible APERTO handling) ─────────
// For signal parity: use simulation that matches Python calcola_risultati:
//   - Scan future candles; check Low<=stop (STOP) or High>=target (TARGET)
//   - If neither fires before end of data: APERTO (last close used as uscita in Python)
//   - No EOD force-close (Python does not force-close at 16:00)
function simulatePythonStyle(signals, candles) {
  return signals.map(s => {
    const future = candles.filter(c => c.time > s.time);
    let esito = 'APERTO', uscita = s.entry;

    for (const c of future) {
      if (s.type === 'LONG') {
        if (c.low <= s.stop)    { esito = 'STOP';   uscita = s.stop;   break; }
        if (c.high >= s.target) { esito = 'TARGET'; uscita = s.target; break; }
      } else {
        if (c.high >= s.stop)   { esito = 'STOP';   uscita = s.stop;   break; }
        if (c.low  <= s.target) { esito = 'TARGET'; uscita = s.target; break; }
      }
    }

    // For APERTO, Python uses the last available close (future.iloc[-1]['Close'])
    if (esito === 'APERTO' && future.length > 0) {
      uscita = future[future.length - 1].close;
    }

    const pnl = s.type === 'LONG' ? uscita - s.entry : s.entry - uscita;
    return { ...s, uscita: Math.round(uscita * 100) / 100, esito, pnl: Math.round(pnl * 100) / 100 };
  });
}

// ── Test helpers ───────────────────────────────────────────────────────────
let passed = 0, failed = 0;

function assert(cond, label) {
  if (cond) { console.log('  ✅  ' + label); passed++; }
  else       { console.error('  ❌  FAIL: ' + label); failed++; }
}

// ── Load data ──────────────────────────────────────────────────────────────
const ROOT     = path.join(__dirname, '..');
const candles  = parseCandlesCSV(path.join(ROOT, 'dati', 'SPY_5m.csv'));
const csvSigs  = parseSignalCSV( path.join(ROOT, 'dati', 'SPY_segnali_pdh_pdl.csv'));
const csvBT    = parseBacktestCSV(path.join(ROOT, 'dati', 'SPY_backtest.csv'));

// Parse CSV signal timestamps to UTC Date objects
// Timestamps in CSV are ET with offset e.g. "2026-04-27 13:25:00-04:00"
function parseETTimestamp(str) {
  return new Date(str.replace(' ', 'T'));
}

const PARAMS = {}; // use all certified definition values

const jsSigs  = strategyPDHPDL(candles, PARAMS);
const jsTrades = simulatePythonStyle(jsSigs, candles);

// ══════════════════════════════════════════════════════════════════════════════
console.log('\n═══════════════════════════════════════════════════════════════════');
console.log(' Phase 2 PDH/PDL — CSV Parity Report');
console.log('═══════════════════════════════════════════════════════════════════\n');

// ── Signal count ──────────────────────────────────────────────────────────
const EXPECTED_SIGNALS = 80;
console.log(`Signal count: expected=${EXPECTED_SIGNALS}, actual=${jsSigs.length}`);
assert(jsSigs.length === EXPECTED_SIGNALS,
  `Signal count === ${EXPECTED_SIGNALS}`);

// ── Signal parity — trade by trade ───────────────────────────────────────
console.log('\n── Signal parity (SPY_segnali_pdh_pdl.csv vs JS) ──\n');

// Floating point tolerance for prices (matching Python's round(x, 2))
const PRICE_TOL = 0.005; // more than enough for round(x,2) comparison

let sigMismatches = 0;
const nCompare = Math.min(csvSigs.length, jsSigs.length);

for (let i = 0; i < nCompare; i++) {
  const csv = csvSigs[i];
  const js  = jsSigs[i];

  const csvTime  = parseETTimestamp(csv.timestamp);
  const jsTime   = js.time;
  const timeOK   = Math.abs(csvTime.getTime() - jsTime.getTime()) < 60000; // within 1 min

  const csvType  = csv.tipo === 'LONG' ? 'LONG' : 'SHORT';
  const typeOK   = js.type === csvType;

  const csvEntry  = parseFloat(csv.entry);
  const csvStop   = parseFloat(csv.stop);
  const csvTarget = parseFloat(csv.target);
  const csvLevel  = parseFloat(csv.livello);

  const entryOK  = Math.abs(js.entry  - csvEntry)  <= PRICE_TOL;
  const stopOK   = Math.abs(js.stop   - csvStop)   <= PRICE_TOL;
  const targetOK = Math.abs(js.target - csvTarget) <= PRICE_TOL;
  const levelOK  = Math.abs(js.level  - csvLevel)  <= PRICE_TOL;

  const ok = timeOK && typeOK && entryOK && stopOK && targetOK && levelOK;

  if (!ok) {
    sigMismatches++;
    if (sigMismatches <= 5) { // show first 5 mismatches
      console.error(`  Mismatch at index ${i}:`);
      console.error(`    CSV: ${csv.timestamp} ${csv.tipo} entry=${csv.entry} stop=${csv.stop} target=${csv.target} level=${csv.livello}`);
      console.error(`    JS:  ${js.time.toISOString()} ${js.type} entry=${js.entry} stop=${js.stop} target=${js.target} level=${js.level}`);
      if (!timeOK)   console.error(`    ↳ TIME mismatch`);
      if (!typeOK)   console.error(`    ↳ TYPE mismatch`);
      if (!entryOK)  console.error(`    ↳ ENTRY mismatch  csv=${csvEntry} js=${js.entry}`);
      if (!stopOK)   console.error(`    ↳ STOP mismatch   csv=${csvStop} js=${js.stop}`);
      if (!targetOK) console.error(`    ↳ TARGET mismatch csv=${csvTarget} js=${js.target}`);
      if (!levelOK)  console.error(`    ↳ LEVEL mismatch  csv=${csvLevel} js=${js.level}`);
    }
  }
}

// Extra or missing signals
const nExtra   = Math.max(0, jsSigs.length  - csvSigs.length);
const nMissing = Math.max(0, csvSigs.length - jsSigs.length);

assert(nMissing === 0,         `Missing signals: 0`);
assert(nExtra === 0,           `Extra signals:   0`);
assert(sigMismatches === 0,    `Field mismatches: 0  (tolerance ±${PRICE_TOL})`);

if (sigMismatches === 0 && nMissing === 0 && nExtra === 0) {
  console.log(`\n  ✅  All ${csvSigs.length} signals match CSV exactly (tolerance ±${PRICE_TOL})`);
}

// ── Outcome parity (SPY_backtest.csv) ────────────────────────────────────
console.log('\n── Outcome parity (SPY_backtest.csv vs JS simulation) ──\n');

const closed   = jsTrades.filter(t => t.esito !== 'APERTO');
const aperti   = jsTrades.filter(t => t.esito === 'APERTO');
const targets  = closed.filter(t => t.esito === 'TARGET');
const stops    = closed.filter(t => t.esito === 'STOP');
const wr       = closed.length ? (targets.length / closed.length * 100) : 0;

console.log(`  Trades total:   ${jsTrades.length} (expected 80)`);
console.log(`  Closed:         ${closed.length}   (expected 79)`);
console.log(`  APERTO:         ${aperti.length}   (expected 1)`);
console.log(`  TARGET:         ${targets.length}  (expected 24)`);
console.log(`  STOP:           ${stops.length}   (expected 55)`);
console.log(`  Win rate:       ${wr.toFixed(1)}% (expected 30.4%)`);

assert(jsTrades.length === 80,  'Total trades === 80');
assert(closed.length  === 79,   'Closed === 79');
assert(aperti.length  === 1,    'APERTO === 1');
assert(targets.length === 24,   'TARGET === 24');
assert(stops.length   === 55,   'STOP === 55');
assert(Math.abs(wr - 30.4) < 0.1, `Win rate ≈ 30.4% (actual ${wr.toFixed(1)}%)`);

// Per-trade outcome comparison
let outcomeMismatches = 0;
const nBT = Math.min(csvBT.length, jsTrades.length);
for (let i = 0; i < nBT; i++) {
  const csv = csvBT[i];
  const js  = jsTrades[i];
  const csvEsito = csv.esito.trim().toUpperCase();
  const jsEsito  = js.esito.toUpperCase();
  if (csvEsito !== jsEsito) {
    outcomeMismatches++;
    if (outcomeMismatches <= 5) {
      console.error(`  Outcome mismatch at index ${i}: CSV=${csvEsito} JS=${jsEsito} (${csv.timestamp})`);
    }
  }
}
assert(outcomeMismatches === 0, `Per-trade outcome matches: 0 mismatches`);

// ── Summary ────────────────────────────────────────────────────────────────
console.log('\n═══════════════════════════════════════════════════════════════════');
console.log(` Results: ${passed} passed, ${failed} failed`);
console.log('═══════════════════════════════════════════════════════════════════\n');

if (failed > 0) process.exit(1);
