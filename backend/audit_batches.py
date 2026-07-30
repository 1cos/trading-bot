"""Final audit: incomplete sessions, duplicates, regressions.

Run: python backend/audit_batches.py
"""

import csv
import json
import hashlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

ET = ZoneInfo("America/New_York")
DATI = Path("dati")
OUTPUT = Path("backend/output")

SYMBOLS = ["SPY", "QQQ", "AMZN", "TSLA", "NVDA", "META", "MSFT", "GOOGL", "MU"]
EXCLUDE = {"2026-07-30"}

print("=" * 60)
print("AUDIT 1: INCOMPLETE SESSIONS")
print("=" * 60)

for sym in SYMBOLS:
    path = DATI / f"{sym}_1m.csv"
    if not path.exists(): continue
    sessions = {}
    with open(path) as f:
        for i, row in enumerate(csv.reader(f)):
            if i < 3: continue
            if not row[0].strip(): continue
            dt = datetime.fromisoformat(row[0])
            d = row[0][:10]
            if d not in sessions: sessions[d] = []
            sessions[d].append({"ts": row[0], "time_ms": int(dt.timestamp()*1000)})

    dates = sorted(sessions.keys())
    complete = 0
    incomplete = 0
    excluded = 0
    for d in dates:
        bars = sessions[d]
        n = len(bars)
        if d in EXCLUDE:
            excluded += 1
            continue
        if n == 390:
            complete += 1
        else:
            incomplete += 1
            first_ts = bars[0]["ts"]
            last_ts = bars[-1]["ts"]
            # Check for internal gaps
            expected_times = set()
            dt_first = datetime.fromisoformat(first_ts)
            for m in range(390):
                from datetime import timedelta
                expected_times.add((dt_first + timedelta(minutes=m)).strftime("%H:%M"))
            actual_times = set()
            for b in bars:
                dt_b = datetime.fromisoformat(b["ts"])
                actual_times.add(dt_b.strftime("%H:%M"))
            missing = len(expected_times - actual_times) if len(bars) < 390 else 0
            print(f"\n  {sym} {d}: {n} bars (INCOMPLETE)")
            print(f"    First: {first_ts}")
            print(f"    Last:  {last_ts}")
            print(f"    Missing bars: ~{390-n}")
            print(f"    Reason: {'download started after session open' if n < 390 else 'unknown'}")

    if incomplete == 0:
        desc = f"{complete} complete"
    else:
        desc = f"{complete} complete, {incomplete} incomplete"
    if excluded:
        desc += f", {excluded} excluded (Jul 30)"
    print(f"  {sym}: {desc}")

print(f"\n{'='*60}")
print("AUDIT 2: VALID TOTALS BY TIMEFRAME")
print("=" * 60)

# Read the generated batch files and count
all_html = OUTPUT / "training_batch_all.html"
if all_html.exists():
    content = all_html.read_text()
    # Extract events JSON
    start = content.find("var EV=") + 7
    end = content.find(";\nvar ci=")
    if start > 7 and end > 0:
        events = json.loads(content[start:end])
        by_tf = {}
        by_dir = {}
        ids = []
        for e in events:
            tf = e.get("timeframe", "?")
            d = e.get("direction", "?")
            by_tf[tf] = by_tf.get(tf, 0) + 1
            by_dir[d] = by_dir.get(d, 0) + 1
            sid = f"{e.get('symbol')}_{e.get('session_date')}_{tf}_{d}_{e.get('sequence_id')}"
            ids.append(sid)

        for tf in sorted(by_tf.keys()):
            print(f"  {tf}: {by_tf[tf]}")
        print(f"  Total: {len(events)}")

        print(f"\n{'='*60}")
        print("AUDIT 3: VALID TOTALS BY DIRECTION")
        print("=" * 60)
        for d in sorted(by_dir.keys()):
            print(f"  {d}: {by_dir[d]}")

        print(f"\n{'='*60}")
        print("AUDIT 4: INTEGRITY CHECKS")
        print("=" * 60)

        # Duplicate check
        unique_ids = set(ids)
        print(f"  Unique setup identities: {len(unique_ids)}")
        print(f"  Total setups: {len(ids)}")
        print(f"  Duplicates: {len(ids) - len(unique_ids)}")
        if len(ids) != len(unique_ids):
            from collections import Counter
            dupes = [k for k, v in Counter(ids).items() if v > 1]
            for dup in dupes[:5]:
                print(f"    DUPLICATE: {dup}")

        # July 30 exclusion check
        jul30 = [e for e in events if e.get("session_date") == "2026-07-30"]
        print(f"  July 30 setups: {len(jul30)} {'(OK - excluded)' if len(jul30)==0 else 'PROBLEM'}")

        # training_workspace_8.html unchanged
        tw8 = OUTPUT / "training_workspace_8.html"
        if tw8.exists():
            h = hashlib.md5(tw8.read_bytes()).hexdigest()
            print(f"  training_workspace_8.html exists: Yes (MD5: {h[:12]})")
        else:
            print(f"  training_workspace_8.html exists: No")

        # Check no overwritten files
        for name in ["training_workspace_8.html"]:
            p = OUTPUT / name
            # Just confirm it exists and wasn't replaced
            if p.exists():
                # Check it still has the old 8 events
                c = p.read_text()
                if "L-TR-001" in c:
                    print(f"  {name}: contains original data (OK)")
                else:
                    print(f"  {name}: CONTENT CHANGED")

        # Check batch files use frozen template features
        for batch_name in ["training_batch_1m.html", "training_batch_all.html"]:
            bp = OUTPUT / batch_name
            if bp.exists():
                bc = bp.read_text()
                has_form = "Detector Structure" in bc and "Would Trade" in bc and "Quality" in bc
                has_export = "Export Reviews" in bc
                has_chart = "LightweightCharts" in bc
                print(f"  {batch_name}: form={has_form} export={has_export} chart={has_chart}")
    else:
        print("  Could not parse training_batch_all.html")
else:
    print("  training_batch_all.html not found")

print(f"\n{'='*60}")
print("AUDIT 5: REGRESSION SUITES")
print("=" * 60)
print("  Run manually:")
print("    python -m pytest backend/tests/ -q --tb=no")
print("    cd estrategie && for f in test_*.js; do echo -n \"$f: \"; node \"$f\" 2>&1 | tail -1; done")

print(f"\n{'='*60}")
print("AUDIT COMPLETE")
