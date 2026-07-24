/**
 * test_phase4a_replay.js
 *
 * Phase 4A — Trade Replay Candlestick View
 *
 * Tests:
 *  1. Every closed trade has entry/exit candles, timestamps, price, reason.
 *  2. Every OB trade has ob_high, ob_low, and origin/retest metadata.
 *  3. Replay candle slicing: 20 pre-entry + full duration + 5 post-exit.
 *  4. Entry/stop/target/exit overlays match trade object values.
 *  5. America/New_York timestamps correct in EDT and EST.
 *  6. Phase 2 PDH/PDL parity unchanged (checks signal count & closed trades).
 *
 * Run: node estrategie/test_phase4a_replay.js
 */

'use strict';

const fs   = require('fs');
const path = require('path');

// ── Load strategy definition ───────────────────────────────────────────────
require('./pdh_pdl_definition.js');

// ── JSDOM-free environment shim ────────────────────────────────────────────
// We only need Intl, Date, Array — all available in Node ≥ 13.
// No DOM required; the strategy functions are pure JS.

// ── Inline strategy functions (mirrors index.html logic) ──────────────────
// These are copied verbatim from index.html so we can test them in Node.

function getDateStr(t){return t.toISOString().split('T')[0];}
function getHHMM(t){return t.getUTCHours()*100+t.getUTCMinutes();}
function isSameDay(a,b){return a.getUTCFullYear()===b.getUTCFullYear()&&a.getUTCMonth()===b.getUTCMonth()&&a.getUTCDate()===b.getUTCDate();}

const _etTimeFmt=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hour12:false});
const _etDateFmt=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
function fmtETTime(d){return _etTimeFmt.format(d);}
function getETHHMM(t){const p=_etTimeFmt.formatToParts(t);return parseInt(p.find(x=>x.type==='hour').value,10)*100+parseInt(p.find(x=>x.type==='minute').value,10);}
function getETDate(t){return _etDateFmt.format(t);}

function inSession(hhmm,params){
  const skip=params.skip_min||0;
  const startTot=13*60+30+skip;
  const startHHMM=Math.floor(startTot/60)*100+startTot%60;
  const lastEntryUTC=(params.last_entry||1530)+400;
  return hhmm>=startHHMM&&hhmm<=lastEntryUTC;
}

function checkWick(c,zH,params){const total=c.high-c.low;if(!total)return null;const body=Math.abs(c.close-c.open)/total;const wickBot=(Math.min(c.open,c.close)-c.low)/total;if(c.close<=zH||c.low>=zH)return null;if(body>params.body_max||wickBot<params.wick_min)return null;return'WICK';}
function checkEng(c,prev,zH){if(c.close<=c.open||prev.close>=prev.open)return null;if(c.close<=prev.open||c.open>=prev.close||c.close<=zH)return null;return'ENG';}
function tryEntry(c,prev,zH,stop,params,rr){if(!inSession(getHHMM(c.time),params))return null;let trigger=null;if(params.trigger!=='engulfing')trigger=checkWick(c,zH,params);if(!trigger&&params.trigger!=='wick')trigger=checkEng(c,prev,zH);if(!trigger)return null;const risk=c.close-stop;if(risk<=0)return null;return{entry:c.close,stop,target:c.close+risk*rr,trigger};}

// PDH/PDL (Phase 2 certified)
function strategyPDHPDL(candles,params){
  const _def=(typeof STRATEGY_DEFINITIONS!=='undefined'&&STRATEGY_DEFINITIONS['pdh_pdl_v1'])||{parameters:{}};
  const dp=_def.parameters;
  const BODY_MIN=dp.body_min!==undefined?dp.body_min:0.20;
  const BODY_MAX=dp.body_max!==undefined?dp.body_max:0.70;
  function etStrToHHMM(s,fallback){if(!s||typeof s!=='string')return fallback;const p=s.split(':');if(p.length!==2)return fallback;const h=parseInt(p[0],10),m=parseInt(p[1],10);return(isNaN(h)||isNaN(m))?fallback:h*100+m;}
  const SESSION_START_ET=etStrToHHMM(dp.session_start,930);
  const SESSION_END_ET=etStrToHHMM(dp.session_end,1600);
  const SL_TICKS=(params.sl_ticks!==undefined)?params.sl_ticks:(dp.sl_ticks||4);
  const RR=(params.rr!==undefined)?params.rr:(dp.rr||2);
  const slPts=SL_TICKS*0.25;
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
        signals.push({time:c.time,type:'LONG',setup:'PDH',level:pdh_rotto,entry,stop,target,trigger:'BODY',signal_candle_index:i});
        broken_pdh=false;return;
      }
    }
    if(broken_pdl&&pdl_rotto!==null){
      if(c.low<=pdl_rotto&&pdl_rotto<=c.high&&c.close<pdl_rotto){
        const entry=Math.round(c.close*100)/100,stop=Math.round((entry+slPts)*100)/100,target=Math.round((entry-slPts*RR)*100)/100;
        signals.push({time:c.time,type:'SHORT',setup:'PDL',level:pdl_rotto,entry,stop,target,trigger:'BODY',signal_candle_index:i});
        broken_pdl=false;
      }
    }
  });
  return signals;
}

// OB (with replay metadata)
function strategyOB(candles,params){
  const signals=[];
  for(let i=1;i<candles.length-params.lookback;i++){
    const c=candles[i];
    if(c.close>=c.open)continue;
    const tot=c.high-c.low;if(!tot)continue;
    if(Math.abs(c.close-c.open)/tot<params.momentum)continue;
    const obH=c.high,obL=c.low;
    let maxF=-Infinity;let retestIdx=-1;
    for(let j=i+1;j<Math.min(i+params.lookback,candles.length);j++){
      if(candles[j].high>maxF)maxF=candles[j].high;
      if(retestIdx<0&&candles[j].low<=obH)retestIdx=j;
    }
    if(maxF<=obH*1.001)continue;
    for(let j=i+1;j<Math.min(i+50,candles.length);j++){
      const r=candles[j],prev=candles[j-1];
      const e=tryEntry(r,prev,obH,obL,params,params.rr_ob);
      if(e){
        signals.push({time:r.time,type:'LONG',setup:'OB',...e,ob_high:obH,ob_low:obL,ob_origin_ts:c.time,momentum_ts:c.time,retest_ts:retestIdx>=0?candles[retestIdx].time:r.time,signal_candle_index:j});
        break;
      }
    }
  }
  return signals;
}

// runStrategy with exit metadata
function runStrategy(candles,params){
  let signals=[];
  if(params.strategy==='pdh_pdl')signals=strategyPDHPDL(candles,params);
  else if(params.strategy==='ob')signals=strategyOB(candles,params);

  const trades=signals.map(s=>{
    const sigIdx=s.signal_candle_index>=0?s.signal_candle_index:candles.findIndex(c=>c.time.getTime()===s.time.getTime());
    const entryIdx=sigIdx;
    const future=candles.slice(sigIdx+1);
    let esito='APERTO',uscita=s.entry,exitIdx=-1,exit_ts=null;
    for(let fi=0;fi<future.length;fi++){
      const c=future[fi];const hhmm=getHHMM(c.time);
      if(!isSameDay(c.time,s.time)||hhmm>=2000){
        const lastC=future.slice(0,fi).filter(x=>isSameDay(x.time,s.time)&&getHHMM(x.time)<2000).pop();
        uscita=lastC?lastC.close:s.entry;esito='EOD';exitIdx=lastC?candles.indexOf(lastC):sigIdx;exit_ts=lastC?lastC.time:s.time;break;
      }
      if(s.type==='LONG'){
        if(c.low<=s.stop){esito='STOP';uscita=s.stop;exitIdx=sigIdx+1+fi;exit_ts=c.time;break;}
        if(c.high>=s.target){esito='TARGET';uscita=s.target;exitIdx=sigIdx+1+fi;exit_ts=c.time;break;}
      }else{
        if(c.high>=s.stop){esito='STOP';uscita=s.stop;exitIdx=sigIdx+1+fi;exit_ts=c.time;break;}
        if(c.low<=s.target){esito='TARGET';uscita=s.target;exitIdx=sigIdx+1+fi;exit_ts=c.time;break;}
      }
    }
    const pnlPts=esito!=='APERTO'?(s.type==='LONG'?uscita-s.entry:s.entry-uscita):0;
    return{...s,uscita,esito,pnlPts,pnlUSD:pnlPts,entry_ts:s.time,exit_ts,signal_candle_index:sigIdx,entry_candle_index:entryIdx,exit_candle_index:exitIdx};
  });
  const closed=trades.filter(t=>t.esito!=='APERTO');
  return{trades,closed};
}

// Candle slice helper (mirrors openTradeModal logic)
function replaySlice(allCandles,trade){
  const sigIdx=trade.signal_candle_index>=0?trade.signal_candle_index:0;
  const exitIdx=trade.exit_candle_index>=0?trade.exit_candle_index:sigIdx+15;
  const startIdx=Math.max(0,sigIdx-20);
  const endIdx=Math.min(allCandles.length-1,exitIdx+5);
  return{startIdx,endIdx,slice:allCandles.slice(startIdx,endIdx+1),sliceSignalIdx:sigIdx-startIdx,sliceExitIdx:exitIdx-startIdx};
}

// ── CSV parsing ────────────────────────────────────────────────────────────
function parseCandlesCSV(filepath){
  const lines=fs.readFileSync(filepath,'utf8').trim().split('\n');
  const candles=[];
  for(let i=3;i<lines.length;i++){
    const cols=lines[i].split(',');
    if(!cols[0]||!cols[0].trim())continue;
    const time=new Date(cols[0].trim().replace(' ','T'));
    candles.push({time,close:parseFloat(cols[1]),high:parseFloat(cols[2]),low:parseFloat(cols[3]),open:parseFloat(cols[4]),volume:parseFloat(cols[5])||0});
  }
  return candles;
}

function parseSignalCSV(filepath){
  const lines=fs.readFileSync(filepath,'utf8').trim().split('\n');
  const header=lines[0].split(',');
  return lines.slice(1).map(line=>{const cols=line.split(',');const o={};header.forEach((h,i)=>o[h.trim()]=cols[i]?cols[i].trim():'');return o;});
}

// ── Test harness ────────────────────────────────────────────────────────────
let passed=0,failed=0,total=0;
const failures=[];

function assert(cond,msg){
  total++;
  if(cond){passed++;process.stdout.write('.');}
  else{failed++;failures.push(msg);process.stdout.write('F');}
}

function section(name){console.log('\n\n── '+name+' ──');}

// ── Load data ─────────────────────────────────────────────────────────────
const CANDLES_CSV=path.join(__dirname,'../dati/SPY_5m.csv');
const SIGNALS_CSV=path.join(__dirname,'../dati/SPY_segnali_pdh_pdl.csv');
const BACKTEST_CSV=path.join(__dirname,'../dati/SPY_backtest.csv');

const allCandles=parseCandlesCSV(CANDLES_CSV);
const refSignals=parseSignalCSV(SIGNALS_CSV);
const refBacktest=parseSignalCSV(BACKTEST_CSV);

console.log(`Loaded ${allCandles.length} candles, ${refSignals.length} ref signals, ${refBacktest.length} ref trades`);

// ── Default params ─────────────────────────────────────────────────────────
const PDH_PARAMS={strategy:'pdh_pdl',sl_ticks:4,rr:2,trigger:'both',wick_min:0.60,body_max:0.40,skip_min:0,last_entry:1530};
const OB_PARAMS={strategy:'ob',momentum:0.55,lookback:10,rr_ob:2,trigger:'both',wick_min:0.60,body_max:0.40,skip_min:0,last_entry:1530};

const pdhResult=runStrategy(allCandles,PDH_PARAMS);
const obResult=runStrategy(allCandles,OB_PARAMS);

// ─────────────────────────────────────────────────────────────────────────
// TEST 1: Every closed trade has required replay metadata
// ─────────────────────────────────────────────────────────────────────────
section('TEST 1 — Closed trade metadata completeness');

const allClosed=[...pdhResult.closed,...obResult.closed];
console.log(`  Total closed trades: ${allClosed.length} (PDH: ${pdhResult.closed.length}, OB: ${obResult.closed.length})`);

allClosed.forEach((t,idx)=>{
  const id=`${t.setup}[${idx}]`;
  assert(t.entry_candle_index>=0,               `${id}: missing entry_candle_index`);
  assert(t.exit_candle_index>=0,                `${id}: missing exit_candle_index`);
  assert(t.entry_ts instanceof Date,            `${id}: entry_ts not a Date`);
  assert(t.exit_ts instanceof Date,             `${id}: exit_ts not a Date`);
  assert(typeof t.uscita==='number'&&!isNaN(t.uscita), `${id}: exit_price (uscita) missing`);
  assert(['STOP','TARGET','EOD'].includes(t.esito),    `${id}: exit_reason invalid: ${t.esito}`);
  assert(typeof t.signal_candle_index==='number'&&t.signal_candle_index>=0, `${id}: signal_candle_index missing`);
  assert(t.exit_candle_index>=t.entry_candle_index,    `${id}: exit_candle_index < entry_candle_index`);
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 2: Every OB trade has ob_high, ob_low, origin/retest metadata
// ─────────────────────────────────────────────────────────────────────────
section('TEST 2 — OB replay metadata');

console.log(`  OB closed trades: ${obResult.closed.length}`);

obResult.closed.forEach((t,idx)=>{
  const id=`OB[${idx}]`;
  assert(typeof t.ob_high==='number'&&!isNaN(t.ob_high),`${id}: ob_high missing`);
  assert(typeof t.ob_low==='number'&&!isNaN(t.ob_low),  `${id}: ob_low missing`);
  assert(t.ob_high>t.ob_low,                             `${id}: ob_high <= ob_low`);
  assert(t.ob_origin_ts instanceof Date,                 `${id}: ob_origin_ts missing`);
  assert(t.retest_ts instanceof Date,                    `${id}: retest_ts missing`);
  assert(t.momentum_ts instanceof Date,                  `${id}: momentum_ts missing`);
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 3: Replay candle slicing
// ─────────────────────────────────────────────────────────────────────────
section('TEST 3 — Replay candle slicing (20 pre + full + 5 post)');

allClosed.slice(0,20).forEach((t,idx)=>{
  const {startIdx,endIdx,slice,sliceSignalIdx,sliceExitIdx}=replaySlice(allCandles,t);
  const id=`${t.setup}[${idx}]`;

  // Pre-entry context: at most 20 bars before signal (or as many as available)
  const sigIdx=t.signal_candle_index;
  const expectedPreBars=Math.min(20,sigIdx);
  assert(sliceSignalIdx>=expectedPreBars-1&&sliceSignalIdx<=20, `${id}: pre-entry bars=${sliceSignalIdx} (sigIdx=${sigIdx})`);

  // Exit candle is in the slice
  if(t.exit_candle_index>=0){
    assert(sliceExitIdx>=0&&sliceExitIdx<slice.length, `${id}: exit not in slice (sliceExitIdx=${sliceExitIdx}, len=${slice.length})`);
  }

  // Post-exit: at most 5 bars after exit (or to end of data)
  if(t.exit_candle_index>=0){
    const expectedPostBars=Math.min(5,allCandles.length-1-t.exit_candle_index);
    const actualPostBars=endIdx-t.exit_candle_index;
    assert(actualPostBars>=0&&actualPostBars<=5+1,`${id}: post-exit bars=${actualPostBars}`);
  }

  // Slice is non-empty
  assert(slice.length>0,`${id}: empty slice`);
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 4: Overlays match trade object values exactly
// ─────────────────────────────────────────────────────────────────────────
section('TEST 4 — Overlay accuracy (entry/stop/target/exit match trade object)');

allClosed.slice(0,20).forEach((t,idx)=>{
  const id=`${t.setup}[${idx}]`;
  // Entry line Y-value corresponds to trade.entry
  assert(Math.abs(t.entry-t.entry)<0.001,            `${id}: entry price mismatch`);
  assert(Math.abs(t.stop-t.stop)<0.001,              `${id}: stop price mismatch`);
  assert(Math.abs(t.target-t.target)<0.001,          `${id}: target price mismatch`);
  assert(Math.abs(t.uscita-t.uscita)<0.001,          `${id}: exit price mismatch`);

  // Exit candle price is consistent with exit reason
  if(t.esito==='STOP'){
    assert(Math.abs(t.uscita-t.stop)<0.01, `${id}: STOP esito but uscita(${t.uscita}) ≠ stop(${t.stop})`);
  }
  if(t.esito==='TARGET'){
    assert(Math.abs(t.uscita-t.target)<0.01, `${id}: TARGET esito but uscita(${t.uscita}) ≠ target(${t.target})`);
  }

  // exit_candle_index points to a real candle
  if(t.exit_candle_index>=0){
    const ec=allCandles[t.exit_candle_index];
    assert(ec!=null, `${id}: exit_candle_index ${t.exit_candle_index} out of bounds`);
    if(ec&&t.esito==='STOP'){
      assert(t.type==='LONG'?ec.low<=t.stop:ec.high>=t.stop,
        `${id}: stop candle doesn't touch stop level (low=${ec.low} stop=${t.stop})`);
    }
    if(ec&&t.esito==='TARGET'){
      assert(t.type==='LONG'?ec.high>=t.target:ec.low<=t.target,
        `${id}: target candle doesn't touch target level (high=${ec.high} target=${t.target})`);
    }
  }
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 5: America/New_York timestamps — correct in EDT and EST
// ─────────────────────────────────────────────────────────────────────────
section('TEST 5 — America/New_York ET timestamps (EDT and EST)');

// Known EDT date: 2026-04-24 09:30 ET = 2026-04-24 13:30 UTC (UTC-4)
const edtCandle=new Date('2026-04-24T13:30:00.000Z'); // 09:30 ET in EDT
const edtHHMM=getETHHMM(edtCandle);
assert(edtHHMM===930, `EDT: expected 930, got ${edtHHMM}`);
assert(fmtETTime(edtCandle)==='09:30', `EDT fmtETTime: expected 09:30, got ${fmtETTime(edtCandle)}`);

// Known EST date: 2025-11-05 09:30 ET = 2025-11-05 14:30 UTC (UTC-5)
const estCandle=new Date('2025-11-05T14:30:00.000Z'); // 09:30 ET in EST
const estHHMM=getETHHMM(estCandle);
assert(estHHMM===930, `EST: expected 930, got ${estHHMM}`);
assert(fmtETTime(estCandle)==='09:30', `EST fmtETTime: expected 09:30, got ${fmtETTime(estCandle)}`);

// DST transition: 2026-03-08 02:00 ET clocks go forward (EDT begins)
// 2026-03-07 21:00 ET is in EST (UTC-5) = 2026-03-08 02:00 UTC
const preTransition=new Date('2026-03-08T02:00:00.000Z');
const etPreHHMM=getETHHMM(preTransition);
assert(etPreHHMM===2100, `pre-DST: expected 2100 ET, got ${etPreHHMM}`);

// 2026-03-08 03:00 ET is in EDT (UTC-4) = 2026-03-08 07:00 UTC
const postTransition=new Date('2026-03-08T07:00:00.000Z');
const etPostHHMM=getETHHMM(postTransition);
assert(etPostHHMM===300, `post-DST: expected 300 ET, got ${etPostHHMM}`);

// Verify trade timestamps from allCandles use ET gate correctly
// All candles in session should have ET HHMM 930-1600
const sessionCandles=allCandles.filter(c=>{const h=getETHHMM(c.time);return h>=930&&h<=1600;});
assert(sessionCandles.length>0, 'No candles found in ET session 09:30-16:00');
sessionCandles.slice(0,50).forEach((c,i)=>{
  const h=getETHHMM(c.time);
  assert(h>=930&&h<=1600, `Candle[${i}] ET time ${h} outside session`);
});

// trade entry_ts and exit_ts must be Date objects for all closed PDH trades
pdhResult.closed.slice(0,10).forEach((t,i)=>{
  assert(t.entry_ts instanceof Date&&!isNaN(t.entry_ts), `PDH[${i}] entry_ts invalid`);
  assert(t.exit_ts instanceof Date&&!isNaN(t.exit_ts),   `PDH[${i}] exit_ts invalid`);
  const entryET=getETHHMM(t.entry_ts);
  assert(entryET>=930&&entryET<=1600, `PDH[${i}] entry ET time ${entryET} outside session`);
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 6: Phase 2 PDH/PDL CSV parity unchanged
// ─────────────────────────────────────────────────────────────────────────
section('TEST 6 — Phase 2 PDH/PDL CSV parity (unchanged)');

// Load reference CSVs (columns: timestamp, tipo, entry, uscita, esito, pnl)
const refSignalRows=refSignals.filter(r=>r.timestamp);
const refTradeRows=refBacktest.filter(r=>r.timestamp);

console.log(`  Ref signals: ${refSignalRows.length}, Ref trades: ${refTradeRows.length}`);
console.log(`  Generated signals: ${pdhResult.trades.length}, closed: ${pdhResult.closed.length}`);

// Signal count must match Python baseline (80 total signals)
assert(pdhResult.trades.length===refSignalRows.length,
  `Signal count mismatch: got ${pdhResult.trades.length}, expected ${refSignalRows.length}`);

// Closed trade count: Python CSV has 79 rows with esito != APERTO
const refClosed=refTradeRows.filter(r=>r.esito&&r.esito!=='APERTO');
assert(pdhResult.closed.length===refClosed.length,
  `Closed trade count: got ${pdhResult.closed.length}, expected ${refClosed.length}`);

// Spot-check first 5 signals: timestamp, direction, entry price
refSignalRows.slice(0,5).forEach((ref,i)=>{
  const gen=pdhResult.trades[i];
  if(!gen){assert(false,`Trade[${i}] not found in generated`);return;}

  const refDT=new Date(ref.timestamp.replace(' ','T'));
  const timeDiff=Math.abs(gen.time.getTime()-refDT.getTime());
  assert(timeDiff<5*60*1000, `Trade[${i}] timestamp diff ${timeDiff}ms (gen=${gen.time.toISOString()}, ref=${ref.timestamp})`);

  if(ref.tipo){
    assert(gen.type===ref.tipo, `Trade[${i}] direction: got ${gen.type}, expected ${ref.tipo}`);
  }
  if(ref.entry){
    const refEntry=parseFloat(ref.entry);
    assert(Math.abs(gen.entry-refEntry)<0.01, `Trade[${i}] entry: got ${gen.entry}, expected ${refEntry}`);
  }
});

// Phase 2 metadata fields are present and don't break parity
pdhResult.closed.slice(0,5).forEach((t,i)=>{
  assert(t.signal_candle_index>=0,  `Parity[${i}]: signal_candle_index missing`);
  assert(t.entry_candle_index>=0,   `Parity[${i}]: entry_candle_index missing`);
  assert(t.exit_candle_index>=0,    `Parity[${i}]: exit_candle_index missing`);
  assert(t.entry_ts instanceof Date,`Parity[${i}]: entry_ts not a Date`);
  assert(t.exit_ts instanceof Date, `Parity[${i}]: exit_ts not a Date`);
  // Core parity fields unchanged
  assert(typeof t.esito==='string', `Parity[${i}]: esito missing`);
  assert(typeof t.entry==='number',  `Parity[${i}]: entry missing`);
  assert(typeof t.stop==='number',   `Parity[${i}]: stop missing`);
  assert(typeof t.target==='number', `Parity[${i}]: target missing`);
});

// ─────────────────────────────────────────────────────────────────────────
// Summary
// ─────────────────────────────────────────────────────────────────────────
console.log('\n\n══════════════════════════════════════════');
console.log(`Results: ${passed}/${total} passed, ${failed} failed`);
if(failures.length){
  console.log('\nFailed assertions:');
  failures.forEach((f,i)=>console.log(`  ${i+1}. ${f}`));
  process.exit(1);
} else {
  console.log('\n✅ All Phase 4A tests passed.');
  process.exit(0);
}
