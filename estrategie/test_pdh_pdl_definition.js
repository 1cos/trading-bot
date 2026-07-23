/**
 * test_pdh_pdl_definition.js
 *
 * Phase 2 — strategy definition and engine tests.
 *
 * Covers:
 *   T1.  Definition shape: all seven fields, correct types/values, version=2.0.0.
 *   T2.  allow_reentry is true in the canonical definition.
 *   T3.  Session strings parse to ET HHMM integers (no UTC offset applied).
 *   T4.  Mutating a definition field changes the resolved engine constant.
 *   T5.  Phase 2 LONG output === hardcoded oracle (JSON equality).
 *   T6.  Phase 2 SHORT output === hardcoded oracle (JSON equality).
 *   T7.  Body boundary 0.19/0.20/0.70/0.71 — phase2 === oracle.
 *   T8.  Fallback when definition absent produces oracle output.
 *   T9.  allow_reentry=true enforced — re-entry after fresh re-breakout fires.
 *   T10. Session gate uses America/New_York ET: 09:29 blocked, 16:00 passes, 16:01 blocked.
 *   T11. Only pdh_pdl_v1 registered; ORB/OB/Combined absent.
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

// strategyPDHPDL — Phase 2 version (inline copy matching index.html).
// Uses Intl.DateTimeFormat for ET time; no inTrade lock.
function strategyPDHPDL_1B(candles,params){
  const _def=(typeof STRATEGY_DEFINITIONS!=='undefined'&&STRATEGY_DEFINITIONS['pdh_pdl_v1'])||{parameters:{}};
  const dp=_def.parameters;
  const BODY_MIN=dp.body_min!==undefined?dp.body_min:0.20;
  const BODY_MAX=dp.body_max!==undefined?dp.body_max:0.70;
  function etStrToHHMM(s,fallback){if(!s||typeof s!=='string')return fallback;const p=s.split(':');if(p.length!==2)return fallback;const h=parseInt(p[0],10),m=parseInt(p[1],10);return(isNaN(h)||isNaN(m))?fallback:h*100+m;}
  const SESSION_START_ET=etStrToHHMM(dp.session_start,930);
  const SESSION_END_ET  =etStrToHHMM(dp.session_end,  1600);
  void (dp.allow_reentry!==undefined?dp.allow_reentry:true);
  const SL_TICKS=(params.sl_ticks!==undefined)?params.sl_ticks:(dp.sl_ticks||4);
  const RR      =(params.rr      !==undefined)?params.rr      :(dp.rr      ||2);
  const slPts=SL_TICKS*0.25;
  const _etHHMMFmt=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hour12:false});
  const _etDateFmt=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
  function getETHHMM(t){const p=_etHHMMFmt.formatToParts(t);return parseInt(p.find(x=>x.type==='hour').value,10)*100+parseInt(p.find(x=>x.type==='minute').value,10);}
  function getETDate(t){return _etDateFmt.format(t);}
  const dailyHL={};
  candles.forEach(c=>{const k=getETDate(c.time);if(!dailyHL[k])dailyHL[k]={high:-Infinity,low:Infinity};if(c.high>dailyHL[k].high)dailyHL[k].high=c.high;if(c.low<dailyHL[k].low)dailyHL[k].low=c.low;});
  const etDates=Object.keys(dailyHL).sort();
  const signals=[];
  let broken_pdh=false,broken_pdl=false,pdh_rotto=null,pdl_rotto=null,lastDate=null;
  candles.forEach((c,i)=>{
    if(!i)return;
    const etDate=getETDate(c.time);
    const di=etDates.indexOf(etDate);
    if(di<1)return;
    const PDH=dailyHL[etDates[di-1]].high,PDL=dailyHL[etDates[di-1]].low;
    const prev=candles[i-1];
    if(etDate!==lastDate){lastDate=etDate;broken_pdh=false;broken_pdl=false;pdh_rotto=null;pdl_rotto=null;}
    const etHHMM=getETHHMM(c.time);
    if(etHHMM<SESSION_START_ET||etHHMM>SESSION_END_ET)return;
    if(prev.close<PDH&&c.close>PDH){broken_pdh=true;pdh_rotto=PDH;broken_pdl=false;pdl_rotto=null;}
    if(prev.close>PDL&&c.close<PDL){broken_pdl=true;pdl_rotto=PDL;broken_pdh=false;pdh_rotto=null;}
    const tot=c.high-c.low;if(!tot)return;
    const bodyRatio=Math.abs(c.close-c.open)/tot;
    if(bodyRatio<BODY_MIN||bodyRatio>BODY_MAX)return;
    if(broken_pdh&&pdh_rotto!==null){
      if(c.low<=pdh_rotto&&pdh_rotto<=c.high&&c.close>pdh_rotto){
        const entry=Math.round(c.close*100)/100,stop=Math.round((entry-slPts)*100)/100,target=Math.round((entry+slPts*RR)*100)/100;
        signals.push({time:c.time,type:'LONG',setup:'PDH',level:pdh_rotto,entry,stop,target,trigger:'BODY'});
        broken_pdh=false;return;
      }
    }
    if(broken_pdl&&pdl_rotto!==null){
      if(c.low<=pdl_rotto&&pdl_rotto<=c.high&&c.close<pdl_rotto){
        const entry=Math.round(c.close*100)/100,stop=Math.round((entry+slPts)*100)/100,target=Math.round((entry-slPts*RR)*100)/100;
        signals.push({time:c.time,type:'SHORT',setup:'PDL',level:pdl_rotto,entry,stop,target,trigger:'BODY'});
        broken_pdl=false;
      }
    }
  });
  return signals;
}

// strategyPDHPDL — oracle (Phase 2 hardcoded; no definition reads, no inTrade).
// Identical logic to strategyPDHPDL_1B but reads hardcoded 0.20/0.70/930/1600.
// Used as ground-truth for output parity tests where STRATEGY_DEFINITIONS is present.
function strategyPDHPDL_oracle(candles,params){
  const slPts=(params.sl_ticks||4)*0.25, RR=(params.rr||2);
  const _etHHMMFmt=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hour12:false});
  const _etDateFmt=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
  function getETHHMM(t){const p=_etHHMMFmt.formatToParts(t);return parseInt(p.find(x=>x.type==='hour').value,10)*100+parseInt(p.find(x=>x.type==='minute').value,10);}
  function getETDate(t){return _etDateFmt.format(t);}
  const dailyHL={};
  candles.forEach(c=>{const k=getETDate(c.time);if(!dailyHL[k])dailyHL[k]={high:-Infinity,low:Infinity};if(c.high>dailyHL[k].high)dailyHL[k].high=c.high;if(c.low<dailyHL[k].low)dailyHL[k].low=c.low;});
  const etDates=Object.keys(dailyHL).sort();
  const signals=[];
  let broken_pdh=false,broken_pdl=false,pdh_rotto=null,pdl_rotto=null,lastDate=null;
  candles.forEach((c,i)=>{
    if(!i)return;
    const etDate=getETDate(c.time);
    const di=etDates.indexOf(etDate);
    if(di<1)return;
    const PDH=dailyHL[etDates[di-1]].high,PDL=dailyHL[etDates[di-1]].low;
    const prev=candles[i-1];
    if(etDate!==lastDate){lastDate=etDate;broken_pdh=false;broken_pdl=false;pdh_rotto=null;pdl_rotto=null;}
    const etHHMM=getETHHMM(c.time);
    if(etHHMM<930||etHHMM>1600)return;   // hardcoded
    if(prev.close<PDH&&c.close>PDH){broken_pdh=true;pdh_rotto=PDH;broken_pdl=false;pdl_rotto=null;}
    if(prev.close>PDL&&c.close<PDL){broken_pdl=true;pdl_rotto=PDL;broken_pdh=false;pdh_rotto=null;}
    const tot=c.high-c.low;if(!tot)return;
    const bodyRatio=Math.abs(c.close-c.open)/tot;
    if(bodyRatio<0.20||bodyRatio>0.70)return;   // hardcoded
    if(broken_pdh&&pdh_rotto!==null){
      if(c.low<=pdh_rotto&&pdh_rotto<=c.high&&c.close>pdh_rotto){
        const entry=Math.round(c.close*100)/100,stop=Math.round((entry-slPts)*100)/100,target=Math.round((entry+slPts*RR)*100)/100;
        signals.push({time:c.time,type:'LONG',setup:'PDH',level:pdh_rotto,entry,stop,target,trigger:'BODY'});
        broken_pdh=false;return;
      }
    }
    if(broken_pdl&&pdl_rotto!==null){
      if(c.low<=pdl_rotto&&pdl_rotto<=c.high&&c.close<pdl_rotto){
        const entry=Math.round(c.close*100)/100,stop=Math.round((entry+slPts)*100)/100,target=Math.round((entry-slPts*RR)*100)/100;
        signals.push({time:c.time,type:'SHORT',setup:'PDL',level:pdl_rotto,entry,stop,target,trigger:'BODY'});
        broken_pdl=false;
      }
    }
  });
  return signals;
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
  makeCandle('2026-05-05',1330,405,410,400,408),
  makeCandle('2026-05-05',1335,408,410,403,406),
  makeCandle('2026-05-05',1340,406,407,400,402),
  makeCandle('2026-05-06',1330,408,411,408,411),  // breakout; body=1.0→fails body_max
  makeCandle('2026-05-06',1335,409,413,408,412),  // retest; body=3/5=0.60 ✓ → LONG
];

// Dataset B — SHORT via PDL retest
//   Day 1: PDL=395, last close=396.
//   Day 2 @1330: prev.close=396>395, close=394<395 → bL=true + same candle retest fires.
//   low=393≤395≤high=397, close=394<395, body=|394-396|/4=0.50 ✓
const CANDLES_B=[
  makeCandle('2026-05-05',1330,398,402,395,400),
  makeCandle('2026-05-05',1335,400,401,396,397),
  makeCandle('2026-05-05',1340,397,398,395,396),
  makeCandle('2026-05-06',1330,396,397,393,394),  // breakout+retest; body=0.50 ✓ → SHORT
];

// makeRetestCandles — minimal set to test a specific body_ratio
//   Day 1: PDH=410. Day 2 @1330 breakout (body=1.0→excluded), @1335 retest.
function makeRetestCandles(bodyRatio){
  const range=10.0,body=Math.round(bodyRatio*range*10000)/10000;
  const closeP=412,openP=Math.round((closeP-body)*10000)/10000;
  const highP=418,lowP=highP-range; // low=408≤PDH=410≤high=418 ✓
  return[
    makeCandle('2026-05-05',1330,405,410,400,408),
    makeCandle('2026-05-05',1335,408,410,403,406),
    makeCandle('2026-05-05',1340,406,407,400,402),
    makeCandle('2026-05-06',1330,408,411,408,411),            // breakout
    makeCandle('2026-05-06',1335,openP,highP,lowP,closeP),   // retest with target ratio
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
  assert(def.version==='2.0.0',                   'version = 2.0.0');
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

// ── T3: Session strings parse to ET HHMM integers ───────────────────────────────
console.log('\nT3: Session ET strings parse to ET HHMM integers (no UTC offset)');
{
  // Phase 2 engine reads the session window as ET HHMM integers directly.
  // "09:30" → 930,  "16:00" → 1600. No UTC offset is applied.
  function etStrToHHMM(s,fallback){if(!s||typeof s!=='string')return fallback;const p=s.split(':');if(p.length!==2)return fallback;const h=parseInt(p[0],10),m=parseInt(p[1],10);return(isNaN(h)||isNaN(m))?fallback:h*100+m;}
  assert(etStrToHHMM('09:30',0)===930,  '"09:30" → 930 (ET HHMM integer)');
  assert(etStrToHHMM('16:00',0)===1600, '"16:00" → 1600 (ET HHMM integer)');
  assert(etStrToHHMM(null,   930)===930,   'null → fallback 930');
  assert(etStrToHHMM('',    1600)===1600,  '"" → fallback 1600');
  assert(etStrToHHMM('NOON', 930)===930,   '"NOON" → fallback 930');
  const dp=STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters;
  assert(etStrToHHMM(dp.session_start,0)===930,  'Definition session_start → 930 ET');
  assert(etStrToHHMM(dp.session_end,  0)===1600, 'Definition session_end → 1600 ET');
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

// ── T9: allow_reentry=true enforced in Phase 2 ────────────────────────────────────────
console.log('\nT9: allow_reentry=true enforced — re-entry after fresh breakout');
{
  // Phase 2 removes the inTrade lock. After a signal fires (broken_pdh=false),
  // a fresh re-breakout of the same level re-arms the flag and a new signal fires.
  assert(STRATEGY_DEFINITIONS['pdh_pdl_v1'].parameters.allow_reentry===true,
    'Definition: allow_reentry=true (Phase 2 enforced)');

  // Dataset: Day 1 PDH=410.
  // @1330: breakout fires (body=1.0→blocked as retest); broken_pdh=true.
  // @1335: 1st retest fires (body=0.60 ✓), broken_pdh=false.
  // @1340: price drops to 409 (below PDH).
  // @1345: fresh re-breakout (body=1.0→not retest); broken_pdh=true.
  // @1350: 2nd retest fires (body=0.60 ✓).
  const candles_reentry=[
    makeCandle('2026-05-05',1330,405,410,400,408),
    makeCandle('2026-05-05',1335,408,410,403,406),
    makeCandle('2026-05-05',1340,406,407,400,402),
    makeCandle('2026-05-06',1330,408,411,408,411),  // breakout; body=1.0→not retest
    makeCandle('2026-05-06',1335,409,413,408,412),  // 1st retest; body=0.60 ✓ → LONG fires
    makeCandle('2026-05-06',1340,412,413,408,409),  // price drops below PDH
    makeCandle('2026-05-06',1345,409,411,409,411),  // re-breakout; body=1.0→not retest; broken_pdh=true
    makeCandle('2026-05-06',1350,409,413,408,412),  // 2nd retest; body=0.60 ✓ → 2nd LONG fires
  ];
  const sigs=strategyPDHPDL_1B(candles_reentry,PARAMS_DEF);
  assert(sigs.length===2,
    'Phase 2: two LONG signals after fresh re-breakout (allow_reentry=true enforced)');
  if(sigs.length>=2){
    assert(sigs[0].type==='LONG','Signal 1 type LONG');
    assert(sigs[1].type==='LONG','Signal 2 type LONG after re-breakout');
  }
}
// ── T10: Session gate uses America/New_York ET times ──────────────────────────
console.log('\nT10: Session gate uses America/New_York ET times');
{
  // Phase 2 engine reads ET time via Intl.DateTimeFormat('America/New_York').
  // Session: 09:30 ET ≤ hhmm ≤ 16:00 ET (both inclusive, matching Python).
  // makeCandle(dateStr, hhmmUTC, ...) builds UTC timestamps.
  // In summer (EDT, UTC-4): 1329 UTC = 09:29 ET, 1330 UTC = 09:30 ET, 2000 UTC = 16:00 ET.
  // Note: 16:00 ET is INCLUSIVE in Python; Phase 2 engine uses etHHMM > 1600 to block,
  // so 1600 passes. The test at 2001 UTC = 16:01 ET must be blocked.

  // 09:29 ET = before session start → blocked
  const before_open=[
    makeCandle('2026-05-05',1330,405,410,400,402), // Day 1
    makeCandle('2026-05-06',1330,402,411,408,411), // breakout at 09:30 ET
    makeCandle('2026-05-06',1329,409,413,408,412), // retest at 09:29 ET → blocked
  ];
  assert(strategyPDHPDL_1B(before_open,PARAMS_DEF).length===0,
    'Candle at 09:29 ET (before session_start) produces no signal');

  // 09:30 ET breakout + 09:35 ET retest → fires
  const at_open=[
    makeCandle('2026-05-05',1330,405,410,400,402), // Day 1
    makeCandle('2026-05-06',1330,402,411,408,411), // breakout at 09:30 ET; body=1.0→not retest
    makeCandle('2026-05-06',1335,409,413,408,412), // retest at 09:35 ET ✓ → fires
  ];
  assert(strategyPDHPDL_1B(at_open,PARAMS_DEF).length===1,
    'Candle at 09:35 ET (inside session) fires');

  // 16:00 ET = inclusive in Python (session_end) → passes (2000 UTC in EDT)
  // Breakout at 09:35 ET, retest at exactly 16:00 ET (2000 UTC)
  const at_close=[
    makeCandle('2026-05-05',1330,405,410,400,402), // Day 1
    makeCandle('2026-05-06',1335,402,411,408,411), // breakout at 09:35 ET
    makeCandle('2026-05-06',2000,409,413,408,412), // retest at 16:00 ET → inclusive → fires
  ];
  assert(strategyPDHPDL_1B(at_close,PARAMS_DEF).length===1,
    'Candle at 16:00 ET (session_end inclusive) fires — Python parity');

  // 16:01 ET = after session end → blocked (2001 UTC in EDT)
  const after_close=[
    makeCandle('2026-05-05',1330,405,410,400,402), // Day 1
    makeCandle('2026-05-06',1335,402,411,408,411), // breakout at 09:35 ET
    makeCandle('2026-05-06',2001,409,413,408,412), // retest at 16:01 ET → blocked
  ];
  assert(strategyPDHPDL_1B(after_close,PARAMS_DEF).length===0,
    'Candle at 16:01 ET (after session_end) is blocked');
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
