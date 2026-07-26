/**
 * estrategie/bdrr_trade_plan.js
 *
 * BDRR Trade Plan construction — TradePlan/v1 only.
 *
 * Exports one primary function:
 *   buildTradePlan(detectionResult, config)
 *
 * This module is deliberately isolated from bdrr_engine.js:
 *   - No import of Stage 1–5 detection logic.
 *   - No market data access of any kind.
 *   - No inspection of candles after the confirmation bar.
 *   - No outcome evaluation.
 *   - No mutation of caller-owned objects.
 *
 * Input contract change (canonical migration):
 *   detectionResult must be a canonical DetectionResult/v1 object produced by
 *   buildDetectionResult() in bdrr_detection_result.js.  The old raw
 *   findRejection() output shape (status: 'OK', confirmation_candle: {...})
 *   is no longer accepted.
 *
 *   Required canonical fields consumed:
 *     schema_version      must equal 'DetectionResult/v1'
 *     status              must equal 'VALID'
 *     confirmation_bar    canonical Bar — { open, high, low, close: PriceTicks }
 *
 *   PriceTicks shape: { ticks: integer, tick_size: number }
 *   Tick values are read directly — no float-to-tick conversion is performed.
 *
 * Frozen output contract: TradePlan/v1
 *   All price/distance values are integer tick counts stored alongside their
 *   tick_size. No raw floating-point prices are stored in the contract.
 *
 * Supported configuration (initial preset):
 *   direction:           LONG   (SHORT → structured UNSUPPORTED_DIRECTION failure)
 *   entry_model:         CONFIRMATION_CLOSE | BREAK_OF_SIGNAL_BAR
 *   entry_buffer_ticks:  integer >= 0
 *   stop_buffer_ticks:   integer >= 0
 *   tick_size:           finite positive number
 *
 * Run tests: node estrategie/test_bdrr_trade_plan.js
 */

'use strict';

// ── Helpers ──────────────────────────────────────────────────────────────────

function isNonNegativeInteger(v) {
  return typeof v === 'number' && Number.isInteger(v) && v >= 0;
}

function isPositiveFiniteNumber(v) {
  return typeof v === 'number' && isFinite(v) && v > 0;
}

function fail(code, reason) {
  return { status: 'FAILED', failure_code: code, reason };
}

// Build one PriceTicks-shaped object (ticks + tick_size).
function priceTicks(ticks, tickSize) {
  return { ticks, tick_size: tickSize };
}

// ── Input validation ─────────────────────────────────────────────────────────

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
      `detectionResult.status must be "VALID"; got "${dr.status}"` +
        (dr.failed_stage ? ` (failed_stage: ${dr.failed_stage})` : '')
    );
  }
  return null;
}

function validateConfig(config) {
  if (!config || typeof config !== 'object') {
    return fail('INVALID_DETECTION_RESULT', 'config must be a non-null object');
  }

  if (config.direction !== 'LONG') {
    return fail(
      'UNSUPPORTED_DIRECTION',
      `direction "${config.direction}" is not supported; only "LONG" is implemented`
    );
  }

  if (
    config.entry_model !== 'CONFIRMATION_CLOSE' &&
    config.entry_model !== 'BREAK_OF_SIGNAL_BAR'
  ) {
    return fail(
      'UNSUPPORTED_ENTRY_MODEL',
      `entry_model "${config.entry_model}" is not recognized; ` +
        'supported values: CONFIRMATION_CLOSE, BREAK_OF_SIGNAL_BAR'
    );
  }

  if (!isPositiveFiniteNumber(config.tick_size)) {
    return fail('TICK_SIZE_MISMATCH', 'config.tick_size must be a finite positive number');
  }

  if (!isNonNegativeInteger(config.entry_buffer_ticks)) {
    return fail(
      'INVALID_BUFFER',
      `entry_buffer_ticks must be a non-negative integer; got ${config.entry_buffer_ticks}`
    );
  }
  if (!isNonNegativeInteger(config.stop_buffer_ticks)) {
    return fail(
      'INVALID_BUFFER',
      `stop_buffer_ticks must be a non-negative integer; got ${config.stop_buffer_ticks}`
    );
  }

  return null;
}

function validateConfirmationBar(dr, tickSize) {
  const bar = dr.confirmation_bar;
  if (!bar || typeof bar !== 'object') {
    return fail(
      'MISSING_CONFIRMATION_BAR',
      'detectionResult.confirmation_bar is missing or not an object'
    );
  }

  // Each OHLC field must be a PriceTicks object: { ticks: integer, tick_size: number }
  for (const field of ['open', 'high', 'low', 'close']) {
    const pt = bar[field];
    if (!pt || typeof pt !== 'object') {
      return fail(
        'INVALID_TICK_VALUE',
        `confirmation_bar.${field} must be a PriceTicks object; got ${JSON.stringify(pt)}`
      );
    }
    if (!Number.isInteger(pt.ticks)) {
      return fail(
        'INVALID_TICK_VALUE',
        `confirmation_bar.${field}.ticks must be an integer; got ${pt.ticks}`
      );
    }
    if (!isPositiveFiniteNumber(pt.tick_size)) {
      return fail(
        'INVALID_TICK_VALUE',
        `confirmation_bar.${field}.tick_size must be a finite positive number; got ${pt.tick_size}`
      );
    }
    // tick_size must match config
    if (pt.tick_size !== tickSize) {
      return fail(
        'TICK_SIZE_MISMATCH',
        `confirmation_bar.${field}.tick_size (${pt.tick_size}) does not match ` +
          `config.tick_size (${tickSize})`
      );
    }
  }

  return null;
}

// ── Primary export ───────────────────────────────────────────────────────────

/**
 * buildTradePlan(detectionResult, config)
 *
 * Constructs a frozen TradePlan/v1 object from a canonical DetectionResult/v1.
 *
 * @param {object} detectionResult  Canonical DetectionResult/v1 produced by
 *   buildDetectionResult().  Must have:
 *     schema_version === 'DetectionResult/v1'
 *     status         === 'VALID'
 *     confirmation_bar: { open, high, low, close: PriceTicks }
 * @param {object} config  Preset configuration object.
 *   Required keys: direction, entry_model, entry_buffer_ticks,
 *                  stop_buffer_ticks, tick_size.
 *
 * @returns {{ status: 'OK', trade_plan: TradePlan/v1 }}
 *        | {{ status: 'FAILED', failure_code: string, reason: string }}
 *
 * Never throws for normal validation failures.
 * Never modifies detectionResult or config.
 * Never reads market data.
 * Never inspects candles after the confirmation bar.
 */
function buildTradePlan(detectionResult, config) {
  // ── Step 1: validate inputs ────────────────────────────────────────────────

  const drErr = validateDetectionResult(detectionResult);
  if (drErr) return drErr;

  const cfgErr = validateConfig(config);
  if (cfgErr) return cfgErr;

  const tickSize = config.tick_size;

  const barErr = validateConfirmationBar(detectionResult, tickSize);
  if (barErr) return barErr;

  // ── Step 2: read confirmation bar tick values directly ────────────────────
  // PriceTicks.ticks are already integers — no float-to-tick conversion needed.

  const bar = detectionResult.confirmation_bar;
  const highTicks  = bar.high.ticks;
  const lowTicks   = bar.low.ticks;
  const closeTicks = bar.close.ticks;

  // ── Step 3: compute entry price ───────────────────────────────────────────

  let entryTicks;
  if (config.entry_model === 'CONFIRMATION_CLOSE') {
    // LONG: entry = close + entry_buffer_ticks (favorable direction is upward)
    entryTicks = closeTicks + config.entry_buffer_ticks;
  } else {
    // BREAK_OF_SIGNAL_BAR — LONG: entry = high + entry_buffer_ticks
    entryTicks = highTicks + config.entry_buffer_ticks;
  }

  // ── Step 4: compute stop price ────────────────────────────────────────────

  // LONG: stop = low - stop_buffer_ticks
  const stopTicks = lowTicks - config.stop_buffer_ticks;

  // ── Step 5: validate geometric relationship ───────────────────────────────

  // LONG: entry must be strictly above stop
  if (entryTicks <= stopTicks) {
    return fail(
      'INVALID_RISK',
      `LONG entry (${entryTicks} ticks) must be strictly above stop (${stopTicks} ticks); ` +
        'check confirmation_bar geometry or buffer configuration'
    );
  }

  // ── Step 6: compute risk ──────────────────────────────────────────────────

  const riskTicks = Math.abs(entryTicks - stopTicks); // always positive given step 5

  if (riskTicks === 0) {
    return fail('INVALID_RISK', 'calculated risk is zero ticks; entry and stop are identical');
  }

  // ── Step 7: compute targets (integer tick arithmetic only) ────────────────

  const r2Ticks = entryTicks + 2 * riskTicks;
  const r3Ticks = entryTicks + 3 * riskTicks;
  const r4Ticks = entryTicks + 4 * riskTicks;

  // ── Step 8: assemble TradePlan/v1 ─────────────────────────────────────────

  const trade_plan = {
    schema_version:      'TradePlan/v1',
    entry_model:         config.entry_model,
    entry_buffer_ticks:  config.entry_buffer_ticks,
    stop_buffer_ticks:   config.stop_buffer_ticks,
    tick_size:           tickSize,
    entry_price:         priceTicks(entryTicks,  tickSize),
    stop_price:          priceTicks(stopTicks,   tickSize),
    risk:                priceTicks(riskTicks,   tickSize),
    r2_price:            priceTicks(r2Ticks,     tickSize),
    r3_price:            priceTicks(r3Ticks,     tickSize),
    r4_price:            priceTicks(r4Ticks,     tickSize)
  };

  return { status: 'OK', trade_plan };
}

module.exports = { buildTradePlan };
