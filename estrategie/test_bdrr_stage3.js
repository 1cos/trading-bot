/**
 * Focused tests for BDRR Stage 3 — Displacement.
 * Run: node estrategie/test_bdrr_stage3.js
 */
'use strict';

const {
  buildSessionContext, buildORB, findBreak, findDisplacement
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
  min_displacement_ticks: null
};

let checks = 0;
const failures = [];
function check(condition, message) {
  checks++;
  if (!condition) failures.push(message);
}
function et(hh, mm) {
  return new Date(Date.UTC(2026, 6, 1, hh + 4, mm));
}
function bar(hh, mm, open, high, low, close) {
  return { time: et(hh, mm), open, high, low, close };
}
function run(candles) {
  const session = buildSessionContext(candles, CONFIG);
  const orb = buildORB(session.candles, session, CONFIG);
  const brk = findBreak(session.candles, orb, CONFIG);
  const displacement = findDisplacement(session.candles, orb, brk, CONFIG);
  return { session, orb, brk, displacement };
}

(function oneBarBeforeRetest() {
  const candles = [
    bar(9,30,100,101,99,100.5),
    bar(9,35,100.5,101.3,100.4,101.1),
    bar(9,40,101.1,101.8,101.05,101.4),
    bar(9,45,101.4,101.7,100.9,101.2)
  ];
  const { displacement: d } = run(candles);
  check(d.status === 'OK', 'one bar: expected OK');
  check(d.displacement_bar_count === 1, 'one bar: expected count 1');
  check(d.displacement_start_index === 2 && d.displacement_end_index === 2,
    'one bar: expected displacement index 2 only');
  check(d.first_retest_contact_index === 3, 'one bar: expected retest index 3');
  check(d.displacement_distance.ticks === 80, `one bar: expected 80 ticks, got ${d.displacement_distance.ticks}`);
})();

(function multipleBarsAndWindowOnlyMaximum() {
  const candles = [
    bar(9,30,100,101,99,100.5),
    bar(9,35,100.5,105,100.4,101.1),       // breakout high must be ignored
    bar(9,40,101.1,101.5,101.01,101.3),
    bar(9,45,101.3,102.25,101.10,102.0),   // window maximum: +125 ticks
    bar(9,50,102.0,110,100.95,101.2)        // retest high must be ignored
  ];
  const { displacement: d } = run(candles);
  check(d.status === 'OK', 'multiple bars: expected OK');
  check(d.displacement_bar_count === 2, 'multiple bars: expected count 2');
  check(d.displacement_window.length === 2, 'multiple bars: expected two window bars');
  check(d.max_favorable_high === 102.25, 'multiple bars: wrong max high');
  check(d.displacement_distance.ticks === 125, 'multiple bars: distance must use window only');
  check(d.displacement_distance.points === 1.25, 'multiple bars: expected 1.25 points');
})();

(function immediateRetestAndEquality() {
  for (const low of [100.9, 101.0]) {
    const candles = [
      bar(9,30,100,101,99,100.5),
      bar(9,35,100.5,101.3,100.4,101.1),
      bar(9,40,101.1,101.5,low,101.2)
    ];
    const { displacement: d } = run(candles);
    check(d.status === 'FAILED', `immediate retest low ${low}: expected FAILED`);
    check(d.failed_stage === 'RETEST_BEFORE_DISPLACEMENT',
      `immediate retest low ${low}: wrong failed_stage ${d.failed_stage}`);
    check(d.first_retest_contact_index === 2,
      `immediate retest low ${low}: wrong contact index`);
  }
})();

(function noRetest() {
  const candles = [
    bar(9,30,100,101,99,100.5),
    bar(9,35,100.5,101.3,100.4,101.1),
    bar(9,40,101.1,101.7,101.1,101.5),
    bar(9,45,101.5,102.0,101.2,101.8)
  ];
  const { displacement: d } = run(candles);
  check(d.status === 'FAILED', 'no retest: expected FAILED');
  check(d.failed_stage === 'RETEST_NOT_FOUND', 'no retest: expected RETEST_NOT_FOUND');
  check(d.displacement_bar_count === 2, 'no retest: expected two open displacement bars');
})();

(function deterministicAndImmutable() {
  const candles = [
    bar(9,30,100,101,99,100.5),
    bar(9,35,100.5,101.3,100.4,101.1),
    bar(9,40,101.1,101.8,101.05,101.4),
    bar(9,45,101.4,101.7,100.9,101.2)
  ];
  const before = JSON.stringify(candles);
  const first = run(candles).displacement;
  const second = run(candles).displacement;
  check(JSON.stringify(first) === JSON.stringify(second), 'determinism: outputs differ');
  check(JSON.stringify(candles) === before, 'immutability: input candles were mutated');
})();

console.log('BDRR Stage 3 tests');
console.log('==================');
console.log(`Checks run: ${checks}`);
console.log(`Failures: ${failures.length}`);
if (failures.length) {
  failures.forEach(message => console.error(`FAIL: ${message}`));
  console.log('\nRESULT: FAIL');
  process.exit(1);
}
console.log('\nRESULT: PASS');
