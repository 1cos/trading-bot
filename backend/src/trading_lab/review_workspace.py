"""Trading Day Review Workspace — standalone HTML generator.

Generates a single HTML file containing all detection events from one or
more sessions. The workspace is designed for a trader to review events
quickly and decide: Accept, Reject, or Skip.

The workspace includes:
  - Large candlestick chart with ORB zone (shaded band)
  - Entry / Stop / Target horizontal lines
  - Only 3 markers: Break, Confirm, Exit
  - Failed retests shown as small markers
  - Explain panel showing why each detection stage was triggered
  - Previous / Next event navigation
  - Accept / Reject / Skip buttons with progress counter
  - Summary panel with detection stages, trade plan, outcome

Public API:

    build_workspace_events(sessions, preset, config)  → list[dict]
    render_workspace_html(events, title)               → str
    write_workspace_html(events, path, title)           → Path

Determinism:
    Given identical event list, output HTML is identical.
"""

from __future__ import annotations

import csv
import json
import html as html_mod
from datetime import datetime
from pathlib import Path

from trading_lab.strategy_runner import run_bdrr_strategy
from trading_lab.visual_review_exporter import export_visual_event


# ── Event builder from pipeline results ─────────────────────────────────────


def _get(obj, attr, default=None):
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _get_ticks(obj, field):
    v = _get(obj, field)
    if v is None:
        return None
    if hasattr(v, "ticks"):
        return v.ticks
    if isinstance(v, dict):
        return v.get("ticks")
    return None


def _build_explain(runner_result: dict) -> dict:
    """Extract explain data from pipeline intermediate results."""
    dr = runner_result.get("detection_result")
    explain = {"stages": []}

    if dr is None:
        explain["stages"].append({
            "name": "Pipeline",
            "status": "FAILED",
            "reason": runner_result.get("failure_stage", "unknown"),
        })
        return explain

    # Direction and level source
    direction = str(_get(dr, "direction", "?"))
    level_source = str(_get(dr, "level_source", "?"))
    explain["direction"] = direction
    explain["level_source"] = level_source

    # ORB
    level_bar = _get(dr, "level_bar")
    if level_bar is not None:
        orb_high = _get(_get(level_bar, "high"), "price")
        orb_low = _get(_get(level_bar, "low"), "price")
        level_price = _get(_get(dr, "level_price"), "price")
        explain["stages"].append({
            "name": "ORB",
            "status": "OK",
            "detail": (
                f"ORB High={_fmt(orb_high)} Low={_fmt(orb_low)}. "
                f"Selected level: {level_source} = {_fmt(level_price)}"
            ),
        })

    # Break
    break_bar = _get(dr, "break_bar")
    if break_bar is not None:
        break_close = _get(break_bar, "close")
        if break_close is not None:
            bc_price = _get(break_close, "price")
        else:
            bc_price = None
        explain["stages"].append({
            "name": "Break",
            "status": "OK",
            "detail": (
                f"Close={_fmt(bc_price)} broke above level. "
                f"Bar index: {_get(break_bar, 'bar_index')}"
            ),
        })

    # Displacement
    disp_window = _get(dr, "displacement_window")
    disp_dist = _get(dr, "displacement_distance")
    if disp_window is not None:
        n_bars = len(disp_window)
        dist_pts = _get(disp_dist, "points") if disp_dist else None
        explain["stages"].append({
            "name": "Displacement",
            "status": "OK",
            "detail": (
                f"{n_bars} bar(s) stayed beyond level. "
                f"Max distance: {_fmt(dist_pts)} points."
            ),
        })

    # Retest window
    retest_window = _get(dr, "retest_window")
    retest_contacts = _get(dr, "retest_contacts")
    if retest_window is not None:
        n_window = len(retest_window)
        n_contacts = len(retest_contacts) if retest_contacts else 0
        explain["stages"].append({
            "name": "Retest Window",
            "status": "OK",
            "detail": (
                f"Window: {n_window} bar(s). "
                f"Level contacts: {n_contacts}."
            ),
        })

    # Rejection / Confirmation
    conf_bar = _get(dr, "confirmation_bar")
    conf_rej_wick = _get(dr, "confirmation_rej_wick")
    conf_body = _get(dr, "confirmation_body")
    conf_fcl = _get(dr, "confirmation_favorable_close_location")
    if conf_bar is not None:
        wick_r = _rational_float(conf_rej_wick)
        body_r = _rational_float(conf_body)
        fcl = _rational_float(conf_fcl)
        explain["stages"].append({
            "name": "Confirmation",
            "status": "OK",
            "detail": (
                f"Rejection wick: {_pct(wick_r)} (min 47%). "
                f"Body: {_pct(body_r)} (max 40%). "
                f"Close location: {_pct(fcl)} (min 80%)."
            ),
        })

    # Failed retests
    failed_retests = _get(dr, "failed_retests")
    if failed_retests:
        fr_data = []
        for fr in failed_retests:
            fr_geo = _get(fr, "geometry")
            rules = _get(fr, "failed_rules", [])
            if fr_geo:
                fr_data.append({
                    "bar_index": _get(fr, "candle_index"),
                    "rejection_wick_ratio": _get(fr_geo, "rejection_wick_ratio"),
                    "body_ratio": _get(fr_geo, "body_ratio"),
                    "favorable_close_location": _get(fr_geo, "favorable_close_location"),
                    "failed_rules": [str(r) for r in rules] if rules else [],
                })
        explain["failed_retests"] = fr_data

    # Status
    status = str(_get(dr, "status", "?"))
    failed_stage = _get(dr, "failed_stage")
    if status != "VALID" and failed_stage:
        explain["stages"].append({
            "name": str(failed_stage),
            "status": "FAILED",
            "reason": "Detection failed at this stage",
        })

    # Trade plan
    tp = runner_result.get("trade_plan")
    if tp is not None:
        entry_p = _get(_get(tp, "entry_price"), "price")
        stop_p = _get(_get(tp, "stop_price"), "price")
        r2_p = _get(_get(tp, "r2_price"), "price")
        risk = None
        if entry_p is not None and stop_p is not None:
            risk = abs(entry_p - stop_p)
        explain["trade_plan"] = {
            "entry": _fmt(entry_p),
            "stop": _fmt(stop_p),
            "target_2r": _fmt(r2_p),
            "risk": _fmt(risk),
        }

    # Outcome
    to = runner_result.get("trade_outcome")
    if to is not None:
        outcome_str = str(_get(to, "outcome", ""))
        realized_r = _get(to, "realized_r")
        explain["outcome"] = {
            "result": outcome_str,
            "realized_r": _fmt(realized_r),
        }

    return explain


def _rational_float(r):
    """Convert a Rational contract to float, or None."""
    if r is None:
        return None
    num = _get(r, "numerator")
    den = _get(r, "denominator")
    if num is not None and den is not None and den != 0:
        return num / den
    return None


def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _pct(v):
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def build_workspace_events(
    sessions: list[dict],
    preset: dict,
    config: dict,
) -> list[dict]:
    """Run the BDRR strategy and build workspace event payloads.

    Returns a list of event dicts, each containing:
      - All fields from ``export_visual_event``
      - ``explain``: dict with stage-by-stage reasoning
    """
    results = run_bdrr_strategy(sessions, preset, config)

    events = []
    for i, result in enumerate(results):
        session = sessions[i]
        candles = _get(session, "candles")
        if not isinstance(candles, list):
            candles = []

        event = export_visual_event(candles, result)
        event["explain"] = _build_explain(result)

        # Add PDH/PDL from session metadata
        event["pdh"] = _get(session, "pdh")
        event["pdl"] = _get(session, "pdl")

        events.append(event)

    return events


# ── JSON helpers ─────────────────────────────────────────────────────────────


def _to_json(obj):
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def _esc(v):
    if v is None:
        return ""
    return html_mod.escape(str(v))


# ── HTML template ────────────────────────────────────────────────────────────


_WORKSPACE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"SF Mono","Cascadia Code","Consolas",monospace;
  background:#f8f9fa;color:#1a1a1a;padding:0}}

/* ── Top bar ── */
.topbar{{display:flex;align-items:center;justify-content:space-between;
  padding:8px 16px;background:#fff;border-bottom:1px solid #d0d7de;
  position:sticky;top:0;z-index:100}}
.topbar-left{{display:flex;align-items:center;gap:12px}}
.topbar-title{{font-size:14px;font-weight:600}}
.topbar-counter{{font-size:12px;color:#656d76}}
.topbar-right{{display:flex;gap:8px}}

/* ── Nav buttons ── */
.btn{{border:none;border-radius:4px;padding:6px 14px;font-size:12px;
  font-weight:600;cursor:pointer;letter-spacing:0.02em;transition:all 0.15s}}
.btn-nav{{background:#e8e8e8;color:#1a1a1a}}
.btn-nav:hover{{background:#d0d0d0}}
.btn-nav:disabled{{opacity:0.3;cursor:default}}

/* ── Decision buttons ── */
.btn-accept{{background:#dafbe1;color:#116329;border:1px solid #a7f3d0}}
.btn-accept:hover{{background:#bbf7d0}}
.btn-reject{{background:#ffebe9;color:#a40e26;border:1px solid #fecdd3}}
.btn-reject:hover{{background:#fecdd3}}
.btn-skip{{background:#fff8c5;color:#6a5d00;border:1px solid #fde68a}}
.btn-skip:hover{{background:#fef3c7}}

/* ── Header ── */
.event-header{{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;
  padding:12px 16px 4px;background:#fff}}
.event-header h1{{font-size:18px;font-weight:700}}
.tag{{display:inline-block;padding:2px 8px;border-radius:3px;
  font-size:11px;font-weight:600;letter-spacing:0.04em}}
.tag-long{{background:#dafbe1;color:#116329}}
.tag-short{{background:#ffebe9;color:#a40e26}}
.tag-valid{{background:#dbeafe;color:#1d4ed8}}
.tag-invalid{{background:#fff8c5;color:#6a5d00}}
.tag-win{{background:#dafbe1;color:#116329}}
.tag-loss{{background:#ffebe9;color:#a40e26}}
.tag-open{{background:#f0f0f0;color:#656d76}}
.pnl{{font-size:16px;font-weight:700;margin-left:auto}}
.pnl-win{{color:#116329}}
.pnl-loss{{color:#a40e26}}

/* ── Main layout ── */
.main{{display:flex;gap:0;padding:0}}
.chart-col{{flex:1;min-width:0}}
.explain-col{{width:320px;min-width:280px;background:#fff;
  border-left:1px solid #d0d7de;overflow-y:auto;max-height:calc(100vh - 100px)}}

/* ── Chart ── */
.chart-wrap{{position:relative;margin:8px 12px;
  border:1px solid #d0d7de;border-radius:6px;overflow:hidden}}
#chart{{width:100%;height:520px}}
.orb-zone-overlay{{position:absolute;left:0;right:52px;
  pointer-events:none;z-index:5}}

/* ── Explain panel ── */
.explain-panel{{padding:12px}}
.explain-panel h2{{font-size:13px;font-weight:700;color:#1a1a1a;
  margin-bottom:10px;letter-spacing:0.04em;text-transform:uppercase}}
.explain-stage{{margin-bottom:10px;padding:8px 10px;
  background:#f6f8fa;border-radius:4px;border-left:3px solid #d0d7de}}
.explain-stage.ok{{border-left-color:#22c55e}}
.explain-stage.failed{{border-left-color:#ef4444}}
.explain-stage-name{{font-size:11px;font-weight:700;color:#656d76;
  text-transform:uppercase;letter-spacing:0.06em;margin-bottom:2px}}
.explain-stage-detail{{font-size:12px;color:#1a1a1a;line-height:1.5}}
.explain-section{{margin-top:16px}}
.explain-section h3{{font-size:11px;font-weight:700;color:#656d76;
  text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px}}
.explain-row{{display:flex;justify-content:space-between;
  font-size:12px;padding:2px 0;border-bottom:1px solid #f0f0f0}}
.explain-row:last-child{{border-bottom:none}}
.explain-label{{color:#656d76}}
.explain-value{{color:#1a1a1a;font-weight:500}}
.explain-value.win{{color:#116329;font-weight:700}}
.explain-value.loss{{color:#a40e26;font-weight:700}}

/* ── Failed retest detail ── */
.failed-retest{{font-size:11px;background:#fff;border:1px solid #e8e8e8;
  border-radius:3px;padding:6px 8px;margin-top:4px}}
.failed-retest-title{{font-weight:600;color:#a40e26;margin-bottom:2px}}
.failed-retest-geo{{color:#656d76}}
.failed-retest-rules{{color:#a40e26;font-weight:500;margin-top:2px}}

/* ── Summary below chart ── */
.summary{{padding:8px 12px;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px}}
.summary-section{{background:#fff;border:1px solid #d0d7de;
  border-radius:4px;padding:8px 12px}}
.summary-section h3{{font-size:11px;color:#1d4ed8;font-weight:600;
  margin-bottom:4px;letter-spacing:0.06em;text-transform:uppercase}}
.srow{{display:flex;justify-content:space-between;font-size:12px;
  padding:1px 0;border-bottom:1px solid #f6f8fa}}
.srow:last-child{{border-bottom:none}}
.srow-label{{color:#656d76}}
.srow-value{{color:#1a1a1a;font-weight:500}}

/* ── Progress bar ── */
.progress{{display:flex;gap:3px;align-items:center}}
.progress-dot{{width:8px;height:8px;border-radius:50%;border:1px solid #d0d7de;
  background:#fff;transition:background 0.2s}}
.progress-dot.current{{border-color:#1d4ed8;background:#dbeafe}}
.progress-dot.accepted{{background:#22c55e;border-color:#22c55e}}
.progress-dot.rejected{{background:#ef4444;border-color:#ef4444}}
.progress-dot.skipped{{background:#eab308;border-color:#eab308}}

/* ── Keyboard hints ── */
.kbd-hints{{font-size:10px;color:#8b949e;padding:4px 16px;
  border-top:1px solid #d0d7de;background:#fff;text-align:center}}
.kbd{{display:inline-block;padding:1px 4px;border:1px solid #d0d7de;
  border-radius:2px;font-family:inherit;background:#f6f8fa;font-size:10px}}
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-left">
    <span class="topbar-title" id="topTitle">BDRR Review</span>
    <span class="topbar-counter" id="topCounter"></span>
    <div class="progress" id="progressDots"></div>
  </div>
  <div class="topbar-right">
    <button class="btn btn-nav" id="btnPrev" onclick="nav(-1)">&#9664; Prev</button>
    <button class="btn btn-accept" id="btnAccept" onclick="decide('accept')">&#10003; Accept</button>
    <button class="btn btn-reject" id="btnReject" onclick="decide('reject')">&#10007; Reject</button>
    <button class="btn btn-skip" id="btnSkip" onclick="decide('skip')">&#9654; Skip</button>
    <button class="btn btn-nav" id="btnExport" onclick="exportDecisions()" style="background:#dbeafe;color:#1d4ed8">&#8681; Export</button>
    <button class="btn btn-nav" id="btnNext" onclick="nav(1)">Next &#9654;</button>
  </div>
</div>

<div class="event-header" id="eventHeader"></div>

<div class="main">
  <div class="chart-col">
    <div class="chart-wrap">
      <div id="chart"></div>
      <div class="orb-zone-overlay" id="orbZone"></div>
    </div>
    <div class="summary" id="summaryPanel"></div>
  </div>
  <div class="explain-col">
    <div class="explain-panel" id="explainPanel"></div>
  </div>
</div>

<div class="kbd-hints">
  <kbd>&#8592;</kbd> Previous &nbsp;
  <kbd>&#8594;</kbd> Next &nbsp;
  <kbd>A</kbd> Accept &nbsp;
  <kbd>R</kbd> Reject &nbsp;
  <kbd>S</kbd> Skip &nbsp;
  <kbd>E</kbd> Export decisions
</div>

<script>
(function(){{
"use strict";

var EVENTS={events_json};
var TICK_SIZE={tick_size};
var currentIdx=0;
var decisions={{}};

// ── Tick-to-price ──
function tp(t){{return t==null?null:Math.round(t*TICK_SIZE*1e6)/1e6;}}
function fmtP(v){{return v==null?"—":v.toFixed(2);}}

// ── Timezone offset (EDT = UTC-4) ──
var ET_OFFSET=-4*3600;

var chart, series;

function initChart(){{
  var el=document.getElementById("chart");
  chart=LightweightCharts.createChart(el,{{
    width:el.clientWidth,height:520,
    layout:{{background:{{type:"solid",color:"#ffffff"}},
             textColor:"#333",fontSize:11}},
    grid:{{vertLines:{{color:"#f5f5f5"}},horzLines:{{color:"#f5f5f5"}}}},
    crosshair:{{mode:0}},
    timeScale:{{timeVisible:true,secondsVisible:false,borderColor:"#d0d7de"}},
    rightPriceScale:{{borderColor:"#d0d7de",minMove:0.01,scaleMargins:{{top:0.05,bottom:0.05}}}},
  }});
  series=chart.addCandlestickSeries({{
    upColor:"#26a69a",downColor:"#ef5350",
    borderUpColor:"#26a69a",borderDownColor:"#ef5350",
    wickUpColor:"#26a69a",wickDownColor:"#ef5350",
  }});
  window.addEventListener("resize",function(){{
    chart.applyOptions({{width:document.getElementById("chart").clientWidth}});
    updateOrbZone();
  }});
}}

function renderEvent(idx){{
  if(idx<0||idx>=EVENTS.length) return;
  currentIdx=idx;
  var E=EVENTS[idx];
  var ann=E.annotations||{{}};
  var expl=E.explain||{{}};

  // ── Candle data ──
  var candles=E.candles.map(function(c){{
    return {{time:Math.floor(c.time_ms/1000)+ET_OFFSET,
            open:c.open,high:c.high,low:c.low,close:c.close}};
  }});
  series.setData(candles);

  // ── Remove old price lines ──
  // Re-create series to clear price lines
  chart.removeSeries(series);
  series=chart.addCandlestickSeries({{
    upColor:"#26a69a",downColor:"#ef5350",
    borderUpColor:"#26a69a",borderDownColor:"#ef5350",
    wickUpColor:"#26a69a",wickDownColor:"#ef5350",
  }});
  series.setData(candles);

  // ── ORB lines: thick, distinctive ──
  var orbHighP=null, orbLowP=null;
  if(E.orb_high_ticks!=null){{
    orbHighP=tp(E.orb_high_ticks);
    series.createPriceLine({{price:orbHighP,color:"#e65100",
      lineWidth:3,lineStyle:0,axisLabelVisible:true,
      title:"ORB High "+fmtP(orbHighP)}});
  }}
  if(E.orb_low_ticks!=null){{
    orbLowP=tp(E.orb_low_ticks);
    series.createPriceLine({{price:orbLowP,color:"#7b1fa2",
      lineWidth:3,lineStyle:0,axisLabelVisible:true,
      title:"ORB Low "+fmtP(orbLowP)}});
  }}

  // ── Entry / Stop / Target ──
  if(ann.entry_price_ticks!=null){{
    var ep=tp(ann.entry_price_ticks);
    series.createPriceLine({{price:ep,color:"#1565c0",
      lineWidth:2,lineStyle:0,axisLabelVisible:true,
      title:"Entry "+fmtP(ep)}});
  }}
  if(ann.stop_price_ticks!=null){{
    var sp=tp(ann.stop_price_ticks);
    series.createPriceLine({{price:sp,color:"#c62828",
      lineWidth:2,lineStyle:2,axisLabelVisible:true,
      title:"Stop "+fmtP(sp)}});
  }}
  if(ann.r2_price_ticks!=null){{
    var r2=tp(ann.r2_price_ticks);
    series.createPriceLine({{price:r2,color:"#2e7d32",
      lineWidth:2,lineStyle:2,axisLabelVisible:true,
      title:"TP 2R "+fmtP(r2)}});
  }}

  // ── PDH / PDL ──
  if(E.pdh!=null){{
    series.createPriceLine({{price:E.pdh,color:"#6d4c41",
      lineWidth:1,lineStyle:3,axisLabelVisible:true,
      title:"PDH "+E.pdh.toFixed(2)}});
  }}
  if(E.pdl!=null){{
    series.createPriceLine({{price:E.pdl,color:"#6d4c41",
      lineWidth:1,lineStyle:3,axisLabelVisible:true,
      title:"PDL "+E.pdl.toFixed(2)}});
  }}

  // ── Markers: Break, Confirm, Exit only ──
  function ts(idx){{
    if(idx==null||idx<0||idx>=candles.length) return null;
    return candles[idx].time;
  }}
  var markers=[];

  // Failed retests as small yellow dots
  var fr=expl.failed_retests||[];
  for(var fi=0;fi<fr.length;fi++){{
    var fri=fr[fi];
    if(fri.bar_index!=null&&ts(fri.bar_index)!=null){{
      markers.push({{time:ts(fri.bar_index),position:"belowBar",
        color:"#d4a017",shape:"circle",text:"✗ Ret",size:0}});
    }}
  }}

  // Consecutive inside closes (orange squares)
  var cic=E.consecutive_inside_closes||[];
  for(var ci=0;ci<cic.length;ci++){{
    var cc=cic[ci];
    if(cc.bar_index!=null&&ts(cc.bar_index)!=null){{
      markers.push({{time:ts(cc.bar_index),position:"aboveBar",
        color:"#e65100",shape:"square",text:"In#"+(ci+1),size:0}});
    }}
  }}

  // Invalidation marker (red X)
  if(E.invalidation_index!=null&&ts(E.invalidation_index)!=null)
    markers.push({{time:ts(E.invalidation_index),position:"aboveBar",
      color:"#c62828",shape:"square",text:"✗ INVALID",size:1}});

  if(ann.break_candle_index!=null&&ts(ann.break_candle_index)!=null)
    markers.push({{time:ts(ann.break_candle_index),position:"aboveBar",
      color:"#1565c0",shape:"arrowDown",text:"Break",size:1}});
  if(ann.confirmation_candle_index!=null&&ts(ann.confirmation_candle_index)!=null)
    markers.push({{time:ts(ann.confirmation_candle_index),position:"belowBar",
      color:"#2e7d32",shape:"arrowUp",text:"Confirm",size:1}});
  if(ann.exit_candle_index!=null&&ts(ann.exit_candle_index)!=null)
    markers.push({{time:ts(ann.exit_candle_index),position:"aboveBar",
      color:"#e65100",shape:"circle",text:"Exit",size:2}});

  markers.sort(function(a,b){{return a.time-b.time;}});
  if(markers.length>0) series.setMarkers(markers);

  chart.timeScale().fitContent();

  // ── ORB zone overlay ──
  setTimeout(function(){{ updateOrbZone(); }}, 100);

  // ── Header ──
  renderHeader(E);

  // ── Explain panel ──
  renderExplain(E);

  // ── Summary ──
  renderSummary(E);

  // ── Nav state ──
  document.getElementById("btnPrev").disabled=(idx===0);
  document.getElementById("btnNext").disabled=(idx===EVENTS.length-1);
  document.getElementById("topCounter").textContent=
    (idx+1)+" / "+EVENTS.length;
  renderProgress();
}}

// ── ORB Zone overlay (semi-transparent band) ──
function updateOrbZone(){{
  var el=document.getElementById("orbZone");
  var E=EVENTS[currentIdx];
  if(!E||E.orb_high_ticks==null||E.orb_low_ticks==null){{
    el.style.display="none";return;
  }}
  var ohP=tp(E.orb_high_ticks), olP=tp(E.orb_low_ticks);
  var yH=series.priceToCoordinate(ohP);
  var yL=series.priceToCoordinate(olP);
  if(yH==null||yL==null){{el.style.display="none";return;}}
  var top=Math.min(yH,yL), height=Math.abs(yL-yH);
  if(height<2) height=2;
  el.style.display="block";
  el.style.top=top+"px";
  el.style.height=height+"px";
  el.style.background="rgba(255,152,0,0.08)";
  el.style.borderTop="1px solid rgba(230,81,0,0.3)";
  el.style.borderBottom="1px solid rgba(123,31,162,0.3)";
}}

function renderHeader(E){{
  var ann=E.annotations||{{}};
  var dir=E.direction||"?";
  var status=E.detection_status||"?";
  var outcome=ann.outcome||"";
  var dirClass=dir==="LONG"?"tag-long":"tag-short";
  var statusClass=status==="VALID"?"tag-valid":"tag-invalid";

  var h='<h1>'+esc(E.symbol||"?")+' — '+esc(E.session_date||"?")+'</h1>';
  if(E.sequence_id)
    h+='<span class="tag" style="background:#dbeafe;color:#1d4ed8">'+esc(E.sequence_id)+'</span>';
  h+='<span class="tag '+dirClass+'">'+esc(dir)+'</span>';
  h+='<span class="tag '+statusClass+'">'+esc(status)+'</span>';

  if(outcome&&outcome!=="NO_VALID_SETUP"&&outcome!=="None"){{
    var ocClass=outcome.indexOf("TARGET")>=0?"tag-win":
                outcome.indexOf("STOPPED")>=0?"tag-loss":"tag-open";
    h+='<span class="tag '+ocClass+'">'+esc(outcome)+'</span>';
  }}

  // P&L display
  var expl=E.explain||{{}};
  if(expl.outcome){{
    var rr=expl.outcome.realized_r;
    if(rr&&rr!=="—"){{
      var pnlClass=parseFloat(rr)>0?"pnl-win":"pnl-loss";
      h+='<span class="pnl '+pnlClass+'">'+esc(rr)+'R</span>';
    }}
  }}

  // Decision badge
  var dec=decisions[currentIdx];
  if(dec){{
    var decClass=dec==="accept"?"tag-win":dec==="reject"?"tag-loss":"tag-open";
    h+='<span class="tag '+decClass+'" style="margin-left:8px">'+
       esc(dec.toUpperCase())+'</span>';
  }}

  document.getElementById("eventHeader").innerHTML=h;
}}

function renderExplain(E){{
  var expl=E.explain||{{}};
  var panel=document.getElementById("explainPanel");
  var h='<h2>Detection Explain</h2>';

  var stages=expl.stages||[];
  for(var i=0;i<stages.length;i++){{
    var s=stages[i];
    var cls=s.status==="OK"?"ok":"failed";
    h+='<div class="explain-stage '+cls+'">';
    h+='<div class="explain-stage-name">'+esc(s.name)+'</div>';
    if(s.detail) h+='<div class="explain-stage-detail">'+esc(s.detail)+'</div>';
    if(s.reason) h+='<div class="explain-stage-detail" style="color:#a40e26">'+esc(s.reason)+'</div>';
    h+='</div>';
  }}

  // Failed retests
  var fr=expl.failed_retests||[];
  if(fr.length>0){{
    h+='<div class="explain-section"><h3>Failed Retests ('+fr.length+')</h3>';
    for(var j=0;j<fr.length;j++){{
      var f=fr[j];
      h+='<div class="failed-retest">';
      h+='<div class="failed-retest-title">Bar #'+f.bar_index+'</div>';
      h+='<div class="failed-retest-geo">';
      h+='Wick: '+(f.rejection_wick_ratio!=null?(f.rejection_wick_ratio*100).toFixed(1)+"%":"—");
      h+=' &middot; Body: '+(f.body_ratio!=null?(f.body_ratio*100).toFixed(1)+"%":"—");
      h+=' &middot; Close: '+(f.favorable_close_location!=null?(f.favorable_close_location*100).toFixed(1)+"%":"—");
      h+='</div>';
      if(f.failed_rules&&f.failed_rules.length>0){{
        h+='<div class="failed-retest-rules">'+f.failed_rules.join(", ")+'</div>';
      }}
      h+='</div>';
    }}
    h+='</div>';
  }}

  // Trade plan
  if(expl.trade_plan){{
    var tp=expl.trade_plan;
    h+='<div class="explain-section"><h3>Trade Plan</h3>';
    h+='<div class="explain-row"><span class="explain-label">Entry</span><span class="explain-value">'+esc(tp.entry)+'</span></div>';
    h+='<div class="explain-row"><span class="explain-label">Stop</span><span class="explain-value">'+esc(tp.stop)+'</span></div>';
    h+='<div class="explain-row"><span class="explain-label">Target 2R</span><span class="explain-value">'+esc(tp.target_2r)+'</span></div>';
    h+='<div class="explain-row"><span class="explain-label">Risk</span><span class="explain-value">'+esc(tp.risk)+'</span></div>';
    h+='</div>';
  }}

  // Outcome
  if(expl.outcome){{
    var oc=expl.outcome;
    var ocClass=oc.result.indexOf("TARGET")>=0?"win":
                oc.result.indexOf("STOPPED")>=0?"loss":"";
    h+='<div class="explain-section"><h3>Outcome</h3>';
    h+='<div class="explain-row"><span class="explain-label">Result</span><span class="explain-value '+ocClass+'">'+esc(oc.result)+'</span></div>';
    h+='<div class="explain-row"><span class="explain-label">Realized R</span><span class="explain-value '+ocClass+'">'+esc(oc.realized_r)+'</span></div>';
    h+='</div>';
  }}

  panel.innerHTML=h;
}}

function renderSummary(E){{
  var ann=E.annotations||{{}};
  var panel=document.getElementById("summaryPanel");
  var h='';

  // ORB section
  h+='<div class="summary-section"><h3>Opening Range</h3>';
  if(E.orb_high_ticks!=null)
    h+='<div class="srow"><span class="srow-label">ORB High</span><span class="srow-value">'+fmtP(tp(E.orb_high_ticks))+'</span></div>';
  if(E.orb_low_ticks!=null)
    h+='<div class="srow"><span class="srow-label">ORB Low</span><span class="srow-value">'+fmtP(tp(E.orb_low_ticks))+'</span></div>';
  if(E.level_source)
    h+='<div class="srow"><span class="srow-label">Level</span><span class="srow-value">'+esc(E.level_source)+'</span></div>';
  if(E.pdh!=null)
    h+='<div class="srow"><span class="srow-label">PDH</span><span class="srow-value">'+E.pdh.toFixed(2)+'</span></div>';
  if(E.pdl!=null)
    h+='<div class="srow"><span class="srow-label">PDL</span><span class="srow-value">'+E.pdl.toFixed(2)+'</span></div>';
  h+='</div>';

  // Stages section
  h+='<div class="summary-section"><h3>Stages</h3>';
  if(ann.break_candle_index!=null)
    h+='<div class="srow"><span class="srow-label">Break</span><span class="srow-value">Bar #'+ann.break_candle_index+'</span></div>';
  if(ann.displacement_start_index!=null)
    h+='<div class="srow"><span class="srow-label">Displacement</span><span class="srow-value">'+ann.displacement_start_index+' → '+(ann.displacement_end_index||"?")+'</span></div>';
  if(ann.confirmation_candle_index!=null)
    h+='<div class="srow"><span class="srow-label">Confirmation</span><span class="srow-value">Bar #'+ann.confirmation_candle_index+'</span></div>';
  h+='</div>';

  // Trade section
  if(ann.entry_price_ticks!=null){{
    h+='<div class="summary-section"><h3>Trade</h3>';
    h+='<div class="srow"><span class="srow-label">Entry</span><span class="srow-value">'+fmtP(tp(ann.entry_price_ticks))+'</span></div>';
    h+='<div class="srow"><span class="srow-label">Stop</span><span class="srow-value">'+fmtP(tp(ann.stop_price_ticks))+'</span></div>';
    h+='<div class="srow"><span class="srow-label">Target 2R</span><span class="srow-value">'+fmtP(tp(ann.r2_price_ticks))+'</span></div>';
    var oc=ann.outcome||"";
    var ocStyle=oc.indexOf("TARGET")>=0?"color:#116329;font-weight:700":
                oc.indexOf("STOPPED")>=0?"color:#a40e26;font-weight:700":"";
    h+='<div class="srow"><span class="srow-label">Outcome</span><span class="srow-value" style="'+ocStyle+'">'+esc(oc)+'</span></div>';
    h+='</div>';
  }}

  panel.innerHTML=h;
}}

function renderProgress(){{
  var h='';
  for(var i=0;i<EVENTS.length;i++){{
    var cls="progress-dot";
    if(i===currentIdx) cls+=" current";
    var d=decisions[i];
    if(d==="accept") cls+=" accepted";
    else if(d==="reject") cls+=" rejected";
    else if(d==="skip") cls+=" skipped";
    h+='<span class="'+cls+'" onclick="nav('+(i-currentIdx)+')" title="Event '+(i+1)+'"></span>';
  }}
  document.getElementById("progressDots").innerHTML=h;
}}

// ── Navigation ──
window.nav=function(delta){{
  var next=currentIdx+delta;
  if(next>=0&&next<EVENTS.length) renderEvent(next);
}};

// ── Decision ──
window.decide=function(d){{
  decisions[currentIdx]=d;
  renderHeader(EVENTS[currentIdx]);
  renderProgress();
  // Auto-advance to next undecided
  if(currentIdx<EVENTS.length-1) nav(1);
}};

// ── Keyboard shortcuts ──
document.addEventListener("keydown",function(e){{
  if(e.target.tagName==="INPUT"||e.target.tagName==="TEXTAREA") return;
  if(e.key==="ArrowLeft") nav(-1);
  else if(e.key==="ArrowRight") nav(1);
  else if(e.key==="a"||e.key==="A") decide("accept");
  else if(e.key==="r"||e.key==="R") decide("reject");
  else if(e.key==="s"||e.key==="S") decide("skip");
  else if(e.key==="e"||e.key==="E") exportDecisions();
}});

// ── Export decisions ──
window.exportDecisions=function(){{
  var out=[];
  for(var i=0;i<EVENTS.length;i++){{
    var e=EVENTS[i];
    var d=decisions[i]||"pending";
    out.push({{
      index:i,
      event_id:e.event_id||null,
      symbol:e.symbol||null,
      session_date:e.session_date||null,
      direction:e.direction||null,
      detection_status:e.detection_status||null,
      decision:d
    }});
  }}
  var json=JSON.stringify(out,null,2);
  var blob=new Blob([json],{{type:"application/json"}});
  var url=URL.createObjectURL(blob);
  var a=document.createElement("a");
  a.href=url;
  a.download="bdrr_decisions.json";
  a.click();
  URL.revokeObjectURL(url);
}};

function esc(v){{return v==null?"":String(v).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}}

// ── Init ──
initChart();
renderEvent(0);

}})();
</script>
</body>
</html>
"""


# ── Rendering ────────────────────────────────────────────────────────────────


def render_workspace_html(
    events: list[dict],
    title: str = "BDRR Review Workspace",
    tick_size: float = 0.01,
) -> str:
    """Render a multi-event review workspace as standalone HTML."""
    return _WORKSPACE_HTML.format(
        title=_esc(title),
        events_json=_to_json(events),
        tick_size=tick_size,
    )


def write_workspace_html(
    events: list[dict],
    output_path: str | Path,
    title: str = "BDRR Review Workspace",
    tick_size: float = 0.01,
) -> Path:
    """Write a review workspace HTML file."""
    p = Path(output_path)
    content = render_workspace_html(events, title, tick_size)
    p.write_text(content, encoding="utf-8", newline="\n")
    return p


# ── Convenience: generate from CSV ──────────────────────────────────────────


def generate_workspace_from_csv(
    csv_path: str | Path,
    output_path: str | Path,
    symbol: str = "SPY",
    direction: str = "LONG",
    level_source: str = "ORB_HIGH",
    tick_size: float = 0.01,
    valid_only: bool = False,
) -> Path:
    """One-step workspace generation from a 5m CSV file.

    Parameters
    ----------
    csv_path : path to the CSV (TradingView export format)
    output_path : where to write the HTML
    symbol : instrument symbol
    direction : LONG or SHORT
    level_source : ORB_HIGH or ORB_LOW
    tick_size : minimum tick size
    valid_only : if True, only include VALID detections

    Returns
    -------
    Path to the written HTML file.
    """
    # Parse CSV
    sessions_map: dict[str, list[dict]] = {}
    csv_path = Path(csv_path)
    with open(csv_path) as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i < 3:
                continue
            if not row[0].strip():
                continue
            dt = datetime.fromisoformat(row[0])
            date = row[0][:10]
            if date not in sessions_map:
                sessions_map[date] = []
            sessions_map[date].append({
                "time_ms": int(dt.timestamp() * 1000),
                "open": float(row[4]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[1]),
                "volume": int(float(row[5])),
            })

    session_list = []
    prev_date = None
    for date in sorted(sessions_map.keys()):
        candles = sessions_map[date]

        # Compute PDH/PDL from previous session
        pdh = None
        pdl = None
        if prev_date is not None and prev_date in sessions_map:
            prev_candles = sessions_map[prev_date]
            pdh = max(c["high"] for c in prev_candles)
            pdl = min(c["low"] for c in prev_candles)

        session_list.append({
            "symbol": symbol,
            "date": date,
            "market_timezone": "America/New_York",
            "session_open_utc_ms": candles[0]["time_ms"],
            "session_close_utc_ms": candles[-1]["time_ms"],
            "timeframe": "5m",
            "candles": candles,
            "pdh": pdh,
            "pdl": pdl,
        })
        prev_date = date

    preset = {
        "preset_id": "review",
        "timeframe_minutes": 5,
        "timezone": "America/New_York",
        "session_open": "09:30",
        "orb_start": "session_open",
        "orb_duration_minutes": 5,
        "level_source": level_source,
        "direction": direction,
        "entry_model": "CONFIRMATION_CLOSE",
        "entry_buffer_ticks": 0,
        "stop_buffer_ticks": 0,
        "min_displacement_ticks": None,
        "min_penetration_ticks": None,
        "min_close_beyond_level_ticks": None,
    }
    config = {
        "tick_size": tick_size,
        "exit_target_r": 2,
        "engine_version": "1.0.0",
    }

    events = build_workspace_events(session_list, preset, config)

    if valid_only:
        events = [e for e in events if e.get("detection_status") == "VALID"]

    title = f"{symbol} {direction} — BDRR Review ({len(events)} events)"
    return write_workspace_html(events, output_path, title, tick_size)
