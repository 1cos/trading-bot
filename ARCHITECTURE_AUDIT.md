# Architecture Audit — New Engineer Perspective

> **Date:** 2026-08-02
> **Question:** Could a new engineer understand the project from the
> documents alone?
> **Answer:** Partially. The vision is clear. The navigation is not.

---

## 1. Missing Links

| From | To | Gap |
|---|---|---|
| `ONE_CANDLE_LEVEL_SPEC.md` calls OCL a "future Level Provider" | `LEVEL_PROVIDER_SPEC.md` defines the contract | Neither document cross-references the other |
| `LEVEL_PROVIDER_SPEC.md` says Entry Engine consumes levels | `ENTRY_CANDLE_ENGINE_SPEC.md` defines the Entry Engine | No explicit reference between them |
| `ENTRY_CANDLE_ENGINE_SPEC.md` output feeds a Trade Candidate | `TRADE_CANDIDATE_SPEC.md` defines it | Entry Engine spec does not mention Trade Candidate by name |
| `TRADE_CANDIDATE_SPEC.md` lists five consumers (Policy Engine, Backtester, Review Workspace, Statistics, Risk Engine) | None of these exist | No placeholder specs, no forward references |
| Visual Language documents describe Max's judgment | `OCL_UNRESOLVED_PARAMETERS.md` lists parameters to resolve | No document connects "body dominance" (visual) to `min_rejection_body` (parameter) |
| `BDRR_GENERIC_LEVEL_ENGINE_STUDY.md` defines an old Level Provider contract | `LEVEL_PROVIDER_SPEC.md` defines a new one | No document declares which one is canonical |
| `BDRR_ARCHITECTURE_PHILOSOPHY.md` claims governance over all architecture | OCL has a different pipeline | No document clarifies OCL's relationship to BDRR governance |

---

## 2. Missing Definitions

| Concept | Referenced in | Never defined |
|---|---|---|
| Policy Engine | TRADE_CANDIDATE_SPEC | No document |
| Backtester | TRADE_CANDIDATE_SPEC | No document |
| Risk Engine | TRADE_CANDIDATE_SPEC | No document |
| Statistics module | TRADE_CANDIDATE_SPEC | No document |
| "strong momentum" | OCL_SPEC, MOMENTUM_VL, PATIENCE_VL, CONTINUATION_VL, CONFLUENCE_VL | Deliberately unresolved but referenced everywhere |
| Discovery Workspace | OCL_SPEC §11 | Described conceptually, not specified as an interface |
| Scorer / Quality Scorer | TRADING_JOURNAL (from memory), OCL_SPEC context | Referenced in project memory but absent from current docs |

---

## 3. Concepts Referenced Before Definition

| Concept | First reference | First definition | Gap |
|---|---|---|---|
| Level Provider | `BDRR_GENERIC_LEVEL_ENGINE_STUDY.md` (conceptual) | `LEVEL_PROVIDER_SPEC.md` (contract) | Study is older, spec is newer — reader encounters concept before contract |
| Trade Candidate | `ENTRY_CANDLE_ENGINE_SPEC.md` (implied output) | `TRADE_CANDIDATE_SPEC.md` | Entry Engine creates the data but does not name it |
| BDRR rejection | `ONE_CANDLE_LEVEL_SPEC.md` v0.1 | `BDRR_ARCHITECTURE_PHILOSOPHY.md` | OCL spec referenced BDRR rejection before OCL's own entry model existed |
| Session split | Multiple BDRR docs | `backend/README.md` / code only | Referenced in docs but defined only in code |

---

## 4. Documents That Could Be Merged

| Documents | Reason |
|---|---|
| `MOMENTUM_VISUAL_LANGUAGE.md` + `OCL_MOMENTUM_DISCOVERY.md` | The discovery doc's "Observed Recurring Characteristics" section is a subset of the visual language doc. The per-example analysis is unique to the discovery doc, but the conclusions overlap heavily. |
| `STRUCTURE_VISUAL_LANGUAGE.md` + `OCL_CONTEXT_CONFLUENCE_VISUAL_LANGUAGE.md` | Structure describes how Max sees important areas. Confluence describes what makes an OCL at those areas more interesting. The "multiple levels converge" and "prior reaction" concepts appear in both with different wording. |
| `SESSION_LOG_2026-07-28_b.md` + `SESSION_LOG_2026-07-31.md` | Session logs are temporal artifacts. They could be consolidated into a single session history or archived into a subdirectory. |

---

## 5. Documents That Should Remain Isolated

| Document | Reason |
|---|---|
| `LEVEL_PROVIDER_SPEC.md` | Universal contract — must not absorb strategy-specific content |
| `ENTRY_CANDLE_ENGINE_SPEC.md` | Level-agnostic interface — must not reference any specific provider |
| `TRADE_CANDIDATE_SPEC.md` | Provider-blind data object — must remain pure |
| `ONE_CANDLE_LEVEL_SPEC.md` | Strategy-specific definition — must not merge with generic specs |
| `OCL_SYNTHETIC_VALIDATION_EXAMPLES.md` | Synthetic validation record — distinct from real discovery data |
| `REJECTION_VISUAL_LANGUAGE.md` | Candle-level visual language — distinct scope from market-level docs |
| `PATIENCE_VISUAL_LANGUAGE.md` | Unique document about NOT trading — no overlap with action-oriented docs |
| `MAX_TRADING_DECISION_FLOW.md` | End-to-end mental workflow — the only document that covers the full sequence |

---

## 6. What a New Engineer Would Struggle With

### No reading order

There is no document that says "read these in this order." A new
engineer would find 24 markdown files in the root directory with no
hierarchy, no numbering, no index pointing to a starting point.
They would not know whether to start with `BDRR_ARCHITECTURE_PHILOSOPHY.md`
or `ONE_CANDLE_LEVEL_SPEC.md` or `LEVEL_PROVIDER_SPEC.md`.

### Two eras, one directory

The BDRR-era documents (architecture philosophy, canonical handoff,
generic level engine study, lab parameter roadmap, detector v2 handoff,
oracle audit) and the OCL-era documents (OCL spec, visual languages,
entry engine, level provider, trade candidate) sit side by side. A new
engineer cannot tell which era is current without reading everything.
The Order Block pause is documented in TRADING_JOURNAL but not visible
from file names.

### Spec vs visual language vs discovery vs log

Four types of documents exist but are not labeled or grouped: formal
specifications (Level Provider, Entry Engine, Trade Candidate, OCL),
visual language documents (6 files), discovery/analysis documents
(momentum discovery, synthetic examples, unresolved parameters,
contradiction audit, vocabulary index), and session logs (2 files).
They are all .md files in the root.

### The pipeline is spread across documents

To understand the full data flow (market data → Level Provider →
Entry Engine → Trade Candidate → downstream), you must read four
separate specs and piece together the connections yourself. No single
document shows the complete pipeline with cross-references.

### Active vs frozen vs paused is unclear

Some work is frozen (BDRR detector), some is paused (Order Blocks),
some is active (OCL discovery), and some is design-complete but not
implemented (Level Provider, Entry Engine, Trade Candidate). The
status is documented per-document but there is no project-level
status overview.

---

## 7. What Works Well

Despite the issues above, a new engineer would quickly understand:

- **The vision is coherent.** Level Provider → Entry Engine → Trade
  Candidate is a clean, well-separated pipeline.
- **The visual language documents are consistent.** They follow the
  same structure (what Max observes / what Max does not observe) and
  use the same natural language register.
- **The OCL strategy is precisely defined.** The spec, synthetic
  examples, and unresolved parameters together give a complete picture
  of what is known and what is not.
- **The discovery-before-implementation principle is clear.** Multiple
  documents reinforce that no code is written before evidence
  accumulates.
- **The separation between formation and trade quality is explicit.**
  This distinction is documented in the spec, the examples, and the
  unresolved parameters.
