/**
 * test_bdrr_trade_plan.js
 *
 * Tests for estrategie/bdrr_trade_plan.js — TradePlan/v1 construction.
 *
 * Coverage:
 *  1.  schema_version === 'TradePlan/v1'
 *  2.  CONFIRMATION_CLOSE with zero buffers
 *  3.  CONFIRMATION_CLOSE with non-zero buffers
 *  4.  BREAK_OF_SIGNAL_BAR with zero buffers
 *  5.  BREAK_OF_SIGNAL_BAR with non-zero buffers
 *  6.  Correct LONG stop calculation
 *  7.  risk equals absolute entry/stop distance
 *  8.  Exact 2R, 3R, and 4R targets
 *  9.  Every stored tick value is an integer
 *  10. tick_size is preserved consistently
 *  11. Invalid detection result is rejected
 *  12. Missing confirmation bar is rejected
 *  13. Non-integer tick input is rejected
 *  14. Inconsistent tick sizes are rejected
 *  15. Negative and non-integer buffers are rejected
 *  16. Unknown entry model is rejected
 *  17. Unsupported SHORT direction is rejected
 *  18. Zero risk is rejected
 *  19. Repeated runs are deeply identical
 *  20. Input objects are not mutated
 *  21. Oracle TradePlan parity for all eligible candidates
 *  22. QQQ 2026-07-14 is explicitly excluded (not rounded or reconstructed)
 *
 * Run: node estrategie/test_bdrr_trade_plan.js
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const { buildTradePlan } = require('./bdrr_trade_plan.js');

// ── Test harness ─────────────────────────────────────────────────────────────

let checks   = 0;
let failures = [];

function check(cond, msg) {
  checks++;
  if (!cond) failures.push(msg);
}

function deepEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

const TICK = 0.01;

// Base config for LONG / CONFIRMATION_CLOSE / zero buffers
const BASE_CONFIG = {
  direction:           'LONG',
  entry_model:         'CONFIRMATION_CLOSE',
  entry_buffer_ticks:  0,
  stop_buffer_ticks:   0,
  tick_size:           TICK
};

// A synthetic confirmation candle whose geometry is straightforward:
//   high=102.00, low=100.00, open=101.50, close=101.20
//   level=101.00 (below low — used only for detecton context, not here)
function syntheticDetectionResult(overrides) {
  const base = {
    status: 'OK',
    date:   '2026-07-01',
    level_price: 101.00,
    direction: 'LONG',
    confirmation_candle: {
      time:  new Date('2026-07-01T13:45:00Z'),
      open:  101.50,
      high:  102.00,
      low:   100.00,
      close: 101.20
    },
    geometry: {},
    failed_retests: [],
    failed_retest_count: 0
  };
  return Object.assign({}, base, overrides);
}

// ── 1. schema_version ────────────────────────────────────────────────────────

(function testSchemaVersion() {
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'schemaVersion: expected OK status');
  check(
    r.trade_plan && r.trade_plan.schema_version === 'TradePlan/v1',
    `schemaVersion: expected "TradePlan/v1", got ${r.trade_plan && r.trade_plan.schema_version}`
  );
})();

// ── 2. CONFIRMATION_CLOSE / zero buffers ─────────────────────────────────────

(function testConfirmationCloseZeroBuffers() {
  // close=101.20 -> 10120 ticks; low=100.00 -> 10000 ticks
  // entry=10120, stop=10000, risk=120
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'CC_zero: expected OK');
  const tp = r.trade_plan;
  check(tp.entry_price.ticks === 10120,
    `CC_zero: entry_price.ticks expected 10120, got ${tp.entry_price.ticks}`);
  check(tp.stop_price.ticks === 10000,
    `CC_zero: stop_price.ticks expected 10000, got ${tp.stop_price.ticks}`);
  check(tp.risk.ticks === 120,
    `CC_zero: risk.ticks expected 120, got ${tp.risk.ticks}`);
  check(tp.entry_model === 'CONFIRMATION_CLOSE',
    `CC_zero: entry_model expected CONFIRMATION_CLOSE, got ${tp.entry_model}`);
  check(tp.entry_buffer_ticks === 0, 'CC_zero: entry_buffer_ticks must be 0');
  check(tp.stop_buffer_ticks === 0, 'CC_zero: stop_buffer_ticks must be 0');
})();

// ── 3. CONFIRMATION_CLOSE / non-zero buffers ──────────────────────────────────

(function testConfirmationCloseNonZeroBuffers() {
  // entry_buffer=3 ticks above close, stop_buffer=2 ticks below low
  // entry = 10120 + 3 = 10123; stop = 10000 - 2 = 9998; risk = 125
  const config = Object.assign({}, BASE_CONFIG, {
    entry_buffer_ticks: 3,
    stop_buffer_ticks:  2
  });
  const r = buildTradePlan(syntheticDetectionResult(), config);
  check(r.status === 'OK', 'CC_nonzero: expected OK');
  const tp = r.trade_plan;
  check(tp.entry_price.ticks === 10123,
    `CC_nonzero: entry expected 10123, got ${tp.entry_price.ticks}`);
  check(tp.stop_price.ticks === 9998,
    `CC_nonzero: stop expected 9998, got ${tp.stop_price.ticks}`);
  check(tp.risk.ticks === 125,
    `CC_nonzero: risk expected 125, got ${tp.risk.ticks}`);
  check(tp.entry_buffer_ticks === 3, 'CC_nonzero: entry_buffer_ticks must be 3');
  check(tp.stop_buffer_ticks  === 2, 'CC_nonzero: stop_buffer_ticks must be 2');
})();

// ── 4. BREAK_OF_SIGNAL_BAR / zero buffers ────────────────────────────────────

(function testBreakOfSignalBarZeroBuffers() {
  // high=102.00 -> 10200 ticks; low=100.00 -> 10000; risk=200
  const config = Object.assign({}, BASE_CONFIG, { entry_model: 'BREAK_OF_SIGNAL_BAR' });
  const r = buildTradePlan(syntheticDetectionResult(), config);
  check(r.status === 'OK', 'BOSB_zero: expected OK');
  const tp = r.trade_plan;
  check(tp.entry_price.ticks === 10200,
    `BOSB_zero: entry expected 10200 (from high), got ${tp.entry_price.ticks}`);
  check(tp.stop_price.ticks === 10000,
    `BOSB_zero: stop expected 10000, got ${tp.stop_price.ticks}`);
  check(tp.risk.ticks === 200,
    `BOSB_zero: risk expected 200, got ${tp.risk.ticks}`);
  check(tp.entry_model === 'BREAK_OF_SIGNAL_BAR',
    `BOSB_zero: entry_model expected BREAK_OF_SIGNAL_BAR, got ${tp.entry_model}`);
})();

// ── 5. BREAK_OF_SIGNAL_BAR / non-zero buffers ────────────────────────────────

(function testBreakOfSignalBarNonZeroBuffers() {
  // entry_buffer=5, stop_buffer=1
  // entry = 10200 + 5 = 10205; stop = 10000 - 1 = 9999; risk = 206
  const config = Object.assign({}, BASE_CONFIG, {
    entry_model: 'BREAK_OF_SIGNAL_BAR',
    entry_buffer_ticks: 5,
    stop_buffer_ticks:  1
  });
  const r = buildTradePlan(syntheticDetectionResult(), config);
  check(r.status === 'OK', 'BOSB_nonzero: expected OK');
  const tp = r.trade_plan;
  check(tp.entry_price.ticks === 10205,
    `BOSB_nonzero: entry expected 10205, got ${tp.entry_price.ticks}`);
  check(tp.stop_price.ticks === 9999,
    `BOSB_nonzero: stop expected 9999, got ${tp.stop_price.ticks}`);
  check(tp.risk.ticks === 206,
    `BOSB_nonzero: risk expected 206, got ${tp.risk.ticks}`);
})();

// ── 6. LONG stop calculation is strictly below entry ─────────────────────────

(function testLongStopBelowEntry() {
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'stopBelowEntry: expected OK');
  const tp = r.trade_plan;
  check(tp.entry_price.ticks > tp.stop_price.ticks,
    `stopBelowEntry: entry (${tp.entry_price.ticks}) must be strictly above stop (${tp.stop_price.ticks})`);
})();

// ── 7. risk === abs(entry - stop) ─────────────────────────────────────────────

(function testRiskEqualsAbsDistance() {
  // Test with both entry models and non-zero buffers
  for (const model of ['CONFIRMATION_CLOSE', 'BREAK_OF_SIGNAL_BAR']) {
    for (const [eb, sb] of [[0,0],[3,2],[0,5]]) {
      const config = Object.assign({}, BASE_CONFIG, {
        entry_model: model,
        entry_buffer_ticks: eb,
        stop_buffer_ticks: sb
      });
      const r = buildTradePlan(syntheticDetectionResult(), config);
      if (r.status !== 'OK') continue;
      const tp = r.trade_plan;
      const expected = Math.abs(tp.entry_price.ticks - tp.stop_price.ticks);
      check(tp.risk.ticks === expected,
        `risk_abs (${model} eb=${eb} sb=${sb}): expected ${expected}, got ${tp.risk.ticks}`);
    }
  }
})();

// ── 8. exact 2R, 3R, 4R targets ──────────────────────────────────────────────

(function testExactTargets() {
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'targets: expected OK');
  const tp = r.trade_plan;
  const e = tp.entry_price.ticks;
  const risk = tp.risk.ticks;
  check(tp.r2_price.ticks === e + 2 * risk,
    `targets: r2 expected ${e + 2*risk}, got ${tp.r2_price.ticks}`);
  check(tp.r3_price.ticks === e + 3 * risk,
    `targets: r3 expected ${e + 3*risk}, got ${tp.r3_price.ticks}`);
  check(tp.r4_price.ticks === e + 4 * risk,
    `targets: r4 expected ${e + 4*risk}, got ${tp.r4_price.ticks}`);

  // Also verify with non-zero buffers and BOSB
  const config2 = Object.assign({}, BASE_CONFIG, {
    entry_model: 'BREAK_OF_SIGNAL_BAR',
    entry_buffer_ticks: 5,
    stop_buffer_ticks:  1
  });
  const r2 = buildTradePlan(syntheticDetectionResult(), config2);
  check(r2.status === 'OK', 'targets BOSB: expected OK');
  const tp2 = r2.trade_plan;
  const e2 = tp2.entry_price.ticks;
  const risk2 = tp2.risk.ticks;
  check(tp2.r2_price.ticks === e2 + 2 * risk2, `targets BOSB: r2 wrong`);
  check(tp2.r3_price.ticks === e2 + 3 * risk2, `targets BOSB: r3 wrong`);
  check(tp2.r4_price.ticks === e2 + 4 * risk2, `targets BOSB: r4 wrong`);
})();

// ── 9. every stored tick value is an integer ──────────────────────────────────

(function testAllTickValuesAreIntegers() {
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'integerTicks: expected OK');
  const tp = r.trade_plan;
  for (const field of ['entry_price', 'stop_price', 'risk', 'r2_price', 'r3_price', 'r4_price']) {
    check(
      Number.isInteger(tp[field].ticks),
      `integerTicks: ${field}.ticks must be an integer, got ${tp[field].ticks}`
    );
  }
})();

// ── 10. tick_size preserved consistently ──────────────────────────────────────

(function testTickSizeConsistency() {
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'tickSize: expected OK');
  const tp = r.trade_plan;
  check(tp.tick_size === TICK, `tickSize: top-level tick_size expected ${TICK}, got ${tp.tick_size}`);
  for (const field of ['entry_price', 'stop_price', 'risk', 'r2_price', 'r3_price', 'r4_price']) {
    check(
      tp[field].tick_size === TICK,
      `tickSize: ${field}.tick_size expected ${TICK}, got ${tp[field].tick_size}`
    );
  }
})();

// ── 11. invalid detection result is rejected ──────────────────────────────────

(function testInvalidDetectionRejected() {
  // status FAILED
  const r1 = buildTradePlan({ status: 'FAILED', failed_stage: 'BREAK_NOT_FOUND' }, BASE_CONFIG);
  check(r1.status === 'FAILED', 'invalidDetection: FAILED status must be rejected');
  check(r1.failure_code === 'INVALID_DETECTION_RESULT',
    `invalidDetection: expected INVALID_DETECTION_RESULT, got ${r1.failure_code}`);

  // null input
  const r2 = buildTradePlan(null, BASE_CONFIG);
  check(r2.status === 'FAILED', 'invalidDetection: null must be rejected');
  check(r2.failure_code === 'INVALID_DETECTION_RESULT',
    `invalidDetection: null gave ${r2.failure_code}`);

  // non-object
  const r3 = buildTradePlan('not-an-object', BASE_CONFIG);
  check(r3.status === 'FAILED', 'invalidDetection: string must be rejected');
})();

// ── 12. missing confirmation bar is rejected ──────────────────────────────────

(function testMissingConfirmationBar() {
  const dr = syntheticDetectionResult({ confirmation_candle: null });
  const r = buildTradePlan(dr, BASE_CONFIG);
  check(r.status === 'FAILED', 'missingBar: expected FAILED');
  check(r.failure_code === 'MISSING_CONFIRMATION_BAR',
    `missingBar: expected MISSING_CONFIRMATION_BAR, got ${r.failure_code}`);

  // undefined
  const dr2 = syntheticDetectionResult();
  delete dr2.confirmation_candle;
  const r2 = buildTradePlan(dr2, BASE_CONFIG);
  check(r2.status === 'FAILED', 'missingBar: undefined must be rejected');
  check(r2.failure_code === 'MISSING_CONFIRMATION_BAR',
    `missingBar: undefined gave ${r2.failure_code}`);
})();

// ── 13. non-integer / non-finite tick input is rejected ───────────────────────

(function testNonIntegerTickInput() {
  // NaN close
  const dr1 = syntheticDetectionResult({
    confirmation_candle: { time: new Date(), open: 101.50, high: 102.00, low: 100.00, close: NaN }
  });
  const r1 = buildTradePlan(dr1, BASE_CONFIG);
  check(r1.status === 'FAILED', 'nonIntegerTick: NaN close must be rejected');
  check(r1.failure_code === 'INVALID_TICK_VALUE',
    `nonIntegerTick: NaN close gave ${r1.failure_code}`);

  // Infinity high
  const dr2 = syntheticDetectionResult({
    confirmation_candle: { time: new Date(), open: 101.50, high: Infinity, low: 100.00, close: 101.20 }
  });
  const r2 = buildTradePlan(dr2, BASE_CONFIG);
  check(r2.status === 'FAILED', 'nonIntegerTick: Infinity high must be rejected');
  check(r2.failure_code === 'INVALID_TICK_VALUE',
    `nonIntegerTick: Infinity high gave ${r2.failure_code}`);

  // String field
  const dr3 = syntheticDetectionResult({
    confirmation_candle: { time: new Date(), open: '101.50', high: 102.00, low: 100.00, close: 101.20 }
  });
  const r3 = buildTradePlan(dr3, BASE_CONFIG);
  check(r3.status === 'FAILED', 'nonIntegerTick: string open must be rejected');
  check(r3.failure_code === 'INVALID_TICK_VALUE',
    `nonIntegerTick: string open gave ${r3.failure_code}`);
})();

// ── 14. inconsistent tick sizes are rejected ──────────────────────────────────

(function testTickSizeMismatch() {
  // Non-finite tick_size
  const r1 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { tick_size: 0 }));
  check(r1.status === 'FAILED', 'tickMismatch: zero tick_size must be rejected');
  check(r1.failure_code === 'TICK_SIZE_MISMATCH',
    `tickMismatch: zero tick_size gave ${r1.failure_code}`);

  const r2 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { tick_size: -0.01 }));
  check(r2.status === 'FAILED', 'tickMismatch: negative tick_size must be rejected');
  check(r2.failure_code === 'TICK_SIZE_MISMATCH',
    `tickMismatch: negative tick_size gave ${r2.failure_code}`);

  const r3 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { tick_size: NaN }));
  check(r3.status === 'FAILED', 'tickMismatch: NaN tick_size must be rejected');
  check(r3.failure_code === 'TICK_SIZE_MISMATCH',
    `tickMismatch: NaN tick_size gave ${r3.failure_code}`);
})();

// ── 15. negative and non-integer buffers are rejected ─────────────────────────

(function testInvalidBuffers() {
  // Negative entry buffer
  const r1 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { entry_buffer_ticks: -1 }));
  check(r1.status === 'FAILED', 'buffers: negative entry_buffer must be rejected');
  check(r1.failure_code === 'INVALID_BUFFER',
    `buffers: negative entry_buffer gave ${r1.failure_code}`);

  // Negative stop buffer
  const r2 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { stop_buffer_ticks: -5 }));
  check(r2.status === 'FAILED', 'buffers: negative stop_buffer must be rejected');
  check(r2.failure_code === 'INVALID_BUFFER',
    `buffers: negative stop_buffer gave ${r2.failure_code}`);

  // Float entry buffer
  const r3 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { entry_buffer_ticks: 1.5 }));
  check(r3.status === 'FAILED', 'buffers: float entry_buffer must be rejected');
  check(r3.failure_code === 'INVALID_BUFFER',
    `buffers: float entry_buffer gave ${r3.failure_code}`);

  // Float stop buffer
  const r4 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { stop_buffer_ticks: 0.5 }));
  check(r4.status === 'FAILED', 'buffers: float stop_buffer must be rejected');
  check(r4.failure_code === 'INVALID_BUFFER',
    `buffers: float stop_buffer gave ${r4.failure_code}`);

  // Undefined buffer (treated as non-integer)
  const r5 = buildTradePlan(syntheticDetectionResult(),
    { direction: 'LONG', entry_model: 'CONFIRMATION_CLOSE',
      tick_size: TICK, stop_buffer_ticks: 0
      /* entry_buffer_ticks missing */ });
  check(r5.status === 'FAILED', 'buffers: missing entry_buffer must be rejected');
  check(r5.failure_code === 'INVALID_BUFFER',
    `buffers: missing entry_buffer gave ${r5.failure_code}`);
})();

// ── 16. unknown entry model is rejected ───────────────────────────────────────

(function testUnknownEntryModel() {
  const r = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { entry_model: 'MARKET_ORDER' }));
  check(r.status === 'FAILED', 'unknownModel: expected FAILED');
  check(r.failure_code === 'UNSUPPORTED_ENTRY_MODEL',
    `unknownModel: expected UNSUPPORTED_ENTRY_MODEL, got ${r.failure_code}`);

  const r2 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { entry_model: undefined }));
  check(r2.status === 'FAILED', 'unknownModel: undefined must be rejected');
  check(r2.failure_code === 'UNSUPPORTED_ENTRY_MODEL',
    `unknownModel: undefined gave ${r2.failure_code}`);
})();

// ── 17. unsupported SHORT direction is rejected ───────────────────────────────

(function testShortDirectionRejected() {
  const r = buildTradePlan(
    syntheticDetectionResult({ direction: 'SHORT' }),
    Object.assign({}, BASE_CONFIG, { direction: 'SHORT' })
  );
  check(r.status === 'FAILED', 'short: expected FAILED');
  check(r.failure_code === 'UNSUPPORTED_DIRECTION',
    `short: expected UNSUPPORTED_DIRECTION, got ${r.failure_code}`);

  // Must not throw
  let threw = false;
  try {
    buildTradePlan(syntheticDetectionResult({ direction: 'SHORT' }),
      Object.assign({}, BASE_CONFIG, { direction: 'SHORT' }));
  } catch (e) { threw = true; }
  check(!threw, 'short: must not throw — must return structured failure');
})();

// ── 18. zero risk is rejected ─────────────────────────────────────────────────

(function testZeroRiskRejected() {
  // entry and stop at the same price: open=high=low=close=101.00
  const drDoji = syntheticDetectionResult({
    confirmation_candle: {
      time: new Date(), open: 101.00, high: 101.00, low: 101.00, close: 101.00
    }
  });
  const r = buildTradePlan(drDoji, BASE_CONFIG);
  check(r.status === 'FAILED', 'zeroRisk: doji (entry==stop) must be rejected');
  check(r.failure_code === 'INVALID_RISK',
    `zeroRisk: doji gave ${r.failure_code}`);

  // BOSB: entry = high + 0 buffer; stop = low - 0 buffer; same price = zero risk
  const r2 = buildTradePlan(drDoji, Object.assign({}, BASE_CONFIG, { entry_model: 'BREAK_OF_SIGNAL_BAR' }));
  check(r2.status === 'FAILED', 'zeroRisk: BOSB doji must be rejected');
  check(r2.failure_code === 'INVALID_RISK',
    `zeroRisk: BOSB doji gave ${r2.failure_code}`);
})();

// ── 19. repeated runs are deeply identical ────────────────────────────────────

(function testDeterminism() {
  function freshDR() { return syntheticDetectionResult(); }
  const config = Object.assign({}, BASE_CONFIG, {
    entry_buffer_ticks: 2,
    stop_buffer_ticks:  1
  });
  const r1 = buildTradePlan(freshDR(), config);
  const r2 = buildTradePlan(freshDR(), config);
  check(deepEqual(r1, r2), 'determinism: repeated runs must be deeply identical');
})();

// ── 20. input objects are not mutated ─────────────────────────────────────────

(function testNoMutation() {
  const dr = syntheticDetectionResult();
  const config = Object.assign({}, BASE_CONFIG);
  const drCopy     = JSON.parse(JSON.stringify(dr,
    (k, v) => v instanceof Date ? v.toISOString() : v));
  const configCopy = Object.assign({}, config);

  buildTradePlan(dr, config);

  // Config fields must not have changed
  check(deepEqual(config, configCopy), 'mutation: config must not be mutated');

  // detectionResult scalar fields must not have changed
  check(dr.status        === drCopy.status,         'mutation: dr.status mutated');
  check(dr.level_price   === drCopy.level_price,    'mutation: dr.level_price mutated');
  check(dr.direction     === drCopy.direction,      'mutation: dr.direction mutated');

  // confirmation_candle fields
  const bar = dr.confirmation_candle;
  const barCopy = drCopy.confirmation_candle;
  for (const f of ['open','high','low','close']) {
    check(bar[f] === barCopy[f],
      `mutation: confirmation_candle.${f} was mutated`);
  }
})();

// ── 21 & 22. Oracle parity ────────────────────────────────────────────────────
//
// Strategy:
//   For each candidate with full confirmation OHLC and detection_status=VALID
//   and executable!==false and trade_plan_oracle_status!=='EXCLUDED':
//     - build a synthetic detectionResult from the oracle's confirmation OHLC
//     - call buildTradePlan with zero buffers and CONFIRMATION_CLOSE
//     - assert entry_price.ticks, stop_price.ticks, risk.ticks, r2/r3/r4.ticks
//       all match oracle arithmetic (computed from oracle OHLC, not from oracle
//       display entry/stop/target fields — those carry independent rounding)
//   For QQQ 2026-07-14 (trade_plan_oracle_status='EXCLUDED'):
//     - assert explicitly that the candidate IS excluded (test 22)
//     - do NOT call buildTradePlan on it
//
// Important note on SPY r2/r3/r4:
//   Oracle *display* targets for SPY_2026-05-26 show 0.01–0.02 drift versus
//   integer-tick arithmetic (documented display-rounding in the oracle's own
//   unresolved_fields). The parity tests compare against the correct integer-tick
//   arithmetic values, not the display values.

(function testOracleParity() {
  function loadOracle(filename) {
    const p = path.join(__dirname, '..', 'dati', filename);
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  }

  const spy = loadOracle('bdrr_spy_oracle.json');
  const qqq = loadOracle('bdrr_qqq_oracle.json');
  const allCandidates = [...spy.candidates, ...qqq.candidates];

  // --- Test 22: QQQ 2026-07-14 explicit exclusion assertion ---
  const qqq0714 = qqq.candidates.find(c => c.candidate_id === 'QQQ_2026-07-14');
  check(!!qqq0714, 'oracle exclusion: QQQ_2026-07-14 candidate must exist in oracle');
  check(
    qqq0714 && qqq0714.trade_plan_oracle_status === 'EXCLUDED',
    `oracle exclusion: QQQ_2026-07-14 trade_plan_oracle_status must be "EXCLUDED", ` +
      `got "${qqq0714 && qqq0714.trade_plan_oracle_status}"`
  );
  check(
    qqq0714 && qqq0714.detection_status === 'VALID',
    'oracle exclusion: QQQ_2026-07-14 detection_status must remain VALID'
  );
  // Detection is VALID, only TradePlan is excluded — confirm the distinction is recorded
  check(
    qqq0714 && typeof qqq0714.trade_plan_oracle_reason === 'string' &&
      qqq0714.trade_plan_oracle_reason.length > 0,
    'oracle exclusion: QQQ_2026-07-14 must carry a non-empty trade_plan_oracle_reason'
  );

  // --- Test 21: parity for eligible candidates ---
  const TICK_SIZE = spy.preset.tick_size; // 0.01

  function pt(price) { return Math.round(price / TICK_SIZE); }

  let parityCount = 0;
  let skippedCount = 0;

  for (const c of allCandidates) {
    const id = c.candidate_id;

    // Skip explicitly excluded TradePlan candidates (test 22 above covers QQQ_2026-07-14)
    if (c.trade_plan_oracle_status === 'EXCLUDED') {
      skippedCount++;
      // Explicitly assert we are skipping, not silently ignoring
      check(id === 'QQQ_2026-07-14',
        `oracle parity: unexpected EXCLUDED candidate ${id} — only QQQ_2026-07-14 expected`);
      continue;
    }

    // Skip INVALID / non-executable candidates (TradePlan must reject them)
    if (c.detection_status === 'INVALID' || c.executable === false) {
      // Verify the function correctly rejects these
      const fakeOk = { status: 'OK', confirmation_candle: null }; // minimal
      // We test the rejection path separately in test 11; here just skip parity.
      skippedCount++;
      continue;
    }

    // Confirm OHLC required
    const conf = c.confirmation;
    if (!conf || conf.o == null || conf.h == null || conf.l == null || conf.c == null) {
      // Candidate lacks OHLC — do not fabricate; report and skip
      check(false,
        `oracle parity: ${id} has detection_status=VALID but missing confirmation OHLC ` +
        '(unexpected — should have been marked EXCLUDED)'
      );
      continue;
    }

    // Build synthetic detectionResult from oracle confirmation OHLC
    const syntheticDR = {
      status: 'OK',
      date: c.date,
      level_price: c.level_price,
      direction: 'LONG',
      confirmation_candle: {
        time:  new Date(),   // timestamp not used in TradePlan computation
        open:  conf.o,
        high:  conf.h,
        low:   conf.l,
        close: conf.c
      },
      geometry: {},
      failed_retests: [],
      failed_retest_count: 0
    };

    const pConfig = {
      direction:           'LONG',
      entry_model:         'CONFIRMATION_CLOSE',
      entry_buffer_ticks:  0,
      stop_buffer_ticks:   0,
      tick_size:           TICK_SIZE
    };

    const result = buildTradePlan(syntheticDR, pConfig);
    check(result.status === 'OK',
      `oracle parity: ${id} — buildTradePlan must succeed, got ${result.status} (${result.failure_code}: ${result.reason})`);
    if (result.status !== 'OK') continue;

    const tp = result.trade_plan;

    // Compute expected integer-tick values from oracle OHLC directly
    const expectedEntry = pt(conf.c);   // CONFIRMATION_CLOSE, buffer=0
    const expectedStop  = pt(conf.l);   // stop_buffer=0
    const expectedRisk  = Math.abs(expectedEntry - expectedStop);
    const expectedR2    = expectedEntry + 2 * expectedRisk;
    const expectedR3    = expectedEntry + 3 * expectedRisk;
    const expectedR4    = expectedEntry + 4 * expectedRisk;

    check(tp.entry_price.ticks === expectedEntry,
      `oracle parity: ${id} entry_price.ticks expected ${expectedEntry}, got ${tp.entry_price.ticks}`);
    check(tp.stop_price.ticks === expectedStop,
      `oracle parity: ${id} stop_price.ticks expected ${expectedStop}, got ${tp.stop_price.ticks}`);
    check(tp.risk.ticks === expectedRisk,
      `oracle parity: ${id} risk.ticks expected ${expectedRisk}, got ${tp.risk.ticks}`);
    check(tp.r2_price.ticks === expectedR2,
      `oracle parity: ${id} r2_price.ticks expected ${expectedR2}, got ${tp.r2_price.ticks}`);
    check(tp.r3_price.ticks === expectedR3,
      `oracle parity: ${id} r3_price.ticks expected ${expectedR3}, got ${tp.r3_price.ticks}`);
    check(tp.r4_price.ticks === expectedR4,
      `oracle parity: ${id} r4_price.ticks expected ${expectedR4}, got ${tp.r4_price.ticks}`);

    // Also verify oracle display entry and stop match engine values
    // (entry/stop are exact in oracle for all verifiable candidates)
    const engineEntryPts = Number((tp.entry_price.ticks * TICK_SIZE).toFixed(2));
    const engineStopPts  = Number((tp.stop_price.ticks  * TICK_SIZE).toFixed(2));
    check(Math.abs(engineEntryPts - c.entry) < 0.005,
      `oracle parity: ${id} engine entry pts (${engineEntryPts}) vs oracle (${c.entry})`);
    check(Math.abs(engineStopPts  - c.stop)  < 0.005,
      `oracle parity: ${id} engine stop  pts (${engineStopPts})  vs oracle (${c.stop})`);

    parityCount++;
  }

  // Sanity: we must have run parity on exactly 6 candidates (3 SPY VALID + 3 QQQ VALID)
  check(parityCount === 6,
    `oracle parity: expected 6 eligible candidates, checked ${parityCount}`);
  // And skipped exactly 2 (SPY_2026-05-05 INVALID + QQQ_2026-07-14 EXCLUDED)
  check(skippedCount === 2,
    `oracle parity: expected 2 skipped candidates, skipped ${skippedCount}`);
})();

// ── Report ────────────────────────────────────────────────────────────────────

console.log('BDRR TradePlan/v1 tests');
console.log('========================');
console.log(`Checks run: ${checks}`);
console.log(`Failures: ${failures.length}`);
if (failures.length) {
  console.log('\nFAILED CHECKS:');
  failures.forEach(f => console.log(' - ' + f));
  console.log('\nRESULT: FAIL');
  process.exitCode = 1;
} else {
  console.log('\nRESULT: PASS');
  process.exitCode = 0;
}
