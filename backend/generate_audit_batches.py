"""Generate detector audit batches from genuine 1-minute data.

Run from trading_bot root with venv activated:

    python backend/generate_audit_batches.py

Or with options:

    python backend/generate_audit_batches.py --symbols SPY QQQ --timeframes 5 --include-valid

Pipeline:

    1m CSV → run_multi_timeframe → build_detector_audit_record →
    select_audit_candidates → export_audit_visual_event → audit HTML

Does NOT modify the detector, strategy runner, or training workspace.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_lab.audit_record_builder import build_detector_audit_record
from trading_lab.audit_candidate_selector import (
    is_audit_worthy,
    select_audit_candidates,
)
from trading_lab.audit_visual_exporter import export_audit_visual_event
from trading_lab.contracts.detector_audit_record import CandidateStatus
from trading_lab.multi_timeframe_runner import run_multi_timeframe
from trading_lab.timeframe_aggregation import aggregate_post_orb


ET = ZoneInfo("America/New_York")
DATI = Path("dati")
OUTPUT = Path("backend/output")

SYMBOLS = ["SPY", "QQQ", "AMZN", "TSLA", "NVDA", "META", "MSFT", "GOOGL", "MU"]
TIMEFRAMES = [1, 2, 3, 5, 10]
DIRECTIONS = ["LONG", "SHORT"]
CONFIG = {"tick_size": 0.01, "exit_target_r": 2, "engine_version": "1.0.0"}
TICK_SIZE = 0.01


# ── Data loading (same convention as generate_batches.py) ────────────────────

def load_1m(symbol: str, dati_path: Path = DATI,
            exclude_dates: set | None = None) -> dict[str, list[dict]]:
    """Load 1-minute candles grouped by session date."""
    if exclude_dates is None:
        exclude_dates = set()
    path = dati_path / f"{symbol}_1m.csv"
    if not path.exists():
        return {}
    candles = []
    with open(path) as f:
        for i, row in enumerate(csv.reader(f)):
            if i < 3:
                continue
            if not row[0].strip():
                continue
            dt = datetime.fromisoformat(row[0])
            candles.append({
                "time_ms": int(dt.timestamp() * 1000),
                "open": float(row[4]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[1]),
                "volume": int(float(row[5])),
            })
    sessions: dict[str, list[dict]] = {}
    for c in candles:
        dt = datetime.fromtimestamp(
            c["time_ms"] / 1000, tz=timezone.utc
        ).astimezone(ET)
        d = dt.strftime("%Y-%m-%d")
        if d in exclude_dates:
            continue
        if d not in sessions:
            sessions[d] = []
        sessions[d].append(c)
    return {d: bars for d, bars in sessions.items() if len(bars) == 390}


# ── Pipeline: runner results → audit records → visual events ─────────────────

def build_audit_events(
    sessions_1m: dict[str, list[dict]],
    symbol: str,
    timeframes: list[int],
    directions: list[str],
    include_valid: bool = False,
) -> tuple[list[dict], dict]:
    """Run pipeline and return (visual_events, summary_stats)."""
    all_records = []
    build_errors = []

    for tf in timeframes:
        for direction in directions:
            results = run_multi_timeframe(
                sessions_1m, symbol, tf, direction,
                preset_overrides={"consecutive_orb_closes": 2},
                config=CONFIG,
            )
            for r in results:
                dr = r.get("detection_result")
                if dr is None:
                    continue  # pipeline failure without DR — skip
                try:
                    record = build_detector_audit_record(r)
                    all_records.append((record, r, tf))
                except (ValueError, TypeError) as e:
                    build_errors.append({
                        "symbol": symbol,
                        "date": r.get("session_date"),
                        "tf": f"{tf}m",
                        "direction": direction,
                        "error": str(e),
                    })

    # Selection
    all_audit_records = [rec for rec, _, _ in all_records]
    selected = select_audit_candidates(all_audit_records)
    selected_ids = {id(r) for r in selected}

    # Filter by include_valid
    if not include_valid:
        selected = tuple(
            r for r in selected
            if r.candidate_status != CandidateStatus.VALID
        )

    # Build visual events
    events = []
    for record, runner_result, tf in all_records:
        if id(record) not in selected_ids:
            continue
        if not include_valid and record.candidate_status == CandidateStatus.VALID:
            continue

        # Get the aggregated candles for this session/timeframe
        date = record.session_date
        if date not in sessions_1m:
            continue
        try:
            orb_summary, post_orb = aggregate_post_orb(
                sessions_1m[date], tf
            )
            full_candles = [orb_summary] + post_orb
        except (ValueError, KeyError):
            continue

        try:
            event = export_audit_visual_event(record, full_candles)
            # Add tick_size for price display
            ts = None
            if record.detection_result.level_price is not None:
                ts = record.detection_result.level_price.tick_size
            event["tick_size"] = ts or str(TICK_SIZE)
            events.append(event)
        except (ValueError, TypeError):
            continue

    # Summary stats
    total_valid = sum(1 for r, _, _ in all_records
                      if r.candidate_status == CandidateStatus.VALID)
    total_rejected = sum(1 for r, _, _ in all_records
                         if r.candidate_status == CandidateStatus.REJECTED)
    total_audit_worthy = sum(1 for r in all_audit_records
                             if is_audit_worthy(r))

    by_stage: dict[str, int] = {}
    by_tf: dict[str, int] = {}
    by_dir: dict[str, int] = {}
    for ev in events:
        fs = ev.get("failed_stage") or "VALID"
        by_stage[fs] = by_stage.get(fs, 0) + 1
        tf_str = ev.get("timeframe", "?")
        by_tf[tf_str] = by_tf.get(tf_str, 0) + 1
        d = ev.get("direction", "?")
        by_dir[d] = by_dir.get(d, 0) + 1

    summary = {
        "symbol": symbol,
        "total_pipeline": len(all_records),
        "total_valid": total_valid,
        "total_rejected": total_rejected,
        "total_audit_worthy": total_audit_worthy,
        "total_excluded": len(all_records) - total_audit_worthy,
        "included_in_batch": len(events),
        "by_stage": by_stage,
        "by_timeframe": by_tf,
        "by_direction": by_dir,
        "build_errors": build_errors,
    }

    return events, summary


# ── HTML generation ──────────────────────────────────────────────────────────

def generate_audit_html(
    events: list[dict],
    summaries: list[dict],
    title: str = "BDRR Detector Audit Batch",
) -> str:
    """Generate a standalone audit review HTML page."""
    events_json = json.dumps(
        events, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )

    summary_lines = []
    grand_total = 0
    grand_valid = 0
    grand_rejected = 0
    grand_audit = 0
    grand_excluded = 0
    for s in summaries:
        grand_total += s["total_pipeline"]
        grand_valid += s["total_valid"]
        grand_rejected += s["total_rejected"]
        grand_audit += s["total_audit_worthy"]
        grand_excluded += s["total_excluded"]
        summary_lines.append(
            f"{s['symbol']}: {s['total_pipeline']} pipeline, "
            f"{s['total_valid']} valid, {s['total_rejected']} rejected, "
            f"{s['total_audit_worthy']} audit-worthy, "
            f"{s['included_in_batch']} included"
        )
        for err in s.get("build_errors", []):
            summary_lines.append(
                f"  WARN: {err['symbol']} {err['date']} {err['tf']} "
                f"{err.get('direction','')} — {err['error']}"
            )

    summary_text = json.dumps(
        "\\n".join(summary_lines), ensure_ascii=True
    )
    gen_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#f8f9fa;--s:#fff;--b:#d0d7de;--t:#1a1a1a;--m:#656d76;--a:#1565c0;--g:#116329;--r:#a40e26;--o:#e65100;--y:#7b6c00}}
body{{font-family:"SF Mono","Cascadia Code",Consolas,monospace;background:var(--bg);color:var(--t);font-size:14px}}
.topbar{{display:flex;align-items:center;padding:12px 20px;background:var(--s);border-bottom:1px solid var(--b);gap:14px;flex-wrap:wrap;position:sticky;top:0;z-index:100}}
.topbar h1{{font-size:18px;font-weight:700;color:var(--a)}}
.counter{{font-size:14px;color:var(--m)}}
.btn{{padding:8px 18px;border:1px solid var(--b);border-radius:4px;background:var(--s);font-family:inherit;font-size:14px;cursor:pointer;font-weight:600}}
.btn:hover{{background:#f0f0f0}}
.btn-accent{{background:var(--a);color:#fff;border-color:var(--a)}}
.tag{{display:inline-block;padding:3px 10px;border-radius:3px;font-size:13px;font-weight:600}}
.tag-valid{{background:#dafbe1;color:var(--g)}}.tag-rejected{{background:#ffebe9;color:var(--r)}}
.tag-long{{background:#dbeafe;color:var(--a)}}.tag-short{{background:#fce7f3;color:#9d174d}}
.tag-tf{{background:#f3f0ff;color:#5b21b6}}
.tag-stage{{background:#fef3c7;color:var(--y)}}
.content{{padding:16px 24px}}
.chart-box{{background:var(--s);border:1px solid var(--b);border-radius:6px;overflow:hidden;margin-bottom:14px}}
.chart-box-title{{font-size:12px;font-weight:700;color:var(--a);text-transform:uppercase;letter-spacing:.06em;padding:10px 14px;border-bottom:1px solid var(--b)}}
.chart-inner{{height:480px}}
.diag-row{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px;margin-bottom:14px}}
.diag-card{{background:#f6f8fa;border:1px solid var(--b);border-radius:4px;padding:8px 12px;font-size:13px}}
.diag-title{{font-size:10px;font-weight:700;color:var(--a);text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px}}
.review-form{{background:var(--s);border:1px solid var(--b);border-radius:6px;padding:20px;margin-bottom:16px}}
.review-title{{font-size:14px;font-weight:700;color:var(--a);text-transform:uppercase;letter-spacing:.06em;margin-bottom:14px}}
.form-row{{display:flex;gap:32px;flex-wrap:wrap;margin-bottom:14px}}
.form-group{{min-width:200px}}
.form-label{{font-size:13px;color:var(--m);margin-bottom:6px;font-weight:600}}
.form-options{{display:flex;flex-wrap:wrap;gap:6px}}
.form-opt{{padding:7px 16px;border:1px solid var(--b);border-radius:4px;font-size:13px;cursor:pointer;font-family:inherit;background:var(--s)}}
.form-opt:hover{{background:#f0f0f0}}
.form-opt.selected{{background:var(--a);color:#fff;border-color:var(--a)}}
.form-textarea{{width:100%;height:60px;border:1px solid var(--b);border-radius:4px;padding:8px 10px;font-family:inherit;font-size:14px;resize:vertical}}
.pdot{{width:12px;height:12px;border-radius:50%;border:1px solid var(--b);background:var(--s);cursor:pointer;display:inline-block}}
.pdot.current{{border-color:var(--a);background:#dbeafe}}.pdot.reviewed{{background:var(--g);border-color:var(--g)}}
.summary-box{{background:var(--s);border:1px solid var(--b);border-radius:6px;padding:16px;margin-bottom:16px;font-size:13px;white-space:pre-wrap;color:var(--m)}}
.fr-list{{margin-top:8px}}.fr-item{{background:#fff3cd;border:1px solid #ffc107;border-radius:4px;padding:6px 10px;margin-bottom:4px;font-size:12px}}
</style></head><body>
<div class="topbar">
<h1>BDRR Detector Audit</h1>
<span class="counter" id="counter"></span>
<span id="dots"></span>
<div style="flex:1"></div>
<button class="btn" id="bSummary">Summary</button>
<button class="btn" id="bPrev">&#9664; Prev</button>
<button class="btn" id="bNext">Next &#9654;</button>
<button class="btn btn-accent" id="bExport">Export Reviews</button>
</div>
<div class="content" id="content"></div>
<script>
var EV={events_json};
var ci=0,dec={{}},charts=[];
var ET=-4*3600;
var SUMMARY={summary_text};
var GEN_TS="{gen_ts}";
function tp(t,ts){{ts=parseFloat(ts||"0.01");return t==null?null:Math.round(t*ts*1e6)/1e6;}}
function fp(v){{return v==null?"\\u2014":typeof v==="number"?v.toFixed(2):String(v);}}
document.getElementById("bPrev").onclick=function(){{doNav(-1);}};
document.getElementById("bNext").onclick=function(){{doNav(1);}};
document.getElementById("bExport").onclick=exportAll;
document.getElementById("bSummary").onclick=function(){{
document.getElementById("content").innerHTML="<div class='summary-box'>"+SUMMARY.replace(/\\\\n/g,"\\n")+"</div>";
}};
function render(idx){{
ci=idx;var E=EV[idx],ann=E.annotations||{{}};
var dir=E.direction||"?",cs=E.candidate_status||"?",tf=E.timeframe||"5m";
var fs=E.failed_stage;var ts=E.tick_size||"0.01";
document.getElementById("counter").textContent=(idx+1)+"/"+EV.length+"  "+E.symbol+" "+dir+" "+tf+" "+E.session_date+" ["+cs+"]"+(fs?" — "+fs:"");
renderDots();charts.forEach(function(c){{try{{c.remove();}}catch(e){{}}}});charts=[];
var h="";
h+="<div style='margin-bottom:12px'>";
h+="<span class='tag tag-"+(cs==="VALID"?"valid":"rejected")+"'>"+cs+"</span> ";
h+="<span class='tag tag-"+(dir==="LONG"?"long":"short")+"'>"+dir+"</span> ";
h+="<span class='tag tag-tf'>"+tf+"</span> ";
if(fs)h+="<span class='tag tag-stage'>"+fs+"</span> ";
h+="</div>";
h+="<div class='chart-box'><div class='chart-box-title'>"+E.symbol+" \\u2014 "+E.session_date+" ("+tf+")</div><div class='chart-inner' id='mainChart'></div></div>";
h+="<div class='diag-row'>";
[["Status",cs],["Failed Stage",fs||"\\u2014"],["ORB H",fp(tp(E.orb_high_ticks,ts))],["ORB L",fp(tp(E.orb_low_ticks,ts))],
["Break","#"+(ann.break_candle_index!=null?ann.break_candle_index:"\\u2014")],
["Confirm","#"+(ann.confirmation_candle_index!=null?ann.confirmation_candle_index:"\\u2014")],
["Reached ORB",E.reached_orb?"\\u2714":"\\u2718"],["Reached Break",E.reached_break?"\\u2714":"\\u2718"],
["Reached Disp",E.reached_displacement?"\\u2714":"\\u2718"],["Reached Retest",E.reached_retest?"\\u2714":"\\u2718"],
["Reached Rej Scan",E.reached_rejection_scan?"\\u2714":"\\u2718"]
].forEach(function(c){{h+="<div class='diag-card'><div class='diag-title'>"+c[0]+"</div>"+c[1]+"</div>";}});
h+="</div>";
var fr=E.failed_retests||[];
if(fr.length>0){{
h+="<div class='fr-list'><div class='diag-title'>Failed Retests ("+fr.length+")</div>";
fr.forEach(function(f,i){{h+="<div class='fr-item'>Bar #"+(f.candle_index!=null?f.candle_index:"?")+": "+f.failed_rules.map(function(r){{return r.rule_id;}}).join(", ")+"</div>";}});
h+="</div>";
}}
var frl=E.failed_rules||[];
if(frl.length>0){{
h+="<div class='fr-list'><div class='diag-title'>Failed Rules</div>";
frl.forEach(function(r){{h+="<div class='fr-item'>"+r.rule_id+": "+r.message+"</div>";}});
h+="</div>";
}}
var d=dec[idx]||{{}};
h+="<div class='review-form'><div class='review-title'>Audit Review</div><div class='form-row'>";
[["detector_correct","Detector Correct?",["YES","NO","UNSURE"]],["would_trade","Would Trade?",["YES","NO","UNSURE"]],["manual_quality","Quality",["A+","A","B","C","REJECT"]]].forEach(function(f){{
h+="<div class='form-group'><div class='form-label'>"+f[1]+"</div><div class='form-options'>";
f[2].forEach(function(opt){{h+="<span class='form-opt"+(d[f[0]]===opt?" selected":"")+"' data-field='"+f[0]+"' data-value='"+opt+"'>"+opt+"</span>";}});
h+="</div></div>";}});
h+="</div><div class='form-group'><div class='form-label'>Notes</div><textarea class='form-textarea' id='noteArea'>"+(d.note||"")+"</textarea></div></div>";
document.getElementById("content").innerHTML=h;
document.getElementById("content").onclick=function(e){{var t=e.target;if(!t.classList.contains("form-opt"))return;var field=t.getAttribute("data-field"),value=t.getAttribute("data-value");if(!dec[ci])dec[ci]={{}};dec[ci][field]=value;t.parentElement.querySelectorAll(".form-opt").forEach(function(o){{o.classList.remove("selected");}});t.classList.add("selected");renderDots();}};
var na=document.getElementById("noteArea");if(na)na.onchange=function(){{if(!dec[ci])dec[ci]={{}};dec[ci].note=na.value;}};
setTimeout(function(){{renderChart(EV[idx]);}},50);
}}
function renderDots(){{var h="";for(var i=0;i<EV.length;i++){{var e=EV[i];var cls="pdot"+(i===ci?" current":"")+(dec[i]?" reviewed":"");h+="<span class='"+cls+"' data-idx='"+i+"' title='"+(e.symbol||"")+" "+(e.candidate_status||"")+"'></span> ";}}document.getElementById("dots").innerHTML=h;document.getElementById("dots").onclick=function(e){{var t=e.target;if(t.hasAttribute("data-idx"))render(parseInt(t.getAttribute("data-idx")));}};}}
function renderChart(E){{var el=document.getElementById("mainChart");if(!el)return;var ann=E.annotations||{{}};var ts=E.tick_size||"0.01";
var candles=E.candles.map(function(c){{return{{time:Math.floor(c.time_ms/1000)+ET,open:c.open,high:c.high,low:c.low,close:c.close}};}});
var chart=LightweightCharts.createChart(el,{{width:el.clientWidth,height:480,layout:{{background:{{type:"solid",color:"#fff"}},textColor:"#333",fontSize:12}},grid:{{vertLines:{{color:"#f5f5f5"}},horzLines:{{color:"#f5f5f5"}}}},crosshair:{{mode:0}},timeScale:{{timeVisible:true,secondsVisible:false,borderColor:"#d0d7de"}},rightPriceScale:{{borderColor:"#d0d7de",minMove:parseFloat(ts),scaleMargins:{{top:0.05,bottom:0.05}}}}}});
var ser=chart.addCandlestickSeries({{upColor:"#26a69a",downColor:"#ef5350",borderUpColor:"#26a69a",borderDownColor:"#ef5350",wickUpColor:"#26a69a",wickDownColor:"#ef5350"}});
ser.setData(candles);charts.push(chart);
if(E.orb_high_ticks!=null){{var p=tp(E.orb_high_ticks,ts);ser.createPriceLine({{price:p,color:"#e65100",lineWidth:3,lineStyle:0,axisLabelVisible:true,title:"ORB H "+fp(p)}});}}
if(E.orb_low_ticks!=null){{var p=tp(E.orb_low_ticks,ts);ser.createPriceLine({{price:p,color:"#7b1fa2",lineWidth:3,lineStyle:0,axisLabelVisible:true,title:"ORB L "+fp(p)}});}}
function tsi(i){{return i!=null&&i>=0&&i<candles.length?candles[i].time:null;}}
var mk=[];
(E.failed_retests||[]).forEach(function(fr){{if(fr.candle_index!=null&&tsi(fr.candle_index)!=null)mk.push({{time:tsi(fr.candle_index),position:"belowBar",color:"#d4a017",shape:"circle",text:"\\u2717 Ret",size:0}});}});
if(ann.break_candle_index!=null&&tsi(ann.break_candle_index)!=null)mk.push({{time:tsi(ann.break_candle_index),position:"aboveBar",color:"#1565c0",shape:"arrowDown",text:"Break",size:1}});
if(ann.confirmation_candle_index!=null&&tsi(ann.confirmation_candle_index)!=null)mk.push({{time:tsi(ann.confirmation_candle_index),position:"belowBar",color:"#2e7d32",shape:"arrowUp",text:"Confirm",size:1}});
mk.sort(function(a,b){{return a.time-b.time;}});if(mk.length>0)ser.setMarkers(mk);chart.timeScale().fitContent();
}}
function doNav(d){{var n=ci+d;if(n>=0&&n<EV.length)render(n);}}
function exportAll(){{var out=[];for(var i=0;i<EV.length;i++){{var e=EV[i],d=dec[i]||{{}};
out.push({{schema_version:"DetectorAuditReviewBatch/v1",audit_id:e.audit_id,symbol:e.symbol,session_date:e.session_date,timeframe:e.timeframe,direction:e.direction,candidate_status:e.candidate_status,failed_stage:e.failed_stage,failed_rules:(e.failed_rules||[]).map(function(r){{return r.rule_id;}}),review:{{detector_correct:d.detector_correct||null,would_trade:d.would_trade||null,manual_quality:d.manual_quality||null,note:d.note||null}},generated_at:GEN_TS}});}};
var blob=new Blob([JSON.stringify(out,null,2)],{{type:"application/json"}});var a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="audit_reviews_batch.json";a.click();}}
document.addEventListener("keydown",function(e){{if(e.target.tagName==="TEXTAREA"||e.target.tagName==="INPUT")return;if(e.key==="ArrowLeft")doNav(-1);else if(e.key==="ArrowRight")doNav(1);}});
if(EV.length>0)render(0);else document.getElementById("content").innerHTML="<div class='summary-box'>No audit candidates found.</div>";
</script></body></html>'''


# ── CLI entry point ──────────────────────────────────────────────────────────

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate BDRR detector audit batch HTML",
    )
    p.add_argument("--symbols", nargs="+", default=SYMBOLS,
                    help="Symbols to process")
    p.add_argument("--timeframes", nargs="+", type=int, default=TIMEFRAMES,
                    help="Timeframe minutes (e.g. 1 2 3 5 10)")
    p.add_argument("--directions", nargs="+", default=DIRECTIONS,
                    help="Directions (LONG SHORT)")
    p.add_argument("--start-date", default=None,
                    help="Start date YYYY-MM-DD (inclusive)")
    p.add_argument("--end-date", default=None,
                    help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--max-records", type=int, default=None,
                    help="Max records in output batch")
    p.add_argument("--include-valid", action="store_true",
                    help="Include VALID records as controls")
    p.add_argument("--output", default=None,
                    help="Output HTML path (default: auto-generated)")
    p.add_argument("--dati", default="dati",
                    help="Input data directory")
    p.add_argument("--exclude-dates", nargs="*", default=["2026-07-30"],
                    help="Dates to exclude")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    dati_path = Path(args.dati)
    output_dir = OUTPUT
    output_dir.mkdir(parents=True, exist_ok=True)

    exclude = set(args.exclude_dates) if args.exclude_dates else set()

    print("=" * 60)
    print("DETECTOR AUDIT BATCH GENERATION")
    print("=" * 60)
    print(f"Symbols:    {args.symbols}")
    print(f"Timeframes: {args.timeframes}")
    print(f"Directions: {args.directions}")
    print(f"Include VALID: {args.include_valid}")
    if args.max_records:
        print(f"Max records: {args.max_records}")

    all_events = []
    all_summaries = []

    for sym in args.symbols:
        sessions_1m = load_1m(sym, dati_path, exclude)
        if not sessions_1m:
            print(f"\n  {sym}: no 1m data")
            continue

        # Filter by date range
        if args.start_date:
            sessions_1m = {
                d: bars for d, bars in sessions_1m.items()
                if d >= args.start_date
            }
        if args.end_date:
            sessions_1m = {
                d: bars for d, bars in sessions_1m.items()
                if d <= args.end_date
            }

        dates = sorted(sessions_1m.keys())
        if not dates:
            print(f"\n  {sym}: no sessions in date range")
            continue

        print(f"\n{sym}: {len(dates)} sessions ({dates[0]} → {dates[-1]})")

        events, summary = build_audit_events(
            sessions_1m, sym, args.timeframes, args.directions,
            include_valid=args.include_valid,
        )
        all_events.extend(events)
        all_summaries.append(summary)

        included = summary["included_in_batch"]
        worthy = summary["total_audit_worthy"]
        valid = summary["total_valid"]
        rejected = summary["total_rejected"]
        print(f"  Pipeline: {summary['total_pipeline']} | "
              f"Valid: {valid} | Rejected: {rejected} | "
              f"Audit-worthy: {worthy} | Included: {included}")
        if summary["build_errors"]:
            for err in summary["build_errors"]:
                print(f"  WARN: {err['error'][:80]}")

    if not all_events:
        print("\nERROR: No audit-worthy candidates found.")
        print("Try --include-valid to include VALID records.")
        sys.exit(1)

    # Apply max-records
    omitted = 0
    if args.max_records and len(all_events) > args.max_records:
        omitted = len(all_events) - args.max_records
        all_events = all_events[:args.max_records]
        print(f"\nMax records applied: showing {args.max_records}, "
              f"omitted {omitted}")

    # Generate HTML
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = output_dir / f"audit_batch_{ts}.html"

    title = f"BDRR Detector Audit — {len(all_events)} candidates"
    html = generate_audit_html(all_events, all_summaries, title)
    out_path.write_text(html, encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"AUDIT BATCH GENERATED")
    print(f"{'=' * 60}")
    print(f"Candidates: {len(all_events)}")
    if omitted:
        print(f"Omitted:    {omitted}")
    print(f"File:       {out_path}")
    print(f"Size:       {out_path.stat().st_size:,} bytes")
    print(f"{'=' * 60}")

    return str(out_path)


if __name__ == "__main__":
    main()
