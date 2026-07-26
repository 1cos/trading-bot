/**
 * test_bdrr_detection_result.js
 *
 * Tests for estrategie/bdrr_detection_result.js — DetectionResult/v1 adapter.
 *
 * Coverage:
 *  1.  Exact DetectionResult/v1 schema_version
 *  2.  Valid UUID v4 result_id
 *  3.  Valid ISO 8601 UTC produced_at (not derived from bar_utc_ms — INV-D-10)
 *  4.  Runtime OK  → canonical VALID
 *  5.  Runtime FAILED → canonical INVALID
 *  6.  VALID result: failed_stage = null, failed_rules = []  (INV-D-02)
 *  7.  INVALID result: failed_stage ≠ null  (INV-D-03)
 *  8.  level_price is a PriceTicks object with correct ticks and tick_size (INV-D-04)
 *  9.  Tick-size consistency: all price fields carry the same tick_size
 * 10.  Canonical SessionMetadata preserved exactly
 * 11.  preset_id and engine_version preserved
 * 12.  level_source and direction preserved from orb
 * 13.  break_bar built correctly from breakResult
 * 14.  directional_break_distance ticks preserved
 * 15.  displacement_window is always an array (never null)
 * 16.  displacement_bar_count correct
 * 17.  displacement_pts ticks correct
 * 18.  displacement_pct is a Rational (numerator / denominator = displacement / level)
 * 19.  rejection_side_clearance_by_bar length == displacement_window length (INV-D-05)
 * 20.  retest_window is always an array (never null)
 * 21.  failed_retests is always an array (never null)
 * 22.  failed_retest_count matches failed_retests length
 * 23.  retest_closest_approach.ticks >= 0  (INV-D-11)
 * 24.  retest_penetration_through_level.ticks >= 0  (INV-D-12)
 * 25.  confirmation_bar built correctly from rejection candle (INV-D-08)
 * 26.  confirmation_rej_wick, body, close_location are canonical Rationals
 * 27.  confirmation_penetration ticks >= 0  (INV-D-14)
 * 28.  bars_break_to_first_retest computed correctly
 * 29.  bars_break_to_confirmation computed correctly
 * 30.  No mutation of stageOutputs inputs
 * 31.  No mutation of metadata input
 * 32.  Output object is frozen (immutable)
 * 33.  null stageOutputs is rejected
 * 34.  Missing rejResult is rejected
 * 35.  Unknown rejResult.status is rejected
 * 36.  null metadata is rejected
 * 37.  Missing tick_size rejected
 * 38.  Non-positive tick_size rejected
 * 39.  Missing preset_id rejected
 * 40.  Missing engine_version rejected
 * 41.  Missing session rejected
 * 42.  Missing session.symbol rejected
 * 43.  Missing session.date rejected
 * 44.  Invalid session.date format rejected
 * 45.  Missing session.session_open_utc_ms rejected
 * 46.  Missing session.timeframe_seconds rejected
 * 47.  Deterministic validation: same inputs yield same structural shape
 * 48.  result_id is unique across calls
 * 49.  produced_at advances monotonically (or equals) across calls
 * 50.  VALID with no orb/break/disp/retest: displacement_window/retest_window = []
 * 51.  INVALID with no orb: level_price = null, level_source = null
 * 52.  retest_displacement_retracement_pct is null when displacement_pts is null (INV-D-15a)
 *
 * Run: node estrategie/test_bdrr_detection_result.js
 */

'use strict';

const { buildDetectionResult } = require('./bdrr_detection_result.js');

// ── Test harness ──────────────────────────────────────────────────────────────

let checks   = 0;
let failures = [];

function check(cond, msg) {
  checks++;
  if (!cond) failures.push(msg);
}

function isUUIDv4(s) {
  return typeof s === 'string' &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(s);
}

function isISO8601UTC(s) {
  if (typeof s !== 'string') return false;
  // Must end with Z and parse as a valid date
  return s.endsWith('Z') && !isNaN(Date.parse(s));
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

const TICK = 0.01;

// A canonical Date used as a candle timestamp — bar_utc_ms
const BAR_TIME = new Date('2026-05-26T14:05:00.000Z'); // 10:05 ET = 14:05 UTC

function makeCandle(time, open, high, low, close) {
  return { time, open, high, low, close };
}

// Confirmation candle matching SPY_2026-05-26 geometry
// o=750.77, h=750.97, l=750.37, c=750.89 → rej_wick≈0.67, body≈0.20, close_loc≈0.87
const CONF_CANDLE = makeCandle(BAR_TIME, 750.77, 750.97, 750.37, 750.89);
const CONF_GEOMETRY = {
  range_ticks:                    60,
  body_ticks:                     12,
  rejection_wick_ticks:           40,
  opposite_wick_ticks:             8,
  rejection_wick_ratio:           40 / 60,       // 0.6667
  body_ratio:                     12 / 60,       // 0.20
  favorable_close_location:       52 / 60,       // 0.8667
  opposite_wick_ratio:             8 / 60,       // 0.1333
  penetration_through_level_ticks: 7,            // 750.44 - 750.37 = 7 ticks
  penetration_through_level_points: 0.07,
  close_beyond_level_ticks:       45,            // 750.89 - 750.44 = 45 ticks
  close_beyond_level_points:       0.45
};

// Level 750.44 → 75044 ticks at $0.01
const LEVEL_PRICE = 750.44;

function makeORB() {
  return {
    status: 'OK',
    date: '2026-05-26',
    orb_candle_index: 0,
    orb_candle: makeCandle(new Date('2026-05-26T13:30:00.000Z'), 750.40, 750.80, 750.30, 750.44),
    orb_high: 750.80,
    orb_low: 750.30,
    level_source: 'ORB_HIGH',
    level_price: LEVEL_PRICE,
    level_price_ticks: 75044,
    direction: 'LONG'
  };
}

function makeBreakResult() {
  return {
    status: 'OK',
    date: '2026-05-26',
    break_candle_index: 2,
    break_candle: makeCandle(new Date('2026-05-26T13:40:00.000Z'), 750.45, 750.70, 750.44, 750.63),
    break_timestamp: new Date('2026-05-26T13:40:00.000Z'),
    directional_break_distance: { points: 0.19, ticks: 19 }
  };
}

function makeDispResult() {
  const dispCandle = makeCandle(new Date('2026-05-26T13:45:00.000Z'), 750.65, 751.12, 750.64, 750.98);
  return {
    status: 'OK',
    date: '2026-05-26',
    level_price: LEVEL_PRICE,
    break_candle_index: 2,
    displacement_start_index: 3,
    displacement_end_index: 3,
    displacement_bar_count: 1,
    displacement_window: [dispCandle],
    max_favorable_high: 751.12,
    displacement_distance: { points: 0.68, ticks: 68 },
    first_retest_contact_index: 4,
    first_retest_contact_candle: makeCandle(new Date('2026-05-26T13:50:00.000Z'), 750.98, 751.04, 749.58, 750.14),
    first_retest_contact_timestamp: new Date('2026-05-26T13:50:00.000Z')
  };
}

function makeRetestResult() {
  const r1 = makeCandle(new Date('2026-05-26T13:50:00.000Z'), 750.98, 751.04, 749.58, 750.14);
  const r2 = makeCandle(new Date('2026-05-26T13:55:00.000Z'), 750.16, 750.78, 750.09, 750.78);
  return {
    status: 'OK',
    date: '2026-05-26',
    level_price: LEVEL_PRICE,
    retest_start_index: 4,
    retest_start_timestamp: new Date('2026-05-26T13:50:00.000Z'),
    retest_window_start_index: 4,
    retest_window_end_index: 16,
    retest_window: [r1, r2],
    retest_contacts: [
      {
        candle_index: 4,
        candle: r1,
        timestamp: r1.time,
        closest_directional_position_ticks: -86,   // 74958 - 75044
        penetration_through_level_ticks: 86,
        penetration_through_level_points: 0.86,
        displacement_retracement_pct: 86 / 68
      },
      {
        candle_index: 5,
        candle: r2,
        timestamp: r2.time,
        closest_directional_position_ticks: -35,   // 75009 - 75044
        penetration_through_level_ticks: 35,
        penetration_through_level_points: 0.35,
        displacement_retracement_pct: 35 / 68
      }
    ],
    retest_contact_count: 2
  };
}

function makeRejResult(overrides) {
  const base = {
    status: 'OK',
    date: '2026-05-26',
    level_price: LEVEL_PRICE,
    confirmation_candle_index: 17,
    confirmation_candle: CONF_CANDLE,
    confirmation_timestamp: BAR_TIME,
    geometry: CONF_GEOMETRY,
    failed_retests: [
      {
        candle_index: 4,
        candle: makeCandle(new Date('2026-05-26T13:50:00.000Z'), 750.98, 751.04, 749.58, 750.14),
        timestamp: new Date('2026-05-26T13:50:00.000Z'),
        geometry: { rejection_wick_ratio: 0.38, body_ratio: 0.58, favorable_close_location: 0.38 },
        failed_rules: ['REJECTION_WICK_RATIO_TOO_LOW', 'BODY_RATIO_TOO_HIGH', 'FAVORABLE_CLOSE_LOCATION_TOO_LOW']
      },
      {
        candle_index: 5,
        candle: makeCandle(new Date('2026-05-26T13:55:00.000Z'), 750.16, 750.78, 750.09, 750.78),
        timestamp: new Date('2026-05-26T13:55:00.000Z'),
        geometry: { rejection_wick_ratio: 0.09, body_ratio: 0.91, favorable_close_location: null },
        failed_rules: ['REJECTION_WICK_RATIO_TOO_LOW', 'BODY_RATIO_TOO_HIGH']
      }
    ],
    failed_retest_count: 2
  };
  return Object.assign({}, base, overrides);
}

function makeStageOutputs(overrides) {
  const base = {
    orb:          makeORB(),
    breakResult:  makeBreakResult(),
    dispResult:   makeDispResult(),
    retestResult: makeRetestResult(),
    rejResult:    makeRejResult()
  };
  return Object.assign({}, base, overrides);
}

function makeMetadata(overrides) {
  const base = {
    tick_size:      TICK,
    preset_id:      'bdrr_spy_v1',
    engine_version: '1.0.0',
    session: {
      symbol:               'SPY',
      date:                 '2026-05-26',
      market_timezone:      'America/New_York',
      session_open_utc_ms:  new Date('2026-05-26T13:30:00.000Z').getTime(),
      session_close_utc_ms: new Date('2026-05-26T20:00:00.000Z').getTime(),
      timeframe_seconds:    300
    }
  };
  return Object.assign({}, base, overrides);
}

// Helper: run a valid call and return the detection_result
function validResult(soOverrides, mdOverrides) {
  const r = buildDetectionResult(
    makeStageOutputs(soOverrides || {}),
    makeMetadata(mdOverrides || {})
  );
  return r;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

// 1. schema_version
(function testSchemaVersion() {
  const r = validResult();
  check(r.status === 'OK', 'schemaVersion: outer status must be OK');
  check(r.detection_result.schema_version === 'DetectionResult/v1',
    `schemaVersion: expected "DetectionResult/v1", got "${r.detection_result.schema_version}"`);
})();

// 2. Valid UUID v4 result_id (INV-D-01)
(function testResultId() {
  const r = validResult();
  check(r.status === 'OK', 'resultId: outer OK');
  const id = r.detection_result.result_id;
  check(isUUIDv4(id), `resultId: "${id}" is not a valid UUID v4`);
})();

// 3. Valid ISO 8601 UTC produced_at, not equal to any bar_utc_ms (INV-D-10)
(function testProducedAt() {
  const r = validResult();
  check(r.status === 'OK', 'producedAt: outer OK');
  const pa = r.detection_result.produced_at;
  check(isISO8601UTC(pa), `producedAt: "${pa}" is not ISO 8601 UTC`);
  // Must not equal the confirmation bar's bar_utc_ms
  const barMs = BAR_TIME.getTime();
  check(new Date(pa).getTime() !== barMs,
    'producedAt: must not be derived from confirmation bar bar_utc_ms');
})();

// 4. Runtime OK → canonical VALID
(function testStatusMappingValid() {
  const r = validResult();
  check(r.status === 'OK', 'statusValid: outer OK');
  check(r.detection_result.status === 'VALID',
    `statusValid: expected "VALID", got "${r.detection_result.status}"`);
})();

// 5. Runtime FAILED → canonical INVALID
(function testStatusMappingInvalid() {
  const r = buildDetectionResult(
    makeStageOutputs({ rejResult: { status: 'FAILED', failed_stage: 'NO_QUALIFYING_REJECTION_CANDLE', reason: 'no match', failed_retests: [], failed_retest_count: 0 } }),
    makeMetadata()
  );
  check(r.status === 'OK', 'statusInvalid: outer OK');
  check(r.detection_result.status === 'INVALID',
    `statusInvalid: expected "INVALID", got "${r.detection_result.status}"`);
})();

// 6. VALID: failed_stage=null, failed_rules=[] (INV-D-02)
(function testValidInvD02() {
  const r = validResult();
  const dr = r.detection_result;
  check(dr.failed_stage === null,
    `INV-D-02: VALID failed_stage must be null, got "${dr.failed_stage}"`);
  check(Array.isArray(dr.failed_rules) && dr.failed_rules.length === 0,
    'INV-D-02: VALID failed_rules must be empty array');
})();

// 7. INVALID: failed_stage ≠ null (INV-D-03)
(function testInvalidInvD03() {
  const r = buildDetectionResult(
    makeStageOutputs({ rejResult: { status: 'FAILED', failed_stage: 'NO_QUALIFYING_REJECTION_CANDLE', reason: 'x', failed_retests: [], failed_retest_count: 0 } }),
    makeMetadata()
  );
  check(r.detection_result.failed_stage !== null,
    'INV-D-03: INVALID failed_stage must not be null');
  check(r.detection_result.failed_stage === 'NO_QUALIFYING_REJECTION_CANDLE',
    `INV-D-03: failed_stage expected NO_QUALIFYING_REJECTION_CANDLE, got "${r.detection_result.failed_stage}"`);
})();

// 8. level_price is PriceTicks with correct ticks and tick_size (INV-D-04)
(function testLevelPricePriceTicks() {
  const r = validResult();
  const lp = r.detection_result.level_price;
  check(lp !== null && typeof lp === 'object', 'levelPrice: must be an object');
  check(Number.isInteger(lp.ticks), `levelPrice: ticks must be integer, got ${lp.ticks}`);
  check(lp.ticks === 75044,
    `levelPrice: expected 75044 ticks (750.44/0.01), got ${lp.ticks}`);
  check(lp.tick_size === TICK,
    `levelPrice: tick_size expected ${TICK}, got ${lp.tick_size}`);
})();

// 9. Tick-size consistency across all price fields (INV-D-04)
(function testTickSizeConsistency() {
  const r = validResult();
  const dr = r.detection_result;

  function checkTickSize(field, obj) {
    if (obj == null) return;
    if (typeof obj.tick_size !== 'undefined') {
      check(obj.tick_size === TICK,
        `tickConsistency: ${field}.tick_size expected ${TICK}, got ${obj.tick_size}`);
    }
  }

  checkTickSize('level_price', dr.level_price);
  checkTickSize('break_bar.open',  dr.break_bar && dr.break_bar.open);
  checkTickSize('break_bar.high',  dr.break_bar && dr.break_bar.high);
  checkTickSize('break_bar.low',   dr.break_bar && dr.break_bar.low);
  checkTickSize('break_bar.close', dr.break_bar && dr.break_bar.close);
  checkTickSize('directional_break_distance', dr.directional_break_distance);
  checkTickSize('displacement_pts', dr.displacement_pts);
  checkTickSize('confirmation_bar.open',  dr.confirmation_bar && dr.confirmation_bar.open);
  checkTickSize('confirmation_bar.close', dr.confirmation_bar && dr.confirmation_bar.close);
  checkTickSize('confirmation_penetration', dr.confirmation_penetration);
  checkTickSize('confirmation_close_beyond_level', dr.confirmation_close_beyond_level);
})();

// 10. SessionMetadata preserved exactly
(function testSessionMetadata() {
  const r = validResult();
  const s = r.detection_result.session;
  check(s.symbol               === 'SPY',              'session.symbol wrong');
  check(s.date                 === '2026-05-26',        'session.date wrong');
  check(s.market_timezone      === 'America/New_York',  'session.market_timezone wrong');
  check(s.session_open_utc_ms  === new Date('2026-05-26T13:30:00.000Z').getTime(),
    'session.session_open_utc_ms wrong');
  check(s.session_close_utc_ms === new Date('2026-05-26T20:00:00.000Z').getTime(),
    'session.session_close_utc_ms wrong');
  check(s.timeframe_seconds    === 300, 'session.timeframe_seconds wrong');
})();

// 11. preset_id and engine_version preserved
(function testPresetAndEngine() {
  const r = validResult();
  check(r.detection_result.preset_id      === 'bdrr_spy_v1', 'preset_id wrong');
  check(r.detection_result.engine_version === '1.0.0',        'engine_version wrong');
})();

// 12. level_source and direction from orb
(function testLevelSourceDirection() {
  const r = validResult();
  check(r.detection_result.level_source === 'ORB_HIGH', 'level_source wrong');
  check(r.detection_result.direction    === 'LONG',      'direction wrong');
})();

// 13. break_bar built correctly
(function testBreakBar() {
  const r = validResult();
  const bb = r.detection_result.break_bar;
  check(bb !== null, 'breakBar: must not be null');
  check(typeof bb === 'object', 'breakBar: must be object');
  check(Number.isInteger(bb.open.ticks),  'breakBar.open.ticks must be integer');
  check(Number.isInteger(bb.close.ticks), 'breakBar.close.ticks must be integer');
  // 750.63 / 0.01 = 75063
  check(bb.close.ticks === 75063,
    `breakBar.close.ticks expected 75063, got ${bb.close.ticks}`);
  check(bb.bar_utc_ms === new Date('2026-05-26T13:40:00.000Z').getTime(),
    'breakBar.bar_utc_ms wrong');
})();

// 14. directional_break_distance ticks preserved
(function testBreakDistance() {
  const r = validResult();
  const d = r.detection_result.directional_break_distance;
  check(d !== null, 'breakDist: must not be null');
  check(d.ticks === 19, `breakDist.ticks expected 19, got ${d.ticks}`);
  check(d.tick_size === TICK, 'breakDist.tick_size wrong');
})();

// 15. displacement_window is always an array (never null)
(function testDisplacementWindowIsArray() {
  const r = validResult();
  check(Array.isArray(r.detection_result.displacement_window),
    'displacement_window must be an array');

  // Also check when dispResult is absent
  const r2 = buildDetectionResult(
    makeStageOutputs({ dispResult: null }),
    makeMetadata()
  );
  check(Array.isArray(r2.detection_result.displacement_window),
    'displacement_window must be array even when dispResult is null');
})();

// 16. displacement_bar_count correct
(function testDisplacementBarCount() {
  const r = validResult();
  check(r.detection_result.displacement_bar_count === 1,
    `displacement_bar_count expected 1, got ${r.detection_result.displacement_bar_count}`);
})();

// 17. displacement_pts ticks correct
(function testDisplacementPts() {
  const r = validResult();
  const dp = r.detection_result.displacement_pts;
  check(dp !== null, 'displacement_pts must not be null');
  check(dp.ticks === 68, `displacement_pts.ticks expected 68, got ${dp.ticks}`);
  check(dp.tick_size === TICK, 'displacement_pts.tick_size wrong');
})();

// 18. displacement_pct is a valid Rational (non-zero denominator)
(function testDisplacementPct() {
  const r = validResult();
  const dpct = r.detection_result.displacement_pct;
  check(dpct !== null, 'displacement_pct must not be null');
  check(typeof dpct.numerator === 'number' && Number.isInteger(dpct.numerator),
    'displacement_pct.numerator must be integer');
  check(typeof dpct.denominator === 'number' && dpct.denominator > 0,
    'displacement_pct.denominator must be positive');
  // numerator = displacement_pts.ticks = 68; denominator = level_price.ticks = 75044
  check(dpct.numerator   === 68,    `displacement_pct.numerator expected 68, got ${dpct.numerator}`);
  check(dpct.denominator === 75044, `displacement_pct.denominator expected 75044, got ${dpct.denominator}`);
})();

// 19. rejection_side_clearance_by_bar length == displacement_window length (INV-D-05)
(function testClearanceArrayLength() {
  const r = validResult();
  const dr = r.detection_result;
  check(Array.isArray(dr.rejection_side_clearance_by_bar),
    'rejection_side_clearance_by_bar must be an array');
  check(dr.rejection_side_clearance_by_bar.length === dr.displacement_window.length,
    `INV-D-05: clearance array length (${dr.rejection_side_clearance_by_bar.length}) ` +
    `must equal displacement_window length (${dr.displacement_window.length})`);
  // INV-D-06: LONG clearance = bar.low.ticks - level_price.ticks
  // displacement candle low = 750.64 → 75064 ticks; level = 75044 → clearance = 20
  const c0 = dr.rejection_side_clearance_by_bar[0];
  check(c0.ticks === 20,
    `INV-D-06: clearance[0].ticks expected 20 (75064-75044), got ${c0.ticks}`);
})();

// 20. retest_window is always an array (never null)
(function testRetestWindowIsArray() {
  const r = validResult();
  check(Array.isArray(r.detection_result.retest_window),
    'retest_window must be an array');

  const r2 = buildDetectionResult(
    makeStageOutputs({ retestResult: null }),
    makeMetadata()
  );
  check(Array.isArray(r2.detection_result.retest_window),
    'retest_window must be array even when retestResult is null');
})();

// 21. failed_retests is always an array (never null)
(function testFailedRetestsIsArray() {
  const r = validResult();
  check(Array.isArray(r.detection_result.failed_retests),
    'failed_retests must be an array');
})();

// 22. failed_retest_count matches failed_retests length
(function testFailedRetestCount() {
  const r = validResult();
  const dr = r.detection_result;
  check(dr.failed_retest_count === dr.failed_retests.length,
    `failed_retest_count (${dr.failed_retest_count}) must equal failed_retests.length (${dr.failed_retests.length})`);
  check(dr.failed_retest_count === 2, `failed_retest_count expected 2, got ${dr.failed_retest_count}`);
})();

// 23. retest_closest_approach.ticks >= 0 (INV-D-11)
(function testRetestClosestApproach() {
  const r = validResult();
  const rca = r.detection_result.retest_closest_approach;
  check(rca !== null, 'retest_closest_approach must not be null');
  check(rca.ticks >= 0,
    `INV-D-11: retest_closest_approach.ticks must be >= 0, got ${rca.ticks}`);
  // min abs(low.ticks - level.ticks): candle2 low=750.09 → 75009, abs(75009-75044)=35
  check(rca.ticks === 35, `retest_closest_approach.ticks expected 35, got ${rca.ticks}`);
})();

// 24. retest_penetration_through_level.ticks >= 0 (INV-D-12)
(function testRetestPenetration() {
  const r = validResult();
  const rpt = r.detection_result.retest_penetration_through_level;
  check(rpt !== null, 'retest_penetration_through_level must not be null');
  check(rpt.ticks >= 0,
    `INV-D-12: retest_penetration.ticks must be >= 0, got ${rpt.ticks}`);
  // min low ticks across contacts: 74958 (r1 @ 749.58); pen = max(0, 75044-74958) = 86
  check(rpt.ticks === 86,
    `retest_penetration.ticks expected 86, got ${rpt.ticks}`);
})();

// 25. confirmation_bar built correctly (INV-D-08: VALID → confirmation_bar ≠ null)
(function testConfirmationBar() {
  const r = validResult();
  const cb = r.detection_result.confirmation_bar;
  check(cb !== null, 'INV-D-08: confirmation_bar must not be null when VALID');
  check(typeof cb === 'object', 'confirmation_bar must be object');
  // close = 750.89 → 75089 ticks
  check(cb.close.ticks === 75089,
    `confirmation_bar.close.ticks expected 75089, got ${cb.close.ticks}`);
  // low = 750.37 → 75037 ticks
  check(cb.low.ticks === 75037,
    `confirmation_bar.low.ticks expected 75037, got ${cb.low.ticks}`);
  // high = 750.97 → 75097 ticks
  check(cb.high.ticks === 75097,
    `confirmation_bar.high.ticks expected 75097, got ${cb.high.ticks}`);
})();

// 26. confirmation geometry Rationals are valid (non-zero denominator)
(function testConfirmationRationals() {
  const r = validResult();
  const dr = r.detection_result;

  for (const field of ['confirmation_rej_wick', 'confirmation_body',
                        'confirmation_opp_wick', 'confirmation_favorable_close_location']) {
    const rat = dr[field];
    check(rat !== null, `${field} must not be null for qualifying candle`);
    check(typeof rat.numerator   === 'number' && Number.isInteger(rat.numerator),
      `${field}.numerator must be integer`);
    check(typeof rat.denominator === 'number' && rat.denominator > 0,
      `${field}.denominator must be positive (INV-D-20)`);
    // Value in [0, 1]
    check(rat.numerator / rat.denominator >= 0 && rat.numerator / rat.denominator <= 1,
      `${field} ratio must be in [0, 1]`);
  }
})();

// 27. confirmation_penetration.ticks >= 0 (INV-D-14)
(function testConfirmationPenetration() {
  const r = validResult();
  const cp = r.detection_result.confirmation_penetration;
  check(cp !== null, 'confirmation_penetration must not be null');
  check(cp.ticks >= 0, `INV-D-14: confirmation_penetration.ticks must be >= 0, got ${cp.ticks}`);
  // penetration = 750.44 - 750.37 = 7 ticks
  check(cp.ticks === 7, `confirmation_penetration.ticks expected 7, got ${cp.ticks}`);
})();

// 28. bars_break_to_first_retest correct
(function testBarsBreakToFirstRetest() {
  const r = validResult();
  // retest_window_start_index=4, break_candle_index=2 → 2 bars
  const b = r.detection_result.bars_break_to_first_retest;
  check(b === 2, `bars_break_to_first_retest expected 2, got ${b}`);
})();

// 29. bars_break_to_confirmation correct
(function testBarsBreakToConfirmation() {
  const r = validResult();
  // confirmation_candle_index=17, break_candle_index=2 → 15 bars
  const b = r.detection_result.bars_break_to_confirmation;
  check(b === 15, `bars_break_to_confirmation expected 15, got ${b}`);
})();

// 30. No mutation of stageOutputs
(function testNoMutationStageOutputs() {
  const so = makeStageOutputs();
  const origRejStatus = so.rejResult.status;
  const origLevelPrice = so.rejResult.level_price;
  const origOrbDate = so.orb.date;
  buildDetectionResult(so, makeMetadata());
  check(so.rejResult.status      === origRejStatus,   'mutation: rejResult.status was mutated');
  check(so.rejResult.level_price === origLevelPrice,  'mutation: rejResult.level_price was mutated');
  check(so.orb.date              === origOrbDate,     'mutation: orb.date was mutated');
})();

// 31. No mutation of metadata
(function testNoMutationMetadata() {
  const md = makeMetadata();
  const origTickSize = md.tick_size;
  const origPresetId = md.preset_id;
  const origSymbol = md.session.symbol;
  buildDetectionResult(makeStageOutputs(), md);
  check(md.tick_size       === origTickSize,  'mutation: metadata.tick_size was mutated');
  check(md.preset_id       === origPresetId,  'mutation: metadata.preset_id was mutated');
  check(md.session.symbol  === origSymbol,    'mutation: metadata.session.symbol was mutated');
})();

// 32. Output is frozen (immutable)
(function testOutputFrozen() {
  const r = validResult();
  check(Object.isFrozen(r.detection_result), 'detection_result must be frozen');
  check(Object.isFrozen(r.detection_result.level_price), 'detection_result.level_price must be frozen');
  check(Object.isFrozen(r.detection_result.session), 'detection_result.session must be frozen');
})();

// 33. null stageOutputs rejected
(function testNullStageOutputs() {
  const r = buildDetectionResult(null, makeMetadata());
  check(r.status === 'FAILED', 'null stageOutputs must return FAILED');
  check(typeof r.failure_code === 'string', 'null stageOutputs must include failure_code');
})();

// 34. Missing rejResult rejected
(function testMissingRejResult() {
  const r = buildDetectionResult({ orb: makeORB() }, makeMetadata());
  check(r.status === 'FAILED', 'missing rejResult must return FAILED');
  check(r.failure_code === 'INVALID_STAGE_OUTPUTS',
    `missing rejResult failure_code expected INVALID_STAGE_OUTPUTS, got ${r.failure_code}`);
})();

// 35. Unknown rejResult.status rejected
(function testUnknownRejStatus() {
  const r = buildDetectionResult(
    makeStageOutputs({ rejResult: { status: 'UNKNOWN' } }),
    makeMetadata()
  );
  check(r.status === 'FAILED', 'unknown rejResult.status must return FAILED');
  check(r.failure_code === 'INVALID_STAGE_OUTPUTS',
    `unknown status failure_code expected INVALID_STAGE_OUTPUTS, got ${r.failure_code}`);
})();

// 36. null metadata rejected
(function testNullMetadata() {
  const r = buildDetectionResult(makeStageOutputs(), null);
  check(r.status === 'FAILED', 'null metadata must return FAILED');
  check(r.failure_code === 'INVALID_METADATA',
    `null metadata failure_code expected INVALID_METADATA, got ${r.failure_code}`);
})();

// 37. Missing tick_size rejected
(function testMissingTickSize() {
  const md = makeMetadata();
  delete md.tick_size;
  const r = buildDetectionResult(makeStageOutputs(), md);
  check(r.status === 'FAILED', 'missing tick_size must return FAILED');
  check(r.failure_code === 'INVALID_METADATA', `got ${r.failure_code}`);
})();

// 38. Non-positive tick_size rejected
(function testNonPositiveTickSize() {
  const r1 = buildDetectionResult(makeStageOutputs(), makeMetadata({ tick_size: 0 }));
  check(r1.status === 'FAILED', 'zero tick_size must return FAILED');
  check(r1.failure_code === 'INVALID_METADATA', `got ${r1.failure_code}`);

  const r2 = buildDetectionResult(makeStageOutputs(), makeMetadata({ tick_size: -0.01 }));
  check(r2.status === 'FAILED', 'negative tick_size must return FAILED');
})();

// 39. Missing preset_id rejected
(function testMissingPresetId() {
  const md = makeMetadata();
  delete md.preset_id;
  const r = buildDetectionResult(makeStageOutputs(), md);
  check(r.status === 'FAILED', 'missing preset_id must return FAILED');
  check(r.failure_code === 'INVALID_METADATA', `got ${r.failure_code}`);
})();

// 40. Missing engine_version rejected
(function testMissingEngineVersion() {
  const md = makeMetadata();
  delete md.engine_version;
  const r = buildDetectionResult(makeStageOutputs(), md);
  check(r.status === 'FAILED', 'missing engine_version must return FAILED');
  check(r.failure_code === 'INVALID_METADATA', `got ${r.failure_code}`);
})();

// 41. Missing session rejected
(function testMissingSession() {
  const md = makeMetadata();
  delete md.session;
  const r = buildDetectionResult(makeStageOutputs(), md);
  check(r.status === 'FAILED', 'missing session must return FAILED');
  check(r.failure_code === 'INVALID_METADATA', `got ${r.failure_code}`);
})();

// 42. Missing session.symbol rejected
(function testMissingSymbol() {
  const md = makeMetadata();
  delete md.session.symbol;
  const r = buildDetectionResult(makeStageOutputs(), md);
  check(r.status === 'FAILED', 'missing session.symbol must return FAILED');
  check(r.failure_code === 'INVALID_METADATA', `got ${r.failure_code}`);
})();

// 43. Missing session.date rejected
(function testMissingDate() {
  const md = makeMetadata();
  delete md.session.date;
  const r = buildDetectionResult(makeStageOutputs(), md);
  check(r.status === 'FAILED', 'missing session.date must return FAILED');
})();

// 44. Invalid session.date format rejected
(function testInvalidDateFormat() {
  const md = makeMetadata();
  md.session = Object.assign({}, md.session, { date: '26-05-2026' });
  const r = buildDetectionResult(makeStageOutputs(), md);
  check(r.status === 'FAILED', 'invalid date format must return FAILED');
})();

// 45. Missing session.session_open_utc_ms rejected
(function testMissingSessionOpenMs() {
  const md = makeMetadata();
  const sess = Object.assign({}, md.session);
  delete sess.session_open_utc_ms;
  md.session = sess;
  const r = buildDetectionResult(makeStageOutputs(), md);
  check(r.status === 'FAILED', 'missing session_open_utc_ms must return FAILED');
})();

// 46. Missing session.timeframe_seconds rejected
(function testMissingTimeframeSeconds() {
  const md = makeMetadata();
  const sess = Object.assign({}, md.session);
  delete sess.timeframe_seconds;
  md.session = sess;
  const r = buildDetectionResult(makeStageOutputs(), md);
  check(r.status === 'FAILED', 'missing timeframe_seconds must return FAILED');
})();

// 47. Deterministic validation: same inputs yield same structural shape
(function testDeterminism() {
  const so = makeStageOutputs();
  const md = makeMetadata();
  const r1 = buildDetectionResult(so, md);
  const r2 = buildDetectionResult(so, md);
  check(r1.status === 'OK' && r2.status === 'OK', 'determinism: both calls must succeed');
  // Structural fields (excluding result_id and produced_at which are intentionally different)
  const dr1 = r1.detection_result, dr2 = r2.detection_result;
  check(dr1.status         === dr2.status,         'determinism: status differs');
  check(dr1.schema_version === dr2.schema_version, 'determinism: schema_version differs');
  check(dr1.level_price.ticks === dr2.level_price.ticks, 'determinism: level_price.ticks differs');
  check(dr1.displacement_bar_count === dr2.displacement_bar_count, 'determinism: displacement_bar_count differs');
  check(dr1.failed_retest_count === dr2.failed_retest_count, 'determinism: failed_retest_count differs');
})();

// 48. result_id is unique across calls (INV-D-01)
(function testResultIdUnique() {
  const r1 = validResult();
  const r2 = validResult();
  check(r1.detection_result.result_id !== r2.detection_result.result_id,
    'INV-D-01: result_id must be unique across calls');
})();

// 49. produced_at advances (or equals) across calls — monotonic wall clock
(function testProducedAtMonotonic() {
  const r1 = validResult();
  const r2 = validResult();
  const t1 = new Date(r1.detection_result.produced_at).getTime();
  const t2 = new Date(r2.detection_result.produced_at).getTime();
  check(t2 >= t1, 'produced_at must be monotonically non-decreasing across calls');
})();

// 50. VALID with no orb/break/disp/retest: arrays default to []
(function testMissingUpstreamArraysDefault() {
  const r = buildDetectionResult(
    { rejResult: makeRejResult() },  // no orb, breakResult, dispResult, retestResult
    makeMetadata()
  );
  check(r.status === 'OK', 'missingUpstream: outer OK');
  check(Array.isArray(r.detection_result.displacement_window) &&
        r.detection_result.displacement_window.length === 0,
    'missingUpstream: displacement_window must be []');
  check(Array.isArray(r.detection_result.retest_window) &&
        r.detection_result.retest_window.length === 0,
    'missingUpstream: retest_window must be []');
  check(Array.isArray(r.detection_result.failed_retests),
    'missingUpstream: failed_retests must be array');
})();

// 51. INVALID with no orb: level_price = null, level_source = null
(function testInvalidNoOrb() {
  const r = buildDetectionResult(
    {
      rejResult: {
        status: 'FAILED',
        failed_stage: 'LEVEL_NOT_FOUND',
        reason: 'no ORB',
        failed_retests: [],
        failed_retest_count: 0
      }
    },
    makeMetadata()
  );
  check(r.status === 'OK', 'invalidNoOrb: outer OK');
  check(r.detection_result.level_price  === null, 'level_price must be null when no orb');
  check(r.detection_result.level_source === null, 'level_source must be null when no orb');
})();

// 52. retest_displacement_retracement_pct is null when displacement_pts is null (INV-D-15a)
(function testRetracementNullWhenNoDisplacement() {
  const r = buildDetectionResult(
    makeStageOutputs({ dispResult: null }),
    makeMetadata()
  );
  check(r.status === 'OK', 'retracementNull: outer OK');
  check(r.detection_result.retest_displacement_retracement_pct === null,
    'INV-D-15a: retest_displacement_retracement_pct must be null when displacement_pts is null');
})();

// 53. Canonical retest window ends at confirmation bar (inclusive), not end-of-session.
//     The fixture confirmation_candle_index is 17; contacts are at indices 4 and 5.
//     A post-confirmation contact injected at index 18 must not affect any metric.
(function testPostConfirmationIsolation() {
  // Build a retestResult that has an additional extreme contact AFTER the
  // confirmation bar (index 18 > confirmation_candle_index 17).
  // Its low is fabricated to be far through the level (e.g. 700.00 → 70000 ticks),
  // which would dominate penetration, closest_approach, and retracement if included.
  // BAR_TIME (the confirmation candle) = 2026-05-26T14:05:00.000Z.
  // The extreme candle must be strictly AFTER that timestamp.
  const r1 = makeRetestResult();
  const extremePostConfCandle = makeCandle(
    new Date('2026-05-26T14:10:00.000Z'), 701.00, 752.00, 700.00, 701.00
  );
  const retestResultWithExtra = Object.assign({}, r1, {
    retest_contacts: r1.retest_contacts.concat([{
      candle_index: 18,   // beyond confirmation_candle_index=17
      candle: extremePostConfCandle,
      timestamp: extremePostConfCandle.time,
      closest_directional_position_ticks: -5044,  // 70000 - 75044 = -5044 ticks
      penetration_through_level_ticks: 5044,
      penetration_through_level_points: 50.44,
      displacement_retracement_pct: 5044 / 68
    }]),
    // Extend the runtime window to include index 18 (one extra raw candle)
    retest_window: r1.retest_window.concat([extremePostConfCandle]),
    retest_window_end_index: 18
  });

  // Baseline (no extra contact)
  const baseline = buildDetectionResult(makeStageOutputs(), makeMetadata());
  // With post-confirmation extreme contact
  const withExtra = buildDetectionResult(
    makeStageOutputs({ retestResult: retestResultWithExtra }),
    makeMetadata()
  );

  check(baseline.status === 'OK' && withExtra.status === 'OK',
    'postConfIsolation: both calls must succeed');

  const bdr = baseline.detection_result;
  const wdr = withExtra.detection_result;

  // retest_window must end at confirmation bar — same length in both cases
  check(wdr.retest_window.length === bdr.retest_window.length,
    `postConfIsolation: retest_window.length must be unchanged; ` +
    `baseline=${bdr.retest_window.length}, withExtra=${wdr.retest_window.length}`);

  // No retest_window bar may post-date the confirmation bar
  const confMs = wdr.confirmation_bar.bar_utc_ms;
  const postConfBars = wdr.retest_window.filter(b => b.bar_utc_ms > confMs);
  check(postConfBars.length === 0,
    `postConfIsolation: ${postConfBars.length} post-confirmation bar(s) found in retest_window`);

  // retest_closest_approach must be identical
  check(wdr.retest_closest_approach.ticks === bdr.retest_closest_approach.ticks,
    `postConfIsolation: retest_closest_approach changed: ` +
    `${bdr.retest_closest_approach.ticks} → ${wdr.retest_closest_approach.ticks}`);

  // retest_penetration_through_level must be identical
  check(wdr.retest_penetration_through_level.ticks === bdr.retest_penetration_through_level.ticks,
    `postConfIsolation: retest_penetration changed: ` +
    `${bdr.retest_penetration_through_level.ticks} → ${wdr.retest_penetration_through_level.ticks}`);

  // retest_displacement_retracement_pct must be identical
  check(
    wdr.retest_displacement_retracement_pct.numerator   === bdr.retest_displacement_retracement_pct.numerator &&
    wdr.retest_displacement_retracement_pct.denominator === bdr.retest_displacement_retracement_pct.denominator,
    `postConfIsolation: retest_displacement_retracement_pct changed: ` +
    `${bdr.retest_displacement_retracement_pct.numerator}/${bdr.retest_displacement_retracement_pct.denominator} → ` +
    `${wdr.retest_displacement_retracement_pct.numerator}/${wdr.retest_displacement_retracement_pct.denominator}`
  );

  // retest_bar_count must be identical
  check(wdr.retest_bar_count === bdr.retest_bar_count,
    `postConfIsolation: retest_bar_count changed: ${bdr.retest_bar_count} → ${wdr.retest_bar_count}`);
})();

// 54. retest_window contains no bars after the confirmation bar's timestamp.
//     The fixture has 2 candles (13:50, 13:55); confirmation is at 14:05.
//     Both pass the canonical filter → retest_window.length == 2.
//     Additionally verify no bar post-dates the confirmation bar.
(function testRetestWindowBoundedByConfirmation() {
  const r = validResult();
  const dr = r.detection_result;
  // Both fixture candles (r1 at 13:50, r2 at 13:55) are before conf (14:05)
  check(dr.retest_window.length === 2,
    `retest_window.length expected 2 for fixture (both contacts pre-confirmation), got ${dr.retest_window.length}`);
  check(dr.retest_bar_count === 2,
    `retest_bar_count expected 2, got ${dr.retest_bar_count}`);
  // No bar in retest_window may post-date the confirmation bar
  const confMs = dr.confirmation_bar.bar_utc_ms;
  const bad = dr.retest_window.filter(b => b.bar_utc_ms > confMs);
  check(bad.length === 0,
    `retest_window must contain no bars after confirmation; found ${bad.length}`);
})();

// ── Report ────────────────────────────────────────────────────────────────────

console.log('BDRR DetectionResult/v1 adapter tests');
console.log('======================================');
console.log(`Checks run: ${checks}`);
console.log(`Failures:   ${failures.length}`);
if (failures.length) {
  console.log('\nFAILED CHECKS:');
  failures.forEach(f => console.log('  - ' + f));
  console.log('\nRESULT: FAIL');
  process.exitCode = 1;
} else {
  console.log('\nRESULT: PASS');
  process.exitCode = 0;
}
