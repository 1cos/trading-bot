/**
 * estrategie/test_bdrr_trade_dataset.js
 *
 * Tests for estrategie/bdrr_trade_dataset.js — BDRR Trade Dataset v1.
 *
 * Core tests (original):
 *   TD1.  Empty dataset
 *   TD2.  Single trade (STOPPED)
 *   TD3.  Multiple records — records vs trades separation
 *   TD4.  Chronological ordering preserved
 *   TD5.  Duplicate run_record_id rejected
 *   TD5b. Duplicate candidate_id rejected
 *   TD6.  Immutable output (dataset / metadata / records / trades)
 *   TD7.  Metadata fields completeness
 *   TD8.  Schema validation (all field-level rules)
 *   TD9.  Invalid input rejection (type errors)
 *  TD10.  Integration — SPY batch
 *  TD10b. Integration — QQQ batch
 *  TD11.  schema_version constant
 *  TD12.  date_range correctness
 *  TD13.  trade_count === trades.length
 *  TD15.  Object identity preserved in records and trades
 *  TD16.  Multiple null candidate_ids (no false-positive dup)
 *  TD17.  Out-of-order records rejected
 *  TD18.  PIPELINE_FAILURE rejected (runner integration issue — engine_version unavailable)
 *  TD19.  Invalid outcome string rejected
 *  TD20.  Invalid detection_status rejected
 *
 * Correction 1 — Deterministic ID (complete content):
 *  TD21.  Same complete input → same dataset_id
 *  TD22.  Changing only outcome (same IDs) → different dataset_id
 *  TD22b. Changing nested field trade_plan.stop_price.ticks (same IDs) → different dataset_id
 *  TD22c. Different key insertion order, same semantic content → same dataset_id
 *  TD22d. generated_at does not affect dataset_id
 *  TD23.  Out-of-order input rejected — cannot silently produce a dataset
 *
 * Correction 2 — records vs trades (unchanged, verified):
 *  TD24.  NO_VALID_SETUP in records but NOT in trades
 *
 * Correction 3 — Homogeneous run:
 *  TD25.  Mixed symbol rejected
 *  TD26.  Mixed preset_id rejected
 *  TD27.  Mixed exit_target_r rejected
 *  TD28.  Mixed engine_version rejected
 *
 * Engine version — no exemptions (C2):
 *  TD29.  Single record with null detection_result → rejected (missing engine_version)
 *  TD30.  PIPELINE_FAILURE record (detection_result=null) → rejected (missing engine_version)
 *  TD31.  Mixed engine versions (one valid, one different) → rejected
 *  TD32.  Homogeneous engine versions → accepted
 *
 * Run: node estrategie/test_bdrr_trade_dataset.js
 */

'use strict';

const { buildTradeDataset, DATASET_SCHEMA_VERSION } = require('./bdrr_trade_dataset.js');
const {
  runBdrrStrategy,
  parseCandlesFromCSV,
  splitIntoSessions,
  OUTCOME
} = require('./bdrr_strategy_runner.js');
const fs   = require('fs');
const path = require('path');

// ── Test harness ──────────────────────────────────────────────────────────────

let checks   = 0;
let failures = [];

function check(cond, msg) {
  checks++;
  if (!cond) failures.push(msg);
}

function checkThrows(fn, expectedErrorType, tag) {
  checks++;
  try {
    fn();
    failures.push(tag + ': expected to throw ' + expectedErrorType + ' but did not throw');
  } catch (e) {
    const Ctor =
      expectedErrorType === 'TypeError'  ? TypeError  :
      expectedErrorType === 'RangeError' ? RangeError :
      Error;
    if (!(e instanceof Ctor)) {
      failures.push(tag + ': expected ' + expectedErrorType +
                    ' but got ' + e.constructor.name + ': ' + e.message);
    }
  }
}

// ── Frozen preset and config ──────────────────────────────────────────────────

const TICK_SIZE = 0.01;

const FROZEN_PRESET = Object.freeze({
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
});

const BASE_CONFIG = Object.freeze({
  tick_size:       TICK_SIZE,
  engine_version:  'bdrr_v1.0',
  exit_target_r:   2
});

// ── Synthetic session helpers ─────────────────────────────────────────────────

function makeDate(dateStr, timeStr) {
  return new Date(dateStr + 'T' + timeStr + ':00-04:00');
}

function makeCandle(dateStr, timeStr, open, high, low, close) {
  return { time: makeDate(dateStr, timeStr), open, high, low, close };
}

function buildNoBreakSession(dateStr, symbol) {
  return {
    symbol: symbol || 'TEST', date: dateStr, market_timezone: 'America/New_York',
    session_open_utc_ms:  makeDate(dateStr, '09:30').getTime(),
    session_close_utc_ms: makeDate(dateStr, '16:00').getTime(),
    timeframe: '5m',
    candles: [
      makeCandle(dateStr, '09:30', 99.50, 100.00, 99.30, 99.80),
      makeCandle(dateStr, '09:35', 99.70, 99.90, 99.60, 99.75),
      makeCandle(dateStr, '09:40', 99.75, 99.95, 99.65, 99.70),
      makeCandle(dateStr, '09:45', 99.70, 99.85, 99.55, 99.60),
      makeCandle(dateStr, '09:50', 99.55, 99.75, 99.40, 99.50)
    ]
  };
}

function buildStoppedSession(dateStr, symbol) {
  return {
    symbol: symbol || 'TEST', date: dateStr, market_timezone: 'America/New_York',
    session_open_utc_ms:  makeDate(dateStr, '09:30').getTime(),
    session_close_utc_ms: makeDate(dateStr, '16:00').getTime(),
    timeframe: '5m',
    candles: [
      makeCandle(dateStr, '09:30', 99.50, 100.00, 99.30, 99.80),
      makeCandle(dateStr, '09:35', 99.90, 100.50, 99.80, 100.20),
      makeCandle(dateStr, '09:40', 100.25, 100.60, 100.10, 100.40),
      makeCandle(dateStr, '09:45', 100.10, 100.50, 99.70, 100.40),
      makeCandle(dateStr, '09:50', 100.30, 100.35, 99.60, 99.65)
    ]
  };
}

function buildTargetHitSession(dateStr, symbol) {
  return {
    symbol: symbol || 'TEST', date: dateStr, market_timezone: 'America/New_York',
    session_open_utc_ms:  makeDate(dateStr, '09:30').getTime(),
    session_close_utc_ms: makeDate(dateStr, '16:00').getTime(),
    timeframe: '5m',
    candles: [
      makeCandle(dateStr, '09:30', 99.50, 100.00, 99.30, 99.80),
      makeCandle(dateStr, '09:35', 99.90, 100.50, 99.80, 100.20),
      makeCandle(dateStr, '09:40', 100.25, 100.60, 100.10, 100.40),
      makeCandle(dateStr, '09:45', 100.10, 100.50, 99.70, 100.40),
      makeCandle(dateStr, '09:50', 100.50, 101.00, 100.30, 100.90),
      makeCandle(dateStr, '09:55', 100.90, 101.50, 100.80, 101.30),
      makeCandle(dateStr, '10:00', 101.30, 101.90, 101.20, 101.70)
    ]
  };
}

// ── Real CSV sessions ─────────────────────────────────────────────────────────

function loadRealSessions(symbol) {
  const csvPath = path.join(__dirname, '..', 'dati', symbol + '_5m.csv');
  const csv     = fs.readFileSync(csvPath, 'utf8');
  const candles = parseCandlesFromCSV(csv);
  const sessions = splitIntoSessions(candles, 'America/New_York');
  return sessions.map(s => ({
    symbol,
    date:                 s.date,
    market_timezone:      'America/New_York',
    session_open_utc_ms:  new Date(s.date + 'T09:30:00-04:00').getTime(),
    session_close_utc_ms: new Date(s.date + 'T16:00:00-04:00').getTime(),
    timeframe:            '5m',
    candles:              s.candles
  }));
}

// ── Hex pattern ───────────────────────────────────────────────────────────────

const HEX64_RE = /^[0-9a-f]{64}$/;

// ═══════════════════════════════════════════════════════════════════════════════
// TD1: Empty dataset
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD1: Empty dataset');
{
  const ds = buildTradeDataset([]);

  check(ds.schema_version === DATASET_SCHEMA_VERSION, 'TD1: schema_version correct');
  check(typeof ds.metadata === 'object' && ds.metadata !== null, 'TD1: metadata is object');
  check(Array.isArray(ds.records), 'TD1: records is array');
  check(Array.isArray(ds.trades),  'TD1: trades is array');
  check(ds.records.length === 0, 'TD1: records.length === 0');
  check(ds.trades.length  === 0, 'TD1: trades.length === 0');
  check(ds.metadata.session_count  === 0,    'TD1: session_count === 0');
  check(ds.metadata.trade_count    === 0,    'TD1: trade_count === 0');
  check(ds.metadata.symbol         === null, 'TD1: symbol null');
  check(ds.metadata.preset_id      === null, 'TD1: preset_id null');
  check(ds.metadata.engine_version === null, 'TD1: engine_version null');
  check(ds.metadata.exit_target_r  === null, 'TD1: exit_target_r null');
  check(ds.metadata.date_range.first === null, 'TD1: date_range.first null');
  check(ds.metadata.date_range.last  === null, 'TD1: date_range.last null');
  check(HEX64_RE.test(ds.metadata.dataset_id), 'TD1: dataset_id is 64-char hex');
  check(typeof ds.metadata.generated_at === 'string', 'TD1: generated_at is string');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD2: Single trade (STOPPED)
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD2: Single trade (STOPPED)');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  const ds = buildTradeDataset(results);

  check(ds.records.length === 1, 'TD2: records.length === 1');
  check(ds.trades.length  === 1, 'TD2: trades.length === 1');
  check(ds.metadata.session_count === 1, 'TD2: session_count === 1');
  check(ds.metadata.trade_count   === 1, 'TD2: trade_count === 1');
  check(ds.records[0].outcome === OUTCOME.STOPPED, 'TD2: records[0].outcome preserved');
  check(ds.trades[0].outcome  === OUTCOME.STOPPED, 'TD2: trades[0].outcome preserved');
  check(ds.trades[0] === ds.records[0], 'TD2: same object reference in records and trades');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD3: Multiple records — records vs trades separation
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD3: Multiple records — records vs trades separation');
{
  const sessions = [
    buildNoBreakSession('2026-07-01'),
    buildStoppedSession('2026-07-02'),
    buildTargetHitSession('2026-07-03'),
    buildNoBreakSession('2026-07-07')
  ];
  const results = runBdrrStrategy(sessions, FROZEN_PRESET, BASE_CONFIG);
  const ds = buildTradeDataset(results);

  check(ds.records.length === 4, 'TD3: records.length === 4');
  check(ds.trades.length  === 2, 'TD3: trades.length === 2');
  check(ds.metadata.session_count === 4, 'TD3: session_count === 4');
  check(ds.metadata.trade_count   === 2, 'TD3: trade_count === 2');

  check(ds.records[0].outcome === OUTCOME.NO_VALID_SETUP, 'TD3: records[0] NO_VALID_SETUP');
  check(ds.records[1].outcome === OUTCOME.STOPPED,        'TD3: records[1] STOPPED');
  check(ds.records[2].outcome === OUTCOME.TARGET_HIT,     'TD3: records[2] TARGET_HIT');
  check(ds.records[3].outcome === OUTCOME.NO_VALID_SETUP, 'TD3: records[3] NO_VALID_SETUP');

  check(ds.trades[0].outcome === OUTCOME.STOPPED,    'TD3: trades[0] STOPPED');
  check(ds.trades[1].outcome === OUTCOME.TARGET_HIT, 'TD3: trades[1] TARGET_HIT');

  check(ds.trades[0] === ds.records[1], 'TD3: trades[0] === records[1]');
  check(ds.trades[1] === ds.records[2], 'TD3: trades[1] === records[2]');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD4: Chronological ordering preserved
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD4: Chronological ordering');
{
  const sessions = [
    buildNoBreakSession('2026-06-01'),
    buildStoppedSession('2026-06-02'),
    buildTargetHitSession('2026-06-03')
  ];
  const results = runBdrrStrategy(sessions, FROZEN_PRESET, BASE_CONFIG);
  const ds = buildTradeDataset(results);

  const dates = ds.records.map(r => r.session_date);
  check(dates[0] === '2026-06-01', 'TD4: records[0] date');
  check(dates[1] === '2026-06-02', 'TD4: records[1] date');
  check(dates[2] === '2026-06-03', 'TD4: records[2] date');
  for (let i = 1; i < dates.length; i++) {
    check(dates[i] >= dates[i - 1], 'TD4: chronological at index ' + i);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD5: Duplicate run_record_id rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD5: Duplicate run_record_id rejected');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  checkThrows(() => buildTradeDataset([results[0], results[0]]), 'RangeError', 'TD5');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD5b: Duplicate candidate_id rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD5b: Duplicate candidate_id rejected');
{
  const r1 = runBdrrStrategy([buildStoppedSession('2026-07-02')],
                              FROZEN_PRESET, BASE_CONFIG)[0];
  const r2 = runBdrrStrategy([buildTargetHitSession('2026-07-03')],
                              FROZEN_PRESET, BASE_CONFIG)[0];
  const r2Modified = Object.assign({}, r2, { candidate_id: r1.candidate_id });
  checkThrows(() => buildTradeDataset([r1, r2Modified]), 'RangeError', 'TD5b');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD6: Immutable output
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD6: Immutable output');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  const ds = buildTradeDataset(results);

  check(Object.isFrozen(ds),                  'TD6: dataset frozen');
  check(Object.isFrozen(ds.records),          'TD6: records array frozen');
  check(Object.isFrozen(ds.trades),           'TD6: trades array frozen');
  check(Object.isFrozen(ds.metadata),         'TD6: metadata frozen');
  check(Object.isFrozen(ds.metadata.date_range), 'TD6: date_range frozen');

  try { ds.schema_version = 'MODIFIED'; } catch (e) { /* expected */ }
  check(ds.schema_version === DATASET_SCHEMA_VERSION, 'TD6: schema_version not modifiable');

  try { ds.records[0] = null; } catch (e) { /* expected */ }
  check(ds.records[0] !== null, 'TD6: records array not modifiable');

  try { ds.trades[0] = null; } catch (e) { /* expected */ }
  check(ds.trades[0] !== null, 'TD6: trades array not modifiable');

  try { ds.metadata.session_count = 999; } catch (e) { /* expected */ }
  check(ds.metadata.session_count === 1, 'TD6: metadata.session_count not modifiable');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD7: Metadata fields completeness and correctness
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD7: Metadata generation');
{
  const sessions = [
    buildNoBreakSession('2026-06-01'),
    buildStoppedSession('2026-06-05'),
    buildTargetHitSession('2026-06-10')
  ];
  const results = runBdrrStrategy(sessions, FROZEN_PRESET, BASE_CONFIG);
  const ds = buildTradeDataset(results);
  const m = ds.metadata;

  check(m.schema_version === DATASET_SCHEMA_VERSION, 'TD7: schema_version in metadata');
  check(HEX64_RE.test(m.dataset_id),            'TD7: dataset_id 64-char hex');
  check(m.preset_id      === 'bdrr_v1_initial', 'TD7: preset_id correct');
  check(m.symbol         === 'TEST',            'TD7: symbol derived');
  check(m.exit_target_r  === 2,                 'TD7: exit_target_r in metadata');
  check(m.engine_version === 'bdrr_v1.0',       'TD7: engine_version in metadata');
  check(m.session_count  === 3,                 'TD7: session_count = 3');
  check(m.trade_count    === 2,                 'TD7: trade_count = 2');
  check(m.date_range.first === '2026-06-01',    'TD7: date_range.first');
  check(m.date_range.last  === '2026-06-10',    'TD7: date_range.last');
  check(typeof m.generated_at === 'string' && !isNaN(new Date(m.generated_at)),
        'TD7: generated_at valid ISO 8601');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD8: Schema validation — field-level rules
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD8: Schema validation');
{
  const goodResults = runBdrrStrategy([buildStoppedSession('2026-07-02')],
                                      FROZEN_PRESET, BASE_CONFIG);
  const good = goodResults[0];

  const del = (obj, key) => { const c = Object.assign({}, obj); delete c[key]; return c; };

  checkThrows(() => buildTradeDataset([del(good, 'run_record_id')]),
              'RangeError', 'TD8-missing-field');
  checkThrows(() => buildTradeDataset([Object.assign({}, good, { run_record_id: 'not-a-uuid' })]),
              'RangeError', 'TD8-bad-run_record_id');
  checkThrows(() => buildTradeDataset([Object.assign({}, good, { symbol: '' })]),
              'RangeError', 'TD8-empty-symbol');
  checkThrows(() => buildTradeDataset([Object.assign({}, good, { session_date: '2026/07/02' })]),
              'RangeError', 'TD8-bad-session-date');
  checkThrows(() => buildTradeDataset([Object.assign({}, good, { exit_target_r: 5 })]),
              'RangeError', 'TD8-bad-exit-target-r');
  checkThrows(() => buildTradeDataset([Object.assign({}, good, { detection_status: 'MAYBE' })]),
              'RangeError', 'TD8-bad-detection-status');
  checkThrows(() => buildTradeDataset([Object.assign({}, good, { failed_rules: null })]),
              'RangeError', 'TD8-failed-rules-not-array');
  checkThrows(() => buildTradeDataset([Object.assign({}, good, { entry_timestamp: 'not-a-date' })]),
              'RangeError', 'TD8-bad-timestamp');
  checkThrows(() => buildTradeDataset([Object.assign({}, good, { candidate_id: 'bad-uuid' })]),
              'RangeError', 'TD8-bad-candidate-id');

  // null timestamp must be allowed
  let nullTsOk = false;
  try { buildTradeDataset([Object.assign({}, good, { entry_timestamp: null })]); nullTsOk = true; }
  catch (e) { /* fail */ }
  check(nullTsOk, 'TD8: null entry_timestamp accepted');

  // Valid good record must succeed
  let validOk = false;
  try { buildTradeDataset(goodResults); validOk = true; } catch (e) { /* fail */ }
  check(validOk, 'TD8: valid record accepted');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD9: Invalid input rejection (type-level)
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD9: Invalid input rejection');
{
  checkThrows(() => buildTradeDataset(null),      'TypeError', 'TD9-null');
  checkThrows(() => buildTradeDataset(undefined), 'TypeError', 'TD9-undefined');
  checkThrows(() => buildTradeDataset('string'),  'TypeError', 'TD9-string');
  checkThrows(() => buildTradeDataset(42),        'TypeError', 'TD9-number');
  checkThrows(() => buildTradeDataset({}),        'TypeError', 'TD9-plain-object');
  checkThrows(() => buildTradeDataset([null]),    'RangeError', 'TD9-null-element');
  checkThrows(() => buildTradeDataset(['str']),   'RangeError', 'TD9-string-element');
  checkThrows(() => buildTradeDataset([42]),      'RangeError', 'TD9-number-element');
  checkThrows(() => buildTradeDataset([{}]),      'RangeError', 'TD9-empty-object');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD10: Integration — SPY batch
// ═══════════════════════════════════════════════════════════════════════════════

console.log('\nTD10: Integration — SPY batch');
{
  const spySessions = loadRealSessions('SPY');
  const results = runBdrrStrategy(spySessions, FROZEN_PRESET, BASE_CONFIG);
  const ds = buildTradeDataset(results);

  check(ds.schema_version === DATASET_SCHEMA_VERSION, 'TD10: schema_version correct');
  check(ds.metadata.session_count === spySessions.length, 'TD10: session_count matches');
  check(ds.records.length === spySessions.length, 'TD10: records.length matches');
  check(ds.metadata.symbol         === 'SPY',        'TD10: symbol = SPY');
  check(ds.metadata.preset_id      === 'bdrr_v1_initial', 'TD10: preset_id');
  check(ds.metadata.engine_version === 'bdrr_v1.0',  'TD10: engine_version');
  check(ds.metadata.exit_target_r  === 2,            'TD10: exit_target_r');

  const ORACLE_SPY = 3;
  check(ds.metadata.trade_count === ORACLE_SPY, 'TD10: trade_count = ' + ORACLE_SPY);
  check(ds.trades.length        === ORACLE_SPY, 'TD10: trades.length = ' + ORACLE_SPY);

  let inOrder = true;
  for (let i = 1; i < ds.records.length; i++) {
    if (ds.records[i].session_date < ds.records[i-1].session_date) { inOrder = false; break; }
  }
  check(inOrder, 'TD10: records chronological');

  const outcomes = {};
  ds.records.forEach(r => outcomes[r.outcome] = (outcomes[r.outcome] || 0) + 1);
  console.log('  Outcome breakdown: ' + JSON.stringify(outcomes));
  check(outcomes['NO_VALID_SETUP'] === 57, 'TD10: NO_VALID_SETUP = 57');
  check(outcomes['STOPPED']        === 2,  'TD10: STOPPED = 2');
  check(outcomes['TARGET_HIT']     === 1,  'TD10: TARGET_HIT = 1');

  const noValidInTrades = ds.trades.filter(r => r.outcome === OUTCOME.NO_VALID_SETUP).length;
  check(noValidInTrades === 0, 'TD10: no NO_VALID_SETUP in trades');

  check(Object.isFrozen(ds),         'TD10: dataset frozen');
  check(Object.isFrozen(ds.records), 'TD10: records frozen');
  check(Object.isFrozen(ds.trades),  'TD10: trades frozen');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD10b: Integration — QQQ batch
// ═══════════════════════════════════════════════════════════════════════════════

console.log('\nTD10b: Integration — QQQ batch');
{
  const qqqSessions = loadRealSessions('QQQ');
  const results = runBdrrStrategy(qqqSessions, FROZEN_PRESET, BASE_CONFIG);
  const ds = buildTradeDataset(results);

  check(ds.metadata.session_count === qqqSessions.length, 'TD10b: session_count correct');
  check(ds.metadata.symbol === 'QQQ', 'TD10b: symbol = QQQ');

  const ORACLE_QQQ = 4;
  check(ds.metadata.trade_count === ORACLE_QQQ, 'TD10b: trade_count = ' + ORACLE_QQQ);
  check(ds.trades.length        === ORACLE_QQQ, 'TD10b: trades.length = ' + ORACLE_QQQ);

  const outcomes = {};
  ds.records.forEach(r => outcomes[r.outcome] = (outcomes[r.outcome] || 0) + 1);
  console.log('  Outcome breakdown: ' + JSON.stringify(outcomes));
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD11: schema_version constant
// ═══════════════════════════════════════════════════════════════════════════════

console.log('\nTD11: schema_version constant');
{
  check(DATASET_SCHEMA_VERSION === 'TradeDataset/v1', 'TD11: constant value');
  check(buildTradeDataset([]).schema_version === 'TradeDataset/v1', 'TD11: schema_version on empty');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD12: date_range correctness
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD12: date_range correctness');
{
  const sessions = [
    buildNoBreakSession('2026-05-01'),
    buildStoppedSession('2026-05-15'),
    buildTargetHitSession('2026-05-31')
  ];
  const results = runBdrrStrategy(sessions, FROZEN_PRESET, BASE_CONFIG);
  const ds = buildTradeDataset(results);

  check(ds.metadata.date_range.first === '2026-05-01', 'TD12: date_range.first');
  check(ds.metadata.date_range.last  === '2026-05-31', 'TD12: date_range.last');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD13: trade_count === trades.length
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD13: trade_count === trades.length');
{
  const sessions = [
    buildNoBreakSession('2026-04-01'),
    buildNoBreakSession('2026-04-02'),
    buildNoBreakSession('2026-04-03'),
    buildStoppedSession('2026-04-04'),
    buildTargetHitSession('2026-04-05')
  ];
  const results = runBdrrStrategy(sessions, FROZEN_PRESET, BASE_CONFIG);
  const ds = buildTradeDataset(results);

  check(ds.metadata.session_count === 5, 'TD13: session_count = 5');
  check(ds.metadata.trade_count   === 2, 'TD13: trade_count = 2');
  check(ds.trades.length          === 2, 'TD13: trades.length = 2');
  check(ds.metadata.trade_count === ds.trades.length, 'TD13: trade_count === trades.length');
  check(ds.trades.every(r => r.candidate_id !== null), 'TD13: all trades have candidate_id');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD15: Object identity preserved
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD15: Object identity preserved');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  const original = results[0];
  const ds = buildTradeDataset(results);

  check(ds.records[0] === original,  'TD15: records[0] is original object');
  check(ds.trades[0]  === original,  'TD15: trades[0] is original object');
  check(ds.records[0] === ds.trades[0], 'TD15: records[0] === trades[0]');

  const FIELDS = [
    'run_record_id', 'symbol', 'session_date', 'preset_id', 'exit_target_r',
    'detection_status', 'candidate_id', 'entry_price_ticks', 'stop_price_ticks',
    'outcome', 'realized_r', 'exit_price_ticks'
  ];
  for (const f of FIELDS) {
    check(ds.trades[0][f] === original[f], 'TD15: "' + f + '" identity preserved');
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD16: Multiple null candidate_ids allowed (no false-positive dup)
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD16: Multiple null candidate_ids allowed');
{
  const sessions = [
    buildNoBreakSession('2026-04-01'), buildNoBreakSession('2026-04-02'),
    buildNoBreakSession('2026-04-03'), buildNoBreakSession('2026-04-04'),
    buildNoBreakSession('2026-04-05')
  ];
  const results = runBdrrStrategy(sessions, FROZEN_PRESET, BASE_CONFIG);

  let ok = false;
  try { buildTradeDataset(results); ok = true; } catch (e) { /* fail */ }
  check(ok, 'TD16: five null candidate_ids accepted');

  const ds = buildTradeDataset(results);
  check(ds.records.length   === 5, 'TD16: records.length = 5');
  check(ds.trades.length    === 0, 'TD16: trades.length = 0');
  check(ds.metadata.trade_count === 0, 'TD16: trade_count = 0');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD17: Out-of-order records rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD17: Out-of-order records rejected');
{
  const r1 = runBdrrStrategy([buildStoppedSession('2026-07-03')], FROZEN_PRESET, BASE_CONFIG)[0];
  const r2 = runBdrrStrategy([buildNoBreakSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG)[0];
  checkThrows(() => buildTradeDataset([r1, r2]), 'RangeError', 'TD17');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD18: PIPELINE_FAILURE rejected (missing engine_version — runner integration issue)
//
// The current Strategy Runner sets detection_result=null for PIPELINE_FAILURE
// records, making engine_version unavailable.  TradeDataset/v1 requires a
// non-empty engine_version on every record without exemption.  Until the runner
// populates engine_version on all record types, PIPELINE_FAILURE records cannot
// be included in a TradeDataset/v1 dataset.
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD18: PIPELINE_FAILURE rejected (runner integration issue)');
{
  const badSession = {
    symbol: 'TEST', date: '2026-07-01', market_timezone: 'America/New_York',
    session_open_utc_ms:  makeDate('2026-07-01', '09:30').getTime(),
    session_close_utc_ms: makeDate('2026-07-01', '16:00').getTime(),
    timeframe: '5m', candles: []
  };
  const results = runBdrrStrategy([badSession], FROZEN_PRESET, BASE_CONFIG);
  check(results[0].outcome === OUTCOME.PIPELINE_FAILURE,
        'TD18: runner produces PIPELINE_FAILURE');
  check(results[0].detection_result === null,
        'TD18: PIPELINE_FAILURE has detection_result=null');

  // Must be rejected because engine_version cannot be extracted
  checkThrows(() => buildTradeDataset(results), 'RangeError',
              'TD18-pipeline-failure-rejected');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD19: Invalid outcome string rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD19: Invalid outcome string rejected');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  checkThrows(() => buildTradeDataset([Object.assign({}, results[0], { outcome: 'INVALID_VALUE' })]),
              'RangeError', 'TD19');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD20: Invalid detection_status rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD20: Invalid detection_status rejected');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  checkThrows(() => buildTradeDataset([Object.assign({}, results[0], { detection_status: 'PENDING' })]),
              'RangeError', 'TD20');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD21: Same complete input → same dataset_id
// ═══════════════════════════════════════════════════════════════════════════════

console.log('\nTD21: Deterministic ID — same input → same dataset_id');
{
  const sessions = [
    buildNoBreakSession('2026-06-01'),
    buildStoppedSession('2026-06-02'),
    buildTargetHitSession('2026-06-03')
  ];
  const results = runBdrrStrategy(sessions, FROZEN_PRESET, BASE_CONFIG);

  const ds1 = buildTradeDataset(results);
  const ds2 = buildTradeDataset(results);

  check(ds1.metadata.dataset_id === ds2.metadata.dataset_id,
        'TD21: same input → same dataset_id');
  check(HEX64_RE.test(ds1.metadata.dataset_id), 'TD21: dataset_id is 64-char hex');
  check(ds1.metadata.dataset_id.length === 64, 'TD21: dataset_id length = 64');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD22: Changing only outcome (same run_record_id / candidate_id) → different dataset_id
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD22: Changing only outcome → different dataset_id');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  const original = results[0];

  const ds1 = buildTradeDataset(results);

  // Manufacture a record with the same IDs but a different outcome
  const modified = Object.assign({}, original, { outcome: OUTCOME.NO_VALID_SETUP });
  const ds2 = buildTradeDataset([modified]);

  check(ds1.metadata.dataset_id !== ds2.metadata.dataset_id,
        'TD22: different outcome → different dataset_id');
  // Confirm IDs are the same (the change is only in content, not identity)
  check(original.run_record_id === modified.run_record_id,
        'TD22: run_record_id unchanged');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD22b: Changing nested field trade_plan.stop_price.ticks → different dataset_id
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD22b: Changing nested trade_plan.stop_price.ticks → different dataset_id');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  const original = results[0];
  const ds1 = buildTradeDataset(results);

  // Deep-clone trade_plan, mutate stop_price.ticks
  const tp = original.trade_plan;
  const modifiedTp = Object.assign({}, tp, {
    stop_price: Object.assign({}, tp.stop_price, { ticks: tp.stop_price.ticks + 1 })
  });
  const modified = Object.assign({}, original, { trade_plan: modifiedTp });
  const ds2 = buildTradeDataset([modified]);

  check(ds1.metadata.dataset_id !== ds2.metadata.dataset_id,
        'TD22b: different trade_plan.stop_price.ticks → different dataset_id');
  check(original.run_record_id === modified.run_record_id,
        'TD22b: run_record_id unchanged');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD22c: Different key insertion order → same dataset_id (key-order insensitive)
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD22c: Different key insertion order → same dataset_id');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  const original = results[0];
  const ds1 = buildTradeDataset(results);

  // Rebuild the record with reversed key insertion order
  const keys = Object.keys(original);
  const reversed = {};
  for (let i = keys.length - 1; i >= 0; i--) {
    reversed[keys[i]] = original[keys[i]];
  }

  const ds2 = buildTradeDataset([reversed]);
  check(ds1.metadata.dataset_id === ds2.metadata.dataset_id,
        'TD22c: reversed key order → same dataset_id');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD22d: generated_at does not affect dataset_id
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD22d: generated_at does not affect dataset_id');
{
  // generated_at is a metadata field produced by buildTradeDataset itself,
  // not a Strategy Runner record field.  It is excluded from the hash input.
  // Two builds of the same records must produce the same dataset_id even if
  // generated_at differs between the two calls.
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);

  const ds1 = buildTradeDataset(results);
  // Small delay would normally make generated_at differ, but dataset_id must not.
  const ds2 = buildTradeDataset(results);

  check(ds1.metadata.dataset_id === ds2.metadata.dataset_id,
        'TD22d: dataset_id stable across two builds (generated_at excluded from hash)');
  // Confirm generated_at is present as a metadata field (it is not absent)
  check(typeof ds1.metadata.generated_at === 'string', 'TD22d: generated_at present in metadata');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD23: Out-of-order input rejected — cannot silently produce a dataset
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD23: Out-of-order input rejected');
{
  const r1 = runBdrrStrategy([buildStoppedSession('2026-07-03')], FROZEN_PRESET, BASE_CONFIG)[0];
  const r2 = runBdrrStrategy([buildNoBreakSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG)[0];

  checkThrows(() => buildTradeDataset([r1, r2]), 'RangeError', 'TD23-out-of-order');

  // Correct order must succeed
  let dsOrdered;
  let ok = false;
  try { dsOrdered = buildTradeDataset([r2, r1]); ok = true; } catch (e) { /* fail */ }
  check(ok, 'TD23: correctly ordered [r2, r1] accepted');
  if (dsOrdered) {
    check(dsOrdered.records[0].session_date === '2026-07-02', 'TD23: records[0] = 07-02');
    check(dsOrdered.records[1].session_date === '2026-07-03', 'TD23: records[1] = 07-03');
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD24: NO_VALID_SETUP in records but NOT in trades
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD24: NO_VALID_SETUP in records but not in trades');
{
  const sessions = [
    buildNoBreakSession('2026-08-01'),
    buildNoBreakSession('2026-08-04'),
    buildStoppedSession('2026-08-05'),
    buildNoBreakSession('2026-08-06')
  ];
  const results = runBdrrStrategy(sessions, FROZEN_PRESET, BASE_CONFIG);
  const ds = buildTradeDataset(results);

  check(ds.records.length === 4, 'TD24: records.length = 4');
  check(ds.trades.length  === 1, 'TD24: trades.length = 1');

  const noValidInTrades   = ds.trades.filter(r => r.outcome === OUTCOME.NO_VALID_SETUP).length;
  const noValidInRecords  = ds.records.filter(r => r.outcome === OUTCOME.NO_VALID_SETUP).length;
  check(noValidInTrades  === 0, 'TD24: no NO_VALID_SETUP in trades');
  check(noValidInRecords === 3, 'TD24: 3 NO_VALID_SETUP in records');
  check(ds.records.filter(r => r.outcome === OUTCOME.NO_VALID_SETUP)
                  .every(r => r.candidate_id === null),
        'TD24: all NO_VALID_SETUP have null candidate_id');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD25: Homogeneous — mixed symbol rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('\nTD25: Homogeneous — mixed symbol rejected');
{
  const r1 = runBdrrStrategy([buildStoppedSession('2026-07-02', 'SPY')],
                              FROZEN_PRESET, BASE_CONFIG)[0];
  const r2 = runBdrrStrategy([buildNoBreakSession('2026-07-03', 'QQQ')],
                              FROZEN_PRESET, BASE_CONFIG)[0];
  checkThrows(() => buildTradeDataset([r1, r2]), 'RangeError', 'TD25-mixed-symbol');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD26: Homogeneous — mixed preset_id rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD26: Homogeneous — mixed preset_id rejected');
{
  const presetB = Object.freeze(Object.assign({}, FROZEN_PRESET, { preset_id: 'bdrr_v2_alt' }));
  const r1 = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG)[0];
  const r2 = runBdrrStrategy([buildNoBreakSession('2026-07-03')], presetB,       BASE_CONFIG)[0];
  checkThrows(() => buildTradeDataset([r1, r2]), 'RangeError', 'TD26-mixed-preset-id');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD27: Homogeneous — mixed exit_target_r rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD27: Homogeneous — mixed exit_target_r rejected');
{
  const configR3 = Object.freeze(Object.assign({}, BASE_CONFIG, { exit_target_r: 3 }));
  const r1 = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG)[0];
  const r2 = runBdrrStrategy([buildNoBreakSession('2026-07-03')], FROZEN_PRESET, configR3)[0];
  checkThrows(() => buildTradeDataset([r1, r2]), 'RangeError', 'TD27-mixed-exit-target-r');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD28: Homogeneous — mixed engine_version rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD28: Homogeneous — mixed engine_version rejected');
{
  const configV2 = Object.freeze(Object.assign({}, BASE_CONFIG, { engine_version: 'bdrr_v2.0' }));
  const r1 = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG)[0];
  const r2 = runBdrrStrategy([buildNoBreakSession('2026-07-03')], FROZEN_PRESET, configV2)[0];
  checkThrows(() => buildTradeDataset([r1, r2]), 'RangeError', 'TD28-mixed-engine-version');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD29: Single record with null detection_result → rejected (missing engine_version)
// ═══════════════════════════════════════════════════════════════════════════════

console.log('\nTD29: Record with null detection_result rejected (missing engine_version)');
{
  const results = runBdrrStrategy([buildStoppedSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG);
  // Null out detection_result to simulate missing engine_version
  const modified = Object.assign({}, results[0], { detection_result: null });
  checkThrows(() => buildTradeDataset([modified]), 'RangeError', 'TD29-null-detection-result');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD30: PIPELINE_FAILURE record (detection_result=null) → rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD30: PIPELINE_FAILURE record rejected (detection_result=null, no engine_version)');
{
  const badSession = {
    symbol: 'TEST', date: '2026-07-01', market_timezone: 'America/New_York',
    session_open_utc_ms:  makeDate('2026-07-01', '09:30').getTime(),
    session_close_utc_ms: makeDate('2026-07-01', '16:00').getTime(),
    timeframe: '5m', candles: []
  };
  const results = runBdrrStrategy([badSession], FROZEN_PRESET, BASE_CONFIG);
  check(results[0].outcome          === OUTCOME.PIPELINE_FAILURE, 'TD30: outcome is PIPELINE_FAILURE');
  check(results[0].detection_result === null, 'TD30: detection_result is null');
  checkThrows(() => buildTradeDataset(results), 'RangeError', 'TD30-pipeline-failure-rejected');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD31: Mixed engine_version (one valid, one different) → rejected
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD31: Mixed engine versions rejected');
{
  const configV2 = Object.freeze(Object.assign({}, BASE_CONFIG, { engine_version: 'bdrr_v2.0' }));
  const r1 = runBdrrStrategy([buildNoBreakSession('2026-07-02')], FROZEN_PRESET, BASE_CONFIG)[0];
  const r2 = runBdrrStrategy([buildStoppedSession('2026-07-03')], FROZEN_PRESET, configV2)[0];

  // Both have detection_results with different engine_version strings
  check(r1.detection_result.engine_version === 'bdrr_v1.0', 'TD31: r1 engine = bdrr_v1.0');
  check(r2.detection_result.engine_version === 'bdrr_v2.0', 'TD31: r2 engine = bdrr_v2.0');
  checkThrows(() => buildTradeDataset([r1, r2]), 'RangeError', 'TD31-mixed-engine-versions');
}

// ═══════════════════════════════════════════════════════════════════════════════
// TD32: Homogeneous engine versions → accepted
// ═══════════════════════════════════════════════════════════════════════════════

console.log('TD32: Homogeneous engine versions accepted');
{
  const sessions = [
    buildNoBreakSession('2026-06-01'),
    buildStoppedSession('2026-06-02'),
    buildTargetHitSession('2026-06-03'),
    buildNoBreakSession('2026-06-04')
  ];
  const results = runBdrrStrategy(sessions, FROZEN_PRESET, BASE_CONFIG);

  // All non-PIPELINE_FAILURE records have detection_result with engine_version
  const evs = results.map(r => r.detection_result && r.detection_result.engine_version);
  check(evs.every(v => v === 'bdrr_v1.0'), 'TD32: all engine versions = bdrr_v1.0');

  let ok = false;
  let ds;
  try { ds = buildTradeDataset(results); ok = true; } catch (e) { /* fail */ }
  check(ok, 'TD32: homogeneous engine versions accepted');
  if (ds) {
    check(ds.metadata.engine_version === 'bdrr_v1.0', 'TD32: engine_version in metadata');
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUMMARY
// ═══════════════════════════════════════════════════════════════════════════════

console.log('\n=== Trade Dataset: ' + checks + ' checks, ' + failures.length + ' failures ===');
if (failures.length > 0) {
  console.log('\nFailed checks:');
  failures.forEach((f, i) => console.log('  ' + (i + 1) + '. ' + f));
  process.exit(1);
} else {
  console.log('All checks passed.\n');
}
