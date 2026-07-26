/**
 * test_bdrr_stage5.js
 *
 * Focused unit tests for estrategie/bdrr_engine.js — Stage 5 (Rejection
 * Qualification) only. Uses small, hand-built synthetic candle fixtures.
 * Does NOT read dati/bdrr_spy_oracle.json or dati/bdrr_qqq_oracle.json.
 *
 * Fixture construction note: rejection_wick_ratio depends on min(open,close),
 * while favorable_close_location depends only on close. With low/high fixed,
 * open and close are the only two free values, so at most two of the three
 * ratios (wick, body, close-location) can be chosen independently — the
 * third follows algebraically. Every fixture below is built from explicit
 * tick offsets and immediately self-checked against a local, independent
 * re-implementation of the geometry formulas before being used in any
 * assertion, so a fixture-construction mistake fails loudly as a named
 * "fixture self-check" rather than silently producing a wrong expectation.
 *
 * Run: node estrategie/test_bdrr_stage5.js
 */

'use strict';

const {
  buildSessionContext,
  buildORB,
  findBreak,
  findDisplacement,
  findRetestWindow,
  findRejection,
  getETTimeString
} = require('./bdrr_engine.js');

const CONFIG = {
  timeframe_minutes: 5,
  timezone: 'America/New_York',
  session_open: '09:30',
  orb_start: 'session_open',
  orb_duration_minutes: 5,
  level_source: 'ORB_HIGH',
  direction: 'LONG',
  tick_size: 0.01,
  min_displacement_ticks: null,
  min_penetration_ticks: null,
  min_close_beyond_level_ticks: 1
};

const TICK_SIZE = 0.01;
const LEVEL = 101.00;
const LEVEL_TICKS = Math.round(LEVEL / TICK_SIZE); // 10100

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

// ── Fixture helpers (2026-07-01, EDT/UTC-4 — self-checked below) ───────────
function et(hh, mm) {
  return new Date(Date.UTC(2026, 6, 1, hh + 4, mm, 0));
}
function candle(hh, mm, o, h, l, c) {
  return { time: et(hh, mm), open: o, high: h, low: l, close: c };
}

(function selfCheckFixtureHelper() {
  const t = getETTimeString(et(9, 30), new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false
  }));
  check(t === '09:30', `fixture helper self-check failed: et(9,30) formatted as "${t}", expected "09:30"`);
})();

// Independent local re-implementation of the geometry formulas, used ONLY to
// self-verify fixtures before they're used in assertions against the real
// engine output (bdrr_engine.js is the actual thing under test).
function localGeometry(c) {
  const h = Math.round(c.high / TICK_SIZE);
  const l = Math.round(c.low / TICK_SIZE);
  const o = Math.round(c.open / TICK_SIZE);
  const cl = Math.round(c.close / TICK_SIZE);
  const range = h - l;
  if (range === 0) return { range_ticks: 0 };
  return {
    range_ticks: range,
    wick: (Math.min(o, cl) - l) / range,
    body: Math.abs(cl - o) / range,
    closeLoc: (cl - l) / range,
    oppWick: (h - Math.max(o, cl)) / range,
    penetrationTicks: Math.max(0, LEVEL_TICKS - l),
    closeBeyondTicks: cl - LEVEL_TICKS
  };
}

// Builds a candle from absolute tick offsets and immediately self-checks it
// against the expected wick/body/closeLoc ratios (each optional; pass null
// to skip checking that one).
function mkCandle(hh, mm, { lowTicks, rangeTicks, openTicks, closeTicks }, expect) {
  const c = {
    time: et(hh, mm),
    open: openTicks / 100,
    high: (lowTicks + rangeTicks) / 100,
    low: lowTicks / 100,
    close: closeTicks / 100
  };
  const g = localGeometry(c);
  if (expect) {
    if (expect.wick != null) check(Math.abs(g.wick - expect.wick) < 1e-9, `mkCandle(${hh}:${mm}) fixture self-check: wick expected ${expect.wick}, got ${g.wick}`);
    if (expect.body != null) check(Math.abs(g.body - expect.body) < 1e-9, `mkCandle(${hh}:${mm}) fixture self-check: body expected ${expect.body}, got ${g.body}`);
    if (expect.closeLoc != null) check(Math.abs(g.closeLoc - expect.closeLoc) < 1e-9, `mkCandle(${hh}:${mm}) fixture self-check: closeLoc expected ${expect.closeLoc}, got ${g.closeLoc}`);
  }
  return c;
}

function run(candles) {
  const sc = buildSessionContext(candles, CONFIG);
  const orb = buildORB(candles, sc, CONFIG);
  const brk = findBreak(candles, orb, CONFIG);
  const disp = findDisplacement(candles, orb, brk, CONFIG);
  const retest = findRetestWindow(candles, orb, brk, disp, CONFIG);
  const rejection = findRejection(candles, orb, brk, disp, retest, CONFIG);
  return { sc, orb, brk, disp, retest, rejection };
}

// Common scaffold: ORB level 101.00 (h=101,l=99), break 09:35 close 101.20,
// one displacement bar 09:40 (low 101.10 > level).
function baseCandles(extra) {
  return [
    candle(9, 30, 100.00, 101.00, 99.00, 100.50),
    candle(9, 35, 100.50, 101.50, 100.30, 101.20),
    candle(9, 40, 101.20, 101.60, 101.10, 101.30)
  ].concat(extra);
}

// A comfortably-qualifying candle: wick=0.50, body=0.30, closeLoc=0.80,
// low = 9600 (96.00, well below level -> a real contact).
function qualifyingCandle(hh, mm) {
  return mkCandle(hh, mm, { lowTicks: 9600, rangeTicks: 1000, openTicks: 10100, closeTicks: 10400 },
    { wick: 0.50, body: 0.30, closeLoc: 0.80 });
}
// A clearly failing candle: wick=0.10, body=0.05, closeLoc=0.15.
function failingCandleA(hh, mm) {
  return mkCandle(hh, mm, { lowTicks: 9600, rangeTicks: 1000, openTicks: 9700, closeTicks: 9750 },
    { wick: 0.10, body: 0.05, closeLoc: 0.15 });
}
// Another clearly failing candle: wick=0.20, body=0.10, closeLoc=0.30.
function failingCandleB(hh, mm) {
  return mkCandle(hh, mm, { lowTicks: 9600, rangeTicks: 1000, openTicks: 9800, closeTicks: 9900 },
    { wick: 0.20, body: 0.10, closeLoc: 0.30 });
}

// ── Test 1: qualifying rejection on the first retest-contact candle ────────
(function testFirstContactQualifies() {
  const candles = baseCandles([qualifyingCandle(9, 45)]);
  const { rejection } = run(candles);
  check(rejection.status === 'OK', `testFirstContactQualifies: expected OK, got ${JSON.stringify(rejection)}`);
  check(rejection.failed_retest_count === 0, 'testFirstContactQualifies: first contact qualifies -> zero failed retests');
  check(rejection.geometry.rejection_wick_ratio >= 0.47, 'testFirstContactQualifies: wick ratio must satisfy threshold');
  check(rejection.geometry.body_ratio <= 0.40, 'testFirstContactQualifies: body ratio must satisfy threshold');
  check(rejection.geometry.favorable_close_location >= 0.80, 'testFirstContactQualifies: close location must satisfy threshold');
})();

// ── Test 2: one or more failed retests before a later qualifying candle ────
(function testFailedRetestsThenQualify() {
  const candles = baseCandles([
    failingCandleA(9, 45),
    failingCandleB(9, 50),
    qualifyingCandle(9, 55)
  ]);
  const { rejection } = run(candles);
  check(rejection.status === 'OK', `testFailedRetestsThenQualify: expected OK, got ${JSON.stringify(rejection)}`);
  check(rejection.failed_retest_count === 2, `testFailedRetestsThenQualify: expected 2 failed retests, got ${rejection.failed_retest_count}`);
  check(rejection.confirmation_timestamp.getTime() === et(9, 55).getTime(), 'testFailedRetestsThenQualify: confirmation must be the 09:55 candle');
})();

// ── Test 3: first qualifying candle stops the scan ──────────────────────────
(function testScanStopsAtFirstQualifier() {
  const candles = baseCandles([
    qualifyingCandle(9, 45),
    qualifyingCandle(9, 50) // would also qualify, but must never be reached
  ]);
  const { rejection } = run(candles);
  check(rejection.status === 'OK', `testScanStopsAtFirstQualifier: expected OK, got ${JSON.stringify(rejection)}`);
  check(rejection.confirmation_timestamp.getTime() === et(9, 45).getTime(),
    'testScanStopsAtFirstQualifier: confirmation must be the FIRST qualifying candle (09:45), not the second');
})();

// ── Test 4: non-contact candles cannot qualify ──────────────────────────────
(function testNonContactCannotQualify() {
  const candles = baseCandles([
    // low 101.20 > level 101.00: NOT a contact, regardless of its own shape.
    candle(9, 45, 101.55, 101.90, 101.20, 101.80),
    qualifyingCandle(9, 50)
  ]);
  const { rejection } = run(candles);
  check(rejection.status === 'OK', `testNonContactCannotQualify: expected OK, got ${JSON.stringify(rejection)}`);
  check(rejection.confirmation_timestamp.getTime() === et(9, 50).getTime(),
    'testNonContactCannotQualify: the non-contact candle at 09:45 must never be selected as confirmation');
  check(rejection.failed_retest_count === 0,
    'testNonContactCannotQualify: the non-contact candle must not appear in failed_retests either');
})();

// ── Test 5: wick ratio exactly 0.47 passes ──────────────────────────────────
(function testWickExactly047() {
  // wick=0.47, body=0.35 -> closeLoc=0.82 (all three happen to pass; that's fine).
  const cnd = mkCandle(9, 45, { lowTicks: 9600, rangeTicks: 1000, openTicks: 10070, closeTicks: 10420 },
    { wick: 0.47, body: 0.35, closeLoc: 0.82 });
  const { rejection } = run(baseCandles([cnd]));
  check(rejection.status === 'OK', `testWickExactly047: expected OK, got ${JSON.stringify(rejection)}`);
  check(Math.abs(rejection.geometry.rejection_wick_ratio - 0.47) < 1e-9,
    `testWickExactly047: expected rejection_wick_ratio 0.47, got ${rejection.geometry.rejection_wick_ratio}`);
  check(rejection.geometry.rejection_wick_ratio >= 0.47, 'testWickExactly047: exactly 0.47 must satisfy the >= 0.47 threshold');
})();

// ── Test 6: body ratio exactly 0.40 passes ──────────────────────────────────
(function testBodyExactly040() {
  // wick=0.55, body=0.40 -> closeLoc=0.95.
  const cnd = mkCandle(9, 45, { lowTicks: 9600, rangeTicks: 1000, openTicks: 10150, closeTicks: 10550 },
    { wick: 0.55, body: 0.40, closeLoc: 0.95 });
  const { rejection } = run(baseCandles([cnd]));
  check(rejection.status === 'OK', `testBodyExactly040: expected OK, got ${JSON.stringify(rejection)}`);
  check(Math.abs(rejection.geometry.body_ratio - 0.40) < 1e-9,
    `testBodyExactly040: expected body_ratio 0.40, got ${rejection.geometry.body_ratio}`);
  check(rejection.geometry.body_ratio <= 0.40, 'testBodyExactly040: exactly 0.40 must satisfy the <= 0.40 threshold');
})();

// ── Test 7: favorable close location exactly 0.80 passes ───────────────────
(function testCloseLocExactly080() {
  // wick=0.50, body=0.30 -> closeLoc=0.80 (this is `qualifyingCandle`'s own shape).
  const cnd = qualifyingCandle(9, 45);
  const { rejection } = run(baseCandles([cnd]));
  check(rejection.status === 'OK', `testCloseLocExactly080: expected OK, got ${JSON.stringify(rejection)}`);
  check(Math.abs(rejection.geometry.favorable_close_location - 0.80) < 1e-9,
    `testCloseLocExactly080: expected favorable_close_location 0.80, got ${rejection.geometry.favorable_close_location}`);
  check(rejection.geometry.favorable_close_location >= 0.80, 'testCloseLocExactly080: exactly 0.80 must satisfy the >= 0.80 threshold');
})();

// ── Test 8: one failing threshold rejects with the correct rule identifier ─
(function testSingleFailingThreshold() {
  // wick=0.45 (just under 0.47 -> fails), body=0.40 (passes), closeLoc=0.85 (passes).
  const cnd = mkCandle(9, 45, { lowTicks: 9600, rangeTicks: 1000, openTicks: 10050, closeTicks: 10450 },
    { wick: 0.45, body: 0.40, closeLoc: 0.85 });
  const { rejection } = run(baseCandles([cnd]));
  check(rejection.status === 'FAILED', `testSingleFailingThreshold: expected FAILED, got ${JSON.stringify(rejection)}`);
  check(rejection.failed_retest_count === 1, 'testSingleFailingThreshold: exactly one failed retest expected');
  const fr = rejection.failed_retests[0];
  check(deepEqual(fr.failed_rules, ['REJECTION_WICK_RATIO_TOO_LOW']),
    `testSingleFailingThreshold: expected only REJECTION_WICK_RATIO_TOO_LOW, got ${JSON.stringify(fr.failed_rules)}`);
})();

// ── Test 9: multiple failing thresholds are all reported ───────────────────
(function testMultipleFailingThresholds() {
  // wick=0.10 (fails), body=0.50 (fails, >0.40), closeLoc=0.60 (fails, <0.80).
  const cnd = mkCandle(9, 45, { lowTicks: 9600, rangeTicks: 1000, openTicks: 9700, closeTicks: 10200 },
    { wick: 0.10, body: 0.50, closeLoc: 0.60 });
  const { rejection } = run(baseCandles([cnd]));
  check(rejection.status === 'FAILED', `testMultipleFailingThresholds: expected FAILED, got ${JSON.stringify(rejection)}`);
  const fr = rejection.failed_retests[0];
  check(fr.failed_rules.includes('REJECTION_WICK_RATIO_TOO_LOW'), 'testMultipleFailingThresholds: must include REJECTION_WICK_RATIO_TOO_LOW');
  check(fr.failed_rules.includes('BODY_RATIO_TOO_HIGH'), 'testMultipleFailingThresholds: must include BODY_RATIO_TOO_HIGH');
  check(fr.failed_rules.includes('FAVORABLE_CLOSE_LOCATION_TOO_LOW'), 'testMultipleFailingThresholds: must include FAVORABLE_CLOSE_LOCATION_TOO_LOW');
  check(fr.failed_rules.length === 3, `testMultipleFailingThresholds: expected exactly 3 failed rules, got ${fr.failed_rules.length}`);
})();

// ── Test 10: zero-range candle returns null ratios and ZERO_RANGE_CANDLE ───
(function testZeroRangeCandle() {
  const candles = baseCandles([
    candle(9, 45, 100.90, 100.90, 100.90, 100.90) // O=H=L=C, low <= level (contact), zero range
  ]);
  const { rejection } = run(candles);
  check(rejection.status === 'FAILED', `testZeroRangeCandle: expected FAILED (no qualifying candle), got ${JSON.stringify(rejection)}`);
  check(rejection.failed_retest_count === 1, 'testZeroRangeCandle: the zero-range candle must be recorded as a failed retest');
  const fr = rejection.failed_retests[0];
  check(fr.geometry.rejection_wick_ratio === null, 'testZeroRangeCandle: rejection_wick_ratio must be null');
  check(fr.geometry.body_ratio === null, 'testZeroRangeCandle: body_ratio must be null');
  check(fr.geometry.favorable_close_location === null, 'testZeroRangeCandle: favorable_close_location must be null');
  check(fr.geometry.opposite_wick_ratio === null, 'testZeroRangeCandle: opposite_wick_ratio must be null');
  check(deepEqual(fr.failed_rules, ['ZERO_RANGE_CANDLE']), `testZeroRangeCandle: expected only ZERO_RANGE_CANDLE, got ${JSON.stringify(fr.failed_rules)}`);
})();

// ── Test 11: low exactly equal to level may qualify ─────────────────────────
(function testLowExactlyEqualsLevelMayQualify() {
  // low === LEVEL_TICKS exactly; wick=0.50, body=0.30, closeLoc=0.80 (comfortable pass).
  const cnd = mkCandle(10, 0, { lowTicks: LEVEL_TICKS, rangeTicks: 1000, openTicks: LEVEL_TICKS + 500, closeTicks: LEVEL_TICKS + 800 },
    { wick: 0.50, body: 0.30, closeLoc: 0.80 });
  const { rejection } = run(baseCandles([cnd]));
  check(rejection.status === 'OK', `testLowExactlyEqualsLevelMayQualify: expected OK, got ${JSON.stringify(rejection)}`);
  check(rejection.confirmation_candle.low === LEVEL, 'testLowExactlyEqualsLevelMayQualify: confirmation candle low must equal level exactly');
  check(rejection.geometry.penetration_through_level_ticks === 0, 'testLowExactlyEqualsLevelMayQualify: penetration must be 0 when low === level');
})();

// ── Test 12: close below level is now REJECTED by close-beyond-level gate ───
// With min_close_beyond_level_ticks=1, a candle whose close is below the level
// must fail Stage 5 even if all three geometry ratios pass.
(function testCloseBelowLevelRejected() {
  // Deep penetration (1000 ticks below level) AND close still below the level,
  // on an otherwise comfortably-qualifying candle: wick=0.50, body=0.35, closeLoc=0.85.
  const lowTicks = LEVEL_TICKS - 1000;
  const rangeTicks = 900;
  const openTicks = lowTicks + 450;  // wick = 450/900 = 0.50
  const closeTicks = lowTicks + 765; // body = (765-450)/900 = 0.35; closeLoc = 765/900 = 0.85
  const cnd = mkCandle(10, 0, { lowTicks, rangeTicks, openTicks, closeTicks },
    { wick: 0.50, body: 0.35, closeLoc: 0.85 });
  const { rejection } = run(baseCandles([cnd]));
  // With min_close_beyond_level_ticks=1 this candle closes 235 ticks BELOW level → must FAIL
  check(rejection.status === 'FAILED',
    `testCloseBelowLevelRejected: expected FAILED, got ${rejection.status}`);
  check(rejection.failed_stage === 'NO_QUALIFYING_REJECTION_CANDLE',
    `testCloseBelowLevelRejected: expected NO_QUALIFYING_REJECTION_CANDLE, got ${rejection.failed_stage}`);
  // The candle should appear in failed_retests with CLOSE_BEYOND_LEVEL_TOO_LOW
  check(rejection.failed_retest_count === 1,
    `testCloseBelowLevelRejected: expected 1 failed retest, got ${rejection.failed_retest_count}`);
  check(rejection.failed_retests[0].failed_rules.includes('CLOSE_BEYOND_LEVEL_TOO_LOW'),
    'testCloseBelowLevelRejected: failed_rules must include CLOSE_BEYOND_LEVEL_TOO_LOW');
})();

// ── Test 12b: close exactly at level is rejected (needs >= 1 tick above) ────
(function testCloseExactlyAtLevelRejected() {
  // Close exactly at level: close_beyond_level_ticks = 0 < 1 → must fail.
  // wick=0.50, body=0.30, closeLoc=0.80 (all pass geometry)
  const cnd = mkCandle(10, 5, { lowTicks: LEVEL_TICKS - 500, rangeTicks: 1000,
    openTicks: LEVEL_TICKS - 200, closeTicks: LEVEL_TICKS },
    { wick: 0.30, body: 0.20, closeLoc: 0.50 });
  const { rejection } = run(baseCandles([cnd]));
  // close_loc = 0.50 < 0.80 so it fails geometry ALSO, but let's build a better fixture:
  // Need: low well below level, close exactly at level, wick>=0.47, body<=0.40, close_loc>=0.80
  // lowTicks = LEVEL_TICKS - 100, rangeTicks = 500, close = LEVEL_TICKS
  // close_loc = (LEVEL_TICKS - (LEVEL_TICKS-100)) / 500 = 100/500 = 0.20 — too low
  // We need a candle where close = level AND close_loc >= 0.80
  // close_loc = (close - low) / (high - low) >= 0.80
  // If close = level, low = level - 100, range = 125 → close_loc = 100/125 = 0.80 ✓
  // open = low + 50 → wick = min(open,close) - low = (level-50)-(level-100) = 50
  // wick_ratio = 50/125 = 0.40 — too low (need >= 0.47)
  // Try: low = level - 100, range = 200, open = low + 100 = level
  // wick = min(level,level) - (level-100) = 100; wick_ratio = 100/200 = 0.50 ✓
  // body = |level-level| / 200 = 0 ✓; close_loc = 100/200 = 0.50 — too low
  // The math: if close = level and we need close_loc >= 0.80, we need
  // (close - low)/(high - low) >= 0.80, so high <= close + (close-low)/4
  // lowTicks = LEVEL_TICKS - 400, close = LEVEL_TICKS → close-low = 400
  // high = LEVEL_TICKS + 100, range = 500, close_loc = 400/500 = 0.80 ✓
  // open = LEVEL_TICKS - 150 → wick = min(LEVEL_TICKS-150, LEVEL_TICKS) - (LEVEL_TICKS-400) = 250
  // wick_ratio = 250/500 = 0.50 ✓
  // body = 150/500 = 0.30 ✓
  const cnd2 = mkCandle(10, 10, {
    lowTicks: LEVEL_TICKS - 400, rangeTicks: 500,
    openTicks: LEVEL_TICKS - 150, closeTicks: LEVEL_TICKS
  }, { wick: 0.50, body: 0.30, closeLoc: 0.80 });
  const { rejection: rej2 } = run(baseCandles([cnd2]));
  check(rej2.status === 'FAILED',
    `testCloseExactlyAtLevelRejected: expected FAILED (close exactly at level), got ${rej2.status}`);
})();

// ── Test 12c: close 1 tick above level passes when all geometry rules pass ──
(function testCloseOneTickAboveLevelPasses() {
  // close_beyond_level_ticks = 1 >= 1 → passes close-beyond gate.
  // lowTicks = LEVEL_TICKS - 400, close = LEVEL_TICKS + 1, range = 502
  // close_loc = 401/502 = 0.7988 → just below 0.80! Need to adjust.
  // lowTicks = LEVEL_TICKS - 500, close = LEVEL_TICKS + 1, high = LEVEL_TICKS + 125
  // range = 625, close_loc = 501/625 = 0.8016 ✓
  // open = LEVEL_TICKS - 200 → wick = (LEVEL_TICKS-200 - (LEVEL_TICKS-500))/625 = 300/625 = 0.48 ✓
  // body = |1-(-200)|/625 = 201/625 = 0.3216 ✓
  const cnd = mkCandle(10, 15, {
    lowTicks: LEVEL_TICKS - 500, rangeTicks: 625,
    openTicks: LEVEL_TICKS - 200, closeTicks: LEVEL_TICKS + 1
  }, null);
  // Self-check
  const g = localGeometry({
    time: et(10,15),
    low: (LEVEL_TICKS-500)/100, high: (LEVEL_TICKS+125)/100,
    open: (LEVEL_TICKS-200)/100, close: (LEVEL_TICKS+1)/100
  });
  check(g.wick >= 0.47, `test12c self-check: wick ${g.wick} >= 0.47`);
  check(g.body <= 0.40, `test12c self-check: body ${g.body} <= 0.40`);
  check(g.closeLoc >= 0.80, `test12c self-check: closeLoc ${g.closeLoc} >= 0.80`);
  check(g.closeBeyondTicks === 1, `test12c self-check: closeBeyond ${g.closeBeyondTicks} === 1`);

  const { rejection } = run(baseCandles([cnd]));
  check(rejection.status === 'OK',
    `testCloseOneTickAboveLevelPasses: expected OK (1 tick above level), got ${rejection.status}`);
  check(rejection.geometry.close_beyond_level_ticks === 1,
    `testCloseOneTickAboveLevelPasses: close_beyond_level_ticks must be 1, got ${rejection.geometry.close_beyond_level_ticks}`);
})();

// ── Test 13: no qualifying candle returns NO_QUALIFYING_REJECTION_CANDLE ───
(function testNoQualifyingCandle() {
  const candles = baseCandles([
    failingCandleA(9, 45),
    failingCandleB(9, 50) // data ends here, still no qualifier
  ]);
  const { rejection } = run(candles);
  check(rejection.status === 'FAILED', 'testNoQualifyingCandle: expected FAILED');
  check(rejection.failed_stage === 'NO_QUALIFYING_REJECTION_CANDLE',
    `testNoQualifyingCandle: expected NO_QUALIFYING_REJECTION_CANDLE, got ${rejection.failed_stage}`);
  check(rejection.failed_retest_count === 2, `testNoQualifyingCandle: expected 2 failed retests, got ${rejection.failed_retest_count}`);
})();

// ── Test 14: repeated runs are deeply identical ─────────────────────────────
(function testDeterminismAcrossRuns() {
  function fresh() {
    return baseCandles([failingCandleA(9, 45), qualifyingCandle(9, 50)]);
  }
  const r1 = run(fresh());
  const r2 = run(fresh());
  check(deepEqual(r1.rejection, r2.rejection), 'testDeterminismAcrossRuns: rejection output must be deeply identical across independent runs');
})();

// ── Test 15: inputs are not mutated ─────────────────────────────────────────
(function testNoMutation() {
  function fresh() {
    return baseCandles([failingCandleA(9, 45), qualifyingCandle(9, 50)]);
  }
  const original = fresh();
  const referenceCopy = fresh();
  run(original);
  check(deepEqual(original, referenceCopy), 'testNoMutation: candles array must not be mutated by the full Stage 1-5 pipeline');
})();

// ── Test 16: failed upstream results return structured failure ─────────────
(function testFailedUpstreamPropagation() {
  const candles = baseCandles([qualifyingCandle(9, 45)]);
  const sc = buildSessionContext(candles, CONFIG);
  const orb = buildORB(candles, sc, CONFIG);
  const brk = findBreak(candles, orb, CONFIG);
  const disp = findDisplacement(candles, orb, brk, CONFIG);

  const fakeFailedRetest = { status: 'FAILED', failed_stage: 'RETEST_NOT_FOUND', reason: 'synthetic failure for test' };
  const r1 = findRejection(candles, orb, brk, disp, fakeFailedRetest, CONFIG);
  check(r1.status === 'FAILED', 'testFailedUpstreamPropagation: FAILED retestResult must propagate as FAILED, not throw');
  check(r1.failed_stage === 'RETEST_NOT_FOUND', `testFailedUpstreamPropagation: expected propagated failed_stage RETEST_NOT_FOUND, got ${r1.failed_stage}`);

  const fakeFailedDisp = { status: 'FAILED', failed_stage: 'RETEST_BEFORE_DISPLACEMENT', reason: 'synthetic failure for test' };
  const r2 = findRejection(candles, orb, brk, fakeFailedDisp, fakeFailedRetest, CONFIG);
  check(r2.status === 'FAILED', 'testFailedUpstreamPropagation: FAILED displacementResult must propagate as FAILED, not throw');
})();

// ── Report ───────────────────────────────────────────────────────────────────
console.log('BDRR Stage 5 (Rejection Qualification) tests');
console.log('===============================================');
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
