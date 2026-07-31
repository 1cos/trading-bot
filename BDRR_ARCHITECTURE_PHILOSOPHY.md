# BDRR Architecture Philosophy

> This document defines the principles that guide every future architectural decision in the BDRR project.
> Whenever we are unsure whether to add a detector rule, a Lab parameter, or a strategy feature, this document should answer that question.
> This document has higher priority than implementation details.

---

## 1. Project Vision

The goal of this project is **not** to build a detector that predicts the future.

The goal is to build a research platform capable of learning, testing, and refining Max's discretionary ORB trading methodology.

The platform must separate objective market structure from subjective trading decisions.

---

## 2. Detector Is Not the Strategy

The detector identifies objectively observable market structures. It should not contain personal trading preferences whenever those preferences can be expressed as configurable policy.

The detector answers:

> "What happened?"

It does **not** answer:

> "Should I trade?"

---

## 3. Policy Is the Strategy

The strategy is a configurable policy layer. It decides which detector candidates are acceptable.

Examples of policy-level concerns:

- Maximum setups per day
- Earliest entry time
- Latest entry time
- Minimum displacement
- Body distance thresholds
- Confirmation delay
- SPY alignment requirements
- Order Block confluence

These belong to the Trading Lab whenever practical.

---

## 4. Manual Review Is the Ground Truth

The manual review process is the primary source of knowledge. Each reviewed setup captures Max's discretionary judgment.

Detector output is compared against human judgment — not the opposite.

---

## 5. Outcome Does Not Define Quality

A stopped trade can still be an excellent setup. A winning trade can still be rejected manually.

Therefore: **trade quality** and **trade profitability** are different concepts.

Never train the detector solely on profitable trades.

---

## 6. No Single Example Changes the Detector

One interesting chart is not sufficient evidence. Detector changes require repeated observations across many reviewed examples.

The progression is intentional:

1. Ideas first become **observations**
2. Observations become **hypotheses**
3. Hypotheses become **Lab parameters**
4. Only proven concepts become **detector rules**

---

## 7. Everything Testable Should Remain Testable

Whenever a discretionary preference can reasonably be expressed as a parameter, prefer a configurable Lab option over hardcoding it into the detector.

Research requires experimentation. Hardcoded assumptions reduce experimentation.

---

## 8. Frozen Layers

The following layers are intentionally stable. Changes require strong justification:

- Detection contracts
- Detector stages (Break → Displacement → Retest → Rejection)
- Workspace compatibility
- Existing review schemas

---

## 9. Evolvable Layers

The following parts are expected to evolve frequently:

- Trading Lab parameters
- Risk management rules
- Entry policies
- Exit policies
- Optimization experiments
- Future ML ranking

---

## 10. Detector Audit Before Detector Modification

Whenever detector behavior appears questionable, **do not immediately change the detector**.

First:

1. Review rejected candidates
2. Understand why they failed
3. Collect evidence

Only then consider structural changes.

---

## 11. Order of Decision Making

Every future feature should follow this sequence:

```
Observation
    ↓
Manual Review
    ↓
Discovery Journal
    ↓
Hypothesis
    ↓
Trading Lab Parameter
    ↓
Backtesting
    ↓
Performance Analysis
    ↓
Detector modification (only if justified)
```

This sequence is intentional. Skipping steps increases the risk of introducing bias.

---

## 12. Long-Term Goal

The final platform should allow:

- One detector
- Many configurable policies
- Many experiments
- Many strategy profiles

Without rewriting detector logic.

---

## 13. Project Motto

> *We do not optimize the detector to fit individual trades.*
> *We optimize the research process so the detector can evolve based on evidence.*
