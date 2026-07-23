/**
 * test_pdh_pdl_definition.js
 *
 * Phase 1B — strategy specification layer tests.
 *
 * Covers:
 *   T1.  Definition shape: all seven fields present with correct types/values.
 *   T2.  allow_reentry is true in the canonical definition.
 *   T3.  session_start/session_end convert correctly to UTC HHMM.
 *   T4.  Mutating a definition field changes the resolved engine constant.
 *   T5.  Trade output identical to the Phase 1 baseline (LONG signal, Dataset A).
 *   T6.  Trade output identical to the Phase 1 baseline (SHORT signal, Dataset B).
 *   T7.  Body ratio boundaries 0.19/0.20/0.70/0.71 — new === old.
 *   T8.  Fallback to hardcoded literals when definition is missing.
 *   T9.  inTrade position lock still active — max 1 signal per day.
 *   T10. Candle geometry smoke: session gate uses SESSION_START/END_UTC constants.
 *   T11. ORB / OB / Combined not registered in Phase 1B.
 *
 * Run: node estrategie/test_pdh_pdl_definition.js
 */

'use strict';

// ── Load the definition ────────────────────────────────────────────────────
require('./pdh_pdl_definition.js');

// ── Engine helpers (exact copies from index.html post Phase-1B) ────────────
function getDateStr(t){return t.toISOString().split('T')[0];}
function getHHMM(t){return t.getUTCHours()*100+t.getUTCMinutes();}

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

// strategyPDHPDL — Phase 1B version (all seven fields from definition).
// Must be kept byte-for-byte identical to the copy in index.html.
function strategyPDHPDL_1B(candles,params){
  const _def=(typeof STRATEGY_DEFINITIONS!=='undefined'&&STRATEGY_DEFINITIONS['pdh_pdl_v1'])
             ||{parameters:{}};
  const dp=_def.parameters;

  const BODY_MIN=dp.body_min!==undefined?dp.body_min:0.20;
  const BODY_MAX=dp.body_max!==undefined?dp.body_max:0.70;

  function etStrToUtcHHMM(etStr,fallback){
    if(!etStr||typeof etStr!=='string')return fallback;
    const parts=etStr.split(':');
    if(parts.length!==2)return fallback;
    const h=parseInt(parts[0],10),m=parseInt(parts[1],10);
    if(isNaN(h)||isNaN(m))return fallback;
    return(h+4)*100+m;
  }
  const SESSION_START_UTC=etStrToUtcHHMM(dp.session_start,1330);
  const SESSION_END_UTC  =etStrToUtcHHMM(dp.session_end,  2000);

  const DEF_ALLOW_REENTRY=dp.allow_reentry!==undefined?dp.allow_reentry:true;
  void DEF_ALLOW_REENTRY;

  const slPts=params.sl_ticks*0.25;
  const daily=getDailyHL(candles),dates=Object.keys(daily).sort(),signals=[];
  let bH=false,bL=false,pdh=null,pdl=null,lastD=null,inTrade=false;
  candles.forEach((c,i)=>{
    if(!i)return;
    const d=getDateStr(c.time),di=dates.indexOf(d);
    if(di<1)return;
    const prev=candles[i-1],PDH=daily[dates[di-1]].high,PDL=daily[dates[di-1]].low;
    if(d!==lastD){lastD=d;bH=false;bL=false;inTrade=false;}
    if(inTrade)return;
    const hhmm=getHHMM(c.time);
    if(hhmm<SESSION_START_UTC||hhmm>=SESSION_END_UTC)return;
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

// strategyPDHPDL — hardcoded baseline (Phase 1 copy, no definition reads).
// Used as the ground-truth "oracle" for output parity tests.
function strategyPDHPDL_oracle(candles,params){
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
    if(hhmm<1330||hhmm>=2000)return;    // hardcoded
    if(prev.close<PDH&&c.close>PDH){bH=true;pdh=PDH;bL=false;}
    if(prev.close>PDL&&c.close<PDL){bL=true;pdl=PDL;bH=false;}
    const tot=c.high-c.low;if(!tot)return;
    const bodyRatio=Math.abs(c.close-c.open)/tot;
    if(bodyRatio<0.20||bodyRatio>0.70)return;    // hardcoded
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

// ── Standalone etStrToUtcHHMM — for direct conversion tests ───────────────
function etStrToUtcHHMM(etStr,fallback){
  if(!etStr||typeof etStr!=='string')return fallback;
  const parts=etStr.split(':');
  if(parts.length!==2)return fallback;
  const h=parseInt(parts[0],10),m=parseInt(parts[1],10);
  if(isNaN(h)||isNaN(m))return fallback;
  return(h+4)*100+m;
}

// ── Test helpers ───────────────────────────────────────────────────────────
let passed=0,failed=0;

function assert(condition,label){
  if(condition){console.log('  ✅  '+label);passed++;}
  else{console.error('  ❌  FAIL: '+label);failed++;}
}

function assertDeepEqual(a,b,label){
  const sa=JSON.stringify(a),sb=JSON.stringify(b);
  if(sa===sb){console.log('  ✅  '+label);passed++;}
  else{
    console.error('  ❌  FAIL: '+label);
    console.error('       expected: '+sb.slice(0,120));
    console.error('       got:      '+sa.slice(0,120));
    failed++;
  }
}

// ── Candle factory ─────────────────────────────────────────────────────────
// All times in UTC. Market: 13:30–20:00 UTC = 09:30–16:00 ET (EDT).
function makeCandle(dateStr,hhmmUTC,open,high,low,close){
  const h=Math.floor(hhmmUTC/100),m=hhmmUTC%100;
  const time=new Date(dateStr+'T'+String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':00Z');
  return{time,open,high,low,close,volume:1000};
}

// ── Shared datasets ────────────────────────────────────────────────────────
//
// Day 1 establishes the daily high/low for PDH/PDL on Day 2.
// Breakout and retest both inside 1330–1959 UTC session window.
//
// Dataset A — LONG via PDH retest
//   Day 1: PDH=410. Day 2 @1330: prev.close=402<410, close=411>410 → bH=true
//   Breakout candle body = |411-408|/|411-408|=1.0 → >0.70 → not a retest itself.
//   Day 2 @1335 retest: low=408≤410≤high=413, close=412>410, body=3/5=0.60 ✓
const CANDLES_A=[
  makeCandle('2026-01-05',1330,405,410,400,408),
  makeCandle('2026-01-05',1335,408,410,403,406),
  makeCandle('2026-01-05',1340,406,407,400,402),
  makeCandle('2026-01-06',1330,408,411,408,411),  // breakout; body=1.0→fails body_max
  makeCandle('2026-01-06',1335,409,413,408,412),  // retest; body=3/5=0.60 ✓ → LONG
];

// Dataset B — SHORT via PDL retest
//   Day 1: PDL=395, last close=396.
//   Day 2 @1330: prev.close=396>395, close=394<395 → bL=true + same candle retest fires.
//   low=393≤395≤high=397, close=394<395, body=|394-396|/4=0.50 ✓
const CANDLES_B=[
  makeCandle('2026-01-05',1330,398,402,395,400),
  makeCandle('2026-01-05',1335,400,401,396,397),
  makeCandle('2026-01-05',1340,397,398,395,396),
  makeCandle('2026-01-06',1330,396,397,393,394),  // breakout+retest; body=0.50 ✓ → SHORT
];

// makeRetestCandles — minimal set to test a specific body_ratio
//   Day 1: PDH=410. Day 2 @1330 breakout (body=1.0→excluded), @1335 retest.
function makeRetestCandles(bodyRatio){
  const range=10.0,body=Math.round(bodyRatio*range*10000)/10000;
  const closeP=412,openP=Math.round((closeP-body)*10000)/10000;
  const highP=418,lowP=highP-range; // low=408≤PDH=410≤high=418 ✓
  return[
    makeCandle('2026-01-05',1330,405,410,400,408),
    makeCandle('2026-01-05',1335,408,410,403,406),
    makeCandle('2026-01-05',1340,406,407,400,402),
    makeCandle('2026-01-06',1330,408,411,408,411),            // breakout
    makeCandle('2026-01-06',1335,openP,highP,lowP,closeP),   // retest with target ratio
  ];
}

const PARAMS_DEF={sl_ticks:4,rr:2}; // matches certified baseline exactly

// ══════════════════════════════════════════════════════════════════════════════
console.log('\n═══════════════════════════════════════════════════════════');
console.log(' PDH/PDL Strategy Definition — Phase 1B Tests');
console.log('═══════════════════════════════════════════════════════════\n');

// ── T1: Definition shape — all seven fields ───────────────────────────────
console.log('T1: Definition shape and canonical values');
{
  const def=STRATEGY_DEFINITIONS['pdh_pdl_v1'];
  assert(def!==undefined,                         'STRATEGY_DEFINITIONS[pdh_pdl_v1] exists');
  assert(def.strategy_id==='pdh_pdl_v1',          'strategy_id = pdh_pdl_v1');
  assert(def.version==='1.0.0',                   'version = 1.0.0');
  assert(def.name==='PDH/PDL Original (Python baseline)','name matches');
  assert(typeof def.parameters==='object',         'parameters is an object');
  // Seven canonical parameters
  assert(def.parameters.sl_ticks===4,             'sl_ticks = 4  (Python STOP_LOSS_TICKS)');
  assert(def.parameters.rr===2,                   'rr = 2         (Python TAKE_PROFIT_MULTIPLIER)');
  assert(def.parameters.body_min===0.20,          'body_min = 0.20 (Python BODY_MIN)');
  assert(def.parameters.body_max===0.70,          'body_max = 0.70 (Python BODY_MAX)');
  assert(def.parameters.session_start==='09:30',  'session_start = "09:30" ET');
  assert(def.parameters.session_end==='16:00',    'session_end = "16:00" ET');
  assert(def.parameters.allow_reentry===true,     'allow_reentry = true (canonical; Phase 2 enforcement pending)');
  assert(Object.keys(def.parameters).length===7,  'Exactly 7 parameters defined');
}

// ── T2: allow_reentry is true ─────────────────────────────────────────────
console.log('\nT2: allow_reentry is true in canonical definition');
{
  const def=STRATEGY_DEFINITIONS['pdh_pdl_v1'];
  assert(def.parameters.allow_reentry===true,
    'allow_reentry = true (Python allows re-entry after fresh opposite-side breakout)');
  // Verify the engine still enforces inTrade (see T9) despite allow_reentry=true
  assert(typeof def.parameters.allow_reentry==='boolean',
    'allow_reentry is a boolean (not truthy string or number)');
}

// ── T3: Session conversion — ET strings → UTC HHMM ───────────────────────
console.log('\nT3: Session window ET strings convert to correct UTC HHMM');
{
  // "09:30" ET + 4h = 13:30 UTC = HHMM 1330
  assert(etStrToUtcHHMM('09:30',0)===1330, '"09:30" ET → 1330 UTC');
  // "16:00" ET + 4h = 20:00 UTC = HHMM 2000
  assert(etStrToUtcHHMM('16:00',0)===2000, '"16:00" ET → 2000 UTC');
  // Fallback on bad input
  assert(etStrToUtcHHMM(null,   1330)===1330, 'null → fallback 1330');
  assert(etStrToUtcHHMM('',     2000)===2000, '"" → fallback 2000');
  assert(etStrToUtcHHMM('NOON', 1330)===1330, '"NOON" → fallback 1330');
  // Definition values pass through correctly
  const dp=STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters;
  assert(etStrToUtcHHMM(dp.session_start,0)===1330,
    'Definition session_start → 1330 UTC');
  assert(etStrToUtcHHMM(dp.session_end,0)===2000,
    'Definition session_end → 2000 UTC');
}

// ── T4: Mutating a definition field changes resolved constant ─────────────
console.log('\nT4: Mutating definition field changes resolved config');
{
  const def=STRATEGY_DEFINITIONS['pdh_pdl_v1'];
  const origBodyMin=def.parameters.body_min; // 0.20

  // Change body_min — a candle with ratio 0.15 should now fire (0.15 >= 0.10)
  // makeRetestCandles(0.15) produces a retest candle with body_ratio=0.15
  def.parameters.body_min=0.10;
  const with_010=strategyPDHPDL_1B(makeRetestCandles(0.15),PARAMS_DEF);
  assert(with_010.length===1,
    'body_min=0.10: body_ratio=0.15 signal fires after mutating definition');

  // Restore
  def.parameters.body_min=origBodyMin;
  const restored=strategyPDHPDL_1B(makeRetestCandles(0.15),PARAMS_DEF);
  assert(restored.length===0,
    'body_min restored to 0.20: body_ratio=0.15 correctly blocked');

  // Confirm allow_reentry change is visible when read
  const origAR=def.parameters.allow_reentry;
  def.parameters.allow_reentry=false;
  assert(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.allow_reentry===false,
    'allow_reentry mutation visible on definition object');
  def.parameters.allow_reentry=origAR; // restore
  assert(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.allow_reentry===true,
    'allow_reentry restored to true');
}

// ── T5: Trade output identical — LONG signal (Dataset A) ──────────────────
console.log('\nT5: Phase 1B output === oracle — LONG signal (Dataset A)');
{
  const oracle =strategyPDHPDL_oracle(CANDLES_A,PARAMS_DEF);
  const phase1b=strategyPDHPDL_1B   (CANDLES_A,PARAMS_DEF);
  assertDeepEqual(phase1b,oracle,'LONG: phase1B output === oracle output');
  assert(phase1b.length===1,      'Exactly 1 LONG signal');
  assert(phase1b[0].type==='LONG','Signal type LONG');
  assert(phase1b[0].setup==='PDH','Setup PDH');
  assert(phase1b[0].trigger==='BODY','Trigger BODY');
  const e=phase1b[0].entry;
  assert(phase1b[0].stop  ===Math.round((e-4*0.25)*100)/100,  'Stop = entry − 1.00');
  assert(phase1b[0].target===Math.round((e+4*0.25*2)*100)/100,'Target = entry + 2.00');
}

// ── T6: Trade output identical — SHORT signal (Dataset B) ─────────────────
console.log('\nT6: Phase 1B output === oracle — SHORT signal (Dataset B)');
{
  const oracle =strategyPDHPDL_oracle(CANDLES_B,PARAMS_DEF);
  const phase1b=strategyPDHPDL_1B   (CANDLES_B,PARAMS_DEF);
  assertDeepEqual(phase1b,oracle, 'SHORT: phase1B output === oracle output');
  assert(phase1b.length===1,       'Exactly 1 SHORT signal');
  assert(phase1b[0].type==='SHORT','Signal type SHORT');
  assert(phase1b[0].setup==='PDL', 'Setup PDL');
}

// ── T7: Body ratio boundaries — new === oracle ────────────────────────────
console.log('\nT7: Body ratio boundaries — phase1B === oracle');
{
  for(const[ratio,expectFires] of[[0.19,false],[0.20,true],[0.70,true],[0.71,false]]){
    const candles=makeRetestCandles(ratio);
    const oracle =strategyPDHPDL_oracle(candles,PARAMS_DEF);
    const phase1b=strategyPDHPDL_1B(candles,PARAMS_DEF);
    assertDeepEqual(phase1b,oracle,`body_ratio=${ratio}: phase1B === oracle`);
    assert(
      phase1b.length===(expectFires?1:0),
      `body_ratio=${ratio}: ${expectFires?'signal fires':'no signal'} (${expectFires?'inclusive':'exclusive'} bound)`
    );
  }
}

// ── T8: Fallback when definition is missing ────────────────────────────────
console.log('\nT8: Fallback to hardcoded literals when definition absent');
{
  const saved=STRATEGY_DEFINITIONS['pdh_pdl_v1'];
  delete STRATEGY_DEFINITIONS['pdh_pdl_v1'];

  const fallback=strategyPDHPDL_1B(CANDLES_A,PARAMS_DEF);
  const oracle  =strategyPDHPDL_oracle(CANDLES_A,PARAMS_DEF);
  assertDeepEqual(fallback,oracle,'Fallback output === oracle output');

  STRATEGY_DEFINITIONS['pdh_pdl_v1']=saved;
  assert(STRATEGY_DEFINITIONS['pdh_pdl_v1']!==undefined,'Definition restored after fallback test');
}

// ── T9: inTrade still active — max 1 signal per day ──────────────────────
console.log('\nT9: inTrade position lock still active (despite allow_reentry=true in definition)');
{
  // allow_reentry is true in the definition, but inTrade is still enforced.
  // Two valid retest candles on the same day — only the first should fire.
  const candles_two=[
    makeCandle('2026-01-05',1330,405,410,400,408),
    makeCandle('2026-01-05',1335,408,410,403,406),
    makeCandle('2026-01-05',1340,406,407,400,402),
    makeCandle('2026-01-06',1330,408,411,408,411),  // breakout; body=1.0→blocked
    makeCandle('2026-01-06',1335,409,413,408,412),  // 1st retest; body=0.60 ✓ → fires, inTrade=true
    makeCandle('2026-01-06',1340,409,413,408,412),  // 2nd retest; inTrade=true → blocked
  ];
  // Verify definition says allow_reentry=true
  assert(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.allow_reentry===true,
    'Definition: allow_reentry=true');
  // Verify engine still locks after first signal
  const oracle =strategyPDHPDL_oracle(candles_two,PARAMS_DEF);
  const phase1b=strategyPDHPDL_1B(candles_two,PARAMS_DEF);
  assertDeepEqual(phase1b,oracle,'Lock: phase1B output === oracle');
  assert(phase1b.length===1,
    'inTrade still active: only 1 signal despite allow_reentry=true in definition');
  assert(oracle.length===1,
    'Oracle also yields 1 signal — engine unchanged from Phase 1');
}

// ── T10: Session gate uses converted constants ─────────────────────────────
console.log('\nT10: Session gate honours SESSION_START_UTC and SESSION_END_UTC');
{
  // A candle at exactly 13:29 UTC must be blocked; 13:30 must be allowed.
  // The breakout must happen at 13:30 or later (session-gated).
  // Build a dataset where the only retest candle is at 1329 UTC.
  const before_open=[
    makeCandle('2026-01-05',1330,405,410,400,402), // Day 1
    makeCandle('2026-01-06',1330,402,411,408,411), // breakout
    makeCandle('2026-01-06',1329,409,413,408,412), // retest at 1329 → blocked by session gate
  ];
  const sig_before=strategyPDHPDL_1B(before_open,PARAMS_DEF);
  assert(sig_before.length===0,
    'Candle at 1329 UTC (before session_start) produces no signal');

  // Retest at exactly 1330 UTC — should fire
  const at_open=[
    makeCandle('2026-01-05',1330,405,410,400,402), // Day 1
    makeCandle('2026-01-06',1330,402,411,408,411), // breakout at 1330; body=1.0→blocked
    makeCandle('2026-01-06',1335,409,413,408,412), // retest at 1335 ✓ → fires
  ];
  const sig_at=strategyPDHPDL_1B(at_open,PARAMS_DEF);
  assert(sig_at.length===1,'Candle at 1335 UTC (inside session) fires');

  // Retest at exactly 2000 UTC must be blocked (>= SESSION_END_UTC)
  const at_close=[
    makeCandle('2026-01-05',1330,405,410,400,402), // Day 1
    makeCandle('2026-01-06',1335,402,411,408,411), // breakout
    makeCandle('2026-01-06',2000,409,413,408,412), // retest at 2000 → >= SESSION_END_UTC → blocked
  ];
  const sig_close=strategyPDHPDL_1B(at_close,PARAMS_DEF);
  assert(sig_close.length===0,'Candle at 2000 UTC (session_end) is blocked (>= end)');
}

// ── T11: ORB / OB / Combined not registered ───────────────────────────────
console.log('\nT11: Only pdh_pdl_v1 registered in Phase 1B');
{
  assert(STRATEGY_DEFINITIONS['orb_v1']===undefined,     'No ORB definition');
  assert(STRATEGY_DEFINITIONS['ob_v1']===undefined,      'No OB definition');
  assert(STRATEGY_DEFINITIONS['combined_v1']===undefined,'No Combined definition');
  assert(Object.keys(STRATEGY_DEFINITIONS).length===1,   'Exactly 1 definition: pdh_pdl_v1');
}

// ── Summary ────────────────────────────────────────────────────────────────
console.log('\n═══════════════════════════════════════════════════════════');
console.log(` Results: ${passed} passed, ${failed} failed`);
console.log('═══════════════════════════════════════════════════════════\n');
if(failed>0)process.exit(1);
