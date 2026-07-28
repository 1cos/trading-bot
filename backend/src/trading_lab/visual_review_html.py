"""Standalone HTML candlestick chart renderer for visual review events.

Accepts one event payload from ``export_visual_event`` and returns a
complete, self-contained HTML document that renders an interactive
candlestick chart with detection annotations.

Uses Lightweight Charts v4 (TradingView open-source, Apache 2.0)
loaded from unpkg CDN — single ``<script>`` tag, no build process.

Public API:

    render_visual_event_html(event)       → str   (HTML document)
    write_visual_event_html(event, path)  → Path  (writes file)

Determinism:
    Given the same event dict, the output HTML is identical.
    No generated timestamps, random IDs, or machine-specific paths.
"""

from __future__ import annotations

import html
import json
from pathlib import Path


# ── Deterministic JSON ──────────────────────────────────────────────────────


def _to_json(obj: object) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


# ── Tick-to-price conversion ────────────────────────────────────────────────


def _ticks_to_price(ticks: int | None, tick_size: float) -> float | None:
    if ticks is None:
        return None
    return round(ticks * tick_size, 6)


def _fmt_price(ticks: int | None, tick_size: float) -> str:
    """Format ticks as 'price (N ticks)' or empty string."""
    if ticks is None:
        return ""
    price = _ticks_to_price(ticks, tick_size)
    return f"{price} ({ticks} ticks)"


# ── Tick size detection ─────────────────────────────────────────────────────


def _infer_tick_size(event: dict) -> float:
    lp_ticks = event.get("level_price_ticks")
    orb_h = event.get("orb_high_ticks")
    candles = event.get("candles", [])
    ref_ticks = lp_ticks or orb_h
    if ref_ticks and candles and ref_ticks != 0:
        first_high = candles[0].get("high")
        if first_high and first_high != 0:
            for ts in (0.0001, 0.001, 0.01, 0.05, 0.10, 0.25, 0.50, 1.0):
                ticks_est = round(first_high / ts)
                if ticks_est != 0 and abs(first_high / ticks_est - ts) < ts * 0.01:
                    return ts
    return 0.01


# ── HTML template ───────────────────────────────────────────────────────────


_HTML_TEMPLATE = """\
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
  background:#0e1117;color:#c9d1d9;padding:16px}}
.header{{display:flex;flex-wrap:wrap;gap:8px 24px;align-items:baseline;
  margin-bottom:12px}}
.header h1{{font-size:18px;font-weight:600;letter-spacing:0.02em}}
.tag{{display:inline-block;padding:2px 8px;border-radius:3px;
  font-size:12px;font-weight:500;letter-spacing:0.04em}}
.tag-long{{background:#0d4429;color:#3fb950}}
.tag-short{{background:#4a1524;color:#f85149}}
.tag-valid{{background:#0c2d6b;color:#58a6ff}}
.tag-invalid{{background:#3d1d00;color:#d29922}}
.tag-outcome{{background:#1c1d21;color:#8b949e;border:1px solid #30363d}}
.meta{{font-size:12px;color:#8b949e;margin-bottom:8px}}
.chart-container{{width:100%;height:480px;border-radius:4px;
  border:1px solid #21262d;overflow:hidden}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:11px;
  color:#8b949e;margin-top:8px;padding-top:8px;
  border-top:1px solid #21262d}}
.legend-item{{display:flex;align-items:center;gap:4px}}
.legend-swatch{{width:14px;height:3px;border-radius:1px}}
.summary{{margin-top:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));
  gap:12px}}
.summary-section{{background:#161b22;border:1px solid #21262d;
  border-radius:4px;padding:10px 14px}}
.summary-section h3{{font-size:12px;color:#58a6ff;font-weight:500;
  margin-bottom:6px;letter-spacing:0.06em;text-transform:uppercase}}
.row{{display:flex;justify-content:space-between;padding:2px 0;
  font-size:12px;border-bottom:1px solid #21262d}}
.row:last-child{{border-bottom:none}}
.row-label{{color:#8b949e}}
.row-value{{color:#c9d1d9;font-weight:500}}
.row-value.selected{{color:#58a6ff}}
.row-value.win{{color:#3fb950}}
.row-value.loss{{color:#f85149}}
</style>
</head>
<body>
<div class="header">
  <h1>{symbol} &mdash; {session_date}</h1>
  <span class="tag {dir_class}">{direction}</span>
  <span class="tag {status_class}">{detection_status}</span>
  {outcome_tag}
  {failed_tag}
</div>
<div class="meta">
  Event: {event_id_display}
</div>
<div id="chart" class="chart-container"></div>
<div class="legend">
  <span class="legend-item"><span class="legend-swatch" style="background:#e3b341"></span>ORB High</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#a371f7"></span>ORB Low</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#58a6ff"></span>Entry</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#f85149"></span>Stop</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#3fb950"></span>Targets</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#f0883e"></span>Exit</span>
</div>
{summary_html}
<script>
(function(){{
"use strict";
var EVENT={event_json};
var TICK_SIZE={tick_size};
function tp(t){{return t==null?null:Math.round(t*TICK_SIZE*1e6)/1e6;}}

var candles=EVENT.candles.map(function(c){{
  return {{time:Math.floor(c.time_ms/1000),open:c.open,high:c.high,
           low:c.low,close:c.close}};
}});

var chart=LightweightCharts.createChart(document.getElementById("chart"),{{
  width:document.getElementById("chart").clientWidth,
  height:480,
  layout:{{background:{{type:"solid",color:"#0e1117"}},
           textColor:"#8b949e",fontSize:11}},
  grid:{{vertLines:{{color:"#1b1f27"}},horzLines:{{color:"#1b1f27"}}}},
  crosshair:{{mode:0}},
  timeScale:{{timeVisible:true,secondsVisible:false,
              borderColor:"#21262d"}},
  rightPriceScale:{{borderColor:"#21262d"}},
}});

var series=chart.addCandlestickSeries({{
  upColor:"#3fb950",downColor:"#f85149",
  borderUpColor:"#3fb950",borderDownColor:"#f85149",
  wickUpColor:"#3fb950",wickDownColor:"#f85149",
}});
series.setData(candles);

var ann=EVENT.annotations||{{}};
var ls=EVENT.level_source||"";

// ── ORB High and Low lines ──
var orbHighLabel=ls==="ORB_HIGH"?"ORB High \u2190 selected":"ORB High";
var orbLowLabel=ls==="ORB_LOW"?"ORB Low \u2190 selected":"ORB Low";
var orbHighStyle=ls==="ORB_HIGH"?0:2;
var orbLowStyle=ls==="ORB_LOW"?0:2;
var orbHighWidth=ls==="ORB_HIGH"?2:1;
var orbLowWidth=ls==="ORB_LOW"?2:1;

if(EVENT.orb_high_ticks!=null){{
  series.createPriceLine({{price:tp(EVENT.orb_high_ticks),color:"#e3b341",
    lineWidth:orbHighWidth,lineStyle:orbHighStyle,axisLabelVisible:true,
    title:orbHighLabel}});
}}
if(EVENT.orb_low_ticks!=null){{
  series.createPriceLine({{price:tp(EVENT.orb_low_ticks),color:"#a371f7",
    lineWidth:orbLowWidth,lineStyle:orbLowStyle,axisLabelVisible:true,
    title:orbLowLabel}});
}}

// ── Trade lines ──
if(ann.entry_price_ticks!=null){{
  series.createPriceLine({{price:tp(ann.entry_price_ticks),color:"#58a6ff",
    lineWidth:1,lineStyle:0,axisLabelVisible:true,title:"Entry"}});
}}
if(ann.stop_price_ticks!=null){{
  series.createPriceLine({{price:tp(ann.stop_price_ticks),color:"#f85149",
    lineWidth:1,lineStyle:0,axisLabelVisible:true,title:"Stop"}});
}}
if(ann.r2_price_ticks!=null){{
  series.createPriceLine({{price:tp(ann.r2_price_ticks),color:"#3fb950",
    lineWidth:1,lineStyle:2,axisLabelVisible:true,title:"2R"}});
}}
if(ann.r3_price_ticks!=null){{
  series.createPriceLine({{price:tp(ann.r3_price_ticks),color:"#3fb950",
    lineWidth:1,lineStyle:3,axisLabelVisible:true,title:"3R"}});
}}
if(ann.r4_price_ticks!=null){{
  series.createPriceLine({{price:tp(ann.r4_price_ticks),color:"#3fb950",
    lineWidth:1,lineStyle:3,axisLabelVisible:true,title:"4R"}});
}}
if(ann.exit_price_ticks!=null){{
  series.createPriceLine({{price:tp(ann.exit_price_ticks),color:"#f0883e",
    lineWidth:2,lineStyle:0,axisLabelVisible:true,title:"Exit"}});
}}

// ── Candle markers ──
function ts(idx){{
  if(idx==null||idx<0||idx>=candles.length) return null;
  return candles[idx].time;
}}
var mList=[
  {{idx:ann.break_candle_index,label:"Break",tip:"Break candle",
    color:"#58a6ff",pos:"aboveBar"}},
  {{idx:ann.displacement_start_index,label:"Disp\u2192",tip:"Displacement start",
    color:"#a371f7",pos:"aboveBar"}},
  {{idx:ann.displacement_end_index,label:"\u2190Disp",tip:"Displacement end",
    color:"#a371f7",pos:"aboveBar"}},
  {{idx:ann.retest_start_index,label:"Ret\u2192",tip:"Retest start",
    color:"#d29922",pos:"belowBar"}},
  {{idx:ann.retest_end_index,label:"\u2190Ret",tip:"Retest end",
    color:"#d29922",pos:"belowBar"}},
  {{idx:ann.confirmation_candle_index,label:"Confirm",tip:"Confirmation candle",
    color:"#3fb950",pos:"belowBar"}},
  {{idx:ann.exit_candle_index,label:"Exit",tip:"Exit candle",
    color:"#f0883e",pos:"aboveBar"}},
];

// Deduplicate markers on the same candle
var seen={{}};
var validMarkers=[];
mList.forEach(function(m){{
  if(m.idx==null||ts(m.idx)==null) return;
  var t=ts(m.idx);
  if(seen[t]){{
    seen[t].text+=(" | "+m.label);
    return;
  }}
  var mk={{time:t,position:m.pos,color:m.color,
           shape:"circle",text:m.label,size:1}};
  seen[t]=mk;
  validMarkers.push(mk);
}});
validMarkers.sort(function(a,b){{return a.time-b.time;}});
if(validMarkers.length>0) series.setMarkers(validMarkers);

chart.timeScale().fitContent();
window.addEventListener("resize",function(){{
  chart.applyOptions({{width:document.getElementById("chart").clientWidth}});
}});
}})();
</script>
</body>
</html>
"""


# ── Summary panel builder ───────────────────────────────────────────────────


def _build_summary_html(event: dict, tick_size: float) -> str:
    ann = event.get("annotations") or {}
    ls = event.get("level_source") or ""

    def _price(ticks):
        return _fmt_price(ticks, tick_size)

    def _idx(key):
        v = ann.get(key)
        return str(v) if v is not None else ""

    # ── ORB & Level section
    orb_rows = []
    oh = event.get("orb_high_ticks")
    ol = event.get("orb_low_ticks")
    sel = " selected" if ls == "ORB_HIGH" else ""
    orb_rows.append(("ORB High", _price(oh), sel))
    sel = " selected" if ls == "ORB_LOW" else ""
    orb_rows.append(("ORB Low", _price(ol), sel))
    orb_rows.append(("Selected level", _esc(ls), ""))
    orb_rows.append(("Level price", _price(event.get("level_price_ticks")), ""))

    # ── Detection stages
    stage_rows = []
    stage_rows.append(("Break candle", _idx("break_candle_index"), ""))
    ds = ann.get("displacement_start_index")
    de = ann.get("displacement_end_index")
    if ds is not None and de is not None:
        stage_rows.append(("Displacement", f"{ds} → {de}", ""))
    elif ds is not None:
        stage_rows.append(("Displacement start", str(ds), ""))
    rs = ann.get("retest_start_index")
    re_ = ann.get("retest_end_index")
    if rs is not None and re_ is not None:
        stage_rows.append(("Retest", f"{rs} → {re_}", ""))
    elif rs is not None:
        stage_rows.append(("Retest start", str(rs), ""))
    stage_rows.append(("Confirmation", _idx("confirmation_candle_index"), ""))

    # ── Trade plan
    trade_rows = []
    trade_rows.append(("Entry", _price(ann.get("entry_price_ticks")), ""))
    trade_rows.append(("Stop", _price(ann.get("stop_price_ticks")), ""))
    trade_rows.append(("2R target", _price(ann.get("r2_price_ticks")), ""))
    trade_rows.append(("3R target", _price(ann.get("r3_price_ticks")), ""))
    trade_rows.append(("4R target", _price(ann.get("r4_price_ticks")), ""))

    # ── Outcome
    outcome_rows = []
    outcome = ann.get("outcome", "")
    oc_class = ""
    if "TARGET" in str(outcome):
        oc_class = " win"
    elif "STOPPED" in str(outcome):
        oc_class = " loss"
    outcome_rows.append(("Outcome", _esc(str(outcome)), oc_class))
    outcome_rows.append(("Exit candle", _idx("exit_candle_index"), ""))
    outcome_rows.append(("Exit price", _price(ann.get("exit_price_ticks")), ""))

    fs = ann.get("failed_stage") or event.get("failed_stage")
    if fs:
        outcome_rows.append(("Failed stage", _esc(str(fs)), ""))
    fr = ann.get("failed_rules", [])
    if fr:
        outcome_rows.append(("Failed rules", _esc(", ".join(str(r) for r in fr)), ""))

    def _section(title, rows):
        filtered = [(l, v, c) for l, v, c in rows if v]
        if not filtered:
            return ""
        inner = "\n".join(
            f'    <div class="row"><span class="row-label">{_esc(l)}</span>'
            f'<span class="row-value{c}">{v}</span></div>'
            for l, v, c in filtered
        )
        return (
            f'<div class="summary-section">\n'
            f'  <h3>{_esc(title)}</h3>\n{inner}\n</div>'
        )

    sections = [
        _section("Opening Range", orb_rows),
        _section("Detection Stages", stage_rows),
        _section("Trade Plan", trade_rows),
        _section("Outcome", outcome_rows),
    ]
    filled = [s for s in sections if s]
    if not filled:
        return ""
    return '<div class="summary">\n' + "\n".join(filled) + "\n</div>"


# ── Primary export ──────────────────────────────────────────────────────────


def render_visual_event_html(event: dict) -> str:
    """Render a visual review event as a standalone HTML candlestick chart.

    Parameters
    ----------
    event : dict
        One event payload from ``export_visual_event``.

    Returns
    -------
    str
        Complete HTML document.

    Deterministic: same event → same output. No timestamps or random IDs.
    """
    symbol = _esc(event.get("symbol") or "???")
    session_date = _esc(event.get("session_date") or "???")
    direction = event.get("direction") or "?"
    detection_status = event.get("detection_status") or "?"
    event_id = event.get("event_id")
    ann = event.get("annotations") or {}
    outcome = ann.get("outcome")
    failed_stage = event.get("failed_stage")

    tick_size = _infer_tick_size(event)

    dir_class = "tag-long" if direction == "LONG" else "tag-short"
    status_class = "tag-valid" if detection_status == "VALID" else "tag-invalid"

    outcome_tag = ""
    if outcome and outcome not in ("", "None", "NO_VALID_SETUP"):
        outcome_tag = f'<span class="tag tag-outcome">{_esc(outcome)}</span>'

    failed_tag = ""
    if failed_stage:
        failed_tag = (
            f'<span class="tag tag-invalid">{_esc(str(failed_stage))}</span>'
        )

    event_id_display = _esc(str(event_id)[:12] + "…") if event_id else "n/a"
    title = f"{symbol} {session_date} {direction} — Visual Review"

    summary_html = _build_summary_html(event, tick_size)
    event_json = _to_json(event)

    return _HTML_TEMPLATE.format(
        title=title,
        symbol=symbol,
        session_date=session_date,
        direction=_esc(direction),
        dir_class=dir_class,
        detection_status=_esc(detection_status),
        status_class=status_class,
        outcome_tag=outcome_tag,
        failed_tag=failed_tag,
        event_id_display=event_id_display,
        summary_html=summary_html,
        event_json=event_json,
        tick_size=tick_size,
    )


# ── File-writing helper ────────────────────────────────────────────────────


def write_visual_event_html(
    event: dict,
    output_path: str | Path,
) -> Path:
    """Write a visual review HTML chart to a file.

    Parameters
    ----------
    event : dict
        One event payload from ``export_visual_event``.
    output_path : str or Path
        File path to write. Created or overwritten.

    Returns
    -------
    Path
        The written file path.
    """
    p = Path(output_path)
    content = render_visual_event_html(event)
    p.write_text(content, encoding="utf-8", newline="\n")
    return p
