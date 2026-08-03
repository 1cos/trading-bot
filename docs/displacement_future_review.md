# Displacement — Future Review

> **Status: FROZEN.** The current rule (`min_displacement_bars ≥ 3`) is an intentional temporary heuristic. No further displacement optimization should be done until a much larger labeled dataset has been reviewed by the user.

## Current rule

LONG: at least 3 consecutive candles with `low > ORB High` after the break.
SHORT: at least 3 consecutive candles with `high < ORB Low` after the break.

This is a bar-count filter only. It does not measure quality, progression, or distance from the level.

## Why 3 bars is only temporary

The rule eliminates the most obvious false positives (single-bar expansions followed by immediate re-entry) but does not distinguish between:

- 3 bars sitting flat just above the level (compression/wall pattern — likely false positive)
- 3 bars progressing away from the level (real displacement — valid setup)

Both pass the current filter. A more nuanced rule is needed, but it requires a larger labeled dataset to calibrate without overfitting.

## Examples

### QQQ 2026-05-06 — False positive (eliminated by 3-bar rule)

- ORB High: 689.16
- Break at 09:40, close at 689.97 (+81 ticks)
- Displacement: **1 bar only** (09:45), low at 689.37 (+21 ticks above level)
- Immediate re-entry: bar at 09:50 drops to 688.46 (−70 ticks below level)
- That same bar (09:50) becomes the confirmation candle

The user's visual assessment: this is not displacement, it's a single wick above the level followed by immediate reversal. The price never built real space above the ORB.

### QQQ 2026-05-13 — Canonical valid setup (passes 3-bar rule)

- ORB High: 709.95
- Break at 10:50, close at 710.54 (+59 ticks)
- Displacement: **6 consecutive bars** (10:55–11:20), all with low above ORB High
- Minimum distance from level: +30 ticks (bar 20, 11:10)
- Maximum distance: +158 ticks (bar 18, 11:00)
- Retest contact at 11:25, low at 709.85 (−10 ticks)
- Clean rejection candle with wick ratio 0.82

The user's visual assessment: the price breaks out, moves away from the level with conviction across multiple bars, builds real space, and only then returns for the retest.

## What the user wants

> "The price must move away from the ORB, build real space, then come back."

Key distinction:

- **Not valid:** price sits just above the level, forming compression or a wall. Even if technically "outside," it never leaves the area.
- **Valid:** price separates from the level, the gap becomes visible, and the subsequent return to the level is a genuine retest of a zone that was clearly left behind.

## Candidate metrics for future review

These were analyzed during the investigation but intentionally NOT implemented:

### 1. Cumulative area

`sum(close_distance_from_level)` across displacement bars. Measures total volume of separation (duration × distance). Strongest single discriminator in the two examples (50 vs 591 ticks). Needs normalization for timeframe or risk.

### 2. Progression / higher closes

Whether each displacement bar's close is further from the level than the previous one. Captures directional movement vs flat consolidation. Sensitive to noise — a single bar dipping slightly breaks the sequence.

### 3. Maximum reentry after displacement

How far the price comes back toward the level in the 1–2 bars immediately after the displacement window ends. The 05-06 example re-entered −154 ticks; the 05-13 example only −10 ticks. Strong discriminator but boundary between displacement and retest is ambiguous.

### 4. Expansion/risk ratio

`max_high_distance / risk_ticks`. Normalizes for instrument and volatility. The 05-06 example had 1.56; the 05-13 had 2.68. Useful as a threshold but doesn't capture the duration component.

### 5. Expansion/average bar range

`max_high_distance / avg_recent_bar_range`. The 05-06 example had 0.68 (expansion smaller than a typical bar); the 05-13 had 1.32 (expansion larger). Captures whether the displacement is "visible" on the chart.

### 6. Persistence

How many bars remain at more than 50% of the maximum distance from the level. Measures whether the price stays away or just spikes and returns.

### 7. Compression detection

Identify when multiple bars cluster near the level with small ranges — the "wall just above the ORB" pattern. This is qualitatively different from expansion and would need its own classifier.

## What NOT to do

- Do not implement any of these metrics without a larger labeled dataset
- Do not combine multiple metrics into a composite score without validation
- Do not tune thresholds on only two examples
- Do not optimize for QQQ-specific behavior
- Do not add displacement quality scoring that can't be explained simply

## When to revisit

Revisit displacement optimization when:

1. The user has visually reviewed at least 50+ detected setups across multiple symbols
2. At least 10 setups have been classified as "false positive due to weak displacement"
3. A clear pattern emerges that a specific metric consistently separates valid from invalid
4. The Strategy Tester UI supports accept/reject labeling for individual setups

This document is a parking lot for future work. The 3-bar rule is good enough to proceed with building the rest of the system.
