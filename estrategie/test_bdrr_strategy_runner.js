/**
 * test_bdrr_strategy_runner.js
 *
 * Tests for estrategie/bdrr_strategy_runner.js — Multi-Session Strategy Runner v1.
 *
 * Required tests:
 *   T1.  No valid setup
 *   T2.  Stopped trade
 *   T3.  Target hit
 *   T4.  exit_target_r = 2/3/4 produce different outcomes
 *   T5.  Entry not triggered (BREAK_OF_SIGNAL_BAR)
 *   T6.  Ambiguous terminal bar
 *   T7.  Open trade
 *   T8.  Chronological ordering
 *   T9.  Failed session isolation
 *   T10. Invalid config rejection
 *   T11. Input immutability
 *   T12. Output immutability
 *   T13. Unique IDs
 *   T14. SPY 2026-05-26: VALID, STOPPED, stop ticks = 75036
 *
 * Batch validation:
 *   T15. SPY 60-session batch with oracle count verification
 *   T16. QQQ 60-session batch with oracle count verification
 *
 * Run: node estrategie/test_bdrr_strategy_runner.js
 */

'use strict';

const fs   = require('fs');
const path = require('path');

const {
  runBdrrStrategy,
  parseCandlesFromCSV,
  splitIntoSessions,
  OUTCOME
} = require('./bdrr_strategy_runner.js');

// ── Test harness ────────────────────────────────────────────────────────────

let checks   = 0;
let failures = [];

function check(cond, msg) {
  checks++;
  if (!cond) failures.push(msg);
}

// ── Frozen preset and config ────────────────────────────────────────────────

const TICK_SIZE = 0.01;

const FROZEN_PRESET = {
  preset_id:                   'bdrr_v1_initial',
  timeframe_minutes:           5,
  timezone:                    'America/New_York',
  session_open:                '09:30',
  orb_start:                   'session_open',
  orb_duration_minutes:        5,
  level_source:                'ORB_HIGH',
  direction:                   'LONG',
  entry_model:                 'CONFIRMATION_CLOSE',
  entry_buffer_ticks:          0,
  stop_buffer_ticks:           0,
  min_displacement_ticks:      null,
  min_penetration_ticks:       null,
  min_close_beyond_level_ticks: 1
};

const BASE_CONFIG = {
  tick_size:       TICK_SIZE,
  engine_version:  'bdrr_v1.0',
  exit_target_r:   2
};

// ── Synthetic candle helpers ────────────────────────────────────────────────

function makeDate(dateStr, timeStr) {
  return new Date(dateStr + 'T' + timeStr + ':00-04:00');
}

function makeCandle(dateStr, timeStr, open, high, low, close) {
  return { time: makeDate(dateStr, timeStr), open, high, low, close };
}

// Build a session that will produce NO valid detection (break not found)
function buildNoBreakSession(dateStr) {
  return {
    symbol: 'TEST', date: dateStr, market_timezone: 'America/New_York',
    session_open_utc_ms: makeDate(dateStr, '09:30').getTime(),
    session_close_utc_ms: makeDate(dateStr, '16:00').getTime(),
    timeframe: '5m',
    candles: [
      makeCandle(dateStr, '09:30', 99.50, 100.00, 99.30, 99.80),
      makeCandle(dateStr, '09:35', 99.70, 99.90, 99.60, 99.75),
      makeCandle(dateStr, '09:40', 99.75, 99.95, 99.65, 99.70),
      makeCandle(dateStr, '09:45', 99.70, 99.85, 99.55, 99.60),
      makeCandle(dateStr, '09:50', 99.55, 99.75, 99.40, 99.50),
    ]
  };
}

// Build a VALID detection → STOPPED
// level=100.00, confirmation candle: O=100.10 H=100.50 L=99.70 C=100.40
// rej_wick = (min(100.10,100.40)-99.70)/(100.50-99.70) = 0.40/0.80 = 0.50 >= 0.47 ✓
// body = |100.40-100.10|/0.80 = 0.375 <= 0.40 ✓
// close_loc = (100.40-99.70)/0.80 = 0.875 >= 0.80 ✓
// entry = 100.40 (CC, 0 buffer), stop = 99.70, risk = 0.70 = 70 ticks
// 2R = 101.80, 3R = 102.50, 4R = 103.20
function buildStoppedSession(dateStr) {
  return {
    symbol: 'TEST', date: dateStr, market_timezone: 'America/New_York',
    session_open_utc_ms: makeDate(dateStr, '09:30').getTime(),
    session_close_utc_ms: makeDate(dateStr, '16:00').getTime(),
    timeframe: '5m',
    candles: [
      makeCandle(dateStr, '09:30', 99.50, 100.00, 99.30, 99.80),
      makeCandle(dateStr, '09:35', 99.90, 100.50, 99.80, 100.20),
      makeCandle(dateStr, '09:40', 100.25, 100.60, 100.10, 100.40),
      makeCandle(dateStr, '09:45', 100.10, 100.50, 99.70, 100.40),
      makeCandle(dateStr, '09:50', 100.30, 100.35, 99.60, 99.65),
    ]
  };
}

// Build a VALID detection → 2R target hit
function buildTargetHitSession(dateStr) {
  return {
    symbol: 'TEST', date: dateStr, market_timezone: 'America/New_York',
    session_open_utc_ms: makeDate(dateStr, '09:30').getTime(),
    session_close_utc_ms: makeDate(dateStr, '16:00').getTime(),
    timeframe: '5m',
    candles: [
      makeCandle(dateStr, '09:30', 99.50, 100.00, 99.30, 99.80),
      makeCandle(dateStr, '09:35', 99.90, 100.50, 99.80, 100.20),
      makeCandle(dateStr, '09:40', 100.25, 100.60, 100.10, 100.40),
      makeCandle(dateStr, '09:45', 100.10, 100.50, 99.70, 100.40),
      makeCandle(dateStr, '09:50', 100.50, 101.00, 100.30, 100.90),
      makeCandle(dateStr, '09:55', 100.90, 101.50, 100.80, 101.30),
      makeCandle(dateStr, '10:00', 101.30, 101.90, 101.20, 101.70),
    ]
  };
}

// Build session with multi-target outcomes: 2R and 3R hit, then stop before 4R
function buildMultiTargetSession(dateStr) {
  return {
    symbol: 'TEST', date: dateStr, market_timezone: 'America/New_York',
    session_open_utc_ms: makeDate(dateStr, '09:30').getTime(),
    session_close_utc_ms: makeDate(dateStr, '16:00').getTime(),
    timeframe: '5m',
    candles: [
      makeCandle(dateStr, '09:30', 99.50, 100.00, 99.30, 99.80),
      makeCandle(dateStr, '09:35', 99.90, 100.50, 99.80, 100.20),
      makeCandle(dateStr, '09:40', 100.25, 100.60, 100.10, 100.40),
      makeCandle(dateStr, '09:45', 100.10, 100.50, 99.70, 100.40),
      makeCandle(dateStr, '09:50', 100.50, 101.00, 100.30, 100.90),
      makeCandle(dateStr, '09:55', 100.90, 101.90, 100.80, 101.50),
      makeCandle(dateStr, '10:00', 101.50, 102.60, 101.40, 102.40),
      makeCandle(dateStr, '10:05', 102.20, 102.30, 99.50, 99.55),
    ]
  };
}

// Build session for BREAK_OF_SIGNAL_BAR entry not triggered
function buildEntryNotTriggeredSession(dateStr) {
  return {
    symbol: 'TEST', date: dateStr, market_timezone: 'America/New_York',
    session_open_utc_ms: makeDate(dateStr, '09:30').getTime(),
    session_close_utc_ms: makeDate(dateStr, '16:00').getTime(),
    timeframe: '5m',
    candles: [
      makeCandle(dateStr, '09:30', 99.50, 100.00, 99.30, 99.80),
      makeCandle(dateStr, '09:35', 99.90, 100.50, 99.80, 100.20),
      makeCandle(dateStr, '09:40', 100.25, 100.60, 100.10, 100.40),
      makeCandle(dateStr, '09:45', 100.10, 100.50, 99.70, 100.40),
      // BOSB entry = high + 0 = 100.50. Post-conf bars never reach 100.50.
      makeCandle(dateStr, '09:50', 100.30, 100.45, 100.10, 100.20),
      makeCandle(dateStr, '09:55', 100.15, 100.40, 100.00, 100.10),
    ]
  };
}

// Build session with same-bar ambiguity (stop + 2R on same bar)
function buildAmbiguousSession(dateStr) {
  return {
    symbol: 'TEST', date: dateStr, market_timezone: 'America/New_York',
    session_open_utc_ms: makeDate(dateStr, '09:30').getTime(),
    session_close_utc_ms: makeDate(dateStr, '16:00').getTime(),
    timeframe: '5m',
    candles: [
      makeCandle(dateStr, '09:30', 99.50, 100.00, 99.30, 99.80),
      makeCandle(dateStr, '09:35', 99.90, 100.50, 99.80, 100.20),
      makeCandle(dateStr, '09:40', 100.25, 100.60, 100.10, 100.40),
      makeCandle(dateStr, '09:45', 100.10, 100.50, 99.70, 100.40),
      // Same bar: high=101.90 >= 2R(101.80) AND low=99.50 <= stop(99.70)
      makeCandle(dateStr, '09:50', 100.50, 101.90, 99.50, 101.00),
    ]
  };
}

// Build session where trade is open at session end
function buildOpenAtEndSession(dateStr) {
  return {
    symbol: 'TEST', date: dateStr, market_timezone: 'America/New_York',
    session_open_utc_ms: makeDate(dateStr, '09:30').getTime(),
    session_close_utc_ms: makeDate(dateStr, '16:00').getTime(),
    timeframe: '5m',
    candles: [
      makeCandle(dateStr, '09:30', 99.50, 100.00, 99.30, 99.80),
      makeCandle(dateStr, '09:35', 99.90, 100.50, 99.80, 100.20),
      makeCandle(dateStr, '09:40', 100.25, 100.60, 100.10, 100.40),
      makeCandle(dateStr, '09:45', 100.10, 100.50, 99.70, 100.40),
      // Price stays between stop and target
      makeCandle(dateStr, '09:50', 100.50, 101.00, 100.00, 100.80),
      makeCandle(dateStr, '09:55', 100.80, 101.50, 100.20, 101.30),
    ]
  };
}

// ── Load real sessions ──────────────────────────────────────────────────────

function loadRealSessions(symbol) {
  const csvPath = path.join(__dirname, '..', 'dati', symbol + '_5m.csv');
  const csv = fs.readFileSync(csvPath, 'utf8');
  const allCandles = parseCandlesFromCSV(csv);
  const groups = splitIntoSessions(allCandles, 'America/New_York');
  return groups.map(g => ({
    symbol, date: g.date, market_timezone: 'America/New_York',
    session_open_utc_ms: g.candles[0].time.getTime(),
    session_close_utc_ms: g.candles[g.candles.length - 1].time.getTime(),
    timeframe: '5m', candles: g.candles
  }));
}

// ═══════════════════════════════════════════════════════════════════════════
// TESTS
// ═══════════════════════════════════════════════════════════════════════════

console.log('\n=== BDRR Strategy Runner Tests ===\n');

// ── T1: No valid setup ──────────────────────────────────────────────────

console.log('T1: No valid setup');
{
  const results = runBdrrStrategy([buildNoBreakSession('2026-07-01')], FROZEN_PRESET, BASE_CONFIG);
  check(results.length === 1, 'T1: one result');
  check(results[0].outcome === OUTCOME.NO_VALID_SETUP,
    'T1: outcome = NO_VALID_SETUP, got ' + results[0].outcome);
  check(results[0].detection_status === 'INVALID', 'T1: detection_status = INVALID');
  check(results[0].trade_plan === null, 'T1: trade_plan null');
  check(results[0].trade_outcome === null, 'T1: trade_outcome null');
}

// ── T2: Stopped trade ───────────────────────────────────────────────────

console.log('T2: Stopped trade');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  check(results[0].outcome === OUTCOME.STOPPED, 'T2: outcome = STOPPED, got ' + results[0].outcome);
  check(results[0].detection_status === 'VALID', 'T2: detection_status = VALID');
  check(results[0].realized_r === -1, 'T2: realized_r = -1');
  check(results[0].trade_plan !== null, 'T2: trade_plan populated');
  check(results[0].trade_outcome !== null, 'T2: trade_outcome populated');
  check(results[0].exit_price_ticks === results[0].stop_price_ticks,
    'T2: exit_price = stop_price for stopped trade');
}

// ── T3: Target hit ──────────────────────────────────────────────────────

console.log('T3: 2R target hit');
{
  const results = runBdrrStrategy([buildTargetHitSession('2026-07-03')], FROZEN_PRESET, BASE_CONFIG);
  check(results[0].outcome === OUTCOME.TARGET_HIT, 'T3: outcome = TARGET_HIT, got ' + results[0].outcome);
  check(results[0].realized_r === 2, 'T3: realized_r = 2');
  check(results[0].exit_price_ticks === results[0].r2_price_ticks,
    'T3: exit_price = r2_price for 2R hit');
}

// ── T4: exit_target_r = 2/3/4 produce different outcomes ────────────────

console.log('T4: Multi-target R different outcomes');
{
  const session = buildMultiTargetSession('2026-07-04');

  const r2 = runBdrrStrategy([session], FROZEN_PRESET, { ...BASE_CONFIG, exit_target_r: 2 });
  const r3 = runBdrrStrategy([session], FROZEN_PRESET, { ...BASE_CONFIG, exit_target_r: 3 });
  const r4 = runBdrrStrategy([session], FROZEN_PRESET, { ...BASE_CONFIG, exit_target_r: 4 });

  check(r2[0].outcome === OUTCOME.TARGET_HIT, 'T4: 2R = TARGET_HIT, got ' + r2[0].outcome);
  check(r2[0].realized_r === 2, 'T4: 2R realized_r = 2');

  check(r3[0].outcome === OUTCOME.TARGET_HIT, 'T4: 3R = TARGET_HIT, got ' + r3[0].outcome);
  check(r3[0].realized_r === 3, 'T4: 3R realized_r = 3');

  // 4R (103.20) is never reached; stop hit at 99.50
  check(r4[0].outcome === OUTCOME.STOPPED, 'T4: 4R = STOPPED, got ' + r4[0].outcome);
  check(r4[0].realized_r === -1, 'T4: 4R realized_r = -1');
}

// ── T5: Entry not triggered ─────────────────────────────────────────────

console.log('T5: Entry not triggered (BREAK_OF_SIGNAL_BAR)');
{
  const bosbPreset = { ...FROZEN_PRESET, entry_model: 'BREAK_OF_SIGNAL_BAR' };
  const results = runBdrrStrategy([buildEntryNotTriggeredSession('2026-07-05')], bosbPreset, BASE_CONFIG);
  check(results[0].outcome === OUTCOME.ENTRY_NOT_TRIGGERED,
    'T5: outcome = ENTRY_NOT_TRIGGERED, got ' + results[0].outcome);
  check(results[0].detection_status === 'VALID', 'T5: detection still VALID');
  check(results[0].trade_plan !== null, 'T5: trade_plan populated');
}

// ── T6: Ambiguous terminal bar ──────────────────────────────────────────

console.log('T6: Ambiguous terminal bar');
{
  const results = runBdrrStrategy([buildAmbiguousSession('2026-07-06')], FROZEN_PRESET, BASE_CONFIG);
  check(results[0].outcome === OUTCOME.AMBIGUOUS, 'T6: outcome = AMBIGUOUS, got ' + results[0].outcome);
  check(results[0].realized_r === null, 'T6: realized_r null for ambiguous');
}

// ── T7: Open trade ──────────────────────────────────────────────────────

console.log('T7: Open trade at session end');
{
  const results = runBdrrStrategy([buildOpenAtEndSession('2026-07-07')], FROZEN_PRESET, BASE_CONFIG);
  check(results[0].outcome === OUTCOME.OPEN, 'T7: outcome = OPEN, got ' + results[0].outcome);
  check(results[0].exit_timestamp === null, 'T7: exit_timestamp null');
  check(results[0].exit_price_ticks === null, 'T7: exit_price_ticks null');
  check(results[0].realized_r === null, 'T7: realized_r null');
}

// ── T8: Chronological ordering ──────────────────────────────────────────

console.log('T8: Chronological ordering');
{
  const results = runBdrrStrategy([
    buildNoBreakSession('2026-07-01'),
    buildStoppedSession('2026-07-02'),
    buildTargetHitSession('2026-07-03')
  ], FROZEN_PRESET, BASE_CONFIG);

  check(results.length === 3, 'T8: 3 results');
  check(results[0].session_date === '2026-07-01', 'T8: first = 2026-07-01');
  check(results[1].session_date === '2026-07-02', 'T8: second = 2026-07-02');
  check(results[2].session_date === '2026-07-03', 'T8: third = 2026-07-03');
  check(results[0].outcome === OUTCOME.NO_VALID_SETUP, 'T8: first = NO_VALID_SETUP');
  check(results[1].outcome === OUTCOME.STOPPED, 'T8: second = STOPPED');
  check(results[2].outcome === OUTCOME.TARGET_HIT, 'T8: third = TARGET_HIT');
}

// ── T9: Failed session isolation ────────────────────────────────────────

console.log('T9: Failed session isolation');
{
  const badSession = {
    symbol: 'TEST', date: '2026-07-01', market_timezone: 'America/New_York',
    session_open_utc_ms: 0, session_close_utc_ms: 0, timeframe: '5m', candles: []
  };
  const results = runBdrrStrategy([badSession, buildTargetHitSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  check(results.length === 2, 'T9: 2 results');
  check(results[0].outcome === OUTCOME.PIPELINE_FAILURE, 'T9: bad session = PIPELINE_FAILURE');
  check(results[1].outcome === OUTCOME.TARGET_HIT, 'T9: good session = TARGET_HIT');
}

// ── T10: Invalid config rejection ───────────────────────────────────────

console.log('T10: Invalid config rejection');
{
  let threw = false;
  try { runBdrrStrategy([], FROZEN_PRESET, { ...BASE_CONFIG, exit_target_r: 5 }); }
  catch (e) { threw = true; }
  check(threw, 'T10: exit_target_r=5 should throw');

  threw = false;
  try { runBdrrStrategy([], FROZEN_PRESET, { ...BASE_CONFIG, tick_size: 0 }); }
  catch (e) { threw = true; }
  check(threw, 'T10: tick_size=0 should throw');

  threw = false;
  try { runBdrrStrategy([], FROZEN_PRESET, { ...BASE_CONFIG, engine_version: '' }); }
  catch (e) { threw = true; }
  check(threw, 'T10: empty engine_version should throw');
}

// ── T11: Input immutability ─────────────────────────────────────────────

console.log('T11: Input immutability');
{
  const session = buildStoppedSession('2026-07-02');
  const candleCount = session.candles.length;
  const preset = { ...FROZEN_PRESET };
  const config = { ...BASE_CONFIG };
  const presetStr = JSON.stringify(preset);
  const configStr = JSON.stringify(config);

  runBdrrStrategy([session], preset, config);

  check(JSON.stringify(preset) === presetStr, 'T11: preset not mutated');
  check(JSON.stringify(config) === configStr, 'T11: config not mutated');
  check(session.candles.length === candleCount, 'T11: candles not mutated');
}

// ── T12: Output immutability ────────────────────────────────────────────

console.log('T12: Output immutability');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  check(Object.isFrozen(results), 'T12: results array frozen');
  check(Object.isFrozen(results[0]), 'T12: result record frozen');
  let threw = false;
  try { results[0].outcome = 'MODIFIED'; } catch (e) { threw = true; }
  check(results[0].outcome === OUTCOME.STOPPED, 'T12: result not modifiable');
}

// ── T13: Unique IDs ─────────────────────────────────────────────────────

console.log('T13: Unique IDs');
{
  const results = runBdrrStrategy([
    buildStoppedSession('2026-07-02'),
    buildTargetHitSession('2026-07-03')
  ], FROZEN_PRESET, BASE_CONFIG);

  const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  const ids = new Set();

  for (const r of results) {
    check(uuidRe.test(r.run_record_id), 'T13: valid UUID v4 run_record_id');
    check(!ids.has(r.run_record_id), 'T13: no duplicate run_record_id');
    ids.add(r.run_record_id);
    if (r.detection_result_id) {
      check(!ids.has(r.detection_result_id), 'T13: no duplicate detection_result_id');
      ids.add(r.detection_result_id);
    }
    if (r.candidate_id) {
      check(!ids.has(r.candidate_id), 'T13: no duplicate candidate_id');
      ids.add(r.candidate_id);
    }
  }
}

// ── T14: SPY 2026-05-26 frozen result ───────────────────────────────────

console.log('T14: SPY 2026-05-26 frozen result');
{
  const spySessions = loadRealSessions('SPY');
  const may26 = spySessions.find(s => s.date === '2026-05-26');
  check(may26 != null, 'T14: SPY 2026-05-26 exists');

  if (may26) {
    const results = runBdrrStrategy([may26], FROZEN_PRESET, BASE_CONFIG);
    check(results.length === 1, 'T14: one result');
    check(results[0].detection_status === 'VALID', 'T14: VALID');
    check(results[0].outcome === OUTCOME.STOPPED, 'T14: STOPPED, got ' + results[0].outcome);
    check(results[0].stop_price_ticks === 75036, 'T14: stop ticks = 75036, got ' + results[0].stop_price_ticks);
    check(results[0].exit_price_ticks === 75036, 'T14: exit ticks = 75036');
    check(results[0].realized_r === -1, 'T14: realized_r = -1');
    check(results[0].detection_result != null, 'T14: detection_result embedded');
    check(results[0].trade_plan != null, 'T14: trade_plan embedded');
    check(results[0].trade_outcome != null, 'T14: trade_outcome embedded');
  }
}

// ── T15: SPY batch — oracle count verification ──────────────────────────

console.log('\nT15: SPY batch');
{
  const spySessions = loadRealSessions('SPY');
  console.log('  Sessions: ' + spySessions.length);
  const results = runBdrrStrategy(spySessions, FROZEN_PRESET, BASE_CONFIG);
  check(results.length === spySessions.length, 'T15: result count matches session count');

  const counts = {};
  results.forEach(r => counts[r.outcome] = (counts[r.outcome] || 0) + 1);
  console.log('  Outcome breakdown:');
  Object.entries(counts).sort().forEach(([k, v]) => console.log('    ' + k + ': ' + v));

  const validResults = results.filter(r => r.detection_status === 'VALID');
  const validDates = validResults.map(r => r.session_date).sort();
  console.log('  VALID detections: ' + validResults.length);
  console.log('  VALID dates: ' + validDates.join(', '));

  // Frozen oracle: SPY has exactly 3 VALID setups
  const ORACLE_SPY_VALID = 3;
  const ORACLE_SPY_DATES = new Set(['2026-05-26', '2026-06-08', '2026-07-06']);

  // Verify oracle dates are present
  for (const d of ORACLE_SPY_DATES) {
    check(validDates.includes(d), 'T15: oracle date ' + d + ' must be VALID');
  }

  // Strict count check
  if (validResults.length !== ORACLE_SPY_VALID) {
    console.log('\n  ╔══════════════════════════════════════════════════════════════╗');
    console.log('  ║ DISCREPANCY: SPY VALID count = ' + validResults.length + ', frozen oracle = ' + ORACLE_SPY_VALID + '          ║');
    console.log('  ╚══════════════════════════════════════════════════════════════╝');
    console.log('  Additional detections not in oracle:');
    for (const r of validResults) {
      if (!ORACLE_SPY_DATES.has(r.session_date)) {
        console.log('    ' + r.session_date + ' | outcome=' + r.outcome +
          ' | entry=' + r.entry_price_ticks + ' | stop=' + r.stop_price_ticks +
          ' | exit=' + r.exit_price_ticks);
      }
    }
  }
  check(validResults.length === ORACLE_SPY_VALID,
    'T15: SPY VALID count must be ' + ORACLE_SPY_VALID + ', got ' + validResults.length);
}

// ── T16: QQQ batch — oracle count verification ──────────────────────────

console.log('\nT16: QQQ batch');
{
  const qqqSessions = loadRealSessions('QQQ');
  console.log('  Sessions: ' + qqqSessions.length);
  const results = runBdrrStrategy(qqqSessions, FROZEN_PRESET, BASE_CONFIG);
  check(results.length === qqqSessions.length, 'T16: result count matches session count');

  const counts = {};
  results.forEach(r => counts[r.outcome] = (counts[r.outcome] || 0) + 1);
  console.log('  Outcome breakdown:');
  Object.entries(counts).sort().forEach(([k, v]) => console.log('    ' + k + ': ' + v));

  const validResults = results.filter(r => r.detection_status === 'VALID');
  const validDates = validResults.map(r => r.session_date).sort();
  console.log('  VALID detections: ' + validResults.length);
  console.log('  VALID dates: ' + validDates.join(', '));

  // Frozen oracle: QQQ has exactly 4 VALID setups
  const ORACLE_QQQ_VALID = 4;
  const ORACLE_QQQ_DATES = new Set(['2026-04-29', '2026-05-06', '2026-05-13', '2026-07-14']);

  // Verify oracle dates are present
  for (const d of ORACLE_QQQ_DATES) {
    check(validDates.includes(d), 'T16: oracle date ' + d + ' must be VALID');
  }

  // Strict count check
  if (validResults.length !== ORACLE_QQQ_VALID) {
    console.log('\n  ╔══════════════════════════════════════════════════════════════╗');
    console.log('  ║ DISCREPANCY: QQQ VALID count = ' + validResults.length + ', frozen oracle = ' + ORACLE_QQQ_VALID + '          ║');
    console.log('  ╚══════════════════════════════════════════════════════════════╝');
    console.log('  Additional detections not in oracle:');
    for (const r of validResults) {
      if (!ORACLE_QQQ_DATES.has(r.session_date)) {
        console.log('    ' + r.session_date + ' | outcome=' + r.outcome +
          ' | entry=' + r.entry_price_ticks + ' | stop=' + r.stop_price_ticks +
          ' | exit=' + r.exit_price_ticks);
      }
    }
  }
  check(validResults.length === ORACLE_QQQ_VALID,
    'T16: QQQ VALID count must be ' + ORACLE_QQQ_VALID + ', got ' + validResults.length);
}

// ── T17: Result record schema completeness ──────────────────────────────

console.log('\nT17: Result record schema');
{
  const FIELDS = [
    'run_record_id', 'symbol', 'session_date', 'preset_id', 'exit_target_r',
    'detection_status', 'failure_stage', 'failed_rules', 'detection_result_id',
    'candidate_id', 'confirmation_timestamp', 'entry_timestamp',
    'first_evaluation_timestamp', 'entry_price_ticks', 'stop_price_ticks',
    'r2_price_ticks', 'r3_price_ticks', 'r4_price_ticks',
    'outcome', 'realized_r', 'highest_target_achieved',
    'exit_timestamp', 'exit_price_ticks',
    'detection_result', 'trade_plan', 'trade_outcome'
  ];
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  for (const f of FIELDS) {
    check(Object.prototype.hasOwnProperty.call(results[0], f),
      'T17: missing field "' + f + '"');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// SUMMARY
// ═══════════════════════════════════════════════════════════════════════════

console.log('\n=== Strategy Runner: ' + checks + ' checks, ' + failures.length + ' failures ===');
if (failures.length > 0) {
  console.log('\nFailed checks:');
  failures.forEach((f, i) => console.log('  ' + (i + 1) + '. ' + f));
  process.exit(1);
} else {
  console.log('All checks passed.\n');
}
