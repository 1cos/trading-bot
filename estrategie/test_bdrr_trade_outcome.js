/**
 * test_bdrr_trade_outcome.js
 *
 * Tests for estrategie/bdrr_trade_outcome.js — TradeOutcome/v1.
 *
 * Run: node estrategie/test_bdrr_trade_outcome.js
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const { evaluateTradeOutcome } = require('./bdrr_trade_outcome.js');

// ── Harness ───────────────────────────────────────────────────────────────────

let checks   = 0;
let failures = [];

function check(cond, msg) {
  checks++;
  if (!cond) failures.push(msg);
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

const TICK = 0.01;

// Default config: LONG, exit at 4R (most conservative — trade stays open longest)
const CONFIG4 = { direction: 'LONG', exit_target_r: 4 };
const CONFIG3 = { direction: 'LONG', exit_target_r: 3 };
const CONFIG2 = { direction: 'LONG', exit_target_r: 2 };

const CONF_BAR_UTC_MS = 1000;  // canonical confirmation bar timestamp

function makeDetectionResult(overrides) {
  return Object.assign({
    schema_version: 'DetectionResult/v1',
    result_id:      'aaaaaaaa-0000-4000-8000-000000000001',
    produced_at:    '2026-07-01T10:00:00.000Z',
    status:         'VALID',
    failed_stage:   null,
    session: {
      symbol: 'TEST', date: '2026-07-01', market_timezone: 'America/New_York',
      session_open_utc_ms: 0, session_close_utc_ms: 99999, timeframe_seconds: 300
    },
    preset_id: 'test', engine_version: '1.0.0',
    direction: 'LONG',
    confirmation_bar: {
      bar_utc_ms: CONF_BAR_UTC_MS,
      open:  { ticks: 10050, tick_size: TICK },
      high:  { ticks: 10090, tick_size: TICK },
      low:   { ticks: 10000, tick_size: TICK },
      close: { ticks: 10070, tick_size: TICK },
      volume: null
    },
    displacement_window: [], retest_window: [],
    failed_retests: [], failed_retest_count: 0
  }, overrides);
}

// TradePlan: entry=10100, stop=10000, risk=100t, 2R=10300, 3R=10400, 4R=10500
function makeTradePlan(overrides) {
  const e = 10100, s = 10000, r = 100;
  return Object.assign({
    schema_version:     'TradePlan/v1',
    entry_model:        'CONFIRMATION_CLOSE',
    entry_buffer_ticks: 0,
    stop_buffer_ticks:  0,
    tick_size:          TICK,
    entry_price: { ticks: e,       tick_size: TICK },
    stop_price:  { ticks: s,       tick_size: TICK },
    risk:        { ticks: r,       tick_size: TICK },
    r2_price:    { ticks: e+2*r,   tick_size: TICK },  // 10300
    r3_price:    { ticks: e+3*r,   tick_size: TICK },  // 10400
    r4_price:    { ticks: e+4*r,   tick_size: TICK }   // 10500
  }, overrides);
}

let _nextMs = 2000;
function resetMs(v) { _nextMs = v || 2000; }

function makeBar(hiTicks, loTicks, utcMs) {
  if (utcMs === undefined) { utcMs = _nextMs; _nextMs += 300000; }
  const mid = Math.round((hiTicks + loTicks) / 2);
  return {
    bar_utc_ms: utcMs,
    open:  { ticks: mid, tick_size: TICK },
    high:  { ticks: hiTicks, tick_size: TICK },
    low:   { ticks: loTicks, tick_size: TICK },
    close: { ticks: mid, tick_size: TICK },
    volume: null
  };
}

// ── 1. Entry not triggered (BOSB) ────────────────────────────────────────────

(function testEntryNotTriggered() {
  resetMs(2000);
  const tp = makeTradePlan({ entry_model: 'BREAK_OF_SIGNAL_BAR' });
  const bars = [makeBar(10090, 10050), makeBar(10095, 10055)];
  const r = evaluateTradeOutcome(makeDetectionResult(), tp, bars, CONFIG4);
  check(r.status === 'OK', 'notTriggered: OK');
  check(r.outcome.outcome === 'ENTRY_NOT_TRIGGERED',
    `notTriggered: expected ENTRY_NOT_TRIGGERED, got ${r.outcome.outcome}`);
  check(r.outcome.entry_triggered === false, 'notTriggered: entry_triggered false');
  check(r.outcome.realized_r === null, 'notTriggered: realized_r null');
})();

// ── 2. STOPPED before any target ─────────────────────────────────────────────

(function testStoppedBeforeTarget() {
  resetMs(2000);
  const bars = [makeBar(10150, 10060), makeBar(10120, 9950)];
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG4);
  check(r.status === 'OK', 'stopped: OK');
  const o = r.outcome;
  check(o.outcome         === 'STOPPED', `stopped: got ${o.outcome}`);
  check(o.exit_bar_index  === 1,         `stopped: exit at bar[1], got ${o.exit_bar_index}`);
  check(o.exit_price_ticks === 10000,    `stopped: exit_price 10000, got ${o.exit_price_ticks}`);
  check(o.realized_r      === -1,        `stopped: realized_r -1, got ${o.realized_r}`);
  check(o.highest_target_achieved === null, 'stopped: no target achieved');
})();

// ── 3. OPEN after session (no stop, no terminal target reached) ───────────────

(function testOpen() {
  resetMs(2000);
  const bars = [makeBar(10150, 10060), makeBar(10200, 10100)];
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG4);
  check(r.status === 'OK', 'open: OK');
  check(r.outcome.outcome    === 'OPEN', `open: got ${r.outcome.outcome}`);
  check(r.outcome.realized_r === null,   'open: realized_r null');
  check(r.outcome.exit_bar_index === null, 'open: no exit');
})();

// ── 4. AMBIGUOUS (stop AND selected terminal target on same bar) ─────────────
//
// Frozen rule (2026-07-25): AMBIGUOUS only when stop + SELECTED TERMINAL target
// on the same bar. Test with CONFIG4: bar must reach 4R to be AMBIGUOUS.

(function testAmbiguous() {
  resetMs(2000);
  // exit_target_r=4; bar hi=10520 (>=4R=10500), lo=9950 (<=stop=10000) → AMBIGUOUS
  const bars = [makeBar(10150, 10060), makeBar(10520, 9950)];
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG4);
  check(r.status === 'OK', 'ambiguous: OK');
  const o = r.outcome;
  check(o.outcome         === 'AMBIGUOUS', `ambiguous: expected AMBIGUOUS (stop+4R terminal), got ${o.outcome}`);
  check(o.exit_price_ticks === null,       'ambiguous: exit_price null');
  check(o.realized_r       === null,       'ambiguous: realized_r null');
  check(o.exit_target_label === null,      'ambiguous: exit_target null');
})();

// ── 5. Configurable exit target — same bar sequence, different configs ────────
//
// Bars: bar[0] stays flat, bar[1] reaches hi=10350 (above 2R=10300, below 3R=10400)
// exit_target_r=2 → TARGET_HIT at 2R on bar[1]
// exit_target_r=3 → 2R is intermediate; trade stays OPEN (bar[1] never hits 10400)
// exit_target_r=4 → same, OPEN

(function testConfigurableExitTarget() {
  resetMs(2000);
  const bars = [makeBar(10150, 10060), makeBar(10350, 10110)];

  const r2 = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG2);
  check(r2.status === 'OK', 'configTarget r2: OK');
  check(r2.outcome.outcome === 'TARGET_HIT', `configTarget r2: expected TARGET_HIT, got ${r2.outcome.outcome}`);
  check(r2.outcome.exit_target_label === '2R', `configTarget r2: label 2R, got ${r2.outcome.exit_target_label}`);
  check(r2.outcome.exit_target_r === 2, `configTarget r2: exit_target_r 2, got ${r2.outcome.exit_target_r}`);
  check(r2.outcome.realized_r === 2, `configTarget r2: realized_r 2, got ${r2.outcome.realized_r}`);

  resetMs(2000);
  const r3 = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG3);
  check(r3.status === 'OK', 'configTarget r3: OK');
  check(r3.outcome.outcome === 'OPEN', `configTarget r3: 2R intermediate → OPEN, got ${r3.outcome.outcome}`);
  check(r3.outcome.highest_target_achieved === '2R',
    `configTarget r3: highest 2R, got ${r3.outcome.highest_target_achieved}`);
  check(r3.outcome.realized_r === null, 'configTarget r3: realized_r null when OPEN');

  resetMs(2000);
  const r4 = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG4);
  check(r4.status === 'OK', 'configTarget r4: OK');
  check(r4.outcome.outcome === 'OPEN', `configTarget r4: 2R intermediate → OPEN, got ${r4.outcome.outcome}`);
  check(r4.outcome.highest_target_achieved === '2R',
    `configTarget r4: highest 2R, got ${r4.outcome.highest_target_achieved}`);
})();

// ── 6. 2R then stopped — behavior differs by exit_target_r ───────────────────
//
// bar[0]: hi=10350 → 2R reached (10300). bar[1]: lo=9950 → stop breached.
// exit_target_r=2 → trade closed on bar[0] at 2R; bar[1] never evaluated → TARGET_HIT
// exit_target_r=3 → 2R intermediate; bar[1] STOPPED; highest=2R, realized_r=-1
// exit_target_r=4 → same as 3R case

(function testTwoRThenStopped() {
  function runCase(config, label) {
    resetMs(2000);
    const bars = [makeBar(10350, 10110), makeBar(10120, 9950)];
    return evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, config);
  }

  const r2 = runCase(CONFIG2, '2R');
  check(r2.outcome.outcome === 'TARGET_HIT',  'twoR_then_stop r2: TARGET_HIT');
  check(r2.outcome.exit_bar_index === 0,      'twoR_then_stop r2: exit bar[0]');
  check(r2.outcome.realized_r === 2,          'twoR_then_stop r2: realized_r 2');

  const r3 = runCase(CONFIG3, '3R');
  check(r3.outcome.outcome === 'STOPPED',     'twoR_then_stop r3: STOPPED');
  check(r3.outcome.exit_bar_index === 1,      'twoR_then_stop r3: exit bar[1]');
  check(r3.outcome.highest_target_achieved === '2R',
    `twoR_then_stop r3: highest 2R, got ${r3.outcome.highest_target_achieved}`);
  check(r3.outcome.realized_r === -1,         'twoR_then_stop r3: realized_r -1');

  const r4 = runCase(CONFIG4, '4R');
  check(r4.outcome.outcome === 'STOPPED',     'twoR_then_stop r4: STOPPED');
  check(r4.outcome.highest_target_achieved === '2R',
    `twoR_then_stop r4: highest 2R, got ${r4.outcome.highest_target_achieved}`);
  check(r4.outcome.realized_r === -1,         'twoR_then_stop r4: realized_r -1');
})();

// ── 7. 3R reached (terminal when exit_target_r=3) ────────────────────────────

(function testThreeRTerminal() {
  resetMs(2000);
  const bars = [makeBar(10420, 10110)]; // hi=10420 >= 3R=10400
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG3);
  check(r.status === 'OK', '3R terminal: OK');
  const o = r.outcome;
  check(o.outcome          === 'TARGET_HIT', `3R terminal: got ${o.outcome}`);
  check(o.exit_target_label === '3R',        `3R terminal: label 3R, got ${o.exit_target_label}`);
  check(o.exit_target_r     === 3,           `3R terminal: r=3, got ${o.exit_target_r}`);
  check(o.realized_r        === 3,           `3R terminal: realized_r 3, got ${o.realized_r}`);
})();

// ── 8. 4R reached ────────────────────────────────────────────────────────────

(function testFourRTerminal() {
  resetMs(2000);
  const bars = [makeBar(10550, 10110)]; // hi=10550 >= 4R=10500
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG4);
  check(r.status === 'OK', '4R terminal: OK');
  const o = r.outcome;
  check(o.outcome           === 'TARGET_HIT', `4R terminal: got ${o.outcome}`);
  check(o.exit_target_label === '4R',         `4R terminal: label 4R, got ${o.exit_target_label}`);
  check(o.realized_r        === 4,            `4R terminal: realized_r 4, got ${o.realized_r}`);
})();

// ── 9. CONFIRMATION_CLOSE entry timestamp = confirmation bar timestamp ────────
//
// Correction 2: for CC, entry_bar_utc_ms must equal
// detectionResult.confirmation_bar.bar_utc_ms (CONF_BAR_UTC_MS = 1000),
// NOT postConfirmationBars[0].bar_utc_ms.

(function testCCEntryTimestamp() {
  resetMs(9000);  // postConfirmationBars start at 9000 — well after 1000
  const bars = [makeBar(10150, 10060, 9000), makeBar(10200, 10110, 12000)];
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG4);
  check(r.status === 'OK', 'CCTimestamp: OK');
  const o = r.outcome;
  check(o.entry_triggered  === true, 'CCTimestamp: entry triggered');
  // entry_bar_utc_ms must be the confirmation bar timestamp, not 9000
  check(o.entry_bar_utc_ms === CONF_BAR_UTC_MS,
    `CCTimestamp: entry_bar_utc_ms must be ${CONF_BAR_UTC_MS} (conf bar), got ${o.entry_bar_utc_ms}`);
  // first_eval_bar_utc_ms is postConfirmationBars[0].bar_utc_ms = 9000
  check(o.first_eval_bar_index  === 0,
    `CCTimestamp: first_eval_bar_index 0, got ${o.first_eval_bar_index}`);
  check(o.first_eval_bar_utc_ms === 9000,
    `CCTimestamp: first_eval_bar_utc_ms 9000, got ${o.first_eval_bar_utc_ms}`);
  // entry and first_eval are distinct for CC
  check(o.entry_bar_utc_ms !== o.first_eval_bar_utc_ms,
    'CCTimestamp: entry_bar_utc_ms must differ from first_eval_bar_utc_ms for CC');
})();

// ── 10. BOSB entry bar index is within postConfirmationBars ──────────────────

(function testBOSBEntryBarIndex() {
  resetMs(5000);
  const tp = makeTradePlan({ entry_model: 'BREAK_OF_SIGNAL_BAR' });
  // entry=10100; bar[0] misses; bar[1] triggers
  const bars = [
    makeBar(10090, 10050, 5000),
    makeBar(10120, 10060, 8000)
  ];
  const r = evaluateTradeOutcome(makeDetectionResult(), tp, bars, CONFIG4);
  check(r.status === 'OK', 'BOSBIndex: OK');
  const o = r.outcome;
  check(o.entry_triggered === true, 'BOSBIndex: triggered');
  check(o.entry_bar_utc_ms === 8000, `BOSBIndex: entry_bar_utc_ms 8000, got ${o.entry_bar_utc_ms}`);
  check(o.bosb_entry_bar_index === 1, `BOSBIndex: bosb_entry_bar_index 1, got ${o.bosb_entry_bar_index}`);
  check(o.first_eval_bar_index === 1, `BOSBIndex: first_eval_bar_index 1, got ${o.first_eval_bar_index}`);
})();

// ── 11. CC with no post-confirmation bars → OPEN (entry still counted) ───────

(function testCCEmptyBars() {
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), [], CONFIG4);
  check(r.status === 'OK', 'CCEmpty: OK');
  const o = r.outcome;
  check(o.outcome === 'OPEN',          `CCEmpty: OPEN, got ${o.outcome}`);
  check(o.entry_triggered === true,    'CCEmpty: entry_triggered true');
  check(o.entry_bar_utc_ms === CONF_BAR_UTC_MS,
    `CCEmpty: entry_bar_utc_ms = conf bar ${CONF_BAR_UTC_MS}, got ${o.entry_bar_utc_ms}`);
  check(o.first_eval_bar_index === null, 'CCEmpty: first_eval_bar_index null (no bars)');
  check(o.realized_r === null,          'CCEmpty: realized_r null');
})();

// ── 12. Same-bar ambiguity — frozen rule: terminal target only ───────────────
//
// FROZEN (2026-07-25): AMBIGUOUS only when stop + SELECTED TERMINAL target on
// same bar. Intermediate + stop on same bar → STOPPED; intermediate not credited.
//
// Test matrix (entry=10100, stop=10000, 2R=10300, 3R=10400, 4R=10500):
//   Required test 1: exit_target_r=4, bar hi=10320 (>=2R, <3R, <4R), lo=9950 → STOPPED
//   Required test 2: exit_target_r=4, 2R reached earlier, later bar hits stop → STOPPED, highest=2R
//   Required test 3: exit_target_r=4, bar hi=10520 (>=4R), lo=9950 → AMBIGUOUS
//   Required test 4: exit_target_r=3, bar hi=10320 (>=2R, <3R), lo=9950 → STOPPED
//   Required test 5: exit_target_r=3, bar hi=10420 (>=3R), lo=9950 → AMBIGUOUS
//   Required test 6: exit_target_r=2, bar hi=10320 (>=2R), lo=9950 → AMBIGUOUS

// Required test 1: target=4R, stop + 2R (not 4R) on same bar → STOPPED, 2R not credited
(function testAmbig_R4_stop_plus_2R_only() {
  resetMs(2000);
  const bars = [makeBar(10320, 9950)]; // hi=10320 >=2R=10300, <3R=10400, <4R=10500; lo<=stop
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG4);
  check(r.status === 'OK', 'ambigR4_2R: OK');
  const o = r.outcome;
  check(o.outcome === 'STOPPED',
    `ambigR4_2R: expected STOPPED (2R is intermediate, not terminal), got ${o.outcome}`);
  check(o.realized_r === -1, `ambigR4_2R: realized_r -1, got ${o.realized_r}`);
  check(o.highest_target_achieved === null,
    `ambigR4_2R: 2R must NOT be credited on stop bar, got ${o.highest_target_achieved}`);
  check(o.exit_price_ticks === 10000, `ambigR4_2R: exit at stop, got ${o.exit_price_ticks}`);
})();

// Required test 2: target=4R, 2R reached on earlier bar, then stop hit → STOPPED, highest=2R
(function testAmbig_R4_2R_earlier_then_stop() {
  resetMs(2000);
  const bars = [
    makeBar(10350, 10110),  // bar[0]: hi=10350 >=2R; lo>stop → 2R credited cleanly
    makeBar(10120, 9950)    // bar[1]: lo<=stop; hi<4R → STOPPED
  ];
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG4);
  check(r.status === 'OK', 'ambigR4_earlier2R: OK');
  const o = r.outcome;
  check(o.outcome === 'STOPPED', `ambigR4_earlier2R: STOPPED, got ${o.outcome}`);
  check(o.highest_target_achieved === '2R',
    `ambigR4_earlier2R: 2R credited (reached earlier), got ${o.highest_target_achieved}`);
  check(o.realized_r === -1, `ambigR4_earlier2R: realized_r -1, got ${o.realized_r}`);
})();

// Required test 3: target=4R, stop + 4R (terminal) on same bar → AMBIGUOUS
(function testAmbig_R4_stop_plus_4R() {
  resetMs(2000);
  const bars = [makeBar(10520, 9950)]; // hi=10520 >=4R=10500; lo<=stop → AMBIGUOUS
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG4);
  check(r.status === 'OK', 'ambigR4_4R: OK');
  check(r.outcome.outcome === 'AMBIGUOUS',
    `ambigR4_4R: expected AMBIGUOUS (stop + terminal 4R), got ${r.outcome.outcome}`);
  check(r.outcome.realized_r === null, `ambigR4_4R: realized_r null, got ${r.outcome.realized_r}`);
})();

// Required test 4: target=3R, stop + 2R (not 3R) on same bar → STOPPED, 2R not credited
(function testAmbig_R3_stop_plus_2R_only() {
  resetMs(2000);
  const bars = [makeBar(10320, 9950)]; // hi>=2R, <3R; lo<=stop
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG3);
  check(r.status === 'OK', 'ambigR3_2R: OK');
  const o = r.outcome;
  check(o.outcome === 'STOPPED',
    `ambigR3_2R: expected STOPPED (2R intermediate for 3R config), got ${o.outcome}`);
  check(o.highest_target_achieved === null,
    `ambigR3_2R: 2R must NOT be credited on stop bar, got ${o.highest_target_achieved}`);
  check(o.realized_r === -1, `ambigR3_2R: realized_r -1, got ${o.realized_r}`);
})();

// Required test 5: target=3R, stop + 3R (terminal) on same bar → AMBIGUOUS
(function testAmbig_R3_stop_plus_3R() {
  resetMs(2000);
  const bars = [makeBar(10420, 9950)]; // hi=10420 >=3R=10400; lo<=stop → AMBIGUOUS
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG3);
  check(r.status === 'OK', 'ambigR3_3R: OK');
  check(r.outcome.outcome === 'AMBIGUOUS',
    `ambigR3_3R: expected AMBIGUOUS (stop + terminal 3R), got ${r.outcome.outcome}`);
})();

// Required test 6: target=2R, stop + 2R (terminal) on same bar → AMBIGUOUS
(function testAmbig_R2_stop_plus_2R() {
  resetMs(2000);
  const bars = [makeBar(10320, 9950)]; // hi>=2R=10300; lo<=stop → AMBIGUOUS (2R IS terminal)
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG2);
  check(r.status === 'OK', 'ambigR2_2R: OK');
  check(r.outcome.outcome === 'AMBIGUOUS',
    `ambigR2_2R: expected AMBIGUOUS (stop + terminal 2R), got ${r.outcome.outcome}`);
})();

// ── 13. Invalid exit_target_r values rejected ─────────────────────────────────

(function testInvalidExitTargetR() {
  const dr = makeDetectionResult(), tp = makeTradePlan(), bars = [];

  for (const badVal of [undefined, null, 0, 1, 5, '2', 2.5, NaN]) {
    const r = evaluateTradeOutcome(dr, tp, bars, { direction: 'LONG', exit_target_r: badVal });
    check(r.status === 'FAILED', `invalidR: ${JSON.stringify(badVal)} must fail`);
    check(r.failure_code === 'INVALID_CONFIG',
      `invalidR: ${JSON.stringify(badVal)} gave ${r.failure_code}`);
  }
})();

// ── 14. Missing direction rejected ────────────────────────────────────────────

(function testMissingDirection() {
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), [],
    { exit_target_r: 2 });
  check(r.status === 'FAILED', 'missingDir: must fail');
})();

// ── 15. INVALID DetectionResult rejected ─────────────────────────────────────

(function testInvalidDetectionResult() {
  const tp   = makeTradePlan();
  const bars = [];
  const cfg  = CONFIG2;

  const r1 = evaluateTradeOutcome(null, tp, bars, cfg);
  check(r1.status === 'FAILED' && r1.failure_code === 'INVALID_DETECTION_RESULT',
    'invalidDR: null');

  const r2 = evaluateTradeOutcome(
    makeDetectionResult({ schema_version: 'DetectionResult/v0' }), tp, bars, cfg);
  check(r2.status === 'FAILED' && r2.failure_code === 'INVALID_DETECTION_RESULT',
    'invalidDR: wrong schema_version');

  const r3 = evaluateTradeOutcome(
    makeDetectionResult({ status: 'INVALID', failed_stage: 'BREAK_NOT_FOUND' }), tp, bars, cfg);
  check(r3.status === 'FAILED' && r3.failure_code === 'INVALID_DETECTION_RESULT',
    'invalidDR: INVALID status');

  // Old runtime shape (status: 'OK', no schema_version)
  const r4 = evaluateTradeOutcome({ status: 'OK', confirmation_candle: {} }, tp, bars, cfg);
  check(r4.status === 'FAILED' && r4.failure_code === 'INVALID_DETECTION_RESULT',
    'invalidDR: raw runtime shape');
})();

// ── 16. Invalid TradePlan rejected ───────────────────────────────────────────

(function testInvalidTradePlan() {
  const dr = makeDetectionResult(), bars = [], cfg = CONFIG2;

  const r1 = evaluateTradeOutcome(dr, null, bars, cfg);
  check(r1.status === 'FAILED' && r1.failure_code === 'INVALID_TRADE_PLAN', 'invalidTP: null');

  const r2 = evaluateTradeOutcome(dr, makeTradePlan({ schema_version: 'TradePlan/v0' }), bars, cfg);
  check(r2.status === 'FAILED' && r2.failure_code === 'INVALID_TRADE_PLAN', 'invalidTP: schema');

  const zeroRisk = makeTradePlan({
    entry_price: { ticks: 10100, tick_size: TICK },
    stop_price:  { ticks: 10100, tick_size: TICK },
    risk:        { ticks: 0,     tick_size: TICK }
  });
  const r3 = evaluateTradeOutcome(dr, zeroRisk, bars, cfg);
  check(r3.status === 'FAILED' && r3.failure_code === 'INVALID_TRADE_PLAN', 'invalidTP: zero risk');
})();

// ── 17. Tick-size mismatch rejected ──────────────────────────────────────────

(function testTickSizeMismatch() {
  const badBar = {
    bar_utc_ms: 2000,
    open:  { ticks: 10050, tick_size: 0.05 },
    high:  { ticks: 10090, tick_size: 0.01 },
    low:   { ticks: 10010, tick_size: 0.01 },
    close: { ticks: 10060, tick_size: 0.01 },
    volume: null
  };
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), [badBar], CONFIG2);
  check(r.status === 'FAILED' && r.failure_code === 'TICK_SIZE_MISMATCH', 'tickMismatch');
})();

// ── 18. Chronological order enforced ─────────────────────────────────────────

(function testChronologicalOrder() {
  const bars = [makeBar(10150, 10060, 5000), makeBar(10120, 10040, 3000)];
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(), bars, CONFIG2);
  check(r.status === 'FAILED' && r.failure_code === 'BARS_NOT_CHRONOLOGICAL', 'chronological');
})();

// ── 19. Inputs not mutated ────────────────────────────────────────────────────

(function testNoMutation() {
  resetMs(2000);
  const dr   = makeDetectionResult();
  const tp   = makeTradePlan();
  const bars = [makeBar(10150, 10060), makeBar(10350, 9950)];
  const cfg  = { direction: 'LONG', exit_target_r: 4 };

  const origDRStatus    = dr.status;
  const origTPEntry     = tp.entry_price.ticks;
  const origBar0Hi      = bars[0].high.ticks;
  const origCfgR        = cfg.exit_target_r;

  evaluateTradeOutcome(dr, tp, bars, cfg);

  check(dr.status           === origDRStatus, 'mutation: dr.status');
  check(tp.entry_price.ticks === origTPEntry, 'mutation: tp.entry_price.ticks');
  check(bars[0].high.ticks  === origBar0Hi,   'mutation: bars[0].high.ticks');
  check(cfg.exit_target_r   === origCfgR,     'mutation: cfg.exit_target_r');
})();

// ── 20. Immutable output ──────────────────────────────────────────────────────

(function testImmutableOutput() {
  resetMs(2000);
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(),
    [makeBar(10150, 10060)], CONFIG4);
  check(r.status === 'OK', 'immutable: OK');
  check(Object.isFrozen(r.outcome), 'immutable: outcome frozen');
})();

// ── 21. Output schema fields present ─────────────────────────────────────────

(function testOutputSchemaFields() {
  resetMs(2000);
  const r = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(),
    [makeBar(10150, 10060)], CONFIG3);
  check(r.status === 'OK', 'schema: OK');
  const o = r.outcome;
  check(o.schema_version             === 'TradeOutcome/v1', 'schema: schema_version');
  check(o.direction                  === 'LONG',            'schema: direction');
  check(o.selected_exit_target_r     === 3,                 'schema: selected_exit_target_r');
  check(o.selected_exit_target_label === '3R',              'schema: selected_exit_target_label');
  check(typeof o.entry_triggered     === 'boolean',         'schema: entry_triggered boolean');
  check(o.r2_price_ticks             === 10300,             'schema: r2');
  check(o.r3_price_ticks             === 10400,             'schema: r3');
  check(o.r4_price_ticks             === 10500,             'schema: r4');
})();

// ── 22. realized_r values ─────────────────────────────────────────────────────

(function testRealizedR() {
  resetMs(2000);
  // STOPPED → -1
  const rStop = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(),
    [makeBar(10120, 9950)], CONFIG4);
  check(rStop.outcome.realized_r === -1, `realizedR: STOPPED → -1, got ${rStop.outcome.realized_r}`);

  // TARGET_HIT at 2R (exit_target_r=2) → 2
  resetMs(2000);
  const rT2 = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(),
    [makeBar(10350, 10110)], CONFIG2);
  check(rT2.outcome.realized_r === 2, `realizedR: TARGET_HIT 2R → 2, got ${rT2.outcome.realized_r}`);

  // TARGET_HIT at 3R (exit_target_r=3) → 3
  resetMs(2000);
  const rT3 = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(),
    [makeBar(10420, 10110)], CONFIG3);
  check(rT3.outcome.realized_r === 3, `realizedR: TARGET_HIT 3R → 3, got ${rT3.outcome.realized_r}`);

  // OPEN → null
  resetMs(2000);
  const rOpen = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(),
    [makeBar(10150, 10060)], CONFIG4);
  check(rOpen.outcome.realized_r === null, `realizedR: OPEN → null, got ${rOpen.outcome.realized_r}`);

  // AMBIGUOUS → null (stop + selected terminal 4R on same bar)
  resetMs(2000);
  const rAmb = evaluateTradeOutcome(makeDetectionResult(), makeTradePlan(),
    [makeBar(10520, 9950)], CONFIG4);  // hi=10520 >=4R=10500 (terminal); lo<=stop
  check(rAmb.outcome.realized_r === null, `realizedR: AMBIGUOUS → null, got ${rAmb.outcome.realized_r}`);

  // ENTRY_NOT_TRIGGERED → null
  const tpBOSB = makeTradePlan({ entry_model: 'BREAK_OF_SIGNAL_BAR' });
  const rNT = evaluateTradeOutcome(makeDetectionResult(), tpBOSB, [], CONFIG4);
  check(rNT.outcome.realized_r === null, `realizedR: ENTRY_NOT_TRIGGERED → null, got ${rNT.outcome.realized_r}`);
})();

// ── 23. Malformed bars rejected ───────────────────────────────────────────────

(function testMalformedBars() {
  const dr = makeDetectionResult(), tp = makeTradePlan(), cfg = CONFIG2;

  const r1 = evaluateTradeOutcome(dr, tp, 'not-an-array', cfg);
  check(r1.status === 'FAILED' && r1.failure_code === 'INVALID_BARS', 'malformed: non-array');

  const badHighLow = {
    bar_utc_ms: 2000,
    open:  { ticks: 10050, tick_size: TICK },
    high:  { ticks: 9900,  tick_size: TICK },  // high < low
    low:   { ticks: 10010, tick_size: TICK },
    close: { ticks: 10060, tick_size: TICK },
    volume: null
  };
  const r2 = evaluateTradeOutcome(dr, tp, [badHighLow], cfg);
  check(r2.status === 'FAILED' && r2.failure_code === 'INVALID_BARS', 'malformed: high < low');
})();

// ── 24. BOSB same-bar entry+stop+target → AMBIGUOUS ──────────────────────────

(function testBOSBSameBarAmbiguous() {
  resetMs(2000);
  const tp = makeTradePlan({ entry_model: 'BREAK_OF_SIGNAL_BAR' });
  // entry=10100, stop=10000, 2R=10300; one bar triggers all three
  const bars = [makeBar(10350, 9950)];
  const r = evaluateTradeOutcome(makeDetectionResult(), tp, bars, CONFIG2);
  check(r.status === 'OK', 'BOSBambig: OK');
  check(r.outcome.outcome === 'AMBIGUOUS', `BOSBambig: AMBIGUOUS, got ${r.outcome.outcome}`);
})();

// ── 25. SPY 2026-05-26 real integration test ──────────────────────────────────
//
// Tests all three exit target configurations.
// Oracle: STOPPED at 11:15 (lo=750.06 ≤ stop); no target reached before stop.
// The TradePlan stop is 75036 ($750.36), not 75037 ($750.37).
// See Correction 3 in the Final Report for full explanation.

(function testSPY20260526Integration() {
  const { buildSessionContext, buildORB, findBreak,
          findDisplacement, findRetestWindow, findRejection } = require('./bdrr_engine.js');
  const { buildDetectionResult } = require('./bdrr_detection_result.js');
  const { buildTradePlan }       = require('./bdrr_trade_plan.js');

  const csvPath = path.join(__dirname, '..', 'dati', 'SPY_5m.csv');
  const csv = fs.readFileSync(csvPath, 'utf8');
  const lines = csv.split('\n');
  const allCandles = [];
  for (let i = 3; i < lines.length; i++) {
    const line = lines[i].trim(); if (!line) continue;
    const parts = line.split(','); if (parts.length < 5) continue;
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
  const candles = sessions['2026-05-26'];
  check(Array.isArray(candles) && candles.length > 0, 'SPY: candles loaded');
  if (!candles || !candles.length) return;

  const engineConfig = {
    timeframe_minutes: 5, timezone: 'America/New_York', session_open: '09:30',
    orb_start: 'session_open', orb_duration_minutes: 5, level_source: 'ORB_HIGH',
    direction: 'LONG', tick_size: 0.01,
    min_displacement_ticks: null, min_penetration_ticks: null,
    min_close_beyond_level_ticks: 1
  };

  const ctx    = buildSessionContext(candles, engineConfig);
  const orb    = buildORB(ctx.candles, ctx, engineConfig);
  const brk    = findBreak(ctx.candles, orb, engineConfig);
  const disp   = findDisplacement(ctx.candles, orb, brk, engineConfig);
  const retest = findRetestWindow(ctx.candles, orb, brk, disp, engineConfig);
  const rej    = findRejection(ctx.candles, orb, brk, disp, retest, engineConfig);
  check(rej.status === 'OK', 'SPY: Stage 1-5 OK');
  if (rej.status !== 'OK') return;

  const metadata = {
    tick_size: 0.01, preset_id: 'bdrr_spy_orb_high_v1', engine_version: '1.0.0',
    session: {
      symbol: 'SPY', date: '2026-05-26', market_timezone: 'America/New_York',
      session_open_utc_ms:  new Date('2026-05-26T13:30:00.000Z').getTime(),
      session_close_utc_ms: new Date('2026-05-26T20:00:00.000Z').getTime(),
      timeframe_seconds: 300
    }
  };
  const drResult = buildDetectionResult(
    { orb, breakResult: brk, dispResult: disp, retestResult: retest, rejResult: rej },
    metadata
  );
  check(drResult.status === 'OK', 'SPY: buildDetectionResult OK');
  if (drResult.status !== 'OK') return;
  const dr = drResult.detection_result;

  const tpConfig = {
    direction: 'LONG', entry_model: 'CONFIRMATION_CLOSE',
    entry_buffer_ticks: 0, stop_buffer_ticks: 0, tick_size: 0.01
  };
  const tpResult = buildTradePlan(dr, tpConfig);
  check(tpResult.status === 'OK', 'SPY: buildTradePlan OK');
  if (tpResult.status !== 'OK') return;
  const tp = tpResult.trade_plan;

  // Verify stop is 75036 (the actual CSV-derived value), not 75037 (oracle display)
  check(tp.stop_price.ticks === 75036,
    `SPY: stop 75036 ($750.36), got ${tp.stop_price.ticks}`);
  check(tp.entry_price.ticks === 75089,
    `SPY: entry 75089 ($750.89), got ${tp.entry_price.ticks}`);

  // Build post-confirmation canonical bars
  const confIdx = rej.confirmation_candle_index;
  const ts = 0.01;
  function toBar(c) {
    const ms = c.time instanceof Date ? c.time.getTime() : c.time;
    return {
      bar_utc_ms: ms,
      open:  { ticks: Math.round(c.open  / ts), tick_size: ts },
      high:  { ticks: Math.round(c.high  / ts), tick_size: ts },
      low:   { ticks: Math.round(c.low   / ts), tick_size: ts },
      close: { ticks: Math.round(c.close / ts), tick_size: ts },
      volume: null
    };
  }
  const postConfBars = ctx.candles.slice(confIdx + 1).map(toBar);
  check(postConfBars.length > 0, 'SPY: post-conf bars exist');

  const hhmmFmt = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false
  });

  // Confirm entry_bar_utc_ms = confirmation_bar.bar_utc_ms for all configurations
  const confBarMs = dr.confirmation_bar.bar_utc_ms;

  for (const [cfg, label] of [[CONFIG2, '2R'], [CONFIG3, '3R'], [CONFIG4, '4R']]) {
    const res = evaluateTradeOutcome(dr, tp, postConfBars, cfg);
    check(res.status === 'OK', `SPY ${label}: OK`);
    if (res.status !== 'OK') continue;
    const o = res.outcome;

    // Entry timestamp = confirmation bar (Correction 2)
    check(o.entry_bar_utc_ms === confBarMs,
      `SPY ${label}: entry_bar_utc_ms = conf bar ms`);
    const confTime = hhmmFmt.format(new Date(confBarMs));
    check(confTime === '11:05',
      `SPY ${label}: confirmation bar at 11:05 ET, got ${confTime}`);

    // First eval bar = 11:10 ET (one bar after confirmation)
    check(o.first_eval_bar_index === 0,
      `SPY ${label}: first_eval_bar_index 0, got ${o.first_eval_bar_index}`);
    const firstEvalTime = hhmmFmt.format(new Date(o.first_eval_bar_utc_ms));
    check(firstEvalTime === '11:10',
      `SPY ${label}: first_eval bar at 11:10 ET, got ${firstEvalTime}`);

    // Oracle: no target reached before stop, all configurations → STOPPED
    check(o.outcome === 'STOPPED', `SPY ${label}: STOPPED`);
    check(o.exit_price_ticks === 75036, `SPY ${label}: exit at stop 75036`);
    check(o.highest_target_achieved === null, `SPY ${label}: no target before stop`);
    check(o.realized_r === -1, `SPY ${label}: realized_r -1`);

    // Stop bar at 11:15 ET
    const exitBar = postConfBars[o.exit_bar_index];
    const exitTime = hhmmFmt.format(new Date(exitBar.bar_utc_ms));
    check(exitTime === '11:15', `SPY ${label}: stop at 11:15 ET, got ${exitTime}`);
    check(exitBar.low.ticks <= 75036,
      `SPY ${label}: exit bar low (${exitBar.low.ticks}) <= stop (75036)`);
  }

  // Print integration summary for the report
  const r4out = evaluateTradeOutcome(dr, tp, postConfBars, CONFIG4).outcome;
  console.log('\n── SPY 2026-05-26 Integration Outcome ─────────────────────');
  const confT = hhmmFmt.format(new Date(r4out.entry_bar_utc_ms));
  const evalT = hhmmFmt.format(new Date(r4out.first_eval_bar_utc_ms));
  const exitT = hhmmFmt.format(new Date(postConfBars[r4out.exit_bar_index].bar_utc_ms));
  console.log(`  entry_bar_utc_ms (CC):     ${confT} ET  ← confirmation bar`);
  console.log(`  first_eval_bar:            ${evalT} ET  ← first post-conf bar`);
  console.log(`  outcome:                   ${r4out.outcome}`);
  console.log(`  exit bar:                  ${exitT} ET`);
  console.log(`  exit_price_ticks:          ${r4out.exit_price_ticks}  ($${(r4out.exit_price_ticks * 0.01).toFixed(2)})`);
  console.log(`  highest_target_achieved:   ${r4out.highest_target_achieved}`);
  console.log(`  realized_r (all configs):  -1`);
  console.log(`  stop_price_ticks:          ${tp.stop_price.ticks}  ($${(tp.stop_price.ticks * 0.01).toFixed(2)})`);
  console.log('  (oracle display stop $750.37 = 75037t; CSV raw low = 750.3650 → 75036t)');
  console.log('────────────────────────────────────────────────────────────\n');
})();

// ── Report ─────────────────────────────────────────────────────────────────────

console.log('BDRR TradeOutcome/v1 tests');
console.log('===========================');
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
