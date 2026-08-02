# One Candle Level — OCL v0.1 Specification

> **Status:** Active research track
> **Created:** 2026-08-02
> **Author:** Max (strategy definition), Claude (documentation)
> **Replaces:** Order Block research (paused, not deleted)

---

## 1. What OCL Is

One Candle Level (OCL) is the internal name for a specific continuation
structure defined by Max. It describes a single opposing candle inside
strong directional momentum, whose wick defines a level that price may
later retest.

OCL is **not** a generic Order Block. The term "Order Block" has many
conflicting public definitions. OCL is Max's precise strategy — do not
replace it with internet OB definitions.

---

## 2. OCL v0.1 Definition — LONG

1. Price is moving upward with clear, fast momentum.
2. Inside that upward move, exactly one bearish candle appears.
3. That bearish candle must have an upper wick (wick pointing in the
   direction of the trend).
4. After that single bearish candle, the upward trend continues promptly.
5. The OCL wick zone is defined as the bearish candle's open through its
   high (i.e. the upper wick region).
6. Later, price returns to retest that wick zone.
7. The retest may touch only the wick — price does not need to enter the
   candle body.
8. The entry/rejection candle itself performs the retest: a later bullish
   candle that touches the OCL wick zone and rejects upward. The retest
   and rejection are one candle, not two separate events.
9. Initial entry model: enter at the close of that entry/rejection candle.
10. Initial stop concepts (preserve for future testing, do not choose yet):
    - Below the entry/rejection candle.
    - Below the complete One Candle.
11. Initial target: 2R.

## 3. OCL v0.1 Definition — SHORT

Exact mirror image:

1. Price is moving downward with clear, fast momentum.
2. Inside that downward move, exactly one bullish candle appears.
3. That bullish candle must have a lower wick (pointing in the direction
   of the downward trend).
4. Downward momentum must resume promptly.
5. The OCL wick zone is defined as the bullish candle's low through its
   open (i.e. the lower wick region).
6. Later, price returns to retest that wick zone.
7. The entry/rejection candle itself performs the retest: a later bearish
   candle that touches the OCL wick zone and rejects downward. The retest
   and rejection are one candle, not two separate events.
8. Initial target: 2R.

---

## 4. Frozen Research Assumptions

These are the initial constraints for the first discovery batch.
They are research starting points, not final optimized parameters.

| Parameter             | Frozen value                           |
|-----------------------|----------------------------------------|
| Timeframe             | 1 minute (genuine data only)           |
| Opposing candles      | Exactly one                            |
| Wick requirement      | Must point in trend direction          |
| Continuation          | Momentum must resume after the candle  |
| LONG wick zone        | Bearish candle open through high       |
| SHORT wick zone       | Bullish candle low through open        |
| Retest focus          | First later retest of the wick         |
| Entry model           | The entry/rejection candle itself touches the wick zone and rejects — retest and rejection are one candle, not two |
| Target                | 2R                                     |
| Quality axes          | Formation quality and trade quality are judged separately |

---

## 5. Unresolved Questions

These must be learned from examples and later exposed as configurable
Lab parameters where appropriate. Do not invent answers.

- Exact definition of "strong momentum"
- Minimum number of trend candles before the OCL
- Minimum number of trend candles after the OCL
- Minimum momentum distance
- Maximum delay before continuation resumes
- Exact wick-size requirement
- Minimum distance price must move away before a retest qualifies
- Maximum age of the OCL level
- Whether second retests are allowed
- Stop mode selection
- Confluence requirements (PDH/PDL, ORB, support/resistance)
- Volume requirements
- ATR filters

---

## 6. Architectural Position

OCL is a future structural Level Provider in the trading pipeline:

```
genuine 1m market data
    ↓
OCL candidate identification
    ↓
wick zone (the level)
    ↓
BDRR-style retest and rejection
    ↓
trade plan
    ↓
2R outcome evaluation
```

The frozen BDRR detector is not modified. OCL is studied independently
first. When the structure is understood, it may feed into the existing
BDRR retest/rejection machinery.

---

## 7. Relationship to Order Block Research

- All existing Order Block documents and files are preserved.
- Order Block research is paused — no OB code is being developed.
- OCL is the active Level Provider research track.
- Future OB work may resume separately if needed.
- The two concepts may overlap but are tracked independently.

---

## 8. Discovery-Before-Implementation Rule

No OCL detector code is written until manual discovery is complete.

The lifecycle is:

```
Manual discovery (label real examples)
    ↓
Pattern understanding (what makes a good OCL?)
    ↓
Parameter identification (what needs to be configurable?)
    ↓
Specification freeze
    ↓
Implementation
```

This matches the project's core principle: one interesting chart is not
enough. Evidence must accumulate before code is written.

---

## 9. Current Research Status

| Item                          | Status       |
|-------------------------------|--------------|
| OCL definition                | v0.1 frozen  |
| 1-minute data                 | Not yet downloaded — download script exists |
| Manual discovery workspace    | Not yet built |
| Discovery batch               | Not yet generated |
| Labeled examples              | None         |
| Parameter ranges              | Unresolved   |
| Detector implementation       | Blocked on discovery |
| Level Provider integration    | Blocked on detector |

---

## 10. Data Audit — 2026-08-02

### 10.1 Current 1-Minute Data

**No genuine 1-minute CSV files exist in the repository.**

The download script (`estrategie/scarica_dati_1m.py`) is ready and
targets all 9 symbols: SPY, QQQ, AMZN, TSLA, NVDA, META, MSFT,
GOOGL, MU. It uses Yahoo Finance, which retains only ~7–30 days of
1-minute data. Files would be saved to `dati/{SYMBOL}_1m.csv`.

### 10.2 Current 5-Minute Data

All 9 symbols have 5-minute data covering 60 sessions each
(2026-04-24 → 2026-07-21), 4,680 bars per symbol.
The 5m data is git-tracked and available in `dati/`.

### 10.3 Existing Timeframe Aggregation

A `timeframe_aggregation.py` module exists that can aggregate genuine
1-minute bars into 2m, 3m, 5m, and 10m candles. A `multi_timeframe_runner.py`
orchestrates ORB computation from 1m bars + post-ORB aggregation.

### 10.4 Data Sufficiency Assessment

**The first OCL discovery batch cannot be generated until Max runs
`scarica_dati_1m.py` on his local machine.** Yahoo Finance 1m data
is ephemeral — it must be downloaded promptly and committed.

Expected yield: ~7 trading sessions × 9 symbols = ~63 symbol-sessions
of 1-minute data (~390 bars per session). This is sufficient for a
first discovery batch of 30–45 candidates.

---

## 11. Recommended Discovery Workflow

### Stage A — Automatic Candidate Generation (permissive)

A deliberately broad mechanical filter scans 1-minute sessions to
propose possible OCL structures. This is not ground truth — it is a
search aid to save Max from scanning every bar manually.

Candidate skeleton rules:

1. N consecutive directional candles (same color) — minimum N to be
   determined, start permissive (e.g., N ≥ 3).
2. Exactly one opposite-color candle appears.
3. That candle has a wick pointing in the trend direction.
4. At least M directional candles follow — start permissive (e.g., M ≥ 2).

Each candidate is presented with surrounding context (30+ bars before
and after) so Max can judge momentum quality.

### Stage B — Manual Labeling by Max

For each proposed candidate, Max answers:

| Question                                        | Type     |
|-------------------------------------------------|----------|
| Is the prior move strong momentum?              | Yes / No / Borderline |
| Is this truly a valid One Candle?                | Yes / No |
| Is the wick the correct zone?                    | Yes / No / Adjust |
| Did momentum continue cleanly?                  | Yes / No / Weak |
| Did price move far enough away?                 | Yes / No / Unclear |
| Did a later retest occur?                        | Yes / No |
| Was the retest candle valid (BDRR rejection)?   | Yes / No / N/A |
| Would I take the trade?                          | Yes / No / Maybe |
| Quality                                          | A / B / C / D |
| Notes                                            | Free text |

The automatic proposal is never treated as ground truth.

### Why Two Stages

- Blind full-session charts would bury Max in 390 bars per session with
  no guidance — too slow for initial discovery.
- Fully automatic detection would teach Max the machine's answer instead
  of capturing his judgment.
- The two-stage approach finds candidates mechanically but lets Max
  define quality, creating unbiased training data.

---

## 12. Proposed First Batch Composition

### Prerequisites

Max must first run `scarica_dati_1m.py` to download 1-minute data.

### Batch Size

30–45 candidates total, balanced across:

| Dimension       | Target distribution                    |
|-----------------|----------------------------------------|
| Symbols         | 3–5 per symbol (no SPY/QQQ dominance) |
| Direction        | ~50% LONG, ~50% SHORT                 |
| Momentum quality | Mix of clear/weak/ambiguous            |
| Retest status    | Has retest / No retest / Failed retest |
| Quality range    | Include obvious and borderline cases   |

If fewer than 30 candidates are found mechanically across all symbols,
include all of them — the batch should not be artificially padded.

### Asset Balance

All 9 symbols are used. If some symbols yield zero candidates (e.g.,
low-volatility sessions), document this — it is a data point about
which instruments produce OCL structures.

---

## 13. Technical Dependencies and Blockers

| Dependency                     | Status    | Blocker? |
|--------------------------------|-----------|----------|
| 1-minute CSV data              | Missing   | **YES** — Max must download |
| `scarica_dati_1m.py` script    | Ready     | No       |
| `csv_parser.py`                | Ready     | Needs 1m format support check |
| `session_split.py`             | Ready     | Should work with 1m data |
| `ob_discovery_template.html`   | Adaptable | No — can serve as starting point |
| Lightweight Charts v4 (CDN)    | Available | No       |
| BDRR rejection logic           | Frozen    | No — reference only |
| Yahoo Finance 1m retention     | ~7–30 days | **TIME-SENSITIVE** |

### Critical Path

1. Max downloads 1m data → commits to repo
2. Verify `csv_parser.py` handles 1m CSV format (likely identical)
3. Build OCL discovery workspace (adapt OB discovery template)
4. Generate first candidate batch
5. Max labels candidates
6. Analyze labeled data → refine parameters

---

## 14. Workspace Adaptation Assessment

### Best candidate: Order Block Discovery Workspace

`backend/generate_ob_discovery.py` + `backend/ob_discovery_template.html`

This workspace already:
- Loads multi-symbol CSV data
- Splits into sessions
- Renders interactive candlestick charts (Lightweight Charts v4)
- Supports stratified sampling across symbols
- Has labeling UI with decision buttons and JSON export

Required adaptations for OCL:
- Change data source from 5m to 1m CSVs
- Add automatic candidate highlighting (Stage A filter)
- Replace OB labeling fields with OCL labeling fields (Table in §11)
- Add wick-zone visual overlay on candidate candles
- Zoom/context controls (1m charts are dense — 390 bars per session)

The existing BDRR review workspace (`review_workspace.py`) is too
tightly coupled to the BDRR pipeline stages to adapt efficiently.

The training workspace and audit workspace are also BDRR-specific.

**Recommendation:** Fork `generate_ob_discovery.py` into a new
`generate_ocl_discovery.py`, adapting the template for OCL-specific
labeling. Do not modify the OB files.
