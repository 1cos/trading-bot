"""Generate multi-timeframe review batches from genuine 1-minute data.

Run from trading_bot with venv activated:
    python backend/generate_batches.py

Excludes incomplete sessions (2026-07-30).
Uses the frozen detector and the frozen Training Workspace 8 template.
"""

import csv
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from trading_lab.multi_timeframe_runner import run_multi_timeframe
from trading_lab.timeframe_aggregation import aggregate_post_orb
from trading_lab.visual_review_exporter import export_visual_event
from trading_lab.sequence_validator import validate_sequence
from trading_lab.orb_builder import build_orb
from trading_lab.break_finder import find_break
from trading_lab.displacement_finder import find_displacement
from trading_lab.retest_window import find_retest_window
from trading_lab.rejection_finder import find_rejection
from trading_lab.session_context import build_session_context

ET = ZoneInfo("America/New_York")
DATI = Path("dati")
OUTPUT = Path("backend/output")
EXCLUDE_DATES = {"2026-07-30"}  # incomplete session

SYMBOLS = ["SPY", "QQQ", "AMZN", "TSLA", "NVDA", "META", "MSFT", "GOOGL", "MU"]
TIMEFRAMES = [1, 2, 3, 5, 10]
DIRECTIONS = ["LONG", "SHORT"]

CONFIG = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0"}


def load_1m(symbol):
    path = DATI / f"{symbol}_1m.csv"
    if not path.exists():
        return {}
    candles = []
    with open(path) as f:
        for i, row in enumerate(csv.reader(f)):
            if i < 3: continue
            if not row[0].strip(): continue
            dt = datetime.fromisoformat(row[0])
            candles.append({
                "time_ms": int(dt.timestamp() * 1000),
                "open": float(row[4]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[1]),
                "volume": int(float(row[5])),
            })
    sessions = {}
    for c in candles:
        dt = datetime.fromtimestamp(c["time_ms"]/1000, tz=timezone.utc).astimezone(ET)
        d = dt.strftime("%Y-%m-%d")
        if d in EXCLUDE_DATES: continue
        if d not in sessions: sessions[d] = []
        sessions[d].append(c)
    # Only keep complete sessions (390 bars)
    return {d: bars for d, bars in sessions.items() if len(bars) == 390}


def prev_day_facts(all_sessions, dates, date):
    idx = dates.index(date) if date in dates else -1
    if idx <= 0: return None
    pd = dates[idx-1]
    pc = all_sessions[pd]
    po=pc[0]["open"]; pcl=pc[-1]["close"]
    ph=max(c["high"] for c in pc); pl=min(c["low"] for c in pc)
    dr=ph-pl; nm=pcl-po
    cl=(pcl-pl)/dr if dr>0 else 0.5
    mr=nm/dr if dr>0 else 0
    if mr>0.25: d="bullish"
    elif mr<-0.25: d="bearish"
    else: d="range"
    co=all_sessions[date][0]["open"]; gap=co-pcl; gp=gap/pcl*100
    return {"prev_date":pd,"open":round(po,2),"high":round(ph,2),"low":round(pl,2),
        "close":round(pcl,2),"net_move":round(nm,2),"range":round(dr,2),
        "close_location":round(cl,4),"classification":d,
        "gap_value":round(gap,2),"gap_pct":round(gp,3),
        "prev_candles":pc}


# ── Phase 1: Count setups ──

print("=" * 60)
print("MULTI-TIMEFRAME BATCH GENERATION")
print("=" * 60)

all_valid = []
summary = {}

for sym in SYMBOLS:
    sessions_1m = load_1m(sym)
    if not sessions_1m:
        print(f"  {sym}: no 1m data")
        continue
    dates = sorted(sessions_1m.keys())
    print(f"\n{sym}: {len(dates)} complete sessions ({dates[0]} -> {dates[-1]})")

    for tf in TIMEFRAMES:
        for direction in DIRECTIONS:
            results = run_multi_timeframe(
                sessions_1m, sym, tf, direction,
                preset_overrides={"consecutive_orb_closes": 2},
                config=CONFIG,
            )
            valid = [r for r in results if r["detection_status"] == "VALID"]
            key = f"{sym}_{tf}m_{direction}"
            summary[key] = len(valid)

            for r in valid:
                all_valid.append({
                    "symbol": sym, "date": r["session_date"],
                    "timeframe": f"{tf}m", "direction": direction,
                    "outcome": str(r["outcome"]),
                    "result": r,
                    "sessions_1m": sessions_1m,
                    "all_dates": dates,
                })

            if valid:
                print(f"  {tf:>3}m {direction}: {len(valid)} VALID "
                      f"({', '.join(r['session_date'] for r in valid)})")

print(f"\n{'='*60}")
print(f"TOTAL VALID SETUPS: {len(all_valid)}")
print(f"{'='*60}")

# Summary table
print(f"\n{'Symbol':>6} | {'TF':>3} | {'LONG':>4} | {'SHORT':>5}")
print("-" * 30)
for sym in SYMBOLS:
    for tf in TIMEFRAMES:
        l = summary.get(f"{sym}_{tf}m_LONG", 0)
        s = summary.get(f"{sym}_{tf}m_SHORT", 0)
        if l > 0 or s > 0:
            print(f"{sym:>6} | {tf:>2}m | {l:>4} | {s:>5}")

if not all_valid:
    print("\nNo VALID setups found. Cannot generate review batches.")
    sys.exit(0)

# ── Phase 2: Build review events ──

print(f"\nBuilding {len(all_valid)} review events...")

# Load SPY/QQQ 1m for alignment
spy_sessions = load_1m("SPY")
qqq_sessions = load_1m("QQQ")

def market_align(date, direction):
    facts = {}
    for s, sm in [("SPY", spy_sessions), ("QQQ", qqq_sessions)]:
        if date not in sm: facts[s] = {"available": False}; continue
        c = sm[date]; oh = c[0]["high"]; ol = c[0]["low"]
        sc = c[-1]["close"]; net = sc - c[0]["open"]
        state = "bullish" if net > 0 else ("bearish" if net < 0 else "neutral")
        loc = "above_orb_high" if sc > oh else ("below_orb_low" if sc < ol else "inside_orb")
        agrees = (direction=="LONG" and state=="bullish") or (direction=="SHORT" and state=="bearish")
        facts[s] = {"available": True, "state": state, "location": loc,
            "broke_orb_high": any(x["close"] > oh for x in c[1:]),
            "broke_orb_low": any(x["close"] < ol for x in c[1:]),
            "agrees_with_trade": agrees}
    return facts

events = []
for idx, v in enumerate(all_valid):
    sym = v["symbol"]; date = v["date"]; tf_str = v["timeframe"]
    direction = v["direction"]; r = v["result"]
    sessions_1m = v["sessions_1m"]; all_dates = v["all_dates"]
    tf_min = int(tf_str.replace("m", ""))

    candles_1m = sessions_1m[date]
    orb_summary, post_orb = aggregate_post_orb(candles_1m, tf_min)
    full_candles = [orb_summary] + post_orb

    event = export_visual_event(full_candles, r)
    event["symbol"] = sym
    event["direction"] = direction
    event["timeframe"] = tf_str
    event["sequence_id"] = f"{'L' if direction=='LONG' else 'S'}-{tf_str}-{idx+1:03d}"

    # PDH/PDL from 1m prev day
    d_idx = all_dates.index(date) if date in all_dates else -1
    if d_idx > 0:
        prev_c = sessions_1m[all_dates[d_idx-1]]
        event["pdh"] = max(c["high"] for c in prev_c)
        event["pdl"] = min(c["low"] for c in prev_c)

    # Prev day context
    event["prev_day"] = prev_day_facts(sessions_1m, all_dates, date)

    # Market alignment
    event["market_alignment"] = market_align(date, direction)

    # Premarket
    event["premarket"] = {"available": False, "reason": "No premarket bars in CSV"}

    # Sequence validation data
    ls = "ORB_HIGH" if direction == "LONG" else "ORB_LOW"
    ec = {"timeframe_minutes": tf_min, "timezone": "America/New_York",
        "session_open": "09:30", "orb_start": "session_open",
        "orb_duration_minutes": tf_min, "level_source": ls,
        "direction": direction, "tick_size": 0.01,
        "min_displacement_ticks": None, "min_penetration_ticks": None,
        "min_close_beyond_level_ticks": None, "consecutive_orb_closes": 2}

    try:
        sc = build_session_context(full_candles, ec)
        orb = {"status": "OK", "date": date, "orb_candle_index": 0,
            "orb_candle": full_candles[0], "orb_high": orb_summary["high"],
            "orb_low": orb_summary["low"], "orb_low_active": ls == "ORB_LOW",
            "level_source": ls, "level_price": orb_summary["low"] if ls == "ORB_LOW" else orb_summary["high"],
            "level_price_ticks": int(round((orb_summary["low"] if ls == "ORB_LOW" else orb_summary["high"]) / 0.01)),
            "direction": direction}
        brk = find_break(sc["candles"], orb, ec)
        disp = find_displacement(sc["candles"], orb, brk, ec)
        sv = validate_sequence(sc["candles"], orb, brk, disp, ec)

        if sv["status"] == "INVALIDATED":
            event["invalidation_index"] = sv["invalidation_index"]
            event["consecutive_inside_closes"] = [
                {"bar_index": bi, "close": cv, "time_ms": sc["candles"][bi]["time_ms"],
                 "open": sc["candles"][bi]["open"], "high": sc["candles"][bi]["high"],
                 "low": sc["candles"][bi]["low"]}
                for bi, cv in sv["consecutive_inside_closes"]
            ]
        else:
            event["invalidation_index"] = None
            event["consecutive_inside_closes"] = []

        rc = {**ec}
        if sv["status"] == "INVALIDATED":
            rc["_max_valid_index"] = sv["max_valid_index"]
        rt = find_retest_window(sc["candles"], orb, brk, disp, rc)
        rej = find_rejection(sc["candles"], orb, brk, disp, rt, rc) if rt["status"] == "OK" else None
        event["wick_depth_ticks"] = rej.get("wick_depth_ticks") if rej and rej.get("status") == "OK" else None
        event["all_retest_candidates"] = [
            {"bar_index": fr["candle_index"], "failed_rules": fr["failed_rules"]}
            for fr in (rej.get("failed_retests", []) if rej else [])
        ]

        # Setup geometry
        if rej and rej.get("status") == "OK":
            cc = rej["confirmation_candle"]
            level = orb["level_price"]
            cc_range = cc["high"] - cc["low"]
            cc_body = abs(cc["close"] - cc["open"])
            if direction == "LONG":
                body_edge = min(cc["open"], cc["close"])
                bd = int(round((body_edge - level) / 0.01))
                sd = int(round((cc["close"] - cc["low"]) / 0.01))
            else:
                body_edge = max(cc["open"], cc["close"])
                bd = int(round((level - body_edge) / 0.01))
                sd = int(round((cc["high"] - cc["close"]) / 0.01))
            conf_dt = datetime.fromtimestamp(cc["time_ms"]/1000, tz=timezone.utc).astimezone(ET)
            event["facts"] = {
                "schema_version": "training_facts/v1",
                "setup_geometry": {
                    "wick_depth_ticks": event["wick_depth_ticks"],
                    "body_distance_from_orb_ticks": bd,
                    "confirmation_range_ticks": int(round(cc_range / 0.01)),
                    "confirmation_body_ticks": int(round(cc_body / 0.01)),
                    "stop_distance_ticks": sd,
                    "confirmation_delay_bars": rej["confirmation_candle_index"] - brk["break_candle_index"],
                    "confirmation_time": conf_dt.strftime("%H:%M"),
                },
                "previous_day": {k: v for k, v in (event.get("prev_day") or {}).items() if k != "prev_candles"} or None,
                "opening": {"gap_value": (event.get("prev_day") or {}).get("gap_value"), "gap_pct": (event.get("prev_day") or {}).get("gap_pct")},
                "premarket": {"available": False},
                "market_alignment": event.get("market_alignment", {}),
                "order_block": {"available": False, "reason": "Not yet implemented"},
            }
    except Exception as e:
        event["invalidation_index"] = None
        event["consecutive_inside_closes"] = []
        event["wick_depth_ticks"] = None
        event["all_retest_candidates"] = []
        event["facts"] = {"error": str(e)}

    events.append(event)

print(f"Built {len(events)} events")

# ── Phase 3: Generate workspace HTML ──

# Read the workspace template structure from training_workspace_8.html
# and generate new batch files using the same visual template

def write_batch_html(events_list, filename, title):
    """Write a review batch using the frozen workspace template."""
    events_json = json.dumps(events_list, separators=(",", ":"),
                              ensure_ascii=True, allow_nan=False)

    html = '''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>''' + title + '''</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f8f9fa;--s:#fff;--b:#d0d7de;--t:#1a1a1a;--m:#656d76;--a:#1565c0;--g:#116329;--r:#a40e26;--o:#e65100}
body{font-family:"SF Mono","Cascadia Code",Consolas,monospace;background:var(--bg);color:var(--t);font-size:14px}
.topbar{display:flex;align-items:center;padding:12px 20px;background:var(--s);border-bottom:1px solid var(--b);gap:14px;flex-wrap:wrap;position:sticky;top:0;z-index:100}
.topbar h1{font-size:18px;font-weight:700;color:var(--a)}
.counter{font-size:14px;color:var(--m)}
.btn{padding:8px 18px;border:1px solid var(--b);border-radius:4px;background:var(--s);font-family:inherit;font-size:14px;cursor:pointer;font-weight:600}
.btn:hover{background:#f0f0f0}
.btn-accent{background:var(--a);color:#fff;border-color:var(--a)}
.tag{display:inline-block;padding:3px 10px;border-radius:3px;font-size:13px;font-weight:600}
.tag-long{background:#dafbe1;color:var(--g)}.tag-short{background:#ffebe9;color:var(--r)}
.tag-win{background:#dafbe1;color:var(--g)}.tag-loss{background:#ffebe9;color:var(--r)}
.tag-tf{background:#dbeafe;color:var(--a)}
.content{padding:16px 24px}
.charts-row{display:grid;grid-template-columns:3fr 1fr;gap:14px;margin-bottom:14px}
.chart-box{background:var(--s);border:1px solid var(--b);border-radius:6px;overflow:hidden}
.chart-box-title{font-size:12px;font-weight:700;color:var(--a);text-transform:uppercase;letter-spacing:.06em;padding:10px 14px;border-bottom:1px solid var(--b)}
.chart-inner{height:480px}.chart-inner-sm{height:340px}
.facts-grid{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;margin-bottom:14px}
.fact-panel{background:var(--s);border:1px solid var(--b);border-radius:6px;padding:12px 16px}
.fact-title{font-size:11px;font-weight:700;color:var(--a);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.fact-row{display:flex;justify-content:space-between;padding:4px 0;font-size:13px;border-bottom:1px solid #f6f8fa}
.fact-row:last-child{border-bottom:none}
.fact-label{color:var(--m);cursor:help}.fact-value{font-weight:500}
.fact-value.bullish{color:var(--g)}.fact-value.bearish{color:var(--r)}
.fact-na{color:#bbb;font-style:italic;font-size:12px}
.review-form{background:var(--s);border:1px solid var(--b);border-radius:6px;padding:20px;margin-bottom:16px}
.review-title{font-size:14px;font-weight:700;color:var(--a);text-transform:uppercase;letter-spacing:.06em;margin-bottom:14px}
.form-row{display:flex;gap:32px;flex-wrap:wrap;margin-bottom:14px}
.form-group{min-width:220px}
.form-label{font-size:13px;color:var(--m);margin-bottom:6px;font-weight:600}
.form-options{display:flex;flex-wrap:wrap;gap:6px}
.form-opt{padding:7px 16px;border:1px solid var(--b);border-radius:4px;font-size:13px;cursor:pointer;font-family:inherit;background:var(--s)}
.form-opt:hover{background:#f0f0f0}
.form-opt.selected{background:var(--a);color:#fff;border-color:var(--a)}
.form-textarea{width:100%;height:60px;border:1px solid var(--b);border-radius:4px;padding:8px 10px;font-family:inherit;font-size:14px;resize:vertical}
.explain-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px;margin-bottom:14px}
.ex-card{background:#f6f8fa;border:1px solid var(--b);border-radius:4px;padding:8px 12px;font-size:13px}
.ex-title{font-size:10px;font-weight:700;color:var(--a);text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px}
.pdot{width:12px;height:12px;border-radius:50%;border:1px solid var(--b);background:var(--s);cursor:pointer;display:inline-block}
.pdot.current{border-color:var(--a);background:#dbeafe}
.pdot.reviewed{background:var(--g);border-color:var(--g)}
[data-tip]{position:relative}
[data-tip]:hover::after{content:attr(data-tip);position:absolute;bottom:100%;left:0;background:#1a1a1a;color:#fff;padding:8px 12px;border-radius:4px;font-size:12px;white-space:pre-line;max-width:380px;z-index:200;line-height:1.5;box-shadow:0 2px 8px rgba(0,0,0,0.2);pointer-events:none;font-style:normal}
</style></head><body>
<div class="topbar">
<h1>BDRR Training Batch</h1>
<span class="counter" id="counter"></span>
<span id="dots"></span>
<div style="flex:1"></div>
<button class="btn" id="bPrev">&#9664; Prev</button>
<button class="btn" id="bNext">Next &#9654;</button>
<button class="btn btn-accent" id="bExport">Export Reviews</button>
</div>
<div class="content" id="content"></div>
<script>
var EV=''' + events_json + ''';
var ci=0,dec={},charts=[];
var ET=-4*3600;
function fp(v){return v==null?"\\u2014":typeof v==="number"?v.toFixed(2):String(v);}
function tp(t){return t==null?null:Math.round(t*0.01*1e6)/1e6;}
function fc(v){return v===true?"Yes":v===false?"No":v==null?"\\u2014":String(v);}
function dcls(v){return v==="bullish"?"bullish":v==="bearish"?"bearish":"";}
document.getElementById("bPrev").onclick=function(){doNav(-1);};
document.getElementById("bNext").onclick=function(){doNav(1);};
document.getElementById("bExport").onclick=exportAll;
function render(idx){
ci=idx;var E=EV[idx],ann=E.annotations||{},F=E.facts||{},pd=E.prev_day,ma=F.market_alignment||E.market_alignment||{};
var dir=E.direction||"?",oc=ann.outcome||"",tf=E.timeframe||"5m";
document.getElementById("counter").textContent=(idx+1)+"/"+EV.length+"  "+E.symbol+" "+dir+" "+tf+" "+E.session_date+" "+oc;
renderDots();charts.forEach(function(c){try{c.remove();}catch(e){}});charts=[];
var h="";
h+="<div style='margin-bottom:12px'>";
h+="<span class='tag tag-"+(dir==="LONG"?"long":"short")+"'>"+dir+"</span> ";
h+="<span class='tag tag-tf'>"+tf+"</span> ";
h+="<span class='tag' style='background:#dbeafe;color:var(--a)'>"+E.sequence_id+"</span> ";
if(oc.indexOf("TARGET")>=0)h+="<span class='tag tag-win'>"+oc+"</span>";
else if(oc.indexOf("STOPPED")>=0)h+="<span class='tag tag-loss'>"+oc+"</span>";
h+="</div>";
h+="<div class='charts-row'>";
h+="<div class='chart-box'><div class='chart-box-title'>"+E.symbol+" \\u2014 "+E.session_date+" ("+tf+")</div><div class='chart-inner' id='mainChart'></div></div>";
if(pd&&pd.prev_candles)h+="<div class='chart-box'><div class='chart-box-title'>Previous Day \\u2014 "+pd.prev_date+"</div><div class='chart-inner-sm' id='prevChart'></div></div>";
else h+="<div class='chart-box'><div class='chart-box-title'>Previous Day</div><div style='padding:40px;color:var(--m);font-size:14px'>No data</div></div>";
h+="</div>";
var geo=(F.setup_geometry||{});
h+="<div class='explain-row'>";
[["ORB","H:"+fp(tp(E.orb_high_ticks))+" L:"+fp(tp(E.orb_low_ticks))],["Break","#"+(ann.break_candle_index!=null?ann.break_candle_index:"\\u2014")],
["Confirm","#"+(ann.confirmation_candle_index!=null?ann.confirmation_candle_index:"\\u2014")],
["Wick",fp(geo.wick_depth_ticks||E.wick_depth_ticks)+"t"],["Delay",fp(geo.confirmation_delay_bars)+" bars"],
["Body Dist",fp(geo.body_distance_from_orb_ticks)+"t"],["Stop Dist",fp(geo.stop_distance_ticks)+"t"],
["Failed Ret",(E.all_retest_candidates||[]).length],["Invalidation",E.invalidation_index!=null?"#"+E.invalidation_index:"None"],
["Outcome",oc||"\\u2014"],["Timeframe",tf]
].forEach(function(c){h+="<div class='ex-card'><div class='ex-title'>"+c[0]+"</div>"+c[1]+"</div>";});
h+="</div>";
h+="<div class='facts-grid'>";
var pf=F.previous_day||(pd?{prev_date:pd.prev_date,open:pd.open,high:pd.high,low:pd.low,close:pd.close,net_move:pd.net_move,range:pd.range,close_location:pd.close_location,classification:pd.classification}:null);
h+="<div class='fact-panel'><div class='fact-title'>Previous Day</div>";
if(pf){[["Date",pf.prev_date],["OHLC",fp(pf.open)+"/"+fp(pf.high)+"/"+fp(pf.low)+"/"+fp(pf.close)],["Net",fp(pf.net_move)],["Range",fp(pf.range)],["Close Loc",(pf.close_location*100).toFixed(0)+"%"],["Class","<span class='fact-value "+dcls(pf.classification)+"'>"+pf.classification+"</span>"]].forEach(function(r){h+="<div class='fact-row'><span class='fact-label'>"+r[0]+"</span><span class='fact-value'>"+r[1]+"</span></div>";});}
else h+="<span class='fact-na'>No data</span>";
h+="</div>";
var og=F.opening||{};
h+="<div class='fact-panel'><div class='fact-title'>Opening / Premarket</div>";
h+="<div class='fact-row'><span class='fact-label'>Gap</span><span class='fact-value'>"+fp(og.gap_value)+" ("+fp(og.gap_pct)+"%)</span></div>";
h+="<div class='fact-row'><span class='fact-label'>Premarket</span><span class='fact-na'>Not available</span></div>";
h+="<div class='fact-row'><span class='fact-label'>Order Block</span><span class='fact-na'>Not yet implemented</span></div>";
h+="</div>";
h+="<div class='fact-panel'><div class='fact-title'>SPY / QQQ</div>";
["SPY","QQQ"].forEach(function(s){var a=ma[s];if(!a||!a.available){h+="<div class='fact-row'><span class='fact-label'>"+s+"</span><span class='fact-na'>\\u2014</span></div>";return;}
h+="<div class='fact-row'><span class='fact-label'>"+s+"</span><span class='fact-value "+dcls(a.state)+"'>"+a.state+" \\u00b7 "+a.location.replace(/_/g," ")+(a.agrees_with_trade?" \\u2714":"")+"</span></div>";});
h+="</div>";
h+="<div class='fact-panel'><div class='fact-title'>Setup Geometry</div>";
[["Wick",fp(geo.wick_depth_ticks||E.wick_depth_ticks)+"t"],["Body Dist",fp(geo.body_distance_from_orb_ticks)+"t"],["Conf Range",fp(geo.confirmation_range_ticks)+"t"],["Conf Body",fp(geo.confirmation_body_ticks)+"t"],["Stop Dist",fp(geo.stop_distance_ticks)+"t"],["Delay",fp(geo.confirmation_delay_bars)+" bars"]].forEach(function(r){h+="<div class='fact-row'><span class='fact-label'>"+r[0]+"</span><span class='fact-value'>"+r[1]+"</span></div>";});
h+="</div></div>";
var d=dec[idx]||{};
h+="<div class='review-form'><div class='review-title'>Review</div><div class='form-row'>";
[["structure","Detector Structure",["CORRECT","WRONG","UNCERTAIN"]],["would_trade","Would Trade",["YES","REDUCED SIZE","WAIT","NO"]],["quality","Quality",["A+","A","B","C"]]].forEach(function(f){
h+="<div class='form-group'><div class='form-label'>"+f[1]+"</div><div class='form-options'>";
f[2].forEach(function(opt){h+="<span class='form-opt"+(d[f[0]]===opt?" selected":"")+"' data-field='"+f[0]+"' data-value='"+opt+"'>"+opt+"</span>";});
h+="</div></div>";});
h+="</div><div class='form-group'><div class='form-label'>Notes</div><textarea class='form-textarea' id='noteArea'>"+(d.note||"")+"</textarea></div></div>";
document.getElementById("content").innerHTML=h;
document.getElementById("content").onclick=function(e){var t=e.target;if(!t.classList.contains("form-opt"))return;var field=t.getAttribute("data-field"),value=t.getAttribute("data-value");if(!dec[ci])dec[ci]={};dec[ci][field]=value;t.parentElement.querySelectorAll(".form-opt").forEach(function(o){o.classList.remove("selected");});t.classList.add("selected");renderDots();};
var na=document.getElementById("noteArea");if(na)na.onchange=function(){if(!dec[ci])dec[ci]={};dec[ci].note=na.value;};
setTimeout(function(){renderMainChart(EV[idx]);},50);
if(pd&&pd.prev_candles)setTimeout(function(){renderPrevChart(pd);},100);
}
function renderDots(){var h="";for(var i=0;i<EV.length;i++)h+="<span class='pdot"+(i===ci?" current":"")+(dec[i]?" reviewed":"")+"' data-idx='"+i+"'></span> ";document.getElementById("dots").innerHTML=h;document.getElementById("dots").onclick=function(e){var t=e.target;if(t.hasAttribute("data-idx"))render(parseInt(t.getAttribute("data-idx")));}}
function renderMainChart(E){var el=document.getElementById("mainChart");if(!el)return;var ann=E.annotations||{};var candles=E.candles.map(function(c){return{time:Math.floor(c.time_ms/1000)+ET,open:c.open,high:c.high,low:c.low,close:c.close};});var chart=LightweightCharts.createChart(el,{width:el.clientWidth,height:480,layout:{background:{type:"solid",color:"#fff"},textColor:"#333",fontSize:12},grid:{vertLines:{color:"#f5f5f5"},horzLines:{color:"#f5f5f5"}},crosshair:{mode:0},timeScale:{timeVisible:true,secondsVisible:false,borderColor:"#d0d7de"},rightPriceScale:{borderColor:"#d0d7de",minMove:0.01,scaleMargins:{top:0.05,bottom:0.05}}});var ser=chart.addCandlestickSeries({upColor:"#26a69a",downColor:"#ef5350",borderUpColor:"#26a69a",borderDownColor:"#ef5350",wickUpColor:"#26a69a",wickDownColor:"#ef5350"});ser.setData(candles);charts.push(chart);
if(E.orb_high_ticks!=null){var p=tp(E.orb_high_ticks);ser.createPriceLine({price:p,color:"#e65100",lineWidth:3,lineStyle:0,axisLabelVisible:true,title:"ORB H "+fp(p)});}
if(E.orb_low_ticks!=null){var p=tp(E.orb_low_ticks);ser.createPriceLine({price:p,color:"#7b1fa2",lineWidth:3,lineStyle:0,axisLabelVisible:true,title:"ORB L "+fp(p)});}
if(E.pdh!=null)ser.createPriceLine({price:E.pdh,color:"#6d4c41",lineWidth:1,lineStyle:3,axisLabelVisible:true,title:"PDH "+fp(E.pdh)});
if(E.pdl!=null)ser.createPriceLine({price:E.pdl,color:"#6d4c41",lineWidth:1,lineStyle:3,axisLabelVisible:true,title:"PDL "+fp(E.pdl)});
if(ann.entry_price_ticks!=null){var p=tp(ann.entry_price_ticks);ser.createPriceLine({price:p,color:"#1565c0",lineWidth:2,lineStyle:0,axisLabelVisible:true,title:"Entry "+fp(p)});}
if(ann.stop_price_ticks!=null){var p=tp(ann.stop_price_ticks);ser.createPriceLine({price:p,color:"#c62828",lineWidth:2,lineStyle:2,axisLabelVisible:true,title:"Stop "+fp(p)});}
if(ann.r2_price_ticks!=null){var p=tp(ann.r2_price_ticks);ser.createPriceLine({price:p,color:"#2e7d32",lineWidth:2,lineStyle:2,axisLabelVisible:true,title:"TP 2R "+fp(p)});}
function ts(i){return i!=null&&i>=0&&i<candles.length?candles[i].time:null;}
var mk=[];
(E.all_retest_candidates||[]).forEach(function(fr){if(fr.bar_index!=null&&ts(fr.bar_index)!=null)mk.push({time:ts(fr.bar_index),position:"belowBar",color:"#d4a017",shape:"circle",text:"\\u2717 Ret",size:0});});
(E.consecutive_inside_closes||[]).forEach(function(cc,i){if(cc.bar_index!=null&&ts(cc.bar_index)!=null)mk.push({time:ts(cc.bar_index),position:"aboveBar",color:"#e65100",shape:"square",text:"In#"+(i+1),size:0});});
if(E.invalidation_index!=null&&ts(E.invalidation_index)!=null)mk.push({time:ts(E.invalidation_index),position:"aboveBar",color:"#c62828",shape:"square",text:"\\u2717 INV",size:1});
if(ann.break_candle_index!=null&&ts(ann.break_candle_index)!=null)mk.push({time:ts(ann.break_candle_index),position:"aboveBar",color:"#1565c0",shape:"arrowDown",text:"Break",size:1});
if(ann.confirmation_candle_index!=null&&ts(ann.confirmation_candle_index)!=null)mk.push({time:ts(ann.confirmation_candle_index),position:"belowBar",color:"#2e7d32",shape:"arrowUp",text:"Confirm",size:1});
if(ann.exit_candle_index!=null&&ts(ann.exit_candle_index)!=null)mk.push({time:ts(ann.exit_candle_index),position:"aboveBar",color:"#e65100",shape:"circle",text:"Exit",size:2});
mk.sort(function(a,b){return a.time-b.time;});if(mk.length>0)ser.setMarkers(mk);chart.timeScale().fitContent();}
function renderPrevChart(pd){var el=document.getElementById("prevChart");if(!el||!pd.prev_candles)return;var candles=pd.prev_candles.map(function(c){return{time:Math.floor(c.time_ms/1000)+ET,open:c.open,high:c.high,low:c.low,close:c.close};});var chart=LightweightCharts.createChart(el,{width:el.clientWidth,height:340,layout:{background:{type:"solid",color:"#fff"},textColor:"#333",fontSize:11},grid:{vertLines:{color:"#f5f5f5"},horzLines:{color:"#f5f5f5"}},crosshair:{mode:0},timeScale:{timeVisible:true,secondsVisible:false,borderColor:"#d0d7de"},rightPriceScale:{borderColor:"#d0d7de",minMove:0.01}});var ser=chart.addCandlestickSeries({upColor:"#26a69a",downColor:"#ef5350",borderUpColor:"#26a69a",borderDownColor:"#ef5350",wickUpColor:"#26a69a",wickDownColor:"#ef5350"});ser.setData(candles);charts.push(chart);
ser.createPriceLine({price:pd.high,color:"#6d4c41",lineWidth:1,lineStyle:0,axisLabelVisible:true,title:"H "+fp(pd.high)});ser.createPriceLine({price:pd.low,color:"#6d4c41",lineWidth:1,lineStyle:0,axisLabelVisible:true,title:"L "+fp(pd.low)});chart.timeScale().fitContent();}
function doNav(d){var n=ci+d;if(n>=0&&n<EV.length)render(n);}
function exportAll(){var out=[];for(var i=0;i<EV.length;i++){var e=EV[i],d=dec[i]||{},ann=e.annotations||{},F=e.facts||{};
out.push({schema_version:"training_review/v1",index:i,symbol:e.symbol,date:e.session_date,direction:e.direction,timeframe:e.timeframe||"5m",sequence_id:e.sequence_id,detection_status:e.detection_status,outcome:ann.outcome||"",realized_r:ann.realized_r||null,review:{structure:d.structure||null,would_trade:d.would_trade||null,quality:d.quality||null,note:d.note||null},facts:F});}
var blob=new Blob([JSON.stringify(out,null,2)],{type:"application/json"});var a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="training_reviews_batch.json";a.click();}
document.addEventListener("keydown",function(e){if(e.target.tagName==="TEXTAREA"||e.target.tagName==="INPUT")return;if(e.key==="ArrowLeft")doNav(-1);else if(e.key==="ArrowRight")doNav(1);});
render(0);
</script></body></html>'''

    path = OUTPUT / filename
    path.write_text(html, encoding="utf-8")
    print(f"  Written: {path} ({path.stat().st_size:,} bytes)")
    return path


# Generate one batch per timeframe
for tf in TIMEFRAMES:
    tf_events = [e for e in events if e.get("timeframe") == f"{tf}m"]
    if tf_events:
        write_batch_html(
            tf_events,
            f"training_batch_{tf}m.html",
            f"BDRR Training Batch — {tf}m ({len(tf_events)} setups)",
        )

# Also generate one combined batch with all setups
if events:
    write_batch_html(
        events,
        "training_batch_all.html",
        f"BDRR Training Batch — All Timeframes ({len(events)} setups)",
    )

print(f"\n{'='*60}")
print("GENERATION COMPLETE")
print(f"Total review examples: {len(events)}")
print(f"Files in: backend/output/training_batch_*.html")
print(f"{'='*60}")
