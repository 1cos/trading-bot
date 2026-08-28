"""Weekly Strategy Lab aggregation — reads observations, decides nothing.

Joins three things that are written independently during a session:

    strategy_lab/<date>/candidates.jsonl   every pair the detector met
    strategy_lab/<date>/<SYM>_bars.jsonl   the tape, for forward settlement
    trade_state/*.json                     what was actually traded

and answers the question the last four audits could not: for each of the
four TWO_CANDLE semantics, what would the population have looked like.

One honest limit is carried explicitly rather than smoothed over. A
shadow candidate is a *geometry* candidate: the scan reproduces the
engine's retest-attempt filter, consecutiveness, NEWS filter and SINGLE
priority, but it does not run the break/displacement/retest chain. So a
pair marked PASS by M2 is not thereby a trade M2 would have taken — the
chain might never have armed that setup at all. Only candidates that
match a real trade_state record are known to have cleared the full
context, and those are counted separately as `actually_tradable`.
Comparing models against each other is sound; reading any single
model's win rate as a strategy result is not.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from trading_lab import strategy_lab
from trading_lab.live.strategy_lab_recorder import (
    DEFAULT_LAB_DIR,
    StrategyLabRecorder,
    available_dates,
)
from trading_lab.live.trade_state_store import DEFAULT_TRADE_STATE_DIR

COHORT_TAKEN = "CURRENTLY_TAKEN"
COHORT_NEW_PASS = "SHADOW_NEW_PASS"
COHORT_NEW_FAIL = "SHADOW_NEW_FAIL"


def _traded_index(trade_state_dir: str | Path) -> dict:
    """(symbol, entry_bar_time_ms) -> trade record, for the join."""
    index = {}
    base = Path(trade_state_dir)
    if not base.exists():
        return index
    for path in sorted(base.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                   # noqa: BLE001
            continue
        sym, ms = record.get("symbol"), record.get("entry_timestamp_ms")
        if sym and isinstance(ms, int):
            index[(sym, ms)] = record
    return index


def _cohort(candidate, model, was_taken):
    """Which of the three buckets this candidate falls into for `model`.

    A candidate is CURRENTLY_TAKEN when the live bot actually traded it,
    regardless of what the model says — that is the set being compared
    against. Otherwise the model's own verdict splits it.
    """
    if was_taken:
        return COHORT_TAKEN
    verdict = (candidate.get("shadow_verdicts", {}).get(model) or {}).get("verdict")
    return COHORT_NEW_PASS if verdict == "PASS" else COHORT_NEW_FAIL


def settle_candidates(date, *, lab_dir=DEFAULT_LAB_DIR,
                      trade_state_dir=DEFAULT_TRADE_STATE_DIR):
    """Attach forward R outcomes and the trade join to one date's rows."""
    recorder = StrategyLabRecorder(lab_dir)
    traded = _traded_index(trade_state_dir)
    tapes: dict[str, list] = {}
    out = []

    for candidate in recorder.load_candidates(date):
        symbol = candidate.get("symbol")
        if symbol not in tapes:
            tapes[symbol] = recorder.load_bars(date, symbol)
        tape = tapes[symbol]

        # The entry is candle 2's close, so settlement starts from the
        # bar that closed the pair, not the one that opened it.
        anchor = candidate.get("bar_time_ms")
        entry_ms = (candidate.get("candle2") or candidate.get("candle") or {}).get("time_ms")
        index = next((i for i, b in enumerate(tape)
                      if b.get("time_ms") == entry_ms), None)
        outcome = None
        if index is not None:
            outcome = strategy_lab.settle_r_outcome(
                tape, index, candidate["entry_price"], candidate["stop_price"],
                candidate["direction"],
            )
        was_taken = (symbol, entry_ms) in traded
        out.append({**candidate, "outcome": outcome,
                    "actually_tradable": was_taken,
                    "anchor_time_ms": anchor})
    return out


def _blank():
    return {"candidate": 0, "actually_tradable": 0,
            "stop_first": 0, "target_first": 0, "unsettled": 0,
            "mfe": [], "mae": [],
            **{strategy_lab._r_key(r): 0 for r in strategy_lab.R_LEVELS}}


def _add(acc, row):
    acc["candidate"] += 1
    acc["actually_tradable"] += int(bool(row.get("actually_tradable")))
    outcome = row.get("outcome")
    if not outcome:
        acc["unsettled"] += 1
        return
    acc["stop_first"] += int(bool(outcome["stop_first"]))
    acc["target_first"] += int(bool(outcome["target_first"]))
    acc["mfe"].append(outcome["mfe_r"])
    acc["mae"].append(outcome["mae_r"])
    for key, hit in outcome["r_reached"].items():
        if hit:
            acc[key] += 1


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _finalize(acc):
    out = dict(acc)
    out["mfe_r_median"] = _median(acc["mfe"])
    out["mae_r_median"] = _median(acc["mae"])
    del out["mfe"], out["mae"]
    return out


def build_report(dates, *, lab_dir=DEFAULT_LAB_DIR,
                 trade_state_dir=DEFAULT_TRADE_STATE_DIR):
    """The full weekly comparison: models x cohorts, plus the buckets."""
    rows = []
    for date in dates:
        rows.extend(settle_candidates(date, lab_dir=lab_dir,
                                      trade_state_dir=trade_state_dir))

    models = defaultdict(lambda: defaultdict(_blank))
    for model in strategy_lab.MODELS:
        for row in rows:
            # M2/M3/M4 are TWO_CANDLE semantics. A SINGLE row carries no
            # opinion from them, and counting it as their SHADOW_NEW_FAIL
            # would invent a rejection none of them ever made.
            if model != "M1_CURRENT" and row.get("pattern") != "TWO_CANDLE":
                continue
            verdict = (row.get("shadow_verdicts", {}).get(model) or {}).get("verdict")
            if verdict == "PASS":
                _add(models[model]["ALL_PASS"], row)
            _add(models[model][_cohort(row, model, row.get("actually_tradable"))], row)

    buckets = {
        "two_candle_margin_open2": defaultdict(_blank),
        "two_candle_penetration_c1": defaultdict(_blank),
        "single_range": defaultdict(_blank),
        "structural_classification": defaultdict(_blank),
    }
    for row in rows:
        b = row.get("buckets") or {}
        if row.get("pattern") == "TWO_CANDLE":
            if b.get("margin_open2"):
                _add(buckets["two_candle_margin_open2"][b["margin_open2"]], row)
            if b.get("penetration_c1"):
                _add(buckets["two_candle_penetration_c1"][b["penetration_c1"]], row)
            if row.get("structural_classification"):
                _add(buckets["structural_classification"][row["structural_classification"]], row)
        elif b.get("single_range"):
            _add(buckets["single_range"][b["single_range"]], row)

    return {
        "schema_version": strategy_lab.SCHEMA_VERSION,
        "dates": list(dates),
        "total_candidates": len(rows),
        "models": {m: {c: _finalize(a) for c, a in cohorts.items()}
                   for m, cohorts in models.items()},
        "buckets": {name: {k: _finalize(v) for k, v in group.items()}
                    for name, group in buckets.items()},
        "note": ("Shadow candidates are geometry candidates: the break/"
                 "displacement/retest chain is NOT replayed. Only rows with "
                 "actually_tradable=true are known to have cleared the full "
                 "strategy context."),
    }


def format_report(report) -> str:
    lines = [f"STRATEGY LAB — {', '.join(report['dates'])}",
             f"candidati totali: {report['total_candidates']}", ""]
    r_keys = [strategy_lab._r_key(r) for r in strategy_lab.R_LEVELS]
    head = (f'{"modello":22}{"coorte":18}{"cand":>6}{"tradab":>7}'
            + "".join(f"{k:>7}" for k in r_keys)
            + f'{"MFE":>7}{"MAE":>7}{"stopF":>7}{"tgtF":>6}')
    lines += [head, "-" * len(head)]
    for model in strategy_lab.MODELS:
        for cohort in ("ALL_PASS", COHORT_TAKEN, COHORT_NEW_PASS, COHORT_NEW_FAIL):
            a = report["models"].get(model, {}).get(cohort)
            if not a or not a["candidate"]:
                continue
            mfe = "—" if a["mfe_r_median"] is None else f'{a["mfe_r_median"]:.2f}'
            mae = "—" if a["mae_r_median"] is None else f'{a["mae_r_median"]:.2f}'
            lines.append(f'{model:22}{cohort:18}{a["candidate"]:>6}{a["actually_tradable"]:>7}'
                         + "".join(f'{a[k]:>7}' for k in r_keys)
                         + f'{mfe:>7}{mae:>7}{a["stop_first"]:>7}{a["target_first"]:>6}')
    for name, group in report["buckets"].items():
        if not group:
            continue
        lines += ["", f"— {name} —",
                  f'{"bucket":24}{"cand":>6}{"tradab":>7}'
                  + "".join(f"{k:>7}" for k in r_keys) + f'{"MFE":>7}']
        for key in sorted(group):
            a = group[key]
            mfe = "—" if a["mfe_r_median"] is None else f'{a["mfe_r_median"]:.2f}'
            lines.append(f'{key:24}{a["candidate"]:>6}{a["actually_tradable"]:>7}'
                         + "".join(f'{a[k]:>7}' for k in r_keys) + f'{mfe:>7}')
    lines += ["", report["note"]]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Weekly Strategy Lab report (read-only).")
    p.add_argument("--date", action="append", dest="dates",
                   help="session date YYYY-MM-DD; repeatable. Default: all available.")
    p.add_argument("--lab-dir", default=DEFAULT_LAB_DIR)
    p.add_argument("--trade-state-dir", default=DEFAULT_TRADE_STATE_DIR)
    p.add_argument("--json", action="store_true", help="emit raw JSON instead of a table")
    args = p.parse_args(argv)

    dates = args.dates or available_dates(args.lab_dir)
    if not dates:
        print(f"nessuna sessione in {args.lab_dir}")
        return 1
    report = build_report(dates, lab_dir=args.lab_dir,
                          trade_state_dir=args.trade_state_dir)
    print(json.dumps(report, indent=2, default=str) if args.json
          else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
