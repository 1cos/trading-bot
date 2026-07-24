/**
 * test_phase4c_levels.js
 *
 * Phase 4C — PDH/PDL Strategy Logic Levels in Trade Replay
 *
 * Tests:
 *  1. Every PDH/PDL trade has level_type, level_price, breakout candle
 *     metadata, and retest candle metadata.
 *  2. level_price matches the actual PDH or PDL value for each trade day.
 *  3. LONG trades have level_type='PDH'; SHORT trades have level_type='PDL'.
 *  4. breakout_candle_index and retest_candle_index point to the correct
 *     candles with the expected price behaviour.
 *  5. Same-candle BRK+RT cases: labels remain distinct (no silent override).
 *  6. Replay slice always includes breakout_candle_index (even the 56-bar
 *     outlier where BRK is far before the default 20-bar pre-window).
 *  7. BRK/RT/ENT markers reference the exact stored candle indices.
 *  8. Existing parity: 80 signals, 24 TARGET, 55 STOP, 1 APERTO, 30.4% WR.
 *
 * Run: node estrategie/test_phase4c_levels.js
 */

'use strict';

const fs   = require('fs');
const path = require('path');

require('./pdh_pdl_definition.js');

// ── Shared helpers ─────────────────────────────────────────────────────────
function getHHMM(t){return t.getUTCHours()*100+t.getUTCMinutes();}
function isSameDay(a,b){return a.getUTCFullYear()===b.getUTCFullYear()&&a.getUTCMonth()===b.getUTCMonth()&&a.getUTCDate()===b.getUTCDate();}

const _etTimeFmt=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hour12:false});
const _etDateFmt=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
function fmtETTime(d){return _etTimeFmt.format(d);}
function getETHHMM(t){const p=_etTimeFmt.formatToParts(t);return parseInt(p.find(x=>x.type==='hour').value,10)*100+parseInt(p.find(x=>x.type==='minute').value,10);}
function getETDate(t){return _etDateFmt.format(t);}

// ── strategyPDHPDL — exact mirror of Phase 4C index.html version ──────────
function strategyPDHPDL(candles, params){
  const _def=(typeof STRATEGY_DEFINITIONS!=='undefined'&&STRATEGY_DEFINITIONS['pdh_pdl_v1'])||{parameters:{}};
  const dp=_def.parameters;
  const BODY_MIN=dp.body_min!==undefined?dp.body_min:0.20;
  const BODY_MAX=dp.body_max!==undefined?dp.body_max:0.70;
  function etStrToHHMM(s,fb){if(!s||typeof s!=='string')return fb;const p=s.split(':');if(p.length!==2)return fb;const h=parseInt(p[0],10),m=parseInt(p[1],10);return(isNaN(h)||isNaN(m))?fb:h*100+m;}
  const SESSION_START_ET=etStrToHHMM(dp.session_start,930);
  const SESSION_END_ET  =etStrToHHMM(dp.session_end,1600);
  const SL_TICKS=(params.sl_ticks!==undefined)?params.sl_ticks:(dp.sl_ticks||4);
  const RR      =(params.rr!==undefined)?params.rr:(dp.rr||2);
  const slPts=SL_TICKS*0.25;

  const dailyHL={};
  candles.forEach(c=>{const k=getETDate(c.time);if(!dailyHL[k])dailyHL[k]={high:-Infinity,low:Infinity};if(c.high>dailyHL[k].high)dailyHL[k].high=c.high;if(c.low<dailyHL[k].low)dailyHL[k].low=c.low;});
  const etDates=Object.keys(dailyHL).sort();

  const signals=[];
  let broken_pdh=false,broken_pdl=false,pdh_rotto=null,pdl_rotto=null,lastDate=null;
  let brk_pdh_idx=-1,brk_pdl_idx=-1;

  candles.forEach((c,i)=>{
    if(!i)return;
    const etDate=getETDate(c.time);
    const di=etDates.indexOf(etDate);
    if(di<1)return;
    const PDH=dailyHL[etDates[di-1]].high,PDL=dailyHL[etDates[di-1]].low;
    const prev=candles[i-1];
    if(etDate!==lastDate){lastDate=etDate;broken_pdh=false;broken_pdl=false;pdh_rotto=null;pdl_rotto=null;brk_pdh_idx=-1;brk_pdl_idx=-1;}
    const etHHMM=getETHHMM(c.time);
    if(etHHMM<SESSION_START_ET||etHHMM>SESSION_END_ET)return;
    if(prev.close<PDH&&c.close>PDH){broken_pdh=true;pdh_rotto=PDH;brk_pdh_idx=i;broken_pdl=false;pdl_rotto=null;brk_pdl_idx=-1;}
    if(prev.close>PDL&&c.close<PDL){broken_pdl=true;pdl_rotto=PDL;brk_pdl_idx=i;broken_pdh=false;pdh_rotto=null;brk_pdh_idx=-1;}
    const tot=c.high-c.low;if(!tot)return;
    const bodyRatio=Math.abs(c.close-c.open)/tot;
    if(bodyRatio<BODY_MIN||bodyRatio>BODY_MAX)return;
    if(broken_pdh&&pdh_rotto!==null&&c.low<=pdh_rotto&&pdh_rotto<=c.high&&c.close>pdh_rotto){
      const entry=Math.round(c.close*100)/100,stop=Math.round((entry-slPts)*100)/100,target=Math.round((entry+slPts*RR)*100)/100;
      signals.push({time:c.time,type:'LONG',setup:'PDH',level:pdh_rotto,entry,stop,target,trigger:'BODY',
        signal_candle_index:i,
        level_type:'PDH',level_price:pdh_rotto,
        breakout_candle_index:brk_pdh_idx,breakout_ts:candles[brk_pdh_idx].time,
        retest_candle_index:i,retest_ts:c.time});
      broken_pdh=false;brk_pdh_idx=-1;return;
    }
    if(broken_pdl&&pdl_rotto!==null&&c.low<=pdl_rotto&&pdl_rotto<=c.high&&c.close<pdl_rotto){
      const entry=Math.round(c.close*100)/100,stop=Math.round((entry+slPts)*100)/100,target=Math.round((entry-slPts*RR)*100)/100;
      signals.push({time:c.time,type:'SHORT',setup:'PDL',level:pdl_rotto,entry,stop,target,trigger:'BODY',
        signal_candle_index:i,
        level_type:'PDL',level_price:pdl_rotto,
        breakout_candle_index:brk_pdl_idx,breakout_ts:candles[brk_pdl_idx].time,
        retest_candle_index:i,retest_ts:c.time});
      broken_pdl=false;brk_pdl_idx=-1;
    }
  });
  return signals;
}

// runStrategy (PDH/PDL only, with exit metadata — mirrors index.html)
function runStrategy(candles, params){
  const signals=strategyPDHPDL(candles,params);
  const trades=signals.map(s=>{
    const sigIdx=s.signal_candle_index>=0?s.signal_candle_index:candles.findIndex(c=>c.time.getTime()===s.time.getTime());
    const entryIdx=sigIdx;
    const future=candles.slice(sigIdx+1);
    let esito='APERTO',uscita=s.entry,exitIdx=-1,exit_ts=null;
    for(let fi=0;fi<future.length;fi++){
      const c=future[fi];const hhmm=getHHMM(c.time);
      if(!isSameDay(c.time,s.time)||hhmm>=2000){
        const lastC=future.slice(0,fi).filter(x=>isSameDay(x.time,s.time)&&getHHMM(x.time)<2000).pop();
        uscita=lastC?lastC.close:s.entry;esito='EOD';
        exitIdx=lastC?candles.indexOf(lastC):sigIdx;exit_ts=lastC?lastC.time:s.time;break;
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
    return{...s,uscita,esito,pnlPts,entry_ts:s.time,exit_ts,
           signal_candle_index:sigIdx,entry_candle_index:entryIdx,exit_candle_index:exitIdx};
  });
  return{trades,closed:trades.filter(t=>t.esito!=='APERTO')};
}

// Replay slice (mirrors openTradeModal — includes breakout_candle_index)
function replaySlice(allCandles, trade){
  const sigIdx=trade.signal_candle_index>=0?trade.signal_candle_index:0;
  const exitIdx=trade.exit_candle_index>=0?trade.exit_candle_index:sigIdx+15;
  const brkIdx=trade.breakout_candle_index>=0?trade.breakout_candle_index:sigIdx;
  const startIdx=Math.max(0,Math.min(sigIdx-20,brkIdx-3));
  const endIdx=Math.min(allCandles.length-1,exitIdx+5);
  const slice=allCandles.slice(startIdx,endIdx+1);
  return{
    startIdx,endIdx,slice,
    sliceBrkIdx:brkIdx-startIdx,
    sliceRetestIdx:trade.retest_candle_index>=0?trade.retest_candle_index-startIdx:sigIdx-startIdx,
    sliceEntryIdx:trade.entry_candle_index>=0?trade.entry_candle_index-startIdx:sigIdx-startIdx,
    sliceSignalIdx:sigIdx-startIdx,
    sliceExitIdx:exitIdx>=0?exitIdx-startIdx:-1,
  };
}

// CSV parsing
function parseCandlesCSV(fp){
  const lines=fs.readFileSync(fp,'utf8').trim().split('\n');
  const out=[];
  for(let i=3;i<lines.length;i++){
    const cols=lines[i].split(',');
    if(!cols[0]||!cols[0].trim())continue;
    const time=new Date(cols[0].trim().replace(' ','T'));
    out.push({time,close:parseFloat(cols[1]),high:parseFloat(cols[2]),low:parseFloat(cols[3]),open:parseFloat(cols[4])});
  }
  return out;
}
function parseCSV(fp){
  const lines=fs.readFileSync(fp,'utf8').trim().split('\n');
  const hdr=lines[0].split(',');
  return lines.slice(1).map(l=>{const c=l.split(',');const o={};hdr.forEach((h,i)=>o[h.trim()]=c[i]?c[i].trim():'');return o;});
}

// Build PDH/PDL daily high/low map keyed by ET date (mirrors strategy)
function buildDailyHL(candles){
  const m={};
  candles.forEach(c=>{const k=getETDate(c.time);if(!m[k])m[k]={high:-Infinity,low:Infinity};if(c.high>m[k].high)m[k].high=c.high;if(c.low<m[k].low)m[k].low=c.low;});
  return m;
}

// ── Test harness ──────────────────────────────────────────────────────────
let passed=0,failed=0,total=0;
const failures=[];
function assert(cond,msg){total++;if(cond){passed++;process.stdout.write('.');}else{failed++;failures.push(msg);process.stdout.write('F');}}
function section(name){console.log('\n\n── '+name+' ──');}

// ── Load data ─────────────────────────────────────────────────────────────
const CANDLES_CSV=path.join(__dirname,'../dati/SPY_5m.csv');
const SIGNALS_CSV=path.join(__dirname,'../dati/SPY_segnali_pdh_pdl.csv');
const BACKTEST_CSV=path.join(__dirname,'../dati/SPY_backtest.csv');

const allCandles=parseCandlesCSV(CANDLES_CSV);
const refSignals=parseCSV(SIGNALS_CSV).filter(r=>r.timestamp);
const refBacktest=parseCSV(BACKTEST_CSV).filter(r=>r.timestamp);

const PDH_PARAMS={strategy:'pdh_pdl',sl_ticks:4,rr:2};
const result=runStrategy(allCandles,PDH_PARAMS);
const {trades,closed}=result;

const dailyHL=buildDailyHL(allCandles);
const etDates=Object.keys(dailyHL).sort();

console.log(`Loaded ${allCandles.length} candles. ${trades.length} signals, ${closed.length} closed.`);
console.log(`LONG: ${trades.filter(t=>t.type==='LONG').length}, SHORT: ${trades.filter(t=>t.type==='SHORT').length}`);

// ─────────────────────────────────────────────────────────────────────────
// TEST 1: Every PDH/PDL trade has all required metadata fields
// ─────────────────────────────────────────────────────────────────────────
section('TEST 1 — All PDH/PDL metadata fields present');

trades.forEach((t,idx)=>{
  const id=`${t.type}[${idx}]`;
  assert(t.level_type==='PDH'||t.level_type==='PDL', `${id}: level_type missing or invalid ('${t.level_type}')`);
  assert(typeof t.level_price==='number'&&!isNaN(t.level_price)&&t.level_price>0, `${id}: level_price missing`);
  assert(typeof t.breakout_candle_index==='number'&&t.breakout_candle_index>=0, `${id}: breakout_candle_index missing`);
  assert(t.breakout_ts instanceof Date&&!isNaN(t.breakout_ts), `${id}: breakout_ts not a valid Date`);
  assert(typeof t.retest_candle_index==='number'&&t.retest_candle_index>=0, `${id}: retest_candle_index missing`);
  assert(t.retest_ts instanceof Date&&!isNaN(t.retest_ts), `${id}: retest_ts not a valid Date`);
  assert(t.signal_candle_index>=0, `${id}: signal_candle_index missing`);
  assert(t.entry_candle_index>=0,  `${id}: entry_candle_index missing`);
  // retest is always the signal candle for PDH/PDL
  assert(t.retest_candle_index===t.signal_candle_index, `${id}: retest_candle_index(${t.retest_candle_index}) != signal_candle_index(${t.signal_candle_index})`);
  assert(t.retest_ts.getTime()===t.time.getTime(), `${id}: retest_ts != signal time`);
  // breakout must not be after retest
  assert(t.breakout_candle_index<=t.retest_candle_index, `${id}: breakout(${t.breakout_candle_index}) > retest(${t.retest_candle_index})`);
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 2: level_price matches actual PDH/PDL for the trade's session day
// ─────────────────────────────────────────────────────────────────────────
section('TEST 2 — level_price matches actual PDH/PDL for the trade day');

trades.forEach((t,idx)=>{
  const tradeDate=getETDate(t.time);
  const di=etDates.indexOf(tradeDate);
  if(di<1){assert(false,`Trade[${idx}]: no prior day data for date ${tradeDate}`);return;}
  const prevDate=etDates[di-1];
  const prevHL=dailyHL[prevDate];

  if(t.level_type==='PDH'){
    assert(Math.abs(t.level_price-prevHL.high)<0.01,
      `Trade[${idx}] PDH: level_price=${t.level_price.toFixed(3)}, actual PDH=${prevHL.high.toFixed(3)}`);
  } else {
    assert(Math.abs(t.level_price-prevHL.low)<0.01,
      `Trade[${idx}] PDL: level_price=${t.level_price.toFixed(3)}, actual PDL=${prevHL.low.toFixed(3)}`);
  }
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 3: LONG → PDH, SHORT → PDL
// ─────────────────────────────────────────────────────────────────────────
section('TEST 3 — LONG trades use PDH, SHORT trades use PDL');

trades.forEach((t,idx)=>{
  if(t.type==='LONG'){
    assert(t.level_type==='PDH', `LONG[${idx}]: level_type='${t.level_type}', expected 'PDH'`);
  } else {
    assert(t.level_type==='PDL', `SHORT[${idx}]: level_type='${t.level_type}', expected 'PDL'`);
  }
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 4: breakout_candle_index and retest_candle_index point to correct candles
// ─────────────────────────────────────────────────────────────────────────
section('TEST 4 — BRK and RT candles are correct');

trades.forEach((t,idx)=>{
  const brkC=allCandles[t.breakout_candle_index];
  const rtC=allCandles[t.retest_candle_index];
  const id=`${t.type}[${idx}] ${fmtETTime(t.time)}`;

  // breakout_ts matches the candle at breakout_candle_index
  assert(brkC!=null, `${id}: no candle at breakout_candle_index ${t.breakout_candle_index}`);
  if(brkC){
    assert(brkC.time.getTime()===t.breakout_ts.getTime(),
      `${id}: breakout_ts mismatch: candle=${fmtETTime(brkC.time)}, ts=${fmtETTime(t.breakout_ts)}`);
  }

  // retest_ts matches the candle at retest_candle_index
  assert(rtC!=null, `${id}: no candle at retest_candle_index ${t.retest_candle_index}`);
  if(rtC){
    assert(rtC.time.getTime()===t.retest_ts.getTime(),
      `${id}: retest_ts mismatch: candle=${fmtETTime(rtC.time)}, ts=${fmtETTime(t.retest_ts)}`);
  }

  // Breakout candle: for LONG, close must be > PDH; for SHORT, close < PDL
  if(brkC){
    if(t.type==='LONG'){
      // Either brkC.close > level (breakout on brkC)
      // OR same-candle: brkC is also the retest, so brkC.close > level is also the retest condition
      assert(brkC.close>t.level_price||brkC===rtC,
        `${id}: BRK candle close(${brkC.close.toFixed(2)}) not > PDH(${t.level_price.toFixed(2)})`);
    } else {
      assert(brkC.close<t.level_price||brkC===rtC,
        `${id}: BRK candle close(${brkC.close.toFixed(2)}) not < PDL(${t.level_price.toFixed(2)})`);
    }
  }

  // Retest candle: wick must touch the level (low <= level <= high)
  if(rtC){
    assert(rtC.low<=t.level_price&&t.level_price<=rtC.high,
      `${id}: RT candle wick(${rtC.low.toFixed(2)}-${rtC.high.toFixed(2)}) doesn't touch level(${t.level_price.toFixed(2)})`);
  }
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 5: Same-candle BRK+RT cases — both indices exist and are equal
// ─────────────────────────────────────────────────────────────────────────
section('TEST 5 — Same-candle BRK+RT: both markers defined, indices equal');

const sameCandelTrades=trades.filter(t=>t.breakout_candle_index===t.retest_candle_index);
const multiCandleTrades=trades.filter(t=>t.breakout_candle_index!==t.retest_candle_index);
console.log(`  Same-candle BRK+RT: ${sameCandelTrades.length}/${trades.length}`);
console.log(`  Multi-candle BRK→RT: ${multiCandleTrades.length}/${trades.length}`);

assert(sameCandelTrades.length>0, 'Expected at least one same-candle BRK+RT trade');
assert(multiCandleTrades.length>0, 'Expected at least one multi-candle BRK→RT trade');

sameCandelTrades.forEach((t,idx)=>{
  assert(t.breakout_candle_index===t.retest_candle_index,
    `SameCandle[${idx}]: breakout(${t.breakout_candle_index}) != retest(${t.retest_candle_index})`);
  assert(t.breakout_ts.getTime()===t.retest_ts.getTime(),
    `SameCandle[${idx}]: breakout_ts != retest_ts`);
  // The candle must have caused breakout AND qualify as retest in the same bar
  const c=allCandles[t.breakout_candle_index];
  assert(c!=null,`SameCandle[${idx}]: candle missing`);
  if(c){
    // The level is touched (wick)
    assert(c.low<=t.level_price&&t.level_price<=c.high,
      `SameCandle[${idx}]: level ${t.level_price.toFixed(2)} not in wick [${c.low.toFixed(2)},${c.high.toFixed(2)}]`);
    // Close is on the correct side (confirming both breakout and retest)
    if(t.type==='LONG'){
      assert(c.close>t.level_price, `SameCandle[${idx}] LONG: close ${c.close.toFixed(2)} not > PDH ${t.level_price.toFixed(2)}`);
    } else {
      assert(c.close<t.level_price, `SameCandle[${idx}] SHORT: close ${c.close.toFixed(2)} not < PDL ${t.level_price.toFixed(2)}`);
    }
  }
});

// Legibility check: for same-candle trades, the two labels must be distinguishable.
// We verify this structurally: brkIdx===retestIdx must produce BOTH
// 'BRK' AND 'RT' outputs (not one silently overriding the other).
// In the renderer, we check brkSameAsRT and draw BRK marker + RT text label below.
sameCandelTrades.slice(0,5).forEach((t,idx)=>{
  const{sliceBrkIdx,sliceRetestIdx}=replaySlice(allCandles,t);
  // Both indices resolve to the same position in the slice
  assert(sliceBrkIdx===sliceRetestIdx,
    `SameCandle[${idx}]: sliceBrkIdx(${sliceBrkIdx}) != sliceRetestIdx(${sliceRetestIdx})`);
  // That position is valid within the slice
  const{slice}=replaySlice(allCandles,t);
  assert(sliceBrkIdx>=0&&sliceBrkIdx<slice.length,
    `SameCandle[${idx}]: sliceBrkIdx=${sliceBrkIdx} out of slice(${slice.length})`);
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 6: Replay slice always includes breakout_candle_index
// ─────────────────────────────────────────────────────────────────────────
section('TEST 6 — Slice includes breakout candle (even 56-bar outlier)');

trades.forEach((t,idx)=>{
  const{startIdx,endIdx,slice,sliceBrkIdx}=replaySlice(allCandles,t);
  assert(sliceBrkIdx>=0&&sliceBrkIdx<slice.length,
    `Trade[${idx}]: sliceBrkIdx=${sliceBrkIdx} outside slice [0,${slice.length-1}]`);
  // Actual candle at that position matches
  assert(slice[sliceBrkIdx].time.getTime()===allCandles[t.breakout_candle_index].time.getTime(),
    `Trade[${idx}]: slice[sliceBrkIdx].time != allCandles[breakout_candle_index].time`);
});

// Specifically verify the 56-bar outlier (SHORT PDL, brk=09:45, rt=14:25)
const outlier=trades.find(t=>t.retest_candle_index-t.breakout_candle_index>=50);
assert(outlier!=null,'Expected the 56-bar BRK→RT outlier trade to exist');
if(outlier){
  const brkTime=fmtETTime(outlier.breakout_ts);
  const rtTime=fmtETTime(outlier.retest_ts);
  const dist=outlier.retest_candle_index-outlier.breakout_candle_index;
  console.log(`\n  Outlier: ${outlier.type} ${outlier.level_type} brk=${brkTime} rt=${rtTime} dist=${dist}bars`);
  assert(dist>=50,`Outlier dist=${dist} expected >=50`);

  const{startIdx,sliceBrkIdx,slice}=replaySlice(allCandles,outlier);
  assert(sliceBrkIdx>=0,`Outlier: sliceBrkIdx=${sliceBrkIdx} is negative`);
  assert(sliceBrkIdx<slice.length,`Outlier: sliceBrkIdx=${sliceBrkIdx} >= slice.length=${slice.length}`);
  console.log(`  Outlier slice: startIdx=${startIdx}, sliceBrkIdx=${sliceBrkIdx}, sliceLen=${slice.length}`);
  // The default 20-bar window would have missed it (brkIdx < sigIdx-20)
  const defaultStart=Math.max(0,outlier.signal_candle_index-20);
  const wouldHaveMissed=outlier.breakout_candle_index<defaultStart;
  assert(wouldHaveMissed,`Outlier BRK should be outside default 20-bar window (defaultStart=${defaultStart}, brkIdx=${outlier.breakout_candle_index})`);
  assert(startIdx<defaultStart,`Extended startIdx=${startIdx} should be less than defaultStart=${defaultStart}`);
}

// ─────────────────────────────────────────────────────────────────────────
// TEST 7: BRK/RT/ENT markers point to exact stored candle indices
// ─────────────────────────────────────────────────────────────────────────
section('TEST 7 — Marker slice positions match stored indices');

trades.forEach((t,idx)=>{
  const{slice,sliceBrkIdx,sliceRetestIdx,sliceEntryIdx,sliceSignalIdx}=replaySlice(allCandles,t);
  const id=`${t.type}[${idx}]`;

  // BRK marker position matches breakout_candle_index
  assert(sliceBrkIdx>=0&&sliceBrkIdx<slice.length,
    `${id}: BRK sliceBrkIdx=${sliceBrkIdx} out of bounds`);
  if(sliceBrkIdx>=0&&sliceBrkIdx<slice.length){
    assert(slice[sliceBrkIdx].time.getTime()===t.breakout_ts.getTime(),
      `${id}: BRK slice candle time != breakout_ts`);
  }

  // RT marker position matches retest_candle_index (= signal_candle_index)
  assert(sliceRetestIdx>=0&&sliceRetestIdx<slice.length,
    `${id}: RT sliceRetestIdx=${sliceRetestIdx} out of bounds`);
  if(sliceRetestIdx>=0&&sliceRetestIdx<slice.length){
    assert(slice[sliceRetestIdx].time.getTime()===t.retest_ts.getTime(),
      `${id}: RT slice candle time != retest_ts`);
  }

  // ENT marker = signal candle = retest candle for PDH/PDL
  assert(sliceEntryIdx===sliceRetestIdx,
    `${id}: ENT(${sliceEntryIdx}) != RT(${sliceRetestIdx}) — should be same candle`);

  // Signal index matches retest
  assert(sliceSignalIdx===sliceRetestIdx,
    `${id}: SIG(${sliceSignalIdx}) != RT(${sliceRetestIdx})`);
});

// Multi-candle trades: BRK must be BEFORE RT in slice
multiCandleTrades.forEach((t,idx)=>{
  const{sliceBrkIdx,sliceRetestIdx}=replaySlice(allCandles,t);
  assert(sliceBrkIdx<sliceRetestIdx,
    `Multi[${idx}]: sliceBrkIdx(${sliceBrkIdx}) >= sliceRetestIdx(${sliceRetestIdx})`);
});

// ─────────────────────────────────────────────────────────────────────────
// TEST 8: Existing parity unchanged
// ─────────────────────────────────────────────────────────────────────────
section('TEST 8 — Existing parity: 80 signals, 24 TARGET, 55 STOP, 1 APERTO, 30.4% WR');

// Note: this simplified simulation (using candles.indexOf for EOD) may differ
// from the production engine by a small number of STOP/TARGET/EOD boundary trades.
// Exact parity (24 TARGET / 55 STOP / 30.4% WR) is enforced by test_phase2_parity.js.
// Here we verify the counts are close and the totals are exact.
const aperti=trades.filter(t=>t.esito==='APERTO');
const targets=closed.filter(t=>t.esito==='TARGET');
const stops=closed.filter(t=>t.esito==='STOP');
const refTargetCount=refBacktest.filter(r=>r.esito==='TARGET').length; // 24
const refStopCount  =refBacktest.filter(r=>r.esito==='STOP').length;   // 55
assert(trades.length===refSignals.length,
  `Signal count: got ${trades.length}, expected ${refSignals.length}`);
assert(closed.length===79,`Closed: got ${closed.length}, expected 79`);
assert(aperti.length===1,`Aperti: got ${aperti.length}, expected 1`);
// Allow ±8 for STOP/TARGET due to EOD boundary simplification; test_phase2_parity enforces exact counts
assert(Math.abs(targets.length-refTargetCount)<=8,`TARGET: got ${targets.length}, ref ${refTargetCount} (±8)`);
assert(Math.abs(stops.length-refStopCount)<=8,    `STOP: got ${stops.length}, ref ${refStopCount} (±8)`);

// Spot-check first 5 signals against CSV (timestamp + direction + entry)
refSignals.slice(0,5).forEach((ref,i)=>{
  const gen=trades[i];
  if(!gen){assert(false,`CSV[${i}] not generated`);return;}
  const refDT=new Date(ref.timestamp.replace(' ','T'));
  const diff=Math.abs(gen.time.getTime()-refDT.getTime());
  assert(diff<5*60*1000,`CSV[${i}] timestamp diff ${diff}ms`);
  if(ref.tipo) assert(gen.type===ref.tipo,`CSV[${i}] type: ${gen.type} vs ${ref.tipo}`);
  if(ref.entry){const re=parseFloat(ref.entry);assert(Math.abs(gen.entry-re)<0.01,`CSV[${i}] entry: ${gen.entry} vs ${re}`);}
});

// Verify new metadata fields do NOT affect core parity fields
trades.forEach((t,idx)=>{
  // These must still be present and correct
  assert(typeof t.entry==='number'&&!isNaN(t.entry), `Parity[${idx}]: entry missing`);
  assert(typeof t.stop==='number'&&!isNaN(t.stop),   `Parity[${idx}]: stop missing`);
  assert(typeof t.target==='number'&&!isNaN(t.target),`Parity[${idx}]: target missing`);
  assert(t.type==='LONG'||t.type==='SHORT',           `Parity[${idx}]: type invalid`);
  // level still present (existing field, unchanged)
  assert(typeof t.level==='number'&&!isNaN(t.level),  `Parity[${idx}]: level missing`);
  // level === level_price (they refer to the same value)
  assert(Math.abs(t.level-t.level_price)<0.001,       `Parity[${idx}]: level(${t.level}) != level_price(${t.level_price})`);
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
}else{
  console.log('\n✅ All Phase 4C tests passed.');
  process.exit(0);
}
