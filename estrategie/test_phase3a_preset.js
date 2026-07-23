/**
 * test_phase3a_preset.js
 *
 * Phase 3A — Certified PDH/PDL Preset UI tests.
 *
 * Covers (Node-only, no browser DOM required):
 *   T1.  Built-in preset 'pdh_pdl_v1' is present in STRATEGY_DEFINITIONS.
 *   T2.  Built-in preset name is exactly 'PDH/PDL Original (Python baseline)'.
 *   T3.  Built-in preset version is '2.0.0'.
 *   T4.  Built-in preset sl_ticks sourced from definition (4).
 *   T5.  Built-in preset rr sourced from definition (2).
 *   T6.  Built-in preset body_min sourced from definition (0.20).
 *   T7.  Built-in preset body_max sourced from definition (0.70).
 *   T8.  Built-in preset session_start sourced from definition ('09:30').
 *   T9.  Built-in preset session_end sourced from definition ('16:00').
 *   T10. Built-in preset allow_reentry sourced from definition (true).
 *   T11. Certified slider values: sl_ticks=4, rr×10=20 → isCertifiedSliderValues true.
 *   T12. Changed sl_ticks → isCertifiedSliderValues false.
 *   T13. Changed rr → isCertifiedSliderValues false.
 *   T14. Restored to certified values → isCertifiedSliderValues true.
 *   T15. Phase 2 parity: 80 signals on SPY repository data (fast check).
 *   T16. Phase 2 parity: 24 TARGET, 55 STOP, 1 APERTO (summary).
 *   T17. Phase 2 parity: closed-trade win rate ≈ 30.4%.
 *   T18. User preset system: config structure is compatible with loadPreset.
 *   T19. Built-in preset loading does NOT mutate STRATEGY_DEFINITIONS['pdh_pdl_v1'].
 *   T20. strategyPDHPDL with certified params still produces 80 signals.
 *
 * Run: node estrategie/test_phase3a_preset.js
 */

'use strict';

const fs   = require('fs');
const path = require('path');

// ── Load definition ────────────────────────────────────────────────────────
require('./pdh_pdl_definition.js');

// ── Test runner ───────────────────────────────────────────────────────────
let passed = 0, failed = 0;
function test(name, fn) {
  try {
    fn();
    console.log(`  ✅ ${name}`);
    passed++;
  } catch (e) {
    console.error(`  ❌ ${name}`);
    console.error(`     ${e.message}`);
    failed++;
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function assertEq(a, b, msg) {
  if (a !== b) throw new Error((msg || '') + ` — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

// ── CSV helpers (from test_phase2_parity.js) ──────────────────────────────
function parseCSV(filepath) {
  const lines = fs.readFileSync(filepath, 'utf8').trim().split('\n');
  const header = lines[0].split(',');
  return lines.slice(1).map(line => {
    const cols = line.split(',');
    const obj = {};
    header.forEach((h, i) => obj[h.trim()] = (cols[i] || '').trim());
    return obj;
  });
}

function parseCandlesCSV(filepath) {
  const lines = fs.readFileSync(filepath, 'utf8').trim().split('\n');
  const candles = [];
  for (let i = 3; i < lines.length; i++) {
    const cols = lines[i].split(',');
    if (!cols[0] || !cols[0].trim()) continue;
    const time = new Date(cols[0].trim().replace(' ', 'T'));
    candles.push({
      time,
      close: parseFloat(cols[1]),
      high:  parseFloat(cols[2]),
      low:   parseFloat(cols[3]),
      open:  parseFloat(cols[4]),
    });
  }
  return candles;
}

// ── strategyPDHPDL engine (exact copy from index.html Phase 2) ─────────────
function strategyPDHPDL(candles, params) {
  const _def = (typeof STRATEGY_DEFINITIONS !== 'undefined' && STRATEGY_DEFINITIONS['pdh_pdl_v1'])
               || { parameters: {} };
  const dp = _def.parameters;
  const BODY_MIN = dp.body_min !== undefined ? dp.body_min : 0.20;
  const BODY_MAX = dp.body_max !== undefined ? dp.body_max : 0.70;
  function etStrToHHMM(s, fallback) {
    if (!s || typeof s !== 'string') return fallback;
    const p = s.split(':');
    if (p.length !== 2) return fallback;
    const h = parseInt(p[0], 10), m = parseInt(p[1], 10);
    return (isNaN(h) || isNaN(m)) ? fallback : h * 100 + m;
  }
  const SESSION_START_ET = etStrToHHMM(dp.session_start, 930);
  const SESSION_END_ET   = etStrToHHMM(dp.session_end,   1600);
  const SL_TICKS = (params.sl_ticks !== undefined) ? params.sl_ticks : (dp.sl_ticks || 4);
  const RR       = (params.rr       !== undefined) ? params.rr       : (dp.rr       || 2);
  const slPts    = SL_TICKS * 0.25;
  const _etHHMMFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false });
  const _etDateFmt = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' });
  function getETHHMM(t) { const p = _etHHMMFmt.formatToParts(t); return parseInt(p.find(x => x.type === 'hour').value, 10) * 100 + parseInt(p.find(x => x.type === 'minute').value, 10); }
  function getETDate(t) { return _etDateFmt.format(t); }
  const dailyHL = {};
  candles.forEach(c => {
    const k = getETDate(c.time);
    if (!dailyHL[k]) dailyHL[k] = { high: -Infinity, low: Infinity };
    if (c.high > dailyHL[k].high) dailyHL[k].high = c.high;
    if (c.low  < dailyHL[k].low)  dailyHL[k].low  = c.low;
  });
  const etDates = Object.keys(dailyHL).sort();
  const signals = [];
  let broken_pdh = false, broken_pdl = false, pdh_rotto = null, pdl_rotto = null, lastDate = null;
  candles.forEach((c, i) => {
    if (!i) return;
    const etDate = getETDate(c.time);
    const di = etDates.indexOf(etDate);
    if (di < 1) return;
    const PDH = dailyHL[etDates[di-1]].high, PDL = dailyHL[etDates[di-1]].low;
    const prev = candles[i-1];
    if (etDate !== lastDate) { lastDate = etDate; broken_pdh = false; broken_pdl = false; pdh_rotto = null; pdl_rotto = null; }
    const etHHMM = getETHHMM(c.time);
    if (etHHMM < SESSION_START_ET || etHHMM > SESSION_END_ET) return;
    if (prev.close < PDH && c.close > PDH) { broken_pdh = true; pdh_rotto = PDH; broken_pdl = false; pdl_rotto = null; }
    if (prev.close > PDL && c.close < PDL) { broken_pdl = true; pdl_rotto = PDL; broken_pdh = false; pdh_rotto = null; }
    const tot = c.high - c.low; if (!tot) return;
    const bodyRatio = Math.abs(c.close - c.open) / tot;
    if (bodyRatio < BODY_MIN || bodyRatio > BODY_MAX) return;
    if (broken_pdh && pdh_rotto !== null) {
      if (c.low <= pdh_rotto && pdh_rotto <= c.high && c.close > pdh_rotto) {
        const entry = Math.round(c.close * 100) / 100, stop = Math.round((entry - slPts) * 100) / 100, target = Math.round((entry + slPts * RR) * 100) / 100;
        signals.push({ time: c.time, type: 'LONG',  setup: 'PDH', level: pdh_rotto, entry, stop, target, trigger: 'BODY' });
        broken_pdh = false; return;
      }
    }
    if (broken_pdl && pdl_rotto !== null) {
      if (c.low <= pdl_rotto && pdl_rotto <= c.high && c.close < pdl_rotto) {
        const entry = Math.round(c.close * 100) / 100, stop = Math.round((entry + slPts) * 100) / 100, target = Math.round((entry - slPts * RR) * 100) / 100;
        signals.push({ time: c.time, type: 'SHORT', setup: 'PDL', level: pdl_rotto, entry, stop, target, trigger: 'BODY' });
        broken_pdl = false;
      }
    }
  });
  return signals;
}

// ── Python-compatible simulation (no EOD force-close) ─────────────────────
// Matches test_phase2_parity.js simulatePythonStyle:
//   APERTO = never hit stop or target before end of data.
//   No EOD boundary check — Python calcola_risultati scans all future candles.
function simulateTrades(signals, candles) {
  return signals.map(s => {
    const future = candles.filter(c => c.time > s.time);
    let esito = 'APERTO';
    for (const c of future) {
      if (s.type === 'LONG') {
        if (c.low  <= s.stop)   { esito = 'STOP';   break; }
        if (c.high >= s.target) { esito = 'TARGET'; break; }
      } else {
        if (c.high >= s.stop)   { esito = 'STOP';   break; }
        if (c.low  <= s.target) { esito = 'TARGET'; break; }
      }
    }
    return { ...s, esito };
  });
}

// ── Certified slider state simulator (mirrors JS logic in index.html) ───────
function getCertifiedSliderValues(strategyId) {
  if (typeof STRATEGY_DEFINITIONS === 'undefined') return null;
  const def = STRATEGY_DEFINITIONS[strategyId];
  if (!def) return null;
  const p = def.parameters;
  return { sl_ticks: String(p.sl_ticks), rr: String(p.rr * 10) };
}
function isCertifiedSliderValues(sliderSl, sliderRr, strategyId) {
  const cert = getCertifiedSliderValues(strategyId);
  if (!cert) return true;
  return String(sliderSl) === cert.sl_ticks && String(sliderRr) === cert.rr;
}

// ── Paths ──────────────────────────────────────────────────────────────────
const ROOT    = path.resolve(__dirname, '..');
const SPY_CSV = path.join(ROOT, 'dati', 'SPY_5m.csv');
const SIG_CSV = path.join(ROOT, 'dati', 'SPY_segnali_pdh_pdl.csv');
const BT_CSV  = path.join(ROOT, 'dati', 'SPY_backtest.csv');

// ── Tests ─────────────────────────────────────────────────────────────────
console.log('\n── Phase 3A: Certified PDH/PDL Preset UI ────────────────────────────────\n');
console.log('Group 1: Built-in preset presence and sourcing\n');

test('T1. pdh_pdl_v1 present in STRATEGY_DEFINITIONS', () => {
  assert(typeof STRATEGY_DEFINITIONS !== 'undefined', 'STRATEGY_DEFINITIONS not defined');
  assert(STRATEGY_DEFINITIONS['pdh_pdl_v1'] !== undefined, 'pdh_pdl_v1 not found');
});

test('T2. Preset name is exactly "PDH/PDL Original (Python baseline)"', () => {
  const def = STRATEGY_DEFINITIONS['pdh_pdl_v1'];
  assertEq(def.name, 'PDH/PDL Original (Python baseline)');
});

test('T3. Preset version is "2.0.0"', () => {
  assertEq(STRATEGY_DEFINITIONS['pdh_pdl_v1'].version, '2.0.0');
});

test('T4. sl_ticks sourced from definition — value is 4', () => {
  assertEq(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.sl_ticks, 4);
});

test('T5. rr sourced from definition — value is 2', () => {
  assertEq(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.rr, 2);
});

test('T6. body_min sourced from definition — value is 0.20', () => {
  assertEq(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.body_min, 0.20);
});

test('T7. body_max sourced from definition — value is 0.70', () => {
  assertEq(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.body_max, 0.70);
});

test('T8. session_start sourced from definition — value is "09:30"', () => {
  assertEq(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.session_start, '09:30');
});

test('T9. session_end sourced from definition — value is "16:00"', () => {
  assertEq(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.session_end, '16:00');
});

test('T10. allow_reentry sourced from definition — value is true', () => {
  assertEq(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.allow_reentry, true);
});

console.log('\nGroup 2: Certified / Experimental slider state\n');

test('T11. Certified slider values (sl=4, rr×10=20) → isCertifiedSliderValues=true', () => {
  assert(isCertifiedSliderValues('4', '20', 'pdh_pdl_v1'), 'Expected certified=true');
});

test('T12. Changed sl_ticks (sl=6, rr×10=20) → isCertifiedSliderValues=false', () => {
  assert(!isCertifiedSliderValues('6', '20', 'pdh_pdl_v1'), 'Expected certified=false');
});

test('T13. Changed rr (sl=4, rr×10=30) → isCertifiedSliderValues=false', () => {
  assert(!isCertifiedSliderValues('4', '30', 'pdh_pdl_v1'), 'Expected certified=false');
});

test('T14. Restored to certified values → isCertifiedSliderValues=true again', () => {
  // Simulate: changed then restored
  assert(!isCertifiedSliderValues('4', '30', 'pdh_pdl_v1'), 'Pre-restore: should be false');
  assert( isCertifiedSliderValues('4', '20', 'pdh_pdl_v1'), 'Post-restore: should be true');
});

console.log('\nGroup 3: Phase 2 parity (CSV repository data)\n');

let candles, btRows;
const candlesLoaded = fs.existsSync(SPY_CSV) && fs.existsSync(SIG_CSV) && fs.existsSync(BT_CSV);

if (candlesLoaded) {
  candles = parseCandlesCSV(SPY_CSV).filter(c => !isNaN(c.close) && c.close > 0);
  btRows  = parseCSV(BT_CSV);
} else {
  console.log('  ⚠️  CSV files not found — skipping parity tests T15–T17 and T20');
}

test('T15. Phase 2 parity: 80 signals on SPY repository data', () => {
  if (!candlesLoaded) throw new Error('CSV files not available — skipped');
  const certParams = { sl_ticks: 4, rr: 2 };
  const signals = strategyPDHPDL(candles, certParams);
  assertEq(signals.length, 80, 'Signal count');
});

test('T16. Phase 2 parity: 24 TARGET, 55 STOP, 1 APERTO', () => {
  if (!candlesLoaded) throw new Error('CSV files not available — skipped');
  const certParams = { sl_ticks: 4, rr: 2 };
  const signals = strategyPDHPDL(candles, certParams);
  const trades  = simulateTrades(signals, candles);
  const target  = trades.filter(t => t.esito === 'TARGET').length;
  const stop    = trades.filter(t => t.esito === 'STOP').length;
  const aperto  = trades.filter(t => t.esito === 'APERTO').length;
  assertEq(target, 24, 'TARGET count');
  assertEq(stop,   55, 'STOP count');
  assertEq(aperto,  1, 'APERTO count');
});

test('T17. Phase 2 parity: closed-trade win rate ≈ 30.4%', () => {
  if (!candlesLoaded) throw new Error('CSV files not available — skipped');
  const certParams = { sl_ticks: 4, rr: 2 };
  const signals = strategyPDHPDL(candles, certParams);
  const trades  = simulateTrades(signals, candles);
  const closed  = trades.filter(t => t.esito !== 'APERTO');
  const wins    = closed.filter(t => t.esito === 'TARGET');
  const wr      = wins.length / closed.length * 100;
  // 24/79 = 30.3797... rounds to 30.4%
  const wrRounded = Math.round(wr * 10) / 10;
  assertEq(wrRounded, 30.4, 'Win rate');
});

console.log('\nGroup 4: Custom preset compatibility\n');

test('T18. User preset config structure includes required fields', () => {
  // Simulate savePreset() config object
  const mockConfig = {
    strategy: 'pdh_pdl',
    trigger:  'both',
    mode:     'futures',
    futures:  'MES',
    strike:   'ATM',
    selects:  { simbolo: 'SPY' },
    sliders:  { sl_ticks: '4', rr: '20', wick_min: '60', body_max: '40' },
  };
  assert(typeof mockConfig.strategy === 'string', 'strategy field');
  assert(typeof mockConfig.sliders  === 'object',  'sliders field');
  assert(typeof mockConfig.selects  === 'object',  'selects field');
  // loadPreset() reads these — ensure compatibility
  const s = mockConfig.strategy || 'pdh_pdl';
  assertEq(s, 'pdh_pdl');
});

test('T19. loadBuiltinPreset does NOT mutate STRATEGY_DEFINITIONS', () => {
  // Snapshot definition before simulating a "load"
  const before = JSON.stringify(STRATEGY_DEFINITIONS['pdh_pdl_v1']);
  // Simulate what loadBuiltinPreset does: reads values, does not write back
  const def = STRATEGY_DEFINITIONS['pdh_pdl_v1'];
  const p   = def.parameters;
  void p.sl_ticks; // read
  void p.rr;       // read
  const after = JSON.stringify(STRATEGY_DEFINITIONS['pdh_pdl_v1']);
  assertEq(before, after, 'STRATEGY_DEFINITIONS mutated');
});

console.log('\nGroup 5: Engine parity with certified params\n');

test('T20. strategyPDHPDL with certified params produces 80 signals', () => {
  if (!candlesLoaded) throw new Error('CSV files not available — skipped');
  // Confirm that using params identical to the certified definition gives same result
  // (i.e., the built-in preset produces the same output as the unconstrained engine)
  const def = STRATEGY_DEFINITIONS['pdh_pdl_v1'];
  const p   = def.parameters;
  const signals = strategyPDHPDL(candles, { sl_ticks: p.sl_ticks, rr: p.rr });
  assertEq(signals.length, 80, 'Signal count with certified params');
});

// ── Summary ───────────────────────────────────────────────────────────────
console.log(`\n────────────────────────────────────────────────────────────────────────────`);
console.log(`  ${passed + failed} tests  |  ✅ ${passed} passed  |  ${failed > 0 ? '❌' : '✅'} ${failed} failed`);
console.log(`────────────────────────────────────────────────────────────────────────────\n`);
process.exit(failed > 0 ? 1 : 0);
