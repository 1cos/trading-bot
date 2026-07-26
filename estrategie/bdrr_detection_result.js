/**
 * estrategie/bdrr_detection_result.js
 *
 * Schema adapter — converts frozen Stage 1–5 runtime detection output into a
 * canonical DetectionResult/v1 object as specified in
 * BDRR_ENGINE_CANONICAL_HANDOFF.md §3.3.
 *
 * Exports one public function:
 *   buildDetectionResult(stageOutputs, metadata)
 *
 * Motivation
 * ──────────
 * The Stage 1–5 engine (bdrr_engine.js) uses runtime-internal status values
 * ('OK' | 'FAILED') and plain numeric prices for its inter-stage plumbing.
 * The canonical DetectionResult/v1 contract requires:
 *   - status: 'VALID' | 'INVALID'
 *   - schema_version: 'DetectionResult/v1'
 *   - result_id: UUID v4
 *   - produced_at: ISO 8601 UTC string
 *   - level_price: PriceTicks (not a plain float)
 *   - all other fields in canonical typed form
 *
 * This adapter bridges that gap without touching any Stage 1–5 function.
 *
 * Status mapping
 * ──────────────
 *   Runtime 'OK'     → canonical 'VALID'
 *   Runtime 'FAILED' → canonical 'INVALID'
 *
 * Input
 * ─────
 * stageOutputs — plain object with these optional keys:
 *   orb          result of buildORB()
 *   breakResult  result of findBreak()
 *   dispResult   result of findDisplacement()
 *   retestResult result of findRetestWindow()
 *   rejResult    result of findRejection()    ← primary source of truth
 *
 * The adapter derives as much as it can from each available stage result and
 * null-fills canonical fields whose source data was not collected by the
 * current engine (all such fields carry '| null' in the schema).
 * The two array fields (displacement_window, retest_window) default to [].
 *
 * metadata — required for fields that are not present in any stage output:
 *   tick_size         number  instrument minimum price increment
 *   session           object  SessionMetadata fields:
 *     symbol          string
 *     date            string  'YYYY-MM-DD'
 *     market_timezone string
 *     session_open_utc_ms   number (int64 ms)
 *     session_close_utc_ms  number (int64 ms)
 *     timeframe_seconds     number
 *   preset_id         string
 *   engine_version    string
 *
 * Immutability
 * ────────────
 * The produced object is frozen with Object.freeze() (shallow), consistent
 * with the repository convention used in bdrr_trade_plan.js.  Neither
 * stageOutputs nor metadata is modified.
 *
 * Failure convention
 * ──────────────────
 * Validation failures return { status: 'FAILED', failure_code, reason }.
 * This mirrors the exact shape used by bdrr_trade_plan.js.
 * The function never throws for normal validation failures.
 *
 * Run tests: node estrategie/test_bdrr_detection_result.js
 */

'use strict';

// ── UUID v4 (self-contained; no external dependencies) ────────────────────────

function generateUUIDv4() {
  // RFC 4122 §4.4 — uses crypto.getRandomValues when available (Node ≥ 15),
  // falls back to Math.random for environments that lack it.
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Manual construction: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function isUUIDv4(s) {
  return typeof s === 'string' &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(s);
}

// ── Tick arithmetic ───────────────────────────────────────────────────────────

function priceToTicks(price, tickSize) {
  return Math.round(price / tickSize);
}

function decimalsOf(tickSize) {
  const s = String(tickSize);
  const i = s.indexOf('.');
  return i === -1 ? 0 : s.length - i - 1;
}

function ticksToPointsStr(ticks, tickSize) {
  // Returns a Decimal-compatible string (exact decimal).
  const decimals = decimalsOf(tickSize);
  return (ticks * tickSize).toFixed(Math.max(decimals, 2));
}

// ── Canonical type constructors ───────────────────────────────────────────────

function priceTicks(ticks, tickSize) {
  return Object.freeze({ ticks, tick_size: tickSize });
}

function absoluteTickDistance(ticks, tickSize) {
  // ticks must be >= 0
  return Object.freeze({ ticks, tick_size: tickSize });
}

function directionalTickDistance(ticks, tickSize) {
  // ticks is signed
  return Object.freeze({ ticks, tick_size: tickSize });
}

function rational(numerator, denominator) {
  return Object.freeze({ numerator, denominator });
}

// ── Failure convention (mirrors bdrr_trade_plan.js exactly) ──────────────────

function fail(code, reason) {
  return { status: 'FAILED', failure_code: code, reason };
}

// ── Metadata validation ───────────────────────────────────────────────────────

function validateMetadata(metadata) {
  if (!metadata || typeof metadata !== 'object') {
    return fail('INVALID_METADATA', 'metadata must be a non-null object');
  }

  if (typeof metadata.tick_size !== 'number' || !isFinite(metadata.tick_size) || metadata.tick_size <= 0) {
    return fail('INVALID_METADATA', 'metadata.tick_size must be a finite positive number');
  }

  if (typeof metadata.preset_id !== 'string' || metadata.preset_id.length === 0) {
    return fail('INVALID_METADATA', 'metadata.preset_id must be a non-empty string');
  }

  if (typeof metadata.engine_version !== 'string' || metadata.engine_version.length === 0) {
    return fail('INVALID_METADATA', 'metadata.engine_version must be a non-empty string');
  }

  const s = metadata.session;
  if (!s || typeof s !== 'object') {
    return fail('INVALID_METADATA', 'metadata.session must be a non-null object');
  }

  const sessionFields = [
    ['symbol',               v => typeof v === 'string' && v.length > 0],
    ['date',                 v => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)],
    ['market_timezone',      v => typeof v === 'string' && v.length > 0],
    ['session_open_utc_ms',  v => typeof v === 'number' && Number.isFinite(v)],
    ['session_close_utc_ms', v => typeof v === 'number' && Number.isFinite(v)],
    ['timeframe_seconds',    v => typeof v === 'number' && Number.isInteger(v) && v > 0],
  ];

  for (const [field, test] of sessionFields) {
    if (!test(s[field])) {
      return fail('INVALID_METADATA', `metadata.session.${field} is missing or invalid`);
    }
  }

  return null; // no error
}

// ── stageOutputs validation ───────────────────────────────────────────────────

function validateStageOutputs(so) {
  if (!so || typeof so !== 'object') {
    return fail('INVALID_STAGE_OUTPUTS', 'stageOutputs must be a non-null object');
  }
  // rejResult is the required minimum (it carries the final detection verdict)
  if (!so.rejResult || typeof so.rejResult !== 'object') {
    return fail('INVALID_STAGE_OUTPUTS',
      'stageOutputs.rejResult is required (output of findRejection())');
  }
  const r = so.rejResult;
  if (r.status !== 'OK' && r.status !== 'FAILED') {
    return fail('INVALID_STAGE_OUTPUTS',
      `stageOutputs.rejResult.status must be 'OK' or 'FAILED'; got '${r.status}'`);
  }
  return null;
}

// ── Field builders ────────────────────────────────────────────────────────────

/**
 * Build a canonical Bar from a plain runtime candle.
 * The engine candle shape: { time: Date, open, high, low, close } — all numbers.
 * The canonical Bar shape: { bar_utc_ms, open, high, low, close, volume } — all PriceTicks.
 */
function buildBar(candle, tickSize) {
  if (!candle || typeof candle !== 'object') return null;
  const bar_utc_ms = candle.time instanceof Date
    ? candle.time.getTime()
    : (typeof candle.time === 'number' ? candle.time : null);
  return Object.freeze({
    bar_utc_ms,
    open:   priceTicks(priceToTicks(candle.open,  tickSize), tickSize),
    high:   priceTicks(priceToTicks(candle.high,  tickSize), tickSize),
    low:    priceTicks(priceToTicks(candle.low,   tickSize), tickSize),
    close:  priceTicks(priceToTicks(candle.close, tickSize), tickSize),
    volume: candle.volume != null ? candle.volume : null
  });
}

/**
 * Build canonical Rational from ratio fields already computed by the engine.
 * The engine stores ratios as plain floats (e.g. rejectionWickRatio).
 * The canonical Rational requires { numerator, denominator }.
 * We express the float as numerator/100000 (sufficient precision for ratios).
 */
function floatToRational(value) {
  if (value == null || !isFinite(value)) return null;
  // Round to 6 significant figures then express as integer/1000000
  const denom = 1000000;
  const num = Math.round(value * denom);
  return rational(num, denom);
}

// ── Primary export ────────────────────────────────────────────────────────────

/**
 * buildDetectionResult(stageOutputs, metadata)
 *
 * Converts Stage 1–5 runtime outputs into a canonical DetectionResult/v1 object.
 *
 * @param {object} stageOutputs  Plain object with keys:
 *   orb, breakResult, dispResult, retestResult, rejResult
 *   All are optional except rejResult, which carries the final detection verdict.
 * @param {object} metadata  tick_size, preset_id, engine_version, session
 *
 * @returns {{ status: 'OK', detection_result: DetectionResult/v1 }}
 *        | {{ status: 'FAILED', failure_code: string, reason: string }}
 *
 * Never throws for normal validation failures.
 * Never modifies stageOutputs or metadata.
 */
function buildDetectionResult(stageOutputs, metadata) {

  // ── Step 1: validate inputs ────────────────────────────────────────────────

  const soErr = validateStageOutputs(stageOutputs);
  if (soErr) return soErr;

  const mdErr = validateMetadata(metadata);
  if (mdErr) return mdErr;

  const tickSize = metadata.tick_size;
  const rej      = stageOutputs.rejResult;
  const orb      = stageOutputs.orb      || null;
  const brk      = stageOutputs.breakResult  || null;
  const disp     = stageOutputs.dispResult   || null;
  const retest   = stageOutputs.retestResult || null;

  // ── Step 2: map runtime status to canonical status ─────────────────────────

  const isValid = rej.status === 'OK';
  const canonicalStatus = isValid ? 'VALID' : 'INVALID';

  // ── Step 3: failed_stage and failed_rules ──────────────────────────────────

  // The canonical failed_stage enum maps directly from the runtime failed_stage
  // values for all real detection failures. Non-detection runtime codes
  // (INVALID_SESSION_INPUT, UNSUPPORTED_CONFIGURATION, INVALID_INPUT) indicate
  // caller/integration errors, not market-data outcomes; they are passed through
  // as-is (they will not appear in a normal pipeline run).
  const failed_stage = isValid ? null : (rej.failed_stage || null);
  const failed_rules = isValid ? [] : [];   // engine does not yet populate structured RuleFailure[]

  // ── Step 4: level fields ───────────────────────────────────────────────────

  // level_price is a plain float in the runtime output (from orb.level_price or
  // rej.level_price). Convert to canonical PriceTicks.
  const rawLevelPrice = (rej.level_price != null)
    ? rej.level_price
    : (orb && orb.status === 'OK' ? orb.level_price : null);

  const level_price = rawLevelPrice != null
    ? priceTicks(priceToTicks(rawLevelPrice, tickSize), tickSize)
    : null;

  const level_source    = (orb && orb.status === 'OK') ? (orb.level_source || null) : null;
  const level_bar       = (orb && orb.status === 'OK') ? buildBar(orb.orb_candle, tickSize) : null;
  const direction       = (orb && orb.status === 'OK') ? (orb.direction || null)   : null;

  // ── Step 5: break fields ───────────────────────────────────────────────────

  const break_bar = (brk && brk.status === 'OK') ? buildBar(brk.break_candle, tickSize) : null;

  let directional_break_distance = null;
  if (brk && brk.status === 'OK' && brk.directional_break_distance != null) {
    directional_break_distance = directionalTickDistance(
      brk.directional_break_distance.ticks, tickSize
    );
  }

  // ── Step 6: displacement fields ───────────────────────────────────────────

  let displacement_window      = [];
  let displacement_bar_count   = null;
  let displacement_pts         = null;
  let displacement_pct         = null;
  let rejection_side_clearance_by_bar   = null;
  let minimum_rejection_side_clearance  = null;
  let average_rejection_side_clearance  = null;

  if (disp && disp.status === 'OK') {
    displacement_bar_count = disp.displacement_bar_count != null
      ? disp.displacement_bar_count : null;

    if (Array.isArray(disp.displacement_window)) {
      displacement_window = disp.displacement_window.map(c => buildBar(c, tickSize));
    }

    if (disp.displacement_distance != null) {
      displacement_pts = absoluteTickDistance(disp.displacement_distance.ticks, tickSize);
    }

    // displacement_pct: displacement_pts.to_price() / level_price.to_price()
    // Expressed as Rational: displacement_pts.ticks / level_price.ticks
    if (displacement_pts != null && level_price != null && level_price.ticks !== 0) {
      displacement_pct = rational(displacement_pts.ticks, level_price.ticks);
    }

    // rejection_side_clearance_by_bar: computed per displacement bar
    // LONG: bar.low.ticks - level_price.ticks (signed)
    // The engine does not pre-compute this array; we derive it here.
    if (level_price != null && displacement_window.length > 0) {
      const clearances = displacement_window.map(bar =>
        directionalTickDistance(bar.low.ticks - level_price.ticks, tickSize)
      );
      rejection_side_clearance_by_bar = clearances;

      const ticks = clearances.map(c => c.ticks);
      const minTicks = Math.min(...ticks);
      const maxTicks = Math.max(...ticks);
      minimum_rejection_side_clearance = directionalTickDistance(minTicks, tickSize);

      // average_rejection_side_clearance: mean of to_price() values (as Decimal string)
      const sum = ticks.reduce((acc, t) => acc + t, 0);
      const mean = sum / ticks.length;
      average_rejection_side_clearance = (mean * tickSize).toFixed(Math.max(decimalsOf(tickSize), 2));
    }
  }

  // ── Step 7: retest fields ─────────────────────────────────────────────────
  //
  // Canonical boundary (§840 Stage 4, FROZEN):
  //   "All bars from first retest contact through the confirmation bar
  //    comprise the retest_window."
  //
  // For a VALID detection the confirmation bar is INCLUDED in the window.
  // Post-confirmation bars must not affect any retest metric.
  //
  // The runtime engine's retest_window and retest_contacts span from first
  // retest contact to end-of-session (retest_window_end_index).  We bound
  // both to confirmation_candle_index (inclusive) for VALID detections.
  // For INVALID detections no confirmation bar exists, so the full runtime
  // retest window is used unchanged.

  let retest_window                     = [];
  let retest_bar_count                  = null;
  let failed_retest_count_canon         = null;
  let failed_retests_canon              = [];
  let bars_break_to_first_retest        = null;
  let bars_break_to_confirmation        = null;
  let retest_closest_approach           = null;
  let retest_penetration_through_level  = null;
  let retest_displacement_retracement_pct = null;

  if (retest && retest.status === 'OK') {
    // Determine inclusive upper index of the canonical retest window.
    // VALID:   end at confirmation_candle_index (the confirmation bar is included).
    // INVALID: end at the last bar the engine examined (retest_window_end_index).
    const retestStartIdx = retest.retest_window_start_index;
    const canonEndIdx = (isValid && rej.confirmation_candle_index != null)
      ? rej.confirmation_candle_index
      : retest.retest_window_end_index;

    // Filter the raw candle array to the canonical window.
    // For VALID detections: keep only candles whose timestamp <= confirmation_candle
    // timestamp (the confirmation bar is included per the frozen contract).
    // For INVALID detections: keep all candles (no confirmation bar exists).
    // Timestamp comparison is the robust boundary — it works regardless of whether
    // the caller's retest_window array exactly covers the full session or not.
    if (Array.isArray(retest.retest_window)) {
      const confTimeMs = (isValid && rej.confirmation_candle && rej.confirmation_candle.time)
        ? (rej.confirmation_candle.time instanceof Date
            ? rej.confirmation_candle.time.getTime()
            : rej.confirmation_candle.time)
        : null;
      const canonRaw = (confTimeMs != null)
        ? retest.retest_window.filter(c => {
            const cMs = c.time instanceof Date ? c.time.getTime() : c.time;
            return cMs <= confTimeMs;
          })
        : retest.retest_window.slice();
      retest_window = canonRaw.map(c => buildBar(c, tickSize));
      retest_bar_count = retest_window.length;
    }

    // Filter contacts to the canonical window (candle_index <= canonEndIdx).
    const canonContacts = Array.isArray(retest.retest_contacts)
      ? retest.retest_contacts.filter(rc => rc.candle_index <= canonEndIdx)
      : [];

    // All retest metrics are computed over canonContacts only.
    if (level_price != null && canonContacts.length > 0) {
      // retest_closest_approach: min abs(bar.low.ticks - level_price.ticks)
      const absDistances = canonContacts.map(rc =>
        Math.abs(priceToTicks(rc.candle.low, tickSize) - level_price.ticks)
      );
      retest_closest_approach = absoluteTickDistance(Math.min(...absDistances), tickSize);

      // retest_penetration_through_level: max(0, level.ticks - min(low.ticks))
      const minLowTicks = Math.min(...canonContacts.map(rc =>
        priceToTicks(rc.candle.low, tickSize)
      ));
      const penTicks = Math.max(0, level_price.ticks - minLowTicks);
      retest_penetration_through_level = absoluteTickDistance(penTicks, tickSize);

      // retest_displacement_retracement_pct (INV-D-15a/b/c/d)
      if (displacement_pts != null && displacement_pts.ticks !== 0) {
        // closest_directional_position: min of (bar.low.ticks - level_price.ticks) — signed
        const closestDP = Math.min(...canonContacts.map(rc =>
          priceToTicks(rc.candle.low, tickSize) - level_price.ticks
        ));
        const retracedTicks = Math.max(0,
          Math.min(displacement_pts.ticks, displacement_pts.ticks - closestDP)
        );
        retest_displacement_retracement_pct = rational(retracedTicks, displacement_pts.ticks);
      }
    }
  }

  // failed_retests from rejResult (available on both OK and FAILED paths)
  if (Array.isArray(rej.failed_retests)) {
    // Each engine failed_retest: { candle_index, candle, timestamp, geometry, failed_rules }
    // Canonical RejectionAttempt: { bar: Bar, failed_rules: RuleFailure[] }
    // Engine failed_rules are plain strings, not structured RuleFailure objects yet —
    // pass them through as-is in the message field (the full RuleFailure schema is
    // populated by the production Detection Engine, not this adapter).
    failed_retests_canon = rej.failed_retests.map(fr => Object.freeze({
      bar: buildBar(fr.candle, tickSize),
      failed_rules: Array.isArray(fr.failed_rules)
        ? fr.failed_rules.map(r => Object.freeze({
            rule_id:        typeof r === 'string' ? r : String(r),
            stage:          'REJECTION_CANDLE',
            value_type:     'BOOLEAN',
            actual_value:   null,
            operator:       null,
            required_value: null,
            unit:           null,
            message:        typeof r === 'string' ? r : String(r)
          }))
        : []
    }));
    failed_retest_count_canon = failed_retests_canon.length;
  }

  // bars_break_to_first_retest and bars_break_to_confirmation:
  // computed from candle indices when all stage results are available
  if (brk && brk.status === 'OK' &&
      retest && retest.status === 'OK') {
    bars_break_to_first_retest =
      retest.retest_window_start_index - brk.break_candle_index;
  }
  if (brk && brk.status === 'OK' && isValid) {
    bars_break_to_confirmation =
      rej.confirmation_candle_index - brk.break_candle_index;
  }

  // ── Step 8: rejection candle fields ───────────────────────────────────────

  let confirmation_bar                      = null;
  let confirmation_rej_wick                 = null;
  let confirmation_body                     = null;
  let confirmation_opp_wick                 = null;
  let confirmation_favorable_close_location = null;
  let confirmation_penetration              = null;
  let confirmation_close_beyond_level       = null;

  if (isValid && rej.confirmation_candle) {
    confirmation_bar = buildBar(rej.confirmation_candle, tickSize);

    const g = rej.geometry;
    if (g) {
      // Rationals from float ratios (range > 0 guaranteed by Stage 5 for qualified candle)
      if (g.rejection_wick_ratio    != null) confirmation_rej_wick                 = floatToRational(g.rejection_wick_ratio);
      if (g.body_ratio              != null) confirmation_body                     = floatToRational(g.body_ratio);
      if (g.opposite_wick_ratio     != null) confirmation_opp_wick                 = floatToRational(g.opposite_wick_ratio);
      if (g.favorable_close_location != null) confirmation_favorable_close_location = floatToRational(g.favorable_close_location);

      // Penetration: AbsoluteTickDistance
      if (g.penetration_through_level_ticks != null) {
        confirmation_penetration = absoluteTickDistance(g.penetration_through_level_ticks, tickSize);
      }

      // Close beyond level: DirectionalTickDistance (signed)
      if (g.close_beyond_level_ticks != null) {
        confirmation_close_beyond_level = directionalTickDistance(g.close_beyond_level_ticks, tickSize);
      }
    }
  }

  // ── Step 9: assemble canonical SessionMetadata ─────────────────────────────

  const session = Object.freeze({
    symbol:               metadata.session.symbol,
    date:                 metadata.session.date,
    market_timezone:      metadata.session.market_timezone,
    session_open_utc_ms:  metadata.session.session_open_utc_ms,
    session_close_utc_ms: metadata.session.session_close_utc_ms,
    timeframe_seconds:    metadata.session.timeframe_seconds
  });

  // ── Step 10: assemble DetectionResult/v1 ──────────────────────────────────

  const detection_result = Object.freeze({
    schema_version:   'DetectionResult/v1',
    result_id:        generateUUIDv4(),
    produced_at:      new Date().toISOString(),   // ISO 8601 UTC; not derived from any bar_utc_ms
    session,
    preset_id:        metadata.preset_id,
    engine_version:   metadata.engine_version,

    status:           canonicalStatus,
    failed_stage,
    failed_rules,

    // Level
    level_price,
    level_source,
    level_bar,
    direction,

    // Break
    break_bar,
    directional_break_distance,

    // Displacement
    displacement_window,
    displacement_bar_count,
    displacement_pts,
    displacement_pct,
    rejection_side_clearance_by_bar,
    minimum_rejection_side_clearance,
    average_rejection_side_clearance,

    // Retest
    retest_window,
    retest_bar_count,
    failed_retest_count:      failed_retest_count_canon,
    failed_retests:           failed_retests_canon,
    bars_break_to_first_retest,
    bars_break_to_confirmation,
    retest_closest_approach,
    retest_penetration_through_level,
    retest_displacement_retracement_pct,

    // Rejection candle
    confirmation_bar,
    confirmation_rej_wick,
    confirmation_body,
    confirmation_opp_wick,
    confirmation_favorable_close_location,
    confirmation_penetration,
    confirmation_close_beyond_level
  });

  return { status: 'OK', detection_result };
}

module.exports = { buildDetectionResult };
