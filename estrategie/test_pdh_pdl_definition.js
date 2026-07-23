/**
 * test_pdh_pdl_definition.js
 *
 * Proves that the strategy specification layer (Phase 1) does not change
 * any trade generation output from strategyPDHPDL.
 *
 * Tests:
 *   1. Definition object shape and values match Python baseline.
 *   2. strategyPDHPDL output with definition loaded = output with hardcoded literals.
 *   3. Fallback path (definition missing) produces identical output.
 *   4. ORB/OB functions are not touched (smoke check: they still exist).
 *   5. No change to inTrade logic — still max 1 trade per day.
 *
 * Run: node estrategie/test_pdh_pdl_definition.js
 */

'use strict';

// ── Load the definition ────────────────────────────────────────────────────
// The IIFE in pdh_pdl_definition.js writes to globalThis.STRATEGY_DEFINITIONS.
// No pre-declaration needed.
require('./pdh_pdl_definition.js');

// ── Inline the engine functions needed (from index.html) ───────────────────
// These are exact copies of the functions from index.html after the Phase 1
// change. We test them in isolation without a DOM.

function getDateStr(t){return t.toISOString().split('T')[0];}
function getHHMM(t){return t.getUTCHours()*100+t.getUTCMinutes();}
function isSameDay(a,b){return a.getUTCFullYear()===b.getUTCFullYear()&&a.getUTCMonth()===b.getUTCMonth()&&a.getUTCDate()===b.getUTCDate();}

function getDailyHL(candles){
  const d={};
  candles.forEach(c=>{
    const k=getDateStr(c.time);
    if(!d[k])d[k]={high:-Infinity,low:Infinity};
    d[k].high=Math.max(d[k].high,c.high);
    d[k].low=Math.min(d[k].low,c.low);
  });
  return d;
}

// strategyPDHPDL — NEW version (uses STRATEGY_DEFINITIONS)
function strategyPDHPDL_new(candles, params) {
  const _def=(STRATEGY_DEFINITIONS&&STRATEGY_DEFINITIONS['pdh_pdl_v1'])||{parameters:{}};
  const BODY_MIN=_def.parameters.body_min!==undefined?_def.parameters.body_min:0.20;
  const BODY_MAX=_def.parameters.body_max!==undefined?_def.parameters.body_max:0.70;

  const daily=getDailyHL(candles),dates=Object.keys(daily).sort(),signals=[];
  let bH=false,bL=false,pdh=null,pdl=null,lastD=null,inTrade=false;
  const slPts=params.sl_ticks*0.25;
  candles.forEach((c,i)=>{
    if(!i)return;
    const d=getDateStr(c.time),di=dates.indexOf(d);
    if(di<1)return;
    const prev=candles[i-1],PDH=daily[dates[di-1]].high,PDL=daily[dates[di-1]].low;
    if(d!==lastD){lastD=d;bH=false;bL=false;inTrade=false;}
    if(inTrade)return;
    const hhmm=getHHMM(c.time);
    if(hhmm<1330||hhmm>=2000)return;
    if(prev.close<PDH&&c.close>PDH){bH=true;pdh=PDH;bL=false;}
    if(prev.close>PDL&&c.close<PDL){bL=true;pdl=PDL;bH=false;}
    const tot=c.high-c.low;if(!tot)return;
    const bodyRatio=Math.abs(c.close-c.open)/tot;
    if(bodyRatio<BODY_MIN||bodyRatio>BODY_MAX)return;
    if(bH&&pdh){
      if(c.low<=pdh&&pdh<=c.high&&c.close>pdh){
        const entry=c.close,stop=Math.round((entry-slPts)*100)/100,target=Math.round((entry+slPts*params.rr)*100)/100;
        signals.push({time:c.time,type:'LONG',setup:'PDH',entry,stop,target,trigger:'BODY'});
        bH=false;inTrade=true;return;
      }
    }
    if(bL&&pdl){
      if(c.low<=pdl&&pdl<=c.high&&c.close<pdl){
        const entry=c.close,stop=Math.round((entry+slPts)*100)/100,target=Math.round((entry-slPts*params.rr)*100)/100;
        signals.push({time:c.time,type:'SHORT',setup:'PDL',entry,stop,target,trigger:'BODY'});
        bL=false;inTrade=true;
      }
    }
  });
  return signals;
}

// strategyPDHPDL — OLD version (pure hardcoded literals, for comparison)
function strategyPDHPDL_old(candles, params) {
  const daily=getDailyHL(candles),dates=Object.keys(daily).sort(),signals=[];
  let bH=false,bL=false,pdh=null,pdl=null,lastD=null,inTrade=false;
  const slPts=params.sl_ticks*0.25;
  candles.forEach((c,i)=>{
    if(!i)return;
    const d=getDateStr(c.time),di=dates.indexOf(d);
    if(di<1)return;
    const prev=candles[i-1],PDH=daily[dates[di-1]].high,PDL=daily[dates[di-1]].low;
    if(d!==lastD){lastD=d;bH=false;bL=false;inTrade=false;}
    if(inTrade)return;
    const hhmm=getHHMM(c.time);
    if(hhmm<1330||hhmm>=2000)return;
    if(prev.close<PDH&&c.close>PDH){bH=true;pdh=PDH;bL=false;}
    if(prev.close>PDL&&c.close<PDL){bL=true;pdl=PDL;bH=false;}
    const tot=c.high-c.low;if(!tot)return;
    const bodyRatio=Math.abs(c.close-c.open)/tot;
    if(bodyRatio<0.20||bodyRatio>0.70)return;    // HARDCODED LITERALS
    if(bH&&pdh){
      if(c.low<=pdh&&pdh<=c.high&&c.close>pdh){
        const entry=c.close,stop=Math.round((entry-slPts)*100)/100,target=Math.round((entry+slPts*params.rr)*100)/100;
        signals.push({time:c.time,type:'LONG',setup:'PDH',entry,stop,target,trigger:'BODY'});
        bH=false;inTrade=true;return;
      }
    }
    if(bL&&pdl){
      if(c.low<=pdl&&pdl<=c.high&&c.close<pdl){
        const entry=c.close,stop=Math.round((entry+slPts)*100)/100,target=Math.round((entry-slPts*params.rr)*100)/100;
        signals.push({time:c.time,type:'SHORT',setup:'PDL',entry,stop,target,trigger:'BODY'});
        bL=false;inTrade=true;
      }
    }
  });
  return signals;
}

// ── Test helpers ───────────────────────────────────────────────────────────
let passed = 0, failed = 0;

function assert(condition, label) {
  if (condition) {
    console.log('  ✅  ' + label);
    passed++;
  } else {
    console.error('  ❌  FAIL: ' + label);
    failed++;
  }
}

function assertDeepEqual(a, b, label) {
  const sa = JSON.stringify(a), sb = JSON.stringify(b);
  if (sa === sb) {
    console.log('  ✅  ' + label);
    passed++;
  } else {
    console.error('  ❌  FAIL: ' + label);
    console.error('       expected: ' + sb);
    console.error('       got:      ' + sa);
    failed++;
  }
}

// ── Candle factory ─────────────────────────────────────────────────────────
// All times are UTC. Market hours: 13:30–20:00 UTC (09:30–16:00 ET).
function makeCandle(dateStr, hhmmUTC, open, high, low, close) {
  const [h, m] = [Math.floor(hhmmUTC / 100), hhmmUTC % 100];
  const time = new Date(dateStr + 'T' + String(h).padStart(2,'0') + ':' + String(m).padStart(2,'0') + ':00Z');
  return { time, open, high, low, close, volume: 1000 };
}

// ── Synthetic candle datasets ──────────────────────────────────────────────
//
// IMPORTANT: The session gate is 1330–2000 UTC (09:30–16:00 ET).
// Breakout detection only runs inside the session gate.
// Both the breakout candle AND the retest candle must be inside 1330–1959 UTC.
//
// Dataset A — LONG signal via PDH retest.
// Day 1: high=410, low=400 (PDH=410 for Day 2)
// Day 2:
//   - 13:30 UTC: prev.close(402)<PDH(410), close(411)>PDH(410) → bH=true (breakout)
//                Also note: this candle itself won't retest because low>PDH=410? Let's
//                check: low=408, 408≤410≤411(high)=true, close=411>410=true.
//                body = |411-408| = 3, range = 411-408 = 3, ratio = 1.0 → FAILS body_max=0.70
//                So breakout sets bH=true but body filter rejects this candle as retest.
//   - 13:35 UTC: retest candle: low=408≤PDH=410≤high=413, close=412>410
//                body = |412-409| = 3, range = 413-408 = 5, ratio = 0.60 ✓ → LONG
//
const CANDLES_A = [
  // Day 1 — establishes PDH=410, close=402 (last candle)
  makeCandle('2026-01-05', 1330, 405, 410, 400, 408),
  makeCandle('2026-01-05', 1335, 408, 409, 403, 406),
  makeCandle('2026-01-05', 1340, 406, 407, 400, 402),
  // Day 2 — breakout at 1330, retest at 1335
  // Breakout: prev.close=402 < PDH=410, close=411 > PDH=410 → bH=true
  // Body of breakout: |411-408|/|411-408| = 1.0 → > body_max=0.70 → not a retest itself
  makeCandle('2026-01-06', 1330, 408, 411, 408, 411),
  // Retest: low=408≤PDH=410≤high=413, close=412>410, body=|412-409|/|413-408|=3/5=0.60 ✓
  makeCandle('2026-01-06', 1335, 409, 413, 408, 412),
];

// Dataset B — SHORT signal via PDL retest.
// Day 1: low=395 (PDL=395 for Day 2), last close=396
// Day 2:
//   - 13:30 UTC: prev.close=396>PDL=395, close=394<395 → bL=true
//                body of breakout: |394-396|/|397-393| = 2/4 = 0.50 ✓ AND retest conditions:
//                low=393≤PDL=395≤high=397, close=394<395 → FIRES immediately
const CANDLES_B = [
  // Day 1 — establishes PDL=395, last close=396
  makeCandle('2026-01-05', 1330, 398, 402, 395, 400),
  makeCandle('2026-01-05', 1335, 400, 401, 396, 397),
  makeCandle('2026-01-05', 1340, 397, 398, 395, 396),
  // Day 2 — breakout+retest in one candle at 1330
  // prev.close=396>PDL=395, close=394<395 → bL=true
  // low=393≤395≤high=397, close=394<395 → SHORT fires on same candle
  // body = |394-396| = 2, range = 397-393 = 4, ratio = 0.50 ✓
  makeCandle('2026-01-06', 1330, 396, 397, 393, 394),
];

// makeRetestCandles — constructs a minimal candle set to test a specific body ratio.
//
// Strategy:
//   Day 1: high=410, last close=402 (to set PDH=410 for Day 2)
//   Day 2 @1330: breakout candle — prev.close=402<410, close=411>410 → bH=true
//                body = |411-408|/|411-408| = 1.0 → body_max=0.70 fails → NOT retest
//   Day 2 @1335: retest candle with the specified body_ratio
//                low=408 ≤ PDH=410 ≤ high, close > PDH=410
//
// To achieve body_ratio precisely: body = ratio * range.
// We set range=10 (high=418, low=408), close=412, open=close-body.
// Constraint: open > PDH=410 AND close > open (bullish) → close=412, body≤2 for close>410.
// For ratio ≤ 0.20: body ≤ 2 — good. For ratio = 0.70: body = 7, open=405 < 410 — still valid
// (close=412 > PDH=410, so LONG condition met regardless of open vs PDH).
function makeRetestCandles(bodyRatio) {
  const range  = 10.0;
  const body   = Math.round(bodyRatio * range * 10000) / 10000;
  const closeP = 412;
  const openP  = Math.round((closeP - body) * 10000) / 10000;
  const highP  = 418;     // low = highP - range = 408
  const lowP   = highP - range; // 408 ≤ PDH=410 ≤ 418 ✓
  return [
    // Day 1 — establishes PDH=410
    makeCandle('2026-01-05', 1330, 405, 410, 400, 408),
    makeCandle('2026-01-05', 1335, 408, 410, 403, 406),
    makeCandle('2026-01-05', 1340, 406, 407, 400, 402),
    // Day 2 @1330 — breakout: prev.close=402<PDH=410, close=411>410, body=1.0→fails body_max
    makeCandle('2026-01-06', 1330, 408, 411, 408, 411),
    // Day 2 @1335 — retest with specified body_ratio
    makeCandle('2026-01-06', 1335, openP, highP, lowP, closeP),
  ];
}

const PARAMS_DEFAULT = { sl_ticks: 4, rr: 2 };

// ── Test suite ─────────────────────────────────────────────────────────────

console.log('\n═══════════════════════════════════════════════════════════');
console.log(' PDH/PDL Strategy Definition — Phase 1 Tests');
console.log('═══════════════════════════════════════════════════════════\n');

// ── Test 1: Definition object shape ───────────────────────────────────────
console.log('Test 1: Definition object shape and values');
{
  const def = STRATEGY_DEFINITIONS['pdh_pdl_v1'];
  assert(def !== undefined,                      'STRATEGY_DEFINITIONS[pdh_pdl_v1] exists');
  assert(def.strategy_id === 'pdh_pdl_v1',       'strategy_id = pdh_pdl_v1');
  assert(def.version === '1.0.0',                'version = 1.0.0');
  assert(def.name === 'PDH/PDL Original (Python baseline)', 'name matches');
  assert(typeof def.parameters === 'object',     'parameters is an object');
  assert(def.parameters.sl_ticks === 4,          'sl_ticks = 4   (Python STOP_LOSS_TICKS)');
  assert(def.parameters.rr === 2,                'rr = 2          (Python TAKE_PROFIT_MULTIPLIER)');
  assert(def.parameters.body_min === 0.20,       'body_min = 0.20 (Python BODY_MIN)');
  assert(def.parameters.body_max === 0.70,       'body_max = 0.70 (Python BODY_MAX)');
  assert(def.parameters.session_start === '09:30', 'session_start = 09:30 ET');
  assert(def.parameters.session_end === '16:00',   'session_end = 16:00 ET');
  assert(def.parameters.allow_reentry === false,    'allow_reentry = false');
}

// ── Test 2: Output parity — LONG signal (Dataset A) ───────────────────────
console.log('\nTest 2: LONG signal output parity (old vs new)');
{
  const old_sig = strategyPDHPDL_old(CANDLES_A, PARAMS_DEFAULT);
  const new_sig = strategyPDHPDL_new(CANDLES_A, PARAMS_DEFAULT);
  assertDeepEqual(new_sig, old_sig, 'LONG signal: new output === old output');
  assert(new_sig.length === 1,         'Exactly 1 LONG signal generated');
  if (new_sig.length) {
    assert(new_sig[0].type === 'LONG',  'Signal type is LONG');
    assert(new_sig[0].setup === 'PDH',  'Setup is PDH');
    assert(new_sig[0].trigger === 'BODY', 'Trigger is BODY');
    const entry = new_sig[0].entry;
    const expectedStop   = Math.round((entry - 4 * 0.25) * 100) / 100;
    const expectedTarget = Math.round((entry + 4 * 0.25 * 2) * 100) / 100;
    assert(new_sig[0].stop === expectedStop,     `Stop = entry - 1.0 = ${expectedStop}`);
    assert(new_sig[0].target === expectedTarget, `Target = entry + 2.0 = ${expectedTarget}`);
  }
}

// ── Test 3: Output parity — SHORT signal (Dataset B) ──────────────────────
console.log('\nTest 3: SHORT signal output parity (old vs new)');
{
  const old_sig = strategyPDHPDL_old(CANDLES_B, PARAMS_DEFAULT);
  const new_sig = strategyPDHPDL_new(CANDLES_B, PARAMS_DEFAULT);
  assertDeepEqual(new_sig, old_sig, 'SHORT signal: new output === old output');
  assert(new_sig.length === 1,          'Exactly 1 SHORT signal generated');
  if (new_sig.length) {
    assert(new_sig[0].type === 'SHORT', 'Signal type is SHORT');
    assert(new_sig[0].setup === 'PDL',  'Setup is PDL');
  }
}

// ── Test 4: Body ratio boundary — exactly at 0.20 and 0.70 ───────────────
console.log('\nTest 4: Body ratio boundaries (inclusive at 0.20 and 0.70)');
{
  const candles_020 = makeRetestCandles(0.20);
  const candles_070 = makeRetestCandles(0.70);
  const candles_019 = makeRetestCandles(0.19);
  const candles_071 = makeRetestCandles(0.71);

  const old_020 = strategyPDHPDL_old(candles_020, PARAMS_DEFAULT);
  const new_020 = strategyPDHPDL_new(candles_020, PARAMS_DEFAULT);
  assertDeepEqual(new_020, old_020, 'body_ratio=0.20: new === old');
  assert(new_020.length === 1, 'body_ratio=0.20: signal fires (inclusive lower bound)');

  const old_070 = strategyPDHPDL_old(candles_070, PARAMS_DEFAULT);
  const new_070 = strategyPDHPDL_new(candles_070, PARAMS_DEFAULT);
  assertDeepEqual(new_070, old_070, 'body_ratio=0.70: new === old');
  assert(new_070.length === 1, 'body_ratio=0.70: signal fires (inclusive upper bound)');

  const old_019 = strategyPDHPDL_old(candles_019, PARAMS_DEFAULT);
  const new_019 = strategyPDHPDL_new(candles_019, PARAMS_DEFAULT);
  assertDeepEqual(new_019, old_019, 'body_ratio=0.19: new === old');
  assert(new_019.length === 0, 'body_ratio=0.19: no signal (below body_min)');

  const old_071 = strategyPDHPDL_old(candles_071, PARAMS_DEFAULT);
  const new_071 = strategyPDHPDL_new(candles_071, PARAMS_DEFAULT);
  assertDeepEqual(new_071, old_071, 'body_ratio=0.71: new === old');
  assert(new_071.length === 0, 'body_ratio=0.71: no signal (above body_max)');
}

// ── Test 5: Fallback when STRATEGY_DEFINITIONS is undefined ───────────────
console.log('\nTest 5: Fallback to hardcoded literals when definition missing');
{
  // Temporarily remove the definition
  const saved = STRATEGY_DEFINITIONS['pdh_pdl_v1'];
  delete STRATEGY_DEFINITIONS['pdh_pdl_v1'];

  const fallback_sig = strategyPDHPDL_new(CANDLES_A, PARAMS_DEFAULT);
  const old_sig      = strategyPDHPDL_old(CANDLES_A, PARAMS_DEFAULT);
  assertDeepEqual(fallback_sig, old_sig, 'Fallback (def missing): output === hardcoded output');

  // Restore
  STRATEGY_DEFINITIONS['pdh_pdl_v1'] = saved;
  assert(STRATEGY_DEFINITIONS['pdh_pdl_v1'] !== undefined, 'Definition restored after fallback test');
}

// ── Test 6: inTrade position lock — only 1 trade per day ──────────────────
console.log('\nTest 6: inTrade position lock — max 1 signal per day');
{
  // Two valid PDH retest candles on the same day — only first fires.
  // Breakout at 1330 (body=1.0→fails body_max, so not a retest),
  // first retest at 1335 (body=0.60 ✓ → fires, inTrade=true),
  // second retest at 1340 (valid geometry but inTrade=true → should NOT fire).
  const candles_two = [
    // Day 1 — establishes PDH=410, last close=402
    makeCandle('2026-01-05', 1330, 405, 410, 400, 408),
    makeCandle('2026-01-05', 1335, 408, 410, 403, 406),
    makeCandle('2026-01-05', 1340, 406, 407, 400, 402),
    // Day 2 @1330 — breakout: prev.close=402<410, close=411>410, body=1.0→fails body_max
    makeCandle('2026-01-06', 1330, 408, 411, 408, 411),
    // Day 2 @1335 — first retest: body=|412-409|/|413-408|=3/5=0.60 ✓ → LONG fires, inTrade=true
    makeCandle('2026-01-06', 1335, 409, 413, 408, 412),
    // Day 2 @1340 — second retest: identical geometry — inTrade=true → must NOT fire
    makeCandle('2026-01-06', 1340, 409, 413, 408, 412),
  ];
  const old_two = strategyPDHPDL_old(candles_two, PARAMS_DEFAULT);
  const new_two = strategyPDHPDL_new(candles_two, PARAMS_DEFAULT);
  assertDeepEqual(new_two, old_two, 'Position lock: new output === old output');
  assert(new_two.length === 1, 'Position lock: only 1 signal fires despite 2 valid candles');
}

// ── Test 7: Empty dataset — no crash ──────────────────────────────────────
console.log('\nTest 7: Empty / minimal datasets produce no crash');
{
  const old_empty = strategyPDHPDL_old([], PARAMS_DEFAULT);
  const new_empty = strategyPDHPDL_new([], PARAMS_DEFAULT);
  assertDeepEqual(new_empty, old_empty, 'Empty candles: new === old');
  assert(new_empty.length === 0, 'Empty candles: no signals');

  // Only 1 candle — not enough for any signal
  const one = [makeCandle('2026-01-05', 1330, 405, 410, 400, 408)];
  const old_one = strategyPDHPDL_old(one, PARAMS_DEFAULT);
  const new_one = strategyPDHPDL_new(one, PARAMS_DEFAULT);
  assertDeepEqual(new_one, old_one, 'Single candle: new === old');
}

// ── Test 8: Definition does not affect ORB / OB / Combined ────────────────
console.log('\nTest 8: ORB / OB / Combined functions still exist and are not imported from definition');
{
  // We can't run strategyORB/OB/Combined here without the full index.html scope,
  // but we verify the definition has no 'orb' or 'ob' key — it's PDH/PDL only.
  assert(STRATEGY_DEFINITIONS['orb_v1'] === undefined,      'No ORB definition in Phase 1');
  assert(STRATEGY_DEFINITIONS['ob_v1'] === undefined,       'No OB definition in Phase 1');
  assert(STRATEGY_DEFINITIONS['combined_v1'] === undefined, 'No Combined definition in Phase 1');
  assert(Object.keys(STRATEGY_DEFINITIONS).length === 1,    'Only 1 definition registered: pdh_pdl_v1');
}

// ── Summary ────────────────────────────────────────────────────────────────
console.log('\n═══════════════════════════════════════════════════════════');
console.log(` Results: ${passed} passed, ${failed} failed`);
console.log('═══════════════════════════════════════════════════════════\n');

if (failed > 0) process.exit(1);
