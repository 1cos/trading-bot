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
 *  11. Invalid detection result is rejected (wrong schema_version, wrong status,
 *      raw findRejection() shape, null, non-object)
 *  12. Missing confirmation_bar is rejected
 *  13. Invalid PriceTicks fields are rejected (non-integer ticks, non-finite,
 *      non-object field, tick_size mismatch)
 *  14. Inconsistent config tick sizes are rejected
 *  15. Negative and non-integer buffers are rejected
 *  16. Unknown entry model is rejected
 *  17. Unsupported SHORT direction is rejected
 *  18. Zero risk is rejected
 *  19. Repeated runs are deeply identical
 *  20. Input objects are not mutated
 *  21. Oracle TradePlan parity for all eligible candidates
 *  22. QQQ 2026-07-14 is explicitly excluded (not rounded or reconstructed)
 *  23. SPY 2026-05-26 full integration: Stage 1–5 → buildDetectionResult →
 *      buildTradePlan — canonical pipeline produces a valid TradePlan/v1
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

// Helper: build a canonical PriceTicks object.
function pt(price, tickSize) {
  tickSize = tickSize || TICK;
  return { ticks: Math.round(price / tickSize), tick_size: tickSize };
}

// A synthetic canonical DetectionResult/v1 whose confirmation_bar matches:
//   high=102.00, low=100.00, open=101.50, close=101.20
// (identical numeric values to the old fixture — only shape changes)
function syntheticDetectionResult(overrides) {
  const base = {
    schema_version: 'DetectionResult/v1',
    result_id:      'aaaaaaaa-0000-4000-8000-000000000001',
    produced_at:    '2026-07-01T13:45:00.000Z',
    status:         'VALID',
    failed_stage:   null,
    failed_rules:   [],
    session: {
      symbol:               'TEST',
      date:                 '2026-07-01',
      market_timezone:      'America/New_York',
      session_open_utc_ms:  new Date('2026-07-01T13:30:00Z').getTime(),
      session_close_utc_ms: new Date('2026-07-01T20:00:00Z').getTime(),
      timeframe_seconds:    300
    },
    preset_id:      'test_preset',
    engine_version: '1.0.0',
    level_price:    pt(101.00),
    level_source:   'ORB_HIGH',
    direction:      'LONG',
    confirmation_bar: {
      bar_utc_ms: new Date('2026-07-01T13:45:00Z').getTime(),
      open:  pt(101.50),
      high:  pt(102.00),
      low:   pt(100.00),
      close: pt(101.20),
      volume: null
    },
    displacement_window: [],
    retest_window:       [],
    failed_retests:      [],
    failed_retest_count: 0
  };
  // Deep-merge overrides at top level only (sufficient for all test cases)
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

// ── 2. CONFIRMATION_CLOSE / zero buffers ──────────────────────────────────────
//    close=101.20 → entry=101.20; low=100.00 → stop=100.00; risk=120 ticks

(function testConfirmationCloseZeroBuffers() {
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'CC_zero: expected OK');
  const tp = r.trade_plan;
  check(tp.entry_price.ticks === 10120, `CC_zero: entry expected 10120, got ${tp.entry_price.ticks}`);
  check(tp.stop_price.ticks  === 10000, `CC_zero: stop expected 10000, got ${tp.stop_price.ticks}`);
  check(tp.risk.ticks        === 120,   `CC_zero: risk expected 120, got ${tp.risk.ticks}`);
})();

// ── 3. CONFIRMATION_CLOSE / non-zero buffers ──────────────────────────────────
//    entry = close(10120) + entry_buf(5) = 10125
//    stop  = low(10000)  - stop_buf(3)  = 9997
//    risk  = 10125 - 9997 = 128

(function testConfirmationCloseNonZeroBuffers() {
  const config = Object.assign({}, BASE_CONFIG, {
    entry_buffer_ticks: 5,
    stop_buffer_ticks:  3
  });
  const r = buildTradePlan(syntheticDetectionResult(), config);
  check(r.status === 'OK', 'CC_nonzero: expected OK');
  const tp = r.trade_plan;
  check(tp.entry_price.ticks === 10125, `CC_nonzero: entry expected 10125, got ${tp.entry_price.ticks}`);
  check(tp.stop_price.ticks  === 9997,  `CC_nonzero: stop expected 9997, got ${tp.stop_price.ticks}`);
  check(tp.risk.ticks        === 128,   `CC_nonzero: risk expected 128, got ${tp.risk.ticks}`);
})();

// ── 4. BREAK_OF_SIGNAL_BAR / zero buffers ─────────────────────────────────────
//    entry = high(10200); stop = low(10000); risk = 200

(function testBreakOfSignalBarZeroBuffers() {
  const config = Object.assign({}, BASE_CONFIG, { entry_model: 'BREAK_OF_SIGNAL_BAR' });
  const r = buildTradePlan(syntheticDetectionResult(), config);
  check(r.status === 'OK', 'BOSB_zero: expected OK');
  const tp = r.trade_plan;
  check(tp.entry_price.ticks === 10200, `BOSB_zero: entry expected 10200, got ${tp.entry_price.ticks}`);
  check(tp.stop_price.ticks  === 10000, `BOSB_zero: stop expected 10000, got ${tp.stop_price.ticks}`);
  check(tp.risk.ticks        === 200,   `BOSB_zero: risk expected 200, got ${tp.risk.ticks}`);
})();

// ── 5. BREAK_OF_SIGNAL_BAR / non-zero buffers ────────────────────────────────
//    entry = high(10200) + 2 = 10202; stop = low(10000) - 1 = 9999; risk = 203

(function testBreakOfSignalBarNonZeroBuffers() {
  const config = Object.assign({}, BASE_CONFIG, {
    entry_model:         'BREAK_OF_SIGNAL_BAR',
    entry_buffer_ticks:  2,
    stop_buffer_ticks:   1
  });
  const r = buildTradePlan(syntheticDetectionResult(), config);
  check(r.status === 'OK', 'BOSB_nonzero: expected OK');
  const tp = r.trade_plan;
  check(tp.entry_price.ticks === 10202, `BOSB_nonzero: entry expected 10202, got ${tp.entry_price.ticks}`);
  check(tp.stop_price.ticks  === 9999,  `BOSB_nonzero: stop expected 9999, got ${tp.stop_price.ticks}`);
  check(tp.risk.ticks        === 203,   `BOSB_nonzero: risk expected 203, got ${tp.risk.ticks}`);
})();

// ── 6. LONG stop is below entry ───────────────────────────────────────────────

(function testLongStopBelowEntry() {
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'stopBelowEntry: expected OK');
  check(
    r.trade_plan.stop_price.ticks < r.trade_plan.entry_price.ticks,
    'stopBelowEntry: stop must be below entry for LONG'
  );
})();

// ── 7. risk = abs(entry − stop) for a range of candles ───────────────────────

(function testRiskCalculation() {
  const cases = [
    { high: 110.00, low: 100.00, close: 108.00 },
    { high: 205.50, low: 200.00, close: 203.25 },
    { high: 510.00, low: 505.00, close: 509.50 },
  ];
  for (const c of cases) {
    const dr = syntheticDetectionResult({
      confirmation_bar: {
        bar_utc_ms: 0,
        open:  pt(c.close),
        high:  pt(c.high),
        low:   pt(c.low),
        close: pt(c.close),
        volume: null
      }
    });
    const r = buildTradePlan(dr, BASE_CONFIG);
    if (r.status !== 'OK') continue;
    const tp = r.trade_plan;
    check(
      tp.risk.ticks === Math.abs(tp.entry_price.ticks - tp.stop_price.ticks),
      `risk: expected risk=abs(entry-stop) for close=${c.close}`
    );
  }
})();

// ── 8. Exact 2R, 3R, 4R targets ──────────────────────────────────────────────

(function testTargets() {
  // CC: entry=10120, stop=10000, risk=120
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'targets: expected OK');
  const tp = r.trade_plan;
  check(tp.r2_price.ticks === 10120 + 2*120, `targets: r2 expected ${10120+2*120}, got ${tp.r2_price.ticks}`);
  check(tp.r3_price.ticks === 10120 + 3*120, `targets: r3 expected ${10120+3*120}, got ${tp.r3_price.ticks}`);
  check(tp.r4_price.ticks === 10120 + 4*120, `targets: r4 expected ${10120+4*120}, got ${tp.r4_price.ticks}`);
})();

(function testTargetsBOSB() {
  // BOSB: entry=10200, stop=10000, risk=200
  const config = Object.assign({}, BASE_CONFIG, { entry_model: 'BREAK_OF_SIGNAL_BAR' });
  const r2 = buildTradePlan(syntheticDetectionResult(), config);
  check(r2.status === 'OK', 'targets BOSB: expected OK');
  const tp = r2.trade_plan;
  check(tp.r2_price.ticks === 10200 + 2*200, `targets BOSB: r2 expected ${10200+2*200}, got ${tp.r2_price.ticks}`);
  check(tp.r3_price.ticks === 10200 + 3*200, `targets BOSB: r3 expected ${10200+3*200}, got ${tp.r3_price.ticks}`);
  check(tp.r4_price.ticks === 10200 + 4*200, `targets BOSB: r4 expected ${10200+4*200}, got ${tp.r4_price.ticks}`);
})();

// ── 9. Every stored tick value is an integer ──────────────────────────────────

(function testIntegerTicks() {
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'integerTicks: expected OK');
  const tp = r.trade_plan;
  for (const field of ['entry_price','stop_price','risk','r2_price','r3_price','r4_price']) {
    check(
      Number.isInteger(tp[field].ticks),
      `integerTicks: ${field}.ticks must be an integer, got ${tp[field].ticks}`
    );
  }
})();

// ── 10. tick_size is preserved consistently ───────────────────────────────────

(function testTickSizePreserved() {
  const r = buildTradePlan(syntheticDetectionResult(), BASE_CONFIG);
  check(r.status === 'OK', 'tickSize: expected OK');
  const tp = r.trade_plan;
  check(tp.tick_size === TICK, `tickSize: trade_plan.tick_size expected ${TICK}, got ${tp.tick_size}`);
  for (const field of ['entry_price','stop_price','risk','r2_price','r3_price','r4_price']) {
    check(
      tp[field].tick_size === TICK,
      `tickSize: ${field}.tick_size expected ${TICK}, got ${tp[field].tick_size}`
    );
  }
})();

// ── 11. Invalid detection result is rejected ──────────────────────────────────

(function testInvalidDetectionRejected() {
  // Wrong schema_version
  const r1 = buildTradePlan(
    syntheticDetectionResult({ schema_version: 'DetectionResult/v0' }),
    BASE_CONFIG
  );
  check(r1.status === 'FAILED', 'invalidDetection: wrong schema_version must be rejected');
  check(r1.failure_code === 'INVALID_DETECTION_RESULT',
    `invalidDetection: wrong schema_version gave ${r1.failure_code}`);

  // Missing schema_version
  const dr2 = syntheticDetectionResult();
  delete dr2.schema_version;
  const r2 = buildTradePlan(dr2, BASE_CONFIG);
  check(r2.status === 'FAILED', 'invalidDetection: missing schema_version must be rejected');
  check(r2.failure_code === 'INVALID_DETECTION_RESULT',
    `invalidDetection: missing schema_version gave ${r2.failure_code}`);

  // status: 'INVALID' (failed detection)
  const r3 = buildTradePlan(
    syntheticDetectionResult({ status: 'INVALID', failed_stage: 'NO_QUALIFYING_REJECTION_CANDLE' }),
    BASE_CONFIG
  );
  check(r3.status === 'FAILED', 'invalidDetection: INVALID status must be rejected');
  check(r3.failure_code === 'INVALID_DETECTION_RESULT',
    `invalidDetection: INVALID status gave ${r3.failure_code}`);

  // Old raw findRejection() shape — status: 'OK', confirmation_candle (not 'VALID', no schema_version)
  const rawRej = {
    status: 'OK',
    date: '2026-05-26',
    level_price: 750.44,
    confirmation_candle: { time: new Date(), open: 750.77, high: 750.97, low: 750.37, close: 750.89 },
    geometry: {},
    failed_retests: []
  };
  const r4 = buildTradePlan(rawRej, BASE_CONFIG);
  check(r4.status === 'FAILED', 'invalidDetection: raw findRejection() output must be rejected');
  check(r4.failure_code === 'INVALID_DETECTION_RESULT',
    `invalidDetection: raw output gave ${r4.failure_code}`);

  // null input
  const r5 = buildTradePlan(null, BASE_CONFIG);
  check(r5.status === 'FAILED', 'invalidDetection: null must be rejected');
  check(r5.failure_code === 'INVALID_DETECTION_RESULT',
    `invalidDetection: null gave ${r5.failure_code}`);

  // non-object
  const r6 = buildTradePlan('not-an-object', BASE_CONFIG);
  check(r6.status === 'FAILED', 'invalidDetection: string must be rejected');
})();

// ── 12. Missing confirmation_bar is rejected ──────────────────────────────────

(function testMissingConfirmationBar() {
  const r = buildTradePlan(syntheticDetectionResult({ confirmation_bar: null }), BASE_CONFIG);
  check(r.status === 'FAILED', 'missingBar: null confirmation_bar must be rejected');
  check(r.failure_code === 'MISSING_CONFIRMATION_BAR',
    `missingBar: expected MISSING_CONFIRMATION_BAR, got ${r.failure_code}`);

  const dr2 = syntheticDetectionResult();
  delete dr2.confirmation_bar;
  const r2 = buildTradePlan(dr2, BASE_CONFIG);
  check(r2.status === 'FAILED', 'missingBar: missing confirmation_bar must be rejected');
  check(r2.failure_code === 'MISSING_CONFIRMATION_BAR',
    `missingBar: missing field gave ${r2.failure_code}`);
})();

// ── 13. Invalid PriceTicks fields are rejected ────────────────────────────────

(function testInvalidPriceTicksFields() {
  // Non-object field (plain float instead of PriceTicks)
  const dr1 = syntheticDetectionResult({
    confirmation_bar: { bar_utc_ms: 0, open: pt(101.50), high: pt(102.00), low: pt(100.00), close: 101.20, volume: null }
  });
  const r1 = buildTradePlan(dr1, BASE_CONFIG);
  check(r1.status === 'FAILED', 'invalidPT: plain float close must be rejected');
  check(r1.failure_code === 'INVALID_TICK_VALUE',
    `invalidPT: plain float gave ${r1.failure_code}`);

  // Non-integer ticks
  const dr2 = syntheticDetectionResult({
    confirmation_bar: { bar_utc_ms: 0, open: pt(101.50), high: pt(102.00), low: pt(100.00), close: { ticks: 101.2, tick_size: TICK }, volume: null }
  });
  const r2 = buildTradePlan(dr2, BASE_CONFIG);
  check(r2.status === 'FAILED', 'invalidPT: non-integer ticks must be rejected');
  check(r2.failure_code === 'INVALID_TICK_VALUE',
    `invalidPT: non-integer ticks gave ${r2.failure_code}`);

  // tick_size mismatch on one field
  const dr3 = syntheticDetectionResult({
    confirmation_bar: { bar_utc_ms: 0, open: pt(101.50), high: pt(102.00), low: pt(100.00), close: { ticks: 10120, tick_size: 0.05 }, volume: null }
  });
  const r3 = buildTradePlan(dr3, BASE_CONFIG);
  check(r3.status === 'FAILED', 'invalidPT: mismatched tick_size must be rejected');
  check(r3.failure_code === 'TICK_SIZE_MISMATCH',
    `invalidPT: tick_size mismatch gave ${r3.failure_code}`);

  // null field
  const dr4 = syntheticDetectionResult({
    confirmation_bar: { bar_utc_ms: 0, open: pt(101.50), high: pt(102.00), low: null, close: pt(101.20), volume: null }
  });
  const r4 = buildTradePlan(dr4, BASE_CONFIG);
  check(r4.status === 'FAILED', 'invalidPT: null field must be rejected');
  check(r4.failure_code === 'INVALID_TICK_VALUE',
    `invalidPT: null field gave ${r4.failure_code}`);
})();

// ── 14. Inconsistent config tick sizes are rejected ───────────────────────────

(function testTickSizeMismatch() {
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

// ── 15. Negative and non-integer buffers are rejected ─────────────────────────

(function testInvalidBuffers() {
  const r1 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { entry_buffer_ticks: -1 }));
  check(r1.status === 'FAILED', 'buffers: negative entry_buffer must be rejected');
  check(r1.failure_code === 'INVALID_BUFFER',
    `buffers: negative entry_buffer gave ${r1.failure_code}`);

  const r2 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { stop_buffer_ticks: -5 }));
  check(r2.status === 'FAILED', 'buffers: negative stop_buffer must be rejected');
  check(r2.failure_code === 'INVALID_BUFFER',
    `buffers: negative stop_buffer gave ${r2.failure_code}`);

  const r3 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { entry_buffer_ticks: 1.5 }));
  check(r3.status === 'FAILED', 'buffers: float entry_buffer must be rejected');
  check(r3.failure_code === 'INVALID_BUFFER',
    `buffers: float entry_buffer gave ${r3.failure_code}`);

  const r4 = buildTradePlan(syntheticDetectionResult(),
    Object.assign({}, BASE_CONFIG, { stop_buffer_ticks: 0.5 }));
  check(r4.status === 'FAILED', 'buffers: float stop_buffer must be rejected');
  check(r4.failure_code === 'INVALID_BUFFER',
    `buffers: float stop_buffer gave ${r4.failure_code}`);

  const r5 = buildTradePlan(syntheticDetectionResult(),
    { direction: 'LONG', entry_model: 'CONFIRMATION_CLOSE',
      tick_size: TICK, stop_buffer_ticks: 0
      /* entry_buffer_ticks missing */ });
  check(r5.status === 'FAILED', 'buffers: missing entry_buffer must be rejected');
  check(r5.failure_code === 'INVALID_BUFFER',
    `buffers: missing entry_buffer gave ${r5.failure_code}`);
})();

// ── 16. Unknown entry model is rejected ───────────────────────────────────────

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

// ── 17. Unsupported SHORT direction is rejected ───────────────────────────────

(function testShortDirectionRejected() {
  const r = buildTradePlan(
    syntheticDetectionResult({ direction: 'SHORT' }),
    Object.assign({}, BASE_CONFIG, { direction: 'SHORT' })
  );
  check(r.status === 'FAILED', 'short: expected FAILED');
  check(r.failure_code === 'UNSUPPORTED_DIRECTION',
    `short: expected UNSUPPORTED_DIRECTION, got ${r.failure_code}`);

  let threw = false;
  try {
    buildTradePlan(
      syntheticDetectionResult({ direction: 'SHORT' }),
      Object.assign({}, BASE_CONFIG, { direction: 'SHORT' })
    );
  } catch (e) { threw = true; }
  check(!threw, 'short: must not throw — must return structured failure');
})();

// ── 18. Zero risk is rejected ─────────────────────────────────────────────────

(function testZeroRiskRejected() {
  // Doji: open=high=low=close=101.00
  const drDoji = syntheticDetectionResult({
    confirmation_bar: {
      bar_utc_ms: 0,
      open:  pt(101.00),
      high:  pt(101.00),
      low:   pt(101.00),
      close: pt(101.00),
      volume: null
    }
  });
  const r = buildTradePlan(drDoji, BASE_CONFIG);
  check(r.status === 'FAILED', 'zeroRisk: doji (entry==stop) must be rejected');
  check(r.failure_code === 'INVALID_RISK',
    `zeroRisk: doji gave ${r.failure_code}`);

  const r2 = buildTradePlan(drDoji, Object.assign({}, BASE_CONFIG, { entry_model: 'BREAK_OF_SIGNAL_BAR' }));
  check(r2.status === 'FAILED', 'zeroRisk: BOSB doji must be rejected');
  check(r2.failure_code === 'INVALID_RISK',
    `zeroRisk: BOSB doji gave ${r2.failure_code}`);
})();

// ── 19. Repeated runs are deeply identical ────────────────────────────────────

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

// ── 20. Input objects are not mutated ─────────────────────────────────────────

(function testNoMutation() {
  const dr = syntheticDetectionResult();
  const config = Object.assign({}, BASE_CONFIG);
  const configCopy = Object.assign({}, config);

  // Snapshot canonical DR fields before call
  const origStatus      = dr.status;
  const origSchema      = dr.schema_version;
  const origLevelTicks  = dr.level_price.ticks;
  const origCloseTicks  = dr.confirmation_bar.close.ticks;
  const origHighTicks   = dr.confirmation_bar.high.ticks;
  const origLowTicks    = dr.confirmation_bar.low.ticks;

  buildTradePlan(dr, config);

  check(deepEqual(config, configCopy), 'mutation: config must not be mutated');
  check(dr.status                     === origStatus,     'mutation: dr.status mutated');
  check(dr.schema_version             === origSchema,     'mutation: dr.schema_version mutated');
  check(dr.level_price.ticks          === origLevelTicks, 'mutation: dr.level_price.ticks mutated');
  check(dr.confirmation_bar.close.ticks === origCloseTicks, 'mutation: confirmation_bar.close.ticks mutated');
  check(dr.confirmation_bar.high.ticks  === origHighTicks,  'mutation: confirmation_bar.high.ticks mutated');
  check(dr.confirmation_bar.low.ticks   === origLowTicks,   'mutation: confirmation_bar.low.ticks mutated');
})();

// ── 21 & 22. Oracle parity ────────────────────────────────────────────────────
//
// For each eligible oracle candidate (detection_status=VALID, not EXCLUDED):
//   Build a synthetic DetectionResult/v1 from oracle confirmation OHLC.
//   Call buildTradePlan with zero buffers and CONFIRMATION_CLOSE.
//   Assert entry_price.ticks, stop_price.ticks, risk.ticks, r2/r3/r4.ticks
//   match integer-tick arithmetic (not oracle display values which carry rounding).
//
// Note: oracle display targets for SPY_2026-05-26 have 0.01–0.02 drift vs
// integer-tick arithmetic (documented in oracle's unresolved_fields).
// Parity tests compare correct integer-tick values, not display values.

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
  check(
    qqq0714 && typeof qqq0714.trade_plan_oracle_reason === 'string' &&
      qqq0714.trade_plan_oracle_reason.length > 0,
    'oracle exclusion: QQQ_2026-07-14 must carry a non-empty trade_plan_oracle_reason'
  );

  // --- Test 21: parity for eligible candidates ---
  const TICK_SIZE = spy.preset.tick_size; // 0.01

  function ptOracle(price) { return Math.round(price / TICK_SIZE); }

  let parityCount  = 0;
  let skippedCount = 0;

  for (const c of allCandidates) {
    const id = c.candidate_id;

    if (c.trade_plan_oracle_status === 'EXCLUDED') {
      skippedCount++;
      check(id === 'QQQ_2026-07-14',
        `oracle parity: unexpected EXCLUDED candidate ${id} — only QQQ_2026-07-14 expected`);
      continue;
    }

    if (c.detection_status === 'INVALID' || c.executable === false) {
      skippedCount++;
      continue;
    }

    const conf = c.confirmation;
    if (!conf || conf.o == null || conf.h == null || conf.l == null || conf.c == null) {
      check(false,
        `oracle parity: ${id} has detection_status=VALID but missing confirmation OHLC`);
      continue;
    }

    // Build canonical DetectionResult/v1 from oracle OHLC
    const canonicalDR = {
      schema_version: 'DetectionResult/v1',
      result_id:      'aaaaaaaa-0000-4000-8000-000000000000',
      produced_at:    new Date().toISOString(),
      status:         'VALID',
      failed_stage:   null,
      failed_rules:   [],
      session: {
        symbol:               id.split('_')[0],
        date:                 c.date,
        market_timezone:      'America/New_York',
        session_open_utc_ms:  0,
        session_close_utc_ms: 1,
        timeframe_seconds:    300
      },
      preset_id:      'oracle_parity',
      engine_version: '1.0.0',
      level_price:    { ticks: ptOracle(c.level_price), tick_size: TICK_SIZE },
      level_source:   'ORB_HIGH',
      direction:      'LONG',
      confirmation_bar: {
        bar_utc_ms: 0,
        open:   { ticks: ptOracle(conf.o), tick_size: TICK_SIZE },
        high:   { ticks: ptOracle(conf.h), tick_size: TICK_SIZE },
        low:    { ticks: ptOracle(conf.l), tick_size: TICK_SIZE },
        close:  { ticks: ptOracle(conf.c), tick_size: TICK_SIZE },
        volume: null
      },
      displacement_window: [],
      retest_window:       [],
      failed_retests:      [],
      failed_retest_count: 0
    };

    const pConfig = {
      direction:           'LONG',
      entry_model:         'CONFIRMATION_CLOSE',
      entry_buffer_ticks:  0,
      stop_buffer_ticks:   0,
      tick_size:           TICK_SIZE
    };

    const result = buildTradePlan(canonicalDR, pConfig);
    check(result.status === 'OK',
      `oracle parity: ${id} — buildTradePlan must succeed, got ${result.status} ` +
      `(${result.failure_code}: ${result.reason})`);
    if (result.status !== 'OK') continue;

    const tp = result.trade_plan;

    const expectedEntry = ptOracle(conf.c);
    const expectedStop  = ptOracle(conf.l);
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

    const engineEntryPts = Number((tp.entry_price.ticks * TICK_SIZE).toFixed(2));
    const engineStopPts  = Number((tp.stop_price.ticks  * TICK_SIZE).toFixed(2));
    check(Math.abs(engineEntryPts - c.entry) < 0.005,
      `oracle parity: ${id} engine entry pts (${engineEntryPts}) vs oracle (${c.entry})`);
    check(Math.abs(engineStopPts  - c.stop)  < 0.005,
      `oracle parity: ${id} engine stop  pts (${engineStopPts})  vs oracle (${c.stop})`);

    parityCount++;
  }

  check(parityCount === 6,
    `oracle parity: expected 6 eligible candidates, checked ${parityCount}`);
  check(skippedCount === 2,
    `oracle parity: expected 2 skipped candidates, skipped ${skippedCount}`);
})();

// ── 23. SPY 2026-05-26 full integration test ──────────────────────────────────
//
// Stage 1–5 → buildDetectionResult → buildTradePlan
// Verifies the canonical pipeline end-to-end with real market data.

(function testSPYIntegration() {
  const { buildSessionContext, buildORB, findBreak,
          findDisplacement, findRetestWindow, findRejection } = require('./bdrr_engine.js');
  const { buildDetectionResult } = require('./bdrr_detection_result.js');

  // Load and parse SPY CSV
  const csvPath = path.join(__dirname, '..', 'dati', 'SPY_5m.csv');
  const csv = fs.readFileSync(csvPath, 'utf8');
  const lines = csv.split('\n');
  const allCandles = [];
  for (let i = 3; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const parts = line.split(',');
    if (parts.length < 5) continue;
    const close = parseFloat(parts[1]), high = parseFloat(parts[2]);
    const low   = parseFloat(parts[3]), open = parseFloat(parts[4]);
    if (isNaN(close)) continue;
    allCandles.push({ time: new Date(parts[0].replace(' ', 'T')), open, high, low, close });
  }

  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit'
  });
  const sessions = {};
  for (const c of allCandles) {
    const d = fmt.format(c.time);
    if (!sessions[d]) sessions[d] = [];
    sessions[d].push(c);
  }

  const TARGET_DATE = '2026-05-26';
  const candles = sessions[TARGET_DATE];

  check(Array.isArray(candles) && candles.length > 0,
    'integration: SPY 2026-05-26 candles must be available');
  if (!candles || !candles.length) return;

  const config = {
    timeframe_minutes: 5, timezone: 'America/New_York',
    session_open: '09:30', orb_start: 'session_open',
    orb_duration_minutes: 5, level_source: 'ORB_HIGH',
    direction: 'LONG', tick_size: 0.01,
    min_displacement_ticks: null, min_penetration_ticks: null,
    min_close_beyond_level_ticks: null
  };

  // Stage 1–5
  const ctx    = buildSessionContext(candles, config);
  const orb    = buildORB(ctx.candles, ctx, config);
  const brk    = findBreak(ctx.candles, orb, config);
  const disp   = findDisplacement(ctx.candles, orb, brk, config);
  const retest = findRetestWindow(ctx.candles, orb, brk, disp, config);
  const rej    = findRejection(ctx.candles, orb, brk, disp, retest, config);

  check(rej.status === 'OK', 'integration: Stage 1–5 must produce status OK');

  const metadata = {
    tick_size: 0.01,
    preset_id: 'bdrr_spy_orb_high_v1',
    engine_version: '1.0.0',
    session: {
      symbol:               'SPY',
      date:                 TARGET_DATE,
      market_timezone:      'America/New_York',
      session_open_utc_ms:  new Date('2026-05-26T13:30:00.000Z').getTime(),
      session_close_utc_ms: new Date('2026-05-26T20:00:00.000Z').getTime(),
      timeframe_seconds:    300
    }
  };

  // buildDetectionResult
  const drResult = buildDetectionResult(
    { orb, breakResult: brk, dispResult: disp, retestResult: retest, rejResult: rej },
    metadata
  );
  check(drResult.status === 'OK', 'integration: buildDetectionResult must succeed');
  if (drResult.status !== 'OK') return;

  const dr = drResult.detection_result;
  check(dr.schema_version === 'DetectionResult/v1', 'integration: schema_version correct');
  check(dr.status === 'VALID', 'integration: canonical status must be VALID');

  // buildTradePlan — canonical pipeline
  const tpConfig = {
    direction: 'LONG', entry_model: 'CONFIRMATION_CLOSE',
    entry_buffer_ticks: 0, stop_buffer_ticks: 0, tick_size: 0.01
  };
  const tpResult = buildTradePlan(dr, tpConfig);
  check(tpResult.status === 'OK',
    `integration: buildTradePlan must succeed; got ${tpResult.status} ` +
    `(${tpResult.failure_code}: ${tpResult.reason})`);
  if (tpResult.status !== 'OK') return;

  const tp = tpResult.trade_plan;

  // Verify TradePlan/v1 schema
  check(tp.schema_version === 'TradePlan/v1', 'integration: TradePlan schema_version correct');
  check(tp.entry_model === 'CONFIRMATION_CLOSE', 'integration: entry_model preserved');
  check(Number.isInteger(tp.entry_price.ticks), 'integration: entry_price.ticks is integer');
  check(Number.isInteger(tp.stop_price.ticks),  'integration: stop_price.ticks is integer');
  check(Number.isInteger(tp.risk.ticks),         'integration: risk.ticks is integer');
  check(tp.risk.ticks > 0, 'integration: risk must be positive');
  check(tp.entry_price.ticks > tp.stop_price.ticks, 'integration: LONG entry > stop');

  // Verify oracle values (SPY 2026-05-26: entry=750.89, stop=750.36)
  // confirmation_bar.close.ticks = 75089; low.ticks = 75036
  check(tp.entry_price.ticks === 75089,
    `integration: entry_price.ticks expected 75089 ($750.89), got ${tp.entry_price.ticks}`);
  check(tp.stop_price.ticks === 75036,
    `integration: stop_price.ticks expected 75036 ($750.36), got ${tp.stop_price.ticks}`);
  check(tp.risk.ticks === 53,
    `integration: risk.ticks expected 53 ($0.53), got ${tp.risk.ticks}`);
  check(tp.r2_price.ticks === 75089 + 2*53,
    `integration: r2 expected ${75089+2*53}, got ${tp.r2_price.ticks}`);
  check(tp.r3_price.ticks === 75089 + 3*53,
    `integration: r3 expected ${75089+3*53}, got ${tp.r3_price.ticks}`);
  check(tp.r4_price.ticks === 75089 + 4*53,
    `integration: r4 expected ${75089+4*53}, got ${tp.r4_price.ticks}`);

  // Canonical DR must not have been mutated by buildTradePlan
  check(dr.status         === 'VALID',           'integration: dr.status not mutated');
  check(dr.schema_version === 'DetectionResult/v1', 'integration: dr.schema_version not mutated');
  check(dr.confirmation_bar.close.ticks === 75089, 'integration: confirmation_bar.close.ticks not mutated');

  // Reject old raw findRejection() shape when passed to the migrated buildTradePlan
  const rawRejShape = {
    status: 'OK',
    level_price: orb.level_price,
    confirmation_candle: rej.confirmation_candle,
    geometry: rej.geometry,
    failed_retests: rej.failed_retests
  };
  const rawRejected = buildTradePlan(rawRejShape, tpConfig);
  check(rawRejected.status === 'FAILED',
    'integration: raw findRejection() output must be rejected by migrated buildTradePlan');
  check(rawRejected.failure_code === 'INVALID_DETECTION_RESULT',
    `integration: raw shape rejection code expected INVALID_DETECTION_RESULT, got ${rawRejected.failure_code}`);
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
