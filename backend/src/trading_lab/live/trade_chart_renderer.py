"""Render a trade's chart to PNG from its persisted trade_state record.

For visual review of Max Entry Candles: the numbers in a JSON record say
whether a candle qualified, but not whether it *looks* like a rejection.
This draws what the detector saw, in Chicago time, so an image can be put
next to a TradingView chart and compared directly.

Reads only what is already on disk — chart_context, exit_chart_context
and setup_snapshot. It requests nothing, recomputes no level, and knows
nothing about IBKR, the orchestrator or the trading loop. Rendering a
chart can never change or delay a trade.

Two images per trade:

    ..._ENTRY.png   lead-in context up to and including the entry candle,
                    with the geometry that made it qualify
    ..._EXIT.png    the whole path from lead-in to the exit bar, with the
                    outcome

Timezone: every visible label is America/Chicago, because that is what
the charts being compared against are set to. The records keep their
original UTC milliseconds untouched.

Geometry is not re-derived by hand: the same
evaluate_single_candle_rejection_geometry() the detector uses is called
on the same candle, so a panel can never disagree with the engine that
took the trade.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")           # no display, no GUI toolkit, no window
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from trading_lab.rejection_finder import evaluate_single_candle_rejection_geometry

log = logging.getLogger("maxbot")

CHART_TZ = ZoneInfo("America/Chicago")
DEFAULT_CHART_DIR = Path("logs/maxbot/trade_charts")

# Structural levels, in the order they are drawn and listed.
_LEVELS = (
    ("orb_high", "ORB H", "#2563eb"),
    ("orb_low", "ORB L", "#2563eb"),
    ("pdh", "PDH", "#7c3aed"),
    ("pdl", "PDL", "#7c3aed"),
    ("pmh", "PMH", "#0891b2"),
    ("pml", "PML", "#0891b2"),
)

_UP = "#16a34a"
_DOWN = "#dc2626"
_ENTRY_HL = "#f59e0b"


# ── helpers ──────────────────────────────────────────────────────────────────

def _ct(ms: int | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, CHART_TZ)


def _hm(ms: int | None) -> str:
    d = _ct(ms)
    return d.strftime("%H:%M") if d else "—"


def _price(v) -> float | None:
    """A snapshot price is either a plain number or {ticks, tick_size}."""
    if isinstance(v, dict) and "ticks" in v:
        try:
            return int(v["ticks"]) * float(v["tick_size"])
        except (TypeError, ValueError):
            return None
    return float(v) if isinstance(v, (int, float)) else None


def _ratio(v) -> float | None:
    if isinstance(v, dict) and "denominator" in v:
        try:
            d = float(v["denominator"])
            return float(v["numerator"]) / d if d else None
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    return float(v) if isinstance(v, (int, float)) else None


def _candle_of(block: dict) -> dict | None:
    """A setup_snapshot bar -> a plain candle dict."""
    if not isinstance(block, dict):
        return None
    out = {"time_ms": block.get("bar_utc_ms")}
    for k in ("open", "high", "low", "close"):
        p = _price(block.get(k))
        if p is None:
            return None
        out[k] = p
    out["volume"] = block.get("volume") or 0
    return out


def chart_filename(record: dict, kind: str) -> str:
    """Deterministic, human-readable, and unique.

    Symbol/direction/level/time read at a glance; the break timestamp is
    appended only when it is needed to tell two trades apart, so the
    common case stays short.
    """
    snap = record.get("setup_snapshot") or {}
    sym = record.get("symbol") or "UNKNOWN"
    direction = record.get("direction") or "?"
    level = snap.get("level_source") or "LEVEL"
    hhmm = (_ct(record.get("entry_timestamp_ms")) or datetime(1970, 1, 1, tzinfo=CHART_TZ)).strftime("%H%M")
    return f"{sym}_{direction}_{level}_{hhmm}_{kind}.png"


def _unique_path(directory: Path, name: str, record: dict) -> Path:
    """Resolve a collision with the trade_id, never by overwriting.

    A sidecar records which trade an image belongs to, so redrawing the
    same trade reuses its path (idempotent) while a genuinely different
    trade that happens to share symbol, direction, level and entry
    minute gets its own file instead of silently replacing one.
    """
    path = directory / name
    if not path.exists():
        return path
    existing = None
    try:
        existing = json.loads(path.with_suffix(".meta").read_text()).get("trade_id")
    except (OSError, ValueError):
        pass
    if existing is None or existing == record.get("trade_id"):
        return path                       # same trade, or unlabelled: a redraw
    tid = str(record.get("trade_id") or "x").replace(":", "_")
    return directory / f"{name[:-4]}__{tid}.png"


def _stamp(path: Path, record: dict) -> None:
    """Remember whose image this is. Failure here costs nothing."""
    try:
        path.with_suffix(".meta").write_text(
            json.dumps({"trade_id": record.get("trade_id")}))
    except OSError:
        pass


# ── drawing ──────────────────────────────────────────────────────────────────

def _pattern_bars(record, candles):
    """Bars to outline: the entry candle, plus candle 1 of a two-candle
    pattern — otherwise the picture shows one highlighted bar while the
    panel talks about two."""
    entry_ms = record.get("entry_timestamp_ms")
    marks = {entry_ms} if entry_ms is not None else set()
    snap = record.get("setup_snapshot") or {}
    if snap.get("entry_pattern_type") == "TWO_CANDLE_ENGULFING_RECOVERY":
        conf = (snap.get("confirmation_bar") or {}).get("bar_utc_ms")
        idx = next((i for i, c in enumerate(candles) if c["time_ms"] == conf), None)
        if idx:
            marks.add(candles[idx - 1]["time_ms"])
    return marks


def _draw_candles(ax, candles, highlight_ms=(), exit_ms=None):
    for i, c in enumerate(candles):
        o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
        colour = _UP if cl >= o else _DOWN
        ax.plot([i, i], [l, h], color=colour, linewidth=0.9, zorder=2)
        body_low, body_h = min(o, cl), abs(cl - o)
        ax.add_patch(Rectangle((i - 0.3, body_low), 0.6, body_h or 0.0001,
                               facecolor=colour, edgecolor=colour,
                               linewidth=0.6, zorder=3))
        if c["time_ms"] in highlight_ms:
            ax.add_patch(Rectangle((i - 0.45, l), 0.9, (h - l) or 0.0001,
                                   facecolor="none", edgecolor=_ENTRY_HL,
                                   linewidth=1.8, zorder=4))
        if exit_ms is not None and c["time_ms"] == exit_ms:
            ax.add_patch(Rectangle((i - 0.45, l), 0.9, (h - l) or 0.0001,
                                   facecolor="none", edgecolor="#0ea5e9",
                                   linewidth=1.8, linestyle=":", zorder=4))


def _price_window(candles, *plan):
    """Vertical range: the price action, plus the trade's own levels.

    Structural levels deliberately do NOT get a vote. A PDH sitting five
    dollars above the action would stretch the axis until the candles
    collapse into an unreadable band — which is exactly what makes the
    picture worthless for judging a wick.
    """
    lo = min(c["low"] for c in candles)
    hi = max(c["high"] for c in candles)
    for v in plan:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            lo, hi = min(lo, v), max(hi, v)
    pad = (hi - lo) * 0.08 or 0.05
    return lo - pad, hi + pad


def _draw_levels(ax, levels, n):
    """Only levels that actually have a value — a missing PDH is drawn as
    nothing, never as a line at zero.

    A level outside the visible range is not silently dropped: it is
    reported in a corner note, so "PDH is far above" stays visible
    without letting it dictate the scale.
    """
    ylo, yhi = ax.get_ylim()
    drawn, off_chart = [], []
    for key, label, colour in _LEVELS:
        v = (levels or {}).get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if not (ylo <= v <= yhi):
            off_chart.append(f"{label} {v:.2f}")
            continue
        ax.axhline(v, color=colour, linewidth=0.8, linestyle="--", alpha=0.55, zorder=1)
        # Inside the axes: outside it would run over the geometry panel.
        ax.annotate(f"{label} {v:.2f}", xy=(0.998, v),
                    xycoords=("axes fraction", "data"), xytext=(0, 0),
                    textcoords="offset points", va="center", ha="right",
                    fontsize=6.5, color=colour, zorder=5,
                    bbox=dict(boxstyle="square,pad=0.12", fc="white",
                              ec="none", alpha=0.8))
        drawn.append(label)
    if off_chart:
        ax.annotate("off chart:  " + "   ".join(off_chart), xy=(0, 0),
                    xycoords="axes fraction", xytext=(4, 4),
                    textcoords="offset points", fontsize=6.5,
                    color="#6b7280", zorder=8)
    return drawn


def _draw_plan(ax, entry, stop, target, n):
    for value, label, colour in ((entry, "ENTRY", "#111827"),
                                 (stop, "STOP", _DOWN),
                                 (target, "TARGET", _UP)):
        if not isinstance(value, (int, float)):
            continue
        ax.axhline(value, color=colour, linewidth=1.2, alpha=0.9, zorder=6)
        ax.annotate(f"{label} {value:.2f}", xy=(0.004, value),
                    xycoords=("axes fraction", "data"), xytext=(0, 4),
                    textcoords="offset points", fontsize=7, color=colour,
                    fontweight="bold", zorder=7,
                    bbox=dict(boxstyle="square,pad=0.12", fc="white",
                              ec="none", alpha=0.8))


def _time_axis(ax, candles):
    n = len(candles)
    step = max(1, n // 12)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([_hm(candles[i]["time_ms"]) for i in ticks],
                       fontsize=7, rotation=0)
    ax.set_xlim(-1, n)
    ax.grid(alpha=0.15, linewidth=0.5)
    ax.tick_params(labelsize=7)
    ax.set_xlabel("America/Chicago", fontsize=7, color="#6b7280")


# ── geometry panel ───────────────────────────────────────────────────────────

def _geometry_lines(record: dict, candles: list[dict]) -> list[str]:
    """The numbers behind the entry, computed by the engine's own function."""
    snap = record.get("setup_snapshot") or {}
    direction = record.get("direction") or ""
    level = _price(snap.get("level_price"))
    pattern = snap.get("entry_pattern_type") or "—"
    conf = _candle_of(snap.get("confirmation_bar") or {})
    lines = [f"PATTERN  {pattern}", ""]

    if conf is None or level is None:
        lines.append("(confirmation bar not in record)")
        return lines

    def block(title, candle, note=""):
        out = [title]
        out.append(f"  O {candle['open']:.2f}  H {candle['high']:.2f}"
                   f"  L {candle['low']:.2f}  C {candle['close']:.2f}")
        try:
            q = evaluate_single_candle_rejection_geometry(
                candle, direction, level, 0.01,
                confirmation_wick_penetration_pct_min=0.20)
            g = q["geometry"]
            out.append(f"  wick_ratio          {g['rejection_wick_ratio']:.3f}")
            out.append(f"  body_ratio          {g['body_ratio']:.3f}")
            out.append(f"  close_location      {g['favorable_close_location']:.3f}")
            out.append(f"  penetration         {g['penetration_through_level_ticks']} ticks")
            out.append(f"  wick_penetration_%  {g['wick_penetration_pct']:.3f}")
            out.append(f"  body_outside_level  {bool(g['body_outside_orb'])}")
            out.append(f"  as SINGLE           {'PASS' if q['qualifies'] else 'FAIL'}")
        except Exception as e:                      # never break an image
            out.append(f"  (geometry unavailable: {e})")
        if note:
            out.append(f"  {note}")
        return out

    if pattern == "TWO_CANDLE_ENGULFING_RECOVERY":
        idx = next((i for i, c in enumerate(candles)
                    if c["time_ms"] == conf["time_ms"]), None)
        first = candles[idx - 1] if idx not in (None, 0) else None
        if first is not None:
            lines += block(f"CANDLE 1  {_hm(first['time_ms'])} CT",
                           first, "-> penetration candle")
            lines.append("")
        lines += block(f"CANDLE 2  {_hm(conf['time_ms'])} CT",
                       conf, "-> ENTRY at its close")
        lines.append("")
        if first is not None:
            pair = (max(first["high"], conf["high"]) if direction == "SHORT"
                    else min(first["low"], conf["low"]))
            side = "high" if direction == "SHORT" else "low"
            lines.append(f"STOP from PAIR {side}: {pair:.2f}")
            lines.append("  (extreme of candle 1 + candle 2)")
    else:
        lines += block(f"ENTRY CANDLE  {_hm(conf['time_ms'])} CT", conf,
                       "-> ENTRY at its close, STOP at its wick")

    lines += ["", f"LEVEL  {snap.get('level_source','?')}  {level:.2f}",
              f"BREAK  {_hm((snap.get('break_bar') or {}).get('bar_utc_ms'))} CT"]
    return lines


def _panel(ax, lines):
    ax.axis("off")
    ax.text(0.0, 1.0, "\n".join(lines), va="top", ha="left",
            fontsize=7.6, family="monospace", linespacing=1.45)


# ── public API ───────────────────────────────────────────────────────────────

def render_entry_chart(record: dict, out_dir: Path | str) -> Path | None:
    """Draw the entry image. None when the record has no chart_context."""
    ctx = record.get("chart_context")
    if not isinstance(ctx, dict) or not ctx.get("candles"):
        return None
    candles = ctx["candles"]
    entry_ms = record.get("entry_timestamp_ms")
    snap = record.get("setup_snapshot") or {}

    fig = plt.figure(figsize=(15, 7.2), dpi=110)
    gs = fig.add_gridspec(1, 2, width_ratios=[3.05, 1], wspace=0.06)
    ax, panel = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])

    _draw_candles(ax, candles, highlight_ms=_pattern_bars(record, candles))
    ax.set_ylim(*_price_window(candles, record.get("underlying_entry"),
                               record.get("stop"), record.get("target")))
    _draw_levels(ax, ctx.get("levels"), len(candles))
    _draw_plan(ax, record.get("underlying_entry"), record.get("stop"),
               record.get("target"), len(candles))
    _time_axis(ax, candles)
    ax.set_title(
        f"{record.get('symbol','?')}  {record.get('direction','?')}  ·  "
        f"{snap.get('level_source','?')}  ·  {snap.get('entry_pattern_type','?')}\n"
        f"entry {_hm(entry_ms)} CT   ·   {len(candles)} bars 1m",
        fontsize=11, fontweight="bold", loc="left")
    _panel(panel, _geometry_lines(record, candles))

    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_path(out_dir, chart_filename(record, "ENTRY"), record)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    _stamp(path, record)
    return path


def render_exit_chart(record: dict, out_dir: Path | str) -> Path | None:
    """Draw the exit image. None when the trade has no exit_chart_context —
    an open or interrupted trade simply has no exit to draw."""
    ctx = record.get("exit_chart_context")
    if not isinstance(ctx, dict) or not ctx.get("candles"):
        return None
    candles = ctx["candles"]
    entry_ms = record.get("entry_timestamp_ms")
    window = ctx.get("window") or {}
    exit_ms = window.get("exit_time_ms")
    if exit_ms is None:
        # A realtime price trigger carries no bar timestamp — nothing in
        # the market "closed" to cause it — so the window has no
        # exit_time_ms. Fall back to the exit event's own clock, floored
        # to its minute, purely to mark the bar on the picture. The
        # record is not touched.
        ev = (record.get("outcome") or {}).get("trigger_time_ms")
        if isinstance(ev, (int, float)):
            floor = int(ev) // 60_000 * 60_000
            if any(c["time_ms"] == floor for c in candles):
                exit_ms = floor
    snap = record.get("setup_snapshot") or {}
    outcome = record.get("outcome") or {}
    terminal = record.get("terminal") or {}

    reason = outcome.get("exit_reason") or terminal.get("reason") or "—"
    result = outcome.get("result") or record.get("state") or "—"
    pnl = outcome.get("gross_pnl")
    pnl_txt = f"   ·   P&L {pnl:+.2f}" if isinstance(pnl, (int, float)) else ""
    fill = outcome.get("exit_fill_premium")

    fig = plt.figure(figsize=(15, 7.2), dpi=110)
    gs = fig.add_gridspec(1, 2, width_ratios=[3.05, 1], wspace=0.06)
    ax, panel = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])

    _draw_candles(ax, candles, highlight_ms=_pattern_bars(record, candles),
                  exit_ms=exit_ms)
    ax.set_ylim(*_price_window(candles, record.get("underlying_entry"),
                               record.get("stop"), record.get("target")))
    _draw_levels(ax, ctx.get("levels"), len(candles))
    _draw_plan(ax, record.get("underlying_entry"), record.get("stop"),
               record.get("target"), len(candles))
    _time_axis(ax, candles)
    ax.set_title(
        f"{record.get('symbol','?')}  {record.get('direction','?')}  ·  "
        f"{snap.get('level_source','?')}  ·  {result}  ·  exit {reason}{pnl_txt}\n"
        f"entry {_hm(entry_ms)} CT   →   exit {_hm(exit_ms)} CT   ·   "
        f"{len(candles)} bars 1m",
        fontsize=11, fontweight="bold", loc="left")

    lines = _geometry_lines(record, candles)
    lines += ["", "OUTCOME", f"  technical   {result}", f"  exit reason {reason}"]
    if isinstance(pnl, (int, float)):
        lines.append(f"  gross P&L   {pnl:+.2f}")
    if isinstance(fill, (int, float)):
        lines.append(f"  exit fill   {fill:.2f}")
    probe = record.get("r_probe") or {}
    if probe:
        lines += ["", "R PROBE",
                  f"  MFE {probe.get('mfe_r','—')}R   MAE {probe.get('mae_r','—')}R"]
        for lvl in ("2r", "2_5r", "3r", "3_5r", "4r"):
            t = (probe.get("first_touch") or {}).get(lvl)
            lines.append(f"  {lvl:5s} {'first touch ' + _hm(t) + ' CT' if t else 'not reached'}")
    _panel(panel, lines)

    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_path(out_dir, chart_filename(record, "EXIT"), record)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    _stamp(path, record)
    return path


def render_trade_charts(record: dict, base_dir: Path | str = DEFAULT_CHART_DIR,
                        *, session_date: str | None = None) -> dict:
    """Both images for one trade. Never raises.

    A chart is a review aid: if drawing one fails, that must cost a log
    line and nothing else. This is what makes the renderer safe to call
    from the trading path later.
    """
    out = {"entry": None, "exit": None, "error": None}
    try:
        date = session_date or ((record.get("setup_snapshot") or {})
                                .get("session") or {}).get("date")
        if not date:
            d = _ct(record.get("entry_timestamp_ms"))
            date = d.strftime("%Y-%m-%d") if d else "unknown"
        directory = Path(base_dir) / date
        out["entry"] = render_entry_chart(record, directory)
        out["exit"] = render_exit_chart(record, directory)
    except Exception as e:
        out["error"] = str(e)
        log.warning(f"trade chart rendering failed for "
                    f"{record.get('trade_id')!r}: {e}")
    return out


def render_session(date: str, state_dir: Path | str = "logs/maxbot/trade_state",
                   base_dir: Path | str = DEFAULT_CHART_DIR,
                   *, overwrite: bool = False) -> list[dict]:
    """Render every trade of one session date. Returns one row per trade."""
    rows = []
    for path in sorted(Path(state_dir).glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            log.warning(f"skipping unreadable {path.name}: {e}")
            continue
        if not isinstance(record, dict):
            continue
        session = ((record.get("setup_snapshot") or {}).get("session") or {})
        if session.get("date") != date:
            continue
        directory = Path(base_dir) / date
        if not overwrite:
            e_p = directory / chart_filename(record, "ENTRY")
            x_p = directory / chart_filename(record, "EXIT")
            if e_p.exists() and (x_p.exists() or not record.get("exit_chart_context")):
                rows.append({"trade_id": record.get("trade_id"), "entry": e_p,
                             "exit": x_p if x_p.exists() else None,
                             "skipped": True, "error": None})
                continue
        res = render_trade_charts(record, base_dir, session_date=date)
        rows.append({"trade_id": record.get("trade_id"), "skipped": False, **res})
    return rows


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Render MaxBot trade charts to PNG.")
    p.add_argument("--date", required=True, help="session date, YYYY-MM-DD")
    p.add_argument("--state-dir", default="logs/maxbot/trade_state")
    p.add_argument("--out-dir", default=str(DEFAULT_CHART_DIR))
    p.add_argument("--overwrite", action="store_true",
                   help="redraw images that already exist")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows = render_session(a.date, a.state_dir, a.out_dir, overwrite=a.overwrite)
    for r in rows:
        tag = "skip" if r.get("skipped") else ("FAIL" if r.get("error") else "ok")
        print(f"[{tag:4s}] {r['trade_id']}")
        for k in ("entry", "exit"):
            if r.get(k):
                print(f"         {k}: {r[k]}")
        if r.get("error"):
            print(f"         error: {r['error']}")
    print(f"\n{len(rows)} trade(s) for {a.date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
