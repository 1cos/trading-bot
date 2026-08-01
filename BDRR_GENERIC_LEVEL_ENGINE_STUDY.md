# BDRR Generic Level Engine — Architecture Study

> This document preserves the completed architectural analysis of whether the
> BDRR detector should evolve from an ORB-specific detector into a generic
> structural-level engine. It was produced through full codebase review in
> July 2026 and captures the findings as a permanent design reference.

---

## Decision

The long-term direction is a **generic BDRR engine operating on static
structural levels.**

However, implementation is deferred until individual level sources are
validated through real examples.

Priority order selected by Max:

1. **Order Block** created during strong momentum, studied across all
   available assets (SPY, QQQ, AMZN, TSLA, NVDA, META, MSFT, GOOGL, MU).
   MES and MNQ are priority future instruments to add when data becomes
   available, but Order Blocks are a generic price-action structure and
   the definition must not be modeled as instrument-specific.
2. **Previous Day High / Previous Day Low**
3. Other level sources only later

No Level Provider framework will be built until the first non-ORB level
source (Order Block) has been validated through manual labeling and
discretionary review across multiple instruments.

---

## 1. Current Architecture — ORB Coupling Analysis

### Where the detector receives the level

The level enters the pipeline at exactly one point: the return value of
`build_orb()` (or `_build_orb_from_override()` for multi-timeframe). This
function returns a dict containing `level_price`, `level_price_ticks`, and
several ORB-specific fields. The `strategy_runner._process_one_session()`
calls `build_orb()` and passes the resulting dict as the `orb` parameter to
every subsequent stage.

### Coupling layers

**Layer 1 — The level interface (what stages actually read).** Stages 2–5
(`find_break`, `find_displacement`, `find_retest_window`, `find_rejection`)
read exactly six fields from the `orb` dict: `status`, `level_price`,
`level_price_ticks`, `date`, `orb_candle_index`, and
`orb_candle["time_ms"]`. Of these, four are fully generic. Only two carry
ORB semantics: `orb_candle_index` and `orb_candle["time_ms"]`, but the
stages use these only as "the candle at which the level was established" and
"start scanning after this index."

**Layer 2 — The sequence validator (genuine ORB coupling).**
`validate_sequence()` is the only stage that reads `orb["orb_high"]` and
`orb["orb_low"]` — the full ORB band. This is the single point of genuine
architectural coupling.

**Layer 3 — Config validation (cosmetic coupling).** Every stage validates
that config contains `orb_start` and `orb_duration_minutes`. These fields
are never read or used by stages 2–5.

**Layer 4 — Level source whitelist (soft coupling).** Stages 3, 4, and 5
contain `_supported_sources = ("ORB_HIGH", "ORB_LOW")` and reject any other
level source.

### Modules that are completely generic already

| Module | ORB references | Functionally generic? |
|---|---|---|
| `find_break` | Parameter named `orb`; reads `level_price` | **Yes** |
| `find_displacement` | Parameter named `orb`; reads `level_price` | **Yes** |
| `find_retest_window` | Parameter named `orb`; reads `level_price` | **Yes** |
| `find_rejection` | Parameter named `orb`; reads `level_price` | **Yes** |
| `detection_result_builder` | Reads `level_price`, `level_source`, `orb_candle` | **Yes** |
| `trade_plan_builder` | Zero ORB references | **Yes** |
| `trade_outcome_evaluator` | Zero ORB references | **Yes** |

### Modules with genuine ORB dependency

| Module | Dependency |
|---|---|
| `orb_builder.py` | IS the ORB Level Provider |
| `sequence_validator.py` | Reads `orb_high` / `orb_low` for band check |
| `strategy_runner.py` | Imports `build_orb`, wires ORB config |
| `multi_timeframe_runner.py` | ORB-specific orchestration |

### Dependency diagram

```
                     ┌─────────────────────┐
                     │   strategy_runner    │  (orchestrator)
                     └──────────┬──────────┘
                                │
            ┌───────────────────┼───────────────────┐
            │                   │                   │
    ┌───────▼───────┐   ┌──────▼──────┐    ┌───────▼───────┐
    │  orb_builder   │   │  session_   │    │  _build_orb_  │
    │  (ORB-ONLY)    │   │  context    │    │  from_override│
    └───────┬───────┘   └─────────────┘    └───────┬───────┘
            └──────────────┬───────────────────────┘
                           │
                   level dict interface
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
  ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────────┐
  │ find_break  │  │ sequence_   │  │ find_            │
  │ (GENERIC)   │  │ validator   │  │ displacement     │
  └──────┬──────┘  │(reads orb_  │  │ (GENERIC)        │
         │         │ high/low)   │  └──────┬───────────┘
         │         └─────────────┘         │
         │                          ┌──────▼──────┐
         │                          │ find_retest │
         │                          │ _window     │
         │                          │ (GENERIC)   │
         │                          └──────┬──────┘
         │                          ┌──────▼──────┐
         │                          │ find_       │
         │                          │ rejection   │
         │                          │ (GENERIC)   │
         │                          └──────┬──────┘
         │                                 │
         └─────────────┬───────────────────┘
                       │
              ┌────────▼────────┐
              │ detection_      │
              │ result_builder  │
              │ (GENERIC)       │
              └────────┬────────┘
                       │
              ┌────────▼────────┐      ┌────────────────┐
              │ trade_plan_     │─────▶│ trade_outcome_  │
              │ builder         │      │ evaluator       │
              │ (GENERIC)       │      │ (GENERIC)       │
              └─────────────────┘      └─────────────────┘
```

---

## 2. Level Provider Concept

A Level Provider is a function or module that receives market data and
produces a standardized level dict:

```
{
    "status":             "OK" or "FAILED",
    "level_price":        float,
    "level_price_ticks":  int,
    "level_candle_index": int,
    "level_candle":       dict,
    "level_high":         float,
    "level_low":          float,
    "level_source":       str,
    "level_type":         str,
    "direction":          str,
    "date":               str,
    "metadata":           dict,
}
```

This separation is architecturally sound because:

1. Every stage 2–5 function already operates on this interface.
2. The `DetectionResult/v1` contract is already generic (`level_price`,
   `level_source`, `level_bar`).
3. The `_build_orb_from_override()` mechanism already demonstrates the
   pattern — a Level Provider in disguise.

---

## 3. Existing ORB Assumptions — Full Inventory

### Category A — Cosmetic (naming only)

1. Parameter name `orb` in stages 2–5
2. Error messages: "upstream ORB result failed"
3. Variable names in `detection_result_builder.py`
4. Comments and docstrings across stage files
5. Review workspace HTML labels ("ORB High", "ORB Low", "ORB Zone")
6. `audit_visual_exporter.py` field names (`orb_high_ticks`, etc.)
7. Section title "Opening Range" in `visual_review_html.py`

### Category B — Medium coupling (config or whitelist)

8. Config keys `orb_start` and `orb_duration_minutes` validated but unused
9. Level source whitelist `("ORB_HIGH", "ORB_LOW")` in stages 3, 4, 5
10. `LevelSource` enum (already contains `PDH`)
11. Preset construction in `strategy_runner.py`
12. `backtest_server.py` preset defaults

### Category C — Hard architectural coupling

13. `orb_builder.py` (the ORB provider itself)
14. `sequence_validator.py` reads `orb_high` / `orb_low`
15. `strategy_runner._build_orb_from_override()`
16. `strategy_runner._process_one_session()` imports `build_orb`
17. `DetectorAuditRecord` fields: `reached_orb`, `orb_high`, `orb_low`,
    `orb_candle_time_ms`
18. `audit_record_builder.py` interprets `level_bar` as ORB candle

---

## 4. Future Level Providers — Compatibility Assessment

| Provider | Static level? | Detector unchanged? | Zone for seq validator? |
|---|---|---|---|
| ORB | Yes | Yes (current) | Candle H/L |
| PDH | Yes | Yes | PDH/PDL range |
| PDL | Yes | Yes | PDH/PDL range |
| Premarket H/L | Yes | Yes | PM range |
| Order Block | Yes | Yes | OB candle H/L |
| Fair Value Gap | Yes | Yes | Gap boundaries |
| VWAP (snapshot) | Yes | Yes | Ambiguous |
| VWAP (dynamic) | No | **No** | N/A |
| Opening Range | Yes | Yes | Range H/L |

Every static level provider can use the detector unchanged. Only dynamic
levels would require architectural changes.

---

## 5. Long-Term Pipeline Architecture

```
Market Data + Prior Sessions
          │
          ▼
    Level Provider(s)
    (ORB, PDH, OB, FVG, ...)
          │
          │ produces: level dict
          │
          ▼
    BDRR Engine (frozen stages 2–5)
    (break → displacement → sequence → retest → rejection)
          │
          │ produces: DetectionResult/v1
          │
          ▼
    Policy Layer
          │
          ▼
    Trade Plan → Backtest / Paper / Live
```

This separation is preferable to embedding ORB logic inside the detector
because:

1. The BDRR sequence is level-agnostic by nature.
2. Separating providers enables combinatorial research.
3. Confluence becomes composable across providers.
4. The `_orb_override` mechanism proves the pattern works today.
5. Testing remains modular — new providers add tests, not modify them.

---

## 6. Risks

1. **Sequence validator zone semantics.** Each provider must define a
   meaningful structural zone. For some levels (VWAP), no natural zone
   exists.
2. **Multiple active levels.** Multiple providers on one session create
   correlated candidates.
3. **Provider inconsistency.** Providers must agree on the level dict
   contract — especially `level_candle_index` semantics.
4. **Level timing.** Intra-session levels (Order Blocks, FVGs) leave fewer
   candles for the BDRR sequence.
5. **Testing complexity.** Each provider adds its own test surface.
6. **Review fragmentation.** More level sources increase manual review
   burden.
7. **Premature generalization.** Only ORB has been validated. Building
   infrastructure before evidence risks wasted effort.

---

## 7. Recommendation

The project should evolve toward a generic BDRR engine. The codebase is
already 80% generic. The remaining 20% of ORB coupling concentrates in
five precise locations. The path is additive, not destructive.

However, following the Architecture Philosophy (observation → evidence →
implementation), the correct sequence is:

1. Validate Order Block levels through manual labeling across all
   available assets (SPY, QQQ, AMZN, TSLA, NVDA, META, MSFT, GOOGL, MU);
   add MES/MNQ when data becomes available
2. Validate PDH/PDL levels through manual labeling on SPY/QQQ
3. Only then build the generic Level Provider framework
4. Only then refactor the remaining ORB coupling points

No provider framework will be built before manual evidence confirms that
non-ORB levels produce BDRR sequences a discretionary trader recognizes
as valid setups.

---

## Document History

| Date | Event |
|---|---|
| 2026-07-31 | Architecture study completed via full codebase review |
| 2026-07-31 | Decision: generic direction confirmed, implementation deferred |
| 2026-07-31 | Priority order: Order Block → PDH/PDL → others |
| 2026-07-31 | Scope corrected: OB studied across all assets, not MES/MNQ only |
