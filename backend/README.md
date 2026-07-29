# Trading Lab — Python Backend

## Quick Start — Backtest Lab

```bash
cd backend
pip install -e ".[dev]"
pip install flask flask-cors
bash start_lab.sh
# Open http://localhost:5001
```

The Backtest Lab is the primary interface. It runs the real Python BDRR
detector against real market data. No strategy logic runs in JavaScript.

## Status

The Python BDRR engine is the production implementation. All detection stages
are fully implemented and validated against oracle fixtures for both LONG and
SHORT directions.

### Completed (as of July 2026 sessions)

**LONG + SHORT pipeline parity** — both directions are fully implemented
across the entire pipeline: ORB builder, break finder, displacement finder,
retest window, rejection finder, trade plan builder, outcome evaluator,
detection result builder, strategy runner, and research batch runner.

**Visual Review Exporter** (`visual_review_exporter.py`) — Exports
deterministic JSON event payloads from Strategy Runner results for chart
rendering. Supports LONG, SHORT, VALID, and FAILED events.

**Visual Review HTML Renderer** (`visual_review_html.py`) — Renders
standalone candlestick charts from exported event payloads. Uses Lightweight
Charts v4 (TradingView open-source). Light theme, ET timezone, price labels,
P&L in header. Markers for Break, Confirm, Exit. Lines for ORB High/Low,
Entry, Stop, Target 2R.

**Review Workspace** (`review_workspace.py`) — Trading Day Review Workspace
for rapid event review. Single HTML file with all events, chart with ORB zone
overlay, explain panel showing detection stage reasoning, Previous/Next
navigation, Accept/Reject/Skip buttons with keyboard shortcuts (←→ ARS),
progress dots, and summary panel. Designed for a trader to decide Accept,
Reject, or Skip in seconds. Decisions will later train the Scorer.

**Real data validation** — Charts generated from `dati/SPY_5m.csv` (60 days,
78 candles per session) verified against TradingView. 8 VALID LONG setups found
across 60 sessions (6 STOPPED, 2 TARGET_HIT).

### Test Coverage

- Python: 1526 tests passing
- JavaScript: all 15 test files passing

### Known Issues — Next Session

**Retest logic investigation needed.** Max reported disagreement with the
first retest marker placement. The engine marks the first candle whose
low touches the level as the retest start, but Max reads the following
candle as the actual retest. This may be a logic issue, not just UI.

**No pre-ORB candles.** CSV data starts at 09:30. For proper trading
context the chart needs candles before the opening range.

**No PDH/PDL levels.** Previous Day High/Low are defined in
`estrategie/pdh_pdl_definition.js` but not computed by the Python runner.

## Relationship to the JavaScript BDRR Engine

The existing JavaScript BDRR engine (`estrategie/bdrr_engine.js` and related
modules) is the frozen, validated reference implementation. Its oracle fixtures
(`dati/bdrr_spy_oracle.json`, `dati/bdrr_qqq_oracle.json`) and regression test
suite define the canonical expected behavior for every detection stage.

Python is the production implementation of the BDRR strategy engine.
Python behavior has been validated against the existing oracle fixtures and
confirmed at parity with the JavaScript reference for LONG direction.
SHORT direction is Python-only (JavaScript does not implement SHORT).

## Development

```bash
cd backend
pip install -e ".[dev]"
pytest
```

Requires Python >= 3.11.

## Generating the Review Workspace

```python
from trading_lab.review_workspace import generate_workspace_from_csv

# Generate workspace with VALID setups only
generate_workspace_from_csv(
    "dati/SPY_5m.csv",
    "backend/output/SPY_review_workspace.html",
    symbol="SPY",
    direction="LONG",
    level_source="ORB_HIGH",
    valid_only=True,
)

# Generate workspace with ALL sessions (VALID + INVALID)
generate_workspace_from_csv(
    "dati/SPY_5m.csv",
    "backend/output/SPY_review_all.html",
    symbol="SPY",
    direction="LONG",
    level_source="ORB_HIGH",
    valid_only=False,
)
```

Use keyboard shortcuts: ← Previous, → Next, A Accept, R Reject, S Skip.
