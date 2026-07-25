/**
 * test_bdrr_stage1_stage2.js
 *
 * Focused unit tests for estrategie/bdrr_engine.js — Stage 1 (Session + ORB
 * construction) and Stage 2 (Confirmed Break) only.
 *
 * Uses small, hand-built synthetic candle fixtures. Deliberately does NOT
 * read dati/bdrr_spy_oracle.json or dati/bdrr_qqq_oracle.json — those are
 * outcome/oracle fixtures for later stages (displacement, retest,
 * rejection, trade planning, outcome evaluation), none of which exist yet.
 *
 * Run: node estrategie/test_bdrr_stage1_stage2.js
 */

'use strict';

const {
  buildSessionContext,
  buildORB,
  findBreak,
  priceToTicks,
  ticksToPoints,
  getETTimeString,
  getETDateString
} = require('./bdrr_engine.js');

const CONFIG = {
  timeframe_minutes: 5,
  timezone: 'America/New_York',
  session_open: '09:30',
  orb_start: 'session_open',
  orb_duration_minutes: 5,
  level_source: 'ORB_HIGH',
  direction: 'LONG',
  tick_size: 0.01
};

// ── Test harness ────────────────────────────────────────────────────────────
let checks = 0;
let failures = [];
function check(cond, msg) {
  checks++;
  if (!cond) failures.push(msg);
}
function deepEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ── Fixture helpers ──────────────────────────────────────────────────────────
// 2026-07-01 is within US Eastern Daylight Time (EDT, UTC-4), so ET hh:mm ==
// UTC (hh+4):mm on this date. Self-checked below against the module's own
// ET formatter rather than trusted blindly.
function et(hh, mm) {
  return new Date(Date.UTC(2026, 6, 1, hh + 4, mm, 0));
}
function candle(hh, mm, o, h, l, c) {
  return { time: et(hh, mm), open: o, high: h, low: l, close: c };
}

// Self-check the fixture helper against the module's real ET formatter
// before trusting it in any other test.
(function selfCheckFixtureHelper() {
  const t = getETTimeString(et(9, 30), new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false
  }));
  check(t === '09:30', `fixture helper self-check failed: et(9,30) formatted as "${t}", expected "09:30"`);
  const d = getETDateString(et(9, 30), new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit'
  }));
  check(d === '2026-07-01', `fixture helper self-check failed: date formatted as "${d}", expected "2026-07-01"`);
})();

function baseSessionCandles() {
  // ORB candle at 09:30, then a clean run of post-ORB candles that never
  // themselves close above the level (used as a scaffold, individual tests
  // override/insert candles as needed).
  return [
    candle(9, 30, 100.00, 101.00, 99.00, 100.50),   // ORB candle: high=101 (level), low=99
    candle(9, 35, 100.40, 100.60, 100.20, 100.45),
    candle(9, 40, 100.45, 100.70, 100.30, 100.50),
    candle(9, 45, 100.50, 100.80, 100.40, 100.55)
  ];
}

// ── Test 1: correct ORB High and ORB Low ────────────────────────────────────
(function testORBHighLow() {
  const candles = baseSessionCandles();
  const sc = buildSessionContext(candles, CONFIG);
  check(sc.status === 'OK', 'testORBHighLow: sessionContext should succeed');
  const orb = buildORB(candles, sc, CONFIG);
  check(orb.status === 'OK', 'testORBHighLow: buildORB should succeed');
  check(orb.orb_high === 101.00, `testORBHighLow: orb_high should be 101.00, got ${orb.orb_high}`);
  check(orb.orb_low === 99.00, `testORBHighLow: orb_low should be 99.00, got ${orb.orb_low}`);
  check(orb.level_price === 101.00, `testORBHighLow: level_price should equal ORB high (101.00), got ${orb.level_price}`);
  check(orb.orb_low_active === false, 'testORBHighLow: orb_low_active must be false (not an active detection level)');
})();

// ── Test 2: break cannot occur on the ORB candle ────────────────────────────
(function testBreakCannotOccurOnORBCandle() {
  // Deliberately construct an `orb` result with an artificially low
  // level_price (100.00), lower than the ORB candle's own close (100.50).
  // If findBreak incorrectly scanned starting at orb_candle_index instead
  // of orb_candle_index + 1, it would wrongly report the ORB candle itself
  // as the break. This is impossible to trigger via real OHLC data (close
  // can never exceed a candle's own high), so it is tested directly against
  // a hand-built orb result.
  const candles = [
    candle(9, 30, 100.00, 101.00, 99.00, 100.50),  // ORB candle
    candle(9, 35, 100.40, 100.60, 99.80, 99.90)    // does NOT close above the artificial level (100.00)
  ];
  const fakeOrb = {
    status: 'OK',
    date: '2026-07-01',
    orb_candle_index: 0,
    orb_candle: candles[0],
    orb_high: 101.00,
    orb_low: 99.00,
    level_source: 'ORB_HIGH',
    level_price: 100.00, // artificially low — candles[0].close (100.50) > 100.00
    level_price_ticks: priceToTicks(100.00, CONFIG.tick_size),
    direction: 'LONG'
  };
  const result = findBreak(candles, fakeOrb, CONFIG);
  check(
    !(result.status === 'OK' && result.break_candle_index === 0),
    'testBreakCannotOccurOnORBCandle: the ORB candle itself (index 0) must never be reported as the break, even though its close (100.50) exceeds the artificially low level_price (100.00)'
  );
  check(result.status === 'FAILED' && result.failed_stage === 'BREAK_NOT_FOUND',
    `testBreakCannotOccurOnORBCandle: expected BREAK_NOT_FOUND (no post-ORB candle closes above 100.00), got ${JSON.stringify(result)}`);
})();

// ── Test 3: first close above ORB High is selected ──────────────────────────
(function testFirstQualifyingBreakSelected() {
  const candles = [
    candle(9, 30, 100.00, 101.00, 99.00, 100.50),  // ORB, level = 101.00
    candle(9, 35, 100.40, 100.90, 100.20, 100.60), // below level
    candle(9, 40, 100.60, 101.20, 100.50, 101.10), // FIRST close above level (101.10 > 101.00)
    candle(9, 45, 101.10, 102.00, 100.90, 101.90)  // also above level, but later — must not be selected
  ];
  const sc = buildSessionContext(candles, CONFIG);
  const orb = buildORB(candles, sc, CONFIG);
  const brk = findBreak(candles, orb, CONFIG);
  check(brk.status === 'OK', 'testFirstQualifyingBreakSelected: break should be found');
  check(brk.break_candle_index === 2, `testFirstQualifyingBreakSelected: expected index 2 (09:40), got ${brk.break_candle_index}`);
  check(getETTimeString(brk.break_timestamp, new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false
  })) === '09:40', 'testFirstQualifyingBreakSelected: break_timestamp should be 09:40');
})();

// ── Test 4: wick above level with close below does not count ───────────────
(function testWickOnlyDoesNotCount() {
  const candles = [
    candle(9, 30, 100.00, 101.00, 99.00, 100.50),  // ORB, level = 101.00
    candle(9, 35, 100.50, 101.50, 100.40, 100.80), // wick to 101.50 but closes at 100.80 (below level) — must NOT count
    candle(9, 40, 100.80, 101.20, 100.70, 101.05)  // genuine close above level — should be selected
  ];
  const sc = buildSessionContext(candles, CONFIG);
  const orb = buildORB(candles, sc, CONFIG);
  const brk = findBreak(candles, orb, CONFIG);
  check(brk.status === 'OK', 'testWickOnlyDoesNotCount: break should be found');
  check(brk.break_candle_index === 2, `testWickOnlyDoesNotCount: wick-only candle (index 1) must be skipped; expected index 2, got ${brk.break_candle_index}`);
})();

// ── Test 5: exact close equal to the level does not count ──────────────────
(function testExactCloseEqualDoesNotCount() {
  const candles = [
    candle(9, 30, 100.00, 101.00, 99.00, 100.50),  // ORB, level = 101.00
    candle(9, 35, 100.50, 101.10, 100.40, 101.00), // closes EXACTLY at level — must NOT count (strict >)
    candle(9, 40, 101.00, 101.20, 100.90, 101.01)  // closes fractionally above — should be selected
  ];
  const sc = buildSessionContext(candles, CONFIG);
  const orb = buildORB(candles, sc, CONFIG);
  const brk = findBreak(candles, orb, CONFIG);
  check(brk.status === 'OK', 'testExactCloseEqualDoesNotCount: break should be found');
  check(brk.break_candle_index === 2, `testExactCloseEqualDoesNotCount: exact-equal candle (index 1) must be skipped; expected index 2, got ${brk.break_candle_index}`);
})();

// ── Test 6: missing ORB candle returns explicit failure ─────────────────────
(function testMissingORBCandle() {
  const candles = [
    // no 09:30 candle at all — session data starts at 09:35
    candle(9, 35, 100.40, 100.90, 100.20, 100.60),
    candle(9, 40, 100.60, 101.20, 100.50, 101.10)
  ];
  const sc = buildSessionContext(candles, CONFIG);
  check(sc.status === 'OK', 'testMissingORBCandle: sessionContext itself should still succeed (candles exist, just no 09:30 bar)');
  const orb = buildORB(candles, sc, CONFIG);
  check(orb.status === 'FAILED', 'testMissingORBCandle: buildORB must fail explicitly');
  check(orb.failed_stage === 'LEVEL_NOT_FOUND', `testMissingORBCandle: expected failed_stage LEVEL_NOT_FOUND, got ${orb.failed_stage}`);
  check(typeof orb.reason === 'string' && orb.reason.length > 0, 'testMissingORBCandle: a human-readable reason must be present');

  // Downstream findBreak must not throw and must propagate the failure.
  const brk = findBreak(candles, orb, CONFIG);
  check(brk.status === 'FAILED', 'testMissingORBCandle: findBreak must not throw, must return FAILED');
})();

// ── Test 7: no break returns BREAK_NOT_FOUND ────────────────────────────────
(function testNoBreakFound() {
  const candles = baseSessionCandles(); // none of the post-ORB candles close above 101.00
  const sc = buildSessionContext(candles, CONFIG);
  const orb = buildORB(candles, sc, CONFIG);
  const brk = findBreak(candles, orb, CONFIG);
  check(brk.status === 'FAILED', 'testNoBreakFound: expected FAILED status');
  check(brk.failed_stage === 'BREAK_NOT_FOUND', `testNoBreakFound: expected BREAK_NOT_FOUND, got ${brk.failed_stage}`);
})();

// ── Test 8: points/ticks conversion is deterministic ────────────────────────
(function testPointsTicksConversion() {
  check(priceToTicks(101.00, 0.01) === 10100, 'testPointsTicksConversion: priceToTicks(101.00) should be 10100');
  check(priceToTicks(100.00, 0.01) === 10000, 'testPointsTicksConversion: priceToTicks(100.00) should be 10000');
  check(ticksToPoints(100, 0.01) === 1.00, 'testPointsTicksConversion: ticksToPoints(100) should be 1.00');

  const candles = [
    candle(9, 30, 100.00, 101.00, 99.00, 100.50),  // ORB, level = 101.00
    candle(9, 35, 101.00, 101.30, 100.90, 101.07)  // close 101.07 -> +0.07 pts / +7 ticks above level
  ];
  const sc = buildSessionContext(candles, CONFIG);
  const orb = buildORB(candles, sc, CONFIG);
  const brk = findBreak(candles, orb, CONFIG);
  check(brk.status === 'OK', 'testPointsTicksConversion: break should be found');
  check(brk.directional_break_distance.ticks === 7,
    `testPointsTicksConversion: expected 7 ticks, got ${brk.directional_break_distance.ticks}`);
  check(brk.directional_break_distance.points === 0.07,
    `testPointsTicksConversion: expected 0.07 points, got ${brk.directional_break_distance.points}`);

  // Run again on freshly built (non-shared) objects — same numeric result every time.
  const candles2 = [
    candle(9, 30, 100.00, 101.00, 99.00, 100.50),
    candle(9, 35, 101.00, 101.30, 100.90, 101.07)
  ];
  const sc2 = buildSessionContext(candles2, CONFIG);
  const orb2 = buildORB(candles2, sc2, CONFIG);
  const brk2 = findBreak(candles2, orb2, CONFIG);
  check(brk2.directional_break_distance.ticks === brk.directional_break_distance.ticks,
    'testPointsTicksConversion: tick conversion must be deterministic across independent runs');
})();

// ── Test 9: repeated runs with identical input return deeply identical output ──
(function testDeterminismAcrossRuns() {
  function freshCandles() {
    return [
      candle(9, 30, 100.00, 101.00, 99.00, 100.50),
      candle(9, 35, 100.40, 100.90, 100.20, 100.60),
      candle(9, 40, 100.60, 101.20, 100.50, 101.10),
      candle(9, 45, 101.10, 102.00, 100.90, 101.90)
    ];
  }

  const runOnce = () => {
    const candles = freshCandles();
    const sc = buildSessionContext(candles, CONFIG);
    const orb = buildORB(candles, sc, CONFIG);
    const brk = findBreak(candles, orb, CONFIG);
    return { sc, orb, brk };
  };

  const run1 = runOnce();
  const run2 = runOnce();

  check(deepEqual(run1.sc, run2.sc), 'testDeterminismAcrossRuns: sessionContext output must be deeply identical across runs');
  check(deepEqual(run1.orb, run2.orb), 'testDeterminismAcrossRuns: ORB output must be deeply identical across runs');
  check(deepEqual(run1.brk, run2.brk), 'testDeterminismAcrossRuns: break output must be deeply identical across runs');

  // Also confirm the input array passed to buildSessionContext is not mutated.
  const original = freshCandles();
  const originalCopyForComparison = freshCandles();
  buildSessionContext(original, CONFIG);
  check(deepEqual(original, originalCopyForComparison), 'testDeterminismAcrossRuns: buildSessionContext must not mutate its input array');
})();

// ── Report ───────────────────────────────────────────────────────────────────
console.log('BDRR Stage 1 / Stage 2 tests');
console.log('=============================');
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
