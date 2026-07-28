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
    """Serialize to deterministic JSON for embedding in HTML."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _esc(value: object) -> str:
    """HTML-escape a value for safe embedding in markup."""
    if value is None:
        return ""
    return html.escape(str(value))


# ── Tick-to-price conversion ────────────────────────────────────────────────


def _ticks_to_price(ticks: int | None, tick_size: float) -> float | None:
    if ticks is None:
        return None
    return round(ticks * tick_size, 6)


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
.legend{{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:11px;
  color:#8b949e;margin-top:8px;padding-top:8px;
  border-top:1px solid #21262d}}
.legend-item{{display:flex;align-items:center;gap:4px}}
.legend-swatch{{width:12px;height:3px;border-radius:1px}}
.chart-container{{width:100%;height:480px;border-radius:4px;
  border:1px solid #21262d;overflow:hidden}}
.ann-table{{margin-top:12px;font-size:12px;border-collapse:collapse;
  width:100%;max-width:720px}}
.ann-table th,.ann-table td{{text-align:left;padding:3px 10px;
  border-bottom:1px solid #21262d}}
.ann-table th{{color:#8b949e;font-weight:400}}
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
  Level: {level_source} @ {level_price_display} &nbsp;|&nbsp;
  Event: {event_id_display}
</div>
<div id="chart" class="chart-container"></div>
<div class="legend" id="legend"></div>
{annotations_table}
<script>
(function(){{
"use strict";
var EVENT={event_json};
var TICK_SIZE={tick_size};

function tp(ticks){{return ticks==null?null:Math.round(ticks*TICK_SIZE*1e6)/1e6;}}

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
var lines=[];
var markers=[];

// Level price
if(EVENT.level_price_ticks!=null){{
  lines.push({{price:tp(EVENT.level_price_ticks),color:"#8b949e",
    style:2,width:1,title:"Level"}});
}}

// Entry
if(ann.entry_price_ticks!=null){{
  lines.push({{price:tp(ann.entry_price_ticks),color:"#58a6ff",
    style:0,width:1,title:"Entry"}});
}}

// Stop
if(ann.stop_price_ticks!=null){{
  lines.push({{price:tp(ann.stop_price_ticks),color:"#f85149",
    style:0,width:1,title:"Stop"}});
}}

// Targets
if(ann.r2_price_ticks!=null){{
  lines.push({{price:tp(ann.r2_price_ticks),color:"#3fb950",
    style:2,width:1,title:"2R"}});
}}
if(ann.r3_price_ticks!=null){{
  lines.push({{price:tp(ann.r3_price_ticks),color:"#3fb950",
    style:3,width:1,title:"3R"}});
}}
if(ann.r4_price_ticks!=null){{
  lines.push({{price:tp(ann.r4_price_ticks),color:"#3fb950",
    style:3,width:1,title:"4R"}});
}}

// Exit price
if(ann.exit_price_ticks!=null){{
  lines.push({{price:tp(ann.exit_price_ticks),color:"#d29922",
    style:0,width:2,title:"Exit"}});
}}

lines.forEach(function(l){{
  series.createPriceLine({{
    price:l.price,color:l.color,lineWidth:l.width,
    lineStyle:l.style,axisLabelVisible:true,
    title:l.title
  }});
}});

// Candle markers
function ts(idx){{
  if(idx==null||idx<0||idx>=candles.length) return null;
  return candles[idx].time;
}}

var mList=[
  {{idx:ann.break_candle_index,label:"B",tip:"Break",
    color:"#58a6ff",pos:"aboveBar"}},
  {{idx:ann.displacement_start_index,label:"Ds",tip:"Disp Start",
    color:"#a371f7",pos:"aboveBar"}},
  {{idx:ann.displacement_end_index,label:"De",tip:"Disp End",
    color:"#a371f7",pos:"aboveBar"}},
  {{idx:ann.retest_start_index,label:"Rs",tip:"Retest Start",
    color:"#d29922",pos:"belowBar"}},
  {{idx:ann.retest_end_index,label:"Re",tip:"Retest End",
    color:"#d29922",pos:"belowBar"}},
  {{idx:ann.confirmation_candle_index,label:"C",tip:"Confirmation",
    color:"#3fb950",pos:"belowBar"}},
  {{idx:ann.exit_candle_index,label:"X",tip:"Exit",
    color:"#f0883e",pos:"aboveBar"}},
];

var validMarkers=mList.filter(function(m){{
  return m.idx!=null&&ts(m.idx)!=null;
}}).map(function(m){{
  return {{time:ts(m.idx),position:m.pos,color:m.color,
           shape:"circle",text:m.label,size:1}};
}});

validMarkers.sort(function(a,b){{return a.time-b.time;}});
if(validMarkers.length>0) series.setMarkers(validMarkers);

chart.timeScale().fitContent();

// Legend
var legendEl=document.getElementById("legend");
var legendItems=[
  {{color:"#8b949e",label:"Level"}},
  {{color:"#58a6ff",label:"Entry / Break"}},
  {{color:"#f85149",label:"Stop"}},
  {{color:"#3fb950",label:"Target / Confirm"}},
  {{color:"#a371f7",label:"Displacement"}},
  {{color:"#d29922",label:"Retest / Exit"}},
];
legendItems.forEach(function(it){{
  var d=document.createElement("span");
  d.className="legend-item";
  d.innerHTML='<span class="legend-swatch" style="background:'+it.color+
    '"></span>'+it.label;
  legendEl.appendChild(d);
}});

window.addEventListener("resize",function(){{
  chart.applyOptions({{width:document.getElementById("chart").clientWidth}});
}});
}})();
</script>
</body>
</html>
"""


# ── Annotation summary table ────────────────────────────────────────────────


def _build_annotations_table(ann: dict, tick_size: float) -> str:
    """Build an HTML table summarizing annotation values."""
    rows: list[tuple[str, str]] = []

    def _add(label: str, key: str, is_ticks: bool = False) -> None:
        v = ann.get(key)
        if v is None:
            return
        if is_ticks:
            price = _ticks_to_price(v, tick_size)
            rows.append((label, f"{v} ticks ({price})"))
        else:
            rows.append((label, str(v)))

    _add("Break candle", "break_candle_index")
    _add("Displacement", "displacement_start_index")
    _add("Displacement end", "displacement_end_index")
    _add("Retest", "retest_start_index")
    _add("Retest end", "retest_end_index")
    _add("Confirmation", "confirmation_candle_index")
    _add("Entry", "entry_price_ticks", True)
    _add("Stop", "stop_price_ticks", True)
    _add("2R target", "r2_price_ticks", True)
    _add("3R target", "r3_price_ticks", True)
    _add("4R target", "r4_price_ticks", True)
    _add("Exit candle", "exit_candle_index")
    _add("Exit price", "exit_price_ticks", True)
    _add("Outcome", "outcome")
    _add("Failed stage", "failed_stage")

    fr = ann.get("failed_rules", [])
    if fr:
        rows.append(("Failed rules", ", ".join(str(r) for r in fr)))

    if not rows:
        return ""

    row_html = "\n".join(
        f"  <tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>"
        for label, value in rows
    )
    return f'<table class="ann-table">\n{row_html}\n</table>'


# ── Tick size detection ─────────────────────────────────────────────────────


def _infer_tick_size(event: dict) -> float:
    """Infer tick_size from the event data.

    Uses level_price_ticks and the first candle's price to estimate.
    Falls back to 0.01 if insufficient data.
    """
    lp_ticks = event.get("level_price_ticks")
    candles = event.get("candles", [])
    if lp_ticks and candles and lp_ticks != 0:
        first_high = candles[0].get("high")
        if first_high and first_high != 0:
            estimated = first_high / (lp_ticks / (first_high / 0.01))
            # Snap to common tick sizes
            for ts in (0.0001, 0.001, 0.01, 0.05, 0.10, 0.25, 0.50, 1.0):
                ratio = first_high / ts
                ticks_est = round(ratio)
                if ticks_est != 0 and abs(first_high / ticks_est - ts) < ts * 0.01:
                    return ts
    return 0.01


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
    level_source = _esc(event.get("level_source") or "?")
    event_id = event.get("event_id")
    ann = event.get("annotations") or {}
    outcome = ann.get("outcome")
    failed_stage = event.get("failed_stage")

    tick_size = _infer_tick_size(event)

    # Direction CSS class
    dir_class = "tag-long" if direction == "LONG" else "tag-short"

    # Status CSS class
    status_class = "tag-valid" if detection_status == "VALID" else "tag-invalid"

    # Outcome tag
    outcome_tag = ""
    if outcome and outcome not in ("", "None", "NO_VALID_SETUP"):
        outcome_tag = f'<span class="tag tag-outcome">{_esc(outcome)}</span>'

    # Failed stage tag
    failed_tag = ""
    if failed_stage:
        failed_tag = f'<span class="tag tag-invalid">{_esc(str(failed_stage))}</span>'

    # Level price display
    lp_ticks = event.get("level_price_ticks")
    level_price_display = "?"
    if lp_ticks is not None:
        price = _ticks_to_price(lp_ticks, tick_size)
        level_price_display = f"{price}"

    # Event ID display
    event_id_display = _esc(str(event_id)[:12] + "…") if event_id else "n/a"

    # Title
    title = f"{symbol} {session_date} {direction} — Visual Review"

    # Annotations table
    annotations_table = _build_annotations_table(ann, tick_size)

    # Embed event JSON deterministically
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
        level_source=level_source,
        level_price_display=level_price_display,
        event_id_display=event_id_display,
        annotations_table=annotations_table,
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
