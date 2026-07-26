/**
 * estrategie/bdrr_trade_outcome.js
 *
 * BDRR Chronological Trade Outcome — TradeOutcome/v1
 *
 * Exports one public function:
 *   evaluateTradeOutcome(detectionResult, tradePlan, postConfirmationBars, config)
 *
 * Implements the frozen chronological evaluation rule:
 *   BDRR_ENGINE_CANONICAL_HANDOFF.md §10 / §1335
 *
 *   For a LONG trade, chronological priority:
 *   1. If bar.low <= stop before any target is reached: STOPPED OUT.
 *      No subsequent target may be reported as achieved.
 *   2. If bar.high >= target before stop is reached: TARGET HIT.
 *   3. If bar.low <= stop AND bar.high >= target in the same bar
 *      and intrabar order is unavailable: AMBIGUOUS (not a win).
 *
 * config fields:
 *   direction       'LONG' (required)
 *   exit_target_r   2 | 3 | 4  (required)
 *     Determines the terminal winning target.
 *     2 → touching r2_price is TARGET_HIT.
 *     3 → touching r2_price is progress; r3_price is TARGET_HIT.
 *     4 → touching r2/r3_price is progress; r4_price is TARGET_HIT.
 *     Intermediate targets (below selected R) are tracked in
 *     highest_target_achieved but do not close the trade.
 *
 * Entry model:
 *   CONFIRMATION_CLOSE
 *     Entry is guaranteed at the confirmation bar close price. The entry
 *     timestamp is the confirmation bar timestamp (detectionResult.
 *     confirmation_bar.bar_utc_ms), not the first post-confirmation bar.
 *     Stop/target evaluation begins on postConfirmationBars[0].
 *     first_eval_bar_index / first_eval_bar_utc_ms record the first
 *     post-confirmation bar (the first bar subject to evaluation).
 *
 *   BREAK_OF_SIGNAL_BAR
 *     Entry triggers when a post-confirmation bar's high >= entry_price.ticks.
 *     The triggering bar is the entry bar.
 *
 * Output fields (TradeOutcome/v1):
 *   schema_version              'TradeOutcome/v1'
 *   direction                   'LONG'
 *   entry_model                 from tradePlan
 *   entry_price_ticks           from tradePlan
 *   stop_price_ticks            from tradePlan
 *   tick_size                   from tradePlan
 *   selected_exit_target_r      config.exit_target_r (2 | 3 | 4)
 *   selected_exit_target_label  '2R' | '3R' | '4R'
 *   entry_triggered             boolean
 *   entry_bar_utc_ms            CC: confirmation_bar.bar_utc_ms
 *                               BOSB: triggering bar bar_utc_ms | null
 *   first_eval_bar_index        index of first bar in postConfirmationBars
 *                               subject to evaluation (0 for CC when bars
 *                               present, null for CC with empty bars)
 *   first_eval_bar_utc_ms       bar_utc_ms of first_eval_bar | null
 *   outcome                     'TARGET_HIT' | 'STOPPED' | 'AMBIGUOUS' |
 *                               'OPEN' | 'ENTRY_NOT_TRIGGERED'
 *   exit_bar_index              index in postConfirmationBars | null
 *   exit_bar_utc_ms             bar_utc_ms of exit bar | null
 *   exit_price_ticks            stop_price_ticks (STOPPED) |
 *                               terminal target ticks (TARGET_HIT) | null
 *   exit_target_label           terminal target label (TARGET_HIT) | null
 *   exit_target_r               terminal target R (TARGET_HIT) | null
 *   highest_target_achieved     label of highest intermediate target reached
 *                               (regardless of whether trade was stopped) | null
 *   highest_target_r            R of highest_target_achieved | null
 *   realized_r                  -1 (STOPPED) | selected exit_target_r (TARGET_HIT)
 *                               | null (OPEN, AMBIGUOUS, ENTRY_NOT_TRIGGERED)
 *   r2_price_ticks              reference
 *   r3_price_ticks              reference
 *   r4_price_ticks              reference
 *
 * Immutability: output is frozen. Inputs are never mutated.
 * Tick arithmetic: all comparisons use integer ticks — no floating-point prices.
 * No MFE/MAE, partial exits, slippage, commissions, trailing stops.
 *
 * Run tests: node estrategie/test_bdrr_trade_outcome.js
 */

'use strict';

// ── Helpers ───────────────────────────────────────────────────────────────────

function fail(code, reason) {
  return { status: 'FAILED', failure_code: code, reason };
}

function isPositiveFiniteNumber(v) {
  return typeof v === 'number' && isFinite(v) && v > 0;
}

// ── Input validation ──────────────────────────────────────────────────────────

function validateDetectionResult(dr) {
  if (!dr || typeof dr !== 'object') {
    return fail('INVALID_DETECTION_RESULT', 'detectionResult must be a non-null object');
  }
  if (dr.schema_version !== 'DetectionResult/v1') {
    return fail(
      'INVALID_DETECTION_RESULT',
      `detectionResult.schema_version must be "DetectionResult/v1"; got "${dr.schema_version}"`
    );
  }
  if (dr.status !== 'VALID') {
    return fail(
      'INVALID_DETECTION_RESULT',
      `detectionResult.status must be "VALID"; got "${dr.status}"`
    );
  }
  return null;
}

function validateTradePlan(tp) {
  if (!tp || typeof tp !== 'object') {
    return fail('INVALID_TRADE_PLAN', 'tradePlan must be a non-null object');
  }
  if (tp.schema_version !== 'TradePlan/v1') {
    return fail(
      'INVALID_TRADE_PLAN',
      `tradePlan.schema_version must be "TradePlan/v1"; got "${tp.schema_version}"`
    );
  }
  for (const field of ['entry_price', 'stop_price', 'risk', 'r2_price', 'r3_price', 'r4_price']) {
    const pt = tp[field];
    if (!pt || typeof pt !== 'object' || !Number.isInteger(pt.ticks) ||
        !isPositiveFiniteNumber(pt.tick_size)) {
      return fail(
        'INVALID_TRADE_PLAN',
        `tradePlan.${field} must be a valid PriceTicks object`
      );
    }
  }
  if (!isPositiveFiniteNumber(tp.tick_size)) {
    return fail('INVALID_TRADE_PLAN', 'tradePlan.tick_size must be a finite positive number');
  }
  if (tp.risk.ticks <= 0) {
    return fail('INVALID_TRADE_PLAN', 'tradePlan.risk.ticks must be positive');
  }
  return null;
}

const VALID_EXIT_TARGET_R = new Set([2, 3, 4]);

function validateConfig(config) {
  if (!config || typeof config !== 'object') {
    return fail('INVALID_CONFIG', 'config must be a non-null object');
  }
  if (config.direction !== 'LONG') {
    return fail(
      'UNSUPPORTED_DIRECTION',
      `direction "${config.direction}" is not supported; only "LONG" is implemented`
    );
  }
  if (!VALID_EXIT_TARGET_R.has(config.exit_target_r)) {
    return fail(
      'INVALID_CONFIG',
      `config.exit_target_r must be 2, 3, or 4; got ${JSON.stringify(config.exit_target_r)}`
    );
  }
  return null;
}

function validateBars(bars, tickSize) {
  if (!Array.isArray(bars)) {
    return fail('INVALID_BARS', 'postConfirmationBars must be an array');
  }
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    if (!b || typeof b !== 'object') {
      return fail('INVALID_BARS', `bar[${i}] must be a non-null object`);
    }
    for (const field of ['open', 'high', 'low', 'close']) {
      const pt = b[field];
      if (!pt || typeof pt !== 'object' || !Number.isInteger(pt.ticks) ||
          !isPositiveFiniteNumber(pt.tick_size)) {
        return fail('INVALID_BARS', `bar[${i}].${field} must be a valid PriceTicks object`);
      }
      if (pt.tick_size !== tickSize) {
        return fail(
          'TICK_SIZE_MISMATCH',
          `bar[${i}].${field}.tick_size (${pt.tick_size}) does not match ` +
          `tradePlan.tick_size (${tickSize})`
        );
      }
    }
    if (typeof b.bar_utc_ms !== 'number' || !isFinite(b.bar_utc_ms)) {
      return fail('INVALID_BARS', `bar[${i}].bar_utc_ms must be a finite number`);
    }
    if (i > 0 && b.bar_utc_ms <= bars[i - 1].bar_utc_ms) {
      return fail(
        'BARS_NOT_CHRONOLOGICAL',
        `bar[${i}].bar_utc_ms (${b.bar_utc_ms}) must be strictly after ` +
        `bar[${i - 1}].bar_utc_ms (${bars[i - 1].bar_utc_ms})`
      );
    }
    if (b.high.ticks < b.low.ticks) {
      return fail(
        'INVALID_BARS',
        `bar[${i}].high.ticks (${b.high.ticks}) must be >= low.ticks (${b.low.ticks})`
      );
    }
  }
  return null;
}

// ── Primary export ────────────────────────────────────────────────────────────

/**
 * evaluateTradeOutcome(detectionResult, tradePlan, postConfirmationBars, config)
 *
 * Deterministic chronological LONG trade evaluation.
 *
 * @param {object}   detectionResult      canonical DetectionResult/v1
 * @param {object}   tradePlan            canonical TradePlan/v1
 * @param {object[]} postConfirmationBars canonical Bar[] strictly after confirmation bar
 * @param {object}   config               { direction: 'LONG', exit_target_r: 2|3|4 }
 */
function evaluateTradeOutcome(detectionResult, tradePlan, postConfirmationBars, config) {

  // ── Step 1: validate inputs ───────────────────────────────────────────────

  const drErr = validateDetectionResult(detectionResult);
  if (drErr) return drErr;

  const tpErr = validateTradePlan(tradePlan);
  if (tpErr) return tpErr;

  const cfgErr = validateConfig(config);
  if (cfgErr) return cfgErr;

  const tickSize = tradePlan.tick_size;

  const barsErr = validateBars(postConfirmationBars, tickSize);
  if (barsErr) return barsErr;

  // ── Step 2: extract trade plan values ─────────────────────────────────────

  const entryTicks = tradePlan.entry_price.ticks;
  const stopTicks  = tradePlan.stop_price.ticks;
  const r2Ticks    = tradePlan.r2_price.ticks;
  const r3Ticks    = tradePlan.r3_price.ticks;
  const r4Ticks    = tradePlan.r4_price.ticks;
  const entryModel = tradePlan.entry_model;

  // All possible milestones in ascending order (LONG).
  // The terminal target is determined by config.exit_target_r.
  const ALL_TARGETS = [
    { ticks: r2Ticks, label: '2R', r: 2 },
    { ticks: r3Ticks, label: '3R', r: 3 },
    { ticks: r4Ticks, label: '4R', r: 4 }
  ];

  // targets = milestones up to and including the selected terminal R.
  // e.g. exit_target_r=2 → only [2R]; exit_target_r=3 → [2R, 3R]
  const selectedR     = config.exit_target_r;
  const selectedLabel = selectedR + 'R';
  const terminalIdx   = ALL_TARGETS.findIndex(t => t.r === selectedR); // 0, 1, or 2
  const targets       = ALL_TARGETS.slice(0, terminalIdx + 1);

  // ── Step 3: entry timestamp (Correction 2) ────────────────────────────────
  //
  // CONFIRMATION_CLOSE:
  //   The entry occurred at the close of the confirmation bar. The canonical
  //   entry timestamp is confirmation_bar.bar_utc_ms — NOT the first
  //   post-confirmation bar. The first post-confirmation bar is the first bar
  //   evaluated for stop/target; it is tracked separately as first_eval_bar_*.
  //
  // BREAK_OF_SIGNAL_BAR:
  //   Entry triggers on the first post-confirmation bar whose high >= entry.
  //   The triggering bar IS the entry bar.

  const isCCModel = (entryModel === 'CONFIRMATION_CLOSE');

  // CC: entry is guaranteed; timestamp comes from confirmation_bar.
  // BOSB: not yet triggered at scan start.
  let entryTriggered = isCCModel;
  let entryBarUtcMs  = isCCModel
    ? (detectionResult.confirmation_bar
        ? detectionResult.confirmation_bar.bar_utc_ms
        : null)
    : null;

  // first_eval_bar: the first postConfirmationBars element examined.
  // For CC this is always bar[0] (if bars are non-empty); for BOSB it is the
  // first bar that triggers entry (same as entry bar).
  let firstEvalBarIndex  = null;
  let firstEvalBarUtcMs  = null;

  // Track the index within postConfirmationBars of the BOSB entry bar, for
  // output (we reuse this field for both models with different semantics).
  // CC: no "entry bar index" within postConfirmationBars — entry happened at
  // the confirmation bar close before this array begins.
  let bosbEntryBarIndex  = null; // only set for BREAK_OF_SIGNAL_BAR

  // ── Step 4: scan bars chronologically ─────────────────────────────────────

  // Highest milestone achieved so far (index into targets[]).
  // -1 means none yet.
  let highestTargetIdx = -1;

  let outcomeType    = null;
  let exitBarIndex   = null;
  let exitBarUtcMs   = null;
  let exitPriceTicks = null;
  let exitTargetLabel = null;
  let exitTargetR     = null;

  for (let i = 0; i < postConfirmationBars.length; i++) {
    const bar     = postConfirmationBars[i];
    const hiTicks = bar.high.ticks;
    const loTicks = bar.low.ticks;

    // Record first_eval_bar on first iteration.
    if (i === 0) {
      firstEvalBarIndex = 0;
      firstEvalBarUtcMs = bar.bar_utc_ms;
    }

    // ── BOSB Phase A: wait for entry trigger ─────────────────────────────
    if (!entryTriggered) {
      // BOSB: entry triggers when this bar's high reaches entry price.
      if (hiTicks >= entryTicks) {
        entryTriggered    = true;
        entryBarUtcMs     = bar.bar_utc_ms;
        bosbEntryBarIndex = i;
        firstEvalBarIndex = i;  // for BOSB, first eval = entry bar
        firstEvalBarUtcMs = bar.bar_utc_ms;

        // Evaluate same-bar ambiguity on the entry bar.
        // FROZEN rule (2026-07-25): AMBIGUOUS only when stop + SELECTED TERMINAL
        // target occur on the same bar. An intermediate milestone does not create
        // ambiguity; if the bar hits stop + intermediate but not terminal → STOPPED.
        const stopHit   = loTicks <= stopTicks;
        const termHit   = hiTicks >= targets[terminalIdx].ticks;

        if (stopHit && termHit) {
          outcomeType    = 'AMBIGUOUS';
          exitBarIndex   = i;
          exitBarUtcMs   = bar.bar_utc_ms;
          exitPriceTicks = null;
          break;
        }
        if (stopHit) {
          // Intermediate milestone may be in bar range but intrabar order is
          // unavailable — do NOT credit it on the stop bar.
          outcomeType    = 'STOPPED';
          exitBarIndex   = i;
          exitBarUtcMs   = bar.bar_utc_ms;
          exitPriceTicks = stopTicks;
          break;
        }
        // Advance through reachable milestones on entry bar.
        for (let t = 0; t < targets.length; t++) {
          if (hiTicks >= targets[t].ticks) highestTargetIdx = t;
          else break;
        }
        if (highestTargetIdx === terminalIdx) {
          outcomeType     = 'TARGET_HIT';
          exitBarIndex    = i;
          exitBarUtcMs    = bar.bar_utc_ms;
          exitTargetLabel = targets[highestTargetIdx].label;
          exitTargetR     = targets[highestTargetIdx].r;
          exitPriceTicks  = targets[highestTargetIdx].ticks;
          break;
        }
        // Entry bar processed; continue to next bar.
      }
      // If entry not triggered, skip stop/target evaluation.
      continue;
    }

    // ── Phase B: entry active — evaluate stop and targets ────────────────
    //
    // FROZEN rules:
    //   rule 1: low <= stop → STOPPED (no milestone credited on stop bar)
    //   rule 2: high >= milestone → advance / TARGET_HIT
    //   same-bar (frozen 2026-07-25): AMBIGUOUS only when stop + SELECTED
    //     TERMINAL target on same bar. Intermediate milestone + stop → STOPPED;
    //     the intermediate is not credited on that stop bar.

    const nextIdx     = highestTargetIdx + 1;
    const stopHit     = loTicks <= stopTicks;
    const terminalHit = hiTicks >= targets[terminalIdx].ticks;

    if (stopHit && terminalHit) {
      // Same-bar ambiguity: stop and selected terminal target on same bar.
      outcomeType    = 'AMBIGUOUS';
      exitBarIndex   = i;
      exitBarUtcMs   = bar.bar_utc_ms;
      exitPriceTicks = null;
      break;
    }

    if (stopHit) {
      // Stop reached. Do NOT credit any intermediate milestone in this bar's
      // range — intrabar order is unavailable on a stop bar.
      outcomeType    = 'STOPPED';
      exitBarIndex   = i;
      exitBarUtcMs   = bar.bar_utc_ms;
      exitPriceTicks = stopTicks;
      break;
    }

    // No stop — check whether any milestone is reached.
    if (nextIdx < targets.length && hiTicks >= targets[nextIdx].ticks) {
      // Advance through all milestones reachable on this bar (no stop conflict).
      for (let t = nextIdx; t < targets.length; t++) {
        if (hiTicks >= targets[t].ticks) highestTargetIdx = t;
        else break;
      }
      if (highestTargetIdx === terminalIdx) {
        outcomeType     = 'TARGET_HIT';
        exitBarIndex    = i;
        exitBarUtcMs    = bar.bar_utc_ms;
        exitTargetLabel = targets[highestTargetIdx].label;
        exitTargetR     = targets[highestTargetIdx].r;
        exitPriceTicks  = targets[highestTargetIdx].ticks;
        break;
      }
      // Intermediate milestone reached cleanly — trade stays open.
    }
  }

  // ── Step 5: resolve session-end outcome ──────────────────────────────────

  if (!entryTriggered) {
    outcomeType = 'ENTRY_NOT_TRIGGERED';
  } else if (outcomeType === null) {
    outcomeType = 'OPEN';
  }

  // ── Step 6: realized_r ────────────────────────────────────────────────────

  let realizedR = null;
  if (outcomeType === 'STOPPED')     realizedR = -1;
  if (outcomeType === 'TARGET_HIT')  realizedR = selectedR;
  // OPEN, AMBIGUOUS, ENTRY_NOT_TRIGGERED → null

  // ── Step 7: assemble frozen output ───────────────────────────────────────

  const outcome = Object.freeze({
    schema_version:             'TradeOutcome/v1',
    direction:                  'LONG',
    entry_model:                entryModel,
    entry_price_ticks:          entryTicks,
    stop_price_ticks:           stopTicks,
    tick_size:                  tickSize,

    // Selected target configuration
    selected_exit_target_r:     selectedR,
    selected_exit_target_label: selectedLabel,

    // Entry
    entry_triggered:            entryTriggered,
    // CC: confirmation_bar.bar_utc_ms  |  BOSB: triggering bar utc_ms | null
    entry_bar_utc_ms:           entryBarUtcMs,
    // CC: no index within postConfirmationBars (entry was at confirmation bar)
    // BOSB: index within postConfirmationBars where entry triggered | null
    bosb_entry_bar_index:       bosbEntryBarIndex,

    // First post-confirmation bar evaluated for stop/target
    first_eval_bar_index:       firstEvalBarIndex,
    first_eval_bar_utc_ms:      firstEvalBarUtcMs,

    // Outcome
    outcome:                    outcomeType,
    exit_bar_index:             exitBarIndex,
    exit_bar_utc_ms:            exitBarUtcMs,
    exit_price_ticks:           exitPriceTicks,
    exit_target_label:          exitTargetLabel,
    exit_target_r:              exitTargetR,

    // Progress tracking
    highest_target_achieved:    highestTargetIdx >= 0 ? targets[highestTargetIdx].label : null,
    highest_target_r:           highestTargetIdx >= 0 ? targets[highestTargetIdx].r     : null,

    // Realized P&L in R multiples
    realized_r:                 realizedR,

    // Reference ticks
    r2_price_ticks:             r2Ticks,
    r3_price_ticks:             r3Ticks,
    r4_price_ticks:             r4Ticks
  });

  return { status: 'OK', outcome };
}

module.exports = { evaluateTradeOutcome };
