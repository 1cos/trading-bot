/**
 * Focused tests for BDRR Stage 4 — Retest Window.
 * Run: node estrategie/test_bdrr_stage4.js
 */
'use strict';

const {
  buildSessionContext, buildORB, findBreak, findDisplacement, findRetestWindow
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
  const retest = findRetestWindow(session.candles, orb, brk, displacement, CONFIG);
  return { session, orb, brk, displacement, retest };
}

(function windowAndContacts() {
  const candles = [
    bar(9,30,100,101,99,100.5),
    bar(9,35,100.5,101.3,100.4,101.1),
    bar(9,40,101.1,102.0,101.2,101.8),  // displacement: distance 100 ticks
    bar(9,45,101.8,102.1,100.8,101.4),  // first contact: penetration 20 ticks
    bar(9,50,101.4,102.0,101.1,101.7),  // non-contact remains in window
    bar(9,55,101.7,102.0,101.0,101.6),  // equality contact
    bar(10,0,101.6,101.9,100.5,101.5)   // deeper contact
  ];
  const { retest: r } = run(candles);
  check(r.status === 'OK', 'window: expected OK');
  check(r.retest_start_index === 3 && r.retest_window_start_index === 3,
    'window: expected start index 3');
  check(r.retest_window_end_index === 6, 'window: expected last available index');
  check(r.retest_window.length === 4, 'window: expected all four chronological bars');
  check(r.retest_contact_count === 3, 'contacts: expected three contacts');
  check(r.retest_contacts.map(x => x.candle_index).join(',') === '3,5,6',
    'contacts: wrong chronological indexes');

  const first = r.retest_contacts[0];
  check(first.closest_directional_position_ticks === -20,
    `first contact: expected -20 directional ticks, got ${first.closest_directional_position_ticks}`);
  check(first.penetration_through_level_ticks === 20, 'first contact: expected 20 penetration ticks');
  check(first.penetration_through_level_points === 0.2, 'first contact: expected 0.2 points');
  check(first.displacement_retracement_pct === 0.2,
    `first contact: expected ratio 0.2, got ${first.displacement_retracement_pct}`);

  const equality = r.retest_contacts[1];
  check(equality.closest_directional_position_ticks === 0, 'equality: expected position 0');
  check(equality.penetration_through_level_ticks === 0, 'equality: expected zero penetration');
  check(equality.displacement_retracement_pct === 0, 'equality: expected zero ratio');
})();

(function noFailedRetestCapAndNoQualification() {
  const contacts = [];
  for (let minute = 45; minute <= 55; minute += 5) {
    contacts.push(bar(9, minute, 101.2, 101.6, 100.9, 101.1));
  }
  for (let minute = 0; minute <= 25; minute += 5) {
    contacts.push(bar(10, minute, 101.2, 101.6, 100.9, 101.1));
  }
  const candles = [
    bar(9,30,100,101,99,100.5),
    bar(9,35,100.5,101.3,100.4,101.1),
    bar(9,40,101.1,102.0,101.2,101.8),
    ...contacts
  ];
  const { retest: r } = run(candles);
  check(r.status === 'OK', 'cap: expected OK');
  check(r.retest_contact_count === contacts.length, 'cap: all contacts must be retained');
  check(!Object.prototype.hasOwnProperty.call(r, 'confirmation_candle'),
    'qualification: Stage 4 must not choose a confirmation candle');
  check(!Object.prototype.hasOwnProperty.call(r, 'failed_retests'),
    'qualification: Stage 4 must not classify failed retests');
})();

(function upstreamFailure() {
  const candles = [
    bar(9,30,100,101,99,100.5),
    bar(9,35,100.5,101.0,100.4,100.9)
  ];
  const session = buildSessionContext(candles, CONFIG);
  const orb = buildORB(session.candles, session, CONFIG);
  const brk = findBreak(session.candles, orb, CONFIG);
  const fakeDisplacement = { status: 'FAILED', failed_stage: 'BREAK_NOT_FOUND', reason: 'upstream' };
  const r = findRetestWindow(session.candles, orb, brk, fakeDisplacement, CONFIG);
  check(r.status === 'FAILED', 'upstream: expected structured FAILED');
  check(r.failed_stage === 'BREAK_NOT_FOUND', 'upstream: expected propagated stage');
})();

(function deterministicAndImmutable() {
  const candles = [
    bar(9,30,100,101,99,100.5),
    bar(9,35,100.5,101.3,100.4,101.1),
    bar(9,40,101.1,102.0,101.2,101.8),
    bar(9,45,101.8,102.1,100.8,101.4),
    bar(9,50,101.4,102.0,101.1,101.7)
  ];
  const before = JSON.stringify(candles);
  const first = run(candles).retest;
  const second = run(candles).retest;
  check(JSON.stringify(first) === JSON.stringify(second), 'determinism: outputs differ');
  check(JSON.stringify(candles) === before, 'immutability: input candles were mutated');
})();

console.log('BDRR Stage 4 tests');
console.log('==================');
console.log(`Checks run: ${checks}`);
console.log(`Failures: ${failures.length}`);
if (failures.length) {
  failures.forEach(message => console.error(`FAIL: ${message}`));
  console.log('\nRESULT: FAIL');
  process.exit(1);
}
console.log('\nRESULT: PASS');
